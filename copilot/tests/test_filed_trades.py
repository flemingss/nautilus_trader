"""
What filing the scored trades has to get right for a later question to trust them.

The rows exist so attribution and the evidence interval can be computed from a record
instead of from a replay. That is only true if reading them back gives the trades that
were scored - not approximately, and not in some other order - and if the net series they
carry averages to the score the record reports.

"""

from __future__ import annotations

import json
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

from copilot.validation.evidence import MEAN_TOLERANCE_R
from copilot.validation.evidence import assess
from copilot.validation.filed_trades import from_rows
from copilot.validation.filed_trades import scored_trades
from copilot.validation.filed_trades import to_rows
from copilot.validation.insample import CandidateResult
from copilot.validation.insample import InSampleReport
from copilot.validation.types import ClosedTrade
from copilot.validation.types import Direction
from copilot.validation.walkforward import FoldResult
from copilot.validation.walkforward import FoldWindows


BASE = datetime(2019, 3, 4, 20, 0, tzinfo=UTC)

CHOSEN = CandidateResult(
    parameters={},
    coordinate={},
    version="v",
    trades=0,
    score=Decimal(0),
    net=Decimal(0),
    wins=0,
)


def trade(symbol: str, day: int, pnl: str, *, risk: str = "333.33") -> ClosedTrade:
    when = BASE + timedelta(days=day)
    return ClosedTrade(
        symbol=symbol,
        direction=Direction.SHORT if day % 3 == 0 else Direction.LONG,
        quantity=17,
        entry_price=Decimal("187.4312"),
        exit_price=Decimal("188.0007"),
        exit_reason="next_close",
        signal_created_at=when,
        opened_at=when + timedelta(hours=1),
        closed_at=when + timedelta(days=1, hours=1),
        realized_pnl=Decimal(pnl),
        risk_amount=Decimal(risk),
    )


def fold(index: int, trades: tuple[ClosedTrade, ...], *, selected: bool = True) -> FoldResult:
    return FoldResult(
        index=index,
        windows=FoldWindows(index=index, train_start=0, train_end=1, purge_end=2, test_end=3),
        train_from=BASE,
        test_from=BASE,
        test_to=BASE + timedelta(days=400),
        in_sample=InSampleReport(
            candidates=(CHOSEN,) if selected else (),
            selected=CHOSEN if selected else None,
            plateau_scores={},
        ),
        selected=CHOSEN if selected else None,
        test_trades=len(trades),
        test_score=Decimal(0),
        passed=False,
        reason="synthetic",
        test_trade_details=trades,
    )


def awkward(trade: ClosedTrade) -> Decimal:
    """
    A cost with a non-terminating expansion, so the twelve-place filing actually rounds.
    """
    return Decimal(1) / Decimal(3) / trade.risk_amount * Decimal(100)


FOLDS = (
    fold(0, (trade("MSFT.XNAS", 1, "120.55"), trade("AAPL.XNAS", 1, "-333.33"))),
    fold(1, (trade("SPY.ARCX", 40, "15.01"), trade("AAPL.XNAS", 39, "402.10"))),
)


def test_reading_the_rows_back_gives_the_trades_that_were_scored() -> None:
    """
    Through JSON, because that is the only form a later reader ever sees.
    """
    rows = json.loads(json.dumps(to_rows(FOLDS, cost_r=awkward)))

    filed = from_rows(rows)

    assert [f.trade for f in filed] == [t for _, t in scored_trades(FOLDS)]
    assert [f.fold for f in filed] == [0, 0, 1, 1]


def test_rows_are_in_signal_order_across_folds_and_symbols() -> None:
    """
    A bootstrap blocks over this order, so grouping by fold or symbol would be wrong.
    """
    rows = to_rows(reversed(FOLDS), cost_r=awkward)

    assert [(r["signal_created_at"][:10], r["symbol"]) for r in rows] == [
        ("2019-03-05", "AAPL.XNAS"),
        ("2019-03-05", "MSFT.XNAS"),
        ("2019-04-12", "AAPL.XNAS"),
        ("2019-04-13", "SPY.ARCX"),
    ]


def test_a_fold_that_selected_nothing_files_nothing() -> None:
    folds = (*FOLDS, fold(2, (trade("GLDM.ARCX", 90, "5"),), selected=False))

    assert len(to_rows(folds, cost_r=awkward)) == 4


def test_the_filed_net_series_averages_to_the_score() -> None:
    """
    Twelve places of cost is claimed to be exact at six places of mean.

    Checked here.

    """
    scored = [t for _, t in scored_trades(FOLDS)]
    exact = sum((t.r_multiple - awkward(t) for t in scored), Decimal(0)) / len(scored)

    filed = from_rows(to_rows(FOLDS, cost_r=awkward))
    recomputed = sum((f.net_r for f in filed), Decimal(0)) / len(filed)

    assert abs(recomputed - exact) < MEAN_TOLERANCE_R / 1000


def test_the_interval_can_be_recomputed_from_the_record_alone() -> None:
    """
    The claim that unblocks everything downstream: no replay, no cost snapshot.

    The charge is read off the rows rather than recomputed, because the snapshot that
    priced a filed run may since have been superseded.

    """
    scored = [t for _, t in scored_trades(FOLDS)]
    original = assess(scored, cost_r=awkward)

    filed = from_rows(json.loads(json.dumps(to_rows(FOLDS, cost_r=awkward))))
    charged = {f.trade: f.cost_r for f in filed}
    recomputed = assess([f.trade for f in filed], cost_r=charged.__getitem__)

    assert recomputed == original


def test_the_display_column_is_net_of_the_filed_cost() -> None:
    row = to_rows(FOLDS, cost_r=awkward)[0]

    gross = Decimal(row["realized_pnl"]) / Decimal(row["risk_amount"])
    assert Decimal(row["net_r"]) == (gross - Decimal(row["cost_r"])).quantize(Decimal("0.000001"))


def test_identical_trades_are_filed_and_read_back_as_two() -> None:
    """
    Audit F31: nothing tested a duplicate row, and a mapping keyed by trade would collapse it.
    """
    twin = trade("SPY.ARCX", 3, "50")

    rows = to_rows([fold(0, (twin, twin))], cost_r=lambda _: Decimal("0.01"))

    assert len(rows) == 2
    assert [f.trade for f in from_rows(rows)] == [twin, twin]
