"""
Paper stage four: submit every planned order type and time in force, and cancel each.

    export IBAPI_TIMEZONE_ALIASES="JST=Asia/Tokyo"
    python -m copilot.live.order_types --account DUT067974 --reference-price 271.86

Stage three proved one order type works. This proves the ones the strategies will actually
use are accepted by the broker in the shapes we intend to send them, before a session
depends on it.

Nothing here may fill
---------------------
Every price is placed where the market cannot reach it: buy limits at half the reference,
buy stops at twice it. That is what makes the stage repeatable and unsupervised-safe, and
it is also the stage's boundary - **submission and acknowledgement are tested, execution is
not**.

The MARKET entry is deliberately absent
----------------------------------------
The gap fade's bracket uses ``entry_order_type=OrderType.MARKET``, which is the single most
important type in the system and **cannot be tested without filling**. There is no
far-from-market price for a market order. Submitting one would open a real paper position,
and the previous stage established that this project cannot reliably clean up after itself
yet: an external order in ``SUBMITTED`` status is invisible to reconciliation and
uncancellable.

So the market path belongs to stage five, under supervision, where a position that needs
closing is expected rather than a surprise. It is recorded here as untested rather than
skipped silently, because a matrix with a hole in it is only useful if the hole is visible.

The bracket is tested with a limit entry
-----------------------------------------
Which tests the shape - parent plus two contingent children, submitted as one list and
cancelled as one - but not child activation, since children activate on the parent filling.
That too waits for stage five.
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

FAR_BELOW = Decimal("0.5")
"""Buy limits sit here: half the reference. Unreachable, not merely unlikely."""

FAR_ABOVE = Decimal(2)
"""Buy stop triggers sit here: twice the reference, so nothing triggers."""

MAX_NOTIONAL_MULTIPLE = 4

UNTESTED_HERE = {
    "MARKET": (
        "No far-from-market price exists for a market order, so it cannot be submitted "
        "without filling. Deferred to stage five under supervision."
    ),
    "BRACKET_MARKET_ENTRY": (
        "The gap fade's real entry shape. Same reason as MARKET, and child activation "
        "needs the parent to fill. Deferred to stage five under supervision."
    ),
}
"""
Planned shapes this stage cannot reach, and why.

Recorded rather than omitted: a matrix with an invisible hole reads as complete.

