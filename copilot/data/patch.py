"""
Fill catalog holes from the Databento store, and refuse a fill it cannot stand behind.

    python -m copilot.data.patch --symbols TLT.XNAS
    python -m copilot.data.patch --symbols TLT.XNAS --write

Why this exists
---------------
[ADR-0018] substitutes an unusable vendor bar whole, from a table written by hand. That
works at eleven entries and stops working long before a hundred: onboarding six ETFs on
2026-09-04 produced **204 holes**, of which 122 were TLT's 2026, where Marketstack
returns a sub-cent value that is not an auction print and the gate rightly refuses it.
A hand-maintained table cannot absorb that, so the same decision needs a generator.

Where the numbers come from, and why not the obvious place
----------------------------------------------------------
The close comes from the listing venue's ``statistics`` **official closing auction
print** (``stat_type`` 11), and the rest of the bar from that venue's ``ohlcv-1d``.
Splitting the bar across two schemas looks fussy until both are measured against the
2026-09-04 catalog on every day the two sources share:

===========  ==============================  ==========================
Source       Agreement with the stored close  Worst
===========  ==============================  ==========================
``ohlcv-1d``  median 3.5 to 11.4 bps out      459 bps
``statistics`` 98 to 100 percent exact        16.7 bps, twice over 10
===========  ==============================  ==========================

``ohlcv-1d`` on a listing venue is that venue's own trade-derived bar, and for an ETF
the listing venue carries a minority of consolidated volume - so its last print is not
the close the market settled on. The auction statistic is. The daily bar is still the
only source here for open, high and low, which is a compromise this module **states**
rather than hides: a patched bar's extremes are one venue's, its close is everyone's.

The coherence check is the guard that makes the compromise safe. An official close
outside the venue bar's own high-low range means the two disagree about what happened
that day, and the fill is refused rather than reconciled.

[ADR-0018]: ../docs/decisions/0018-an-unusable-bar-is-substituted-whole.md

"""

from __future__ import annotations

import argparse
import csv
import io
import shutil
import sys
from dataclasses import dataclass
from datetime import UTC
from datetime import date
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import zstandard

from copilot.calibration.spread_history import resolve
from copilot.calibration.spread_history import symbology_for
from copilot.data.calendar import session_close
from copilot.data.calendar import trading_days
from copilot.data.catalog import bar_type_for
from copilot.data.catalog import equity_for
from copilot.data.catalog import open_catalog
from copilot.data.catalog import read_daily_bars
from copilot.data.catalog import read_series
from copilot.data.catalog import write_ingestion
from copilot.data.databento import DEFAULT_STORE
from copilot.data.databento import LISTING_DATASETS
from copilot.data.databento import STAT_CLOSING_PRICE
from copilot.data.marketstack import IngestionResult
from copilot.paths import add_catalog_argument
from copilot.validation.types import DailyBar


if TYPE_CHECKING:
    from collections.abc import Sequence


PRICE_SCALE = Decimal(10) ** 9
"""
Databento's fixed-point price scale: prices arrive as integers of 1e-9 dollars.
"""

UNSOURCED_SHOWN = 5
"""
Holes named in the report before it summarises the rest; the same cut the warm-up uses,
so the two operator tables read alike.
"""

TS_UNSET = 18446744073709551615
"""
``ts_ref`` when the vendor did not set it.

Reads as a valid integer, which is the
trap: parsed as a timestamp it lands in the year 2554 and silently resolves no symbol.

"""


@dataclass(frozen=True)
class Fill:
    """
    One hole this module can fill, with both sources kept for the record.
    """

    symbol: str
    day: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    venue_close: Decimal
    """
    The listing venue's own last print, kept so the auction's premium is auditable.
    """

    @property
    def close_gap_bps(self) -> Decimal:
        """
        How far the venue's last print sat from the official close, in basis points.
        """
        if self.venue_close <= 0:
            return Decimal(0)
        return abs(self.close - self.venue_close) / self.venue_close * 10_000

    def to_bar(self) -> DailyBar:
        """
        Build the catalog bar, closed at the session's real close instant.
        """
        return DailyBar(
            symbol=self.symbol,
            closed_at=session_close(self.day),
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
        )


@dataclass(frozen=True)
class PatchResult:
    """
    What one symbol's patch found, filled and could not fill.
    """

    symbol: str
    venue: str
    held: int
    holes: tuple[date, ...]
    fills: tuple[Fill, ...]
    unsourced: tuple[date, ...]
    """Holes the store cannot price: outside its coverage, or absent from it."""
    incoherent: tuple[tuple[date, str], ...]
    """
    Holes refused because the two schemas disagree about the day.
    """

    @property
    def remaining(self) -> int:
        """
        Holes still open after this patch.
        """
        return len(self.unsourced) + len(self.incoherent)


