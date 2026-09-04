"""
Tests for deriving a sizing budget from account equity.

The failure this guards is quiet and was live: the research R-unit of USD 1,000 reaching
a broker-connected strategy as if it were an amount of money, on an account forty times
too small for it. Everything here pins the direction of the arithmetic - floor, never
round; never more than the broker reports - and that every number in the record can be
traced back to an equity and a fraction.

"""

from __future__ import annotations

from decimal import Decimal

import pytest

from copilot.risk.budget import DEFAULT_MAX_NEW_ENTRIES
from copilot.risk.budget import DEFAULT_MAX_POSITION_FRACTION
from copilot.risk.budget import DEFAULT_MAX_TOTAL_RISK_FRACTION
from copilot.risk.budget import DEFAULT_RISK_FRACTION
from copilot.risk.budget import RiskPolicy
from copilot.risk.budget import budget_for


class TestDefaults:
    """
    The two fractions the playbook names, at the end it names them for a canary.
    """

    def test_risk_fraction_is_the_low_end_of_the_playbook_band(self) -> None:
        """
        0.10% to 0.25%; a default that risked more would argue the account sweep wrong.
        """
        assert Decimal("0.0010") == DEFAULT_RISK_FRACTION

    def test_position_cap_is_the_playbooks_ten_percent(self) -> None:
        assert Decimal("0.10") == DEFAULT_MAX_POSITION_FRACTION

    @pytest.mark.parametrize("value", ["0", "-0.001", "1.5"])
    def test_a_fraction_outside_the_unit_interval_is_refused(self, value: str) -> None:
        with pytest.raises(ValueError, match="must be in"):
            RiskPolicy(risk_fraction=Decimal(value))


class TestBudgetFor:
    """
    The arithmetic, and the direction it errs in.
    """

    def test_the_drill_account(self) -> None:
        """
        USD 1,000 at the defaults: one dollar of risk, a hundred of notional.

        Both numbers are small enough that most signals size to zero shares, and that is
        the correct answer for an account this size - the sweep found the premise crossing
        at USD 25,000.

        """
        budget = budget_for(Decimal(1000))
        assert budget.risk_budget == Decimal("1.00")
        assert budget.max_notional == Decimal("100.00")

    def test_floors_to_the_cent(self) -> None:
        """
        Rounding a budget up risks more than the fraction allows.
        """
        budget = budget_for(Decimal("12345.67"))
        assert budget.risk_budget == Decimal("12.34")
        assert budget.risk_budget <= Decimal("12345.67") * DEFAULT_RISK_FRACTION

    def test_an_allocation_below_equity_is_used_as_given(self) -> None:
        """
        Running a strategy on a thousand dollars of a larger account is ordinary.
        """
        budget = budget_for(Decimal(1_000_000), allocation=Decimal(1000))
        assert budget.allocation == Decimal(1000)
        assert budget.risk_budget == Decimal("1.00")
        assert not budget.allocation_capped

    def test_an_allocation_above_equity_is_capped_and_says_so(self) -> None:
        """
        An operator cannot decide the account is larger than the broker says.

        Capped rather than refused, because the alternative is a session that will not
        run on the morning the account dipped below a number in a script - and the record
        carries the request so the cap is visible rather than silent.

        """
        budget = budget_for(Decimal(800), allocation=Decimal(1000))
        assert budget.allocation == Decimal(800)
        assert budget.allocation_capped
        assert budget.as_record()["requested_allocation"] == "1000"
        assert budget.as_record()["allocation_capped"] == "True"

    def test_no_allocation_means_the_whole_equity(self) -> None:
        budget = budget_for(Decimal(5000))
        assert budget.allocation == Decimal(5000)
        assert budget.requested is None
        assert not budget.allocation_capped

    @pytest.mark.parametrize("equity", ["0", "-1"])
    def test_nothing_can_be_sized_against_no_equity(self, equity: str) -> None:
        with pytest.raises(ValueError, match="positive"):
            budget_for(Decimal(equity))

    def test_a_non_positive_allocation_is_refused(self) -> None:
        with pytest.raises(ValueError, match="allocation must be positive"):
            budget_for(Decimal(1000), allocation=Decimal(0))

    def test_the_record_carries_every_input(self) -> None:
        """
        A number in a session record that cannot be traced to an equity and a fraction
        is a number nobody can check the next morning.
        """
        record = budget_for(
            Decimal(1000),
            policy=RiskPolicy(risk_fraction=Decimal("0.0025")),
        ).as_record()
        assert record == {
            "equity": "1000",
            "allocation": "1000",
            "requested_allocation": "",
            "allocation_capped": "False",
            "risk_fraction": "0.0025",
            "max_position_fraction": "0.10",
            "risk_budget": "2.50",
            "max_notional": "100.00",
            "max_total_risk_fraction": "0.0050",
            "max_total_risk": "5.00",
            "max_new_entries": "2",
        }


class TestSessionCaps:
    """
    The two caps the playbook names for the session as a whole.
    """

    def test_total_risk_is_the_low_end_of_the_playbook_band(self) -> None:
        """
        0.50% to 1.00%: five positions at the default per-position risk.
        """
        assert Decimal("0.0050") == DEFAULT_MAX_TOTAL_RISK_FRACTION

    def test_two_entries_is_a_declared_canary_default(self) -> None:
        """
        The case that motivated the cap was four correlated wrappers firing at once, and
        a cap that admits all four is not a cap.
        """
        assert DEFAULT_MAX_NEW_ENTRIES == 2

    def test_the_drill_account_gets_five_dollars_of_session_risk(self) -> None:
        budget = budget_for(Decimal(1000))
        assert budget.max_total_risk == Decimal("5.00")
        assert budget.as_record()["max_new_entries"] == "2"

    def test_a_session_cap_below_one_position_is_a_policy_to_not_trade(self) -> None:
        """
        And should be written as one, not as fractions that refuse everything.
        """
        with pytest.raises(ValueError, match="no position could ever be opened"):
            RiskPolicy(risk_fraction=Decimal("0.0025"), max_total_risk_fraction=Decimal("0.0010"))

    def test_zero_entries_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            RiskPolicy(max_new_entries=0)
