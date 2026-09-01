"""
Tests for the guard that stands between a paper test and the real account.

On the stage-one deployment shape, paper and live differ by a port number and nothing
else. These tests exist because that distance is one keystroke wide, so the checks that
widen it need to be pinned rather than trusted.

"""

from __future__ import annotations

import pytest

from copilot.live.node import apply_order_switch
from copilot.live.session import GATEWAY_LIVE_PORT
from copilot.live.session import GATEWAY_PAPER_PORT
from copilot.live.session import TWS_LIVE_PORT
from copilot.live.session import TWS_PAPER_PORT
from copilot.live.session import NotAPaperSessionError
from copilot.live.session import PaperSession
from copilot.live.session import verify_client_id
from copilot.live.session import verify_paper_session


PAPER_ACCOUNT = "DU067974"


def a_session(**overrides: object) -> PaperSession:
    return PaperSession(**{"account_id": PAPER_ACCOUNT, **overrides})  # type: ignore[arg-type]


# ------------------------------------------------------------------ the port check


@pytest.mark.parametrize("port", [TWS_PAPER_PORT, GATEWAY_PAPER_PORT])
def test_a_known_paper_port_is_accepted(port):
    assert a_session(port=port).port == port


@pytest.mark.parametrize("port", [TWS_LIVE_PORT, GATEWAY_LIVE_PORT])
def test_a_known_live_port_is_refused(port):
    """
    The mistake that reaches real capital fastest, so it is named explicitly.
    """
    with pytest.raises(NotAPaperSessionError, match="live"):
        a_session(port=port)


def test_an_unrecognised_port_is_refused_rather_than_assumed_paper():
    """
    Defaulting an unknown port to paper is the failure this whole module exists to stop.

    A reconfigured TWS is a real possibility; the answer is to add the port to
    PAPER_PORTS deliberately, which is a reviewable diff, not to let the check pass.

    """
    with pytest.raises(NotAPaperSessionError, match="not a known IB paper port"):
        a_session(port=7500)


def test_transposing_the_paper_port_digits_lands_on_live_and_is_caught():
    """
    7497 mistyped as 7496 is the concrete accident being guarded against.
    """
    assert TWS_PAPER_PORT - 1 == TWS_LIVE_PORT
    with pytest.raises(NotAPaperSessionError):
        a_session(port=TWS_PAPER_PORT - 1)


# --------------------------------------------------------------- the account check


def test_an_account_without_the_paper_prefix_is_refused():
    with pytest.raises(NotAPaperSessionError, match="DU"):
        a_session(account_id="U1234567")


def test_an_empty_account_is_refused():
    with pytest.raises(NotAPaperSessionError, match="no account identifier"):
        a_session(account_id="")


def test_a_paper_shaped_account_that_is_not_the_configured_one_is_refused():
    """
    The prefix catches a wrong-but-plausible account; the exact match catches the rest.

    Neither check alone is enough, which is why both run.

    """
    with pytest.raises(NotAPaperSessionError, match="not the configured paper account"):
        verify_paper_session(
            account_id="DU999999",
            port=TWS_PAPER_PORT,
            expected_account_id=PAPER_ACCOUNT,
        )


def test_the_two_checks_are_independent():
    """
    A right account on a live port fails, and a wrong account on a paper port fails.
    """
    with pytest.raises(NotAPaperSessionError):
        verify_paper_session(account_id=PAPER_ACCOUNT, port=TWS_LIVE_PORT)
    with pytest.raises(NotAPaperSessionError):
        verify_paper_session(account_id="U1234567", port=TWS_PAPER_PORT)


# ------------------------------------------------------------------ client IDs


def test_a_client_id_divisible_by_the_partition_is_refused():
    """
    Cheap insurance against an unverified but plausible IB constraint.
    """
    with pytest.raises(NotAPaperSessionError, match="divisible"):
        verify_client_id(1_000)


@pytest.mark.parametrize("client_id", [0, -1])
def test_a_non_positive_client_id_is_refused(client_id):
    with pytest.raises(NotAPaperSessionError, match="must be positive"):
        verify_client_id(client_id)


def test_data_and_execution_clients_may_not_share_an_id():
    """
    IB partitions order IDs by client ID; sharing one makes two connections collide.
    """
    with pytest.raises(NotAPaperSessionError, match="must differ"):
        a_session(data_client_id=5, exec_client_id=5)


# --------------------------------------------------------------- the order switch


class FakeRiskEngine:
    """
    Records what was set, so the switch can be tested without a node.
    """

    def __init__(self) -> None:
        self.states: list[object] = []

    def set_trading_state(self, state: object) -> None:
        self.states.append(state)


def test_orders_disabled_halts_the_engine():
    engine = FakeRiskEngine()

    apply_order_switch(engine, orders_enabled=False)

    assert [str(s) for s in engine.states] == ["HALTED"]


def test_orders_enabled_sets_nothing_at_all():
    """
    Enabling orders is the absence of a halt, never an instruction to resume.

    Setting ACTIVE here would silently clear a halt a breaker had set for a reason this
    code knows nothing about.

    """
    engine = FakeRiskEngine()

    apply_order_switch(engine, orders_enabled=True)

    assert engine.states == []


def test_a_session_defaults_to_orders_disabled():
    """
    The safe default.

    A session that forgot to say must not be able to trade.

    """
    assert a_session().orders_enabled is False
