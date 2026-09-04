"""
Run one registered activation against the paper broker, on the catalog's own bars.

    export IBAPI_TIMEZONE_ALIASES="JST=Asia/Tokyo"
    python -m copilot.live.run_activation aapl-gap-fade-long-next-close --account DUT067974

The first thing in this repository that puts a **researched strategy** on a broker
connection. Everything before it was plumbing exercised by purpose-built probes; this runs
the rule that a walk-forward scored, over the series that scored it, and records what it
decided.

Why the bars come from a file
-----------------------------
Because the broker will not supply them. ``GapReversalStrategy.on_start`` subscribes to
``1-DAY-LAST-EXTERNAL``, the adapter routes any spec other than five seconds to
``reqHistoricalData``, and IB refuses US equity historical bars on this account with
**2188** - re-probed 2026-09-04 across five request shapes, on REALTIME and DELAYED, SMART
and directed. The subscription is left in place and its refusal is recorded, because a
session that quietly did without it would hide the constraint that shapes this whole module.

So the catalog is the data client for daily bars. That is not a workaround so much as the
arrangement [ADR-0017] made possible: research and execution read one series, kept current
by ``copilot.data.append``, so a live decision and its replay are comparable rather than
merely similar.

**What this skips, stated plainly.** There is no Python route into the engine's data path
on this build - no message bus, no data engine, and ``publish_data`` reaches a custom-data
topic rather than the bars topic. So the bar is handed to the strategy directly. The engine's
cache therefore does not see it, and anything that marks from the last bar - unrealised PnL,
in particular - will not. With orders denied and no position open that is inert. **It stops
being inert the moment orders are enabled**, and closing it properly means exposing a bar
publish on the node, which is a Rust change.

Orders are denied, deliberately
-------------------------------
The risk engine is ``HALTED`` before the run starts, so a strategy that decides to trade
does everything except place the order and the denial is recorded. That is the whole point
at this stage: the charter's *System* gate asks whether the code does what the research
says, and that question is answerable without a frozen candidate. Enabling orders is paper
stage seven, which needs one.

Two prices, one instrument
--------------------------
Research names the instrument ``AAPL.XNAS`` and the broker resolves ``AAPL=STK.SMART``
(:mod:`copilot.live.symbology`). The strategy is configured with the **broker** id, because
its orders have to be routable, and its bars are built against the **broker's** instrument
so their precision is the one the venue actually quotes. The catalog stores four decimals
because the vendor delivers four; the broker prices in its own increment, and a bar handed
to a live strategy has to be expressed in the broker's terms. That conversion rounds, which
:func:`to_broker_bars` reports rather than refuses - the opposite of
``catalog.to_nautilus_bars``, which refuses, because a stored history that rounds is corrupt
while a live bar that does not is unusable.

[ADR-0017]: ../docs/decisions/0017-the-evaluation-window-is-pinned-at-both-ends.md

"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import date
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from copilot.data.catalog import BAR_SPEC
from copilot.data.catalog import bar_type_for
from copilot.data.catalog import equity_for
from copilot.live.node import build_paper_node
from copilot.live.session import PaperSession
from copilot.live.symbology import broker_instrument_id
from copilot.live.warmup import load as load_warmup
from copilot.live.warmup import session_to_prepare
from copilot.strategies.activations import Activation
from copilot.strategies.activations import find_activation
from copilot.validation.types import DailyBar
from nautilus_trader.adapters.interactive_brokers import MarketDataType
from nautilus_trader.model import Bar
from nautilus_trader.model import BarType
from nautilus_trader.model import Equity
from nautilus_trader.model import InstrumentId
from nautilus_trader.trading import Strategy


OUT_DIR = Path(__file__).parent / "out"
DEFAULT_CATALOG = "~/.nautilus_copilot/catalog"

SETTLE_SECS = 30
"""
Seconds between node start and handing over the decision bar.

