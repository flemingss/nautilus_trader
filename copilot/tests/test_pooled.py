"""
What pooling has to get right before a pooled number means anything.

Pooling is a claim that nine symbols were scored as one experiment. The ways that claim
can be false are quiet ones, and each is pinned here:

- **The pool is one premise.** Averaging trades made under different rules produces a
  number that describes nothing, so activations that differ are refused rather than
  averaged.
- **Each trade is charged its own symbol's costs.** Charging one symbol's spread to all of
  them subsidises the wide names with the narrow ones, which is the direction that
  flatters the result.
- **A late-starting symbol shortens its own contribution, not the experiment.** The union
  spine is what makes that work, and an intersection would silently discard history.
- **The merged trade sequence is in signal order.** The evidence bootstrap blocks over this
  sequence, so trades grouped by symbol would measure the wrong dependence entirely.

"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

import pytest

from copilot.validation.pooled import SPINE_SYMBOL
from copilot.validation.pooled import PooledMember
from copilot.validation.pooled import contribution
from copilot.validation.pooled import pooled_objective
from copilot.validation.pooled import pooled_replay
from copilot.validation.pooled import spine
from copilot.validation.types import BacktestRunResult
from copilot.validation.types import ClosedTrade
from copilot.validation.types import DailyBar
from copilot.validation.types import Direction


BASE = datetime(2020, 1, 2, tzinfo=UTC)


def bar(symbol: str, day: int) -> DailyBar:
    price = Decimal(100)
    return DailyBar(
        symbol=symbol,
        closed_at=BASE + timedelta(days=day),
        open=price,
        high=price,
        low=price,
        close=price,
        volume=1_000,
    )


def trade(symbol: str, day: int, r: str) -> ClosedTrade:
    when = BASE + timedelta(days=day)
    risk = Decimal(100)
    return ClosedTrade(
        symbol=symbol,
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


def member(symbol: str, days: range, r: str = "0.5") -> PooledMember:
    """
    A member that trades once on every bar it is shown, at a fixed R.
    """

    def replay(bars, _params) -> BacktestRunResult:
        return BacktestRunResult(
            trades=tuple(trade(symbol, (b.closed_at - BASE).days, r) for b in bars),
        )

    return PooledMember(symbol=symbol, bars=tuple(bar(symbol, d) for d in days), replay=replay)


# --------------------------------------------------------------------------- the spine


def test_the_spine_is_the_union_of_every_members_dates() -> None:
    """
    Union, not intersection: a late start shortens one member, not the experiment.
    """
    axis = spine([member("EARLY", range(10)), member("LATE", range(5, 15))])

    assert len(axis) == 15
    assert axis[0].closed_at == BASE
    assert axis[-1].closed_at == BASE + timedelta(days=14)


def test_the_spine_is_sorted_and_has_no_duplicate_dates() -> None:
    axis = spine([member("A", range(10)), member("B", range(10))])

    assert len(axis) == 10
    assert [b.closed_at for b in axis] == sorted(b.closed_at for b in axis)


def test_spine_bars_are_obviously_not_an_instrument() -> None:
    """
    Only ``closed_at`` is ever read, so the rest must not be mistakable for a price.
    """
    axis = spine([member("A", range(3))])

    assert {b.symbol for b in axis} == {SPINE_SYMBOL}
    assert all(b.close == 0 and b.volume == 0 for b in axis)


# -------------------------------------------------------------------------- the replay


def test_every_member_contributes_trades_inside_the_window() -> None:
    members = [member("A", range(10)), member("B", range(10))]
    axis = spine(members)

    result = pooled_replay(members)(axis, None)

    assert contribution(result) == {"A": 10, "B": 10}
    assert len(result.trades) == 20


def test_a_member_with_no_bars_in_the_window_contributes_nothing() -> None:
    """
    The late-start case, which needs no special handling if the slice is honest.
    """
    members = [member("EARLY", range(5)), member("LATE", range(10, 15))]
    axis = spine(members)

    early_only = pooled_replay(members)(axis[:5], None)

    assert contribution(early_only) == {"EARLY": 5}
    assert "LATE" not in contribution(early_only)


def test_the_merged_trades_are_in_signal_order_not_symbol_order() -> None:
    """
    The evidence bootstrap blocks over this sequence.

    Grouped by symbol, a block would span one instrument's whole history and the
    dependence measured would be the wrong dependence.

    """
    members = [member("Z", range(5)), member("A", range(5))]

    result = pooled_replay(members)(spine(members), None)
    instants = [t.signal_created_at for t in result.trades]

    assert instants == sorted(instants)
    assert [t.symbol for t in result.trades[:2]] == ["A", "Z"], "same instant, then by name"


def test_an_empty_window_replays_nothing() -> None:
    assert pooled_replay([member("A", range(5))])((), None).trades == ()


# ----------------------------------------------------------------------- the objective


def test_each_trade_is_charged_its_own_symbols_cost() -> None:
    """
    The regression for subsidising wide spreads with narrow ones.

    ``CHEAP`` and ``DEAR`` each return 1.0 R gross. Charged their own costs the pooled
    mean is 0.5 R; charged either symbol's cost uniformly it would be 0.9 or 0.1.

    """
    costs = {"CHEAP": Decimal("0.1"), "DEAR": Decimal("0.9")}
    objective = pooled_objective(lambda _trade, symbol: costs[symbol])

    result = BacktestRunResult(trades=(trade("CHEAP", 0, "1.0"), trade("DEAR", 1, "1.0")))

    assert objective(result) == Decimal("0.5")


def test_an_empty_result_scores_zero_rather_than_dividing_by_nothing() -> None:
    objective = pooled_objective(lambda _t, _s: Decimal(0))

    assert objective(BacktestRunResult()) == Decimal(0)


def test_the_pooled_mean_weights_every_trade_equally() -> None:
    """
    Per trade, not per symbol.

    A symbol that trades ten times as often carries ten times the weight, which is what
    *one experiment over a universe* means. Equal-weighting the symbols instead would be
    a portfolio question, and this is not one.

    """
    objective = pooled_objective(lambda _t, _s: Decimal(0))
    trades = (*(trade("BUSY", d, "1.0") for d in range(9)), trade("QUIET", 9, "0.0"))

    assert objective(BacktestRunResult(trades=trades)) == Decimal("0.9")


def test_contribution_is_empty_for_a_result_that_was_not_pooled() -> None:
    assert contribution(BacktestRunResult()) == {}


# ------------------------------------------------------------------------- coherence


def test_a_pool_of_one_is_refused() -> None:
    from copilot.strategies.activations import load_activations
    from copilot.strategies.pool import IncoherentPoolError
    from copilot.strategies.pool import coherent

    one = [a for a in load_activations() if a.name == "aapl-gap-fade-long-next-close"]

    with pytest.raises(IncoherentPoolError, match="at least 2"):
        coherent(one)


def test_activations_of_different_timing_modes_are_refused() -> None:
    """
    ADR-0013 makes the two timing modes different experiments, not two runs of one.
    """
    from copilot.strategies.activations import load_activations
    from copilot.strategies.pool import IncoherentPoolError
    from copilot.strategies.pool import coherent

    mixed = [
        a
        for a in load_activations()
        if a.name in {"aapl-gap-fade-long-next-close", "aapl-gap-fade-long"}
    ]

    with pytest.raises(IncoherentPoolError, match="not one premise"):
        coherent(mixed)


def test_the_registered_next_close_family_is_one_premise() -> None:
    """
    The pool this project actually runs has to be poolable, or the check is untested.
    """
    from copilot.strategies.activations import load_activations
    from copilot.strategies.pool import coherent

    family = [
        a for a in load_activations() if str(a.parameters.get("entry_timing", "")) == "next_close"
    ]
    pooled = coherent(family)

    assert len(pooled) >= 9
    assert [a.symbol for a in pooled] == sorted(a.symbol for a in pooled)


def test_the_cost_model_is_keyed_on_the_bare_symbol_a_trade_records_a_full_id() -> None:
    """
    The regression for the pooled run's first failure.

    A replayed trade records ``MSFT.XNAS``; the spread snapshot is keyed on ``MSFT``. The
    single-symbol objective never met this because it was handed the activation's symbol
    directly, so the first pooled run raised ``UncalibratedSymbolError`` for every name.

    """
    from copilot.validation.pooled import calibrated_symbol

    assert calibrated_symbol("MSFT.XNAS") == "MSFT"
    assert calibrated_symbol("SPY.ARCX") == "SPY"
    assert calibrated_symbol("AAPL") == "AAPL", "already bare, and must stay so"


def test_the_objective_reaches_the_cost_model_with_a_symbol_it_knows() -> None:
    from copilot.calibration.cost_model import CostModel
    from copilot.validation.pooled import pooled_objective

    objective = pooled_objective(CostModel.from_snapshot().cost_r)
    result = BacktestRunResult(trades=(trade("MSFT.XNAS", 0, "1.0"),))

    scored = objective(result)

    assert 0 < scored < Decimal("1.0"), "gross 1.0 R, less a real modelled cost"


# ------------------------------------------------------------------------ the record


def synthetic_verdict(trades_by_symbol: dict[str, int]):
    """
    A PooledVerdict over a hand-built report, with no catalog and no replay.
    """
    from copilot.calibration.cost_model import CostModel
    from copilot.strategies.activations import load_activations
    from copilot.strategies.pool import PooledVerdict
    from copilot.validation.insample import CandidateResult
    from copilot.validation.insample import InSampleReport
    from copilot.validation.walkforward import FoldResult
    from copilot.validation.walkforward import FoldWindows
    from copilot.validation.walkforward import WalkForwardReport

    scored = tuple(
        trade(symbol, day, "0.5")
        for symbol, count in trades_by_symbol.items()
        for day in range(count)
    )
    windows = FoldWindows(index=0, train_start=0, train_end=10, purge_end=12, test_end=20)
    # Selected, because a fold that selected nothing scores no trades: ``evaluate_fold``
    # returns before replaying the test window. An earlier version of this fixture put
    # trades in an unselected fold, which no run can produce.
    chosen = CandidateResult(
        parameters={},
        coordinate={},
        version="synthetic",
        trades=len(scored),
        score=Decimal("0.5"),
        net=Decimal(0),
        wins=len(scored),
    )
    fold = FoldResult(
        index=0,
        windows=windows,
        train_from=BASE,
        test_from=BASE,
        test_to=BASE + timedelta(days=100),
        in_sample=InSampleReport(candidates=(chosen,), selected=chosen, plateau_scores={}),
        selected=chosen,
        test_trades=len(scored),
        test_score=Decimal("0.5"),
        passed=True,
        reason="synthetic",
        test_trade_details=scored,
    )
    members = tuple(
        a for a in load_activations() if str(a.parameters.get("entry_timing", "")) == "next_close"
    )[:2]
    return PooledVerdict(
        members=members,
        report=WalkForwardReport(folds=(fold,), threshold=Decimal(0)),
        spine_bars=100,
        first_bar="2020-01-02",
        last_bar="2021-12-31",
        seconds=1.0,
        cost_model=CostModel.from_snapshot(),
    )


def test_the_record_builds_without_running_anything() -> None:
    """
    The regression for a ten-minute run that died on the last line.

    ``as_record`` reached for ``mean_test_score``, which the report does not have, and
    the whole pooled walk-forward was thrown away to learn it. Every field it reads is
    now exercised without a catalog, a replay or a minute of compute.

    """
    record = synthetic_verdict({"AAPL.XNAS": 6, "SPY.ARCX": 4}).as_record()

    assert record["trades"] == 10
    assert record["trades_by_symbol"] == {"AAPL.XNAS": 6, "SPY.ARCX": 4}
    assert record["folds"] == 1
    assert record["folds_evaluated"] == 1
    assert record["symbols_per_fold"] == [2]
    assert len(record["trade_rows"]) == 10
    assert Decimal(record["mean_of_fold_scores_r"]) == Decimal("0.5"), "the mean of the fold scores"
    assert record["mean_per_trade_net_r"] == record["evidence"]["mean_r"], "the interval's centre"
    assert record["evidence"]["clears_zero"] is True
    assert "not spent" in record["holdout"]


def test_the_record_reports_which_symbols_actually_traded() -> None:
    """
    ``pooled`` is a claim to check, not a label.

    A pool whose trades all come from one name is a single-symbol experiment with extra
    machinery, and the record has to make that visible.

    """
    record = synthetic_verdict({"AAPL.XNAS": 30, "SPY.ARCX": 0}).as_record()

    assert record["trades_by_symbol"] == {"AAPL.XNAS": 30}
    assert "SPY.ARCX" not in record["trades_by_symbol"]


def test_constant_membership_clips_every_member_to_the_window_all_of_them_cover() -> None:
    """
    Audit F23: the first pool was three symbols for 22 folds and nine for three.
    """
    from copilot.strategies.pool import clip_to_common_window

    early = member("AAPL", range(100))
    late = member("SCHX", range(40, 140))

    clipped = clip_to_common_window([early, late])

    assert {m.symbol: (m.bars[0].closed_at, m.bars[-1].closed_at) for m in clipped} == {
        "AAPL": (bar("AAPL", 40).closed_at, bar("AAPL", 99).closed_at),
        "SCHX": (bar("SCHX", 40).closed_at, bar("SCHX", 99).closed_at),
    }


def test_members_with_no_common_window_are_refused() -> None:
    from copilot.strategies.pool import IncoherentPoolError
    from copilot.strategies.pool import clip_to_common_window

    with pytest.raises(IncoherentPoolError, match="share no window"):
        clip_to_common_window([member("AAPL", range(10)), member("SCHX", range(20, 30))])
