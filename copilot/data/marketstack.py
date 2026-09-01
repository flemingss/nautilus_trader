"""
Marketstack end-of-day ingestion.

Ported from trade-copilot ``services/ingestion/marketstack.py``. The HTTP client's
shape - page to exhaustion, bounded retry, a browser-ish user agent - is carried
across because each part of it was paid for once already; the port notes below record
where this version had to differ, and why.

Which prices go into a backtest
-------------------------------
Marketstack returns two OHLC sets per row. This module uses the plain
``open/high/low/close`` set, **not** ``adj_*``, and that is a deliberate reversal of
the obvious choice. Measured over 15,851 rows of AAPL, MSFT and SPY spanning
2005-2025:

===============================  ==========  ==========
Property                         raw set     adj set
===============================  ==========  ==========
Rows with incoherent OHLC        0.011%      22%
Rows with null fields            0           11%
Already back-adjusted for splits yes         yes
Adjusted for dividends           no          yes
===============================  ==========  ==========

The adjusted set is not internally consistent: AAPL 2022-11-03 reports
``adj_close`` 138.65 against an ``adj_low`` of 138.75, a close outside its own bar.
Feeding that to a backtest yields fills at prices the bar says never traded.

**The raw set is not perfectly clean either, and an earlier version of this note said
it was.** Over 15,851 rows it had zero failures; over 105,414 it has twelve. Every one
is the *open* sitting a few cents outside the day's high or low - GOOGL 2025-12-29
opens at 314.52 against a high of 314.02 - which reads like an official opening print
carried from a different source than the intraday range. The rate is 0.011% against
22% for the adjusted set, so the choice is unchanged and the gate rejects them either
way; the point is that the coherence check earns its keep on both sets rather than
being a formality on one.

What makes the choice safe is that the raw set is *already split-adjusted* by the
vendor: AAPL's close on
2020-08-28 is reported as 124.8075, which is the pre-split 499.23 divided by the 4:1
split that settled on the 31st. So the catastrophic discontinuity, a split reading as
a -75% day, does not exist in the raw series either.

What the raw set lacks is dividend adjustment. That understates total return by the
yield and does **not** manufacture a discontinuity that a strategy could mistake for a
signal, which is the failure mode that matters here. ``dividend`` and ``split_factor``
are carried through per row, so a dividend-adjusted series can be derived later from a
coherent base rather than inherited from an incoherent one.

Port notes
----------
- **Pagination.** The original pages until a short page because Marketstack v1 capped
  ``total`` at ``limit``, making a truncated page indistinguishable from a complete
  one. v2 reports ``total`` truthfully (verified: a 3,020-row window reports
  ``total: 3020`` under ``limit: 1000``). Paging to exhaustion is kept - it is correct
  either way - and ``total`` is now used as a cross-check rather than trusted alone.
- **Contracts.** ``DailyBar`` here is the overlay's gate contract, which carries OHLCV
  and nothing else. Corporate actions and rejects travel in :class:`IngestionResult`
  instead of being bolted onto the bar, so the type the validation gate is written
  against does not change shape.
- **Persistence and events.** The original's repository and event-bus wiring is
  dropped. This overlay's sink is a Nautilus ``ParquetDataCatalog`` (see
  ``copilot/data/catalog.py``), not Postgres and Redis.
- **Trading-day gate.** New here, and not a port. The vendor emits bars on days the
  market was shut; see ``copilot/data/calendar.py``.

"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC
from datetime import date
from datetime import datetime
from decimal import Decimal
from decimal import InvalidOperation
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request
from urllib.request import urlopen

from copilot.data.calendar import is_trading_day
from copilot.validation.types import DailyBar


RawRow = Mapping[str, object]

DEFAULT_BASE_URL = "https://api.marketstack.com/v2"
"""v2 over v1: v1 still answers, but v2 is the current version and reports ``total``
truthfully, which v1 capped at ``limit``.

v2 also adds a ``price_currency`` per row. It is **not** used as authoritative and
must not be: MSFT returns 18 rows tagged ``EUR``, 59 tagged lowercase ``usd``, and
5,023 with no tag at all, while the prices across all of them are plainly the same
continuous USD series (513.71 tagged USD, then 512.50 tagged EUR the next session).
The tag is unreliable metadata, not a second series, so it is collected for reporting
and the real currency is supplied by the caller."""

# Marketstack sits behind a WAF that rejects the default urllib agent.
USER_AGENT = "NautilusCopilot/1.0"

PAGE_LIMIT = 1000
"""
Provider maximum.

Requesting more is silently reduced to this.

"""

MAX_PAGES = 50
"""
Backstop against an unbounded loop, ~50k daily bars - two centuries of one symbol.
"""

MAX_FETCH_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 2.0
"""
Geometric backoff (2s, 4s).

