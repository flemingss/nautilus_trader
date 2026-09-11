"""
Probe EODHD's daily series with the checks that caught Marketstack.

    python -m copilot.data.eodhd_probe --symbols AAPL.XNAS
    python -m copilot.data.eodhd_probe --symbols AAPL.XNAS --write
    EODHD_API_KEY=... python -m copilot.data.eodhd_probe --symbols AAPL.XNAS,SPY.ARCX --write

Read-only. It fetches from the vendor, reads the registered corporate actions and the
Databento store, and prints what it found; ``--write`` files the findings under ``data/out/``.
It constructs no execution client and writes nothing to the catalog.

Why this exists
---------------
[ADR-0015] names EODHD as the candidate to replace Marketstack's 2005-2018 daily series, and
the owner's call of 2026-09-05 was to probe it and cancel nothing: it is adopted only if it
passes the checks Marketstack failed. Those checks lived in four modules, each written against
one vendor's wire format. This asks all of them of another vendor's series, in one place.

The checks
----------
1. **Coherent bars.** Open and close inside their own high and low, and a series of distinct
   values rather than a repeated payload. Marketstack's adjusted set put 22% of rows outside
   their own range.
2. **Real sessions.** No bar on a day the market was shut, and no session missing inside the
   series. Marketstack returned holiday bars with nine-figure volume.
3. **Whole-cent closes.** An as-traded close above a dollar that is not a whole cent was not
   printed by an auction - or was rebuilt by multiplying an adjusted series back up.
4. **As-traded splits.** The series jumps by about a split's factor on each registered
   effective date and on no other day, and the vendor's own split list names the same dates.

And one measurement that makes the rest mean something: **agreement with the official close**,
the closing-auction print the Databento store holds for the listing venue. Agreement with the
catalog proves little, because the catalog came from Marketstack and two vendors can share an
upstream - on AAPL the two agree to a basis point on every one of 5,455 sessions.

The demo token
--------------
EODHD serves AAPL under its public ``demo`` token and refuses other US tickers. That exercises
every check on one real twenty-year series with three splits in it; it cannot adopt a vendor
for nine symbols. Without ``EODHD_API_KEY`` the probe uses the demo token and says so.

[ADR-0015]: ../docs/decisions/0015-databento-is-the-intraday-source-only.md

"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC
from datetime import date
from datetime import datetime
from decimal import Decimal
from decimal import InvalidOperation
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request
from urllib.request import urlopen

from copilot.data.calendar import is_trading_day
from copilot.data.calendar import trading_days
from copilot.data.corporate_actions import split_actions
from copilot.data.databento import DEFAULT_STORE
from copilot.data.databento import LISTING_DATASETS
from copilot.data.patch import read_official_closes
from copilot.paths import EODHD_API_KEY_ENV


if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Mapping
    from collections.abc import Sequence


DEMO_TOKEN = "demo"  # noqa: S105 - EODHD's published public token, not a credential
BASE_URL = "https://eodhd.com/api"
EARLIEST = date(2005, 1, 1)
OUT_DIR = Path(__file__).parent / "out"

PENNY = Decimal("0.01")
SUB_DOLLAR = Decimal(1)

SPLIT_MOVE = Decimal("1.4")
"""
A day whose previous close is more than 1.4 times its open, either way, is read as a
split.

The smallest share split the registry holds is 3-for-2. No session in these series has
moved 40% overnight on news, and a jump that large on a day no split explains is exactly
the finding.

"""

SPLIT_TOLERANCE = Decimal("0.15")
"""
How far an observed jump may sit from the split's factor and still be that split.

The jump is previous close over next open, so it carries one overnight gap on top of the
factor: AAPL's 7-for-1 of 2014-06-09 measured 6.964.

