"""
Tests for reading what the account holds.

The settled-cash read is the one that matters most and can be exercised least: the paper
account is margin with a million dollars in it, and IB sends no settled figure for a margin
account at all, so the only place the figure could bind is the live cash account. What these
pin is the direction each case fails in - a cash account refused, never defaulted to equity;
a margin account told it has no cap, never refused - and that a later account update without
a summary does not hide the figure an earlier one carried.

"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from decimal import Decimal

import pytest

from copilot.live.account import SETTLED_CASH_NOT_APPLICABLE
from copilot.live.account import SETTLED_CASH_REPORTED
from copilot.live.account import SETTLED_CASH_TAG
from copilot.live.account import NoAccountError
from copilot.live.account import reported_settled_cash
from nautilus_trader.model import AccountType
from nautilus_trader.model import Venue


VENUE = Venue("IB")


@dataclass
class FakeState:
    info: dict | None


@dataclass
class FakeAccount:
    events: list[FakeState] = field(default_factory=list)
    base_currency: object = None
    account_type: AccountType = AccountType.CASH


class FakeCache:
    def __init__(self, account: FakeAccount | None) -> None:
        self._account = account

    def account_id(self, venue: Venue) -> str | None:
        return "IB-DU0000000" if self._account is not None and venue == VENUE else None

    def account_for_venue(self, _venue: Venue) -> FakeAccount | None:
        return self._account


def settled(
    *infos: dict | None,
    base: object = None,
    account_type: AccountType = AccountType.CASH,
) -> tuple[Decimal | None, str]:
    account = FakeAccount(
        events=[FakeState(info) for info in infos],
        base_currency=base,
        account_type=account_type,
    )
    return reported_settled_cash(FakeCache(account), (VENUE,))


def test_reads_the_tag_the_adapter_carries_into_info() -> None:
    assert settled({SETTLED_CASH_TAG: "1000000.00", "NetLiquidation": "1000250.00"}) == (
        Decimal("1000000.00"),
        SETTLED_CASH_REPORTED,
    )


def test_the_newest_state_carrying_the_tag_wins() -> None:
    amount, _ = settled({SETTLED_CASH_TAG: "900"}, {SETTLED_CASH_TAG: "750.50"})
    assert amount == Decimal("750.50")


def test_a_later_update_without_a_summary_does_not_hide_the_figure() -> None:
    """
    An account update that brought balances and no summary is not the cash disappearing.
    """
    amount, _ = settled({SETTLED_CASH_TAG: "5000"}, None, {})
    assert amount == Decimal(5000)


def test_a_negative_figure_is_read_as_reported() -> None:
    """
    The budget decides what a negative figure means; the reader does not soften it.
    """
    amount, _ = settled({SETTLED_CASH_TAG: "-12.34"})
    assert amount == Decimal("-12.34")


def test_a_margin_account_has_no_cap_and_says_why() -> None:
    """
    The paper account's own shape on 2026-09-11: nine summary tags, no SettledCash.
    """
    paper = {
        "AvailableFunds": "1000000.00",
        "BuyingPower": "4000000.00",
        "NetLiquidation": "1000079.59",
        "TotalCashValue": "1000079.59",
    }
    assert settled(paper, account_type=AccountType.MARGIN) == (None, SETTLED_CASH_NOT_APPLICABLE)


def test_a_cash_account_missing_the_figure_is_refused_rather_than_defaulted() -> None:
    with pytest.raises(NoAccountError, match="reports no SettledCash"):
        settled({"TotalCashValue": "1000"})


def test_an_unreadable_figure_is_refused() -> None:
    with pytest.raises(NoAccountError, match="not a number"):
        settled({SETTLED_CASH_TAG: "n/a"})


def test_a_non_usd_cash_account_is_refused() -> None:
    with pytest.raises(NoAccountError, match="carries no currency"):
        settled({SETTLED_CASH_TAG: "1000"}, base="EUR")


def test_a_usd_cash_account_is_read() -> None:
    amount, _ = settled({SETTLED_CASH_TAG: "1000"}, base="USD")
    assert amount == Decimal(1000)


def test_no_account_is_refused() -> None:
    with pytest.raises(NoAccountError, match="no account under"):
        reported_settled_cash(FakeCache(None), (VENUE,))
