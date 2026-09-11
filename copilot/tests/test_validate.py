"""
Tests for what a `validate` invocation means, which the CLI had never had.
"""

from __future__ import annotations

from decimal import Decimal

from copilot.strategies.activations import load_activations
from copilot.strategies.validate import selected_activations


def test_changed_alone_means_every_activation() -> None:
    """
    The morning command, with no second flag whose absence fails after the step felt
    done.
    """
    assert len(selected_activations(None, all_=False, changed=True)) == len(load_activations())


def test_a_name_with_changed_means_that_one() -> None:
    chosen = selected_activations("aapl-gap-fade-long-next-close", all_=False, changed=True)
    assert [a.name for a in chosen] == ["aapl-gap-fade-long-next-close"]


def test_all_wins_over_a_name() -> None:
    assert len(
        selected_activations("aapl-gap-fade-long-next-close", all_=True, changed=False),
    ) == len(load_activations())


def test_nothing_named_is_nothing() -> None:
    """
    Empty rather than a default: the CLI turns this into an error the operator reads.
    """
    assert selected_activations(None, all_=False, changed=False) == ()


def test_the_verdict_record_files_its_trades_and_their_interval() -> None:
    """
    Built without a catalog or a replay, so a bad field name costs a second, not a run.

    The pooled record lost a ten-minute walk-forward to exactly that on 2026-09-10. The
    two new blocks are the ones attribution reads, so they are the ones checked.

    """
    record = synthetic_verdict().as_record()

    rows = record["trade_rows"]
    assert len(rows) == record["total_test_trades"] == 3
    assert {r["symbol"] for r in rows} == {"MSFT.XNAS"}
    assert record["evidence"]["series"] == "net R per trade, in signal order"
    assert record["evidence"]["trades"] == 3
    net = (
        sum(
            (Decimal(r["realized_pnl"]) / Decimal(r["risk_amount"]) - Decimal(r["cost_r"]))
            for r in rows
        )
        / 3
    )
    assert abs(Decimal(record["evidence"]["mean_r"]) - net) <= Decimal("0.000001")
    assert Decimal(record["evidence"]["mean_r"]) < Decimal("0.5"), "costs were charged"


def synthetic_verdict():
    from datetime import UTC
    from datetime import datetime
    from datetime import timedelta

    from copilot.calibration.cost_model import CostModel
    from copilot.strategies.fingerprint import Fingerprint
    from copilot.strategies.validate import Verdict
    from copilot.validation.insample import CandidateResult
    from copilot.validation.insample import InSampleReport
    from copilot.validation.types import ClosedTrade
    from copilot.validation.types import Direction
    from copilot.validation.walkforward import FoldResult
    from copilot.validation.walkforward import FoldWindows
    from copilot.validation.walkforward import WalkForwardReport

    base = datetime(2020, 6, 1, 20, 0, tzinfo=UTC)
    trades = tuple(
        ClosedTrade(
            symbol="MSFT.XNAS",
            direction=Direction.LONG,
            quantity=50,
            entry_price=Decimal(200),
            exit_price=Decimal(201),
            exit_reason="next_close",
            signal_created_at=base + timedelta(days=d),
            opened_at=base + timedelta(days=d, hours=20),
            closed_at=base + timedelta(days=d + 1, hours=20),
            realized_pnl=Decimal(50),
            risk_amount=Decimal(100),
        )
        for d in range(3)
    )
    chosen = CandidateResult(
        parameters={},
        coordinate={},
        version="v",
        trades=3,
        score=Decimal("0.5"),
        net=Decimal(150),
        wins=3,
    )
    fold = FoldResult(
        index=0,
        windows=FoldWindows(index=0, train_start=0, train_end=1, purge_end=2, test_end=3),
        train_from=base,
        test_from=base,
        test_to=base + timedelta(days=30),
        in_sample=InSampleReport(candidates=(chosen,), selected=chosen, plateau_scores={}),
        selected=chosen,
        test_trades=3,
        test_score=Decimal("0.5"),
        passed=True,
        reason="synthetic",
        test_trade_details=trades,
    )
    activation = next(a for a in load_activations() if a.name == "msft-gap-fade-long-next-close")
    return Verdict(
        activation=activation,
        report=WalkForwardReport(folds=(fold,), threshold=Decimal(0)),
        bars=100,
        first_bar="2020-01-02",
        last_bar="2020-12-31",
        holdout_bars=0,
        holdout_range=("", ""),
        unevaluated_bars=0,
        seconds=1.0,
        cost_model=CostModel.from_snapshot(),
        fingerprint=Fingerprint(data="d", identity="i", cost="c", code="k"),
    )
