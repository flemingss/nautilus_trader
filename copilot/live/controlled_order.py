"""
Paper stage three: submit one controlled order through the strategy path, and cancel it.

    export IBAPI_TIMEZONE_ALIASES="JST=Asia/Tokyo"
    python -m copilot.live.controlled_order --account DUT067974 --reference-price 271.86

**This is the first code in the project that places an order.** Everything before it either
ran offline or ran with the risk engine halted.

What makes it controlled
------------------------
Four independent things, because one safeguard is a preference and four are a design:

1. **A limit far below the market.** The default reference is the instrument's last close
   from the research catalog, halved. A one-share buy at half price cannot transact against
   any plausible book, so the order can be acknowledged and cancelled without ever being
   fillable. The offset is deliberately not tighter: a limit a few percent away is a limit
   that fills on a bad morning.
2. **One share.** The smallest order the instrument admits.
3. **An engine-level notional cap.** ``max_notional_per_order`` is set on the risk engine,
   outside the strategy, so the strategy cannot relax its own limit. A submission above the
   cap is denied before it reaches the execution client.
4. **A fill is recorded as a failure.** :meth:`ControlledOrder.on_order_filled` marks the
   run failed. Nothing here should ever fill, so if something does, the run must say so
   rather than quietly report a successful lifecycle.

What the stage proves, and what it does not
--------------------------------------------
It proves the order path works end to end: the strategy submits, the broker acknowledges,
the identifiers come back, the cancel is acknowledged. It proves nothing whatever about
fill quality, edge, or the strategy that will eventually use this path - see
``docs/PAPER_CAMPAIGN.md``.
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
from nautilus_trader.model import TimeInForce
from nautilus_trader.trading import Strategy
from nautilus_trader.trading import StrategyConfig


OUT_DIR = Path(__file__).parent / "out"

LIMIT_FRACTION_OF_REFERENCE = Decimal("0.5")
"""
How far below the reference price the limit sits.

Half. Not a few percent: the point is that no plausible session can reach it, and a
near-the-money limit placed to be "realistic" is a limit that fills.

"""

MAX_NOTIONAL_MULTIPLE = 4
"""
Engine cap, as a multiple of the intended order's notional.

Loose enough not to deny the order this stage means to place, tight enough that a
quantity or price mistake of any size is stopped by configuration rather than by luck.

