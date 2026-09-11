"""
Cancel every working order on the registered instruments, and confirm the broker agrees.

    export IBAPI_TIMEZONE_ALIASES="JST=Asia/Tokyo"
    python -m copilot.live.cancel_working --all --account DUT067974

``playbook/OPERATIONS.md`` makes this the non-optional end of every monitoring window:
*"Block new entry intents; cancel every working entry order; wait for and verify broker
cancellation acknowledgements."* It exists as a tool rather than a paragraph because the
first stage-three attempt left a live GTC order at the broker and there was nothing to
clean it up with.

One node, every instrument
--------------------------
Until 2026-09-05 this took one ``--symbol`` per invocation and defaulted to AAPL. The
operator-day walk measured that at 41.5 seconds a symbol, so a nine-instrument sweep was
six minutes of commands, and a forgotten one left an order working overnight - the exact
failure the monitoring-end policy exists to prevent, prevented by the operator remembering.
``--all`` sweeps the registry's instruments in one node, and the registry is the source so
the sweep follows the universe without anyone extending a list.

``strategy_only=False`` is the point
------------------------------------
A residual order belongs to the strategy instance that placed it, and that instance died
with the run that owns it. A strategy-scoped cancel would find nothing and report success,
which is the worst possible outcome for a tool whose entire job is to leave nothing behind.

**What this tool can and cannot see**
-------------------------------------
It cancels what is in the cache, and an order only enters the cache if reconciliation
adopted it.

Until 2026-09-01 that excluded any external order the broker reported as ``SUBMITTED``.
The status match in ``crates/execution/src/reconciliation/orders.rs`` dropped it with a
warning and an empty event list, so an order left working by a previous run was logged as
*"Unhandled order status SUBMITTED for external order"* and then existed at the broker and
nowhere else - and this tool reported success, because the cache it consulted was empty.

**That defect is fixed here.** ``Submitted`` is adopted as an acceptance, and
``fetch_all_open_orders=True`` in ``live/node.py`` stops ``reqOpenOrders`` from returning
only the calling client id's orders. A sweep can now see a previous run's orders, and the
fix was confirmed against IB on 2026-09-03 (``probes/strand_recovery``).

**The caveat survives the fix, for a different reason.** A large order can trip a TWS
precautionary size setting, which holds it in the GUI awaiting a manual transmit: our side
records an acceptance, the broker never receives the order, and no API call can see or
cancel it. No code change on our side reaches that state.

The verdict is the broker's, asked twice
----------------------------------------
Until 2026-09-10 the verdict was this node's own event stream: an order found working and
never acknowledged was FAIL, and nothing found was CLEAR. Both readings were wrong in
practice. Measured that day, an adopted order is cancelled at the broker and **no
acknowledgement arrives at all**, from the placing client id or a foreign one, so the sweep
reported FAIL on an order that was gone; and a cache that never adopted an order reported
CLEAR over one that was not. What worked by hand was asking again on a fresh connection.

So the sweep now ends with a **census**: a new node, orders denied, whose startup
reconciliation asks the broker for every open order on the account (``reqOpenOrders``
across client ids), read and reported without touching anything. It is retried on a
schedule, because the same measurement saw a cancel take about ten minutes to clear. Three
verdicts, three exit codes:

- ``BROKER CLEAR`` (0) - a census found nothing open on the swept instruments.
- ``STILL WORKING`` (1) - the last census still found orders.
- ``UNCONFIRMED`` (3) - no census could be read at all. Per ``OPERATIONS.md`` an order whose
  status cannot be confirmed is an alert, not a pass, and this is that case.

The precautionary-size caveat above survives: an order held in the TWS or Gateway GUI never
reached the broker, so a census cannot see it either. The paper VM's Gateway is to bypass
order precautions for API orders for that reason (``docs/DRAFT_PAPER_VM.md``).

"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from typing import Any

from copilot.live.account import EXEC_CLIENT_VENUE
from copilot.live.account import find_account
from copilot.live.alerting import Alert
from copilot.live.alerting import Severity
from copilot.live.alerting import alerter_from_environment
from copilot.live.node import CANCEL_DEADLINE_SECS
from copilot.live.node import build_paper_node
from copilot.live.node import wait_for_settlement
from copilot.live.session import PaperSession
from copilot.live.session import add_broker_arguments
from copilot.live.symbology import broker_instrument_id
from copilot.live.symbology import registered_instruments
from nautilus_trader.adapters.interactive_brokers import MarketDataType
from nautilus_trader.model import InstrumentId
from nautilus_trader.model import Venue
from nautilus_trader.trading import Strategy
from nautilus_trader.trading import StrategyConfig


class CancelWorkingConfig(StrategyConfig):
    """
    Which instruments to sweep.
    """

    _CUSTOM_FIELDS = ("instrument_ids",)

    def __new__(cls, *args: object, **kwargs: object):  # noqa: ANN204 - pyo3 base
        """
        Strip the custom fields before the pyo3 base sees them.
        """
        for field_name in cls._CUSTOM_FIELDS:
            kwargs.pop(field_name, None)
        return super().__new__(cls, *args, **kwargs)

    def __init__(self, instrument_ids: tuple[Any, ...], **_kwargs: object) -> None:
        """
        Configure the sweep.
        """
        super().__init__()
        self.instrument_ids = tuple(instrument_ids)


class CancelWorking(Strategy):
    """
    Cancels every working order on each configured instrument, across strategies.
    """

    def __init__(self, config: CancelWorkingConfig) -> None:
        """
        Start with nothing observed.
        """
        super().__init__(config)
        self.before: dict[str, list[str]] = {str(i): [] for i in config.instrument_ids}
        """
        Working orders found per instrument before the sweep, by client order id.
        """
        self.canceled: list[str] = []

    def on_start(self) -> None:
        """
        Sweep every instrument, across strategies rather than only this one.
        """
        for order in self.cache.orders_open():
            bucket = self.before.get(str(order.instrument_id))
            if bucket is not None:
                bucket.append(str(order.client_order_id))
        found = {k: v for k, v in self.before.items() if v}
        self.log.info(f"Working orders before sweep: {found or 'none'}")
        for instrument_id in self.config.instrument_ids:
            self.cancel_all_orders(instrument_id, strategy_only=False)

    def on_order_canceled(self, event: Any) -> None:
        """
        Record each acknowledgement, since the acknowledgement is the evidence.
        """
        self.canceled.append(str(event.client_order_id))

    def outstanding(self) -> dict[str, list[str]]:
        """
        Return, per instrument, the orders found working that were never acknowledged.
        """
        acknowledged = set(self.canceled)
        return {
            instrument: [o for o in orders if o not in acknowledged]
            for instrument, orders in self.before.items()
            if any(o not in acknowledged for o in orders)
        }


BROKER_CLEAR = "BROKER CLEAR"
STILL_WORKING = "STILL WORKING"
UNCONFIRMED = "UNCONFIRMED"

EXIT_CODES = {BROKER_CLEAR: 0, STILL_WORKING: 1, UNCONFIRMED: 3}

CENSUS_WAITS_SECS = (0, 60, 180, 360)
"""
Seconds to wait before each census, so the last is about ten minutes after the sweep.

