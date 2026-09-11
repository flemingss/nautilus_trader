"""
The randomised-signal control every experiment is compared against.

[`RESEARCH.md`](../docs/playbook/RESEARCH.md) lists **a randomised or permuted signal with
similar trading frequency** among the baselines each experiment runs, and *benchmarks and null
controls run* on its checklist. Nothing built it until 2026-09-11.

Why a timing premise needs it
-----------------------------
[ADR-0026](../docs/decisions/0026-attribution-is-measured-per-trade.md) regresses each trade's
excess R on its own leverage times the factor returns **over that trade's own window**. For a
long position in a broad index fund the trade's R is close to that leverage times the market's
return over the same window, so the fit returns a market loading near one and an alpha near
zero **however well the entries were timed**: choosing better windows raises the regressor, and
the regression books the gain to the loading. Alpha is the right test for a premise whose edge
is in which instrument it holds. It cannot see a premise whose edge is in *when* it holds one,
and on the charter's universe - liquid US ETFs, long only - every premise is the second kind.

The question this answers is the one attribution cannot: **did the sessions the rule chose earn
more than sessions chosen by chance?** Same instrument, same number of entries, same holding
rule, same stop and sizing; only the entry dates are drawn at random from the development
window. The premise's mean sits somewhere in that distribution, and where it sits is the
reading.

What it is not
--------------
**Not a replacement for the interval or for alpha.** A premise that beats chance on entry dates
can still fail its net evidence interval, and one whose edge is selection rather than timing is
still judged by alpha. This is filed **beside** a verdict, never instead of one.

**Not a p-value to hunt.** The threshold is declared here, once: a one-sided p at or below 0.10,
the complement of the 90% confidence every interval in this project uses. Running the control
repeatedly with different draws until it passes is the failure the whole playbook is written
against, which is why the seed is fixed and recorded with the result.

The refusals
------------
- **Fewer than :data:`MIN_REPLICATES` replicates** is refused. A percentile drawn from a handful
  of replays is noise reported as a number.
- **More than :data:`MAX_SKIPPED_SHARE` of replicates scoring nothing** withholds the reading.
  A replicate that produced too few trades to score is not evidence of anything, and a
  distribution built from the ones that happened to work is a selected sample - the same rule
  ADR-0031 applies to singular resamples in attribution.

"""

from __future__ import annotations

import random
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Sequence
    from datetime import date


DEFAULT_REPLICATES = 500
"""
Replicates a control runs by default.

Each one is a replay over the whole development window, so this is minutes of machine time
rather than the milliseconds a bootstrap costs; 500 puts the smallest resolvable p-value at
0.002, which is finer than any reading here is meant to support.

"""

MIN_REPLICATES = 100
"""
Fewer than this is refused: a percentile from a handful of replays is noise with a number on it.
"""

MAX_SKIPPED_SHARE = Decimal("0.05")
"""
Above this share of replicates scoring nothing, the reading is withheld rather than
reported.
"""

SIGNIFICANCE = Decimal("0.10")
"""
The declared one-sided p at or below which a premise is said to beat chance.

The complement of the 90% confidence [ADR-0024] uses for every interval, so the two readings
are made at the same strictness.

[ADR-0024]: ../docs/decisions/0024-a-holdout-pass-needs-an-interval.md

"""

BEATS_CHANCE = "beats_chance"
INDISTINGUISHABLE = "indistinguishable_from_chance"
WITHHELD = "withheld: too many replicates scored nothing"


