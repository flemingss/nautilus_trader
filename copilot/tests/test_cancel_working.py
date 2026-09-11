"""
Tests for the monitoring-end sweep's coverage and its verdict.

The broker half is exercised by running it. What is testable here is the part that
decided, for a day, that the sweep covered one instrument in nine: which instruments an
invocation means, and whether "clear" is computed from acknowledgements rather than from
having sent a cancel.

"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from copilot.live.cancel_working import BROKER_CLEAR
from copilot.live.cancel_working import CENSUS_WAITS_SECS
from copilot.live.cancel_working import STILL_WORKING
from copilot.live.cancel_working import UNCONFIRMED
from copilot.live.cancel_working import CancelWorking
from copilot.live.cancel_working import CancelWorkingConfig
from copilot.live.cancel_working import CensusResult
from copilot.live.cancel_working import Confirmation
from copilot.live.cancel_working import OpenOrderCensus
from copilot.live.cancel_working import OpenOrderCensusConfig
from copilot.live.cancel_working import confirm
from copilot.live.cancel_working import instruments_to_sweep
from copilot.live.cancel_working import report
from copilot.live.node import CANCEL_DEADLINE_SECS
from copilot.live.node import wait_for_settlement
from copilot.live.symbology import registered_instruments
from nautilus_trader.model import InstrumentId


def test_all_means_the_registry() -> None:
    # The sweep follows the universe; a list would have stopped at the first three.
    assert instruments_to_sweep(all_=True, symbol=None, venue="XNAS") == registered_instruments()
    assert len(registered_instruments()) > 1


def test_a_symbol_means_that_one_in_the_brokers_form() -> None:
    assert instruments_to_sweep(all_=False, symbol="schx", venue="arcx") == ("SCHX=STK.SMART",)


def test_naming_nothing_sweeps_nothing() -> None:
    # Empty rather than a default: the old AAPL default swept one instrument while the
    # operator read RESULT: CACHE CLEAR as the session being closed.
    assert instruments_to_sweep(all_=False, symbol=None, venue="XNAS") == ()


def test_all_wins_over_a_symbol() -> None:
    assert len(instruments_to_sweep(all_=True, symbol="AAPL", venue="XNAS")) == len(
        registered_instruments(),
    )


def _sweeper(*ids: str) -> CancelWorking:
    return CancelWorking(
        CancelWorkingConfig(instrument_ids=tuple(InstrumentId.from_str(i) for i in ids)),
    )


def test_outstanding_is_what_was_found_and_never_acknowledged() -> None:
    # Sending a cancel is not evidence; the acknowledgement is. An order found working
    # and never acknowledged stays outstanding whatever was sent.
    strategy = _sweeper("AAPL=STK.SMART", "SCHX=STK.SMART")
    strategy.before["AAPL=STK.SMART"] = ["O-1", "O-2"]
    strategy.before["SCHX=STK.SMART"] = ["O-3"]
    strategy.canceled = ["O-1", "O-3"]
    assert strategy.outstanding() == {"AAPL=STK.SMART": ["O-2"]}


def test_nothing_found_is_clear() -> None:
    assert _sweeper("AAPL=STK.SMART").outstanding() == {}


def test_every_configured_instrument_is_reported_even_when_clear() -> None:
    # A sweep that only listed instruments with orders would read the same whether it
    # covered nine instruments or one.
    strategy = _sweeper("AAPL=STK.SMART", "SCHX=STK.SMART")
    assert set(strategy.before) == {"AAPL=STK.SMART", "SCHX=STK.SMART"}


# --------------------------------------------------------------- the settlement wait


@pytest.mark.asyncio
async def test_settlement_returns_as_soon_as_the_condition_holds() -> None:
    """
    The common case must not cost the whole deadline.

    A sweep that always burns its deadline is a sweep nobody runs at the end of a
    session, and the monitoring-end policy is not optional.

    """
    calls = {"n": 0}

    def settled() -> bool:
        calls["n"] += 1
        return calls["n"] >= 2

    loop = asyncio.get_running_loop()
    started = loop.time()
    assert await wait_for_settlement(settled, deadline_secs=60, poll_secs=0.01) is True
    assert loop.time() - started < 1.0


@pytest.mark.asyncio
async def test_settlement_reports_false_when_the_deadline_passes() -> None:
    """
    The distinction a fixed sleep cannot draw: not yet, against refused.
    """
    assert await wait_for_settlement(lambda: False, deadline_secs=0, poll_secs=0.01) is False


@pytest.mark.asyncio
async def test_settlement_checks_before_it_waits() -> None:
    """
    An already-settled condition must not sleep once, or every sweep pays a poll.
    """
    assert await wait_for_settlement(lambda: True, deadline_secs=0, poll_secs=30) is True


def test_the_sweeps_deadline_is_long_enough_for_the_acknowledgement_seen() -> None:
    """
    2026-09-10: the acknowledgement had not arrived 90s after a cancel, and had by the
    next connection. A 30s deadline called that a refusal and sent the operator to TWS
    to cancel an order the broker had already cancelled.
    """
    assert CANCEL_DEADLINE_SECS > 90


# ------------------------------------------------------- the broker's verdict, asked twice


def _census(**open_: list[str]) -> CensusResult:
    return CensusResult(open={k.replace("_", "="): v for k, v in open_.items()})


class _Broker:
    """
    Answers censuses from a script, and records how long it was made to wait.
    """

    def __init__(self, *answers: CensusResult | None) -> None:
        self.answers = list(answers)
        self.asked = 0
        self.waited: list[float] = []

    async def census(self) -> CensusResult | None:
        self.asked += 1
        return self.answers.pop(0)

    async def sleep(self, seconds: float) -> None:
        self.waited.append(seconds)


@pytest.mark.asyncio
async def test_a_clean_sweep_is_confirmed_by_the_first_census_and_waits_for_nothing() -> None:
    broker = _Broker(_census())

    confirmation = await confirm(broker.census, sleep=broker.sleep)

    assert confirmation.verdict == BROKER_CLEAR
    assert broker.asked == 1
    assert broker.waited == []


@pytest.mark.asyncio
async def test_a_slow_cancel_is_waited_for_rather_than_failed() -> None:
    """
    2026-09-10: the order was gone about ten minutes after the sweep, and no ack ever came.
    """
    broker = _Broker(
        _census(AAPL_STK_SMART=["O-1"]),
        _census(AAPL_STK_SMART=["O-1"]),
        _census(),
    )

    confirmation = await confirm(broker.census, sleep=broker.sleep)

    assert confirmation.verdict == BROKER_CLEAR
    assert broker.waited == [60, 180]


@pytest.mark.asyncio
async def test_an_order_still_open_at_the_last_census_is_still_working() -> None:
    broker = _Broker(*[_census(SPY_STK_SMART=["O-9"])] * len(CENSUS_WAITS_SECS))

    confirmation = await confirm(broker.census, sleep=broker.sleep)

    assert confirmation.verdict == STILL_WORKING
    assert len(confirmation.censuses) == len(CENSUS_WAITS_SECS)
    assert sum(broker.waited) >= 600, "the schedule reaches about ten minutes"


@pytest.mark.asyncio
async def test_a_broker_that_cannot_be_read_is_unconfirmed_not_clear() -> None:
    """
    The old verdict read an empty cache as CLEAR; an unread broker must never pass.
    """
    broker = _Broker(*[None] * len(CENSUS_WAITS_SECS))

    confirmation = await confirm(broker.census, sleep=broker.sleep)

    assert confirmation.verdict == UNCONFIRMED


@pytest.mark.asyncio
async def test_an_unread_last_census_does_not_inherit_an_earlier_reading() -> None:
    broker = _Broker(_census(SPY_STK_SMART=["O-9"]), None, None, None)

    assert (await confirm(broker.census, sleep=broker.sleep)).verdict == UNCONFIRMED


@pytest.mark.asyncio
async def test_an_unread_census_is_retried() -> None:
    broker = _Broker(None, _census())

    assert (await confirm(broker.census, sleep=broker.sleep)).verdict == BROKER_CLEAR


def test_a_census_sorts_open_orders_into_the_swept_instruments_and_the_rest() -> None:
    orders = [
        SimpleNamespace(instrument_id="AAPL=STK.SMART", client_order_id="O-1"),
        SimpleNamespace(instrument_id="AAPL=STK.SMART", client_order_id="O-2"),
        SimpleNamespace(instrument_id="QQQ=STK.SMART", client_order_id="O-3"),
    ]

    result = CensusResult.of([InstrumentId.from_str("AAPL=STK.SMART")], orders)

    assert result.open == {"AAPL=STK.SMART": ["O-1", "O-2"]}
    assert result.elsewhere == ["QQQ=STK.SMART O-3"]


def test_the_census_strategy_starts_unread() -> None:
    config = OpenOrderCensusConfig(instrument_ids=(InstrumentId.from_str("AAPL=STK.SMART"),))

    assert OpenOrderCensus(config).result is None


@pytest.mark.parametrize(
    ("verdict", "code"),
    [(BROKER_CLEAR, 0), (STILL_WORKING, 1), (UNCONFIRMED, 3)],
)
def test_each_verdict_has_its_own_exit_code(verdict: str, code: int, capsys) -> None:
    assert report(Confirmation(verdict, (None,))) == code
    out = capsys.readouterr().out
    assert f"RESULT: {verdict}" in out
    assert ("is an alert, not a pass" in out) is (verdict != BROKER_CLEAR)


class _Cache:
    def __init__(self, account: str | None, orders: list[object]) -> None:
        self._account = account
        self._orders = orders

    def account_id(self, venue: object) -> str | None:
        return self._account if str(venue) == "IB" else None

    def orders_open(self) -> list[object]:
        return self._orders


def test_a_census_without_an_account_is_unread_not_clear() -> None:
    """
    The execution client never connected - read-only API, say - and the cache is empty.

    Reading that as nothing open would report BROKER CLEAR over an order still working.
    """
    ids = [InstrumentId.from_str("AAPL=STK.SMART")]

    assert CensusResult.read(ids, _Cache(None, [])) is None


def test_a_census_with_an_account_reports_what_reconciliation_adopted() -> None:
    ids = [InstrumentId.from_str("AAPL=STK.SMART")]
    order = SimpleNamespace(instrument_id="AAPL=STK.SMART", client_order_id="O-7")

    result = CensusResult.read(ids, _Cache("IB-DUT067974", [order]))

    assert result is not None
    assert result.account == "IB-DUT067974"
    assert result.open == {"AAPL=STK.SMART": ["O-7"]}
