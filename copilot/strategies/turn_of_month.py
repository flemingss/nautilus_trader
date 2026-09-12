"""
Hold SPY across the turn of the month, on an institutional flow argument.

EXP-2026-001, the first premise after the gap-fade family was rejected on 2026-09-10. The card
is [`experiments/EXP-2026-001-turn-of-month.md`](experiments/EXP-2026-001-turn-of-month.md) and
it was written before this module ran, which is the playbook's order.

The rule
--------
**Enter at the close of a month's last trading session. Exit at the close of the
``hold_sessions``-th session after it, or at a protective stop, whichever comes first.**

Nothing in the trigger reads a price. The entry session is a property of the exchange calendar,
known weeks in advance, so a close fill is honest here in a way it is not for the gap fade: the
decision cannot have used the price it fills at, where ADR-0013's bracket is about a rule whose
trigger is the same close it fills on. That also means this premise does not need ADR-0032's
marketable-limit path: a market-on-close order is the honest live expression of it.

Why it might be real
--------------------
Salaries, pension and retirement contributions, coupon and dividend reinvestment and index-fund
rebalancing cluster at month end and month start. That is a predictable, calendar-driven demand
for equities from buyers who are not trading on information, and arbitraging it away means taking
the other side of money that arrives regardless. Documented since Ariel (1987) and Lakonishok and
Smidt (1988); McConnell and Xu (2008) find it out of sample and not explained by size or January.

Why there is no profit target
-----------------------------
The premise *is* a window. A target would truncate the distribution the experiment measures, and
the R denominator would then describe a different trade from the one the argument predicts. The
stop is there because the playbook sizes from an executable stop, not because the rule wants one:
it is the loss the position is allowed, and the null control carries the same one.

What the null control replaces
------------------------------
``entry_sessions`` overrides the calendar with a list of sessions, which is how
:mod:`copilot.validation.null_control` runs this rule on entry dates drawn at random. Everything
else - the hold, the stop, the sizing, the costs - is unchanged, so the only difference between
the premise and its null is *which* sessions were chosen.

"""

from __future__ import annotations

from datetime import UTC
from datetime import date
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from typing import Any

from copilot.calibration.cost_model import commission
from copilot.data.calendar import is_trading_day
from copilot.risk.exposure import ExposureLedger
from copilot.risk.sizing import Sizing
from copilot.risk.sizing import size_from_levels
from copilot.validation.types import Direction
from nautilus_trader.indicators import AverageTrueRange
from nautilus_trader.model import Bar
from nautilus_trader.model import OrderSide
from nautilus_trader.trading import Strategy
from nautilus_trader.trading import StrategyConfig


if TYPE_CHECKING:
    from collections.abc import Sequence


DEFAULT_ATR_PERIOD = 14
DEFAULT_HOLD_SESSIONS = 3
DEFAULT_STOP_ATR = "1.5"
DEFAULT_RISK_BUDGET = "1000"

DAYS_IN_A_MONTH = 31
"""
Days scanned forward to answer "is any trading session left in this month".
"""

SEARCH_SPACE: dict[str, tuple[Any, ...]] = {
    "hold_sessions": (2, 3, 4),
    "stop_atr": (Decimal("1.5"), Decimal("2.5")),
}
"""
Six points, declared in the experiment card before anything ran.

The hold is the premise's own claim - the effect is documented over the first few sessions of a
month - and the stop is the sizing's, not the rule's. Both are searched because neither is
pinned by the argument, and the space stays small because the best score obtainable from noise
grows with the number of trials.

"""

AXIS_DEFAULTS: dict[str, Any] = {
    "hold_sessions": DEFAULT_HOLD_SESSIONS,
    "stop_atr": Decimal(DEFAULT_STOP_ATR),
}
"""
What each searched axis runs at when an activation leaves it unfixed.
"""

WARMUP_BARS = DEFAULT_ATR_PERIOD + 2
"""
History the rule needs: the ATR the stop is placed from, plus a bar.
"""

