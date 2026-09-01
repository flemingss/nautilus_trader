"""
Purged walk-forward analysis, ported from trade-copilot.

Rolling train/test windows with **purge gaps** to prevent leakage, and a
**majority-pass gate**: the strategy must pass in a majority of out-of-sample folds,
not just on average.

**What walk-forward validates is the selection process, not one parameter set.** Each
fold runs the in-sample search on its own training window and tests whatever *that*
search chose. A strategy passes when the procedure generalises repeatedly - a much
stronger claim than one parameter set happening to work once, and it is why averaging
fold results is explicitly not the gate: one spectacular fold can carry a mean while
the rule loses money most of the time.

Two subtleties the method leaves open, settled here:

**The purge gap separates training from testing, not the model from history.** Bars in
the gap are never used to *select* parameters, but the test replay is allowed to warm
up on the bars immediately preceding its window - because in live trading the feature
engine always has prior history, and denying it would measure a cold start rather than
the strategy. Only trades whose signal was *generated inside the test window* are
scored.

**Parameter stability is reported even though it is not a gate.** Folds that each pass
while selecting wildly different parameters are a warning: the process is finding
something in every window, which is what an overfit process also does.

Port note
---------
The original derived a default warm-up from the grid by introspecting its indicator and
setup registries. Those registries are not part of this overlay, so ``warmup_bars`` is
**required** here rather than defaulted. That is deliberate: the original's own history
records that guessing warm-up wrong made test windows cold-start, score no trades, and
have the majority gate read *a missing history as a failing fold*. A gate that fails a
strategy for the harness's own oversight is worse than no gate, because the failure
looks like evidence. Making it explicit removes that failure mode rather than
reproducing it.

"""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from copilot.validation.insample import DEFAULT_CLIFF_DROP
from copilot.validation.insample import DEFAULT_MIN_TRADES
from copilot.validation.insample import CandidateResult
from copilot.validation.insample import InSampleReport
from copilot.validation.insample import ParameterGrid
from copilot.validation.insample import Replay
from copilot.validation.insample import expectancy
from copilot.validation.insample import search_in_sample
from copilot.validation.tearsheet import Tearsheet
from copilot.validation.tearsheet import tearsheet_for
from copilot.validation.types import BacktestRunResult
from copilot.validation.types import ClosedTrade
from copilot.validation.types import DailyBar


# A fold passes when its out-of-sample expectancy clears this. Zero, not a comfortable
# margin: costs are already modelled in the fill, so "better than not trading" is the
# honest bar, and setting it higher would quietly re-tune the gate rather than the
# strategy. Zero also means the same thing whether the objective is dollars or R.
DEFAULT_FOLD_PASS_THRESHOLD = Decimal(0)


@dataclass(frozen=True)
class FoldWindows:
    """
    Index ranges for one fold.

    Half-open, as Python slices.

    """

    index: int
    train_start: int
    train_end: int
    purge_end: int
    test_end: int

    @property
    def train(self) -> slice:
        """
        Bars the in-sample search may select on.
        """
        return slice(self.train_start, self.train_end)

    @property
    def test(self) -> slice:
        """
        Bars scored out of sample, after the purge gap.
        """
        return slice(self.purge_end, self.test_end)


def build_folds(
    total_bars: int,
    *,
    train_bars: int,
    test_bars: int,
    purge_bars: int,
    step_bars: int | None = None,
) -> tuple[FoldWindows, ...]:
    """
    Build rolling train/purge/test windows over ``total_bars``.

    ``step_bars`` defaults to ``test_bars``, which tiles the test windows without
    overlap - overlapping test windows would score the same bars more than once and
    inflate the fold count the majority gate divides by.

    Returns empty when the series cannot fit even one fold, rather than raising: "this
    history is too short to walk forward" is a finding a caller should be able to
    report, not an exception to handle.

    """
    if train_bars <= 0 or test_bars <= 0:
        raise ValueError("train_bars and test_bars must be positive")
    if purge_bars < 0:
        raise ValueError("purge_bars must not be negative")

    step = step_bars if step_bars is not None else test_bars
    if step <= 0:
        raise ValueError("step_bars must be positive")

    folds: list[FoldWindows] = []
    start = 0
    while True:
        train_end = start + train_bars
        purge_end = train_end + purge_bars
        test_end = purge_end + test_bars
        if test_end > total_bars:
            break
        folds.append(
            FoldWindows(
                index=len(folds),
                train_start=start,
                train_end=train_end,
                purge_end=purge_end,
                test_end=test_end,
            ),
        )
        start += step
    return tuple(folds)


