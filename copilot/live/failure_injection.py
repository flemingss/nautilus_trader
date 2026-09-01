"""
Paper stage six: make things go wrong on purpose, and check the system notices.

    export IBAPI_TIMEZONE_ALIASES="JST=Asia/Tokyo"
    python -m copilot.live.failure_injection --account DUT067974

Stages one to five confirmed the happy path, which was never seriously in doubt. **This is
the stage worth the eight weeks.** An unattended system is not defined by what it does when
things work.

Three injections, and one case that is recorded as failing
-----------------------------------------------------------
Each probe is designed so the *expected* outcome is a refusal or an alarm, not a fill.

1. **Denied by our own risk engine.** An order above ``max_notional_per_order`` on AAPL. The
   engine must refuse it before it reaches the execution client, which is the property the
   whole preventive-risk argument rests on and which has only ever been asserted.
2. **Rejected by the broker.** An order far beyond the account's buying power on MSFT, which
   carries no cap, so it passes our engine and IB has to be the one to say no. The two
   refusals travel different paths and a system that conflates them will handle one wrongly.
3. **Stale data.** Quotes are subscribed, then unsubscribed while the node keeps running, and
   the age of the last quote is watched until it crosses a threshold. This tests the
   *detector* against a real feed going quiet. It does not test IB going quiet, which is a
   different fault with the same symptom.

The fourth case, **recovering an unknown working order**, is recorded as a known failure
rather than re-run. Stage three established that Nautilus reconciliation drops an external
order reported as ``SUBMITTED``, so the order becomes invisible and uncancellable. Re-running
it would strand another live order to re-learn something already evidenced, so this module
reports it from the record and the fix belongs in ``crates/execution``.
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

NOTIONAL_CAP = Decimal(1000)
"""
The deployable capital, used as the cap on the instrument the denial probe targets.
"""

DENIAL_QUANTITY = 10
"""
Enough shares of a USD 300 instrument to exceed a USD 1,000 cap, and no more.
"""

REJECT_QUANTITY = 100_000
"""
Far beyond any buying power a paper account has, so the broker has to refuse it.
"""

STALE_AFTER_SECS = 20
"""
How long without a quote before the feed is called stale.

Short because this is a check, not a policy. A real session sets this from the
instrument's expected tick rate, and a threshold shorter than the quiet spells a thin
name has in normal trading is a threshold that cries wolf.