The instrument provider has to have resolved the contract before a bar can be built
against it, and the exec client has to be up before a denial can be recorded. Generous
rather than tight: this runs once a day and a short wait that occasionally is not long
enough would produce a session that looks like a strategy decision and is a race.

"""

DRAIN_SECS = 10
"""
Seconds after the decision, to let order events land before the cache is read.
"""


class NoBrokerInstrumentError(RuntimeError):
    """
    The broker did not resolve the instrument the strategy was configured for.
    """


@dataclass(frozen=True)
class OrderRecord:
    """
    One order the strategy submitted, and what became of it.
    """

    client_order_id: str
    side: str
    quantity: str
    order_type: str
    status: str
    price: str | None = None


@dataclass
class SessionRecord:
    """
    What one activation's session decided, and everything needed to audit it.
    """

    activation: str
    broker_instrument: str
    research_instrument: str
    decision_bar: str
    warmup_bars: int
    warmup_from: str
    warmup_to: str
    parameters: dict[str, str]
    atr_initialized: bool = False
    atr_value: str | None = None
    previous_close: str | None = None
    skips: dict[str, int] = field(default_factory=dict)
    orders: list[OrderRecord] = field(default_factory=list)
    largest_rounding: str = "0"
    instrument_resolved: bool = False
    note: str = ""

    @property
    def decided_to_trade(self) -> bool:
        """
        Whether the rule produced an order, denied or not.
        """
        return bool(self.orders)


def broker_bar_type(instrument_id: InstrumentId) -> BarType:
    """
    Return the daily bar type in the broker's own instrument namespace.

    The spec matches the catalog's exactly - the series is the same series - and only the
    instrument id differs, which is the one thing that has to.

    """
    return BarType.from_str(f"{instrument_id}-{BAR_SPEC}")


def to_broker_bars(
    bars: tuple[DailyBar, ...],
    instrument: Equity,
    bar_type: BarType,
) -> tuple[list[Bar], Decimal]:
    """
    Convert catalog bars to the broker's precision, reporting the largest rounding.

    Reports rather than refuses, unlike ``catalog.to_nautilus_bars``. The difference is
    the direction of the harm: a *stored* history that silently rounded would be read as
    ground truth by every later run, while a *live* bar that refuses to round cannot be
    handed to a strategy at all. The amount is returned so a session that rounded more
    than a tick has somewhere to say so.

    """
    out: list[Bar] = []
    largest = Decimal(0)
    for daily in sorted(bars, key=lambda b: b.closed_at):
        prices = []
        for source in (daily.open, daily.high, daily.low, daily.close):
            converted = instrument.make_price(source)
            largest = max(largest, abs(Decimal(str(converted)) - source))
            prices.append(converted)
        ts = int(daily.closed_at.timestamp() * 1_000_000_000)
        out.append(
            Bar(
                bar_type=bar_type,
                open=prices[0],
                high=prices[1],
                low=prices[2],
                close=prices[3],
                volume=instrument.make_qty(daily.volume),
                ts_event=ts,
                ts_init=ts,
            ),
        )
    return out, largest


def build_strategy(activation: Activation, instrument_id: InstrumentId) -> Strategy:
    """
    Build the activation's strategy against the broker's instrument id.

    The parameters are the activation's own - its seeded identity plus whatever the
    registry fixes - and **not** a holdout's frozen set. Reading frozen parameters back
    into a live run is a promotion, and a promotion is a diff someone reviewed rather
    than a default this module reaches for.

    """
    return activation.setup.factory(
        dict(activation.parameters),
        instrument_id=instrument_id,
        bar_type=broker_bar_type(instrument_id),
        risk_registry=None,
    )


async def run_session(
    session: PaperSession,
    activation: Activation,
    warmup: tuple[DailyBar, ...],
    decision: DailyBar,
    *,
    settle_secs: int = SETTLE_SECS,
    drain_secs: int = DRAIN_SECS,
) -> SessionRecord:
    """
    Start the node, hand the strategy its history and its bar, and record what it did.
    """
    broker_id = broker_instrument_id(activation.symbol, activation.venue)
    research_id = equity_for(activation.symbol, activation.venue).id
    strategy = build_strategy(activation, broker_id)

    record = SessionRecord(
        activation=activation.name,
        broker_instrument=str(broker_id),
        research_instrument=str(research_id),
        decision_bar=decision.closed_at.date().isoformat(),
        warmup_bars=len(warmup),
        warmup_from=warmup[0].closed_at.date().isoformat(),
        warmup_to=warmup[-1].closed_at.date().isoformat(),
        parameters={k: str(v) for k, v in activation.parameters.items()},
    )

    node, _risk_engine = build_paper_node(
        session,
        market_data_type=MarketDataType.DELAYED,
        strategies=(strategy,),
    )
    # Captured before the run, for the same reason ``build_paper_node`` returns the risk
    # engine handle: a hosted run takes ownership of the node, and ``node.cache`` raises
    # once one is under way. Reaching for it mid-session is the mistake this line exists
    # to have made once.
    cache = node.cache
    handle = node.handle()
    task = asyncio.create_task(node.run_async())
    try:
        await asyncio.sleep(settle_secs)

        instrument = cache.instrument(broker_id)
        if instrument is None:
            raise NoBrokerInstrumentError(
                f"the broker did not resolve {broker_id} within {settle_secs}s. A bar "
                f"cannot be built without its instrument's precision; check the "
                f"instrument provider and the connection before reading anything else "
                f"into this session.",
            )
        record.instrument_resolved = True

        bar_type = broker_bar_type(broker_id)
        warm_bars, warm_rounding = to_broker_bars(warmup, instrument, bar_type)
        decision_bars, decision_rounding = to_broker_bars((decision,), instrument, bar_type)
        record.largest_rounding = str(max(warm_rounding, decision_rounding))

        strategy.warm_up(warm_bars)
        record.atr_initialized = bool(strategy._atr.initialized)

        strategy.on_bar(decision_bars[0])
        await asyncio.sleep(drain_secs)

        record.atr_value = str(strategy._atr.value)
        record.previous_close = str(strategy._previous_close)
        record.skips = dict(strategy.skips)
        record.orders = [
            OrderRecord(
                client_order_id=str(order.client_order_id),
                side=str(order.side),
                quantity=str(order.quantity),
                order_type=str(order.order_type),
                status=str(order.status),
                price=str(order.price) if hasattr(order, "price") else None,
            )
            for order in cache.orders()
        ]
    finally:
        handle.stop()
        try:
            await asyncio.wait_for(task, timeout=90)
        except (TimeoutError, asyncio.CancelledError):
            record.note = "node did not stop cleanly within 90s"
    return record


def _report(record: SessionRecord) -> None:
    """
    Print what the session decided, in the order an operator reads it.
    """
    print(f"\n  activation      {record.activation}")
    print(f"  instrument      {record.broker_instrument}  (research {record.research_instrument})")
    print(f"  warm-up         {record.warmup_bars} bars, {record.warmup_from}..{record.warmup_to}")
    print(f"  decision bar    {record.decision_bar}")
    print(f"  indicator       initialized={record.atr_initialized}  atr={record.atr_value}")
    print(f"  previous close  {record.previous_close}")
    if record.largest_rounding != "0":
        print(f"  rounding        {record.largest_rounding} to the broker's precision")
    if record.skips:
        for reason, count in sorted(record.skips.items()):
            print(f"  declined        {reason} x{count}")
    if record.orders:
        for order in record.orders:
            print(
                f"  ORDER           {order.side} {order.quantity} {order.order_type} "
                f"-> {order.status}",
            )
    else:
        print("  no order        the rule did not fire on this bar")
    if record.note:
        print(f"  note            {record.note}")


def main(argv: list[str] | None = None) -> int:
    """
    Run one activation for one session and file what it decided.
    """
    parser = argparse.ArgumentParser(
        prog="python -m copilot.live.run_activation",
        description="Run a registered activation on the paper broker, orders denied.",
    )
    parser.add_argument("activation", help="Activation name")
    parser.add_argument("--host", default=os.getenv("IB_V2_HOST", "172.17.112.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("IB_V2_PORT", "7497")))
    parser.add_argument("--account", default=os.getenv("COPILOT_PAPER_ACCOUNT", ""))
    parser.add_argument("--catalog", default=DEFAULT_CATALOG, help="Catalog directory")
    parser.add_argument("--session", help="Session to run for, YYYY-MM-DD (default: the next)")
    parser.add_argument("--settle-secs", type=int, default=SETTLE_SECS)
    parser.add_argument("--data-client-id", type=int, default=871)
    parser.add_argument("--exec-client-id", type=int, default=872)
    args = parser.parse_args(argv)

    if not args.account:
        print("error: no paper account; pass --account or set COPILOT_PAPER_ACCOUNT")
        return 2

    activation = find_activation(args.activation)
    first_session = (
        datetime.fromisoformat(args.session).date()
        if args.session
        else session_to_prepare(datetime.now(tz=UTC))
    )

    # The warm-up loader is used as the gate and not as the source. It refuses a stale or
    # holed window - the same refusal the operator's morning check makes, and the reason a
    # session cannot quietly warm across a missing day - but it returns bars built at the
    # catalog's precision, and what reaches the strategy has to be built at the broker's.
    load_warmup(args.catalog, activation, first_session=first_session)

    # The last bar before the session is the one the rule decides on; the warm-up is the
    # history behind it. Splitting them here rather than in the loader keeps the loader's
    # single job - is the catalog current enough - separate from this module's.
    catalog_bars = _catalog_bars(args.catalog, activation, first_session)
    needed = activation.setup.warmup_bars
    if len(catalog_bars) < needed + 1:
        print(f"error: {len(catalog_bars)} bars available, need {needed + 1}")
        return 2
    decision = catalog_bars[-1]
    warmup = catalog_bars[-(needed + 1) : -1]

    print(
        f"Running {activation.name} for the session opening {first_session}.\n"
        f"Orders are DENIED in the risk engine: this measures what the rule decides, "
        f"not what it fills. Enabling them is paper stage seven and needs a frozen "
        f"candidate.\n",
    )

    session = PaperSession(
        account_id=args.account,
        host=args.host,
        port=args.port,
        data_client_id=args.data_client_id,
        exec_client_id=args.exec_client_id,
        orders_enabled=False,
        instrument_ids=(str(broker_instrument_id(activation.symbol, activation.venue)),),
    )

    started = datetime.now(tz=UTC)
    record = asyncio.run(
        run_session(session, activation, warmup, decision, settle_secs=args.settle_secs),
    )
    _report(record)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"run_activation_{started.strftime('%Y%m%dT%H%M%SZ')}.json"
    payload = {
        "run_at": started.isoformat(),
        "session": first_session.isoformat(),
        "orders_enabled": False,
        **{k: v for k, v in vars(record).items() if k != "orders"},
        "orders": [vars(o) for o in record.orders],
    }
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n")
    print(f"\n  filed {path}")
    return 0 if record.instrument_resolved else 1


def _catalog_bars(catalog_path: str, activation: Activation, before: date) -> tuple[DailyBar, ...]:
    """
    Return the catalog's bars for this instrument, ending before ``before``.
    """
    from copilot.data.catalog import open_catalog  # noqa: PLC0415 - CLI path only
    from copilot.data.catalog import read_daily_bars  # noqa: PLC0415

    instrument = equity_for(activation.symbol, activation.venue)
    stored = read_daily_bars(open_catalog(catalog_path), bar_type_for(instrument.id))
    return tuple(bar for bar in stored if bar.closed_at.date() < before)


__all__ = [
    "DRAIN_SECS",
    "SETTLE_SECS",
    "NoBrokerInstrumentError",
    "OrderRecord",
    "SessionRecord",
    "broker_bar_type",
    "build_strategy",
    "run_session",
    "to_broker_bars",
]


if __name__ == "__main__":
    sys.exit(main())
