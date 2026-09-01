"""Tests for risk-based position sizing.

The properties that matter are directional: never round up into more risk than the
budget allows, and refuse rather than guess when a stop cannot be honoured.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from copilot.risk.sizing import (
    position_size,
    risk_amount,
    size_from_levels,
    stop_distance,
)
from copilot.validation.types import Direction


class TestStopDistance:
    def test_long_measures_entry_down_to_stop(self):
        d = stop_distance(
            direction=Direction.LONG,
            entry_price=Decimal(100),
            stop_price=Decimal(95),
        )
        assert d == Decimal(5)

    def test_short_measures_stop_up_from_entry(self):
        d = stop_distance(
            direction=Direction.SHORT,
            entry_price=Decimal(100),
            stop_price=Decimal(105),
        )
        assert d == Decimal(5)

    @pytest.mark.parametrize(
        ("direction", "entry", "stop"),
        [
            # A long stop at or above entry would make a stop-out a profit.
            (Direction.LONG, "100", "100"),
            (Direction.LONG, "100", "105"),
            # And the mirror image for a short.
            (Direction.SHORT, "100", "100"),
            (Direction.SHORT, "100", "95"),
        ],
    )
    def test_stop_on_the_wrong_side_is_unsizable(self, direction, entry, stop):
        assert stop_distance(
            direction=direction,
            entry_price=Decimal(entry),
            stop_price=Decimal(stop),
        ) == Decimal(0)

    @pytest.mark.parametrize(("entry", "stop"), [("0", "95"), ("100", "0"), ("-1", "95")])
    def test_non_positive_prices_are_unsizable(self, entry, stop):
        assert stop_distance(
            direction=Direction.LONG,
            entry_price=Decimal(entry),
            stop_price=Decimal(stop),
        ) == Decimal(0)


class TestPositionSize:
    def test_sizes_so_a_stop_out_costs_the_budget(self):
        qty = position_size(risk_budget=Decimal(1000), distance=Decimal(5))
        assert qty == Decimal(200)

    def test_floors_rather_than_rounding_up(self):
        # 1000 / 3 = 333.33 -> 333, never 334: rounding up would risk more than the
        # budget allows, the one direction a risk control must not err in.
        qty = position_size(risk_budget=Decimal(1000), distance=Decimal(3))
        assert qty == Decimal(333)
        assert qty * Decimal(3) <= Decimal(1000)

    def test_respects_lot_size(self):
        # 1000/5 = 200 units, but in lots of 30 that is 6 whole lots = 180.
        qty = position_size(
            risk_budget=Decimal(1000),
            distance=Decimal(5),
            lot_size=Decimal(30),
        )
        assert qty == Decimal(180)
        assert qty * Decimal(5) <= Decimal(1000)

    def test_budget_too_small_for_one_lot_refuses(self):
        qty = position_size(
            risk_budget=Decimal(10),
            distance=Decimal(5),
            lot_size=Decimal(100),
        )
        assert qty == Decimal(0)

    @pytest.mark.parametrize(
        ("budget", "distance", "lot"),
        [("1000", "0", "1"), ("0", "5", "1"), ("-100", "5", "1"), ("1000", "5", "0")],
    )
    def test_unsizable_inputs_return_zero(self, budget, distance, lot):
        assert position_size(
            risk_budget=Decimal(budget),
            distance=Decimal(distance),
            lot_size=Decimal(lot),
        ) == Decimal(0)


class TestRiskAmount:
    def test_uses_the_floored_quantity_not_the_budget(self):
        # The realised risk sits just under the budget because of the floor, and R
        # must be denominated by what was actually risked.
        distance = Decimal(3)
        qty = position_size(risk_budget=Decimal(1000), distance=distance)
        actual = risk_amount(quantity=qty, distance=distance)
        assert actual == Decimal(999)
        assert actual < Decimal(1000)

    def test_zero_when_nothing_is_sized(self):
        assert risk_amount(quantity=Decimal(0), distance=Decimal(5)) == Decimal(0)
        assert risk_amount(quantity=Decimal(10), distance=Decimal(0)) == Decimal(0)


class TestSizeFromLevels:
    def test_returns_quantity_and_realised_risk(self):
        qty, risk = size_from_levels(
            direction=Direction.LONG,
            entry_price=Decimal(100),
            stop_price=Decimal(97),
            risk_budget=Decimal(1000),
        )
        assert qty == Decimal(333)
        assert risk == Decimal(999)

    def test_refuses_a_bad_stop_on_a_single_check(self):
        qty, risk = size_from_levels(
            direction=Direction.LONG,
            entry_price=Decimal(100),
            stop_price=Decimal(105),
            risk_budget=Decimal(1000),
        )
        assert (qty, risk) == (Decimal(0), Decimal(0))

    def test_equal_risk_across_price_levels(self):
        # The whole reason for sizing from the stop: a 3% stop costs the same whether
        # the instrument trades at 450 or at 180.
        _, risk_high = size_from_levels(
            direction=Direction.LONG,
            entry_price=Decimal(450),
            stop_price=Decimal("436.5"),
            risk_budget=Decimal(1000),
        )
        _, risk_low = size_from_levels(
            direction=Direction.LONG,
            entry_price=Decimal(180),
            stop_price=Decimal("174.6"),
            risk_budget=Decimal(1000),
        )
        assert abs(risk_high - risk_low) < Decimal(10)
