"""
Bars the daily vendor could not deliver, taken whole from a second source.

Marketstack's 2026 rows carry sessions it cannot price: eight with a null close, two
where SPY's close is exactly ``0.0`` while its open, high and low are sane, and one where
MSFT's fields are shifted - the open repeats the previous session's and the true open sits
in the high slot, which reads as an incoherent bar. A narrow re-fetch of those exact dates
returns the identical rows, so this is the vendor's stored data rather than a query
artefact, and waiting does not fix it.

The ingestion gate refuses the batch at a 2.17% rejection ratio, which is correct and also
terminal: nothing writes, so the catalog cannot be brought past 2025-12-31, and the live
warm-up needs it current. This table is how those eleven sessions get in.

What is substituted, and what is not
------------------------------------
**The price bar is taken whole.** Open, high, low, close and volume all come from
Databento - the consolidated daily bar, with the close replaced by the listing venue's
official closing auction print. Splicing one vendor's open onto another's close would
produce a bar that is neither vendor's and can be checked against neither.

**The corporate-action fields stay with the vendor that sells them.** Databento sells no
splits or dividends ([ADR-0015]), so ``split_factor`` and ``dividend`` are left exactly as
Marketstack returned them. Those are a different axis, governed by [ADR-0016], and taking
them from a source that does not have them would be worse than taking nothing.

**Only where the vendor failed.** A substitution is not a preference. Each row below names
the defect that made the vendor's bar unusable, and every one of them fails a gate that
already existed - not one is a disagreement about a bar both sources could price.

Why this is a table in code
---------------------------
The same reason the corporate-action adjustments are ([ADR-0016]): a repair applied by
hand is a number nobody can check afterwards. Here the values are versioned, each carries
its reason, and ``python -m copilot.data.substitutions`` re-fetches from Databento and
compares, exiting non-zero on any disagreement. The catalog can be rebuilt from a commit.

Its limits are worth stating. ``EQUS.SUMMARY`` begins 2024-07-01, so nothing before that
can be repaired this way, and the seventeen bad closes the 2018-2025 audit found are **not**
substituted here - they were disagreements rather than absences, they sit inside the pinned
evaluation window, and moving them would move filed verdicts ([ADR-0017]).

[ADR-0015]: ../docs/decisions/0015-databento-is-the-intraday-source-only.md
[ADR-0016]: ../docs/decisions/0016-corporate-actions-are-applied-on-read.md
[ADR-0017]: ../docs/decisions/0017-the-evaluation-window-is-pinned-at-both-ends.md
[ADR-0018]: ../docs/decisions/0018-an-unusable-bar-is-substituted-whole.md

"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Iterable
    from collections.abc import Mapping


SOURCE = "databento EQUS.SUMMARY ohlcv-1d, close from the listing venue's auction print"
"""
Where every substituted bar came from, in one string, for the ingestion report.
"""

COVERAGE_START = date(2024, 7, 1)
"""
The first session ``EQUS.SUMMARY`` covers, and therefore the earliest repairable day.

Stated so a future substitution against an older date fails against a constant rather
than against a confusing empty response.

"""


@dataclass(frozen=True)
class Substitution:
    """
    One session's bar, taken whole from the second source.
    """

    symbol: str
    day: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    reason: str

    def as_fields(self) -> dict[str, str | int]:
        """
        Return the price fields in the shape a provider row carries them.
        """
        return {
            "open": str(self.open),
            "high": str(self.high),
            "low": str(self.low),
            "close": str(self.close),
            "volume": self.volume,
        }


NULL_CLOSE = "vendor close is null"
ZERO_CLOSE = "vendor close is exactly 0.0 with a sane open, high and low"
SHIFTED_ROW = "vendor fields are shifted: open repeats the prior session, true open sits in high"

SUBSTITUTIONS: tuple[Substitution, ...] = (
    Substitution(
        "AAPL",
        date(2026, 6, 9),
        Decimal("300.275"),
        Decimal("300.75"),
        Decimal("287.78"),
        Decimal("290.55"),
        70_108_847,
        NULL_CLOSE,
    ),
    Substitution(
        "AAPL",
        date(2026, 6, 10),
        Decimal("290.74"),
        Decimal("294.75"),
        Decimal("287.38"),
        Decimal("291.58"),
        52_793_266,
        NULL_CLOSE,
    ),
    Substitution(
        "MSFT",
        date(2026, 1, 15),
        Decimal("464.12"),
        Decimal("464.25"),
        Decimal("455.9"),
        Decimal("456.66"),
        23_225_839,
        SHIFTED_ROW,
    ),
    Substitution(
        "MSFT",
        date(2026, 6, 9),
        Decimal("409.03"),
        Decimal("411.98"),
        Decimal("398.48"),
        Decimal("403.41"),
        35_317_302,
        NULL_CLOSE,
    ),
    Substitution(
        "MSFT",
        date(2026, 6, 10),
        Decimal("398.55"),
        Decimal("405.04"),
        Decimal("397.16"),
        Decimal("397.36"),
        32_576_041,
        NULL_CLOSE,
    ),
    Substitution(
        "MSFT",
        date(2026, 6, 15),
        Decimal("396.795"),
        Decimal("401.75"),
        Decimal("392.845"),
        Decimal("399.76"),
        32_266_437,
        NULL_CLOSE,
    ),
    Substitution(
        "SPY",
        date(2026, 4, 7),
        Decimal("656.65"),
        Decimal("659.61"),
        Decimal("651.06"),
        Decimal("659.22"),
        69_980_362,
        ZERO_CLOSE,
    ),
    Substitution(
        "SPY",
        date(2026, 4, 8),
        Decimal("676.39"),
        Decimal("677.08"),
        Decimal("671.46"),
        Decimal("676.01"),
        93_606_114,
        ZERO_CLOSE,
    ),
    Substitution(
        "SPY",
        date(2026, 6, 9),
        Decimal("743.63"),
        Decimal("746.9"),
        Decimal("722.59"),
        Decimal("737.05"),
        87_683_517,
        NULL_CLOSE,
    ),
    Substitution(
        "SPY",
        date(2026, 6, 10),
        Decimal("733.39"),
        Decimal("738.38"),
        Decimal("725.33"),
        Decimal("725.43"),
        60_341_349,
        NULL_CLOSE,
    ),
    Substitution(
        "SPY",
        date(2026, 6, 15),
        Decimal("751.85"),
        Decimal("756.68"),
        Decimal("751.76"),
        Decimal("754.83"),
        60_176_425,
        NULL_CLOSE,
    ),
)
"""
Every substituted session, measured 2026-09-04 and verifiable from a commit.