"""


@dataclass
class OrderEvent:
    """
    One thing the broker said about the order, in the order it said it.
    """

    at: str
    kind: str
    detail: str = ""


@dataclass
class Outcome:
    """
    What the stage observed, and whether it passed.
    """

    submitted: bool = False
    accepted: bool = False
    canceled: bool = False
    filled: bool = False
    rejected: bool = False
    denied: bool = False
    client_order_id: str = ""
    venue_order_id: str = ""
    events: list[OrderEvent] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """A clean lifecycle: acknowledged, cancelled, never filled, never refused."""
        return (
            self.submitted
            and self.accepted
            and self.canceled
            and not self.filled
            and not self.rejected
            and not self.denied
        )


class ControlledOrderConfig(StrategyConfig):
    """
    Knobs for the single order.

    ``StrategyConfig`` is a pyo3 class, so custom fields follow the ``_CUSTOM_FIELDS``
    plus ``__new__`` pattern the rest of the overlay uses.

    """

    _CUSTOM_FIELDS = ("instrument_id", "limit_price", "quantity")

    def __new__(cls, *args: object, **kwargs: object):  # noqa: ANN204 - pyo3 base
        """
        Strip the custom fields before the pyo3 base sees them.
        """
        for field_name in cls._CUSTOM_FIELDS:
            kwargs.pop(field_name, None)
        return super().__new__(cls, *args, **kwargs)

    def __init__(
        self,
        instrument_id: Any,
        limit_price: str,
        *,
        quantity: int = 1,
        **_kwargs: object,
    ) -> None:
        """
        Configure the one order this stage places.
        """
        super().__init__()
        self.instrument_id = instrument_id
        self.limit_price = limit_price
        self.quantity = quantity


class ControlledOrder(Strategy):
    """
    Submits one limit order on start, cancels it the moment the broker accepts it.

    Cancelling from ``on_order_accepted`` rather than after a fixed wait is deliberate:
    the thing being tested is the acknowledgement round trip, so the cancel should be
    driven by the acknowledgement rather than by a timer that might fire before it.

    """

    def __init__(self, config: ControlledOrderConfig) -> None:
        """
        Start with an empty outcome; every observation is appended as it arrives.
        """
        super().__init__(config)
        self.outcome = Outcome()

    def _record(self, kind: str, detail: str = "") -> None:
        self.outcome.events.append(
            OrderEvent(at=datetime.now(tz=UTC).isoformat(), kind=kind, detail=detail),
        )

    def on_start(self) -> None:
        """
        Submit the single order.
        """
        instrument = self.cache.instrument(self.config.instrument_id)
        if instrument is None:
            self._record("no_instrument", str(self.config.instrument_id))
            return

        order = self.order_factory.limit(
            instrument_id=self.config.instrument_id,
            order_side=OrderSide.BUY,
            quantity=instrument.make_qty(Decimal(self.config.quantity)),
            price=instrument.make_price(Decimal(self.config.limit_price)),
            time_in_force=TimeInForce.GTC,
        )
        self.outcome.client_order_id = str(order.client_order_id)
        self.outcome.submitted = True
        self._record("submitted", f"{order.client_order_id} at {self.config.limit_price}")
        self.submit_order(order)

    def on_order_accepted(self, event: Any) -> None:
        """
        Cancel immediately: this acknowledgement is what the stage exists to observe.

        """
        self.outcome.accepted = True
        self.outcome.venue_order_id = str(getattr(event, "venue_order_id", ""))
        self._record("accepted", self.outcome.venue_order_id)
        # `Strategy.cancel_order` takes a ClientOrderId, not an Order - unlike
        # `ExecutionAlgorithm.cancel_order`, which takes the order. Passing the order
        # here did nothing visible: no cancel, no error, and a working order left at the
        # broker after the node stopped.
        self.cancel_order(event.client_order_id)

    def on_order_canceled(self, event: Any) -> None:
        """
        Record the other half of the round trip.
        """
        self.outcome.canceled = True
        self._record("canceled", str(event.client_order_id))

    def on_order_rejected(self, event: Any) -> None:
        """
        Record a refusal: the stage wants an acknowledged order, not a rejected one.
        """
        self.outcome.rejected = True
        self._record("rejected", str(getattr(event, "reason", "")))

    def on_order_denied(self, event: Any) -> None:
        """
        Record a denial made inside the risk engine, before the broker saw the order.
        """
        self.outcome.denied = True
        self._record("denied", str(getattr(event, "reason", "")))

    def on_order_filled(self, event: Any) -> None:
        """
        Must never happen.

        Recorded loudly rather than counted as a clean lifecycle.

        """
        self.outcome.filled = True
        self._record("FILLED", f"unexpected fill: {event}")
        self.log.error(f"Controlled order filled, which should be impossible: {event}")


async def run_controlled_order(
    session: PaperSession,
    *,
    instrument_id: Any,
    limit_price: Decimal,
    settle_secs: int,
) -> Outcome:
    """
    Run the node just long enough to place and cancel one order.
    """
    notional = limit_price * MAX_NOTIONAL_MULTIPLE
    strategy = ControlledOrder(
        ControlledOrderConfig(instrument_id=instrument_id, limit_price=str(limit_price)),
    )
    node, _risk_engine = build_paper_node(
        session,
        market_data_type=MarketDataType.DELAYED,
        strategies=(strategy,),
        risk_engine_config=LiveRiskEngineConfig(
            max_notional_per_order={str(instrument_id): str(notional)},
        ),
    )

    handle = node.handle()
    task = asyncio.create_task(node.run_async())
    try:
        await asyncio.sleep(settle_secs)
    finally:
        handle.stop()
        try:
            await asyncio.wait_for(task, timeout=60)
        except (TimeoutError, asyncio.CancelledError):
            strategy.outcome.events.append(
                OrderEvent(at=datetime.now(tz=UTC).isoformat(), kind="unclean_shutdown"),
            )
    return strategy.outcome


def main(argv: list[str] | None = None) -> int:
    """
    Place and cancel one controlled order.

    Non-zero exit if the lifecycle was not clean.

    """
    parser = argparse.ArgumentParser(
        prog="python -m copilot.live.controlled_order",
        description="Paper stage three: one controlled order, submitted and cancelled.",
    )
    parser.add_argument("--host", default=os.getenv("IB_V2_HOST", "172.17.112.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("IB_V2_PORT", "7497")))
    parser.add_argument("--account", default=os.getenv("COPILOT_PAPER_ACCOUNT", ""))
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument("--venue", default="XNAS", help="Research (MIC) venue; mapped to IB")
    parser.add_argument("--reference-price", required=True, help="Last known price, for the offset")
    parser.add_argument("--settle-secs", type=int, default=30)
    parser.add_argument("--data-client-id", type=int, default=811)
    parser.add_argument("--exec-client-id", type=int, default=812)
    args = parser.parse_args(argv)

    if not args.account:
        print("error: no paper account; pass --account or set COPILOT_PAPER_ACCOUNT")
        return 2

    instrument_id = broker_instrument_id(args.symbol, args.venue)
    limit_price = (Decimal(args.reference_price) * LIMIT_FRACTION_OF_REFERENCE).quantize(
        Decimal("0.01"),
    )

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
    print(
        f"Placing 1 {instrument_id} BUY LIMIT at {limit_price} (reference {args.reference_price})",
    )
    outcome = asyncio.run(
        run_controlled_order(
            session,
            instrument_id=instrument_id,
            limit_price=limit_price,
            settle_secs=args.settle_secs,
        ),
    )

    print(f"\n{'event':<20}detail")
    for e in outcome.events:
        print(f"{e.kind:<20}{e.detail[:90]}")
    print(f"\nclient_order_id: {outcome.client_order_id or '-'}")
    print(f"venue_order_id:  {outcome.venue_order_id or '-'}")
    print(f"\nRESULT: {'PASS' if outcome.passed else 'FAIL'}")

    report = {
        "started_at": started.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "stage": "3",
        "source": f"interactive_brokers {args.host}:{args.port}",
        "instrument_id": str(instrument_id),
        "limit_price": str(limit_price),
        "reference_price": args.reference_price,
        "quantity": 1,
        "max_notional_per_order": str(limit_price * MAX_NOTIONAL_MULTIPLE),
        "passed": outcome.passed,
        "outcome": asdict(outcome),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"controlled_order_{started.strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Wrote {path}")
    return 0 if outcome.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
