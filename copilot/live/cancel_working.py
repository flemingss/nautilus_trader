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

A clean result therefore still means **"everything this node knew about is cancelled"**,
which is weaker than "nothing is working at the broker". Confirm the broker's own order
list before treating a session as closed. Per ``OPERATIONS.md``, an order whose status
cannot be confirmed is an alert, not a pass.

"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC
from datetime import datetime
from typing import Any

from copilot.live.node import build_paper_node
from copilot.live.session import PaperSession
from copilot.live.session import add_broker_arguments
from copilot.live.symbology import broker_instrument_id
from copilot.live.symbology import registered_instruments
from nautilus_trader.adapters.interactive_brokers import MarketDataType
from nautilus_trader.model import InstrumentId
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


async def sweep(
    session: PaperSession,
    *,
    instrument_ids: tuple[Any, ...],
    settle_secs: int,
) -> CancelWorking:
    """
    Run the node long enough to cancel on every instrument and hear back.
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
        await asyncio.sleep(settle_secs)
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
    parser.add_argument("--settle-secs", type=int, default=30)
    args = parser.parse_args(argv)

    if not args.account:
        print("error: no paper account; pass --account or set COPILOT_PAPER_ACCOUNT")
        return 2
    ids = instruments_to_sweep(all_=args.all, symbol=args.symbol, venue=args.venue)
    if not ids:
        print("error: name an instrument with --symbol, or pass --all for the registry")
        return 2

    instrument_ids = tuple(InstrumentId.from_str(s) for s in ids)
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

    outstanding = strategy.outstanding()
    print()
    for instrument, orders in strategy.before.items():
        state = "still working" if instrument in outstanding else "clear"
        print(f"  {instrument:<20} before {len(orders):>2}  {state}")
    print(f"\ncancelled:      {strategy.canceled or 'none'}")
    print(f"still working:  {outstanding or 'none'}")
    print(f"\nRESULT: {'CACHE CLEAR' if not outstanding else 'FAIL'}")
    print(
        "\nThis is not proof the broker has nothing working. An order held by a TWS "
        "precautionary\nsize setting never reached the broker and is invisible to the API. "
        "The reconciliation\nfix behind this sweep was confirmed against IB on 2026-09-03 "
        "(strand_recovery), which\ndoes not change the first point. Check the broker's own "
        "order list before calling a\nsession closed.",
    )
    return 0 if not outstanding else 1


__all__ = [
    "CancelWorking",
    "CancelWorkingConfig",
    "instruments_to_sweep",
    "sweep",
]


if __name__ == "__main__":
    raise SystemExit(main())
