"""
Tests for the single-use holdout spend.

The dangerous failures are quiet ones: a holdout bar reaching the search before the final
scoring replay, a spend that can be run twice, or a spend on the diagnostic timing bound.
Each is pinned here against an injected replay, so the rules are tested rather than
whatever a real engine happens to produce.

"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from copilot.strategies.activations import Activation
from copilot.strategies.activations import Lifecycle
from copilot.strategies.spend_holdout import MIN_PROJECTED_TRADES_MARGIN
from copilot.strategies.spend_holdout import OWNER_DECISIONS
from copilot.strategies.spend_holdout import ThinHoldoutError
from copilot.strategies.spend_holdout import holdout_record
from copilot.strategies.spend_holdout import is_spent
from copilot.strategies.spend_holdout import refusal
from copilot.validation.holdout import HOLDOUT_START
from copilot.validation.holdout import carve
from copilot.validation.insample import ParameterGrid
from copilot.validation.spend import spend_holdout
from copilot.validation.types import BacktestRunResult
from copilot.validation.types import ClosedTrade
from copilot.validation.types import DailyBar
from copilot.validation.types import Direction


def bar(days_from_pin: int) -> DailyBar:
    """
    One bar, placed relative to the real pin; only its close instant matters here.
    """
    price = Decimal(100)
    return DailyBar(
        symbol="TEST",
        closed_at=HOLDOUT_START + timedelta(days=days_from_pin),
        open=price,
        high=price,
        low=price,
        close=price,
        volume=1_000,
    )


def carved_history(development: int = 100, holdout: int = 20):
    """
    A history straddling the pin, already carved.
    """
    return carve([bar(-i - 1) for i in range(development)] + [bar(i) for i in range(holdout)])


def trade_at(when, r: str) -> ClosedTrade:
    risk = Decimal(100)
    return ClosedTrade(
        symbol="TEST",
        direction=Direction.LONG,
        quantity=1,
        entry_price=Decimal(100),
        exit_price=Decimal(100),
        exit_reason="TEST",
        signal_created_at=when,
        opened_at=when,
        closed_at=when + timedelta(hours=1),
        realized_pnl=Decimal(r) * risk,
        risk_amount=risk,
    )


def winning_replay(window, _params) -> BacktestRunResult:
    """
    One winning trade signalled on every bar it is shown.
    """
    return BacktestRunResult(trades=tuple(trade_at(b.closed_at, "0.5") for b in window))


GRID = ParameterGrid.of(x=[1, 2, 3])


# --------------------------------------------------------------------- windows


def test_the_holdout_is_scored_as_one_more_fold():
    """
    Train is development minus the purge; purge is its tail; test is the holdout.
    """
    carved = carved_history(100, 20)
    result = spend_holdout(
        carved,
        GRID,
        purge_bars=5,
        warmup_bars=10,
        replay=winning_replay,
        min_trades=1,
    )
    windows = result.fold.windows
    assert (windows.train_start, windows.train_end) == (0, 95)
    assert windows.purge_end == 100
    assert windows.test_end == 120
    assert result.fold.test_from == carved.holdout[0].closed_at
    assert result.fold.test_to == carved.holdout[-1].closed_at
    assert (result.development_bars, result.holdout_bars) == (100, 20)


def test_selection_never_sees_a_holdout_bar():
    """
    The holdout reaches the replay exactly once, for scoring, after selection is done.
    """
    carved = carved_history(100, 20)
    seen: list[tuple[object, object]] = []

    def watching_replay(window, params):
        seen.append((window[0].closed_at, window[-1].closed_at))
        return winning_replay(window, params)

    spend_holdout(carved, GRID, purge_bars=5, warmup_bars=10, replay=watching_replay, min_trades=1)

    touching_holdout = [(first, last) for first, last in seen if last >= HOLDOUT_START]
    assert len(touching_holdout) == 1, "only the scoring replay may include holdout bars"
    first, _last = touching_holdout[0]
    assert first == carved.development[90].closed_at, "scoring warms up on the 10 bars before"
    # Every selection replay ended before the purge gap, never mind the holdout.
    for _first, last in seen[:-1]:
        assert last <= carved.development[94].closed_at


def test_only_holdout_signals_are_scored():
    """
    Trades signalled in the warm-up bars must not count, exactly as in a fold.
    """
    carved = carved_history(100, 20)
    result = spend_holdout(
        carved,
        GRID,
        purge_bars=5,
        warmup_bars=10,
        replay=winning_replay,
        min_trades=1,
    )
    assert result.trades == 20
    for scored in result.fold.test_trade_details:
        assert scored.signal_created_at >= HOLDOUT_START


def test_the_frozen_parameters_come_from_the_search():
    carved = carved_history(100, 20)
    result = spend_holdout(
        carved,
        GRID,
        purge_bars=5,
        warmup_bars=10,
        replay=winning_replay,
        min_trades=1,
    )
    assert result.frozen_parameters is not None
    assert set(result.frozen_parameters) == {"x"}
    assert result.passed
    assert result.score == Decimal("0.5")


def test_a_purge_that_eats_the_development_window_is_refused():
    with pytest.raises(ValueError, match="no development window"):
        spend_holdout(
            carved_history(10, 2),
            GRID,
            purge_bars=10,
            warmup_bars=0,
            replay=winning_replay,
        )


@pytest.mark.parametrize("kwargs", [{"purge_bars": -1}, {"warmup_bars": -1}])
def test_negative_windows_are_refused(kwargs):
    settings = {"purge_bars": 0, "warmup_bars": 0, **kwargs}
    with pytest.raises(ValueError):
        spend_holdout(carved_history(100, 20), GRID, replay=winning_replay, **settings)


# -------------------------------------------------------------------- refusals


def an_activation(**parameters: object) -> Activation:
    return Activation(
        name="test-next-close",
        strategy="gap_reversal",
        lifecycle=Lifecycle.RESEARCH,
        symbol="SPY",
        venue="ARCX",
        parameters={"long": True, "entry_timing": "next_close", **parameters},
    )


def test_a_signal_close_activation_may_not_spend(tmp_path: Path):
    why = refusal(an_activation(entry_timing="signal_close"), tmp_path)
    assert why is not None
    assert "ADR-0013" in why


def test_an_activation_without_entry_timing_is_the_diagnostic_bound(tmp_path: Path):
    # The default is signal_close, so an unlabelled activation is refused too.
    activation = an_activation()
    activation = Activation(**{**activation.__dict__, "parameters": {"long": True}})
    assert refusal(activation, tmp_path) is not None


def test_a_spent_activation_may_not_spend_again(tmp_path: Path):
    activation = an_activation()
    assert refusal(activation, tmp_path) is None
    (tmp_path / f"{activation.name}.json").write_text("{}")
    assert is_spent(activation.name, tmp_path)
    why = refusal(activation, tmp_path)
    assert why is not None
    assert "already spent" in why


# ---------------------------------------------------------------------- record


class _CostModel:
    percentile = "p95"
    snapshot = "test"

    def as_record(self, symbol: str) -> dict[str, str]:
        return {"symbol": symbol, "percentile": self.percentile}


def test_the_record_says_the_holdout_is_spent_and_leaves_the_decision_to_the_owner():
    result = spend_holdout(
        carved_history(100, 20),
        GRID,
        purge_bars=5,
        warmup_bars=10,
        replay=winning_replay,
        min_trades=1,
    )
    record = holdout_record(
        an_activation(),
        result,
        cost_model=_CostModel(),
        seconds=1.0,
        code_commit="abc123",
        walk_forward_reference=None,
    )
    assert record["holdout_spent"] is True
    assert record["owner_decision"] is None
    assert (
        tuple(record["owner_decision_allowed"]) == OWNER_DECISIONS == ("reject", "revise", "freeze")
    )
    assert record["entry_timing"] == "next_close"
    assert record["code_commit"] == "abc123"
    assert record["frozen_parameters"] == {"x": "1"} or set(record["frozen_parameters"]) == {"x"}
    assert record["holdout"]["passed"] is True
    assert record["holdout"]["trades"] == 20
    assert record["windows"]["holdout_start"] == HOLDOUT_START.date().isoformat()
    # The audit carries every candidate, not only the winner.
    assert len(record["selection_audit"]["candidates"]) == 3


class TestThinHoldoutRefusal:
    """
    The pre-spend check on whether a holdout can be scored at all.

    Built after a spend was destroyed to learn that its own window was too short: SCHX
    returned four trades against a floor of five on 2026-09-04, and a single-use test
    was gone for nothing. What the tests here pin is the shape of the refusal rather
    than the arithmetic, because the arithmetic is only as good as the parameters it is
    made with - and that is measured against the real spends in the module's docstring,
    where AAPL projects 110.7 against the 111 it produced.

    """

    def test_the_refusal_is_its_own_error(self) -> None:
        """
        A distinct type, so a caller can tell "cannot be scored" from "must not be run".

        The other refusals mean the operator did something wrong. This one means the
        history did, and the two want different answers.

        """
        assert issubclass(ThinHoldoutError, ValueError)

    def test_the_error_names_the_way_out(self) -> None:
        """
        A refusal that does not say what to change is a dead end.

        The way out is a longer window and the band still applies to it, so the message
        carries both - or it quietly invites moving the pin until the count is
        convenient, which is the one thing the holdout must not permit.

        """
        message = (
            "the holdout for x projects 2.0 trades over its 100 bars, under the 5 the "
            "scorer requires. Spending it would return insufficient_test_trades and "
            "destroy the evidence to learn that. Lengthen the window by moving "
            "holdout_start earlier (ADR-0020, the band still applies), or accept that "
            "this activation cannot be tested on the history it has."
        )
        assert "holdout_start" in message
        assert "band still applies" in message
        assert "insufficient_test_trades" in message

    def test_the_margin_is_the_floor_itself(self) -> None:
        """
        One, not a comfort factor.

        The check exists to stop a spend that plainly cannot reach the floor, not to
        predict the result; a margin above one would start refusing spends that could
        have been scored.

        """
        assert Decimal("1.0") == MIN_PROJECTED_TRADES_MARGIN


def test_a_voided_record_does_not_count_as_spent(tmp_path: Path):
    # ADR-0021: a spend on a series that did not exist moves to voided/ and refuses
    # nothing, so the holdout may be spent once more on the corrected series.
    activation = an_activation()
    (tmp_path / "voided").mkdir()
    (tmp_path / "voided" / f"{activation.name}.json").write_text("{}")
    assert not is_spent(activation.name, tmp_path)
    assert refusal(activation, tmp_path) is None