"""

MATERIAL_BPS = Decimal(10)
"""
A close further than this from the official print is materially wrong, as ADR-0015
counted.
"""

REPEATED_SHARE = Decimal("0.5")
"""
A field with fewer distinct values than half its rows is a repeated payload, not a
series.
"""


@dataclass(frozen=True)
class VendorBar:
    """
    One daily bar as the vendor sent it, prices as-traded.
    """

    day: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


@dataclass(frozen=True)
class SplitMove:
    """
    A day the series jumped by a split-sized ratio: previous close over this open.
    """

    day: date
    ratio: Decimal


@dataclass(frozen=True)
class SplitFindings:
    """
    How the series' jumps, the registry and the vendor's split list line up.
    """

    moves: tuple[SplitMove, ...]
    unexplained: tuple[SplitMove, ...]
    """
    Jumps on a day no registered split explains.
    """
    unseen: tuple[date, ...]
    """
    Registered splits inside the series with no jump near their factor.
    """
    vendor_only: tuple[date, ...]
    """
    Splits the vendor lists inside the series that the registry does not hold.
    """
    registry_only: tuple[date, ...]
    """
    Registered splits inside the series that the vendor does not list.
    """

    @property
    def passed(self) -> bool:
        """
        Return whether every jump is a registered split and both lists agree.
        """
        return not (self.unexplained or self.unseen or self.vendor_only or self.registry_only)


@dataclass(frozen=True)
class Agreement:
    """
    The vendor's closes against the official closing-auction print.
    """

    compared: int
    exact: int
    material: tuple[tuple[date, Decimal, Decimal, Decimal], ...]
    """
    ``(day, bps, vendor close, official close)`` for every close over
    :data:`MATERIAL_BPS`.
    """
    worst: tuple[date, Decimal] | None

    @property
    def passed(self) -> bool:
        """
        Return whether the closes were measured at all and none is materially wrong.
        """
        return self.compared > 0 and not self.material


@dataclass(frozen=True)
class ProbeResult:
    """
    Every check for one symbol, and whether the vendor passes on it.
    """

    symbol: str
    venue: str
    ticker: str
    demo: bool
    rows: int
    first: date | None
    last: date | None
    incoherent: tuple[date, ...]
    distinct: dict[str, int]
    phantom: tuple[date, ...]
    missing: tuple[date, ...]
    sub_penny: tuple[date, ...]
    splits: SplitFindings
    agreement: Agreement

    @property
    def repeated_fields(self) -> tuple[str, ...]:
        """
        Return the price fields whose values repeat more than a real series' would.
        """
        if not self.rows:
            return ()
        return tuple(
            name
            for name, count in sorted(self.distinct.items())
            if Decimal(count) / Decimal(self.rows) < REPEATED_SHARE
        )

    @property
    def failures(self) -> tuple[str, ...]:
        """
        Return the name of every check this symbol failed.
        """
        failed = []
        if not self.rows:
            failed.append("no rows")
        if self.incoherent or self.repeated_fields:
            failed.append("coherent bars")
        if self.phantom or self.missing:
            failed.append("real sessions")
        if self.sub_penny:
            failed.append("whole-cent closes")
        if not self.splits.passed:
            failed.append("as-traded splits")
        if not self.agreement.passed:
            failed.append("official close")
        return tuple(failed)

    @property
    def passed(self) -> bool:
        """
        Return whether the vendor passed every check on this symbol.
        """
        return not self.failures

    def as_record(self) -> dict[str, object]:
        """
        Return the JSON form: counts, the dates that failed, every number as a string.
        """
        return {
            "symbol": self.symbol,
            "venue": self.venue,
            "ticker": self.ticker,
            "token": "demo" if self.demo else "key",
            "rows": self.rows,
            "first": _iso(self.first),
            "last": _iso(self.last),
            "passed": self.passed,
            "failures": list(self.failures),
            "coherent_bars": {
                "incoherent": [d.isoformat() for d in self.incoherent],
                "distinct": self.distinct,
                "repeated_fields": list(self.repeated_fields),
            },
            "real_sessions": {
                "phantom": [d.isoformat() for d in self.phantom],
                "missing": [d.isoformat() for d in self.missing],
            },
            "whole_cent_closes": {
                "sub_penny": len(self.sub_penny),
                "first": _iso(self.sub_penny[0] if self.sub_penny else None),
                "last": _iso(self.sub_penny[-1] if self.sub_penny else None),
            },
            "as_traded_splits": {
                "moves": [
                    {"day": m.day.isoformat(), "ratio": str(m.ratio)} for m in self.splits.moves
                ],
                "unexplained": [m.day.isoformat() for m in self.splits.unexplained],
                "unseen": [d.isoformat() for d in self.splits.unseen],
                "vendor_only": [d.isoformat() for d in self.splits.vendor_only],
                "registry_only": [d.isoformat() for d in self.splits.registry_only],
            },
            "official_close": {
                "compared": self.agreement.compared,
                "exact": self.agreement.exact,
                "material": [
                    {"day": d.isoformat(), "bps": str(bps), "vendor": str(v), "official": str(o)}
                    for d, bps, v, o in self.agreement.material
                ],
                "worst": (
                    {
                        "day": self.agreement.worst[0].isoformat(),
                        "bps": str(self.agreement.worst[1]),
                    }
                    if self.agreement.worst
                    else None
                ),
            },
        }


def parse_bars(rows: Sequence[Mapping[str, Any]]) -> tuple[VendorBar, ...]:
    """
    Turn the vendor's rows into bars in date order, prices through ``Decimal``.

    The vendor sends JSON numbers, which a parser reads as binary floats; each goes through
    its shortest string form so ``63.2912`` stays ``63.2912``.

    """
    return tuple(
        sorted(
            (
                VendorBar(
                    day=date.fromisoformat(str(row["date"])),
                    open=_decimal(row["open"]),
                    high=_decimal(row["high"]),
                    low=_decimal(row["low"]),
                    close=_decimal(row["close"]),
                    volume=int(row.get("volume") or 0),
                )
                for row in rows
            ),
            key=lambda bar: bar.day,
        ),
    )


def parse_vendor_splits(rows: Sequence[Mapping[str, Any]]) -> tuple[tuple[date, Decimal], ...]:
    """
    Read the vendor's split list, ``"7.000000/1.000000"`` becoming a factor of 7.
    """
    splits = []
    for row in rows:
        new, _, old = str(row["split"]).partition("/")
        splits.append((date.fromisoformat(str(row["date"])), _decimal(new) / _decimal(old or "1")))
    return tuple(sorted(splits))


def incoherent_days(bars: Sequence[VendorBar]) -> tuple[date, ...]:
    """
    Return the days whose open or close sits outside that day's own high and low.
    """
    return tuple(
        bar.day
        for bar in bars
        if not (bar.low <= bar.open <= bar.high and bar.low <= bar.close <= bar.high)
    )


def distinct_values(bars: Sequence[VendorBar]) -> dict[str, int]:
    """
    Return how many distinct values each price field takes.
    """
    return {
        name: len({getattr(bar, name) for bar in bars}) for name in ("open", "high", "low", "close")
    }


def phantom_days(bars: Sequence[VendorBar]) -> tuple[date, ...]:
    """
    Return the days the vendor sent a bar for and the market held no session.
    """
    return tuple(bar.day for bar in bars if not is_trading_day(bar.day))


def missing_days(bars: Sequence[VendorBar]) -> tuple[date, ...]:
    """
    Return the sessions missing between the series' own first and last bar.
    """
    if not bars:
        return ()
    held = {bar.day for bar in bars}
    return tuple(day for day in trading_days(bars[0].day, bars[-1].day) if day not in held)


def sub_penny_closes(bars: Sequence[VendorBar]) -> tuple[date, ...]:
    """
    Return the days whose close is above a dollar and not a whole cent.
    """
    return tuple(bar.day for bar in bars if bar.close >= SUB_DOLLAR and bar.close % PENNY != 0)


def split_moves(bars: Sequence[VendorBar]) -> tuple[SplitMove, ...]:
    """
    Return every day the series jumped by a split-sized ratio, either way.
    """
    moves = []
    for previous, current in pairwise(bars):
        if current.open <= 0:
            continue
        ratio = previous.close / current.open
        if ratio > SPLIT_MOVE or ratio < 1 / SPLIT_MOVE:
            moves.append(SplitMove(current.day, ratio.quantize(Decimal("0.001"))))
    return tuple(moves)


def compare_splits(
    moves: Sequence[SplitMove],
    *,
    registered: Sequence[tuple[date, Decimal]],
    vendor: Sequence[tuple[date, Decimal]],
    first: date,
    last: date,
) -> SplitFindings:
    """
    Line the series' jumps up against the registry and the vendor's own split list.

    Only splits strictly after the first bar and by the last count: one on the first day
    leaves no previous close to jump from, and the vendor's list reaches back further than the
    probe asks for.

    """
    in_series = {day: factor for day, factor in registered if first < day <= last}
    by_day = {move.day: move for move in moves}
    vendor_days = {day for day, _ in vendor if first < day <= last}
    return SplitFindings(
        moves=tuple(moves),
        unexplained=tuple(move for move in moves if move.day not in in_series),
        unseen=tuple(
            day
            for day, factor in sorted(in_series.items())
            if day not in by_day or abs(by_day[day].ratio / factor - 1) > SPLIT_TOLERANCE
        ),
        vendor_only=tuple(sorted(vendor_days - in_series.keys())),
        registry_only=tuple(sorted(in_series.keys() - vendor_days)),
    )


def agreement(bars: Sequence[VendorBar], official: Mapping[date, Decimal]) -> Agreement:
    """
    Measure the vendor's closes against the official print on every day both exist.
    """
    gaps = []
    for bar in bars:
        print_ = official.get(bar.day)
        if print_ is None or print_ <= 0:
            continue
        bps = abs(bar.close - print_) / print_ * 10_000
        gaps.append((bar.day, bps, bar.close, print_))
    worst = max(gaps, key=lambda gap: gap[1]) if gaps else None
    return Agreement(
        compared=len(gaps),
        exact=sum(1 for gap in gaps if gap[1] == 0),
        material=tuple(
            (day, bps.quantize(Decimal("0.01")), close, print_)
            for day, bps, close, print_ in gaps
            if bps > MATERIAL_BPS
        ),
        worst=(worst[0], worst[1].quantize(Decimal("0.01"))) if worst else None,
    )


def probe(  # noqa: PLR0913 - one symbol's identity and its three sources
    symbol: str,
    venue: str,
    *,
    rows: Sequence[Mapping[str, Any]],
    vendor_splits: Sequence[Mapping[str, Any]],
    official: Mapping[date, Decimal],
    demo: bool,
) -> ProbeResult:
    """
    Run every check on one symbol's rows.

    No network, no catalog: the sources are passed in.

    """
    bars = parse_bars(rows)
    first = bars[0].day if bars else None
    last = bars[-1].day if bars else None
    moves = split_moves(bars)
    splits = compare_splits(
        moves,
        registered=[(a.effective.date(), a.factor) for a in split_actions(symbol)],
        vendor=parse_vendor_splits(vendor_splits),
        first=first or EARLIEST,
        last=last or EARLIEST,
    )
    return ProbeResult(
        symbol=symbol,
        venue=venue,
        ticker=ticker_for(symbol),
        demo=demo,
        rows=len(bars),
        first=first,
        last=last,
        incoherent=incoherent_days(bars),
        distinct=distinct_values(bars),
        phantom=phantom_days(bars),
        missing=missing_days(bars),
        sub_penny=sub_penny_closes(bars),
        splits=splits,
        agreement=agreement(bars, official),
    )


def ticker_for(symbol: str) -> str:
    """
    Return EODHD's ticker for a US listing.
    """
    return f"{symbol.upper()}.US"


class EodhdClient:
    """
    The two endpoints the probe reads.

    The token rides in the query and is never printed.

    """

    def __init__(self, token: str, opener: Callable[..., Any] = urlopen) -> None:
        """
        Hold the token and the function that opens a request.
        """
        self._token = token
        self._opener = opener

    def eod(self, ticker: str, start: date) -> list[dict[str, Any]]:
        """
        Return the daily bars from ``start``.
        """
        return self._get(f"eod/{ticker}", {"from": start.isoformat()})

    def splits(self, ticker: str, start: date) -> list[dict[str, Any]]:
        """
        Return the vendor's split list from ``start``.
        """
        return self._get(f"splits/{ticker}", {"from": start.isoformat()})

    def _get(self, path: str, params: Mapping[str, str]) -> list[dict[str, Any]]:
        query = urlencode({**params, "api_token": self._token, "fmt": "json"})
        request = Request(  # noqa: S310 - a fixed https base URL
            f"{BASE_URL}/{path}?{query}",
            headers={"User-Agent": "copilot-eodhd-probe"},
        )
        with self._opener(request, timeout=60) as response:
            return json.loads(response.read())


def print_result(result: ProbeResult) -> None:
    """
    Print one symbol's checks the way the other operator tables read.
    """
    token = "demo token" if result.demo else "key"
    print(
        f"\n  {result.symbol}.{result.venue}  via {result.ticker} ({token})  "
        f"{result.rows} bars {_iso(result.first)}..{_iso(result.last)}",
    )
    distinct = result.distinct.values()
    lines = (
        (
            "coherent bars",
            not (result.incoherent or result.repeated_fields),
            (
                f"{len(result.incoherent)} of {result.rows} outside their own high-low; distinct "
                f"values {min(distinct, default=0)}..{max(distinct, default=0)} per field"
            ),
        ),
        (
            "real sessions",
            not (result.phantom or result.missing),
            f"{len(result.phantom)} on closed days, {len(result.missing)} missing",
        ),
        (
            "whole-cent closes",
            not result.sub_penny,
            (
                f"{len(result.sub_penny)} above a dollar are not whole cents"
                + (f", {result.sub_penny[0]} .. {result.sub_penny[-1]}" if result.sub_penny else "")
            ),
        ),
        (
            "as-traded splits",
            result.splits.passed,
            (
                f"{len(result.splits.moves)} jumps; "
                f"unexplained {len(result.splits.unexplained)}, "
                f"registered but unseen {len(result.splits.unseen)}, "
                f"vendor-only {len(result.splits.vendor_only)}, "
                f"registry-only {len(result.splits.registry_only)}"
            ),
        ),
        (
            "official close",
            result.agreement.passed,
            (
                f"{result.agreement.compared} compared, {result.agreement.exact} exact, "
                f"{len(result.agreement.material)} over {MATERIAL_BPS} bps"
                + (f", worst {result.agreement.worst[1]} bps" if result.agreement.worst else "")
                + ("" if result.agreement.compared else "; the store holds no official closes")
            ),
        ),
    )
    for name, passed, detail in lines:
        print(f"    [{'PASS' if passed else 'FAIL'}] {name:18} {detail}")
    if result.passed:
        print("  passes every check on this symbol")
    else:
        print(f"  NOT ADOPTABLE on this symbol: {', '.join(result.failures)}")


def main(argv: list[str] | None = None) -> int:
    """
    Probe each requested symbol, print the checks, and optionally file them.
    """
    parser = argparse.ArgumentParser(
        prog="python -m copilot.data.eodhd_probe",
        description="Probe EODHD's daily series against the checks that caught Marketstack.",
    )
    parser.add_argument("--symbols", required=True, help="Comma-separated SYMBOL.VENUE pairs")
    parser.add_argument("--store", default=DEFAULT_STORE, help="The Databento store")
    parser.add_argument("--from", dest="start", default=EARLIEST.isoformat(), help="Start date")
    parser.add_argument("--write", action="store_true", help="File the findings under data/out/")
    args = parser.parse_args(argv)

    token = os.environ.get(EODHD_API_KEY_ENV) or DEMO_TOKEN
    demo = token == DEMO_TOKEN
    client = EodhdClient(token)
    start = date.fromisoformat(args.start)
    store = Path(args.store).expanduser()
    print(
        f"EODHD probe from {start}, "
        + (
            "on the public demo token, which serves AAPL only"
            if demo
            else f"on {EODHD_API_KEY_ENV}"
        ),
    )

    results: list[ProbeResult] = []
    refused: list[str] = []
    closes: dict[str, dict[tuple[str, date], Decimal]] = {}
    for token_pair in args.symbols.split(","):
        symbol, _, venue = token_pair.strip().upper().partition(".")
        if not symbol or not venue:
            print(f"error: expected SYMBOL.VENUE, got {token_pair.strip()!r}", file=sys.stderr)
            return 2
        try:
            rows = client.eod(ticker_for(symbol), start)
            vendor_splits = client.splits(ticker_for(symbol), start)
        except (HTTPError, URLError) as e:
            reason = getattr(e, "code", None) or getattr(e, "reason", e)
            print(f"\n  {symbol}.{venue}: the vendor refused ({reason}); not probed")
            refused.append(f"{symbol}.{venue}")
            continue
        dataset = LISTING_DATASETS.get(venue)
        if dataset and dataset not in closes:
            try:
                closes[dataset] = read_official_closes(store, dataset)
            except FileNotFoundError:
                closes[dataset] = {}
        official = {
            day: price
            for (held, day), price in closes.get(dataset or "", {}).items()
            if held == symbol
        }
        result = probe(
            symbol,
            venue,
            rows=rows,
            vendor_splits=vendor_splits,
            official=official,
            demo=demo,
        )
        print_result(result)
        results.append(result)

    if args.write and results:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
        path = OUT_DIR / f"eodhd_probe_{stamp}.json"
        record = {
            "run_at": datetime.now(tz=UTC).isoformat(),
            "token": "demo" if demo else "key",
            "from": start.isoformat(),
            "refused": refused,
            "results": [r.as_record() for r in results],
        }
        path.write_text(json.dumps(record, indent=2) + "\n")
        print(f"\nfiled {path}")
    return 0 if results and not refused and all(r.passed for r in results) else 1


def _decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value))
    except InvalidOperation as e:
        raise ValueError(f"not a number: {value!r}") from e


def _iso(day: date | None) -> str | None:
    return day.isoformat() if day else None


__all__ = [
    "Agreement",
    "EodhdClient",
    "ProbeResult",
    "SplitFindings",
    "SplitMove",
    "VendorBar",
    "agreement",
    "compare_splits",
    "distinct_values",
    "incoherent_days",
    "missing_days",
    "parse_bars",
    "parse_vendor_splits",
    "phantom_days",
    "probe",
    "split_moves",
    "sub_penny_closes",
    "ticker_for",
]


if __name__ == "__main__":
    sys.exit(main())
