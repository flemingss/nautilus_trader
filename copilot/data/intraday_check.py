"""
Check the daily bars against the intraday path they claim to summarise.

Read-only. It reads stored files and prints findings; it constructs no execution client
and cannot place an order.

    python -m copilot.data.intraday_check

What this can decide that nothing else could
--------------------------------------------
The daily series carries twelve rows whose **open sits outside the day's own high-low
range** - GOOGL opens at 314.52 against a high of 314.02. The vendor module records
them and reasons that they read like an official opening print carried from a different
source than the intraday range. That was inference. A minute-by-minute path settles it:
either the price traded there or it did not.

The comparison is one-sided on purpose
--------------------------------------
The daily bars are consolidated across every venue; the intraday path is the **listing
venue alone**, which is where the official auctions run but is roughly 38% of the tape.
A single venue therefore cannot print a higher high than the consolidated tape, so:

- ``daily_high >= venue_high`` and ``daily_low <= venue_low`` are the expected
  relations, and they are **not** evidence of agreement when they hold.
- A **violation** is evidence, and only in one direction: a consolidated bar whose high
  is below a price the listing venue actually printed is wrong, because that trade is
  part of the consolidated tape by construction.

So this finds daily bars that are too narrow. It cannot find ones that are too wide,
and does not claim to.

Both sides are put in today's terms before comparing, since the catalog is
back-adjusted on read ([ADR-0016]) and the venue files are as-traded.

"""

from __future__ import annotations

import argparse
import csv
import io
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC
from datetime import date
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from copilot.calibration.spread_history import resolve
from copilot.calibration.spread_history import symbology_for
from copilot.data.calendar import SESSION_OPEN_MINUTES
from copilot.data.calendar import session_end_minutes
from copilot.data.catalog import bar_type_for
from copilot.data.catalog import equity_for
from copilot.data.catalog import open_catalog
from copilot.data.catalog import read_daily_bars
from copilot.data.corporate_actions import ACTIONS
from copilot.data.corporate_actions import cumulative_factor


try:
    import zstandard
except ImportError:  # pragma: no cover - required to read what was bought
    zstandard = None

DEFAULT_STORE = "~/.nautilus_copilot/databento"
DEFAULT_CATALOG = "~/.nautilus_copilot/catalog"

EASTERN = ZoneInfo("America/New_York")
PRICE_SCALE = Decimal(10) ** 9

# Regular hours only. The daily bar summarises the session, so pre- and post-market
# prints would widen the venue envelope past what the bar is claiming to describe and
# manufacture violations that are not defects. The end of the session is asked for per
# day rather than assumed: on the three scheduled early closes the market stops at
# 13:00, and reading through to 16:00 collects three hours of extended-hours prints and
# reports them as intraday range.

# A consolidated high a hair under a venue print is rounding, not a defect. One basis
# point on a $200 stock is two cents.
TOLERANCE_BPS = Decimal(1)

# Findings are banded rather than counted, because a consolidated high a hair under a
# venue print is not a defect. Measured across 38,539 sessions: 88% of the differences
# are under 5 bps, which is the floor set by two things this check cannot remove - the
# vendor's daily range is not necessarily the full consolidated extreme, and a
# back-adjusted price does not land on the same quantum as a stored one. Only the top
# band is worth reading as a finding.
MATERIAL_BPS = Decimal(20)


@dataclass(frozen=True)
class Envelope:
    """
    The high and low the listing venue printed in one regular session.
    """

    high: Decimal
    low: Decimal


@dataclass(frozen=True)
class Violation:
    """
    One daily bar narrower than the venue path it should contain.
    """

    symbol: str
    day: date
    field: str
    daily: Decimal
    venue: Decimal

    @property
    def bps(self) -> Decimal:
        """
        Return how far outside the bar the venue printed, in basis points.
        """
        return abs(self.venue - self.daily) / self.daily * 10000


def session_date(stamp: int) -> date | None:
    """
    Return the Eastern session date of a bar, or None if outside regular hours.
    """
    local = datetime.fromtimestamp(stamp / 1e9, tz=UTC).astimezone(EASTERN)
    minutes = local.hour * 60 + local.minute
    day = local.date()
    if not (SESSION_OPEN_MINUTES <= minutes < session_end_minutes(day)):
        return None
    return day