"""


@dataclass
class Attempt:
    """
    One order shape, and everything the broker said about it.
    """

    name: str
    submitted: bool = False
    accepted: bool = False
    canceled: bool = False
    filled: bool = False
    rejected: bool = False
    denied: bool = False
    detail: str = ""
    client_order_ids: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """
        Acknowledged and cancelled, never filled, never refused.
        """
        return (
            self.submitted
            and self.accepted
            and self.canceled
            and not self.filled
            and not self.rejected
            and not self.denied
        )


class OrderTypesConfig(StrategyConfig):
    """
    Instrument and the prices every shape is derived from.
    """

    _CUSTOM_FIELDS = ("instrument_id", "reference_price")

    def __new__(cls, *args: object, **kwargs: object):  # noqa: ANN204 - pyo3 base
        """
        Strip the custom fields before the pyo3 base sees them.
        """
        for field_name in cls._CUSTOM_FIELDS:
            kwargs.pop(field_name, None)
        return super().__new__(cls, *args, **kwargs)

    def __init__(self, instrument_id: Any, reference_price: str, **_kwargs: object) -> None:
        """
        Configure the matrix.
        """
        super().__init__()
        self.instrument_id = instrument_id
        self.reference_price = reference_price


class OrderTypes(Strategy):
    """
    Submits one order of each planned shape, cancels each on acknowledgement.

    All shapes go out together rather than in sequence. A sequential version would stop
    at the first refusal and report nothing about the rest, and the point of a matrix is
    to learn every cell in one session.

    """

    def __init__(self, config: OrderTypesConfig) -> None:
        """
        Start with no attempts; each is registered as it is submitted.
        """
        super().__init__(config)
        self.attempts: dict[str, Attempt] = {}
        self._by_order: dict[str, str] = {}

    def _attempt(self, name: str) -> Attempt:
        return self.attempts.setdefault(name, Attempt(name=name))

    def _register(self, name: str, orders: list[Any]) -> None:
        attempt = self._attempt(name)
        attempt.submitted = True
        for order in orders:
            client_order_id = str(order.client_order_id)
            attempt.client_order_ids.append(client_order_id)
            self._by_order[client_order_id] = name

    def on_start(self) -> None:
        """
        Build and submit every shape.
        """
        instrument = self.cache.instrument(self.config.instrument_id)
        if instrument is None:
            self._attempt("NO_INSTRUMENT").detail = str(self.config.instrument_id)
            return

        reference = Decimal(self.config.reference_price)
        below = instrument.make_price(reference * FAR_BELOW)
        far_below = instrument.make_price(reference * FAR_BELOW * Decimal("0.9"))
        above = instrument.make_price(reference * FAR_ABOVE)
        one = instrument.make_qty(Decimal(1))
        factory = self.order_factory

        singles = {
            "LIMIT/GTC": factory.limit(
                instrument_id=instrument.id,
                order_side=OrderSide.BUY,
                quantity=one,
                price=below,
                time_in_force=TimeInForce.GTC,
            ),
            "LIMIT/DAY": factory.limit(
                instrument_id=instrument.id,
                order_side=OrderSide.BUY,
                quantity=one,
                price=below,
                time_in_force=TimeInForce.DAY,
            ),
            "STOP_MARKET/GTC": factory.stop_market(
                instrument_id=instrument.id,
                order_side=OrderSide.BUY,
                quantity=one,
                trigger_price=above,
                time_in_force=TimeInForce.GTC,
            ),
            "STOP_LIMIT/GTC": factory.stop_limit(
                instrument_id=instrument.id,
                order_side=OrderSide.BUY,
                quantity=one,
                price=above,
                trigger_price=above,
                time_in_force=TimeInForce.GTC,
            ),
        }

        # The gap fade's shape, with a limit entry standing in for the market entry it
        # actually uses. Tests that parent and children are accepted as one list; does
        # not test child activation, which needs a fill.
        bracket = factory.bracket(
            instrument_id=instrument.id,
            order_side=OrderSide.BUY,
            quantity=one,
            entry_order_type=OrderType.LIMIT,
            entry_price=below,
            sl_trigger_price=far_below,
            tp_price=above,
            time_in_force=TimeInForce.GTC,
        )

        # Everything is constructed before anything is sent. The first version submitted
        # the singles and then raised while building the bracket, which aborted node
        # startup with four orders already on their way - a half-submitted matrix and no
        # strategy left running to cancel it.
        for name, order in singles.items():
            self._register(name, [order])
            self.submit_order(order)

        self._register("BRACKET_LIMIT_ENTRY/GTC", list(bracket))
        self.submit_order_list(bracket)

    def _mark(self, event: Any, attribute: str, detail: str = "") -> Attempt | None:
        name = self._by_order.get(str(event.client_order_id))
        if name is None:
            return None
        attempt = self._attempt(name)
        setattr(attempt, attribute, True)
        if detail:
            attempt.detail = detail
        return attempt

    def on_order_accepted(self, event: Any) -> None:
        """
        Cancel on acknowledgement, which is the event the stage is measuring.
        """
        if self._mark(event, "accepted") is not None:
            self.cancel_order(event.client_order_id)

    def on_order_canceled(self, event: Any) -> None:
        """
        Record the other half of the round trip.
        """
        self._mark(event, "canceled")

    def on_order_rejected(self, event: Any) -> None:
        """
        Record a broker refusal, with its reason - the reason is the finding.
        """
        self._mark(event, "rejected", str(getattr(event, "reason", "")))

    def on_order_denied(self, event: Any) -> None:
        """
        Record a denial made inside the risk engine, before the broker saw the order.
        """
        self._mark(event, "denied", str(getattr(event, "reason", "")))

    def on_order_filled(self, event: Any) -> None:
        """
        Record a fill loudly.

        Every price here is unreachable, so this must not happen.

        """
        self._mark(event, "filled", "unexpected fill")
        self.log.error(f"Order filled at an unreachable price: {event}")


async def run_matrix(
    session: PaperSession,
    *,
    instrument_id: Any,
    reference_price: Decimal,
    settle_secs: int,
) -> dict[str, Attempt]:
    """
    Run the node long enough to submit every shape and hear back on each.
    """
    strategy = OrderTypes(
        OrderTypesConfig(instrument_id=instrument_id, reference_price=str(reference_price)),
    )
    cap = reference_price * FAR_ABOVE * MAX_NOTIONAL_MULTIPLE
    node, _risk_engine = build_paper_node(
        session,
        market_data_type=MarketDataType.DELAYED,
        strategies=(strategy,),
        risk_engine_config=LiveRiskEngineConfig(
            max_notional_per_order={str(instrument_id): str(cap)},
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
        except (TimeoutError, asyncio.CancelledError) as e:
            strategy.log.error(f"Node did not stop cleanly: {e!r}")
    return strategy.attempts


def main(argv: list[str] | None = None) -> int:
    """
    Run the order-type matrix.

    Non-zero exit if any shape did not round trip cleanly.

    """
    parser = argparse.ArgumentParser(
        prog="python -m copilot.live.order_types",
        description="Paper stage four: every planned order type and time in force.",
    )
    parser.add_argument("--host", default=os.getenv("IB_V2_HOST", "172.17.112.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("IB_V2_PORT", "7497")))
    parser.add_argument("--account", default=os.getenv("COPILOT_PAPER_ACCOUNT", ""))
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument("--venue", default="XNAS")
    parser.add_argument("--reference-price", required=True)
    parser.add_argument("--settle-secs", type=int, default=45)
    parser.add_argument("--data-client-id", type=int, default=841)
    parser.add_argument("--exec-client-id", type=int, default=842)
    args = parser.parse_args(argv)

    if not args.account:
        print("error: no paper account; pass --account or set COPILOT_PAPER_ACCOUNT")
        return 2

    instrument_id = broker_instrument_id(args.symbol, args.venue)
    reference = Decimal(args.reference_price)
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
    print(f"Submitting the order-type matrix on {instrument_id}, reference {reference}")
    attempts = asyncio.run(
        run_matrix(
            session,
            instrument_id=instrument_id,
            reference_price=reference,
            settle_secs=args.settle_secs,
        ),
    )

    print(f"\n{'shape':<26}{'result':<8}{'orders':<8}detail")
    for name, a in sorted(attempts.items()):
        print(
            f"{name:<26}{'PASS' if a.passed else 'FAIL':<8}"
            f"{len(a.client_order_ids):<8}{a.detail[:60]}",
        )
    for name, why in UNTESTED_HERE.items():
        print(f"{name:<26}{'N/A':<8}{'-':<8}{why[:60]}")

    failed = sorted(n for n, a in attempts.items() if not a.passed)
    report = {
        "started_at": started.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "stage": "4",
        "source": f"interactive_brokers {args.host}:{args.port}",
        "instrument_id": str(instrument_id),
        "reference_price": str(reference),
        "passed": not failed,
        "failed_shapes": failed,
        "untested_here": UNTESTED_HERE,
        "attempts": {n: asdict(a) for n, a in sorted(attempts.items())},
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"order_types_{started.strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(report, indent=2) + "\n")
    print(f"\nRESULT: {'PASS' if not failed else 'FAIL ' + ', '.join(failed)}")
    print(f"Wrote {path}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
