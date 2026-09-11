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

Clustering the trade order cannot see
-------------------------------------
The autocorrelation time is measured in **trade order**, and a one-position premise's
consecutive trades barely correlate, so on nine of twelve filed verdicts it read one and
``effective_trades`` equalled the raw count - 439 of 439 for SPY - while the clustering the
paragraph above describes is by **calendar year** (``docs/AUDIT_2026-09-11.md``, F17). So
:func:`calendar_design_effect` measures that too: the one-way intraclass correlation of net R
within calendar years of entry, and the design effect ``1 + (m - 1) rho`` it implies, the
standard discount for a clustered sample. ``effective_trades`` divides by the largest of the
three. The **interval** is unchanged: re-drawn with whole calendar years as blocks it moved no
verdict (the audit's check), so the block length stays the data-derived moving block.

What this deliberately is not
-----------------------------
**The series is net of costs.** The engine replays with no fees, so a trade's recorded
P&L is gross, and the score every verdict reports is that minus the cost model's charge.
An interval bootstrapped on the gross series brackets a number the verdict never scored,
on the flattering side. So :func:`assess` takes the cost function as a required argument:
every caller has to say what it charges, and charging nothing has to be said out loud.

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
    from collections.abc import Callable

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
    ``trades`` discounted by the largest of :attr:`concurrency`, the autocorrelation time and
    :attr:`calendar_design_effect` - the independent observations the sample is worth.

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

    calendar_design_effect: Decimal = Decimal(1)
    """
    How much clustering within calendar years inflates the variance of the mean; one
    when the years do not differ more than chance allows.
    """

    mean_r: Decimal = Decimal(0)
    """
    Exact mean of the net series the interval was drawn from.

    Carried so the interval can be checked against the score it sits beside: if the two
    differ, the interval describes some other series, and :func:`require_describes`
    refuses to let it be reported as this one's.

    """

    def clears(self, threshold: Decimal) -> bool:
        """
        Whether the whole interval sits above ``threshold``.

        This is the question the gate asks. A score above the bar with an interval
        straddling it is not a result, it is a direction. With no interval drawn - fewer than
        two trades - nothing clears anything.

        """
        return self.replicates > 0 and self.lower_r > threshold

    def as_record(self) -> dict[str, object]:
        """
        Return the filed form, identical wherever an interval is written down.
        """
        return {
            "series": "net R per trade, in signal order",
            "trades": self.trades,
            "mean_r": str(self.mean_r),
            "effective_trades": str(self.effective_trades),
            "concurrency": str(self.concurrency),
            "calendar_design_effect": str(self.calendar_design_effect),
            "block_trades": self.block_bars,
            "replicates": self.replicates,
            "confidence": str(self.confidence),
            "lower_r": str(self.lower_r),
            "upper_r": str(self.upper_r),
            "standard_error_r": str(self.standard_error_r),
        }


class IncoherentEvidenceError(ValueError):
    """
    The interval was drawn from a different series than the score it would sit beside.
    """


MEAN_TOLERANCE_R = Decimal("0.000001")
"""
How far the series mean may sit from the score before they are different numbers.

The score is exact ``Decimal``; this matches the six places every R figure is written to,
so a rounding difference passes and a cost charged on one side only - tenths of an R at
target size - cannot.

"""


def require_describes(evidence: Evidence, score: Decimal) -> Evidence:
    """
    Return ``evidence`` if its series mean is ``score``, or refuse.

    For a caller holding the objective and the cost function as separate arguments,
    where nothing else forces them to agree. A net objective with a gross cost function
    is exactly the defect that shipped in the first version of this module, and it
    produced a plausible interval rather than an error.

    """
    if abs(evidence.mean_r - score) > MEAN_TOLERANCE_R:
        raise IncoherentEvidenceError(
            f"the interval's series averages {evidence.mean_r} R and the score is "
            f"{score.quantize(MEAN_TOLERANCE_R)} R. The objective and the cost function "
            "disagree about what a trade costs, so the interval describes a different "
            "series than the one scored.",
        )
    return evidence


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


MIN_CALENDAR_CLUSTERS = 2


def calendar_design_effect(values: Sequence[float], years: Sequence[int]) -> Decimal:
    """
    Return the variance inflation from returns clustering within calendar years.

    One-way analysis of variance across the years of entry: the intraclass correlation
    ``rho = (MSB - MSW) / (MSB + (n0 - 1) MSW)``, with ``n0`` the usual adjusted cluster size,
    and the design effect ``1 + (m - 1) rho`` for mean cluster size ``m``. A negative ``rho`` is
    sampling noise around no clustering and reads as one.

    Returns one with fewer than two years, or fewer trades than years plus one, where the
    within-year variance cannot be estimated.

    """
    n = len(values)
    groups: dict[int, list[float]] = {}
    for value, year in zip(values, years, strict=True):
        groups.setdefault(year, []).append(value)
    k = len(groups)
    if k < MIN_CALENDAR_CLUSTERS or n <= k:
        return Decimal(1)
    grand = sum(values) / n
    between = sum(len(g) * (sum(g) / len(g) - grand) ** 2 for g in groups.values()) / (k - 1)
    within = sum(sum((v - sum(g) / len(g)) ** 2 for v in g) for g in groups.values()) / (n - k)
    n0 = (n - sum(len(g) ** 2 for g in groups.values()) / n) / (k - 1)
    denominator = between + (n0 - 1) * within
    if denominator <= 0:
        return Decimal(1)
    rho = max(0.0, (between - within) / denominator)
    return max(Decimal(1), _decimal(1 + (n / k - 1) * rho))


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


def net_r(
    trades: Sequence[ClosedTrade],
    cost_r: Callable[[ClosedTrade], Decimal],
) -> tuple[Decimal, ...]:
    """
    Each trade's return in units of the risk it took, less its round-trip cost.

    Exact, and computed the way the cost model's objective computes it, so the mean of
    this series is the score and not an approximation of it. Trades with no recorded risk
    are dropped: their R is undefined, not zero.

    """
    return tuple(t.r_multiple - cost_r(t) for t in scoreable(trades))


def scoreable(trades: Sequence[ClosedTrade]) -> tuple[ClosedTrade, ...]:
    """
    Return the trades with a recorded risk, the only ones an R can be computed for.

    One filter for every quantity :func:`assess` reports, so the overlap, the clustering and
    the mean are all measured on the same trades.

    """
    return tuple(t for t in trades if t.risk_amount and t.risk_amount > 0)


def assess(
    trades: Sequence[ClosedTrade],
    *,
    cost_r: Callable[[ClosedTrade], Decimal],
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
    trades = scoreable(trades)
    overlap = concurrency(trades)
    exact = net_r(trades, cost_r)
    # Float, deliberately, and only for the resample: its output is a percentile estimate,
    # where exactness has nothing to be exact about. The mean it is checked against stays
    # exact, because that one is compared to a score.
    values = tuple(float(value) for value in exact)
    n = len(values)
    mean = (sum(exact, Decimal(0)) / n).quantize(MEAN_TOLERANCE_R) if n else Decimal(0)
    length = block_length(n, overlap, values)
    # The larger of the two reasons the count overstates the evidence, not their
    # product: they are two views of the same dependence, and multiplying them would
    # double-count a premise whose overlap is exactly why its returns cluster.
    clustering = calendar_design_effect(values, [t.opened_at.year for t in trades])
    inflation = max(overlap, autocorrelation_time(values), clustering, Decimal(1))
    effective = (_decimal(n) / inflation).quantize(Decimal("0.1"))

    if n < MIN_TRADES_FOR_AN_INTERVAL:
        # One trade has no spread to estimate. The point is filed as both ends with no
        # replicates, which :meth:`Evidence.clears` reads as no interval - until
        # 2026-09-11 a single losing trade filed a lower bound above its upper one.
        point = mean if n else Decimal(0)
        return Evidence(
            trades=n,
            concurrency=overlap,
            effective_trades=effective,
            block_bars=length,
            replicates=0,
            confidence=confidence,
            lower_r=point,
            upper_r=point,
            standard_error_r=Decimal(0),
            calendar_design_effect=clustering,
            mean_r=mean,
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
    lower = percentile(means, tail)
    upper = percentile(means, 1 - tail)

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
        calendar_design_effect=clustering,
        mean_r=mean,
    )


def percentile(ordered: Sequence[float], fraction: float) -> float:
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
    "MEAN_TOLERANCE_R",
    "MIN_TRADES_FOR_AN_INTERVAL",
    "Evidence",
    "IncoherentEvidenceError",
    "assess",
    "autocorrelation_time",
    "block_length",
    "calendar_design_effect",
    "concurrency",
    "net_r",
    "percentile",
    "require_describes",
    "scoreable",
]
