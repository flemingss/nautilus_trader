"""
Measure the stressed next-session gap that position sizing has to carry.

    python -m copilot.calibration.gap_history
    python -m copilot.calibration.gap_history --write

Read-only against the market: it reads the stored daily series and writes a JSON record.
It constructs no execution client and cannot place an order.

What this measures, and why sizing needs it
-------------------------------------------
[`RISK.md`](../docs/playbook/RISK.md) sizes from the stop **plus a stressed per-share
allowance**, ``q = floor(R / (|P - S| + g))``, and requires that ``g`` come from a *tested*
next-session stress loss whenever the strategy exits on a completed daily bar. Both
strategies here do exactly that, and `size_from_levels` divided by the stop distance
alone until 2026-09-12, so every verdict filed before then understated planned risk by
the gap risk it never charged.

A stop is not a loss guarantee, and the replay says so exactly: a stop breached inside a
session fills at its trigger and costs one R, while a session that **gaps through** the
stop fills at the open and costs whatever the gap was. The second case is what ``g`` is
for.

So the measured quantity is the adverse overnight gap: for a long, the drop from one
session's close to the next session's open, floored at zero because a favourable gap is
not a risk. It is expressed **in units of ATR** rather than in dollars, for the same
reason the stop is: the stop is placed at ``stop_atr`` times the ATR, so an allowance in
the same unit composes with it and stays comparable across instruments and across price
levels.

Development bars only
---------------------
Each symbol is carved at its activations' own holdout boundary before anything is
measured, and the **earliest** boundary is used when a symbol carries several
activations. An allowance fitted to holdout bars would be a sizing basis that read the
single-use test, which is the leak
[ADR-0033](../docs/decisions/0033-the-turn-of-month-single-use-test-is-forward.md) was
written about. Nothing here reads a bar the gate may not see.

What it does not claim
----------------------
The ATR here is a plain mean of true range over the period, computed from the stored
series, and the strategies read Nautilus's own ``AverageTrueRange``. The two are close
but not identical, so this is a measurement *of the instrument*, not a reproduction of
one strategy's indicator state. That is the right shape for a pinned allowance: it
describes how the instrument gaps, and it must not move when a strategy changes its
smoothing.

The percentile is nearest-rank, not interpolated, so a pinned number is always a gap that
actually happened.

"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from decimal import ROUND_CEILING
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from copilot.data.catalog import read_series
from copilot.paths import add_catalog_argument
from copilot.strategies.activations import load_activations
from copilot.validation.holdout import HOLDOUT_START
from copilot.validation.holdout import carve


if TYPE_CHECKING:
    from collections.abc import Sequence

    from copilot.validation.types import DailyBar


OUT_DIR = Path(__file__).parent / "out"

ATR_PERIOD = 14
"""
The period every activation in the registry places its stop from.
"""

PERCENTILE = Decimal("0.99")
"""
The stressed percentile, not the typical one.

The playbook asks for a *stress* loss. A p95 overnight gap is an ordinary month's worst
morning and is reached several times a year; p99 is about two or three sessions a year,
which is the kind of event a risk control has to survive rather than the kind it should
expect. Higher still would be dominated by single outliers - the largest adverse gap
measured on MSFT is 8.3 ATR, on one morning in 2005 - and sizing every trade for the
worst morning in twenty years would refuse nearly every trade.

"""

MIN_SAMPLES = 250
"""
Sessions a symbol needs before its percentile is worth pinning.

At 250 the 99th percentile is decided by its worst two or three observations, which is
already thin; below it the number is one morning's accident.

"""

QUANTUM = Decimal("0.01")
"""
Pinned allowances are carried to two decimal places, rounded **up**.

Up, because rounding an allowance down is the direction that sizes a position larger
than the measurement supports.

