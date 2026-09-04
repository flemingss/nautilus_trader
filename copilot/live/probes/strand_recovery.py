"""
Confirm unknown-working-order recovery against the broker, by stranding one on purpose.

    export IBAPI_TIMEZONE_ALIASES="JST=Asia/Tokyo"
    python -m copilot.live.probes.strand_recovery --account DUT067974 --reference-price 326

The engine fix this confirms landed 2026-09-01: reconciliation adopts an external order
reported as ``SUBMITTED`` (`crates/execution/src/reconciliation/orders.rs`), and the
execution client sets ``fetch_all_open_orders=True`` so ``reqOpenOrders`` is not blind to
other client ids. Both carry Rust tests; neither had ever been watched working at the
broker, because watching it requires stranding a live order - which is what this does,
deliberately and at the smallest size that asks the question.

The three phases
----------------
1. **Strand.** A node on one pair of client ids submits a one-share BUY LIMIT GTC at half
   the reference price - unfillable against any plausible book - waits for the broker's
   acceptance, and stops **without cancelling**. This is stage three's accident, made on
   purpose.
2. **Recover.** A fresh node on *different* client ids starts with an empty cache, so the
   order can only reach it through reconciliation. The claim under test is that it now
   does: before the fix it was logged as *"Unhandled order status SUBMITTED for external
   order"* and existed at the broker and nowhere else.
3. **Sweep.** The same fresh node cancels everything working and waits for the broker's
   acknowledgements. A cancel sent from a client id that did not place the order is the
   part no unit test can decide - IB binds orders to their originating client id, and
   whether TWS honours a foreign cancel here is exactly what this phase observes.

A failure in phase 3 with a success in phase 2 is a real and useful verdict: adoption
works, remote cancel does not, and the operator cancels by hand in TWS. The record says
which phase failed rather than collapsing them into one bit.

What this does not prove
------------------------
The stranded order is acknowledged and working at TWS. An order held unsent by a TWS
precautionary size setting never reaches the broker at all and is invisible to every API
call; no probe of this shape can reach that state, and the one-share size is chosen to
stay far below those thresholds. Eyeball the TWS order list at the end regardless - per
``OPERATIONS.md``, a clean sweep is not proof the broker has nothing working.

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
"""Same offset as the controlled order: no plausible session reaches half price."""

MAX_NOTIONAL_MULTIPLE = 4
"""
Engine cap on the strand node, so a price or quantity mistake stops at configuration.
"""


@dataclass
class PhaseEvent:
    """
    One observation, in the order it arrived.
    """

    at: str
    phase: str
    kind: str
    detail: str = ""


@dataclass
class Outcome:
    """
    What each phase observed, kept separate so a mixed verdict stays readable.
    """

    stranded_client_order_id: str = ""
    stranded_venue_order_id: str = ""
    strand_accepted: bool = False
    strand_filled: bool = False
    adopted_open_orders: list[str] = field(default_factory=list)
    adopted: bool = False
    cancel_acknowledged: bool = False
    left_open: list[str] = field(default_factory=list)
    events: list[PhaseEvent] = field(default_factory=list)

    def record(self, phase: str, kind: str, detail: str = "") -> None:
        """
        Append one observation.
        """
        self.events.append(
            PhaseEvent(at=datetime.now(tz=UTC).isoformat(), phase=phase, kind=kind, detail=detail),
        )

    @property
    def passed(self) -> bool:
        """
        Stranded, adopted by a stranger's reconciliation, cancelled, nothing left.
        """
        return (
            self.strand_accepted
            and not self.strand_filled
            and self.adopted
            and self.cancel_acknowledged
            and not self.left_open
        )


class StrandConfig(StrategyConfig):
    """
    The one order phase 1 places and abandons.
    """

    _CUSTOM_FIELDS = ("instrument_id", "limit_price")

    def __new__(cls, *args: object, **kwargs: object):  # noqa: ANN204 - pyo3 base
        """
        Strip the custom fields before the pyo3 base sees them.
        """
        for field_name in cls._CUSTOM_FIELDS:
            kwargs.pop(field_name, None)
        return super().__new__(cls, *args, **kwargs)

    def __init__(self, instrument_id: Any, limit_price: str, **_kwargs: object) -> None:
        """
        Configure the strand.
        """
        super().__init__()
        self.instrument_id = instrument_id
        self.limit_price = limit_price


class StrandOrder(Strategy):
    """
    Submits one far-from-market limit and then deliberately does nothing.

    The absence of a cancel is the entire point; see the module docstring.

    """

    def __init__(self, config: StrandConfig) -> None:
        """
        Start unwired; ``configure`` attaches the shared outcome.
        """
        super().__init__(config)
        self.outcome = Outcome()

    def configure(self, outcome: Outcome) -> None:
        """
        Attach the probe-wide outcome so both phases write one record.

        Separate from ``__init__`` because the pyo3 ``Strategy`` base accepts only the
        config positionally - the same reason the gap fade wires its registry this way.

        """
        self.outcome = outcome

    def on_start(self) -> None:
        """
        Submit the order that will be abandoned.
        """
        instrument = self.cache.instrument(self.config.instrument_id)
        if instrument is None:
            self.outcome.record("strand", "no_instrument", str(self.config.instrument_id))
            return
        order = self.order_factory.limit(
            instrument_id=self.config.instrument_id,
            order_side=OrderSide.BUY,
            quantity=instrument.make_qty(Decimal(1)),
            price=instrument.make_price(Decimal(self.config.limit_price)),
            time_in_force=TimeInForce.GTC,
        )
        self.outcome.stranded_client_order_id = str(order.client_order_id)
        self.outcome.record("strand", "submitted", str(order.client_order_id))
        self.submit_order(order)

    def on_order_accepted(self, event: Any) -> None:
        """
        Record the acceptance that arms the strand.
        """
        self.outcome.strand_accepted = True
        self.outcome.stranded_venue_order_id = str(getattr(event, "venue_order_id", ""))
        self.outcome.record("strand", "accepted", self.outcome.stranded_venue_order_id)

    def on_order_filled(self, event: Any) -> None:
        """
        Must never happen at half price; recorded loudly.
        """
        self.outcome.strand_filled = True
        self.outcome.record("strand", "FILLED", f"unexpected fill: {event}")
        self.log.error(f"Strand order filled, which should be impossible: {event}")


class RecoverConfig(StrategyConfig):
    """
    Which instrument phase 2 sweeps.
    """

    _CUSTOM_FIELDS = ("instrument_id",)

    def __new__(cls, *args: object, **kwargs: object):  # noqa: ANN204 - pyo3 base
        """
        Strip the custom fields before the pyo3 base sees them.
        """
        for field_name in cls._CUSTOM_FIELDS:
            kwargs.pop(field_name, None)
        return super().__new__(cls, *args, **kwargs)

    def __init__(self, instrument_id: Any, **_kwargs: object) -> None:
        """
        Configure the recovery.
        """
        super().__init__()
        self.instrument_id = instrument_id


class RecoverAndSweep(Strategy):
    """
    Reads what reconciliation adopted, then cancels it and listens for the ack.
    """

    def __init__(self, config: RecoverConfig) -> None:
        """
        Start unwired; ``configure`` attaches the shared outcome.
        """
        super().__init__(config)
        self.outcome = Outcome()

    def configure(self, outcome: Outcome) -> None:
        """
        Attach the probe-wide outcome carried over from the strand phase.
        """
        self.outcome = outcome

    def on_start(self) -> None:
        """
        Sample the cache reconciliation filled, then sweep it.
        """
        open_orders = list(self.cache.orders_open())
        self.outcome.adopted_open_orders = [str(o.client_order_id) for o in open_orders]
        self.outcome.adopted = any(
            str(o.client_order_id) == self.outcome.stranded_client_order_id
            or str(getattr(o, "venue_order_id", "")) == self.outcome.stranded_venue_order_id
            for o in open_orders
        )
        self.outcome.record(
            "recover",
            "cache_after_reconciliation",
            f"open={self.outcome.adopted_open_orders or 'none'}",
        )
        self.cancel_all_orders(self.config.instrument_id, strategy_only=False)
        self.outcome.record("sweep", "cancel_all_sent")

    def on_order_canceled(self, event: Any) -> None:
        """
        Record each acknowledgement, since the acknowledgement is the evidence.
        """
        if str(event.client_order_id) == self.outcome.stranded_client_order_id:
            self.outcome.cancel_acknowledged = True
        self.outcome.record("sweep", "canceled", str(event.client_order_id))

    def on_order_cancel_rejected(self, event: Any) -> None:
        """
        Record a refusal of the foreign cancel - the verdict phase 3 exists to catch.
        """
        self.outcome.record("sweep", "cancel_rejected", str(getattr(event, "reason", event)))

    def snapshot_leftovers(self) -> None:
        """
        Record what is still open, as the last thing before shutdown.
        """
        self.outcome.left_open = [str(o.client_order_id) for o in self.cache.orders_open()]


async def _run_node(session: PaperSession, strategy: Strategy, settle_secs: int, **kwargs: Any):
    """
    Run one node for a bounded window and stop it cleanly.
    """
    node, _risk_engine = build_paper_node(
        session,
        market_data_type=MarketDataType.DELAYED,
        strategies=(strategy,),
        **kwargs,
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


async def run_probe(
    *,
    account: str,
    host: str,
    port: int,
    instrument_id: Any,
    limit_price: Decimal,
    settle_secs: int,
) -> Outcome:
    """
    Strand on one pair of client ids, recover and sweep on another.
    """
    outcome = Outcome()

    strand = StrandOrder(StrandConfig(instrument_id=instrument_id, limit_price=str(limit_price)))
    strand.configure(outcome)
    await _run_node(
        PaperSession(
            account_id=account,
            host=host,
            port=port,
            data_client_id=831,
            exec_client_id=832,
            orders_enabled=True,
            instrument_ids=(str(instrument_id),),
        ),
        strand,
        settle_secs,
        risk_engine_config=LiveRiskEngineConfig(
            max_notional_per_order={str(instrument_id): str(limit_price * MAX_NOTIONAL_MULTIPLE)},
        ),
    )
    outcome.record("strand", "node_stopped", "order left working on purpose")

    if not outcome.strand_accepted:
        outcome.record("recover", "skipped", "nothing was stranded, so there is nothing to prove")
        return outcome

    recover = RecoverAndSweep(RecoverConfig(instrument_id=instrument_id))
    recover.configure(outcome)
    await _run_node(
        PaperSession(
            account_id=account,
            host=host,
            port=port,
            data_client_id=841,
            exec_client_id=842,
            # Deliberately left disabled: cancel commands bypass the risk engine, so the
            # sweep works with the halt in place, and a recovery node that cannot submit
            # is a recovery node that cannot make the situation worse.
            instrument_ids=(str(instrument_id),),
        ),
        recover,
        settle_secs,
    )
    recover.snapshot_leftovers()
    return outcome


def main(argv: list[str] | None = None) -> int:
    """
    Run the three phases and file the record.

    Non-zero exit unless the order was stranded, adopted, cancelled, and nothing is
    left.

    """
    parser = argparse.ArgumentParser(
        prog="python -m copilot.live.probes.strand_recovery",
        description="Strand a working order on purpose; confirm a fresh node recovers it.",
    )
    parser.add_argument("--host", default=os.getenv("IB_V2_HOST", "172.17.112.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("IB_V2_PORT", "7497")))
    parser.add_argument("--account", default=os.getenv("COPILOT_PAPER_ACCOUNT", ""))
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument("--venue", default="XNAS", help="Research (MIC) venue; mapped to IB")
    parser.add_argument("--reference-price", required=True, help="Last known price, for the offset")
    parser.add_argument("--settle-secs", type=int, default=30)
    args = parser.parse_args(argv)

    if not args.account:
        print("error: no paper account; pass --account or set COPILOT_PAPER_ACCOUNT")
        return 2

    instrument_id = broker_instrument_id(args.symbol, args.venue)
    limit_price = (Decimal(args.reference_price) * LIMIT_FRACTION_OF_REFERENCE).quantize(
        Decimal("0.01"),
    )

    started = datetime.now(UTC)
    print(f"Stranding 1 {instrument_id} BUY LIMIT GTC at {limit_price}, then recovering it")
    outcome = asyncio.run(
        run_probe(
            account=args.account,
            host=args.host,
            port=args.port,
            instrument_id=instrument_id,
            limit_price=limit_price,
            settle_secs=args.settle_secs,
        ),
    )

    print(f"\n{'phase':<9}{'event':<28}detail")
    for e in outcome.events:
        print(f"{e.phase:<9}{e.kind:<28}{e.detail[:80]}")
    print(f"\nstranded:  {outcome.stranded_client_order_id or '-'}")
    print(f"adopted:   {outcome.adopted}")
    print(f"cancelled: {outcome.cancel_acknowledged}")
    print(f"left open: {outcome.left_open or 'none'}")
    print(f"\nRESULT: {'PASS' if outcome.passed else 'FAIL'}")
    if not outcome.passed and outcome.strand_accepted and not outcome.cancel_acknowledged:
        print(
            "The stranded order was not cancelled through the API. "
            "Cancel it by hand in TWS before doing anything else.",
        )
    print("Eyeball the TWS order list regardless - a clean sweep is not proof (OPERATIONS.md).")

    report = {
        "started_at": started.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "stage": "6-confirmation",
        "case": "recover_unknown_working_order",
        "source": f"interactive_brokers {args.host}:{args.port}",
        "instrument_id": str(instrument_id),
        "limit_price": str(limit_price),
        "quantity": 1,
        "passed": outcome.passed,
        "outcome": asdict(outcome),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    path = OUT_DIR / f"strand_recovery_{stamp}.json"
    path.write_text(json.dumps(report, indent=2) + "\n")
    print(f"\nWrote {path}")
    return 0 if outcome.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
