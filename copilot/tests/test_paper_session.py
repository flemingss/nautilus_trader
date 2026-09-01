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


# --------------------------------------------------------------- stage two checks


class FakeInstrument:
    def __init__(self, instrument_id: object) -> None:
        self.id = instrument_id


class FakeCache:
    """
    Enough cache to exercise the stage-two observations without a broker.
    """

    def __init__(self, instruments: list[object], account_id: str | None = None) -> None:
        self._instruments = instruments
        self._account_id = account_id

    def instruments(self) -> list[object]:
        return self._instruments

    def account_id(self, venue: object) -> str | None:
        return self._account_id

    def account_for_venue(self, venue: object) -> object | None:
        return None


def test_missing_instruments_are_counted_not_merely_reported():
    """
    A partial resolution is a failure, not a warning.

    Trading a subset of the configured universe silently is worse than not starting.

    """
    from copilot.live.preflight import observe_environment
    from nautilus_trader.model import InstrumentId

    session = a_session(instrument_ids=("AAPL=STK.SMART", "MSFT=STK.SMART"))
    cache = FakeCache([FakeInstrument(InstrumentId.from_str("AAPL=STK.SMART"))])

    resolved = next(
        c for c in observe_environment(cache, session) if c.name == "instruments_resolved"
    )

    assert not resolved.passed
    assert resolved.observed == "1"
    assert resolved.expected == "2"


def test_an_absent_account_names_the_read_only_cause():
    """
    Two checks fail together and neither says why, so the hint is carried in the record.

    The account is missing because the execution client never connected, and it never
    connected because of a checkbox in the TWS GUI. Nothing in the failure text says so.

    """
    from copilot.live.preflight import observe_environment
    from nautilus_trader.model import InstrumentId

    session = a_session(instrument_ids=("AAPL=STK.SMART",))
    cache = FakeCache([FakeInstrument(InstrumentId.from_str("AAPL=STK.SMART"))])

    account = next(
        c for c in observe_environment(cache, session) if c.name == "account_reported_by_broker"
    )

    assert not account.passed
    assert "Read-Only API" in account.note


# ------------------------------------------------------- the controlled order outcome


def test_a_clean_lifecycle_passes():
    from copilot.live.controlled_order import Outcome

    assert Outcome(submitted=True, accepted=True, canceled=True).passed


def test_a_fill_fails_the_stage_even_though_the_lifecycle_completed():
    """
    The order is priced so it cannot fill, so a fill means an assumption was wrong.

    Counting it as a pass because submit, accept and cancel all happened would report a
    healthy run at the exact moment the safety argument collapsed.

    """
    from copilot.live.controlled_order import Outcome

    assert not Outcome(submitted=True, accepted=True, canceled=True, filled=True).passed


@pytest.mark.parametrize("refusal", ["rejected", "denied"])
def test_a_refused_order_fails_the_stage(refusal):
    """
    Stage three wants an acknowledged order.

    A refusal proves the path is not working.

    """
    from copilot.live.controlled_order import Outcome

    assert not Outcome(submitted=True, **{refusal: True}).passed


def test_an_order_that_was_never_cancelled_fails():
    """
    The failure the first stage-three run actually had.

    `Strategy.cancel_order` takes a ClientOrderId; it was passed an Order, did nothing,
    raised nothing, and left a working order at the broker. Only the missing cancel event
    distinguished that run from a clean one.

    """
    from copilot.live.controlled_order import Outcome

    assert not Outcome(submitted=True, accepted=True).passed


# ------------------------------------------------------------ the order type matrix


def test_a_shape_that_round_trips_passes():
    from copilot.live.order_types import Attempt

    assert Attempt(name="LIMIT/GTC", submitted=True, accepted=True, canceled=True).passed


@pytest.mark.parametrize("problem", ["filled", "rejected", "denied"])
def test_a_shape_that_filled_or_was_refused_fails(problem):
    """
    Every price in the matrix is unreachable, so a fill means an assumption broke.

    Grouped with the refusals because all three mean the same thing here: the cell cannot
    be reported as working.

    """
    from copilot.live.order_types import Attempt

    attempt = Attempt(
        name="LIMIT/GTC",
        submitted=True,
        accepted=True,
        canceled=True,
        **{problem: True},
    )

    assert not attempt.passed


def test_the_untestable_shapes_are_named_rather_than_omitted():
    """
    A matrix with an invisible hole reads as complete.

    MARKET is the gap fade's real entry and cannot be submitted without filling, so it
    has to appear in the output as untested rather than simply be absent from it.

    """
    from copilot.live.order_types import UNTESTED_HERE

    assert "MARKET" in UNTESTED_HERE
    assert "BRACKET_MARKET_ENTRY" in UNTESTED_HERE
    for why in UNTESTED_HERE.values():
        assert "stage five" in why