"""


@dataclass(frozen=True)
class GapStress:
    """
    One symbol's measured adverse-gap distribution, in ATR units.
    """

    symbol: str
    venue: str
    holdout_start: str
    development_bars: int
    samples: int
    median: Decimal
    p95: Decimal
    p99: Decimal
    worst: Decimal
    allowance: Decimal
    """
    The pinned figure: :attr:`p99`, rounded up to :data:`QUANTUM`.
    """
    first: str
    last: str

    def as_record(self) -> dict[str, object]:
        """
        Return the JSON form.
        """
        return {
            "symbol": self.symbol,
            "venue": self.venue,
            "holdout_start": self.holdout_start,
            "development_bars": self.development_bars,
            "samples": self.samples,
            "median_atr": str(self.median),
            "p95_atr": str(self.p95),
            "p99_atr": str(self.p99),
            "worst_atr": str(self.worst),
            "allowance_atr": str(self.allowance),
            "range": [self.first, self.last],
        }


def true_range(bar: DailyBar, previous: DailyBar) -> Decimal:
    """
    Wilder's true range for one bar, against the one before it.
    """
    return max(
        bar.high - bar.low,
        abs(bar.high - previous.close),
        abs(bar.low - previous.close),
    )


def average_true_range(bars: Sequence[DailyBar], index: int, period: int = ATR_PERIOD) -> Decimal:
    """
    Return the mean true range over ``period`` bars, or zero when unavailable.
    """
    if index < period:
        return Decimal(0)
    total = sum(
        (true_range(bars[i], bars[i - 1]) for i in range(index - period + 1, index + 1)),
        Decimal(0),
    )
    return total / Decimal(period)


def adverse_gaps(bars: Sequence[DailyBar], *, period: int = ATR_PERIOD) -> list[Decimal]:
    """
    Return each session's adverse opening gap, in ATR units, floored at zero.

    Adverse for a long held overnight: the distance the open fell below the previous
    close. A favourable gap is not a risk and enters as zero rather than as a negative,
    because the percentile of interest is of losses.

    The ATR is the one standing at the **previous** close, which is the only one a
    position opened then could have been sized with.

    """
    measured: list[Decimal] = []
    for i in range(period + 1, len(bars)):
        atr = average_true_range(bars, i - 1, period)
        if atr <= 0:
            continue
        adverse = bars[i - 1].close - bars[i].open
        measured.append(max(adverse, Decimal(0)) / atr)
    return measured


def percentile(values: Sequence[Decimal], share: Decimal) -> Decimal:
    """
    Nearest-rank percentile: the smallest observation at or above ``share`` of the sample.

    Not interpolated, so the figure is always a gap that actually happened rather than a
    weighted average of two that did.

    """
    if not values:
        raise ValueError("no observations to take a percentile of")
    ordered = sorted(values)
    rank = (share * Decimal(len(ordered))).to_integral_value(rounding=ROUND_CEILING)
    index = min(max(int(rank) - 1, 0), len(ordered) - 1)
    return ordered[index]


def measure_symbol(
    symbol: str,
    venue: str,
    catalog_path: str,
    *,
    holdout_start: datetime | None,
) -> GapStress | None:
    """
    Measure one symbol over its development bars, or None when there are too few.
    """
    carved = carve(read_series(catalog_path, symbol, venue), holdout_start=holdout_start)
    bars = carved.development
    gaps = adverse_gaps(bars)
    if len(gaps) < MIN_SAMPLES:
        return None
    p99 = percentile(gaps, PERCENTILE)
    return GapStress(
        symbol=symbol,
        venue=venue,
        holdout_start=(holdout_start or HOLDOUT_START).date().isoformat(),
        development_bars=len(bars),
        samples=len(gaps),
        median=percentile(gaps, Decimal("0.50")).quantize(Decimal("0.001")),
        p95=percentile(gaps, Decimal("0.95")).quantize(Decimal("0.001")),
        p99=p99.quantize(Decimal("0.001")),
        worst=max(gaps).quantize(Decimal("0.001")),
        allowance=p99.quantize(QUANTUM, rounding=ROUND_CEILING),
        first=bars[0].closed_at.date().isoformat(),
        last=bars[-1].closed_at.date().isoformat(),
    )


def measure(catalog_path: str) -> dict[str, GapStress]:
    """
    Measure every symbol the registry activates, carved at its earliest boundary.

    The earliest, when a symbol carries several activations, because a later boundary
    would let one activation's development window include another's holdout.

    """
    boundaries: dict[tuple[str, str], datetime] = {}
    for activation in load_activations():
        key = (activation.symbol, activation.venue)
        boundary = activation.validation.holdout_boundary or HOLDOUT_START
        boundaries[key] = min(boundaries.get(key, boundary), boundary)

    measured: dict[str, GapStress] = {}
    for (symbol, venue), boundary in sorted(boundaries.items()):
        stress = measure_symbol(symbol, venue, catalog_path, holdout_start=boundary)
        if stress is not None:
            measured[symbol] = stress
    return measured


def main(argv: list[str] | None = None) -> int:
    """
    Measure the gap allowance for every activated symbol and print it.
    """
    parser = argparse.ArgumentParser(
        prog="python -m copilot.calibration.gap_history",
        description="Measure the stressed next-session gap sizing must allow for.",
    )
    add_catalog_argument(parser)
    parser.add_argument("--write", action="store_true", help="File the measurement as JSON")
    args = parser.parse_args(argv)

    measured = measure(args.catalog)
    if not measured:
        print("error: no symbol had enough development bars to measure", file=sys.stderr)
        return 2

    print(
        f"Adverse next-session gap in ATR{ATR_PERIOD} units, development bars only, "
        f"nearest-rank percentiles.\n",
    )
    header = f"  {'symbol':<7}{'carved':<12}{'bars':>6}{'median':>9}{'p95':>8}{'p99':>8}"
    print(header + f"{'worst':>8}{'pinned':>9}")
    for symbol, s in sorted(measured.items()):
        print(
            f"  {symbol:<7}{s.holdout_start:<12}{s.development_bars:>6}{s.median:>9}"
            f"{s.p95:>8}{s.p99:>8}{s.worst:>8}{s.allowance:>9}",
        )
    print(
        f"\nThe pinned column is p99 rounded up to {QUANTUM}, and is what "
        f"copilot.risk.gap_stress carries.",
    )

    if args.write:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
        path = OUT_DIR / f"gap_stress_{stamp}.json"
        path.write_text(
            json.dumps(
                {
                    "measured_at": datetime.now(tz=UTC).isoformat(),
                    "atr_period": ATR_PERIOD,
                    "percentile": str(PERCENTILE),
                    "min_samples": MIN_SAMPLES,
                    "quantum": str(QUANTUM),
                    "symbols": {k: v.as_record() for k, v in sorted(measured.items())},
                },
                indent=2,
            )
            + "\n",
        )
        print(f"\nfiled {path}")
    return 0


__all__ = [
    "ATR_PERIOD",
    "MIN_SAMPLES",
    "PERCENTILE",
    "GapStress",
    "adverse_gaps",
    "average_true_range",
    "measure",
    "measure_symbol",
    "percentile",
    "true_range",
]


if __name__ == "__main__":
    sys.exit(main())