def read_official_closes(store: Path, dataset: str) -> dict[tuple[str, date], Decimal]:
    """
    Read the official closing auction print per symbol and day.
    """
    return _read(store, dataset, "statistics", _official_row)


def read_venue_bars(store: Path, dataset: str) -> dict[tuple[str, date], tuple[Decimal, ...]]:
    """
    Read the listing venue's own daily bar per symbol and day.
    """
    return _read(store, dataset, "ohlcv-1d", _bar_row)


def holes_in(catalog_path: str, symbol: str, venue: str) -> tuple[tuple[date, ...], int]:
    """
    Return the sessions missing between a series' own first and last stored bar.

    Bounded by what is held rather than by a requested window: a series that starts in
    2020 because the instrument did not exist in 2019 has no hole in 2019, and calling
    one would bury the real holes in thousands of false ones.

    """
    bars = read_series(catalog_path, symbol, venue)
    if not bars:
        return (), 0
    held = {bar.closed_at.date() for bar in bars}
    expected = trading_days(min(held), max(held))
    return tuple(day for day in expected if day not in held), len(bars)


def plan(catalog_path: str, symbol: str, venue: str, store: Path) -> PatchResult:
    """
    Work out what could be filled, without touching the catalog.
    """
    dataset = LISTING_DATASETS.get(venue.upper())
    if dataset is None:
        raise KeyError(
            f"no listing dataset for venue {venue!r}; known: {sorted(LISTING_DATASETS)}",
        )
    holes, held = holes_in(catalog_path, symbol, venue)
    closes = read_official_closes(store, dataset)
    bars = read_venue_bars(store, dataset)

    fills: list[Fill] = []
    unsourced: list[date] = []
    incoherent: list[tuple[date, str]] = []
    for day in holes:
        key = (symbol, day)
        official = closes.get(key)
        venue_bar = bars.get(key)
        if official is None or venue_bar is None:
            unsourced.append(day)
            continue
        low, high = venue_bar[2], venue_bar[1]
        if not low <= official <= high:
            incoherent.append(
                (day, f"official close {official} outside the venue range {low}-{high}"),
            )
            continue
        fills.append(
            Fill(
                symbol=symbol,
                day=day,
                open=venue_bar[0],
                high=high,
                low=low,
                close=official,
                volume=int(venue_bar[4]),
                venue_close=venue_bar[3],
            ),
        )
    return PatchResult(
        symbol=symbol,
        venue=venue,
        held=held,
        holes=holes,
        fills=tuple(fills),
        unsourced=tuple(unsourced),
        incoherent=tuple(incoherent),
    )


def apply_patch(catalog_path: str, result: PatchResult) -> int:
    """
    Rewrite one series with its holes filled, keeping the old copy until it reads back.

    A rewrite rather than an append because the catalog raises on non-disjoint interval
    writes: a bar landing inside a stored range is refused, and every hole is by
    definition inside one. The old directory is moved aside rather than deleted, and
    moved back if anything fails, so an interrupted patch leaves the series as it was.

    """
    if not result.fills:
        return 0
    catalog = open_catalog(catalog_path)
    instrument = equity_for(result.symbol, result.venue)
    bar_type = bar_type_for(instrument.id)
    existing = read_daily_bars(catalog, bar_type)

    merged = sorted(
        [*existing, *(fill.to_bar() for fill in result.fills)],
        key=lambda bar: bar.closed_at,
    )
    root = Path(catalog_path).expanduser() / "data" / "bars" / str(bar_type)
    backup = root.with_name(root.name + ".superseded")
    if backup.exists():
        shutil.rmtree(backup)
    root.rename(backup)
    try:
        write_ingestion(
            open_catalog(catalog_path),
            IngestionResult(bars=tuple(merged), fetched=len(merged)),
            venues={result.symbol: result.venue},
        )
        written = read_daily_bars(open_catalog(catalog_path), bar_type)
        if len(written) != len(merged):
            raise RuntimeError(
                f"rewrote {len(merged)} bars but read back {len(written)}",
            )
    except Exception:
        if root.exists():
            shutil.rmtree(root)
        backup.rename(root)
        raise
    shutil.rmtree(backup)
    return len(result.fills)


