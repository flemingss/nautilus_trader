"""
Wires :mod:`copilot.risk.protections` into a running Nautilus node.

Nautilus enforces risk per-order (``max_notional_per_order``, submit/modify rate
limits). This adds the account-wide, sequence-aware layer: a run of stop-outs or a
realised drawdown pauses trading for a cooldown.

Enforcement strength - read this before relying on it
-----------------------------------------------------
Nautilus has the right primitive: ``TradingState`` (``ACTIVE`` / ``HALTED`` /
``REDUCING``) is enforced natively in the Rust risk engine, which denies new orders
before they reach an execution client. This guard now reaches it, through the
``RiskEngine`` handle added in this fork (``LiveNode.risk_engine``).

On breach, in descending order of reliability:

1. **Halts the engine** - every subsequent order is denied inside the risk engine, so
   a strategy that keeps submitting is stopped rather than merely tidied up after.
2. **Cancels working orders** across strategies (``strategy_only=False``).
3. **Flattens open positions** for the instruments it is configured to watch.
4. **Publishes a signal** other strategies can consult before entering.

Step 1 is what makes this preventive rather than reactive, and it is the only one that
holds between evaluations. **Supply the handle or it degrades silently** to the reactive
behaviour it had before - :meth:`ProtectionGuard.configure` takes it, and the guard logs
a warning at start if it is missing rather than pretending to be an engine-level gate.

Take the handle **before** starting a hosted run: the run takes ownership of the node,
so ``node.risk_engine`` stops resolving once one is under way, while a handle taken
beforehand keeps working.

    node = LiveNode.build("MY-NODE", config)
    guard.configure(settings, risk_engine=node.risk_engine)

The state is restored to ``ACTIVE`` when the breach expires, so a cooldown ends by
itself. It is never set to ``ACTIVE`` on start: doing so would clear a halt someone
else set for a reason this guard knows nothing about.

"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from typing import Any

from copilot.risk.protections import ProtectionBreach
from copilot.risk.protections import ProtectionPolicy
from copilot.risk.protections import TradeOutcome
from copilot.risk.protections import evaluate_protections
from nautilus_trader.model import InstrumentId
from nautilus_trader.model import OrderType
from nautilus_trader.model import TradingState
from nautilus_trader.trading import Strategy


# Closing a position with any of these means the stop decided the trade, which is
# what `TradeOutcome.stopped_out` means to the consecutive-stops breaker.
STOP_ORDER_TYPES = frozenset(
    {
        OrderType.STOP_MARKET,
        OrderType.STOP_LIMIT,
        OrderType.TRAILING_STOP_MARKET,
        OrderType.TRAILING_STOP_LIMIT,
        OrderType.MARKET_IF_TOUCHED,
        OrderType.LIMIT_IF_TOUCHED,
    },
)


@dataclass(frozen=True)
class ProtectionGuardSettings:
    """
    Operator settings for the guard, kept separate from the pure policy.
    """

    policy: ProtectionPolicy
    instrument_ids: tuple[InstrumentId, ...]
    """
    Instruments the guard will flatten on breach.

    Order cancellation is account wide regardless; flattening needs an explicit
    instrument.

    """
    account_value: Decimal
    """
    Denominator for the drawdown limit.

    Sourced from the operator rather than read live, so the limit does not move as the
    account does mid-run.

    """
    evaluate_seconds: int = 60
    flatten_on_breach: bool = True


class ProtectionGuard(Strategy):
    """
    Portfolio-level breaker. Holds no positions of its own.

    Implemented as a ``Strategy`` rather than a ``DataActor`` only because order
    cancellation and position closing live on ``Strategy``.

    """

    def configure(
        self,
        settings: ProtectionGuardSettings,
        risk_engine: Any = None,
    ) -> None:
        """
        Attach operator settings and, optionally, the engine handle to halt with.

        ``risk_engine`` is ``LiveNode.risk_engine``, taken before the run starts.
        Without it the guard still cancels and flattens, but cannot stop the next
        order - see the module docstring.

        """
        self._settings = settings
        self._risk_engine = risk_engine
        self._breach: ProtectionBreach | None = None

    @property
    def breach(self) -> ProtectionBreach | None:
        """
        The breach currently in force, or ``None``.

        Strategies may consult this.

        """
        return self._breach

    def on_start(self) -> None:
        """
        Announce the policy and start the evaluation timer.
        """
        self.log.info(
            f"ProtectionGuard active: {self._settings.policy}",
        )
        self.clock.set_timer(
            name="copilot-protection-eval",
            interval=timedelta(seconds=self._settings.evaluate_seconds),
        )

    def on_time_event(self, _event) -> None:  # noqa: ANN001 - TimeEvent from the engine
        """
        Re-evaluate on the timer.
        """
        self.evaluate()

    def on_position_closed(self, _event) -> None:  # noqa: ANN001 - PositionClosed
        """
        Re-evaluate immediately on new evidence.
        """
        # Evaluate immediately on new evidence rather than waiting for the timer;
        # the breaker exists to react to a losing sequence, and a whole timer
        # interval of delay is exactly the window it is meant to remove.
        self.evaluate()

    def evaluate(self) -> None:
        """
        Re-read closed trades, judge them, and act if the verdict changed.
        """
        now = datetime.now(UTC)
        outcomes = self.collect_outcomes()
        breach = evaluate_protections(
            outcomes,
            self._settings.policy,
            now=now,
            account_value=self._settings.account_value,
        )

        if breach is not None and self._breach is None:
            self._on_breach_opened(breach)
        elif breach is None and self._breach is not None:
            self._on_breach_closed()
        self._breach = breach

    def collect_outcomes(self) -> list[TradeOutcome]:
        """
        Map this node's closed positions onto the breaker's input type.
        """
        outcomes: list[TradeOutcome] = []
        for position in self.cache.positions_closed():
            ts_closed = position.ts_closed
            if ts_closed is None:
                continue
            outcomes.append(
                TradeOutcome(
                    closed_at=datetime.fromtimestamp(ts_closed / 1e9, tz=UTC),
                    realized_pnl=Decimal(str(position.realized_pnl.as_double()))
                    if position.realized_pnl is not None
                    else Decimal(0),
                    stopped_out=self._closed_by_stop(position),
                ),
            )
        return outcomes

    def _closed_by_stop(self, position) -> bool:  # noqa: ANN001 - Position
        """
        Whether a stop order closed this position.

        Nautilus does not record an exit reason, so this reads the closing order's type.
        An unknown or missing closing order is treated as *not* a stop-out: the
        consecutive-stops breaker should fire on evidence, not on absence of it.

        """
        closing_order_id = position.closing_order_id
        if closing_order_id is None:
            return False
        order = self.cache.order(closing_order_id)
        if order is None:
            return False
        return order.order_type in STOP_ORDER_TYPES

    def _on_breach_closed(self) -> None:
        """
        Release the halt when the cooldown expires.

        Only ever moves HALTED back to ACTIVE. If the state is something else by now,
        someone or something else changed it, and this guard is not the right thing to
        overrule that.

        """
        if self._risk_engine is None:
            self.log.info("Protection cooldown expired; trading may resume")
            return
        if self._risk_engine.trading_state == TradingState.HALTED:
            self._risk_engine.set_trading_state(TradingState.ACTIVE)
            self.log.info("Protection cooldown expired; trading state restored to ACTIVE")
        else:
            self.log.warning(
                f"Protection cooldown expired, but trading state is "
                f"{self._risk_engine.trading_state} rather than HALTED. Leaving it alone - "
                f"this guard did not set it.",
            )

    def _set_trading_state(self, state: TradingState) -> bool:
        """
        Move the engine's trading state, reporting whether it actually happened.

        Returns False when no handle was supplied, which is the difference between a
        preventive gate and a tidy-up. The caller logs it rather than letting a halt
        that never reached the engine read as a successful one.

        """
        if self._risk_engine is None:
            return False
        self._risk_engine.set_trading_state(state)
        return True

    def _on_breach_opened(self, breach: ProtectionBreach) -> None:
        self.log.error(
            f"PROTECTION BREACH [{breach.trigger}] {breach.detail} "
            f"- pausing until {breach.until.isoformat()}",
        )
        # Halt first. Cancelling and flattening take time and emit orders of their own;
        # anything submitted in between is denied only once the state is already set.
        if self._set_trading_state(TradingState.HALTED):
            self.log.error("Trading state HALTED - new orders will be denied by the risk engine")
        else:
            self.log.warning(
                "No risk engine handle: cancelling and flattening only. A strategy that "
                "keeps submitting will keep getting orders accepted. Pass "
                "`risk_engine=node.risk_engine` to configure() for engine-level enforcement.",
            )
        for instrument_id in self._settings.instrument_ids:
            # Account-wide: a breaker that only cancelled this component's own
            # orders would leave every real strategy running.
            self.cancel_all_orders(instrument_id, strategy_only=False)
        if self._settings.flatten_on_breach:
            for instrument_id in self._settings.instrument_ids:
                self.close_all_positions(instrument_id)
        self.publish_signal("copilot_protection_breach", str(breach.trigger))


__all__ = ["STOP_ORDER_TYPES", "ProtectionGuard", "ProtectionGuardSettings"]