Unattended, a single 5xx would otherwise skip a trading day silently.

"""

HTTP_SERVER_ERROR = 500
HTTP_TOO_MANY_REQUESTS = 429


@dataclass(frozen=True)
class CorporateAction:
    """
    A row that carried a split or a dividend.

    Surfaced from every run so that an adjustment becoming visible is a routine reported
    event rather than a forensic discovery later. A split silently re-bases the vendor's
    entire history for that symbol, so an already-written catalog range disagrees with a
    fresh fetch of the same range from that point on.

    """

    symbol: str
    closed_at: datetime
    split_factor: Decimal
    dividend_amount: Decimal


@dataclass(frozen=True)
class RejectedRow:
    """
    One provider row that did not pass a gate, kept with the reason it failed.
    """

    reason: str
    symbol: str | None
    closed_at: datetime | None
    raw: dict[str, object]


@dataclass(frozen=True)
class IngestionResult:
    """
    Everything one fetch-and-gate pass produced.
    """

    bars: tuple[DailyBar, ...] = ()
    rejected: tuple[RejectedRow, ...] = ()
    corporate_actions: tuple[CorporateAction, ...] = ()
    currency_tags: Mapping[str, tuple[str, ...]] | None = None
    """
    Distinct ``price_currency`` values the provider reported, per symbol.

    Advisory only - see :data:`DEFAULT_BASE_URL`. Surfaced so a symbol that really is
    priced in something other than the currency the caller assumed has somewhere to
    show up, rather than being silently normalised away.

    """
    fetched: int = 0
    """
    Rows the provider returned, before any gate.

    Read from the response rather than derived as ``len(bars) + len(rejected)``: the
    two are equal today, and that identity is exactly what a future normalizer change
    would break unnoticed. It is also the only thing separating an empty window - a
    holiday, nothing to fetch - from a mass rejection.

    """

    @property
    def rejection_ratio(self) -> Decimal:
        """
        Share of fetched rows that failed a gate.
        """
        if not self.fetched:
            return Decimal(0)
        return Decimal(len(self.rejected)) / Decimal(self.fetched)


class MarketstackClient:
    """
    Marketstack EOD client. Owns no secret: the access key is passed in.

    The caller reads it from the environment, so the key has no chance to reach a config
    file inside the repository.

    """

    def __init__(
        self,
        access_key: str,
        base_url: str = DEFAULT_BASE_URL,
        *,
        page_limit: int = PAGE_LIMIT,
        max_pages: int = MAX_PAGES,
        max_attempts: int = MAX_FETCH_ATTEMPTS,
        retry_backoff_seconds: float = RETRY_BACKOFF_SECONDS,
        timeout_seconds: int = 60,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """
        Configure endpoint, paging and retry policy.
        """
        if not access_key:
            raise ValueError("Marketstack access key is required")
        self._access_key = access_key
        self._base_url = base_url.rstrip("/")
        self._page_limit = page_limit
        self._max_pages = max_pages
        self._max_attempts = max_attempts
        self._retry_backoff_seconds = retry_backoff_seconds
        self._timeout_seconds = timeout_seconds
        self._sleep = sleep

    def fetch_eod(
        self,
        symbols: Sequence[str],
        start: date,
        end: date,
    ) -> tuple[RawRow, ...]:
        """
        Fetch every row in ``[start, end]``, following pagination to exhaustion.

        One request returns at most ``page_limit`` rows. A multi-year window is
        therefore silently cut to its most recent slice unless the caller pages, and a
        backfill would look successful while missing most of its own date range.

        Pages until one comes back short. When the provider reports a total, it is
        checked against the row count afterwards and a shortfall raises rather than
        returning a partial history that a later run would treat as complete.

        """
        if not symbols:
            return ()

        rows: list[RawRow] = []
        reported_total: int | None = None

        for page in range(self._max_pages):
            page_rows, total = self._fetch_page(
                symbols,
                start,
                end,
                offset=page * self._page_limit,
            )
            rows.extend(page_rows)
            if reported_total is None:
                reported_total = total
            if len(page_rows) < self._page_limit:
                self._check_total(len(rows), reported_total)
                return tuple(rows)

        raise ValueError(
            f"Marketstack window exceeded {self._max_pages} pages ({len(rows)} rows); "
            "narrow the date range rather than silently truncating",
        )

    @staticmethod
    def _check_total(fetched: int, reported_total: int | None) -> None:
        """
        Fail loudly when the provider says there was more than we collected.
        """
        if reported_total is not None and fetched < reported_total:
            raise ValueError(
                f"Marketstack reported {reported_total} rows but pagination ended "
                f"after {fetched}; the window would be silently incomplete",
            )

    def _fetch_page(
        self,
        symbols: Sequence[str],
        start: date,
        end: date,
        *,
        offset: int,
    ) -> tuple[tuple[RawRow, ...], int | None]:
        """
        One page, with bounded retry on transient failures.

        Retryable: any network-level failure, plus HTTP 5xx and 429 - the provider
        saying "not right now". Other client errors mean the request itself is wrong,
        so retrying would only re-send the same mistake.

        """
        last_error: Exception | None = None
        for attempt in range(self._max_attempts):
            if attempt:
                self._sleep(self._retry_backoff_seconds * 2 ** (attempt - 1))
            try:
                return self._fetch_page_once(symbols, start, end, offset=offset)
            except HTTPError as e:
                if e.code < HTTP_SERVER_ERROR and e.code != HTTP_TOO_MANY_REQUESTS:
                    raise
                last_error = e
            except (URLError, TimeoutError) as e:
                last_error = e

        if last_error is None:  # pragma: no cover - max_attempts >= 1 always
            raise RuntimeError("retry loop exited without an attempt")
        raise last_error

    def _fetch_page_once(
        self,
        symbols: Sequence[str],
        start: date,
        end: date,
        *,
        offset: int,
    ) -> tuple[tuple[RawRow, ...], int | None]:
        params = urlencode(
            {
                "access_key": self._access_key,
                "symbols": ",".join(symbols),
                "date_from": start.isoformat(),
                "date_to": end.isoformat(),
                "limit": self._page_limit,
                "offset": offset,
            },
        )
        request = Request(  # noqa: S310 - fixed https scheme from DEFAULT_BASE_URL
            f"{self._base_url}/eod?{params}",
            headers={"User-Agent": USER_AGENT},
        )
        with urlopen(request, timeout=self._timeout_seconds) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))

        data = payload.get("data", [])
        if not isinstance(data, list):
            raise TypeError("Marketstack EOD response 'data' must be a list")

        pagination = payload.get("pagination")
        total = pagination.get("total") if isinstance(pagination, Mapping) else None
        return tuple(_as_mapping(row) for row in data), total if isinstance(total, int) else None


def normalize(
    rows: Iterable[RawRow],
    *,
    received_at: datetime,
    require_trading_day: bool = True,
) -> IngestionResult:
    """
    Turn provider rows into gate-ready bars, rejecting what cannot be trusted.

    Every rejection is returned with its reason rather than dropped. A run that
    quietly discarded a tenth of its rows would still look successful, and the
    resulting hole in the history would surface much later as an unexplained gap in a
    walk-forward fold.

    ``require_trading_day`` exists for non-US venues whose calendar this overlay does
    not model. Turning it off on US equities re-admits the phantom holiday bars.

    """
    accepted: list[DailyBar] = []
    rejected: list[RejectedRow] = []
    actions: list[CorporateAction] = []
    currency_tags: dict[str, set[str]] = {}
    fetched = 0

    for row in rows:
        fetched += 1
        raw = dict(row)
        try:
            bar, action = _parse(raw)
        except (KeyError, TypeError, ValueError) as e:
            rejected.append(_reject(raw, f"schema_or_value_error: {e}"))
            continue

        if bar.closed_at > received_at:
            rejected.append(_reject(raw, "future_bar", bar))
            continue
        if require_trading_day and not is_trading_day(bar.closed_at.date()):
            rejected.append(_reject(raw, "non_trading_day", bar))
            continue

        accepted.append(bar)
        if action is not None:
            actions.append(action)
        currency = raw.get("price_currency")
        if isinstance(currency, str) and currency:
            currency_tags.setdefault(bar.symbol, set()).add(currency.upper())

    gated = _apply_series_gates(accepted)
    # Only actions on bars that survived every gate: reporting a split whose bar was
    # rejected would send someone looking for a rebase that never landed.
    surviving = {(b.symbol, b.closed_at) for b in gated.bars}
    return IngestionResult(
        bars=gated.bars,
        rejected=(*rejected, *gated.rejected),
        corporate_actions=tuple(a for a in actions if (a.symbol, a.closed_at) in surviving),
        currency_tags={s: tuple(sorted(t)) for s, t in currency_tags.items()} or None,
        fetched=fetched,
    )


def _apply_series_gates(bars: Sequence[DailyBar]) -> IngestionResult:
    """
    Gates that need the batch, not one row: duplicates and time ordering.

    Providers choose their own row order - Marketstack returns EOD newest-first - so the
    batch is put in canonical order first. The sort is stable, so of two rows sharing a
    key the one that arrived first still wins.

    """
    ordered = sorted(bars, key=lambda b: (b.symbol, b.closed_at))
    accepted: list[DailyBar] = []
    rejected: list[RejectedRow] = []
    seen: set[tuple[str, datetime]] = set()
    last_seen: dict[str, datetime] = {}

    for bar in ordered:
        key = (bar.symbol, bar.closed_at)
        if key in seen:
            rejected.append(_reject(_bar_raw(bar), "duplicate_bar", bar))
            continue
        previous = last_seen.get(bar.symbol)
        if previous is not None and bar.closed_at <= previous:
            rejected.append(_reject(_bar_raw(bar), "non_monotonic_timestamp", bar))
            continue
        seen.add(key)
        last_seen[bar.symbol] = bar.closed_at
        accepted.append(bar)

    return IngestionResult(bars=tuple(accepted), rejected=tuple(rejected))


def _parse(raw: Mapping[str, object]) -> tuple[DailyBar, CorporateAction | None]:
    """
    Build one bar, raising rather than guessing at anything malformed.
    """
    symbol = _required_str(raw, "symbol").upper()
    closed_at = _parse_datetime(_required_str(raw, "date"))
    open_ = _decimal(raw, "open")
    high = _decimal(raw, "high")
    low = _decimal(raw, "low")
    close = _decimal(raw, "close")

    if min(open_, high, low, close) <= 0:
        raise ValueError("prices must be positive")
    # A bar whose close sits outside its own range is the defect that makes the
    # vendor's adjusted set unusable. Check the set actually being used too, rather
    # than assuming the good one stays good.
    if not (low <= min(open_, close) and max(open_, close) <= high):
        raise ValueError(f"incoherent OHLC: o={open_} h={high} l={low} c={close}")

    bar = DailyBar(
        symbol=symbol,
        closed_at=closed_at,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=_int(raw, "volume"),
    )

    # Required, not defaulted: a provider row that stops carrying these must reject
    # loudly rather than pass as "no split, no dividend".
    split_factor = _decimal(raw, "split_factor")
    dividend = _decimal(raw, "dividend")
    action = (
        CorporateAction(symbol, closed_at, split_factor, dividend)
        if split_factor != 1 or dividend != 0
        else None
    )
    return bar, action


def _as_mapping(value: object) -> RawRow:
    if not isinstance(value, Mapping):
        raise TypeError("Marketstack EOD row must be an object")
    return value


def _required_str(raw: Mapping[str, object], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value:
        raise TypeError(f"{field} must be a non-empty string")
    return value


def _decimal(raw: Mapping[str, object], field: str) -> Decimal:
    value = raw.get(field)
    if value is None:
        raise ValueError(f"{field} cannot be null")
    if isinstance(value, bool):
        raise TypeError(f"{field} must be numeric")
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as e:
        # InvalidOperation is an ArithmeticError, not a ValueError, so without this a
        # malformed number crashes the run instead of rejecting the row.
        raise ValueError(f"{field} must be numeric, got {value!r}") from e
    if not parsed.is_finite():
        raise ValueError(f"{field} must be finite, got {value!r}")
    return parsed


def _int(raw: Mapping[str, object], field: str) -> int:
    value = raw.get(field)
    if value is None or isinstance(value, bool):
        raise TypeError(f"{field} must be an integer")
    parsed = Decimal(str(value))
    if parsed != parsed.to_integral_value():
        raise ValueError(f"{field} must be a whole number, got {value!r}")
    if parsed < 0:
        raise ValueError(f"{field} must not be negative, got {value!r}")
    return int(parsed)


def _parse_datetime(value: str) -> datetime:
    """
    Parse the provider's ISO timestamp, whose offset has no colon.
    """
    normalized = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", value.replace("Z", "+00:00"))
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _reject(raw: dict[str, object], reason: str, bar: DailyBar | None = None) -> RejectedRow:
    symbol = bar.symbol if bar is not None else _optional_symbol(raw)
    closed_at = bar.closed_at if bar is not None else _optional_closed_at(raw)
    return RejectedRow(reason=reason, symbol=symbol, closed_at=closed_at, raw=raw)


def _optional_symbol(raw: Mapping[str, object]) -> str | None:
    value = raw.get("symbol")
    return value.strip().upper() if isinstance(value, str) and value.strip() else None


def _optional_closed_at(raw: Mapping[str, object]) -> datetime | None:
    value = raw.get("date")
    if not isinstance(value, str):
        return None
    try:
        return _parse_datetime(value)
    except ValueError:
        return None


def _bar_raw(bar: DailyBar) -> dict[str, object]:
    return {
        "symbol": bar.symbol,
        "date": bar.closed_at.isoformat(),
        "open": str(bar.open),
        "high": str(bar.high),
        "low": str(bar.low),
        "close": str(bar.close),
        "volume": bar.volume,
    }


__all__ = [
    "DEFAULT_BASE_URL",
    "CorporateAction",
    "IngestionResult",
    "MarketstackClient",
    "RejectedRow",
    "normalize",
]
