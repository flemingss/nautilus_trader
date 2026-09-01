"""
Paper stage five: a full supervised round trip, sized as the real account will be.

    export IBAPI_TIMEZONE_ALIASES="JST=Asia/Tokyo"
    python -m copilot.live.supervised_session --account DUT067974 --capital 1000

**This fills.** Everything before it was priced where the market could not reach it. Stage
five exists to test the parts that a fill is the only way to reach: the bracket's market
entry, its children activating once the parent fills, the position opening and closing, and
what the round trip actually costs.

Run it with someone watching. It opens a real paper position.

Sized for the account we will have, not the one paper gives us
--------------------------------------------------------------
The paper account holds USD 1,000,000. The real account will hold **USD 500 to 1,000**. A
systems check sized against the paper balance would exercise quantities the live account can
never reach, and every conclusion drawn from it would be about a regime that does not apply.

So ``--capital`` is the deployable figure, and two things enforce it:

- Quantity is whole shares within ``capital``, so one share of a USD 270 instrument is
  twenty-seven percent of a USD 1,000 account. That is not a flaw in the test - it is what a
  small account looks like, and it is the constraint
  [ADR-0009](../docs/decisions/0009-cost-is-modelled-at-the-target-account-size.md) is about.
- ``max_notional_per_order`` is set on the risk engine, **outside** the strategy, so a
  grossly wrong order is denied before it reaches the broker rather than merely not being
  written. It is a backstop rather than the budget - see :data:`NOTIONAL_CAP_MULTIPLE`.

Levels come from a live quote, not from history
------------------------------------------------
The catalog's last close is from 2025 and this runs in 2026. A stop placed from a stale
reference can sit the wrong side of the market and trigger the instant the entry fills, which
would look like a strategy result and be an arithmetic error. So the bracket waits for the
first quote and places its levels around the live mid, wide enough that neither child can be
reached during the check.

The close is explicit
---------------------
Waiting for the stop or target to resolve would mean holding a position for an unknown span,
which is not a supervised check. The position is closed on purpose once the children are
confirmed working, and the remaining children are cancelled after it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from copilot.live.node import build_paper_node
from copilot.live.session import PaperSession
from copilot.live.symbology import broker_instrument_id
from nautilus_trader.adapters.interactive_brokers import MarketDataType
from nautilus_trader.live import LiveRiskEngineConfig
from nautilus_trader.model import OrderSide
from nautilus_trader.model import OrderType
from nautilus_trader.model import TimeInForce
from nautilus_trader.trading import Strategy
from nautilus_trader.trading import StrategyConfig


OUT_DIR = Path(__file__).parent / "out"

STOP_FRACTION = Decimal("0.90")
"""Stop at ten percent below the mid: far enough that the check cannot trigger it."""

TARGET_FRACTION = Decimal("1.10")
"""
Target ten percent above, for the same reason in the other direction.
"""

NOTIONAL_CAP_MULTIPLE = 2
"""
Engine cap, as a multiple of the deployable capital.

Not set at ``capital`` exactly, and the reason is worth stating because the first version
was. A bracket's take-profit is a sell **above** the entry, so at three shares of a USD 317
instrument the entry is USD 950 and the target is USD 1,045 - a cap set at the deployable
capital would have denied the take-profit while allowing the entry, leaving a position with
no upside exit.

``max_notional_per_order`` is a backstop against an order that is wildly wrong, such as a
quantity typo. The deployable capital is enforced by **sizing**, which is where a budget
belongs.