@dataclass(frozen=True)
class FoldResult:
    """
    One fold's outcome, carrying enough to audit the decision.
    """

    index: int
    windows: FoldWindows
    train_from: datetime
    test_from: datetime
    test_to: datetime
    in_sample: InSampleReport
    selected: CandidateResult | None
    test_trades: int
    test_score: Decimal
    passed: bool
    reason: str
    test_trade_details: tuple[ClosedTrade, ...] = ()
    """
    The scored trades themselves, kept so a tearsheet can be built over one fold or over
    every fold together.

    The fold's own verdict needs only the count and the score; a human reading the
    result needs the shape.

    """

    @property
    def tearsheet(self) -> Tearsheet:
        """
        Win rate, profit factor, drawdown and the rest, for this fold alone.
        """
        return tearsheet_for(self.test_trade_details)

    @property
    def selected_version(self) -> str | None:
        """
        Identity of the parameter set this fold chose, if any.
        """
        return self.selected.version if self.selected else None


@dataclass(frozen=True)
class WalkForwardReport:
    """
    The majority-pass verdict, plus what it was based on.
    """

    folds: tuple[FoldResult, ...]
    threshold: Decimal

    @property
    def evaluated(self) -> tuple[FoldResult, ...]:
        """
        Folds whose in-sample search produced a set to test.

        A fold where the search selected nothing is not a pass and not a fail - there
        was no strategy to evaluate. Counting it either way would misstate the gate.

        """
        return tuple(fold for fold in self.folds if fold.selected is not None)

    @property
    def passed_count(self) -> int:
        """
        How many evaluated folds cleared the threshold.
        """
        return sum(1 for fold in self.evaluated if fold.passed)

    @property
    def majority_passed(self) -> bool:
        """
        Strictly more than half of the evaluated folds passed.

        Strict, so an even split fails: a coin flip is not evidence of an edge.

        """
        evaluated = len(self.evaluated)
        return evaluated > 0 and self.passed_count * 2 > evaluated

    @property
    def mean_score(self) -> Decimal:
        """
        Reported for context, deliberately **not** the gate.

        One spectacular fold can carry a mean while the rule loses money in most
        windows, which is exactly what the majority gate exists to catch.

        """
        evaluated = self.evaluated
        if not evaluated:
            return Decimal(0)
        return sum((f.test_score for f in evaluated), Decimal(0)) / Decimal(len(evaluated))

    @property
    def tearsheet(self) -> Tearsheet:
        """
        Every evaluated fold's out-of-sample trades, pooled.

        Pooled rather than averaged across folds: a mean of per-fold win rates weights
        a three-trade fold equally with a thirty-trade one. Pooling asks the question a
        human actually has - "across everything this rule did out of sample, what
        happened" - and the per-fold majority verdict is what keeps a single strong
        window from carrying the gate.

        """
        return tearsheet_for(
            tuple(trade for fold in self.evaluated for trade in fold.test_trade_details),
        )

    @property
    def selected_versions(self) -> tuple[str, ...]:
        """
        What each evaluated fold chose, for the stability report.
        """
        return tuple(f.selected_version or "" for f in self.evaluated)

    @property
    def parameter_stability(self) -> Decimal:
        """
        Share of evaluated folds that chose the most common parameter set.

        Not a gate, a warning light: folds that all pass while each choosing something
        different mean the process finds *something* in every window - which is also
        what an overfit process does.

        """
        versions = [v for v in self.selected_versions if v]
        if not versions:
            return Decimal(0)
        most_common = max(set(versions), key=versions.count)
        return Decimal(versions.count(most_common)) / Decimal(len(versions))

    @property
    def attempts(self) -> int:
        """
        Total candidates replayed across every fold's search.

        The input the deflation statistic needs: how hard was this searched.

        """
        return sum(len(f.in_sample.candidates) for f in self.folds)