ENTRY_SUBMITTED = "entry_submitted"
EXIT_SUBMITTED = "exit_submitted"
NO_GAP_ALLOWANCE = "no_gap_allowance"
NOT_MONTH_END = "not_month_end"
INSUFFICIENT_HISTORY = "insufficient_history"
HOLDING = "holding"


def month_end_sessions(sessions: Sequence[date]) -> tuple[date, ...]:
    """
    Return the sessions that are the last trading session of their calendar month.

    Answered from the exchange calendar rather than from the bars, because a rule that
    decided "last session of the month" by looking at whether another bar followed would
    be reading the future. The calendar is published years ahead; the bars are not.

    """
    return tuple(day for day in sessions if is_last_session_of_month(day))


def is_last_session_of_month(day: date) -> bool:
    """
    Whether ``day`` is a trading session with no trading session left in its month.
    """
    if not is_trading_day(day):
        return False
    for ahead in range(1, DAYS_IN_A_MONTH):
        later = date.fromordinal(day.toordinal() + ahead)
        if later.month != day.month:
            return True
        if is_trading_day(later):
            return False
    return True


class TurnOfMonthConfig(StrategyConfig):
    """
    Knobs for the turn-of-month hold.

    ``StrategyConfig`` is a pyo3 class, so custom fields follow the ``_CUSTOM_FIELDS`` plus
    ``__new__`` pattern the other strategies here use. Numeric knobs are carried as strings and
    converted at use: they end up multiplying an ATR to place a stop, and a float round trip
    would move them.

    """

    _CUSTOM_FIELDS = (
        "instrument_id",
        "bar_type",
        "atr_period",
        "hold_sessions",
        "stop_atr",
        "gap_atr",
        "risk_budget",
        "max_notional",
        "long",
        "entry_sessions",
        "subscribe_bars",
    )

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
        bar_type: Any,
        *,
        atr_period: int = DEFAULT_ATR_PERIOD,
        hold_sessions: int = DEFAULT_HOLD_SESSIONS,
        stop_atr: str = DEFAULT_STOP_ATR,
        gap_atr: str = "",
        risk_budget: str = DEFAULT_RISK_BUDGET,
        max_notional: str = "",
        long: bool = True,
        entry_sessions: str = "",
        subscribe_bars: bool = True,
        **_kwargs: object,
    ) -> None:
        """
        Configure one turn-of-month hold.

        ``entry_sessions`` is a comma-separated list of ISO dates that **replaces** the calendar
        rule, which is how the null control runs this rule on random sessions. ``long`` is here
        because the config shape is shared across the project's strategies; a short turn of the
        month is not a premise anyone has argued, and the charter forbids shorts anyway.

        """
        if hold_sessions < 1:
            raise ValueError(f"hold_sessions must be at least 1, got {hold_sessions}")
        super().__init__()
        self.instrument_id = instrument_id
        self.bar_type = bar_type
        self.atr_period = atr_period
        self.hold_sessions = hold_sessions
        self.stop_atr = stop_atr
        self.gap_atr = gap_atr
        self.risk_budget = risk_budget
        self.max_notional = max_notional
        self.long = long
        self.entry_sessions = entry_sessions
        self.subscribe_bars = subscribe_bars


def _session_of(bar: Bar) -> date:
    """
    Return the bar's session: the UTC date of its timestamp.

    The convention every reader of this catalog uses, and the only one right for both stamps it
    holds - vendor bars at midnight UTC on the session date, patched bars at the real close.

    """
    return datetime.fromtimestamp(bar.ts_event / 1e9, tz=UTC).date()


def _cash_for(quantity: Decimal, price: Decimal) -> Decimal:
    """
    Return the settled cash a buy spends: its notional and its commission.
    """
    notional = quantity * price
    return notional + commission(quantity, notional)