@dataclass(frozen=True)
class NullControl:
    """
    What random entry dates earned, and where the premise sits among them.
    """

    replicates: int
    """
    Replicates asked for.
    """
    skipped: int
    """
    Replicates that scored nothing, because they produced too few trades to score.
    """
    premise_mean_r: Decimal
    premise_trades: int
    null_means: tuple[Decimal, ...]
    """
    Every scored replicate's mean R per trade, in the order drawn.
    """
    seed: int
    significance: Decimal = SIGNIFICANCE

    @property
    def scored(self) -> int:
        """
        Replicates that produced a score.
        """
        return len(self.null_means)

    @property
    def beaten(self) -> int:
        """
        Scored replicates whose mean reached the premise's.
        """
        return sum(1 for mean in self.null_means if mean >= self.premise_mean_r)

    @property
    def p_value(self) -> Decimal:
        """
        The one-sided p: how often chance matched the premise, counting the premise itself.

        ``(1 + beaten) / (1 + scored)``, which cannot report zero - a control of 500 replicates
        can say a result is rarer than one in 501 and no more than that.

        """
        if not self.scored:
            return Decimal(1)
        return (Decimal(1 + self.beaten) / Decimal(1 + self.scored)).quantize(Decimal("0.0001"))

    @property
    def percentile(self) -> Decimal:
        """
        Where the premise sits in the null distribution, in percent.
        """
        if not self.scored:
            return Decimal(0)
        below = sum(1 for mean in self.null_means if mean < self.premise_mean_r)
        return (Decimal(100 * below) / Decimal(self.scored)).quantize(Decimal("0.01"))

    @property
    def null_mean(self) -> Decimal:
        """
        What a randomly chosen set of entry dates earned on average.
        """
        if not self.scored:
            return Decimal(0)
        return (sum(self.null_means, Decimal(0)) / Decimal(self.scored)).quantize(
            Decimal("0.000001"),
        )

    @property
    def withheld(self) -> bool:
        """
        Whether too many replicates scored nothing for the reading to mean anything.
        """
        if not self.replicates:
            return True
        return Decimal(self.skipped) / Decimal(self.replicates) > MAX_SKIPPED_SHARE

    @property
    def reading(self) -> str:
        """
        The verdict this control supports, named rather than left to the reader.
        """
        if self.withheld:
            return WITHHELD
        return BEATS_CHANCE if self.p_value <= self.significance else INDISTINGUISHABLE

    def as_record(self) -> dict[str, object]:
        """
        Return the JSON form, every number as a string, without the raw distribution.

        The means themselves are not filed: five hundred of them bury the record they sit in,
        and every question asked of them here - the mean, the percentile, the p - is answered
        above. A control is reproducible from its seed.

        """
        return {
            "control": "random entry dates, matched count and holding rule",
            "replicates": self.replicates,
            "scored": self.scored,
            "skipped": self.skipped,
            "seed": self.seed,
            "significance": str(self.significance),
            "premise_mean_r": str(self.premise_mean_r),
            "premise_trades": self.premise_trades,
            "null_mean_r": str(self.null_mean),
            "percentile": str(self.percentile),
            "p_value": str(self.p_value),
            "reading": self.reading,
        }


def random_entry_days(
    sessions: Sequence[date],
    *,
    count: int,
    rng: random.Random,
) -> tuple[date, ...]:
    """
    Draw ``count`` entry sessions without replacement, in date order.

    Without replacement because the premise cannot enter the same session twice either,
    and a null that could would hold a different number of positions than the rule it
    stands in for.

    """
    if count <= 0:
        raise ValueError(f"count must be positive, got {count}")
    if count > len(sessions):
        raise ValueError(
            f"cannot draw {count} entry sessions from {len(sessions)}: the control has to be "
            f"able to enter as often as the premise did",
        )
    return tuple(sorted(rng.sample(list(sessions), count)))


def run_null_control(
    replicate: Callable[[tuple[date, ...]], Decimal | None],
    sessions: Sequence[date],
    *,
    entries: int,
    premise_mean_r: Decimal,
    premise_trades: int,
    replicates: int = DEFAULT_REPLICATES,
    seed: int = 20260911,
    significance: Decimal = SIGNIFICANCE,
) -> NullControl:
    """
    Run the premise's own machinery on random entry dates.

    ``replicate`` is given one drawn set of entry sessions and returns that run's mean R per
    trade, or None when it produced too few trades to score. Composing the run and the scoring
    is the caller's, so the control charges the same costs and keeps the same stop and sizing
    the premise used - a null scored differently from the premise measures the difference in
    scoring.

    """
    if replicates < MIN_REPLICATES:
        raise ValueError(
            f"{replicates} replicates cannot support a percentile; the control needs "
            f"{MIN_REPLICATES}",
        )
    rng = random.Random(seed)  # noqa: S311 - fixed for reproducibility, not secrecy
    means: list[Decimal] = []
    skipped = 0
    for _ in range(replicates):
        scored = replicate(random_entry_days(sessions, count=entries, rng=rng))
        if scored is None:
            skipped += 1
            continue
        means.append(scored)
    return NullControl(
        replicates=replicates,
        skipped=skipped,
        premise_mean_r=premise_mean_r,
        premise_trades=premise_trades,
        null_means=tuple(means),
        seed=seed,
        significance=significance,
    )


__all__ = [
    "BEATS_CHANCE",
    "DEFAULT_REPLICATES",
    "INDISTINGUISHABLE",
    "MAX_SKIPPED_SHARE",
    "MIN_REPLICATES",
    "SIGNIFICANCE",
    "WITHHELD",
    "NullControl",
    "random_entry_days",
    "run_null_control",
]
