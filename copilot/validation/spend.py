"""
The single-use holdout test, built as exactly one more fold.

The walk-forward gate validates a *selection process*: each fold searches its training
window and tests what that search chose. The holdout asks the process the same question
one last time, with the whole development window as training and the locked holdout as
the test - so the number it produces was made by the same evaluator, the same objective,
the same plateau rule and the same scoring window as every fold behind every verdict.
Nothing here is a second methodology that could be tuned to flatter the first.

What is deliberately *not* here: any way to run it twice, any parameter the operator
chooses at spend time, and any use of the holdout bars before the final scoring replay.
The first two are enforced by :mod:`copilot.strategies.spend_holdout`, which owns the
spent marker and the confirmation; the third is enforced by construction below and pinned
by tests. [ADR-0014] records the decision.

[ADR-0014]: ../docs/decisions/0014-the-holdout-is-spent-as-one-more-fold.md

"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from copilot.validation.evidence import Evidence
from copilot.validation.evidence import assess
from copilot.validation.insample import DEFAULT_CLIFF_DROP
from copilot.validation.insample import DEFAULT_MIN_TRADES
from copilot.validation.insample import expectancy
from copilot.validation.tearsheet import Tearsheet
from copilot.validation.walkforward import DEFAULT_FOLD_PASS_THRESHOLD
from copilot.validation.walkforward import FoldResult
from copilot.validation.walkforward import FoldWindows
from copilot.validation.walkforward import evaluate_fold


if TYPE_CHECKING:
    from copilot.validation.holdout import CarvedHistory
    from copilot.validation.insample import ParameterGrid
    from copilot.validation.insample import Replay
    from copilot.validation.types import BacktestRunResult


@dataclass(frozen=True)
class HoldoutResult:
    """
    The one-time out-of-sample result, carrying the fold it was scored as.
    """

    fold: FoldResult
    development_bars: int
    holdout_bars: int
    purge_bars: int
    warmup_bars: int
    evidence: Evidence
    """
    How much the score is worth, which the score alone cannot say.
    """
    threshold: Decimal = Decimal(0)
    """
    The bar the score and its interval had to clear, predeclared by the activation.
    """

    @property
    def cleared_threshold(self) -> bool:
        """
        Whether the point estimate beat the bar.

        The whole of the old test, kept because it is still a necessary condition and
        because the record should say which of the two conditions a result failed.

        """
        return self.fold.passed

    @property
    def verdict(self) -> str:
        """
        ``pass``, ``insufficient_evidence`` or ``fail`` - the charter's three outcomes.

        Passing requires both a score above the bar **and** an interval that stays above
        it. A score above the bar whose interval straddles it is not a pass and is not a
        failure either: it is a measurement that did not resolve, and calling it either
        one throws away what was actually learned. [ADR-0024] records why the middle
        outcome exists.

        [ADR-0024]: ../docs/decisions/0024-a-holdout-pass-needs-an-interval.md

        """
        if not self.cleared_threshold:
            return "fail"
        if not self.evidence.clears(self.threshold):
            return "insufficient_evidence"
        return "pass"

    @property
    def passed(self) -> bool:
        """
        Whether the holdout was cleared outright.

        Narrower than it was before ADR-0024: this was ``score > 0``, which passed the
        AAPL next-close premise on +0.035 R over 111 trades at a third of a standard
        error from zero. It now also requires the interval to clear the bar.

        """
        return self.verdict == "pass"

    @property
    def score(self) -> Decimal:
        """
        Net expectancy per trade in R over the holdout window.
        """
        return self.fold.test_score

    @property
    def trades(self) -> int:
        """
        Trades whose signal fell inside the holdout window.
        """
        return self.fold.test_trades

    @property
    def frozen_parameters(self) -> dict[str, object] | None:
        """
        The parameter set selected on the full development window, or None.
        """
        if self.fold.selected is None:
            return None
        return dict(self.fold.selected.parameters)

    @property
    def tearsheet(self) -> Tearsheet:
        """
        The holdout trades' shape: win rate, profit factor, drawdown, and the rest.
        """
        return self.fold.tearsheet


def spend_holdout(
    carved: CarvedHistory,
    grid: ParameterGrid,
    *,
    purge_bars: int,
    warmup_bars: int,
    replay: Replay,
    objective: Callable[[BacktestRunResult], Decimal] = expectancy,
    min_trades: int = DEFAULT_MIN_TRADES,
    cliff_drop: Decimal = DEFAULT_CLIFF_DROP,
    fold_min_trades: int = 1,
    threshold: Decimal = DEFAULT_FOLD_PASS_THRESHOLD,
) -> HoldoutResult:
    """
    Select on the whole development window, then score the holdout once.

    The windows mirror a walk-forward fold exactly: the training slice is the
    development window minus its last ``purge_bars``, the purge gap is those bars, the
    test slice is the holdout, and warm-up is drawn from the bars immediately preceding
    it. The holdout bars reach the replay exactly once, for scoring, after selection is
    complete.

    ``min_trades`` and ``cliff_drop`` are the activation's, unchanged: the training
    window is far longer than a fold's, so the eligibility floor is cleared by more
    evidence rather than by a looser rule.

    """
    if purge_bars < 0:
        raise ValueError("purge_bars must not be negative")
    if warmup_bars < 0:
        raise ValueError("warmup_bars must not be negative")

    development = carved.development
    holdout = carved.holdout
    if len(development) - purge_bars <= 0:
        raise ValueError(
            f"purge of {purge_bars} bars leaves no development window to select on "
            f"({len(development)} development bars)",
        )

    ordered = (*development, *holdout)
    fold = FoldWindows(
        index=0,
        train_start=0,
        train_end=len(development) - purge_bars,
        purge_end=len(development),
        test_end=len(ordered),
    )
    result = evaluate_fold(
        ordered,
        fold,
        grid,
        replay=replay,
        objective=objective,
        min_trades=min_trades,
        cliff_drop=cliff_drop,
        warmup_bars=warmup_bars,
        fold_min_trades=fold_min_trades,
        threshold=threshold,
    )
    return HoldoutResult(
        fold=result,
        development_bars=len(development),
        holdout_bars=len(holdout),
        purge_bars=purge_bars,
        warmup_bars=warmup_bars,
        evidence=assess(result.test_trade_details),
        threshold=threshold,
    )


__all__ = [
    "HoldoutResult",
    "spend_holdout",
]
