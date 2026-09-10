"""
Whether a result is distinguishable from nothing, which a point estimate cannot say.

The walk-forward gate asks each fold to beat zero and then counts the folds. That
aggregation is what carries the statistical weight, and it is why
``DEFAULT_FOLD_PASS_THRESHOLD`` is zero rather than a comfortable margin: raising a
fold's bar would re-tune the gate instead of the strategy.

**The holdout has no aggregation to lean on.** It is one fold, run once, and
``score > 0`` on a single sample is a coin that came up heads. The AAPL next-close
holdout is the worked example: +0.035 R over 111 trades passed on exactly that rule,
while the same trades put the standard error at 0.10 R - so the result was one third of
a standard error from zero, and the gate could not tell.

[`RESEARCH.md`](../docs/playbook/RESEARCH.md) already required the missing half, under
*Evidence sufficiency*: predeclare the requirement from **effective** rather than raw
sample size, report **dependence-aware intervals**, and reject when the interval is too
wide. Nothing in the code did any of it. This module is that requirement, made
executable.

Why the raw trade count overstates the evidence
-----------------------------------------------
Two reasons, and they compound.

**Trades overlap.** A premise that holds a position for three sessions and signals every
session has three open at once, and those three share the same market. Counting them as
three independent observations inflates the sample by the concurrency factor.
:func:`concurrency` measures it from the trades' own holding intervals rather than
assuming it from the parameters.

**Returns cluster.** `cost_impact` already reports the shape: the gap fade earns 54% of
its R in two years out of twenty and is negative in eight. A resample that draws single
trades independently destroys exactly the structure that makes the mean unreliable, and
reports an interval far too narrow. So the resample draws **contiguous blocks**, which
is the playbook's *dependence-aware resampling such as a block bootstrap*.

What this deliberately is not
-----------------------------
It is not a second gate that can be tuned until a favourite premise passes. There is one
knob, the predeclared effect size, it lives in the activation's committed file, and it
raises the bar rather than lowering it. The interval itself has no free parameters at
spend time: the block length is derived from the data, the replicate count is fixed, and
the seed is fixed so the interval is reproducible from a commit like every other number
in a verdict.

"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from copilot.validation.types import ClosedTrade


MIN_TRADES_FOR_AN_INTERVAL = 2
"""
One observation has no spread to estimate, so no interval is reported for it.
"""

MIN_TRADES_FOR_AUTOCORRELATION = 8
"""
Below this the lag estimates are noise, and summing noise inflates the block.
"""

DEFAULT_REPLICATES = 2000
"""
Bootstrap replicates.

Enough for a stable 5th percentile - the quantity the verdict reads - and small enough
that spending a holdout stays interactive. More replicates narrow the Monte Carlo error
of the interval, not the interval itself, so there is little to buy above this.

"""

DEFAULT_SEED = 20260910
"""
Fixed, so the interval is reproducible from a commit.

A verdict that cannot be recomputed to the same number is not a verdict, and
``copilot.strategies.validate`` exists to recompute them. An interval that moved every
run would make that check meaningless for the one number the holdout turns on.

"""

DEFAULT_CONFIDENCE = Decimal("0.90")
"""
Two-sided coverage, so the verdict reads the 5th percentile.

The one-sided question - *is the lower bound above the bar* - is what a gate asks, and
at 90% two-sided that is the conventional 95% one-sided test.

