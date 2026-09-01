"""
The overnight-gap fade, as a Nautilus strategy.

Ported from trade-copilot ``libs/setups/gap_reversal.py`` (V1-32). The rule: when a
session opens more than ``min_gap_atr`` ATRs below the previous close, fade it - enter
long, stop at ``stop_atr`` ATRs, target at ``target_1_atr``. One leg per strategy; the
short leg fades gap-ups and is the same class with ``long=False``.

Why this premise first
----------------------
It was chosen over the RSI reversal and the trend rule for a **structural** reason
recorded in the original, not for a stronger literature. trade-copilot's V1-31 found
that a 252-bar training window with a 30-trade eligibility floor needs a signal on
roughly 12% of trading days, and that ANDing any quality filter onto an RSI(2) trigger
dropped every configuration below that floor - producing no evaluable folds and so no
verdict at all.

A gap has the property that its *trigger is already its quality measure*: how far the
open sits from the previous close is simultaneously "did this fire" and "how good is
it". There is nothing to AND on, so nothing to dilute. The original's search values were
then chosen so that **every one of them clears the floor** on all three symbols, at
30-37 trades per 252 bars.

That matters more here than it did there. This overlay's gate applies the same kind of
floor, so a premise that cannot fire often enough does not return a weak verdict - it
returns none, and the run looks like a bug.

The economic content is the overnight-to-intraday reversal, with the asymmetry that
gap-downs revert materially more often than gap-ups (~52% vs ~35% on SPY), which is why
the legs stay separate strategies rather than one rule taking the absolute gap.

Port note: entry fills at the signal bar's close
------------------------------------------------
**This is a real divergence from the original and it changes results.**

trade-copilot evaluates at the close and fills at the *next open*, and its docstring is
explicit that this is a weaker version of the published effect because much of the
intraday reversion has already happened by then.

Nautilus fills a market order submitted from ``on_bar`` at the **close of the bar that
triggered it** - verified against the engine, not assumed. That is not lookahead:
``on_bar`` fires after the bar has closed, so the price is known when the decision is
made. It is a market-on-close assumption, and it is mildly optimistic in a different
way: it assumes the closing print can be transacted at exactly the level that was just
used to decide.

Two consequences, both deliberate:

- **Verdicts from this port are not comparable with trade-copilot's** on the same
  premise. The entry is a session earlier, so the two are measuring different trades.
- The MOC assumption is exactly what the paper run's final step checks - compare
  realised fills against the modelled cost. Until then it is an assumption on the
  record, not a validated one.

Risk reporting
--------------
Sizing comes from the stop through :mod:`copilot.risk.sizing`, so a stop-out costs the
same currency amount whatever the instrument's price level, and R stays scale-free. The
strategy records what each position actually risked into
:class:`~copilot.validation.nautilus_replay.RiskAmountRegistry` - without that the gate
scores every trade at ``r_multiple == 0`` and reports no edge anywhere.

"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from copilot.risk.sizing import size_from_levels
from copilot.validation.types import Direction
from nautilus_trader.indicators import AverageTrueRange
from nautilus_trader.model import Bar
from nautilus_trader.model import OrderSide
from nautilus_trader.trading import Strategy
from nautilus_trader.trading import StrategyConfig


DEFAULT_ATR_PERIOD = 14
DEFAULT_MIN_GAP_ATR = "0.25"
DEFAULT_STOP_ATR = "1.5"
DEFAULT_TARGET_ATR = "1.0"
DEFAULT_RISK_BUDGET = "1000"
"""
Currency risked per trade.

Sizing floors to whole shares, so realised risk sits at or just under this and is
recorded per position rather than assumed.

