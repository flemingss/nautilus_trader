"""
Tests for the monitoring-end sweep's coverage and its verdict.

The broker half is exercised by running it. What is testable here is the part that
decided, for a day, that the sweep covered one instrument in nine: which instruments an
invocation means, and whether "clear" is computed from acknowledgements rather than from
having sent a cancel.

"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from copilot.live import cancel_working
from copilot.live.alerting import Alert
from copilot.live.alerting import Delivery
from copilot.live.alerting import Severity
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
from copilot.live.cancel_working import file_record
from copilot.live.cancel_working import instruments_to_sweep
from copilot.live.cancel_working import report
from copilot.live.cancel_working import sweep
from copilot.live.cancel_working import sweep_alert
from copilot.live.cancel_working import sweep_record
from copilot.live.node import CANCEL_DEADLINE_SECS
from copilot.live.node import wait_for_settlement
from copilot.live.session import PaperSession
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


def test_a_sweep_that_has_not_started_is_not_settled() -> None:
    """
    Audit F1: before ``on_start`` nothing is found, so *nothing outstanding* holds trivially.
    """
    strategy = _sweeper("AAPL=STK.SMART")

    assert strategy.outstanding() == {}
    assert strategy.settled() is False

    strategy.started = True
    assert strategy.settled() is True

    strategy.before["AAPL=STK.SMART"] = ["O-1"]
    assert strategy.settled() is False, "started, and an order found is not yet acknowledged"


class _Handle:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class _Node:
    """
    Stands in for a ``LiveNode``: connects for a moment, then starts its strategy unless a stop
    arrived first - which is what ``run_async`` does with a stop requested during startup.
    """

    def __init__(self, strategy: CancelWorking, events: list[str], *, starts: bool) -> None:
        self._strategy = strategy
        self._handle = _Handle()
        self._events = events
        self._starts = starts

    def handle(self) -> _Handle:
        return self._handle

    async def run_async(self) -> None:
        await asyncio.sleep(0.05)
        if self._handle.stopped:
            self._events.append("aborted startup")
            return
        if self._starts:
            self._strategy.started = True
            self._events.append("started")
        while not self._handle.stopped:
            await asyncio.sleep(0.01)
        self._events.append("stopped")


def _fake_builder(events: list[str], *, starts: bool):
    def build(_session: object, *, strategies: tuple[object, ...], **_kwargs: object):
        return _Node(strategies[0], events, starts=starts), None

    return build


SWEEP_SESSION = PaperSession(account_id="DU1234567", orders_enabled=True, cancels_only=True)
AAPL = (InstrumentId.from_str("AAPL=STK.SMART"),)


@pytest.mark.asyncio
async def test_the_sweep_lets_its_node_start_before_it_stops_it(monkeypatch) -> None:
    """
    Audit F1, the regression.

    The old wait returned before the node task ran, so the stop reached the node during
    startup and it aborted without issuing a cancel.

    """
    events: list[str] = []
    monkeypatch.setattr(cancel_working, "build_paper_node", _fake_builder(events, starts=True))

    strategy = await sweep(SWEEP_SESSION, instrument_ids=AAPL, settle_secs=5, start_deadline_secs=5)

    assert events == ["started", "stopped"]
    assert strategy.started is True


@pytest.mark.asyncio
async def test_a_sweep_whose_node_never_starts_returns_unstarted(monkeypatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(cancel_working, "build_paper_node", _fake_builder(events, starts=False))

    strategy = await sweep(SWEEP_SESSION, instrument_ids=AAPL, settle_secs=5, start_deadline_secs=0)

    assert strategy.started is False


class _FailingNode(_Node):
    """
    The 2026-09-11 census: the execution client refused with IB 326, and the run raised.
    """

    async def run_async(self) -> None:
        await asyncio.sleep(0.01)
        raise RuntimeError("readiness timeout while waiting for engine connections")


@pytest.mark.asyncio
async def test_a_node_that_fails_to_start_is_an_unread_census_not_a_crashed_sweep(
    monkeypatch,
    capsys,
) -> None:
    def build(_session: object, *, strategies: tuple[object, ...], **_kwargs: object):
        return _FailingNode(strategies[0], [], starts=False), None

    monkeypatch.setattr(cancel_working, "build_paper_node", build)

    result = await cancel_working.census(SWEEP_SESSION, instrument_ids=AAPL, read_deadline_secs=0)
    swept = await sweep(SWEEP_SESSION, instrument_ids=AAPL, settle_secs=0, start_deadline_secs=0)

    assert result is None
    assert swept.started is False
    assert "readiness timeout" in capsys.readouterr().err


def test_every_census_in_a_sweep_has_its_own_client_ids() -> None:
    """
    2026-09-11: the retried census reused the first one's ids and IB refused it with 326.
    """
    base = PaperSession(account_id="DU1234567", data_client_id=823, exec_client_id=824)

    sessions = cancel_working.census_sessions_for(base)
    pairs = [(s.data_client_id, s.exec_client_id) for s in sessions]
    ids = [i for pair in pairs for i in pair]

    assert len(sessions) == len(CENSUS_WAITS_SECS), "one pair per scheduled census"
    assert len(set(ids)) == len(ids)
    assert not {821, 822} & set(ids), "the cancel node's own pair"
    assert all(s.orders_enabled is False for s in sessions)


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


# ------------------------------------------------------------------------- who is told


def test_a_clear_broker_raises_nothing() -> None:
    assert sweep_alert(Confirmation(BROKER_CLEAR, (_census(),)), account="DU1") is None


def test_an_order_still_working_wakes_the_operator_and_names_it() -> None:
    confirmation = Confirmation(STILL_WORKING, (_census(SPY_STK_SMART=["O-9"]),))

    alert = sweep_alert(confirmation, account="DU1")

    assert alert is not None
    assert alert.severity == Severity.CRITICAL
    assert "O-9" in alert.body
    assert alert.context["account"] == "DU1"


def test_an_unreadable_broker_is_critical_too() -> None:
    """
    *Alert on any order whose status cannot be confirmed* - not only on ones seen working.
    """
    alert = sweep_alert(Confirmation(UNCONFIRMED, (None, None)), account="DU1")

    assert alert is not None
    assert alert.severity == Severity.CRITICAL
    assert "No census could read the broker" in alert.body


def test_a_clear_broker_after_a_sweep_that_never_started_still_warns() -> None:
    """
    Nothing is at risk today, and the limb every sweep and the kill switch rely on is
    broken.
    """
    alert = sweep_alert(Confirmation(BROKER_CLEAR, (_census(),)), account="DU1", swept=False)

    assert alert is not None
    assert alert.severity == Severity.WARNING
    assert alert.title == "sweep never started"


def test_an_order_still_working_after_a_sweep_that_never_started_says_nothing_was_cancelled() -> (
    None
):
    confirmation = Confirmation(STILL_WORKING, (_census(SPY_STK_SMART=["O-9"]),))

    alert = sweep_alert(confirmation, account="DU1", swept=False)

    assert alert is not None
    assert alert.severity == Severity.CRITICAL
    assert "nothing was cancelled" in alert.body


def test_every_sweep_files_what_it_found_and_what_the_broker_said(tmp_path: Path) -> None:
    strategy = _sweeper("AAPL=STK.SMART", "SCHX=STK.SMART")
    strategy.started = True
    strategy.before["AAPL=STK.SMART"] = ["O-1", "O-2"]
    strategy.canceled = ["O-1"]
    confirmation = Confirmation(STILL_WORKING, (_census(AAPL_STK_SMART=["O-2"]), None))
    alert = Alert(Severity.CRITICAL, "sweep STILL WORKING", "b")
    run_at = datetime(2026, 9, 11, 14, 30, tzinfo=UTC)

    record = sweep_record(
        run_at=run_at,
        account="DU1",
        strategy=strategy,
        confirmation=confirmation,
        delivery=Delivery(alert=alert, delivered=True, notifier="pushover"),
    )
    path = file_record(record, run_at, out_dir=tmp_path)

    filed = json.loads(path.read_text())
    assert path.name == "sweep_20260911T143000Z.json"
    assert filed["swept"] is True
    assert filed["found"] == {"AAPL=STK.SMART": ["O-1", "O-2"]}
    assert filed["never_acknowledged"] == {"AAPL=STK.SMART": ["O-2"]}
    assert filed["verdict"] == STILL_WORKING
    assert filed["exit_code"] == 1
    assert filed["censuses"][1] is None
    assert filed["alert"] == {
        "severity": "CRITICAL",
        "title": "sweep STILL WORKING",
        "outcome": "sent",
    }
