"""
The basket carries the account-wide breaker, with the ledger that makes it survive a
restart.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from copilot.live.run_activation import protection_record
from copilot.live.run_activation import protection_settings
from copilot.paths import risk_ledger_path


def _plan(symbol: str, venue: str) -> SimpleNamespace:
    return SimpleNamespace(activation=SimpleNamespace(symbol=symbol, venue=venue))


def test_the_basket_guard_watches_every_instrument_once_and_keeps_a_ledger_per_account() -> None:
    plans = (_plan("SPY", "ARCX"), _plan("AAPL", "XNAS"), _plan("SPY", "ARCX"))

    settings = protection_settings(plans, account_value=Decimal("1000.00"), account_id="DU1234567")

    assert [str(i) for i in settings.instrument_ids] == ["SPY=STK.SMART", "AAPL=STK.SMART"]
    assert settings.ledger_path == risk_ledger_path("DU1234567")
    assert settings.account_value == Decimal("1000.00")
    assert settings.policy.enabled


def test_the_session_record_says_whether_a_breach_is_in_force() -> None:
    settings = protection_settings(
        (_plan("SPY", "ARCX"),),
        account_value=Decimal(1000),
        account_id="DU1",
    )

    assert protection_record(settings, SimpleNamespace(breach=None))["breach"] == "none"
    breach = SimpleNamespace(
        trigger="consecutive_stops",
        until=SimpleNamespace(isoformat=lambda: "2026-09-14"),
    )
    assert protection_record(settings, SimpleNamespace(breach=breach))["breach"] == (
        "consecutive_stops until 2026-09-14"
    )