Ordered by symbol then date so a diff adding one reads as an addition rather than as a
rewrite.

"""


def substitution_for(symbol: str, day: date) -> Substitution | None:
    """
    Return the substitution for one symbol-day, or None.
    """
    key = symbol.upper()
    for entry in SUBSTITUTIONS:
        if entry.symbol == key and entry.day == day:
            return entry
    return None


def apply_to(rows: Iterable[Mapping[str, object]]) -> tuple[list[dict], tuple[Substitution, ...]]:
    """
    Replace the price fields of any substituted row, and report which were applied.

    Returns new dictionaries rather than mutating: the caller's rows are the vendor's
    answer, and a run that reported "11 substituted" while having quietly rewritten its
    own input would be describing something that no longer exists.

    A substitution with no matching vendor row is **not** applied. There would be nothing
    to carry the split and dividend fields, and inventing them is exactly the kind of
    silent fabrication this module exists to avoid.

    """
    applied: list[Substitution] = []
    out: list[dict] = []
    for row in rows:
        fresh = dict(row)
        symbol = fresh.get("symbol")
        stamp = fresh.get("date")
        entry = None
        if isinstance(symbol, str) and isinstance(stamp, str):
            entry = substitution_for(symbol, date.fromisoformat(stamp[:10]))
        if entry is not None:
            fresh.update(entry.as_fields())
            applied.append(entry)
        out.append(fresh)
    return out, tuple(applied)


def unmatched(applied: Iterable[Substitution]) -> tuple[Substitution, ...]:
    """
    Return the table entries that no vendor row carried.

    An entry that never matches is a silent hole: the session it names stays missing and
    the ingestion report says nothing, because nothing was rejected either.

    """
    seen = {(s.symbol, s.day) for s in applied}
    return tuple(s for s in SUBSTITUTIONS if (s.symbol, s.day) not in seen)


def _verify(argv: list[str]) -> int:
    """
    Re-fetch every substituted session from Databento and compare against the table.
    """
    from copilot.data.databento import LISTING_DATASETS  # noqa: PLC0415 - CLI-only
    from copilot.data.databento import DatabentoClient  # noqa: PLC0415

    parser = argparse.ArgumentParser(description="Verify the substitution table against Databento")
    parser.add_argument("--api-key", default=os.environ.get("DATABENTO_API_KEY"))
    args = parser.parse_args(argv)
    if not args.api_key:
        print("DATABENTO_API_KEY is not set; this check re-fetches and cannot run without it")
        return 2

    client = DatabentoClient(args.api_key)
    days = sorted({s.day for s in SUBSTITUTIONS})
    start, end = days[0].isoformat(), (days[-1].toordinal() + 1)
    end = date.fromordinal(end).isoformat()
    symbols = sorted({s.symbol for s in SUBSTITUTIONS})

    bars = client.fetch_daily_bars(symbols, start, end)
    closes: dict[str, dict[date, Decimal]] = {}
    for mic, dataset in LISTING_DATASETS.items():
        listed = [s for s in symbols if _venue_of(s) == mic]
        if listed:
            closes.update(client.fetch_official_closes(listed, dataset, start, end))

    wrong = 0
    for entry in SUBSTITUTIONS:
        bar = bars.get(entry.symbol, {}).get(entry.day)
        auction = closes.get(entry.symbol, {}).get(entry.day)
        if bar is None or auction is None:
            print(f"  {entry.symbol} {entry.day} MISSING from the source")
            wrong += 1
            continue
        source = (bar[0], bar[1], bar[2], auction, bar[4])
        held = (entry.open, entry.high, entry.low, entry.close, entry.volume)
        if source != held:
            print(f"  {entry.symbol} {entry.day} DISAGREES")
            print(f"      table  {held}")
            print(f"      source {source}")
            wrong += 1
            continue
        print(f"  {entry.symbol} {entry.day} ok    close {entry.close}  [{entry.reason}]")

    print(
        f"\n{len(SUBSTITUTIONS) - wrong}/{len(SUBSTITUTIONS)} substitutions reproduce from source",
    )
    return 1 if wrong else 0


def _venue_of(symbol: str) -> str:
    """
    Return the listing MIC for a substituted symbol.

    Deliberately not a lookup into the catalog: this check must run before a catalog
    exists, which is the situation it is most useful in.

    """
    return "ARCX" if symbol == "SPY" else "XNAS"


def main(argv: list[str] | None = None) -> int:
    """
    Verify the table against its source.
    """
    return _verify(list(sys.argv[1:] if argv is None else argv))


__all__ = [
    "COVERAGE_START",
    "SOURCE",
    "SUBSTITUTIONS",
    "Substitution",
    "apply_to",
    "substitution_for",
    "unmatched",
]


if __name__ == "__main__":
    sys.exit(main())
