"""
Tests for the account-size sweep.

The sweep exists because cost in R is not scale-free, and the two halves of that fact
pull in opposite directions: spread cost per R is identical at every size because
quantity cancels, while commission per R rises without limit as size falls because
IB's per-order minimum does not. A test suite that only checked totals would not
notice if those two were swapped.

The other failure worth guarding is quieter. A trade too small to size is not taken, so
it must leave the gross as well as the cost. Dropping it from cost alone flatters a
small account; dropping it from gross alone punishes one.

"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from decimal import Decimal

from copilot.calibration.account_sweep import Priced
from copilot.calibration.account_sweep import crossing_equity
from copilot.calibration.account_sweep import reprice
from copilot.calibration.account_sweep import sweep
from copilot.calibration.cost_model import FIXED
from copilot.calibration.cost_model import SCHEDULE
from copilot.calibration.cost_model import TIERED


@dataclass(frozen=True)
class FakeTrade:
    """
    The fields the sweep reads off a scored trade.
    """

    quantity: int
    entry_price: Decimal
    risk_amount: Decimal
    r_multiple: Decimal
    opened_at: datetime = datetime(2024, 1, 3, tzinfo=UTC)


def trade(distance: str, price: str = "100", r: str = "0.05", quantity: int = 100) -> FakeTrade:
    """
    Build a trade whose stop sits ``distance`` from a ``price`` entry.
    """
    return FakeTrade(
        quantity=quantity,
        entry_price=Decimal(price),
        risk_amount=Decimal(distance) * quantity,
        r_multiple=Decimal(r),
    )


def test_spread_cost_in_r_does_not_move_with_account_size() -> None:
    """
    Quantity cancels out of the spread term entirely.

    If this ever fails, the sweep is measuring something other than what ADR-0009 says
    it is.

    """
    trades = [trade("1.00")]

    small = reprice(trades, "SPY", Decimal(20), Decimal(2))
    large = reprice(trades, "SPY", Decimal(10_000), Decimal(2))

    assert small.spread_r == large.spread_r


def test_commission_in_r_falls_as_the_account_grows() -> None:
    """
    The per-order minimum is fixed in dollars, so its weight in R is set by how many
    dollars are at risk. This is the whole mechanism the sweep exists to expose, and the
    two ends of it are different schedules rather than one scaled.

    At a USD 20 budget the position is 20 shares, per-share comes to 10 cents, and the
    USD 1.00 minimum binds: a round trip costs USD 2 against USD 20 at risk, 0.10 R. At
    USD 10,000 it is 10,000 shares, per-share comes to USD 50, the minimum is
    irrelevant, and the same round trip is 0.01 R.

    """
    trades = [trade("1.00")]

    small = reprice(trades, "SPY", Decimal(20), Decimal(2))
    large = reprice(trades, "SPY", Decimal(10_000), Decimal(2))

    assert small.commission_r == Decimal("0.10")
    assert large.commission_r == Decimal("0.01")
    assert small.commission_r == large.commission_r * 10


def test_a_trade_too_small_to_size_leaves_both_the_cost_and_the_gross() -> None:
    """
    One share risks $50 here, so a $20 budget cannot take it at all.

    Counting it as a zero-return trade would understate the edge; charging its cost
    would overstate it.

    """
    trades = [trade("50.00", r="1.00"), trade("1.00", r="0.05")]

    priced = reprice(trades, "SPY", Decimal(20), Decimal(2))

    assert priced.trades_taken == 1
    assert priced.trades_unsized == 1
    assert priced.gross_r == Decimal("0.05")


def test_a_budget_that_sizes_nothing_reports_zero_rather_than_dividing_by_it() -> None:
    priced = reprice([trade("50.00")], "SPY", Decimal(1), Decimal(2))

    assert priced.trades_taken == 0
    assert priced.trades_unsized == 1
    assert priced.net_r == 0
    assert not priced.viable


def test_gross_is_the_mean_of_the_trades_actually_taken() -> None:
    trades = [trade("1.00", r="0.10"), trade("1.00", r="0.20")]

    priced = reprice(trades, "SPY", Decimal(10_000), Decimal(0))

    assert priced.gross_r == Decimal("0.15")


def test_net_subtracts_both_costs() -> None:
    priced = Priced(
        risk_budget=Decimal(100),
        trades_taken=10,
        trades_unsized=0,
        gross_r=Decimal("0.0500"),
        spread_r=Decimal("0.0120"),
        commission_r=Decimal("0.0300"),
    )

    assert priced.net_r == Decimal("0.0080")
    assert priced.viable


def test_the_crossing_is_the_lowest_viable_equity() -> None:
    def priced(net: str) -> Priced:
        return Priced(Decimal(1), 10, 0, Decimal(net), Decimal(0), Decimal(0))

    rows = [
        (Decimal(50_000), priced("0.01")),
        (Decimal(10_000), priced("-0.02")),
        (Decimal(25_000), priced("0.005")),
    ]

    assert crossing_equity(rows) == Decimal(25_000)


def test_a_premise_viable_nowhere_reports_nothing_rather_than_the_top_of_the_range() -> None:
    rows = [
        (Decimal(e), Priced(Decimal(1), 10, 0, Decimal("-0.01"), Decimal(0), Decimal(0)))
        for e in (10_000, 50_000)
    ]

    assert crossing_equity(rows) is None


def test_the_default_plan_is_the_pinned_one() -> None:
    """
    A sweep that names no plan prices what every verdict is charged at.
    """
    trades = [trade("1.00")]

    assert reprice(trades, "SPY", Decimal(20), Decimal(2)) == reprice(
        trades,
        "SPY",
        Decimal(20),
        Decimal(2),
        SCHEDULE,
    )


def test_tiered_is_cheaper_where_the_minimum_binds() -> None:
    """
    Twenty shares at USD 100: Fixed pays its USD 1.00 minimum, Tiered its USD 0.35 plus
    0.0032 a share passed through, 0.414 a side.

    0.828 against USD 20 at risk is 0.0414 R, against Fixed's 0.10 R.

    """
    trades = [trade("1.00")]

    fixed = reprice(trades, "SPY", Decimal(20), Decimal(2), FIXED)
    tiered = reprice(trades, "SPY", Decimal(20), Decimal(2), TIERED)

    assert fixed.commission_r == Decimal("0.10")
    assert tiered.commission_r == Decimal("0.0414")
    assert fixed.spread_r == tiered.spread_r
    assert fixed.gross_r == tiered.gross_r


def test_fixed_is_cheaper_once_the_per_share_rate_binds() -> None:
    """
    The crossover ADR-0025 measured, from the other side.

    Ten thousand shares: Fixed's flat 0.005 is USD 50 a side, while Tiered's 0.0035 plus
    0.0032 passed through is USD 67. A plan comparison that only looked at small accounts
    would call Tiered cheaper everywhere.

    """
    trades = [trade("1.00")]

    fixed = reprice(trades, "SPY", Decimal(10_000), Decimal(2), FIXED)
    tiered = reprice(trades, "SPY", Decimal(10_000), Decimal(2), TIERED)

    assert fixed.commission_r == Decimal("0.01")
    assert tiered.commission_r == Decimal("0.0134")


def test_the_plan_reaches_every_row_of_a_sweep() -> None:
    trades = [trade("1.00")]

    rows = sweep(
        trades,
        "SPY",
        Decimal(2),
        Decimal("0.0010"),
        equities=(Decimal(20_000),),
        schedule=TIERED,
    )

    assert rows[0][1] == reprice(trades, "SPY", Decimal(20), Decimal(2), TIERED)


def test_zero_is_not_viable() -> None:
    """
    Breaking even is not paying for itself, and the boundary is where a premise is most
    likely to sit.
    """
    exactly_zero = Priced(Decimal(1), 10, 0, Decimal("0.01"), Decimal("0.01"), Decimal(0))

    assert exactly_zero.net_r == 0
    assert not exactly_zero.viable