def walk_forward(
    bars: Sequence[DailyBar],
    grid: ParameterGrid,
    *,
    train_bars: int,
    test_bars: int,
    purge_bars: int,
    warmup_bars: int,
    replay: Replay,
    step_bars: int | None = None,
    objective: Callable[[BacktestRunResult], Decimal] = expectancy,
    min_trades: int = DEFAULT_MIN_TRADES,
    cliff_drop: Decimal = DEFAULT_CLIFF_DROP,
    fold_min_trades: int = 1,
    threshold: Decimal = DEFAULT_FOLD_PASS_THRESHOLD,
) -> WalkForwardReport:
    """
    Run the in-sample search per fold and test what each fold chose.

    ``warmup_bars`` is drawn from the bars **immediately preceding** the test window:
    the purge gap and, if the gap is shorter than the warm-up, the tail of training.
    That does not leak - those bars never influenced *selection*, and in live trading
    the feature engine would have them.

    """
    if warmup_bars < 0:
        raise ValueError("warmup_bars must not be negative")

    ordered = tuple(sorted(bars, key=lambda bar: bar.closed_at))
    folds = build_folds(
        len(ordered),
        train_bars=train_bars,
        test_bars=test_bars,
        purge_bars=purge_bars,
        step_bars=step_bars,
    )

    results: list[FoldResult] = []
    for fold in folds:
        train_slice = ordered[fold.train]
        report = search_in_sample(
            train_slice,
            grid,
            replay=replay,
            objective=objective,
            min_trades=min_trades,
            cliff_drop=cliff_drop,
        )
        selected = report.selected

        test_slice = ordered[fold.test]
        test_from = test_slice[0].closed_at
        test_to = test_slice[-1].closed_at

        if selected is None:
            results.append(
                FoldResult(
                    index=fold.index,
                    windows=fold,
                    train_from=train_slice[0].closed_at,
                    test_from=test_from,
                    test_to=test_to,
                    in_sample=report,
                    selected=None,
                    test_trades=0,
                    test_score=Decimal(0),
                    passed=False,
                    reason="in_sample_selected_nothing",
                ),
            )
            continue

        warmup_start = max(0, fold.purge_end - warmup_bars)
        replayed = replay(ordered[warmup_start : fold.test_end], selected.parameters)
        # Score only what the test window itself produced: the warm-up exists to give
        # the feature engine history, not to contribute trades.
        scored = tuple(t for t in replayed.trades if test_from <= t.signal_created_at <= test_to)
        windowed = BacktestRunResult(
            trades=scored,
            signals=replayed.signals,
            features=replayed.features,
        )
        score = objective(windowed)

        if len(scored) < fold_min_trades:
            passed, reason = False, f"insufficient_test_trades: {len(scored)} < {fold_min_trades}"
        elif score > threshold:
            passed, reason = True, f"score {score} above {threshold}"
        else:
            passed, reason = False, f"score {score} not above {threshold}"

        results.append(
            FoldResult(
                index=fold.index,
                windows=fold,
                train_from=train_slice[0].closed_at,
                test_from=test_from,
                test_to=test_to,
                in_sample=report,
                selected=selected,
                test_trades=len(scored),
                test_score=score,
                passed=passed,
                reason=reason,
                test_trade_details=scored,
            ),
        )

    return WalkForwardReport(folds=tuple(results), threshold=threshold)


__all__ = [
    "DEFAULT_FOLD_PASS_THRESHOLD",
    "FoldResult",
    "FoldWindows",
    "WalkForwardReport",
    "build_folds",
    "walk_forward",
]