"""


@dataclass(frozen=True)
class Evidence:
    """
    How much a holdout score is worth, beside the score itself.
    """

    trades: int
    """
    Raw closed trades scored in the window.
    """

    concurrency: Decimal
    """
    Average positions open at once, from the trades' own holding intervals.

    One means they never overlapped. Two means the raw count is twice the independent
    evidence it looks like.

    """

    effective_trades: Decimal
    """
    ``trades`` discounted by the larger of :attr:`concurrency` and the autocorrelation
    time - the number of independent observations the sample is actually worth.

    Reported because the playbook's evidence-sufficiency gate is written in terms of it,
    and because a reader who sees 111 trades and 24 effective ones has learned the thing
    the raw count was hiding.

    """

    block_bars: int
    """
    Block length the resample used, in trades.
    """

    replicates: int

    confidence: Decimal

    lower_r: Decimal
    """
    Lower bound of the interval on net expectancy per trade, in R.
    """

    upper_r: Decimal

    standard_error_r: Decimal
    """
    Bootstrap standard error, reported because it makes the margin legible.

    A score one third of a standard error from zero is a different object from a score
    three standard errors from zero, and the pass/fail bit hides which one it is.

    """

    def clears(self, threshold: Decimal) -> bool:
        """
        Whether the whole interval sits above ``threshold``.

        This is the question the gate asks. A score above the bar with an interval
        straddling it is not a result, it is a direction.

        """
        return self.lower_r > threshold


def concurrency(trades: Sequence[ClosedTrade]) -> Decimal:
    """
    Average number of positions open at once, over the span they were open.

    Total holding time divided by the union of the holding intervals - so a premise
    whose positions never overlap scores one, and a premise holding three at a time
    scores three. Measured rather than derived from the holding-period parameter,
    because early exits and stops make the realised overlap the smaller number.

    Returns one for fewer than two trades, where overlap is not a meaningful idea.

    """
    if len(trades) < MIN_TRADES_FOR_AN_INTERVAL:
        return Decimal(1)

    held = sum((t.closed_at - t.opened_at).total_seconds() for t in trades)
    if held <= 0:
        # Same-session round trips, so every trade is its own observation.
        return Decimal(1)

    intervals = sorted((t.opened_at, t.closed_at) for t in trades)
    union = 0.0
    current_start, current_end = intervals[0]
    for start, end in intervals[1:]:
        if start > current_end:
            union += (current_end - current_start).total_seconds()
            current_start, current_end = start, end
        else:
            current_end = max(current_end, end)
    union += (current_end - current_start).total_seconds()

    if union <= 0:
        return Decimal(1)
    return _decimal(held / union)


def autocorrelation_time(values: Sequence[float]) -> Decimal:
    """
    How many trades it takes before the series stops remembering itself.

    The integrated autocorrelation time, ``1 + 2 * sum(rho(k))`` summed to the first
    non-positive lag. It is the right quantity rather than a proxy for one: it *is* the
    factor by which dependence inflates the variance of the mean, so ``n / tau`` is the
    number of independent observations the sample is worth.

    Summing is deliberate. The first-crossing rule that this replaced read a single lag,
    and a single lag is noise - on a clustered series of 120 the autocorrelation dipped
    under the threshold at lag 3 and climbed back at lag 4, so the rule reported an
    independence horizon of three trades for a series built from runs of twenty.

    Returns one when there is nothing to measure, which is the no-dependence case.

    """
    n = len(values)
    if n < MIN_TRADES_FOR_AUTOCORRELATION:
        return Decimal(1)
    mean = sum(values) / n
    deviations = [v - mean for v in values]
    variance = sum(d * d for d in deviations)
    if variance <= 0:
        return Decimal(1)

    # Beyond a quarter of the sample the autocorrelation is estimated from too few
    # pairs to be worth summing, and including it adds variance rather than signal.
    total = 0.0
    for lag in range(1, n // 4 + 1):
        rho = sum(deviations[i] * deviations[i + lag] for i in range(n - lag)) / variance
        if rho <= 0:
            break
        total += rho

    return max(Decimal(1), _decimal(1 + 2 * total))


def block_length(trades: int, overlap: Decimal, values: Sequence[float] = ()) -> int:
    """
    Blocks long enough to carry the dependence, chosen before the result is seen.

    The largest of three quantities, none of which is a free parameter:

    - ``n ** (1/3)``, the standard order for a moving-block bootstrap of a mean. It is
      the textbook default precisely so that nobody picks a block length that flatters a
      particular series.
    - The measured concurrency, rounded up, because a block shorter than the overlap
      would split positions that were open together and reintroduce the independence
      the block is there to avoid.
    - :func:`autocorrelation_time`, which catches clustering the calendar cannot see.

    Taking the largest is deliberate and always widens the interval. Each term is a
    reason the raw count overstates the evidence, and a rule that averaged them could be
    argued down by whichever term happened to be small.

    """
    if trades <= 1:
        return 1
    standard = round(trades ** (1 / 3))
    horizon = math.ceil(autocorrelation_time(values))
    # A block near the sample length leaves almost nothing to resample, so the interval
    # would stop reflecting the data at all.
    return max(1, min(max(standard, math.ceil(overlap), horizon), max(1, trades // 4)))


def r_multiples(trades: Sequence[ClosedTrade]) -> tuple[float, ...]:
    """
    Each trade's realised return in units of the risk it actually took.

    Float, deliberately, and only here. Every monetary quantity upstream stays
    ``Decimal``; this is the input to a random resample whose output is a percentile
    estimate, where exactness has nothing to be exact about. The point estimate the
    verdict compares against is *not* computed here - it comes from the same
    ``expectancy`` the folds use.

    """
    return tuple(
        float(t.realized_pnl / t.risk_amount) for t in trades if t.risk_amount and t.risk_amount > 0
    )


def assess(
    trades: Sequence[ClosedTrade],
    *,
    replicates: int = DEFAULT_REPLICATES,
    confidence: Decimal = DEFAULT_CONFIDENCE,
    seed: int = DEFAULT_SEED,
) -> Evidence:
    """
    Measure how much the holdout's trades are worth as evidence.

    A moving-block bootstrap: draw ceil(n / L) contiguous blocks of length L with
    replacement from the trade sequence in signal order, truncate to n, take the mean,
    and repeat. The percentiles of those means are the interval.

    """
    overlap = concurrency(trades)
    values = r_multiples(trades)
    n = len(values)
    length = block_length(n, overlap, values)
    # The larger of the two reasons the count overstates the evidence, not their
    # product: they are two views of the same dependence, and multiplying them would
    # double-count a premise whose overlap is exactly why its returns cluster.
    inflation = max(overlap, autocorrelation_time(values), Decimal(1))
    effective = (_decimal(n) / inflation).quantize(Decimal("0.1"))

    if n < MIN_TRADES_FOR_AN_INTERVAL:
        # One trade has no spread to estimate, and reporting a zero-width interval
        # around it would read as certainty rather than as no information.
        point = _decimal(values[0]) if values else Decimal(0)
        return Evidence(
            trades=n,
            concurrency=overlap,
            effective_trades=effective,
            block_bars=length,
            replicates=0,
            confidence=confidence,
            lower_r=Decimal(0),
            upper_r=point,
            standard_error_r=Decimal(0),
        )

    # The seed is fixed so `validate` can recompute this interval from a commit.
    rng = random.Random(seed)  # noqa: S311
    starts = n - length + 1
    blocks = math.ceil(n / length)
    means: list[float] = []
    for _ in range(replicates):
        drawn: list[float] = []
        for _ in range(blocks):
            start = rng.randrange(starts)
            drawn.extend(values[start : start + length])
        means.append(sum(drawn[:n]) / n)

    means.sort()
    tail = float((Decimal(1) - confidence) / 2)
    lower = _percentile(means, tail)
    upper = _percentile(means, 1 - tail)

    centre = sum(means) / replicates
    variance = sum((m - centre) ** 2 for m in means) / (replicates - 1)

    return Evidence(
        trades=n,
        concurrency=overlap,
        effective_trades=effective,
        block_bars=length,
        replicates=replicates,
        confidence=confidence,
        lower_r=_decimal(lower),
        upper_r=_decimal(upper),
        standard_error_r=_decimal(math.sqrt(variance)),
    )


def _percentile(ordered: Sequence[float], fraction: float) -> float:
    """
    Linear-interpolated percentile of an already-sorted sequence.
    """
    if not ordered:
        return 0.0
    position = fraction * (len(ordered) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _decimal(value: float) -> Decimal:
    """
    Six places, matching how every other R figure is written down.
    """
    return Decimal(str(value)).quantize(Decimal("0.000001"))


__all__ = [
    "DEFAULT_CONFIDENCE",
    "DEFAULT_REPLICATES",
    "DEFAULT_SEED",
    "MIN_TRADES_FOR_AN_INTERVAL",
    "Evidence",
    "assess",
    "autocorrelation_time",
    "block_length",
    "concurrency",
    "r_multiples",
]
