"""
Databento historical client, and the probe that decides whether to trust it.

Read-only against the market. It fetches history and prints findings; it constructs no
execution client and cannot place an order.

    export DATABENTO_API_KEY=...
    python -m copilot.data.databento --survey
    python -m copilot.data.databento --cost --symbols AAPL,MSFT,SPY
    python -m copilot.data.databento --probe AAPL --spend

Why this module exists
----------------------
Databento is the intraday source, and *only* the intraday source. It sells no corporate
actions and its US equities history begins around 2018-05, so it cannot rebuild the
2005-2018 daily series the catalog already holds. What it does offer that nothing else
cheap does is 1-minute bars from a real consolidated feed, plus an independent daily
series for 2018 onward that can be used to audit seven of the catalog's twenty years
against a vendor that is not Marketstack.

Why the probe exists
--------------------
The last vendor's intraday endpoint returned rows shaped exactly like OHLCV bars that
were not bars: ``open``, ``high``, ``low`` and ``close`` held **one distinct value
across 200 rows** - the day's own values, repeated - and ``volume`` was **cumulative
since the open** rather than per-interval. Read naively, that hands a backtest the
day's closing price at 09:31, which is lookahead severe enough to make any intraday
result meaningless while looking entirely healthy.

That was caught by measurement, not by reading a pricing page, so no new intraday
vendor is trusted here until the same measurements pass. `probe` runs them and prints
what it found:

- **distinct OHLC values** across the sample. A real minute series varies; one
  distinct value per field is the failure above.
- **volume monotonicity.** Per-interval volume rises and falls. Volume that never
  decreases across a session is a running total wearing a bar's name.
- **summed volume against the daily total.** Minute volume that sums well past the
  day's own figure is double counting.
- **OHLC coherence**, the same rule the daily gate applies: low <= min(open, close)
  and max(open, close) <= high.

On billing
----------
Historical is usage-based and metered on the *uncompressed bytes delivered*, with no
per-dataset subscription: an API key alone reaches every historical dataset. The
metadata calls used by ``--survey`` and ``--cost`` are free, so the cost of a pull is
knowable before it is paid for. ``--probe`` spends, which is why it refuses to run
without ``--spend``.

A caution that belongs next to the code rather than in a portal: because billing
follows bytes returned, a careless full-depth query is the entire risk surface, and
that is not hypothetical. Priced on 2026-09-03 for the same 20 symbols:

===============================================  ==========
Query                                            Cost
===============================================  ==========
``ohlcv-1d``, EQUS.SUMMARY, 18 months            $0.01
``ohlcv-1m``, EQUS.MINI, 2023-03 to 2025-12      $3.74
``ohlcv-1m``, single venue, 2018-05 to 2025-12   $12.62
``statistics``, EQUS.SUMMARY, 18 months          **$1,246.00**
===============================================  ==========

The last one is ten times the signup credit, and differs from the first only by the
schema name. Price every new query shape with ``--cost`` first.

**The portal cap is set to USD 100 per month, warning at 90%** (2026-09-03). It is a
backstop against a runaway query, not a budget: the whole planned programme is roughly
$16 against $125 of credits, so tapping the cap means something is wrong rather than
that the work grew. If it is ever reached, find the query shape that did it before
raising the number.

"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import UTC
from datetime import date
from datetime import datetime
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request
from urllib.request import urlopen


API_KEY_ENV = "DATABENTO_API_KEY"
DATASET_ENV = "DATABENTO_DATASET"

DEFAULT_BASE_URL = "https://hist.databento.com/v0"

# Measured 2026-09-03 with --survey, because the documentation does not carry these and
# the choice turns entirely on them:
#
#   ARCX.PILLAR   2018-05-01   one venue     ohlcv-1m
#   XNAS.ITCH     2018-05-01   one venue     ohlcv-1m   ~38% of consolidated volume
#   XNYS.PILLAR   2018-05-01   one venue     ohlcv-1m
#   DBEQ.BASIC    2023-03-28   multi-venue   ohlcv-1m   one row per venue per day
#   EQUS.MINI     2023-03-28   composite     ohlcv-1m   ~3% of consolidated volume
#   EQUS.SUMMARY  2024-07-01   consolidated  NO ohlcv-1m; ohlcv-1d, statistics only
#
# EQUS.MINI is the default because it is the deepest dataset that serves one-minute
# bars on a composite rather than a single venue. Its prices track the tape closely and
# **its volume does not**: it is a fraction of consolidated volume, so a premise with a
# volume filter wants XNAS.ITCH's honest single-venue count instead, which also reaches
# back to 2018.
DEFAULT_DATASET = "EQUS.MINI"

DEFAULT_CATALOG = "~/.nautilus_copilot/catalog"

# Bulk pulls land here, outside the repository and beside the catalog, because they are
# machine state rather than source. Same backup obligation as the catalog.
DEFAULT_STORE = "~/.nautilus_copilot/databento"

# The venue datasets reach 2018-05-01, six years deeper than the consolidated summary,
# and their `statistics` schema carries the **official closing auction print** rather
# than a trade-derived bar. For a listed security the listing venue runs that auction,
# so its print is the official close - which is why the audit routes each symbol to the
# dataset for the venue the catalog says it is listed on.
LISTING_DATASETS = {
    "XNAS": "XNAS.ITCH",
    "XNYS": "XNYS.PILLAR",
    "ARCX": "ARCX.PILLAR",
}

# Databento's statistic type for the official closing price.
STAT_CLOSING_PRICE = 11

# The consolidated daily series. Closes matched the catalog to the cent on every day
# tested, which makes it the audit instrument - but only from 2024-07-01.
DAILY_DATASET = "EQUS.SUMMARY"

USER_AGENT = "NautilusCopilot/1.0"
TIMEOUT_SECONDS = 60

# Enough rows to see structure without paying for a session. The vendor failure this
# probe exists to catch showed up inside the first 200.
PROBE_ROWS = 200


class DatabentoError(RuntimeError):
    """
    A request to Databento failed, or returned something unusable.
    """


@dataclass(frozen=True)
class DatasetRange:
    """
    The window a dataset actually covers, as the vendor reports it.
    """

    dataset: str
    start: str
    end: str


@dataclass(frozen=True)
class Minute:
    """
    One 1-minute bar, normalized out of the vendor's JSON.
    """

    opened_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int

    @property
    def coherent(self) -> bool:
        """
        Return whether the bar's close and open sit inside its own range.
        """
        return self.low <= min(self.open, self.close) and max(self.open, self.close) <= self.high


@dataclass(frozen=True)
class ProbeFinding:
    """
    What the fidelity probe measured, and whether it is worth trusting.
    """

    symbol: str
    dataset: str
    rows: int
    distinct: dict[str, int]
    volume_is_monotonic: bool
    volume_sum: int
    incoherent_rows: int

    @property
    def looks_like_real_bars(self) -> bool:
        """
        Return whether every fidelity check passed.

        A single distinct value in any price field, or volume that never decreases, is
        the failure mode that made the previous vendor's intraday unusable.

        """
        varies = all(count > 1 for count in self.distinct.values())
        return varies and not self.volume_is_monotonic and self.incoherent_rows == 0

    def report(self) -> str:
        """
        Return the human-readable verdict, findings first.
        """
        lines = [
            f"{self.symbol} on {self.dataset}: {self.rows} one-minute rows",
            "  distinct values  " + "  ".join(f"{k}={v}" for k, v in self.distinct.items()),
            f"  volume monotonic {self.volume_is_monotonic}  (True means cumulative)",
            f"  volume summed    {self.volume_sum:,}",
            f"  incoherent rows  {self.incoherent_rows}",
            f"  VERDICT          {'real bars' if self.looks_like_real_bars else 'NOT USABLE'}",
        ]
        return "\n".join(lines)


class DatabentoClient:
    """
    A minimal historical client over the vendor's REST interface.

    Deliberately stdlib-only and hand-rolled, matching ``marketstack``: the vendor SDK
    would be a dependency carried for four endpoints, three of which are free.

    """

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        dataset: str = DEFAULT_DATASET,
    ) -> None:
        """
        Build a client.

        The key is held here and never written to a config or a log.

        """
        if not api_key:
            raise DatabentoError(f"no API key. Set {API_KEY_ENV}.")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self.dataset = dataset

    def _get(self, endpoint: str, params: dict[str, str | int] | None = None) -> Any:
        """
        Issue one authenticated GET, and return the decoded body.
        """
        query = urlencode(params or {})
        url = f"{self._base_url}/{endpoint}"
        if query:
            url = f"{url}?{query}"

        # Databento authenticates with HTTP Basic, the key as username and no password.
        token = base64.b64encode(f"{self._api_key}:".encode()).decode()
        request = Request(  # noqa: S310 - fixed https scheme from DEFAULT_BASE_URL
            url,
            headers={"Authorization": f"Basic {token}", "User-Agent": USER_AGENT},
        )
        try:
            with urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
                body = response.read().decode()
        except HTTPError as e:
            detail = e.read().decode(errors="replace")[:400]
            raise DatabentoError(f"{endpoint} failed: HTTP {e.code} {detail}") from e
        except URLError as e:
            raise DatabentoError(f"{endpoint} failed: {e.reason}") from e

        text = body.strip()
        if not text:
            return []
        # Some endpoints answer with JSON, the data endpoints with newline-delimited
        # JSON. Try the whole body first, then fall back to per-line decoding.
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return [json.loads(line) for line in text.splitlines() if line.strip()]

    def list_datasets(self) -> list[str]:
        """
        Return every dataset the key can reach.

        Free.

        """
        return list(self._get("metadata.list_datasets"))

    def dataset_range(self, dataset: str) -> DatasetRange:
        """
        Return the window a dataset covers.

        Free, and the answer to how far back it goes.

        """
        raw = self._get("metadata.get_dataset_range", {"dataset": dataset})
        if not isinstance(raw, dict):
            raise DatabentoError(f"unexpected range payload for {dataset}: {raw!r}")
        start = raw.get("start") or raw.get("start_date") or "?"
        end = raw.get("end") or raw.get("end_date") or "?"
        return DatasetRange(dataset=dataset, start=str(start), end=str(end))

    def fetch_minutes(
        self,
        symbol: str,
        start: str,
        end: str,
        limit: int = PROBE_ROWS,
    ) -> list[Minute]:
        """
        Fetch one-minute bars.

        **This spends credit**; price it with ``cost`` first.

        """
        rows = self._get(
            "timeseries.get_range",
            {
                "dataset": self.dataset,
                "symbols": symbol,
                "schema": "ohlcv-1m",
                "start": start,
                "end": end,
                "stype_in": "raw_symbol",
                "encoding": "json",
                "limit": limit,
            },
        )
        return [normalize_minute(row) for row in rows]

    def fetch_daily(
        self,
        symbols: list[str],
        start: str,
        end: str,
        dataset: str | None = None,
    ) -> dict[str, dict[date, tuple[Decimal, int]]]:
        """
        Fetch daily closes and volumes per symbol.

        **Spends**; price it with ``cost``.

        """
        rows = self._get(
            "timeseries.get_range",
            {
                "dataset": dataset or DAILY_DATASET,
                "symbols": ",".join(symbols),
                "schema": "ohlcv-1d",
                "start": start,
                "end": end,
                "stype_in": "raw_symbol",
                "encoding": "json",
            },
        )
        scale = Decimal(10) ** 9
        intervals = self.resolve_symbols(symbols, start, end, dataset)
        out: dict[str, dict[date, tuple[Decimal, int]]] = {s: {} for s in symbols}
        for row in rows:
            header = row.get("hd", row)
            stamp = int(row.get("ts_event") or header["ts_event"])
            day = datetime.fromtimestamp(stamp / 1e9, tz=UTC).date()
            name = _symbol_for(intervals, int(header["instrument_id"]), day)
            if name not in out:
                continue
            out[name][day] = (Decimal(str(row["close"])) / scale, int(row["volume"]))
        return out

    def fetch_daily_bars(
        self,
        symbols: list[str],
        start: str,
        end: str,
        dataset: str | None = None,
    ) -> dict[str, dict[date, tuple[Decimal, Decimal, Decimal, Decimal, int]]]:
        """
        Fetch full daily OHLCV per symbol, as ``(open, high, low, close, volume)``.

        ``fetch_daily`` returns close and volume because the audit only ever compares
        closes. A repair needs the whole bar: a session the daily vendor could not price
        is missing its open, high and low as well, and a bar assembled from one true
        close and three guesses is worse than no bar.

        The close here is trade-derived. Where the official auction print matters - and
        for a stored bar it does - pair this with ``fetch_official_closes`` and prefer
        the print.

        **Spends**; price it with ``cost``.

        """
        rows = self._get(
            "timeseries.get_range",
            {
                "dataset": dataset or DAILY_DATASET,
                "symbols": ",".join(symbols),
                "schema": "ohlcv-1d",
                "start": start,
                "end": end,
                "stype_in": "raw_symbol",
                "encoding": "json",
            },
        )
        scale = Decimal(10) ** 9
        intervals = self.resolve_symbols(symbols, start, end, dataset)
        out: dict[str, dict[date, tuple[Decimal, Decimal, Decimal, Decimal, int]]] = {
            s: {} for s in symbols
        }
        for row in rows:
            header = row.get("hd", row)
            stamp = int(row.get("ts_event") or header["ts_event"])
            day = datetime.fromtimestamp(stamp / 1e9, tz=UTC).date()
            name = _symbol_for(intervals, int(header["instrument_id"]), day)
            if name not in out:
                continue
            out[name][day] = (
                Decimal(str(row["open"])) / scale,
                Decimal(str(row["high"])) / scale,
                Decimal(str(row["low"])) / scale,
                Decimal(str(row["close"])) / scale,
                int(row["volume"]),
            )
        return out

    def fetch_official_closes(
        self,
        symbols: list[str],
        dataset: str,
        start: str,
        end: str,
    ) -> dict[str, dict[date, Decimal]]:
        """
        Fetch official closing auction prices from a listing venue; this spends.

        Unlike ``ohlcv-1d``, which is derived from trades and can differ from the
        official print, this reads the venue's own closing statistic.

        """
        rows = self._get(
            "timeseries.get_range",
            {
                "dataset": dataset,
                "symbols": ",".join(symbols),
                "schema": "statistics",
                "start": start,
                "end": end,
                "stype_in": "raw_symbol",
                "encoding": "json",
            },
        )
        scale = Decimal(10) ** 9
        intervals = self.resolve_symbols(symbols, start, end, dataset)
        out: dict[str, dict[date, Decimal]] = {s: {} for s in symbols}
        for row in rows:
            if int(row.get("stat_type", -1)) != STAT_CLOSING_PRICE:
                continue
            header = row.get("hd", row)
            day = datetime.fromtimestamp(int(header["ts_event"]) / 1e9, tz=UTC).date()
            name = _symbol_for(intervals, int(header["instrument_id"]), day)
            if name in out:
                out[name][day] = Decimal(str(row["price"])) / scale
        return out

    def fetch_to_file(
        self,
        *,
        dataset: str,
        symbols: list[str],
        schema: str,
        start: str,
        end: str,
        path: Path,
    ) -> int:
        """
        Stream a bulk range to disk as zstd-compressed CSV; this spends.

        Returns the bytes written.

        **Not JSON.** Cost is billed on the uncompressed binary size either way, so the
        encoding is free to choose - but a year of one-minute bars for twenty symbols is
        tens of millions of rows, and decoding that as JSON in memory is the difference
        between a minute and an hour. CSV streamed straight to a file also means the
        pull survives being analysed twice without being bought twice.

        """
        query = urlencode(
            {
                "dataset": dataset,
                "symbols": ",".join(symbols),
                "schema": schema,
                "start": start,
                "end": end,
                "stype_in": "raw_symbol",
                "encoding": "csv",
                "compression": "zstd",
            },
        )
        token = base64.b64encode(f"{self._api_key}:".encode()).decode()
        request = Request(  # noqa: S310 - fixed https scheme from DEFAULT_BASE_URL
            f"{self._base_url}/timeseries.get_range?{query}",
            headers={"Authorization": f"Basic {token}", "User-Agent": USER_AGENT},
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        try:
            with (
                urlopen(request, timeout=TIMEOUT_SECONDS * 30) as response,  # noqa: S310
                path.open("wb") as sink,
            ):
                while chunk := response.read(1 << 20):
                    sink.write(chunk)
                    written += len(chunk)
        except HTTPError as e:
            detail = e.read().decode(errors="replace")[:400]
            raise DatabentoError(f"bulk fetch failed: HTTP {e.code} {detail}") from e
        except URLError as e:
            raise DatabentoError(f"bulk fetch failed: {e.reason}") from e
        return written

    def resolve_symbols(
        self,
        symbols: list[str],
        start: str,
        end: str,
        dataset: str | None = None,
    ) -> list[tuple[str, int, date, date]]:
        """
        Map ticker to instrument id over a window. Free.

        Data rows identify their instrument by **numeric id only** - there is no symbol
        field on the wire - so a multi-symbol pull cannot be attributed without this.
        The mapping is date-ranged because an id may be reassigned, so it is returned as
        intervals rather than a flat dictionary.

        """
        raw = self._get(
            "symbology.resolve",
            {
                "dataset": dataset or DAILY_DATASET,
                "symbols": ",".join(symbols),
                "stype_in": "raw_symbol",
                "stype_out": "instrument_id",
                "start_date": start,
                "end_date": end,
            },
        )
        resolved = [
            (
                symbol.upper(),
                int(span["s"]),
                date.fromisoformat(span["d0"]),
                date.fromisoformat(span["d1"]),
            )
            for symbol, spans in (raw.get("result") or {}).items()
            for span in spans
        ]
        missing = list(raw.get("not_found") or [])
        if missing:
            raise DatabentoError(f"symbols not found on {dataset or DAILY_DATASET}: {missing}")
        return resolved

    def cost(
        self,
        symbols: list[str],
        schema: str,
        start: str,
        end: str,
        dataset: str | None = None,
    ) -> Decimal:
        """
        Return the US dollar cost of a pull, without making it.

        Free.

        """
        raw = self._get(
            "metadata.get_cost",
            {
                "dataset": dataset or self.dataset,
                "symbols": ",".join(symbols),
                "schema": schema,
                "start": start,
                "end": end,
                "stype_in": "raw_symbol",
                "mode": "historical",
            },
        )
        # The endpoint answers with a bare number.
        return Decimal(str(raw))


def _symbol_for(
    intervals: list[tuple[str, int, date, date]],
    instrument_id: int,
    day: date,
) -> str:
    """
    Return the ticker an instrument id stood for on a given day, or "" if none did.
    """
    for symbol, resolved_id, first, last in intervals:
        if resolved_id == instrument_id and first <= day <= last:
            return symbol
    return ""


def normalize_minute(raw: dict[str, Any]) -> Minute:
    """
    Turn one vendor row into a ``Minute``.

    Prices arrive as integers scaled by 1e-9. They are converted through ``Decimal``
    rather than float so the exact arithmetic the rest of the project relies on is not
    broken at the boundary.

    """
    scale = Decimal(10) ** 9

    def price(field: str) -> Decimal:
        return Decimal(str(raw[field])) / scale

    stamp = raw.get("ts_event") or raw.get("hd", {}).get("ts_event")
    opened_at = datetime.fromtimestamp(int(stamp) / 1e9, tz=UTC)
    return Minute(
        opened_at=opened_at,
        open=price("open"),
        high=price("high"),
        low=price("low"),
        close=price("close"),
        volume=int(raw["volume"]),
    )


def measure(symbol: str, dataset: str, minutes: list[Minute]) -> ProbeFinding:
    """
    Run the fidelity checks over a sample, and return what they found.
    """
    if not minutes:
        raise DatabentoError(f"no rows returned for {symbol}; nothing to measure")

    distinct = {
        field: len(Counter(getattr(m, field) for m in minutes))
        for field in ("open", "high", "low", "close")
    }
    volumes = [m.volume for m in minutes]
    monotonic = all(b >= a for a, b in pairwise(volumes))
    return ProbeFinding(
        symbol=symbol,
        dataset=dataset,
        rows=len(minutes),
        distinct=distinct,
        volume_is_monotonic=monotonic,
        volume_sum=sum(volumes),
        incoherent_rows=sum(1 for m in minutes if not m.coherent),
    )


@dataclass(frozen=True)
class SymbolAudit:
    """
    One symbol's catalog checked against an independent daily series.
    """

    symbol: str
    days_compared: int
    exact_closes: int
    max_deviation_bps: Decimal
    worst_day: str
    median_volume_ratio: Decimal

    @property
    def clean(self) -> bool:
        """
        Return whether every overlapping close matched to the cent.
        """
        return self.days_compared > 0 and self.exact_closes == self.days_compared

    def row(self) -> str:
        """
        Return one fixed-width line for the audit table.
        """
        share = f"{self.exact_closes}/{self.days_compared}"
        return (
            f"  {self.symbol:<7}{share:>10}{self.max_deviation_bps:>12}"
            f"{self.worst_day:>13}{self.median_volume_ratio:>10}  "
            f"{'ok' if self.clean else 'MISMATCH'}"
        )


def audit_symbol(
    symbol: str,
    vendor: dict[date, tuple[Decimal, int]],
    catalog: dict[date, tuple[Decimal, int]],
) -> SymbolAudit:
    """
    Compare one symbol's closes and volumes over the days both sources carry.

    Only overlapping days are compared, so a shorter vendor window narrows the audit
    rather than reporting the missing days as failures. The catalog holds vendor prices
    that are already split-adjusted while Databento reports them as traded, so a split
    inside the window would surface here as a large deviation - which is a finding, not
    a bug in this function.

    """
    shared = sorted(set(vendor) & set(catalog))
    exact = 0
    worst = Decimal(0)
    worst_day = "-"
    ratios = []
    for day in shared:
        vendor_close, vendor_volume = vendor[day]
        catalog_close, catalog_volume = catalog[day]
        if vendor_close == catalog_close:
            exact += 1
        deviation = abs(catalog_close - vendor_close) / catalog_close * 10000
        if deviation > worst:
            worst, worst_day = deviation, day.isoformat()
        if catalog_volume:
            ratios.append(Decimal(vendor_volume) / Decimal(catalog_volume))

    ratios.sort()
    median = ratios[len(ratios) // 2] if ratios else Decimal(0)
    return SymbolAudit(
        symbol=symbol,
        days_compared=len(shared),
        exact_closes=exact,
        max_deviation_bps=worst.quantize(Decimal("0.01")),
        worst_day=worst_day,
        median_volume_ratio=median.quantize(Decimal("0.001")),
    )


def catalog_series(
    catalog_path: str,
) -> dict[str, tuple[str, dict[date, tuple[Decimal, int]]]]:
    """
    Read every symbol the catalog holds, as venue plus closes and volumes by day.

    Imported here rather than at module scope so the survey and cost paths, which are
    the ones an operator runs first, do not need the Nautilus extension present.

    """
    from copilot.data.catalog import bar_type_for  # noqa: PLC0415
    from copilot.data.catalog import equity_for  # noqa: PLC0415
    from copilot.data.catalog import open_catalog  # noqa: PLC0415
    from copilot.data.catalog import read_daily_bars  # noqa: PLC0415

    root = Path(catalog_path).expanduser() / "data" / "bars"
    catalog = open_catalog(catalog_path)
    held: dict[str, tuple[str, dict[date, tuple[Decimal, int]]]] = {}
    for entry in sorted(root.iterdir()):
        symbol, _, rest = entry.name.partition(".")
        venue = rest.split("-", 1)[0]
        instrument = equity_for(symbol, venue)
        # Raw, deliberately: the audit compares against as-traded official prints,
        # and a back-adjusted series cannot be checked against one.
        bars = read_daily_bars(catalog, bar_type_for(instrument.id), adjust=False)
        held[symbol] = (venue, {b.closed_at.date(): (b.close, b.volume) for b in bars})
    return held


def catalog_closes(catalog_path: str) -> dict[str, dict[date, tuple[Decimal, int]]]:
    """
    Read the catalog, discarding the venue.
    """
    return {symbol: series for symbol, (_, series) in catalog_series(catalog_path).items()}


def _pull(client: DatabentoClient, args: argparse.Namespace) -> int:
    """
    Buy one schema over the catalog's universe, routed per listing venue.

    Prints the metered price of every leg before buying any of it, because a bundle is
    where a mistake compounds: four legs at the wrong schema is four times the surprise.

    """
    held = catalog_series(args.catalog)
    by_venue: dict[str, list[str]] = {}
    for symbol, (venue, _) in held.items():
        by_venue.setdefault(venue, []).append(symbol)

    legs = []
    for venue, symbols in sorted(by_venue.items()):
        dataset = args.dataset or LISTING_DATASETS.get(venue)
        if dataset is None:
            print(f"  no listing dataset for venue {venue}; skipped {sorted(symbols)}")
            continue
        price = client.cost(sorted(symbols), args.schema, args.start, args.end, dataset)
        legs.append((dataset, sorted(symbols), price))
        print(f"  {dataset:<14}{len(symbols):>3} symbols  {args.schema:<10}${price:>9.4f}")

    total = sum(price for _, _, price in legs)
    print(f"  {'TOTAL':<14}{'':>3}          {'':<10}${total:>9.4f}")
    if not args.spend:
        print("\n  priced only. Pass --spend to buy it.", file=sys.stderr)
        return 0
    if total > args.budget:
        print(
            f"\n  refusing: ${total:.2f} exceeds --budget ${args.budget:.2f}",
            file=sys.stderr,
        )
        return 2

    store = Path(args.store).expanduser()
    for dataset, symbols, _ in legs:
        path = store / dataset / args.schema / f"{args.start}_{args.end}.csv.zst"
        print(f"\n  fetching {dataset} {args.schema} -> {path}", flush=True)
        written = client.fetch_to_file(
            dataset=dataset,
            symbols=symbols,
            schema=args.schema,
            start=args.start,
            end=args.end,
            path=path,
        )
        print(f"  wrote {written / 1e6:.1f} MB", flush=True)

        # The rows identify their instrument by numeric id and carry no symbol, so a
        # pull without its map is unreadable later. It is free, so it is never skipped.
        intervals = client.resolve_symbols(symbols, args.start, args.end, dataset)
        sidecar = path.with_suffix(".symbology.json")
        sidecar.write_text(
            json.dumps(
                [
                    {"symbol": s, "instrument_id": i, "from": a.isoformat(), "to": b.isoformat()}
                    for s, i, a, b in intervals
                ],
                indent=2,
            )
            + "\n",
        )
        print(f"  mapped {len(intervals)} instrument ids -> {sidecar.name}", flush=True)
    return 0


def _client(args: argparse.Namespace) -> DatabentoClient:
    key = os.environ.get(API_KEY_ENV, "")
    dataset = args.dataset or os.environ.get(DATASET_ENV) or DEFAULT_DATASET
    return DatabentoClient(api_key=key, dataset=dataset)


def _survey(client: DatabentoClient) -> None:
    """
    Print every equities dataset the key reaches, and the window each covers.
    """
    datasets = client.list_datasets()
    print(f"{len(datasets)} datasets reachable with this key.\n")
    equities = [d for d in datasets if d.startswith(("EQUS", "XNAS", "XNYS", "DBEQ", "ARCX"))]
    for name in sorted(equities):
        try:
            window = client.dataset_range(name)
            print(f"  {name:<20} {window.start}  ->  {window.end}")
        except DatabentoError as e:
            print(f"  {name:<20} range unavailable: {e}")
    print(f"\n  ({len(datasets) - len(equities)} non-equities datasets not listed)")


def _cost(client: DatabentoClient, symbols: list[str], start: str, end: str) -> None:
    """
    Print what a one-minute pull would cost, without making it.
    """
    price = client.cost(symbols, "ohlcv-1m", start, end)
    print(
        f"\nohlcv-1m, {len(symbols)} symbols, {start} to {end}: ${price} on {client.dataset}",
    )


def _audit_deep(client: DatabentoClient, catalog_path: str, start: str, end: str) -> int:
    """
    Check the catalog against each listing venue's official closing auction print.

    Routed per venue because the auction that sets a security's official close is run by
    the venue it is listed on, and those datasets reach 2018-05-01 rather than the
    consolidated summary's 2024-07-01.

    """
    held = catalog_series(catalog_path)
    by_venue: dict[str, list[str]] = {}
    for symbol, (venue, _) in held.items():
        by_venue.setdefault(venue, []).append(symbol)

    audits = []
    for venue, symbols in sorted(by_venue.items()):
        dataset = LISTING_DATASETS.get(venue)
        if dataset is None:
            print(f"  no listing dataset for venue {venue}; skipped {sorted(symbols)}")
            continue
        official = client.fetch_official_closes(sorted(symbols), dataset, start, end)
        for symbol in sorted(symbols):
            series = {day: (price, 0) for day, price in official.get(symbol, {}).items()}
            audits.append(audit_symbol(symbol, series, held[symbol][1]))

    print(f"\nCatalog audited against official closing auction prints, {start} to {end}")
    print(f"  {'symbol':<7}{'exact':>12}{'worst bps':>12}{'worst day':>13}")
    for entry in sorted(audits, key=lambda a: a.symbol):
        share = f"{entry.exact_closes}/{entry.days_compared}"
        flag = "ok" if entry.clean else "MISMATCH"
        print(
            f"  {entry.symbol:<7}{share:>12}{entry.max_deviation_bps:>12}"
            f"{entry.worst_day:>13}  {flag}",
        )
    clean = sum(1 for a in audits if a.clean)
    compared = sum(a.days_compared for a in audits)
    disagreed = sum(a.days_compared - a.exact_closes for a in audits)
    print(f"\n  {clean}/{len(audits)} symbols exact across {compared:,} days")
    print(f"  {disagreed} disagreements ({disagreed / compared * 100:.3f}%)")
    return 0


def _audit(client: DatabentoClient, catalog_path: str, start: str, end: str) -> int:
    """
    Check the catalog's closes against an independent daily series.
    """
    held = catalog_closes(catalog_path)
    vendor = client.fetch_daily(sorted(held), start, end, DAILY_DATASET)
    audits = [audit_symbol(s, vendor.get(s, {}), held[s]) for s in sorted(held)]
    print(f"\nCatalog audited against {DAILY_DATASET}, {start} to {end}")
    print(
        f"  {'symbol':<7}{'exact':>10}{'worst bps':>12}{'worst day':>13}{'vol ratio':>10}",
    )
    for entry in audits:
        print(entry.row())
    clean = sum(1 for a in audits if a.clean)
    compared = sum(a.days_compared for a in audits)
    print(f"\n  {clean}/{len(audits)} symbols matched exactly across {compared} days")
    if clean != len(audits):
        return 1

    return 0


def _probe(client: DatabentoClient, symbol: str, start: str, end: str) -> int:
    """
    Measure whether a feed's one-minute rows are bars at all.
    """
    minutes = client.fetch_minutes(symbol, start, end)
    finding = measure(symbol, client.dataset, minutes)
    print()
    print(finding.report())
    if not finding.looks_like_real_bars:
        return 1

    return 0


def _preflight(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """
    Refuse a run that names no action, or one that would spend without saying so.

    ``--pull`` is exempt from the second rule because it prices every leg and prints
    the total before it buys anything; the others go straight to the wire.

    """
    if not (args.survey or args.cost or args.probe or args.audit or args.pull):
        parser.error("choose --survey, --cost, --probe SYMBOL, --audit, or --pull")
    if (args.audit or args.probe) and not args.spend:
        parser.error("--audit and --probe spend credit: price it with --cost, then --spend")


def main(argv: list[str] | None = None) -> int:
    """
    Survey the account, price a pull, or probe a vendor's intraday fidelity.

    Returns a process exit code. A probe that fails its checks exits non-zero, so an
    unusable feed cannot be mistaken for a usable one by a caller reading status.

    """
    parser = argparse.ArgumentParser(
        prog="python -m copilot.data.databento",
        description="Survey Databento, price a pull, and probe intraday fidelity.",
    )
    parser.add_argument("--survey", action="store_true", help="List datasets and ranges (free)")
    parser.add_argument("--cost", action="store_true", help="Price a one-minute pull (free)")
    parser.add_argument("--probe", metavar="SYMBOL", help="Measure intraday fidelity (spends)")
    parser.add_argument(
        "--audit",
        action="store_true",
        help="Check the catalog's closes against the daily series (spends)",
    )
    parser.add_argument("--catalog", default=DEFAULT_CATALOG, help="Catalog directory")
    parser.add_argument("--pull", action="store_true", help="Buy one schema in bulk")
    parser.add_argument("--schema", default="ohlcv-1m", help="Schema for --pull")
    parser.add_argument("--store", default=DEFAULT_STORE, help="Where bulk pulls land")
    parser.add_argument(
        "--budget",
        type=float,
        default=25.0,
        help="Refuse a pull priced above this, in USD",
    )
    parser.add_argument(
        "--deep",
        action="store_true",
        help="Audit against official auction prints, back to 2018 (spends)",
    )
    parser.add_argument("--spend", action="store_true", help="Permit the probe to incur cost")
    parser.add_argument("--dataset", help=f"Dataset override (default {DEFAULT_DATASET})")
    parser.add_argument("--symbols", default="AAPL,MSFT,SPY", help="Comma-separated symbols")
    parser.add_argument("--from", dest="start", default="2024-01-02", help="Start date")
    parser.add_argument("--to", dest="end", default="2024-01-03", help="End date")
    args = parser.parse_args(argv)

    _preflight(parser, args)

    client = _client(args)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    if args.survey:
        _survey(client)

    if args.cost:
        _cost(client, symbols, args.start, args.end)

    if args.pull:
        failed = _pull(client, args)
        if failed:
            return failed

    if args.audit:
        step = _audit_deep if args.deep else _audit
        failed = step(client, args.catalog, args.start, args.end)
        if failed:
            return failed

    if args.probe:
        failed = _probe(client, args.probe.upper(), args.start, args.end)
        if failed:
            return failed

    return 0


if __name__ == "__main__":
    sys.exit(main())