def report(results: Sequence[PatchResult], *, written: bool) -> int:
    """
    Print one block per symbol and return the exit code a scheduler should act on.
    """
    verb = "filled" if written else "fillable"
    print(f"Catalog patch from the Databento store  ({verb})\n")
    left = 0
    for r in results:
        print(
            f"  {r.symbol + '.' + r.venue:12} held {r.held:>5}  holes {len(r.holes):>4}"
            f"  {verb} {len(r.fills):>4}  still open {r.remaining:>4}",
        )
        if r.fills:
            gaps = sorted(f.close_gap_bps for f in r.fills)
            worst = max(r.fills, key=lambda f: f.close_gap_bps)
            print(
                f"      {r.fills[0].day} .. {r.fills[-1].day}   "
                f"auction vs venue print: median {gaps[len(gaps) // 2]:.2f} bps, "
                f"worst {worst.close_gap_bps:.2f} bps on {worst.day}",
            )
        for day, why in r.incoherent:
            print(f"      REFUSED  {day}  {why}")
        if r.unsourced:
            shown = ", ".join(d.isoformat() for d in r.unsourced[:UNSOURCED_SHOWN])
            hidden = len(r.unsourced) - UNSOURCED_SHOWN
            more = f" (+{hidden} more)" if hidden > 0 else ""
            print(f"      no source {shown}{more}")
        left += r.remaining
    if left:
        print(
            f"\n{left} hole(s) the store cannot price. Databento's US equity history "
            f"starts 2018-05-01; anything earlier needs a different source, and a "
            f"refusal needs looking at rather than forcing.",
        )
    return 1 if left else 0


def main(argv: list[str] | None = None) -> int:
    """
    Report or fill the holes in each requested series.
    """
    parser = argparse.ArgumentParser(
        prog="python -m copilot.data.patch",
        description="Fill catalog holes from the Databento store.",
    )
    parser.add_argument("--symbols", required=True, help="Comma-separated SYMBOL.VENUE pairs")
    add_catalog_argument(parser)
    parser.add_argument("--store", default=DEFAULT_STORE, help="Where bulk pulls landed")
    parser.add_argument("--write", action="store_true", help="Rewrite the series (default: report)")
    args = parser.parse_args(argv)

    store = Path(args.store).expanduser()
    if not store.is_dir():
        print(f"error: no Databento store at {store}", file=sys.stderr)
        return 2

    results = []
    for token in args.symbols.split(","):
        symbol, _, venue = token.strip().partition(".")
        if not symbol or not venue:
            print(f"error: expected SYMBOL.VENUE, got {token.strip()!r}", file=sys.stderr)
            return 2
        result = plan(args.catalog, symbol.upper(), venue.upper(), store)
        if args.write:
            apply_patch(args.catalog, result)
        results.append(result)
    return report(results, written=args.write)


def _read(store: Path, dataset: str, schema: str, row_reader):  # noqa: ANN001, ANN202
    """
    Read one schema's files for a dataset, resolving instrument ids to symbols.
    """
    out: dict = {}
    root = store / dataset / schema
    paths = sorted(root.glob("*.csv.zst"))
    if not paths:
        raise FileNotFoundError(
            f"no {schema} files under {root}; run "
            f"python -m copilot.data.databento --pull --schema {schema} first",
        )
    decompressor = zstandard.ZstdDecompressor()
    for path in paths:
        symbology = symbology_for(path)
        with path.open("rb") as handle:
            stream = io.TextIOWrapper(decompressor.stream_reader(handle), encoding="utf-8")
            for row in csv.DictReader(stream):
                parsed = row_reader(row)
                if parsed is None:
                    continue
                moment, value = parsed
                symbol = resolve(symbology, int(row["instrument_id"]), moment.date())
                if symbol is not None:
                    out[(symbol, moment.date())] = value
    return out


def _official_row(row: dict[str, str]) -> tuple[datetime, Decimal] | None:
    """
    Return the closing auction print, or None for any other statistic.
    """
    if int(row["stat_type"]) != STAT_CLOSING_PRICE:
        return None
    reference = int(row["ts_ref"])
    nanos = reference if reference != TS_UNSET else int(row["ts_event"])
    return _moment(nanos), Decimal(row["price"]) / PRICE_SCALE


def _bar_row(row: dict[str, str]) -> tuple[datetime, tuple[Decimal, ...]]:
    """
    Return the venue's daily bar as open, high, low, close, volume.
    """
    return _moment(int(row["ts_event"])), (
        Decimal(row["open"]) / PRICE_SCALE,
        Decimal(row["high"]) / PRICE_SCALE,
        Decimal(row["low"]) / PRICE_SCALE,
        Decimal(row["close"]) / PRICE_SCALE,
        Decimal(row["volume"]),
    )


def _moment(nanos: int) -> datetime:
    """
    Turn a Databento nanosecond timestamp into an aware datetime.
    """
    return datetime.fromtimestamp(nanos / 1e9, tz=UTC)


__all__ = [
    "Fill",
    "PatchResult",
    "apply_patch",
    "holes_in",
    "plan",
    "read_official_closes",
    "read_venue_bars",
    "report",
]


if __name__ == "__main__":
    sys.exit(main())
