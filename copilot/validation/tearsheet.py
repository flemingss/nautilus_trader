"""
Performance summary and trial deflation, ported from trade-copilot.

Two things a human needs before believing a validation result, and neither is an
expectancy.

**The tearsheet.** One figure cannot distinguish the shapes that matter: a rule that
wins 70% of the time in tiny increments and gives it all back in three losses has the
same expectancy as one that wins 30% of the time and is worth running.

**Deflation.** Search hard enough over noise and something clears any bar.
:func:`deflated_pass_probability` turns the attempt count into the question actually
worth asking - *how likely was a result this good from candidates with no edge at all?*

Everything here is pure and reported. **Nothing here is a gate.** The gate stays
majority-of-folds; adding a second threshold against figures nobody has read yet would
mean tuning a gate to taste.

Ported faithfully from `services/validation/tearsheet.py`; only the import of the
result types changes, since this overlay vendors them in
:mod:`copilot.validation.types`.

"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from math import comb
from math import sqrt

from copilot.validation.types import BacktestRunResult
from copilot.validation.types import ClosedTrade


TRADING_DAYS_PER_YEAR = Decimal(252)

# Standard deviation is undefined below this many samples.
MIN_TRADES_FOR_DEVIATION = 2


@dataclass(frozen=True)
class Tearsheet:
    """
    What a run did, in R, beyond its expectancy.

    Every currency-denominated figure is in R, so a tearsheet from one symbol is
    comparable with one from another.

    """

    trades: int
    wins: int
    losses: int
    win_rate: Decimal
    expectancy_r: Decimal
    total_r: Decimal
    average_win_r: Decimal
    average_loss_r: Decimal
    """
    Reported positive.

    A rule's asymmetry is the ratio of this to ``average_win_r``,
    and a signed figure makes that comparison read backwards.

    """
    profit_factor: Decimal | None
    """
    Gross R won over gross R lost.

    ``None`` when nothing was lost - undefined rather
    than infinite, because a run with no losing trade has not demonstrated a profit
    factor, it has demonstrated too small a sample.

    """
    max_drawdown_r: Decimal
    """
    Peak-to-trough of the cumulative-R curve, by trade rather than by day.

    The replay records fills, not a daily equity series; trade-ordered drawdown is the
    honest thing to compute from what exists, and it understates a true daily drawdown
    because it cannot see intra-trade excursion.

    """
    sharpe: Decimal | None
    """
    Mean R over the standard deviation of R, **per trade and not annualised**.

    Annualising would require a trade frequency this harness does not hold fixed across
    candidates, and scaling by a varying frequency would reward candidates that simply
    traded more. ``None`` below two trades, where deviation is undefined.

    """
    exposure: Decimal
    """
    Fraction of the run's calendar span spent holding a position.

    Overlapping positions are counted once - this measures *time in the market*, not the
    sum of position-days, which would exceed 1 and mean something different.

    """
    span_days: int
    trades_per_year: Decimal

    def as_dict(self) -> dict[str, object]:
        """
        JSON-safe form, for logging and persistence.

        Decimals become strings rather than floats: a float round trip would quietly
        change a reported figure.

        """
        return {
            "trades": self.trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": str(self.win_rate),
            "expectancy_r": str(self.expectancy_r),
            "total_r": str(self.total_r),
            "average_win_r": str(self.average_win_r),
            "average_loss_r": str(self.average_loss_r),
            "profit_factor": None if self.profit_factor is None else str(self.profit_factor),
            "max_drawdown_r": str(self.max_drawdown_r),
            "sharpe": None if self.sharpe is None else str(self.sharpe),
            "exposure": str(self.exposure),
            "span_days": self.span_days,
            "trades_per_year": str(self.trades_per_year),
        }


EMPTY_TEARSHEET = Tearsheet(
    trades=0,
    wins=0,
    losses=0,
    win_rate=Decimal(0),
    expectancy_r=Decimal(0),
    total_r=Decimal(0),
    average_win_r=Decimal(0),
    average_loss_r=Decimal(0),
    profit_factor=None,
    max_drawdown_r=Decimal(0),
    sharpe=None,
    exposure=Decimal(0),
    span_days=0,
    trades_per_year=Decimal(0),
)


def tearsheet(result: BacktestRunResult) -> Tearsheet:
    """
    Summarise a replay's closed trades.

    Pure; no clock, no I/O.

    """
    return tearsheet_for(result.trades)


def tearsheet_for(trades: tuple[ClosedTrade, ...]) -> Tearsheet:
    """
    As :func:`tearsheet`, over trades already selected - one fold's window, say.
    """
    if not trades:
        return EMPTY_TEARSHEET

    ordered = sorted(trades, key=lambda t: (t.closed_at, t.opened_at, t.symbol))
    multiples = [t.r_multiple for t in ordered]
    wins = [r for r in multiples if r > 0]
    losses = [r for r in multiples if r < 0]

    gross_win = sum(wins, Decimal(0))
    gross_loss = -sum(losses, Decimal(0))
    total = sum(multiples, Decimal(0))
    count = Decimal(len(multiples))

    # Earliest open to latest close, taken across all trades rather than from the ends
    # of the close-ordered list: the first trade to close is not necessarily the first
    # to have opened, and using it clips the span whenever a long position is outlived
    # by a shorter one opened after it.
    first_open = min(t.opened_at for t in ordered)
    last_close = max(t.closed_at for t in ordered)
    span_days = max((last_close - first_open).days, 0)

    return Tearsheet(
        trades=len(ordered),
        wins=len(wins),
        losses=len(losses),
        win_rate=Decimal(len(wins)) / count,
        expectancy_r=total / count,
        total_r=total,
        average_win_r=(gross_win / Decimal(len(wins))) if wins else Decimal(0),
        average_loss_r=(gross_loss / Decimal(len(losses))) if losses else Decimal(0),
        profit_factor=(gross_win / gross_loss) if gross_loss > 0 else None,
        max_drawdown_r=_max_drawdown(multiples),
        sharpe=_sharpe(multiples),
        exposure=_exposure(ordered, span_days),
        span_days=span_days,
        trades_per_year=(
            count * TRADING_DAYS_PER_YEAR / Decimal(span_days) if span_days > 0 else Decimal(0)
        ),
    )


def _max_drawdown(multiples: list[Decimal]) -> Decimal:
    """
    Peak-to-trough of the cumulative-R curve, as a positive number.
    """
    equity = Decimal(0)
    peak = Decimal(0)
    worst = Decimal(0)
    for r in multiples:
        equity += r
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


def _sharpe(multiples: list[Decimal]) -> Decimal | None:
    """
    Mean over sample standard deviation of per-trade R.

    Sample (n-1) rather than population: these trades are a sample of the rule's
    behaviour, not the population of everything it will ever do, and the population form
    flatters a small sample by dividing by a larger number.

    """
    n = len(multiples)
    if n < MIN_TRADES_FOR_DEVIATION:
        return None
    mean = sum(multiples, Decimal(0)) / Decimal(n)
    variance = sum(((r - mean) ** 2 for r in multiples), Decimal(0)) / Decimal(n - 1)
    if variance <= 0:
        return None
    return mean / Decimal(sqrt(float(variance)))


def _exposure(ordered: list[ClosedTrade], span_days: int) -> Decimal:
    """
    Share of the span with at least one position open.

    Union of the holding intervals, so two overlapping positions count once. The
    alternative - summing position-days - can exceed the span, and would report a
    concentrated portfolio as more "exposed" than a continuously-invested one.

    """
    if span_days <= 0:
        return Decimal(0)

    intervals = sorted((t.opened_at, t.closed_at) for t in ordered)
    held = Decimal(0)
    current_start, current_end = intervals[0]
    for start, end in intervals[1:]:
        if start > current_end:
            held += Decimal((current_end - current_start).days)
            current_start, current_end = start, end
        else:
            current_end = max(current_end, end)
    held += Decimal((current_end - current_start).days)

    return min(held / Decimal(span_days), Decimal(1))


def deflated_pass_probability(*, attempts: int, folds: int, folds_passed: int) -> Decimal:
    """
    Chance that **some** candidate out of ``attempts`` passes this well on noise.

    A cheap form of the deflated Sharpe idea, computed from what the gate already
    holds rather than from a new statistic.

    The null: a fold is a coin flip. That is exactly what the fold threshold asserts -
    it passes on expectancy above zero, "better than not trading", so a rule with no
    edge passes each fold with probability half by construction. The chance one
    edgeless candidate matches or beats ``folds_passed`` of ``folds`` is the binomial
    tail, and the chance at least one of ``attempts`` such candidates does is
    ``1 - (1 - tail) ** attempts``.

    **Read it as an upper bound, and a loose one.** Grid candidates are neighbours of
    each other and their results are strongly correlated, so the effective number of
    independent trials is far below ``attempts`` and the true probability is lower. The
    bound errs toward making a result look *less* impressive, which is the direction a
    deflation statistic should err.

    Returns 1 when nothing was passed well enough to be surprising, and is defined to
    be 1 for a zero-fold run - no evidence cannot be improbable.

    """
    if folds <= 0 or attempts <= 0:
        return Decimal(1)

    passed = max(0, min(folds_passed, folds))
    tail = sum(Decimal(comb(folds, k)) for k in range(passed, folds + 1)) / Decimal(2**folds)
    if tail >= Decimal(1):
        return Decimal(1)
    return Decimal(1) - (Decimal(1) - tail) ** attempts


__all__ = [
    "EMPTY_TEARSHEET",
    "TRADING_DAYS_PER_YEAR",
    "Tearsheet",
    "deflated_pass_probability",
    "tearsheet",
    "tearsheet_for",
]