class TurnOfMonthStrategy(Strategy):
    """
    Enter at a month's last close, exit a fixed number of sessions later or at the stop.
    """

    def __init__(self, config: TurnOfMonthConfig) -> None:
        """
        Build the indicator and the state the rule needs.
        """
        super().__init__(config)
        self._atr = AverageTrueRange(config.atr_period)
        self._registry: Any = None
        self._pending_risk = Decimal(0)
        self._sizing = Sizing(
            risk_budget=Decimal(config.risk_budget),
            max_notional=Decimal(config.max_notional) if config.max_notional else None,
        )
        self._ledger: ExposureLedger | None = None
        self._entry_order_id: str | None = None
        self._stop_order: Any = None
        self._quantity = Decimal(0)
        self._sessions_held: int | None = None
        """
        Sessions since the entry bar, or None when nothing is held.
        """
        self._entry_days = frozenset(
            date.fromisoformat(token.strip())
            for token in config.entry_sessions.split(",")
            if token.strip()
        )
        self.last_outcome: str | None = None
        self.skips: dict[str, int] = {}

    def configure(self, registry: Any) -> None:
        """
        Attach the registry the replay reads risk amounts from.
        """
        self._registry = registry

    def size_against(
        self,
        risk_budget: Decimal,
        max_notional: Decimal | None,
        ledger: ExposureLedger | None = None,
    ) -> None:
        """
        Replace the research R-unit with numbers derived from the account.

        The same hook the gap fade carries, for the same reason: the config's budget is
        a unit that makes scores comparable, not an amount anyone decided to risk.

        """
        if risk_budget <= 0:
            raise ValueError(f"risk_budget must be positive, got {risk_budget}")
        if max_notional is not None and max_notional <= 0:
            raise ValueError(f"max_notional must be positive when given, got {max_notional}")
        self._sizing = Sizing(risk_budget=risk_budget, max_notional=max_notional)
        self._ledger = ledger

    def warm_up(self, bars: Sequence[Bar]) -> None:
        """
        Prime the ATR from history, before the node runs.
        """
        for bar in bars:
            self._atr.handle_bar(bar)

    def decide(self, bar: Bar) -> None:
        """
        Hand the rule one bar the way the engine does: indicator first, then the rule.
        """
        self._atr.handle_bar(bar)
        self.on_bar(bar)

    def on_start(self) -> None:
        """
        Subscribe and let the engine feed the indicator - in a replay only.

        A live session hands its bars over from the catalog, and a broker bar arriving beside
        them is a second decision path (see :mod:`copilot.live.run_activation`).

        """
        if not self.config.subscribe_bars:
            return
        self.register_indicator_for_bars(self.config.bar_type, self._atr)
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar) -> None:
        """
        Hold, exit, or enter.

        A held position counts this session and closes when the hold is up; otherwise
        the rule enters if this is a month's last trading session.

        """
        if self._sessions_held is not None:
            self._sessions_held += 1
            if self._sessions_held >= self.config.hold_sessions:
                self._exit()
            else:
                self.last_outcome = HOLDING
            return

        if not self._atr.initialized:
            self._skip(INSUFFICIENT_HISTORY)
            return
        if not self._is_entry_session(_session_of(bar)):
            self._skip(NOT_MONTH_END)
            return
        self._enter(bar)

    def _is_entry_session(self, session: date) -> bool:
        """
        Whether this session is the one the rule enters on.

        The override wins where it is set, which is what makes the null control the same
        rule on different dates rather than a different rule.

        """
        if self._entry_days:
            return session in self._entry_days
        return is_last_session_of_month(session)

    def _enter(self, bar: Bar) -> None:
        """
        Size from the stop and submit the entry with its protective stop.
        """
        instrument = self.cache.instrument(self.config.instrument_id)
        if not self.config.gap_atr:
            # Sizing on the stop distance alone charges nothing for a gap through the
            # stop, which is the understatement RISK.md's `g` exists to prevent. Refuse
            # rather than size, because a silent zero is the defect itself.
            self._skip(NO_GAP_ALLOWANCE)
            return

        entry = Decimal(str(bar.close))
        atr = Decimal(str(self._atr.value))
        distance = atr * Decimal(self.config.stop_atr)
        if distance <= 0:
            self._skip("non_positive_atr")
            return
        allowance = atr * Decimal(self.config.gap_atr)
        stop_price = entry - distance if self.config.long else entry + distance
        if stop_price <= 0:
            self._skip("invalid_levels")
            return

        direction = Direction.LONG if self.config.long else Direction.SHORT
        quantity, risk = size_from_levels(
            direction=direction,
            entry_price=entry,
            stop_price=stop_price,
            risk_budget=self._sizing.risk_budget,
            max_notional=self._sizing.max_notional,
            gap_allowance=allowance,
        )
        if quantity <= 0:
            self._skip("unsizeable")
            return

        cash = Decimal(0)
        if self._ledger is not None and self.config.long:
            # The stressed per-share loss, so a size fitted to cash records risk on the
            # same basis the sizing used.
            quantity, risk, cash = self._fit_to_settled_cash(
                entry,
                distance + allowance,
                quantity,
                risk,
            )
        if self._ledger is not None and not self._ledger.reserve(
            str(self.strategy_id),
            risk,
            cash,
        ):
            self._skip("portfolio_risk_capped")
            return

        self._pending_risk = risk
        self._quantity = quantity
        side = OrderSide.BUY if self.config.long else OrderSide.SELL
        entry_order = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=instrument.make_qty(quantity),
        )
        self._entry_order_id = str(entry_order.client_order_id)
        self.submit_order(entry_order)

        # The stop is the loss the position is allowed, and the reason the sizing has a
        # denominator. Reduce-only so it can never open one.
        self._stop_order = self.order_factory.stop_market(
            instrument_id=self.config.instrument_id,
            order_side=OrderSide.SELL if self.config.long else OrderSide.BUY,
            quantity=instrument.make_qty(quantity),
            trigger_price=instrument.make_price(stop_price),
            reduce_only=True,
        )
        self.submit_order(self._stop_order)
        self._sessions_held = 0
        self.last_outcome = ENTRY_SUBMITTED

    def _exit(self) -> None:
        """
        Close at market, which fills at the close of the session being handled.

        It takes no bar because it reads none: a market order submitted while handling a session
        fills at that session's close, so the hold's last price is the engine's to supply rather
        than this method's to choose.

        The stop is cancelled first: a stop left working after the position is flat is an order
        the next session could fill, which is the strand the sweep exists to catch.

        """
        instrument = self.cache.instrument(self.config.instrument_id)
        if self._stop_order is not None:
            # By id, not by the order: ``cancel_order`` takes a ``ClientOrderId``, and handing it
            # the order raised a TypeError inside ``on_bar`` - which the engine logs and swallows,
            # so every exit silently did nothing and each trade ran to the window's end instead.
            self.cancel_order(self._stop_order.client_order_id)
            self._stop_order = None
        if self._quantity > 0:
            self.submit_order(
                self.order_factory.market(
                    instrument_id=self.config.instrument_id,
                    order_side=OrderSide.SELL if self.config.long else OrderSide.BUY,
                    quantity=instrument.make_qty(self._quantity),
                    reduce_only=True,
                ),
            )
        self.last_outcome = EXIT_SUBMITTED
        self._sessions_held = None
        self._quantity = Decimal(0)

    def _fit_to_settled_cash(
        self,
        entry: Decimal,
        distance: Decimal,
        quantity: Decimal,
        risk: Decimal,
    ) -> tuple[Decimal, Decimal, Decimal]:
        """
        Return ``(quantity, risk, cash)``, sized down to the settled cash left.

        The playbook's ``floor(C_settled_net / P)``, the same cap the gap fade applies, so a cash
        account's buys draw on one pool whichever premise is trading.

        """
        headroom = self._ledger.cash_headroom if self._ledger is not None else None
        if headroom is None:
            return quantity, risk, Decimal(0)
        needed = _cash_for(quantity, entry)
        if needed <= headroom:
            return quantity, risk, needed
        fitted = min(quantity, (headroom / entry).to_integral_value())
        while fitted > 0 and _cash_for(fitted, entry) > headroom:
            fitted -= 1
        if fitted <= 0:
            return quantity, risk, needed
        return fitted, fitted * distance, _cash_for(fitted, entry)

    def on_position_opened(self, event: Any) -> None:
        """
        Report what this position put at risk: the contract the replay scores on.
        """
        if self._registry is not None:
            self._registry.record(str(event.position_id), self._pending_risk)

    def on_position_closed(self, _event: Any) -> None:
        """
        Give the session its risk back, and forget the hold - the stop may have ended it.
        """
        if self._ledger is not None:
            self._ledger.release(str(self.strategy_id))
        self._sessions_held = None
        self._quantity = Decimal(0)
        self._stop_order = None
        self._entry_order_id = None

    def on_order_denied(self, event: Any) -> None:
        """
        Release an entry the risk engine refused; it never became open risk.
        """
        self._release_if_entry(event)

    def on_order_rejected(self, event: Any) -> None:
        """
        Release an entry the broker refused; it never became open risk.
        """
        self._release_if_entry(event)

    def _release_if_entry(self, event: Any) -> None:
        if (
            self._entry_order_id is not None
            and str(getattr(event, "client_order_id", "")) == self._entry_order_id
        ):
            if self._ledger is not None:
                self._ledger.release(str(self.strategy_id))
                self._ledger.release_cash(str(self.strategy_id))
            self._entry_order_id = None
            self._sessions_held = None
            self._quantity = Decimal(0)

    def _skip(self, reason: str) -> None:
        self.skips[reason] = self.skips.get(reason, 0) + 1
        self.last_outcome = reason

    def decision_record(self) -> dict[str, object]:
        """
        Return what the rule holds after its last bar, in a form two runs can compare.
        """
        return {
            "atr_initialized": bool(self._atr.initialized),
            "atr_value": str(self._atr.value) if self._atr.initialized else None,
            "previous_close": None,
            "deferred_atr": None,
            "sessions_held": self._sessions_held,
            "outcome": self.last_outcome,
            "skips": dict(self.skips),
        }