"""


class GapReversalConfig(StrategyConfig):
    """
    Knobs for the gap fade.

    ``StrategyConfig`` is a pyo3 class, so custom fields follow the ``_CUSTOM_FIELDS``
    plus ``__new__`` pattern the project's own tutorials use - passing them straight to
    ``super().__new__`` raises.

    Values are carried as strings and converted to ``Decimal`` at use. These are price
    multiples that end up deciding order prices, and a float round trip would move them.

    """

    _CUSTOM_FIELDS = (
        "instrument_id",
        "bar_type",
        "atr_period",
        "min_gap_atr",
        "stop_atr",
        "target_1_atr",
        "risk_budget",
        "require_unfilled",
        "long",
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
        min_gap_atr: str = DEFAULT_MIN_GAP_ATR,
        stop_atr: str = DEFAULT_STOP_ATR,
        target_1_atr: str = DEFAULT_TARGET_ATR,
        risk_budget: str = DEFAULT_RISK_BUDGET,
        require_unfilled: bool = False,
        long: bool = True,
        **_kwargs: object,
    ) -> None:
        """
        Configure one leg of the fade.
        """
        super().__init__()
        self.instrument_id = instrument_id
        self.bar_type = bar_type
        self.atr_period = atr_period
        self.min_gap_atr = min_gap_atr
        self.stop_atr = stop_atr
        self.target_1_atr = target_1_atr
        self.risk_budget = risk_budget
        self.require_unfilled = require_unfilled
        self.long = long


class GapReversalStrategy(Strategy):
    """
    Fade an overnight gap, with an ATR stop and a nearer ATR target.
    """

    def __init__(self, config: GapReversalConfig) -> None:
        """
        Build the indicator and the state the rule needs.
        """
        super().__init__(config)
        self._atr = AverageTrueRange(config.atr_period)
        self._previous_close: Decimal | None = None
        self._registry: Any = None
        self._pending_risk = Decimal(0)
        self.skips: dict[str, int] = {}
        """
        Why the rule declined, counted by reason.

        Carried across from the original, which returns a reason rather than ``None``
        precisely so that "why was there no signal" stays a log line instead of a
        debugging session. It is also the first thing to read when a fold produces no
        trades: `insufficient_history` means the warm-up was set too short, while
        `setup_not_triggered` on every bar means the threshold is wrong.

        """

    def configure(self, registry: Any) -> None:
        """
        Attach the registry the replay reads risk amounts from.

        Separate from ``__init__`` because ``StrategyConfig`` is a pyo3 class and cannot
        carry a live Python object as a config field.

        """
        self._registry = registry

    def on_start(self) -> None:
        """
        Subscribe and let the engine feed the indicator.
        """
        self.register_indicator_for_bars(self.config.bar_type, self._atr)
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar) -> None:
        """
        Evaluate the rule on one closed bar.
        """
        previous_close, self._previous_close = self._previous_close, Decimal(str(bar.close))

        if not self._atr.initialized:
            self._skip("insufficient_history")
            return
        if previous_close is None:
            self._skip("insufficient_price_history")
            return

        atr = Decimal(str(self._atr.value))
        if atr <= 0:
            self._skip("non_positive_atr")
            return

        # One position at a time. The original's engine enforces this upstream of the
        # setup; here the rule has to, or a run of gap days stacks correlated entries
        # that the risk budget was never sized for.
        if not self.portfolio.is_net_flat(self.config.instrument_id):
            self._skip("position_already_open")
            return

        if not self._triggered(bar, previous_close, atr):
            return

        self._enter(bar, atr)

    def _triggered(self, bar: Bar, previous_close: Decimal, atr: Decimal) -> bool:
        """
        Whether this bar's gap fires the configured leg.
        """
        gap_ratio = (Decimal(str(bar.open)) - previous_close) / atr
        threshold = Decimal(self.config.min_gap_atr)

        # Signed, never absolute: the long leg must only ever fire on a gap *down*.
        # Taking the magnitude here would have each leg trade both kinds of gap, which
        # is exactly the pooling the leg split exists to prevent - the reversion rates
        # differ by direction, so a pooled rule lets the stronger leg carry the weaker.
        triggered = gap_ratio <= -threshold if self.config.long else gap_ratio >= threshold
        if not triggered:
            self._skip("setup_not_triggered")
            return False

        if self.config.require_unfilled:
            # Did the dislocation survive the session, or did the bell arbitrage it
            # away? A closed gap has already paid out the move this rule is trying to
            # capture, so entering afterwards is buying the reversion after it happened.
            close = Decimal(str(bar.close))
            survived = close < previous_close if self.config.long else close > previous_close
            if not survived:
                self._skip("gap_filled_intraday")
                return False

        return True

    def _enter(self, bar: Bar, atr: Decimal) -> None:
        """
        Size from the stop and submit the bracket.
        """
        instrument = self.cache.instrument(self.config.instrument_id)
        entry = Decimal(str(bar.close))
        stop_distance = atr * Decimal(self.config.stop_atr)
        target_distance = atr * Decimal(self.config.target_1_atr)

        if self.config.long:
            stop_price, target_price = entry - stop_distance, entry + target_distance
        else:
            stop_price, target_price = entry + stop_distance, entry - target_distance

        if stop_price <= 0 or target_price <= 0:
            # A stop below zero is not a tradeable level. Reachable on a low-priced
            # instrument in a high-ATR regime, where the original's `levels_are_valid_at`
            # rejects for the same reason.
            self._skip("invalid_levels")
            return

        direction = Direction.LONG if self.config.long else Direction.SHORT
        quantity, risk = size_from_levels(
            direction=direction,
            entry_price=entry,
            stop_price=stop_price,
            risk_budget=Decimal(self.config.risk_budget),
        )
        if quantity <= 0:
            # Exactly when a real risk engine vetoes. Skipping is right; inventing a
            # one-share trade production would never take is not.
            self._skip("unsizeable")
            return

        self._pending_risk = risk
        orders = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=OrderSide.BUY if self.config.long else OrderSide.SELL,
            quantity=instrument.make_qty(quantity),
            sl_trigger_price=instrument.make_price(stop_price),
            tp_price=instrument.make_price(target_price),
        )
        self.submit_order_list(orders)

    def on_position_opened(self, event: Any) -> None:
        """
        Report what this position put at risk.

        The contract the replay depends on. ``risk`` is the floored quantity times the
        stop distance, not the budget: quantity is floored to whole shares, so realised
        risk sits at or just under the budget and using the budget as the R denominator
        would overstate every trade by that rounding.

        """
        if self._registry is not None:
            self._registry.record(str(event.position_id), self._pending_risk)

    def _skip(self, reason: str) -> None:
        self.skips[reason] = self.skips.get(reason, 0) + 1


def strategy_factory(
    parameters: Any,
    *,
    instrument_id: Any,
    bar_type: Any,
    risk_registry: Any,
) -> GapReversalStrategy:
    """
    Build one configured strategy for a gate candidate.

    Matches :class:`~copilot.validation.nautilus_replay.StrategyFactory`, so this can be
    passed straight to ``make_replay`` and searched over by ``walk_forward``.
    ``parameters`` is a plain mapping of the searched axes, which is what
    ``ParameterGrid`` produces by default.

    """
    strategy = GapReversalStrategy(
        GapReversalConfig(
            instrument_id=instrument_id,
            bar_type=bar_type,
            atr_period=int(parameters.get("atr_period", DEFAULT_ATR_PERIOD)),
            min_gap_atr=str(parameters.get("min_gap_atr", DEFAULT_MIN_GAP_ATR)),
            stop_atr=str(parameters.get("stop_atr", DEFAULT_STOP_ATR)),
            target_1_atr=str(parameters.get("target_1_atr", DEFAULT_TARGET_ATR)),
            risk_budget=str(parameters.get("risk_budget", DEFAULT_RISK_BUDGET)),
            require_unfilled=bool(parameters.get("require_unfilled", False)),
            long=bool(parameters.get("long", True)),
        ),
    )
    strategy.configure(risk_registry)
    return strategy


__all__ = [
    "DEFAULT_ATR_PERIOD",
    "DEFAULT_MIN_GAP_ATR",
    "DEFAULT_RISK_BUDGET",
    "DEFAULT_STOP_ATR",
    "DEFAULT_TARGET_ATR",
    "GapReversalConfig",
    "GapReversalStrategy",
    "strategy_factory",
]