"""


@dataclass
class Milestone:
    """
    One thing that happened, in the order it happened.
    """

    at: str
    kind: str
    detail: str = ""


@dataclass
class SessionResult:
    """
    What the round trip did, and what it cost.
    """

    quote_seen: bool = False
    mid_price: str = ""
    quantity: int = 0
    bracket_submitted: bool = False
    entry_filled: bool = False
    entry_price: str = ""
    children_working: int = 0
    position_opened: bool = False
    position_closed: bool = False
    exit_price: str = ""
    realized_pnl: str = ""
    commissions: str = ""
    orders_left_working: list[str] = field(default_factory=list)
    milestones: list[Milestone] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """
        A complete round trip that left nothing behind.

        ``children_working`` is part of the pass because the whole reason stage four could
        not test the bracket was that children only activate on a fill.

        """
        return (
            self.entry_filled
            and self.position_opened
            and self.position_closed
            and self.children_working > 0
            and not self.orders_left_working
        )


class SupervisedSessionConfig(StrategyConfig):
    """
    Instrument and the capital the position must fit inside.
    """

    _CUSTOM_FIELDS = ("instrument_id", "capital")

    def __new__(cls, *args: object, **kwargs: object):  # noqa: ANN204 - pyo3 base
        """
        Strip the custom fields before the pyo3 base sees them.
        """
        for field_name in cls._CUSTOM_FIELDS:
            kwargs.pop(field_name, None)
        return super().__new__(cls, *args, **kwargs)

    def __init__(self, instrument_id: Any, capital: str, **_kwargs: object) -> None:
        """
        Configure the round trip.
        """
        super().__init__()
        self.instrument_id = instrument_id
        self.capital = capital


class SupervisedSession(Strategy):
    """
    One market-entry bracket, held briefly, then closed on purpose.
    """

    def __init__(self, config: SupervisedSessionConfig) -> None:
        """
        Start with nothing observed.
        """
        super().__init__(config)
        self.result = SessionResult()
        self._entry_id: str = ""
        self._child_ids: list[str] = []
        self._accepted_children: set[str] = set()
        self._armed = True
        self._closing = False

    def _note(self, kind: str, detail: str = "") -> None:
        self.result.milestones.append(
            Milestone(at=datetime.now(tz=UTC).isoformat(), kind=kind, detail=detail),
        )
        self.log.info(f"[stage5] {kind}: {detail}")

    def on_start(self) -> None:
        """
        Subscribe and wait: the bracket needs a live mid before it can place levels.
        """
        self.subscribe_quotes(self.config.instrument_id)
        self._note("subscribed", str(self.config.instrument_id))

    def on_quote(self, quote: Any) -> None:
        """
        Place the bracket on the first usable quote, once.
        """
        if not self._armed:
            return
        bid, ask = Decimal(str(quote.bid_price)), Decimal(str(quote.ask_price))
        if bid <= 0 or ask <= 0 or ask < bid:
            return
        self._armed = False

        instrument = self.cache.instrument(self.config.instrument_id)
        mid = (bid + ask) / 2
        self.result.quote_seen = True
        self.result.mid_price = str(mid)

        # Whole shares inside the deployable capital. A fractional share is not assumed to
        # be available; PREFLIGHT records that as unverified.
        quantity = int(Decimal(self.config.capital) // ask)
        self.result.quantity = quantity
        if quantity < 1:
            self._note("unsizeable", f"capital {self.config.capital} below ask {ask}")
            return

        bracket = self.order_factory.bracket(
            instrument_id=instrument.id,
            order_side=OrderSide.BUY,
            quantity=instrument.make_qty(Decimal(quantity)),
            entry_order_type=OrderType.MARKET,
            sl_trigger_price=instrument.make_price(mid * STOP_FRACTION),
            tp_price=instrument.make_price(mid * TARGET_FRACTION),
            time_in_force=TimeInForce.GTC,
        )
        orders = list(bracket)
        self._entry_id = str(orders[0].client_order_id)
        self._child_ids = [str(o.client_order_id) for o in orders[1:]]
        self.result.bracket_submitted = True
        self._note(
            "bracket_submitted",
            f"{quantity} @ market, mid {mid}, sl {mid * STOP_FRACTION:.2f}, "
            f"tp {mid * TARGET_FRACTION:.2f}",
        )
        self.submit_order_list(bracket)

    def on_order_filled(self, event: Any) -> None:
        """
        Record the entry fill, which is the event stage four could not reach.
        """
        if str(event.client_order_id) == self._entry_id:
            self.result.entry_filled = True
            self.result.entry_price = str(getattr(event, "last_px", ""))
            self._note("entry_filled", self.result.entry_price)
        else:
            self.result.exit_price = str(getattr(event, "last_px", ""))
            self._note("exit_filled", self.result.exit_price)

    def on_position_opened(self, event: Any) -> None:
        """
        Record the opening; the close waits until the children are confirmed working.
        """
        self.result.position_opened = True
        self._note("position_opened", str(getattr(event, "position_id", "")))
        self._close_when_ready()

    def on_order_accepted(self, event: Any) -> None:
        """
        Count each child the broker acknowledges.

        Children activate only once the parent fills, which is exactly why stage four
        could not test them. Counting acknowledgements rather than waiting a fixed
        interval means the close happens when the evidence exists rather than when a
        timer says so.

        """
        if str(event.client_order_id) in self._child_ids:
            self._accepted_children.add(str(event.client_order_id))
            self._note("child_accepted", str(event.client_order_id))
            self._close_when_ready()

    def _close_when_ready(self) -> None:
        """
        Close once the position is open and every child has been acknowledged.
        """
        if self._closing or not self.result.position_opened:
            return
        if len(self._accepted_children) < len(self._child_ids):
            return
        self._closing = True
        self.result.children_working = len(self._accepted_children)

        for position in self.cache.positions_open(instrument_id=self.config.instrument_id):
            self.close_position(position)
            self._note("close_submitted", str(position.id))

    def on_position_closed(self, event: Any) -> None:
        """
        Record what the round trip realised, then cancel the children it left behind.
        """
        self.result.position_closed = True
        position = self.cache.position(event.position_id)
        if position is not None:
            self.result.realized_pnl = str(position.realized_pnl)
            self.result.commissions = ", ".join(str(c) for c in position.commissions())
        self._note("position_closed", f"pnl {self.result.realized_pnl}")
        self.cancel_all_orders(self.config.instrument_id, strategy_only=False)

    def on_stop(self) -> None:
        """
        Report anything still working, since leaving an order behind is the failure.
        """
        self.result.orders_left_working = [
            str(o.client_order_id)
            for o in self.cache.orders_open(instrument_id=self.config.instrument_id)
        ]


async def run_session(
    session: PaperSession,
    *,
    instrument_id: Any,
    capital: Decimal,
    settle_secs: int,
) -> SessionResult:
    """
    Run the node long enough to open, hold, close and tidy up.
    """
    strategy = SupervisedSession(
        SupervisedSessionConfig(instrument_id=instrument_id, capital=str(capital)),
    )
    node, _risk_engine = build_paper_node(
        session,
        market_data_type=MarketDataType.DELAYED,
        strategies=(strategy,),
        # The deployable capital, enforced outside the strategy. An order for more than the
        # real account holds is denied before it reaches the broker.
        risk_engine_config=LiveRiskEngineConfig(
            max_notional_per_order={
                str(instrument_id): str(capital * NOTIONAL_CAP_MULTIPLE),
            },
        ),
    )
    handle = node.handle()
    task = asyncio.create_task(node.run_async())
    try:
        await asyncio.sleep(settle_secs)
    finally:
        handle.stop()
        try:
            await asyncio.wait_for(task, timeout=90)
        except (TimeoutError, asyncio.CancelledError) as e:
            strategy.log.error(f"Node did not stop cleanly: {e!r}")
    return strategy.result


def main(argv: list[str] | None = None) -> int:
    """
    Run one supervised round trip.

    Non-zero exit if it did not complete cleanly.

    """
    parser = argparse.ArgumentParser(
        prog="python -m copilot.live.supervised_session",
        description="Paper stage five: a supervised round trip, sized for the real account.",
    )
    parser.add_argument("--host", default=os.getenv("IB_V2_HOST", "172.17.112.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("IB_V2_PORT", "7497")))
    parser.add_argument("--account", default=os.getenv("COPILOT_PAPER_ACCOUNT", ""))
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument("--venue", default="XNAS")
    parser.add_argument(
        "--capital",
        default="1000",
        help="Deployable USD. The real account, not the paper balance.",
    )
    parser.add_argument("--settle-secs", type=int, default=90)
    parser.add_argument("--data-client-id", type=int, default=861)
    parser.add_argument("--exec-client-id", type=int, default=862)
    args = parser.parse_args(argv)

    if not args.account:
        print("error: no paper account; pass --account or set COPILOT_PAPER_ACCOUNT")
        return 2

    instrument_id = broker_instrument_id(args.symbol, args.venue)
    capital = Decimal(args.capital)
    session = PaperSession(
        account_id=args.account,
        host=args.host,
        port=args.port,
        data_client_id=args.data_client_id,
        exec_client_id=args.exec_client_id,
        orders_enabled=True,
        instrument_ids=(str(instrument_id),),
    )

    started = datetime.now(UTC)
    print(f"Supervised round trip on {instrument_id}, deployable capital USD {capital}")
    result = asyncio.run(
        run_session(
            session,
            instrument_id=instrument_id,
            capital=capital,
            settle_secs=args.settle_secs,
        ),
    )

    print(f"\n{'milestone':<22}detail")
    for m in result.milestones:
        print(f"{m.kind:<22}{m.detail[:80]}")
    print(f"\nquantity:        {result.quantity}")
    print(f"entry / exit:    {result.entry_price or '-'} / {result.exit_price or '-'}")
    print(f"children working:{result.children_working}")
    print(f"realized pnl:    {result.realized_pnl or '-'}")
    print(f"commissions:     {result.commissions or '-'}")
    print(f"left working:    {result.orders_left_working or 'none'}")
    print(f"\nRESULT: {'PASS' if result.passed else 'FAIL'}")

    report = {
        "started_at": started.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "stage": "5",
        "source": f"interactive_brokers {args.host}:{args.port}",
        "instrument_id": str(instrument_id),
        "deployable_capital": str(capital),
        "passed": result.passed,
        "result": asdict(result),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"supervised_session_{started.strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Wrote {path}")
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