def strategy_factory(
    parameters: Any,
    *,
    instrument_id: Any,
    bar_type: Any,
    risk_registry: Any,
) -> TurnOfMonthStrategy:
    """
    Build one configured strategy for a gate candidate.

    Matches :class:`~copilot.validation.nautilus_replay.StrategyFactory`, so ``make_replay`` and
    ``walk_forward`` can search over it.

    """
    strategy = TurnOfMonthStrategy(
        TurnOfMonthConfig(
            instrument_id=instrument_id,
            bar_type=bar_type,
            atr_period=int(parameters.get("atr_period", DEFAULT_ATR_PERIOD)),
            hold_sessions=int(parameters.get("hold_sessions", DEFAULT_HOLD_SESSIONS)),
            stop_atr=str(parameters.get("stop_atr", DEFAULT_STOP_ATR)),
            gap_atr=str(parameters.get("gap_atr", "")),
            risk_budget=str(parameters.get("risk_budget", DEFAULT_RISK_BUDGET)),
            max_notional=str(parameters.get("max_notional", "")),
            long=bool(parameters.get("long", True)),
            entry_sessions=str(parameters.get("entry_sessions", "")),
            subscribe_bars=bool(parameters.get("subscribe_bars", True)),
            **(
                {"order_id_tag": str(parameters["order_id_tag"])}
                if parameters.get("order_id_tag")
                else {}
            ),
        ),
    )
    strategy.configure(risk_registry)
    return strategy


__all__ = [
    "AXIS_DEFAULTS",
    "ENTRY_SUBMITTED",
    "EXIT_SUBMITTED",
    "NOT_MONTH_END",
    "NO_GAP_ALLOWANCE",
    "SEARCH_SPACE",
    "WARMUP_BARS",
    "TurnOfMonthConfig",
    "TurnOfMonthStrategy",
    "is_last_session_of_month",
    "month_end_sessions",
    "strategy_factory",
]