def read_envelopes(path: Path, symbols: dict) -> dict[tuple[str, date], Envelope]:
    """
    Reduce one stored minute file to a high and low per symbol and session.
    """
    if zstandard is None:
        raise RuntimeError("zstandard is required to read stored minute files")

    highs: dict[tuple[str, date], Decimal] = {}
    lows: dict[tuple[str, date], Decimal] = {}
    with path.open("rb") as handle:
        stream = zstandard.ZstdDecompressor().stream_reader(handle)
        for row in csv.DictReader(io.TextIOWrapper(stream, encoding="utf-8")):
            day = session_date(int(row["ts_event"]))
            if day is None:
                continue
            symbol = resolve(symbols, int(row["instrument_id"]), day)
            if symbol is None:
                continue
            high = Decimal(row["high"]) / PRICE_SCALE
            low = Decimal(row["low"]) / PRICE_SCALE
            key = (symbol, day)
            if key not in highs or high > highs[key]:
                highs[key] = high
            if key not in lows or low < lows[key]:
                lows[key] = low
    return {k: Envelope(high=highs[k], low=lows[k]) for k in highs}


def compare(
    symbol: str,
    daily: dict[date, tuple[Decimal, Decimal]],
    envelopes: dict[date, Envelope],
) -> list[Violation]:
    """
    Return every session where the daily bar failed to contain the venue's own path.
    """
    found = []
    for day in sorted(set(daily) & set(envelopes)):
        high, low = daily[day]
        window = envelopes[day]
        if window.high > high * (1 + TOLERANCE_BPS / 10000):
            found.append(Violation(symbol, day, "high", high, window.high))
        if window.low < low * (1 - TOLERANCE_BPS / 10000):
            found.append(Violation(symbol, day, "low", low, window.low))
    return found


def main(argv: list[str] | None = None) -> int:
    """
    Check every stored minute file against the catalog, and report what fails.

    Returns a process exit code, non-zero when a daily bar is narrower than the path it
    claims to summarise.

    """
    parser = argparse.ArgumentParser(
        prog="python -m copilot.data.intraday_check",
        description="Check daily bars against the intraday path they summarise.",
    )
    parser.add_argument("--store", default=DEFAULT_STORE, help="Where minute files landed")
    parser.add_argument("--catalog", default=DEFAULT_CATALOG, help="Catalog directory")
    args = parser.parse_args(argv)

    root = Path(args.store).expanduser()
    files = sorted(root.glob("*/ohlcv-1m/*.csv.zst"))
    if not files:
        parser.error(f"no ohlcv-1m files under {root}; run the pull first")

    envelopes: dict[str, dict[date, Envelope]] = defaultdict(dict)
    for path in files:
        print(f"  reading {path.parent.parent.name}/{path.name}", flush=True)
        for (symbol, day), window in read_envelopes(path, symbology_for(path)).items():
            # Both sides in today's terms: the catalog is adjusted on read, these are
            # as-traded, and a ratio comparison across a split is meaningless otherwise.
            factor = cumulative_factor(
                ACTIONS.get(symbol, ()),
                datetime(day.year, day.month, day.day, tzinfo=UTC),
            )
            cent = Decimal("0.0001")
            envelopes[symbol][day] = Envelope(
                high=(window.high / factor).quantize(cent),
                low=(window.low / factor).quantize(cent),
            )

    catalog_root = Path(args.catalog).expanduser() / "data" / "bars"
    catalog = open_catalog(args.catalog)
    violations: list[Violation] = []
    compared = 0
    for entry in sorted(catalog_root.iterdir()):
        symbol, _, rest = entry.name.partition(".")
        if symbol not in envelopes:
            continue
        venue = rest.split("-", 1)[0]
        bars = read_daily_bars(catalog, bar_type_for(equity_for(symbol, venue).id))
        daily = {b.closed_at.date(): (b.high, b.low) for b in bars}
        found = compare(symbol, daily, envelopes[symbol])
        compared += len(set(daily) & set(envelopes[symbol]))
        violations.extend(found)

    print(f"\n  {compared:,} sessions compared across {len(envelopes)} symbols")

    bands = (
        ("under 5 bps", Decimal(0), Decimal(5)),
        ("5 to 20 bps", Decimal(5), MATERIAL_BPS),
        ("20 to 100 bps", MATERIAL_BPS, Decimal(100)),
        ("over 100 bps", Decimal(100), None),
    )
    print(f"\n  {'band':<16}{'count':>7}{'share of sessions':>20}")
    for label, low, high in bands:
        n = sum(1 for v in violations if low <= v.bps and (high is None or v.bps < high))
        print(f"  {label:<16}{n:>7}{n / compared * 100:>19.3f}%")

    material = sorted((v for v in violations if v.bps >= MATERIAL_BPS), key=lambda x: -x.bps)
    print(f"\n  {len(material)} material findings at or above {MATERIAL_BPS} bps\n")
    if material:
        print(f"  {'symbol':<7}{'date':<13}{'field':<7}{'daily':>11}{'venue':>11}{'bps':>9}")
        for v in material[:20]:
            print(
                f"  {v.symbol:<7}{v.day!s:<13}{v.field:<7}{v.daily:>11}{v.venue:>11}{v.bps:>9.1f}",
            )
    return 1 if material else 0


if __name__ == "__main__":
    sys.exit(main())