Ten because that is what a cancel took to clear on 2026-09-10 (``PAPER_CAMPAIGN.md``,
*The residue problem*). A clean sweep - the normal case - stops at the first census.

"""

CENSUS_READ_DEADLINE_SECS = 90
"""
How long one census node may take to connect, reconcile and start before it is unread.
"""

CENSUS_DATA_CLIENT_ID = 823
CENSUS_EXEC_CLIENT_ID = 824


class OpenOrderCensusConfig(CancelWorkingConfig):
    """
    Which instruments a census reports on; every other open order is counted too.
    """


class OpenOrderCensus(Strategy):
    """
    Reads what the broker reports open at startup, and places and cancels nothing.
    """

    def __init__(self, config: OpenOrderCensusConfig) -> None:
        """
        Start unread.
        """
        super().__init__(config)
        self.result: CensusResult | None = None
        self.started = False

    def on_start(self) -> None:
        """
        Record the open orders reconciliation adopted, which is the broker's answer.
        """
        self.result = CensusResult.read(self.config.instrument_ids, self.cache)
        self.started = True
        if self.result is None:
            self.log.error(
                "Census unread: no account in the cache, so the execution client never "
                "reconciled and an empty order list would mean nothing",
            )
        else:
            self.log.info(f"Census of {self.result.account}: {self.result.open or 'nothing open'}")


@dataclass(frozen=True)
class CensusResult:
    """
    What one census found open, per swept instrument, and elsewhere on the account.
    """

    open: dict[str, list[str]]
    elsewhere: list[str] = field(default_factory=list)
    account: str = ""

    @classmethod
    def read(cls, instrument_ids: Sequence[Any], cache: Any) -> CensusResult | None:
        """
        Read the cache's open orders, or None when no account proves it reconciled.

        **An empty cache is not an empty broker.** An execution client that never connected
        - a read-only API setting blocks it, and its only symptom is a missing account -
        leaves the cache empty too, and without this check the census would report that as
        ``BROKER CLEAR``. The account is what reconciliation leaves behind.

        """
        found = find_account(cache, (Venue(EXEC_CLIENT_VENUE),))
        if found is None:
            return None
        result = cls.of(instrument_ids, cache.orders_open())
        return cls(open=result.open, elsewhere=result.elsewhere, account=found[1])

    @classmethod
    def of(cls, instrument_ids: Sequence[Any], orders: Any) -> CensusResult:
        """
        Sort open orders into the swept instruments and everything else.
        """
        swept = {str(i) for i in instrument_ids}
        found: dict[str, list[str]] = {}
        elsewhere: list[str] = []
        for order in orders:
            instrument = str(order.instrument_id)
            if instrument in swept:
                found.setdefault(instrument, []).append(str(order.client_order_id))
            else:
                elsewhere.append(f"{instrument} {order.client_order_id}")
        return cls(open=found, elsewhere=elsewhere)


@dataclass(frozen=True)
class Confirmation:
    """
    The broker's verdict on a sweep, and every census it rests on.
    """

    verdict: str
    censuses: tuple[CensusResult | None, ...]


async def confirm(
    take_census: Callable[[], Awaitable[CensusResult | None]],
    *,
    waits: Sequence[int] = CENSUS_WAITS_SECS,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> Confirmation:
    """
    Ask the broker until the swept instruments are clear, or the schedule runs out.

    ``BROKER CLEAR`` at the first census that finds nothing open. Otherwise the last census
    decides: ``STILL WORKING`` if it read and found orders, ``UNCONFIRMED`` if it could not
    read - an unread final census does not inherit an earlier reading, because the question
    is what is working *now*.

    """
    censuses: list[CensusResult | None] = []
    for wait in waits:
        if wait:
            await sleep(wait)
        result = await take_census()
        censuses.append(result)
        if result is not None and not result.open:
            return Confirmation(BROKER_CLEAR, tuple(censuses))
    last = censuses[-1] if censuses else None
    return Confirmation(STILL_WORKING if last is not None else UNCONFIRMED, tuple(censuses))


async def census(
    session: PaperSession,
    *,
    instrument_ids: tuple[Any, ...],
    read_deadline_secs: int = CENSUS_READ_DEADLINE_SECS,
) -> CensusResult | None:
    """
    Run one fresh node with orders denied until it has read the broker, and return that.
    """
    strategy = OpenOrderCensus(OpenOrderCensusConfig(instrument_ids=instrument_ids))
    node, _risk_engine = build_paper_node(
        session,
        market_data_type=MarketDataType.DELAYED,
        strategies=(strategy,),
    )
    handle = node.handle()
    task = asyncio.create_task(node.run_async())
    try:
        await wait_for_settlement(
            lambda: strategy.started,
            deadline_secs=read_deadline_secs,
        )
    finally:
        handle.stop()
        try:
            await asyncio.wait_for(task, timeout=60)
        except (TimeoutError, asyncio.CancelledError) as e:
            strategy.log.error(f"Census node did not stop cleanly: {e!r}")
    return strategy.result


async def sweep(
    session: PaperSession,
    *,
    instrument_ids: tuple[Any, ...],
    settle_secs: int,
) -> CancelWorking:
    """
    Run the node until every acknowledgement is in, or the deadline passes.
    """
    strategy = CancelWorking(CancelWorkingConfig(instrument_ids=instrument_ids))
    node, _risk_engine = build_paper_node(
        session,
        market_data_type=MarketDataType.DELAYED,
        strategies=(strategy,),
    )
    handle = node.handle()
    task = asyncio.create_task(node.run_async())
    try:
        await wait_for_settlement(
            lambda: not strategy.outstanding(),
            deadline_secs=settle_secs,
        )
    finally:
        handle.stop()
        try:
            await asyncio.wait_for(task, timeout=60)
        except (TimeoutError, asyncio.CancelledError) as e:
            # Worth saying loudly: a node that will not stop may still hold a
            # connection, and the sweep's result is only meaningful once it has.
            strategy.log.error(f"Node did not stop cleanly: {e!r}")
    return strategy


def instruments_to_sweep(*, all_: bool, symbol: str | None, venue: str) -> tuple[str, ...]:
    """
    Return the broker ids one invocation means, or nothing when it named none.

    ``--all`` is the registry; a symbol is one instrument. Neither is an error the CLI
    reports, rather than a default that sweeps one instrument while the operator believes
    the session is closed.

    """
    if all_:
        return registered_instruments()
    if symbol:
        return (str(broker_instrument_id(symbol.upper(), venue.upper())),)
    return ()


def main(argv: list[str] | None = None) -> int:
    """
    Cancel working orders.

    Non-zero exit if anything was still working at the end.

    """
    parser = argparse.ArgumentParser(
        prog="python -m copilot.live.cancel_working",
        description="Cancel every working order on the registered instruments and verify "
        "the broker agrees.",
    )
    add_broker_arguments(parser, data_client_id=821, exec_client_id=822)
    parser.add_argument(
        "--all",
        action="store_true",
        help="Every registered activation's instrument",
    )
    parser.add_argument("--symbol", help="One symbol instead of the registry")
    parser.add_argument("--venue", default="XNAS", help="Listing venue for --symbol")
    parser.add_argument(
        "--settle-secs",
        type=int,
        default=CANCEL_DEADLINE_SECS,
        help="Deadline for the broker's acknowledgements; the sweep returns as soon as "
        "they are all in",
    )
    args = parser.parse_args(argv)

    if not args.account:
        print("error: no paper account; pass --account or set COPILOT_PAPER_ACCOUNT")
        return 2
    ids = instruments_to_sweep(all_=args.all, symbol=args.symbol, venue=args.venue)
    if not ids:
        print("error: name an instrument with --symbol, or pass --all for the registry")
        return 2

    instrument_ids = tuple(InstrumentId.from_str(s) for s in ids)
    census_session = PaperSession(
        account_id=args.account,
        host=args.host,
        port=args.port,
        data_client_id=CENSUS_DATA_CLIENT_ID,
        exec_client_id=CENSUS_EXEC_CLIENT_ID,
        # A census reads; it must not be able to place or cancel anything.
        orders_enabled=False,
        instrument_ids=ids,
    )
    session = PaperSession(
        account_id=args.account,
        host=args.host,
        port=args.port,
        data_client_id=args.data_client_id,
        exec_client_id=args.exec_client_id,
        # Cancelling is an order command, so the engine must not be halted.
        orders_enabled=True,
        instrument_ids=ids,
    )

    started = datetime.now(UTC)
    print(f"Sweeping working orders on {len(ids)} instrument(s) at {started.isoformat()}")
    strategy = asyncio.run(
        sweep(session, instrument_ids=instrument_ids, settle_secs=args.settle_secs),
    )

    unacknowledged = strategy.outstanding()
    print()
    for instrument, orders in strategy.before.items():
        acknowledged = len(orders) - len(unacknowledged.get(instrument, []))
        print(f"  {instrument:<20} found {len(orders):>2}  acknowledged {acknowledged:>2}")
    print(f"\ncancel acknowledgements: {strategy.canceled or 'none'}")
    print(f"never acknowledged:      {unacknowledged or 'none'} (not a verdict; see the census)")

    print("\nAsking the broker on a fresh connection, orders denied:")
    confirmation = asyncio.run(
        confirm(lambda: census(census_session, instrument_ids=instrument_ids)),
    )
    code = report(confirmation)
    alert = sweep_alert(confirmation, account=args.account)
    if alert is not None:
        delivery = alerter_from_environment().send(alert)
        print(f"alert CRITICAL {alert.title!r}: {delivery.outcome}")
    return code


def sweep_alert(confirmation: Confirmation, *, account: str) -> Alert | None:
    """
    Return the ``CRITICAL`` alert a sweep owes, or None when the broker is clear.

    Critical because the monitoring-end policy names this exact case - *alert on any order
    whose status cannot be confirmed* - and because it is the one failure in the day whose
    cost grows while nobody looks: an order left working overnight is a position by morning.

    """
    if confirmation.verdict == BROKER_CLEAR:
        return None
    last = confirmation.censuses[-1] if confirmation.censuses else None
    if confirmation.verdict == STILL_WORKING and last is not None:
        body = f"The broker still reports orders open after the sweep: {last.open}."
    else:
        body = "No census could read the broker, so no order's status can be confirmed."
    return Alert(
        severity=Severity.CRITICAL,
        title=f"sweep {confirmation.verdict}",
        body=body + " Check the broker's own order list now, and do not re-enable orders "
        "until it reconciles.",
        context={"account": account, "censuses": str(len(confirmation.censuses))},
    )


def report(confirmation: Confirmation) -> int:
    """
    Print each census and the verdict, and return the verdict's exit code.
    """
    for number, result in enumerate(confirmation.censuses, start=1):
        if result is None:
            print(f"  census {number}: could not read the broker")
            continue
        print(f"  census {number}: {result.open or 'nothing open on the swept instruments'}")
        if result.elsewhere:
            print(f"             also open elsewhere on the account: {result.elsewhere}")
    print(f"\nRESULT: {confirmation.verdict}")
    if confirmation.verdict != BROKER_CLEAR:
        print(
            "An order whose status cannot be confirmed is an alert, not a pass "
            "(playbook/OPERATIONS.md, At monitoring end).",
        )
    print(
        "An order held untransmitted by a TWS or Gateway precautionary setting never "
        "reached the\nbroker, so no census can see it.",
    )
    return EXIT_CODES[confirmation.verdict]


__all__ = [
    "BROKER_CLEAR",
    "CENSUS_WAITS_SECS",
    "STILL_WORKING",
    "UNCONFIRMED",
    "CancelWorking",
    "CancelWorkingConfig",
    "CensusResult",
    "Confirmation",
    "OpenOrderCensus",
    "OpenOrderCensusConfig",
    "census",
    "confirm",
    "instruments_to_sweep",
    "report",
    "sweep",
    "sweep_alert",
]


if __name__ == "__main__":
    raise SystemExit(main())
