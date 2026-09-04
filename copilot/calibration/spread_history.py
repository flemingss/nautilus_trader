"""
Measure the spread the strategy actually pays, from historical top-of-book.

Read-only. It reads stored quote files and writes a JSON snapshot; it constructs no
execution client and cannot place an order.

    python -m copilot.calibration.spread_history --write

What this replaces, and why it is worth replacing
-------------------------------------------------
[ADR-0011] charges spread at p95 from a pinned snapshot, and that snapshot is the
weakest input in the cost model by some distance. It admits as much: **delayed** IB
quotes, **251 to 301 samples** per symbol drawn from one 27-minute session, taken
**mid-session while the strategy trades the open and the close**, and applied to trades
running back to 2006 - with a note that the median moved 2x between two sessions.

The historical top-of-book behind this module answers the same question with
**hundreds of thousands of samples per symbol across 7.6 years**, from real quotes
rather than delayed ones, and it can be cut by time of day. That last part is the one
that matters: the strategy enters at the close under [ADR-0013], so the closing
spread is the number it pays, and no mid-session sample can stand in for it.

What it does not fix
--------------------
A per-minute sample is not every quote. Within a minute the book moves, and the
sampled instant is not necessarily the instant an order crosses. This measures the
spread that *stood* at each minute, which is the right input for a cost charged per
trade at daily frequency, and would not be for anything acting inside a minute.

Quotes are as-traded. They are **not** adjusted for the corporate actions
[ADR-0016] applies to the bar series, because a spread in basis points is a ratio and a
split moves numerator and denominator together.

"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from array import array
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC
from datetime import date
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


try:
    import zstandard
except ImportError:  # pragma: no cover - the reader is required to read what was bought
    zstandard = None

DEFAULT_STORE = "~/.nautilus_copilot/databento"
OUT_DIR = Path(__file__).parent / "out"

EASTERN = ZoneInfo("America/New_York")

# INT64_MAX is Databento's "no quote here", and it is not rare: the first rows of every
# session carry it. Treated as a price it produces a spread of nine billion basis
# points, so it is dropped rather than clamped.
UNDEFINED = 9223372036854775807

PRICE_SCALE = 1e-9

# Regular hours, in Eastern. The strategy trades the close under ADR-0013 and measures
# its gap at the open, so those two windows are reported separately from the session.
OPEN_MINUTES = 5
CLOSE_MINUTES = 5


@dataclass(frozen=True)
class Distribution:
    """
    One bucket's spread distribution, in basis points of the mid.
    """

    samples: int
    median: float
    p75: float
    p95: float
    p99: float
    maximum: float

    def as_record(self) -> dict[str, float | int]:
        """
        Return the JSON form, rounded the way the incumbent snapshot rounds.
        """
        return {
            "samples": self.samples,
            "median": round(self.median, 4),
            "p75": round(self.p75, 4),
            "p95": round(self.p95, 4),
            "p99": round(self.p99, 4),
            "max": round(self.maximum, 4),
        }


def summarise(spreads: array[float]) -> Distribution | None:
    """
    Return the distribution of a bucket, or None when nothing landed in it.
    """
    if not spreads:
        return None
    ordered = sorted(spreads)
    n = len(ordered)

    def at(q: float) -> float:
        return ordered[min(n - 1, int(q * n))]

    return Distribution(
        samples=n,
        median=at(0.50),
        p75=at(0.75),
        p95=at(0.95),
        p99=at(0.99),
        maximum=ordered[-1],
    )


def bucket_for(moment: datetime) -> str | None:
    """
    Return which window a quote instant falls in, or None if outside regular hours.

    Sessions that close early - the half days around Thanksgiving and Christmas - are
    not special-cased, so their last five minutes land in ``session`` rather than
    ``close``. That understates the sample count for ``close`` slightly and does not
    contaminate it, which is the safer direction for a number used as a cost.

    """
    local = moment.astimezone(EASTERN)
    minutes = local.hour * 60 + local.minute
    open_at, close_at = 9 * 60 + 30, 16 * 60
    if not (open_at <= minutes < close_at):
        return None
    if minutes < open_at + OPEN_MINUTES:
        return "open"
    if minutes >= close_at - CLOSE_MINUTES:
        return "close"
    return "session"


def read_quotes(path: Path, symbols: Symbology) -> dict[tuple[str, str], array[float]]:
    """
    Stream one stored quote file, returning spreads in bps per symbol and window.

    Streamed rather than loaded: these files run to hundreds of megabytes compressed,
    and the whole point of buying them once is not to need them in memory twice.

    """
    if zstandard is None:
        raise RuntimeError("zstandard is required to read stored quote files")

    out: dict[tuple[str, str], array[float]] = defaultdict(lambda: array("f"))
    with path.open("rb") as handle:
        stream = zstandard.ZstdDecompressor().stream_reader(handle)
        for row in csv.DictReader(io.TextIOWrapper(stream, encoding="utf-8")):
            bid = int(row["bid_px_00"])
            ask = int(row["ask_px_00"])
            if UNDEFINED in (bid, ask) or bid <= 0 or ask <= bid:
                continue
            moment = datetime.fromtimestamp(int(row["ts_recv"]) * PRICE_SCALE, tz=UTC)
            window = bucket_for(moment)
            if window is None:
                continue
            symbol = resolve(symbols, int(row["instrument_id"]), moment.date())
            if symbol is None:
                continue
            mid = (bid + ask) / 2
            out[(symbol, window)].append((ask - bid) / mid * 10000)
    return out


Symbology = dict[int, list[tuple[date, date, str]]]


def symbology_for(path: Path) -> Symbology:
    """
    Load the instrument-id map a pull wrote beside itself, keeping the date ranges.

    The rows carry a numeric id and no symbol, so this file is not optional metadata:
    without it the quotes cannot be attributed and the pull is worthless.

    **The dates are not decoration.** A venue reassigns instrument ids, and measured on
    the stored XNAS pull, **525 ids are used by more than one of eight symbols** -
    GOOGL and INTC share dozens. Flattening this to ``id -> symbol`` silently files one
    company's quotes under another's name, and the resulting spread would look
    perfectly plausible.

    """
    sidecar = path.with_suffix(".symbology.json")
    if not sidecar.exists():
        raise FileNotFoundError(f"no symbology beside {path.name}; re-run the pull")

    mapping: Symbology = defaultdict(list)
    for entry in json.loads(sidecar.read_text()):
        mapping[int(entry["instrument_id"])].append(
            (
                date.fromisoformat(entry["from"]),
                date.fromisoformat(entry["to"]),
                entry["symbol"],
            ),
        )
    for spans in mapping.values():
        spans.sort()
    return dict(mapping)


def resolve(symbology: Symbology, instrument_id: int, day: date) -> str | None:
    """
    Return the symbol an id stood for on a day, or None if it stood for nothing.

    The upper bound is exclusive: the vendor's ranges abut, so treating it as inclusive
    would make two symbols match on every handover date.

    """
    for first, last, symbol in symbology.get(instrument_id, ()):
        if first <= day < last:
            return symbol
    return None


def measure(store: str, schema: str = "bbo-1m") -> dict[str, dict[str, Distribution]]:
    """
    Read every stored quote file and return each symbol's distribution per window.
    """
    root = Path(store).expanduser()
    files = sorted(root.glob(f"*/{schema}/*.csv.zst"))
    if not files:
        raise FileNotFoundError(f"no {schema} files under {root}; run the pull first")

    collected: dict[tuple[str, str], array[float]] = defaultdict(lambda: array("f"))
    for path in files:
        print(f"  reading {path.parent.parent.name}/{path.name}", flush=True)
        for key, spreads in read_quotes(path, symbology_for(path)).items():
            collected[key].extend(spreads)

    out: dict[str, dict[str, Distribution]] = defaultdict(dict)
    for (symbol, window), spreads in collected.items():
        summary = summarise(spreads)
        if summary is not None:
            out[symbol][window] = summary
    return dict(out)


def as_record(measured: dict[str, dict[str, Distribution]], store: str) -> dict[str, object]:
    """
    Return the snapshot written to disk, in the shape a cost model can pin.
    """
    return {
        "measured_at": datetime.now(tz=UTC).isoformat(),
        "source": f"databento bbo-1m, historical top of book, from {store}",
        "market_data_type": "HISTORICAL_REALTIME",
        "delayed_caveat": False,
        "windows": {
            "open": f"first {OPEN_MINUTES} minutes of regular hours",
            "close": f"last {CLOSE_MINUTES} minutes of regular hours",
            "session": "the rest of regular hours",
        },
        "symbols": [
            {
                "symbol": symbol,
                "full_spread_bps": {w: d.as_record() for w, d in sorted(windows.items())},
                "per_side_bps": {
                    w: {"median": round(d.median / 2, 4), "p95": round(d.p95 / 2, 4)}
                    for w, d in sorted(windows.items())
                },
            }
            for symbol, windows in sorted(measured.items())
        ],
    }


def main(argv: list[str] | None = None) -> int:
    """
    Measure historical spreads and print them beside the pinned snapshot.

    Returns a process exit code.

    """
    parser = argparse.ArgumentParser(
        prog="python -m copilot.calibration.spread_history",
        description="Measure the spread actually paid, from stored top-of-book.",
    )
    parser.add_argument("--store", default=DEFAULT_STORE, help="Where bulk pulls landed")
    parser.add_argument("--schema", default="bbo-1m", help="Quote schema to read")
    parser.add_argument("--write", action="store_true", help="File the snapshot as JSON")
    args = parser.parse_args(argv)

    measured = measure(args.store, args.schema)

    print(f"\n  {'symbol':<7}{'window':<9}{'samples':>10}{'median':>9}{'p95':>9}{'p99':>9}")
    for symbol, windows in sorted(measured.items()):
        for window in ("open", "session", "close"):
            d = windows.get(window)
            if d is None:
                continue
            print(
                f"  {symbol:<7}{window:<9}{d.samples:>10,}"
                f"{d.median:>9.3f}{d.p95:>9.3f}{d.p99:>9.3f}",
            )

    if args.write:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
        path = OUT_DIR / f"spread_history_{stamp}.json"
        path.write_text(json.dumps(as_record(measured, args.store), indent=2) + "\n")
        print(f"\n  filed {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