"""

UNRECOVERABLE_CASE = {
    "name": "recover_unknown_working_order",
    "status": "KNOWN FAILURE",
    "detail": (
        "Nautilus reconciliation drops an external order reported as SUBMITTED "
        "(crates/execution/src/reconciliation/orders.rs), so it never enters the cache and "
        "cannot be cancelled. Evidenced by stage three on 2026-09-01; not re-run here "
        "because re-running strands another live order to re-learn it."
    ),
}


@dataclass
class Probe:
    """
    One injected fault and what the system did about it.
    """

    name: str
    expected: str
    observed: str = ""
    detail: str = ""

    @property
    def passed(self) -> bool:
        """
        Whether the system reacted the way the fault requires.
        """
        return self.observed == self.expected


@dataclass
class InjectionResult:
    """
    Every probe, plus the quote bookkeeping the staleness check needs.
    """

    probes: list[Probe] = field(default_factory=list)
    quotes_seen: int = 0
    stale_detected: bool = False
    max_quote_age_secs: float = 0.0
    orders_left_working: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """
        Every probe reacted correctly and nothing was left working.
        """
        return all(p.passed for p in self.probes) and not self.orders_left_working


class FailureInjectionConfig(StrategyConfig):
    """
    The two instruments the probes target, and the prices they are derived from.
    """

    _CUSTOM_FIELDS = ("capped_id", "uncapped_id", "capped_price", "uncapped_price")

    def __new__(cls, *args: object, **kwargs: object):  # noqa: ANN204 - pyo3 base
        """
        Strip the custom fields before the pyo3 base sees them.
        """
        for field_name in cls._CUSTOM_FIELDS:
            kwargs.pop(field_name, None)
        return super().__new__(cls, *args, **kwargs)

    def __init__(
        self,
        capped_id: Any,
        uncapped_id: Any,
        capped_price: str,
        uncapped_price: str,
        **_kwargs: object,
    ) -> None:
        """
        Configure the injections.
        """
        super().__init__()
        self.capped_id = capped_id
        self.uncapped_id = uncapped_id
        self.capped_price = capped_price
        self.uncapped_price = uncapped_price


class FailureInjection(Strategy):
    """
    Submits orders that must be refused, then starves its own feed.
    """

    def __init__(self, config: FailureInjectionConfig) -> None:
        """
        Start with the probes declared and nothing observed.
        """
        super().__init__(config)
        self.result = InjectionResult(
            probes=[
                Probe(name="denied_by_risk_engine", expected="denied"),
                Probe(name="rejected_by_broker", expected="rejected"),
                Probe(name="stale_feed_detected", expected="stale"),
            ],
        )
        self._by_order: dict[str, str] = {}
        self._last_quote: datetime | None = None
        self._unsubscribed = False

    def _probe(self, name: str) -> Probe:
        return next(p for p in self.result.probes if p.name == name)

    def on_start(self) -> None:
        """
        Fire both order probes, and start the feed that will later be starved.
        """
        self.subscribe_quotes(self.config.capped_id)

        capped = self.cache.instrument(self.config.capped_id)
        uncapped = self.cache.instrument(self.config.uncapped_id)

        # Above our own cap, so the risk engine must refuse it before the broker sees it.
        over_cap = self.order_factory.limit(
            instrument_id=capped.id,
            order_side=OrderSide.BUY,
            quantity=capped.make_qty(Decimal(DENIAL_QUANTITY)),
            price=capped.make_price(Decimal(self.config.capped_price)),
            time_in_force=TimeInForce.GTC,
        )
        self._by_order[str(over_cap.client_order_id)] = "denied_by_risk_engine"

        # No cap on this instrument, so it reaches IB and IB has to be the one to say no.
        over_buying_power = self.order_factory.limit(
            instrument_id=uncapped.id,
            order_side=OrderSide.BUY,
            quantity=uncapped.make_qty(Decimal(REJECT_QUANTITY)),
            price=uncapped.make_price(Decimal(self.config.uncapped_price)),
            time_in_force=TimeInForce.GTC,
        )
        self._by_order[str(over_buying_power.client_order_id)] = "rejected_by_broker"

        self.submit_order(over_cap)
        self.submit_order(over_buying_power)

    def on_quote(self, quote: Any) -> None:  # noqa: ARG002 - the tick's content is irrelevant
        """
        Count quotes, then cut the feed off once there is enough to starve.
        """
        self._last_quote = datetime.now(tz=UTC)
        self.result.quotes_seen += 1
        if not self._unsubscribed and self.result.quotes_seen >= 3:  # noqa: PLR2004
            self._unsubscribed = True
            self.unsubscribe_quotes(self.config.capped_id)
            self.log.info("[stage6] unsubscribed; feed should now go stale")

    def check_staleness(self) -> None:
        """
        Check how old the newest quote is, and flag the feed if it is too old.

        Deliberately a plain method rather than a timer. The runner owns the clock here,
        so the check is observable from outside the node instead of buried in a
        callback.

        """
        if self._last_quote is None:
            return
        age = (datetime.now(tz=UTC) - self._last_quote).total_seconds()
        self.result.max_quote_age_secs = max(self.result.max_quote_age_secs, age)
        if age >= STALE_AFTER_SECS and not self.result.stale_detected:
            self.result.stale_detected = True
            probe = self._probe("stale_feed_detected")
            probe.observed = "stale"
            probe.detail = f"no quote for {age:.1f}s"

    def on_order_denied(self, event: Any) -> None:
        """
        Record a refusal from our own engine, before the broker was involved.
        """
        self._settle(event, "denied", str(getattr(event, "reason", "")))

    def on_order_rejected(self, event: Any) -> None:
        """
        Record a refusal from the broker: a different path, and a different meaning.
        """
        self._settle(event, "rejected", str(getattr(event, "reason", "")))

    def on_order_accepted(self, event: Any) -> None:
        """
        Not what any probe wants.

        Recorded, then cancelled so nothing is left working.

        """
        self._settle(event, "accepted", "the broker allowed an order that should be refused")
        self.cancel_order(event.client_order_id)

    def on_order_filled(self, event: Any) -> None:
        """
        Record a fill: the worst outcome, and the one the prices are chosen to prevent.
        """
        self._settle(event, "filled", "an order that should have been refused filled")
        self.log.error(f"Injected order filled: {event}")

    def _settle(self, event: Any, observed: str, detail: str) -> None:
        name = self._by_order.get(str(event.client_order_id))
        if name is None:
            return
        probe = self._probe(name)
        if not probe.observed:
            probe.observed = observed
            probe.detail = detail

    def on_stop(self) -> None:
        """
        Report anything still working, since leaving an order behind is its own failure.
        """
        left = []
        for instrument_id in (self.config.capped_id, self.config.uncapped_id):
            left += [
                str(o.client_order_id) for o in self.cache.orders_open(instrument_id=instrument_id)
            ]
        self.result.orders_left_working = left


async def run_injection(
    session: PaperSession,
    *,
    capped_id: Any,
    uncapped_id: Any,
    capped_price: Decimal,
    uncapped_price: Decimal,
    settle_secs: int,
) -> InjectionResult:
    """
    Run the node, polling the staleness check while it is up.
    """
    strategy = FailureInjection(
        FailureInjectionConfig(
            capped_id=capped_id,
            uncapped_id=uncapped_id,
            capped_price=str(capped_price),
            uncapped_price=str(uncapped_price),
        ),
    )
    node, _risk_engine = build_paper_node(
        session,
        market_data_type=MarketDataType.DELAYED,
        strategies=(strategy,),
        # Only the first instrument is capped. The second must reach IB so the broker is
        # the one refusing it, which is the whole point of having two probes.
        risk_engine_config=LiveRiskEngineConfig(
            max_notional_per_order={str(capped_id): str(NOTIONAL_CAP)},
        ),
    )
    handle = node.handle()
    task = asyncio.create_task(node.run_async())
    try:
        for _ in range(settle_secs):
            await asyncio.sleep(1)
            strategy.check_staleness()
    finally:
        handle.stop()
        try:
            await asyncio.wait_for(task, timeout=90)
        except (TimeoutError, asyncio.CancelledError) as e:
            strategy.log.error(f"Node did not stop cleanly: {e!r}")
    return strategy.result


def main(argv: list[str] | None = None) -> int:
    """
    Inject the faults and report.

    Non-zero exit if the system did not react correctly.

    """
    parser = argparse.ArgumentParser(
        prog="python -m copilot.live.failure_injection",
        description="Paper stage six: injected faults, and whether the system notices.",
    )
    parser.add_argument("--host", default=os.getenv("IB_V2_HOST", "172.17.112.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("IB_V2_PORT", "7497")))
    parser.add_argument("--account", default=os.getenv("COPILOT_PAPER_ACCOUNT", ""))
    parser.add_argument("--capped-symbol", default="AAPL")
    parser.add_argument("--capped-price", default="158.00", help="Far below market, cannot fill")
    parser.add_argument("--uncapped-symbol", default="MSFT")
    parser.add_argument("--uncapped-price", default="240.00", help="Far below market, cannot fill")
    parser.add_argument("--venue", default="XNAS")
    parser.add_argument("--settle-secs", type=int, default=60)
    parser.add_argument("--data-client-id", type=int, default=871)
    parser.add_argument("--exec-client-id", type=int, default=872)
    args = parser.parse_args(argv)

    if not args.account:
        print("error: no paper account; pass --account or set COPILOT_PAPER_ACCOUNT")
        return 2

    capped_id = broker_instrument_id(args.capped_symbol, args.venue)
    uncapped_id = broker_instrument_id(args.uncapped_symbol, args.venue)
    session = PaperSession(
        account_id=args.account,
        host=args.host,
        port=args.port,
        data_client_id=args.data_client_id,
        exec_client_id=args.exec_client_id,
        orders_enabled=True,
        instrument_ids=(str(capped_id), str(uncapped_id)),
    )

    started = datetime.now(UTC)
    print(f"Injecting faults against {capped_id} (capped) and {uncapped_id} (uncapped)")
    result = asyncio.run(
        run_injection(
            session,
            capped_id=capped_id,
            uncapped_id=uncapped_id,
            capped_price=Decimal(args.capped_price),
            uncapped_price=Decimal(args.uncapped_price),
            settle_secs=args.settle_secs,
        ),
    )

    print(f"\n{'probe':<26}{'result':<8}{'expected':<12}{'observed':<12}detail")
    for p in result.probes:
        print(
            f"{p.name:<26}{'PASS' if p.passed else 'FAIL':<8}{p.expected:<12}"
            f"{p.observed or '-':<12}{p.detail[:50]}",
        )
    print(
        f"{UNRECOVERABLE_CASE['name']:<26}{'FAIL':<8}{'recovered':<12}{'invisible':<12}"
        f"{UNRECOVERABLE_CASE['detail'][:50]}",
    )
    print(f"\nquotes seen: {result.quotes_seen}, max quote age {result.max_quote_age_secs:.1f}s")
    print(f"left working: {result.orders_left_working or 'none'}")
    print(f"\nRESULT: {'PASS' if result.passed else 'FAIL'} (known failure above excluded)")

    report = {
        "started_at": started.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "stage": "6",
        "source": f"interactive_brokers {args.host}:{args.port}",
        "passed": result.passed,
        "known_failure": UNRECOVERABLE_CASE,
        "result": asdict(result),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"failure_injection_{started.strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Wrote {path}")
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
