"""
In-sample parameter search, ported from trade-copilot.

Selects a **stability plateau, not a peak**, and vetoes candidates whose neighbours
fall off sharply.

The distinction is the whole point. The peak of a noisy surface is usually the
luckiest sample, not the best rule - pick it and the out-of-sample result reverts. A
plateau is a region where the neighbours also work, which is what survives contact
with data the search never saw.

Three mechanisms, in the order they run:

1. **Eligibility.** A candidate whose sample is too small has an expectancy that means
   nothing; it is rejected before it can win on noise.
2. **Cliff veto.** A candidate any of whose neighbours falls off sharply is rejected
   however good it looks itself - that shape is the signature of a fit to noise, not
   of an edge.
3. **Plateau score.** Survivors are ranked by the **worst** score in their own
   neighbourhood (themselves included), so a candidate is only as good as the weakest
   point around it. The mean was tried first and is wrong: a poor candidate sitting
   next to a spike inherits the spike's score and wins on a neighbour's luck. A
   plateau is a region whose *floor* is high.

**Neighbour** means: differs in exactly one parameter, by exactly one step along that
parameter's grid axis. That definition needs the grid's own ordering, which is why the
search owns the grid rather than taking a bag of parameter sets.

Free of I/O and wall-clock time: the same inputs give the same report, so an in-sample
result is reproducible and auditable.

Port note
---------
The original typed its candidates as a pydantic ``StrategyParameters`` subclass and
keyed them by that contract's ``version`` property. Here a parameter set is whatever
``ParameterGrid.factory`` returns - a plain ``dict`` by default - and identity is
derived from the values themselves. The selection algorithm is unchanged.

"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field
from decimal import Decimal
from typing import Any

from copilot.validation.types import BacktestRunResult
from copilot.validation.types import DailyBar


# A sample this small cannot support an expectancy; the default is deliberately blunt
# rather than a statistical test, because the honest answer at n=8 is "run it over more
# data", not "here is a confidence interval".
DEFAULT_MIN_TRADES = 30

# How far a neighbour may fall below a candidate, in the objective's own units, before
# the candidate is treated as a spike. Absolute rather than relative on purpose: scores
# cross zero, and a relative test is meaningless around a sign change (a drop from +0.1
# to -0.1 is not "200%"). In R, so a quarter of the risk taken means the same thing on
# every instrument.
DEFAULT_CLIFF_DROP = Decimal("0.25")


def parameter_version(values: Mapping[str, Any]) -> str:
    """
    Deterministic identity for one parameter set.

    Replaces the original contract's ``version`` property. Sorted so that dictionary
    insertion order cannot change a candidate's identity, which would make the search
    non-deterministic in a way that is very hard to see.

    """
    return "|".join(f"{name}={values[name]!r}" for name in sorted(values))


@dataclass(frozen=True)
class ParameterGrid:
    """
    Candidate values per parameter, in a deliberate order.

    The order defines adjacency: ``stop_atr=[2, 3, 4]`` makes 3 a neighbour of both 2
    and 4, and 2 not a neighbour of 4. Supply values sorted, or adjacency stops meaning
    what it should.

    """

    axes_by_name: Mapping[str, Sequence[Any]] = field(default_factory=dict)
    base: Mapping[str, Any] = field(default_factory=dict)
    """
    Values every candidate starts from, before the searched axes are applied.

    A strategy's *unsearched* parameters are part of its identity, not placeholders for
    the search to fill in. Searched axes win over ``base``, so supplying a base can
    never quietly narrow a search.

    """
    factory: Callable[..., Any] = dict
    """
    Builds a parameter set from keyword values.

    Defaults to ``dict``. A factory that
    raises ``ValueError`` for an invalid combination has that candidate skipped.

    """

    @classmethod
    def of(cls, **axes: Sequence[Any]) -> ParameterGrid:
        """
        Explicit axes; an axis left out is not searched.
        """
        return cls(axes_by_name={k: v for k, v in axes.items() if v})

    def with_base(self, base: Mapping[str, Any]) -> ParameterGrid:
        """
        Return the same search, starting from ``base``.
        """
        return ParameterGrid(axes_by_name=self.axes_by_name, base=dict(base), factory=self.factory)

    def axes(self) -> dict[str, Sequence[Any]]:
        """
        Only the parameters this grid actually varies.
        """
        return {name: values for name, values in self.axes_by_name.items() if values}

    def expand(self) -> tuple[tuple[Any, dict[str, int], str], ...]:
        """
        Every valid combination, with its integer coordinate and its identity.

        Combinations the factory rejects are skipped rather than raised: a grid is a
        sweep of intent, and a few invalid corners are expected, not a caller error.

        """
        axes = self.axes()
        if not axes:
            values = dict(self.base)
            try:
                return ((self.factory(**values), {}, parameter_version(values)),)
            except ValueError:
                return ()

        names = list(axes)
        expanded: list[tuple[Any, dict[str, int], str]] = []
        for combo in itertools.product(*(range(len(axes[name])) for name in names)):
            coordinate = dict(zip(names, combo, strict=True))
            # Base first, searched axes second: a searched value always wins, so
            # supplying a base can never silently narrow the declared search.
            values = dict(self.base)
            values.update({name: axes[name][index] for name, index in coordinate.items()})
            try:
                parameters = self.factory(**values)
            except ValueError:
                continue
            expanded.append((parameters, coordinate, parameter_version(values)))
        return tuple(expanded)


@dataclass(frozen=True)
class CandidateResult:
    """
    One parameter set's in-sample outcome.
    """

    parameters: Any
    coordinate: Mapping[str, int]
    version: str
    trades: int
    score: Decimal
    """
    The objective - expectancy per trade in R by default.

    Zero when there are no trades, which the eligibility gate rejects anyway.

    """
    net: Decimal
    """
    Total realised P&L in currency.

    Reported, never optimised on: it is what the run
    made, while ``score`` is what the run is judged by. Keeping the currency figure is
    what makes a cost analysis possible at all.

    """
    wins: int

    @property
    def win_rate(self) -> Decimal:
        """
        Share of this candidate's trades that made money.
        """
        if not self.trades:
            return Decimal(0)
        return Decimal(self.wins) / Decimal(self.trades)


@dataclass(frozen=True)
class InSampleReport:
    """
    Everything the search saw, not only what it chose.

    Rejections carry their reason so a record can show *why* a better-scoring set was
    passed over - the audit that makes "we did not pick the peak" a checkable claim
    rather than an assertion.

    """

    candidates: tuple[CandidateResult, ...]
    selected: CandidateResult | None
    plateau_scores: Mapping[str, Decimal]
    rejections: Mapping[str, str] = field(default_factory=dict)

    @property
    def peak(self) -> CandidateResult | None:
        """
        The highest raw score, selected or not - the set a naive search takes.
        """
        eligible = [c for c in self.candidates if c.trades]
        if not eligible:
            return None
        return max(eligible, key=lambda c: c.score)

    @property
    def selected_the_peak(self) -> bool:
        """
        Whether the search landed on the raw peak, which it exists to avoid.
        """
        peak = self.peak
        return (
            peak is not None and self.selected is not None and peak.version == self.selected.version
        )


def expectancy_r(result: BacktestRunResult) -> Decimal:
    """
    Mean R per closed trade - the scale-free objective.

    R is ``realized_pnl / risk_amount``: what the trade made per unit of what it put at
    risk. Scoring dollars instead would put each symbol's price level inside its score
    and leave no two symbols comparable.

    """
    if not result.trades:
        return Decimal(0)
    total = sum((trade.r_multiple for trade in result.trades), Decimal(0))
    return total / Decimal(len(result.trades))


# The objective *is* the expectancy; the explicit name is kept for call sites that want
# to say which unit they mean.
expectancy = expectancy_r


def _are_neighbours(left: Mapping[str, int], right: Mapping[str, int]) -> bool:
    """
    Differ in exactly one axis, by exactly one step along it.
    """
    if left.keys() != right.keys():
        return False
    diffs = [abs(left[name] - right[name]) for name in left]
    return sum(1 for d in diffs if d) == 1 and max(diffs, default=0) == 1


Replay = Callable[[Sequence[DailyBar], Any], BacktestRunResult]


def search_in_sample(
    bars: Sequence[DailyBar],
    grid: ParameterGrid,
    *,
    replay: Replay,
    objective: Callable[[BacktestRunResult], Decimal] = expectancy,
    min_trades: int = DEFAULT_MIN_TRADES,
    cliff_drop: Decimal = DEFAULT_CLIFF_DROP,
) -> InSampleReport:
    """
    Replay ``bars`` for every grid point and select a stability plateau.

    Cost is one full replay per grid point, so a six-axis grid is expensive by
    construction - that is the nature of the search, not an inefficiency to hide. Size
    the grid against the bar count being replayed.

    ``replay`` is injected because the selection rule is the thing worth testing, and
    it should be testable against a controlled score surface rather than against
    whatever a real engine produces. It is required here rather than defaulted, since
    this overlay has no single canonical replay.

    """
    expanded = grid.expand()
    candidates: list[CandidateResult] = []
    for parameters, coordinate, version in expanded:
        result = replay(bars, parameters)
        trades = result.trades
        candidates.append(
            CandidateResult(
                parameters=parameters,
                coordinate=coordinate,
                version=version,
                trades=len(trades),
                score=objective(result),
                net=sum((t.realized_pnl for t in trades), Decimal(0)),
                wins=sum(1 for t in trades if t.realized_pnl > 0),
            ),
        )

    rejections: dict[str, str] = {}
    neighbours: dict[str, list[CandidateResult]] = {
        c.version: [
            other
            for other in candidates
            if other.version != c.version and _are_neighbours(c.coordinate, other.coordinate)
        ]
        for c in candidates
    }

    plateau_scores: dict[str, Decimal] = {}
    eligible: list[CandidateResult] = []
    for candidate in candidates:
        own = neighbours[candidate.version]
        window = [candidate.score, *(n.score for n in own)]
        # The floor of the neighbourhood, not its mean - see the module docstring.
        plateau_scores[candidate.version] = min(window)

        if candidate.trades < min_trades:
            rejections[candidate.version] = (
                f"insufficient_trades: {candidate.trades} < {min_trades}"
            )
            continue
        if len(expanded) > 1 and not own:
            # An isolated point cannot demonstrate a plateau, so it cannot be selected
            # on stability grounds - only on being the peak, which is exactly what this
            # search exists to avoid.
            rejections[candidate.version] = "no_neighbours: stability is undemonstrable"
            continue
        worst = min((n.score for n in own), default=candidate.score)
        if candidate.score - worst > cliff_drop:
            rejections[candidate.version] = (
                f"cliff: neighbour {worst} is more than {cliff_drop} below {candidate.score}"
            )
            continue
        eligible.append(candidate)

    selected = None
    if eligible:
        # Ties break toward the *interior* of a plateau: more neighbours means more
        # evidence that the region is flat, where an edge point may simply be sitting
        # against a boundary the grid cannot see past. Then raw score, then version, so
        # the search is deterministic rather than dependent on grid iteration order.
        selected = max(
            eligible,
            key=lambda c: (
                plateau_scores[c.version],
                len(neighbours[c.version]),
                c.score,
                c.version,
            ),
        )

    return InSampleReport(
        candidates=tuple(candidates),
        selected=selected,
        plateau_scores=plateau_scores,
        rejections=rejections,
    )


__all__ = [
    "DEFAULT_CLIFF_DROP",
    "DEFAULT_MIN_TRADES",
    "CandidateResult",
    "InSampleReport",
    "ParameterGrid",
    "Replay",
    "expectancy",
    "expectancy_r",
    "parameter_version",
    "search_in_sample",
]
