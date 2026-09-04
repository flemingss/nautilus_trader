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

Entry timing: a bracket, not a point (ADR-0013)
-----------------------------------------------
The charter requires trading no earlier than the next eligible session; this port
originally filled at the signal bar's close. Neither the original's next-*open* entry
nor the charter's concession-bounded window is expressible on a daily-bar replay -
the next session's open is consumed as a matching tick before any order submitted from
``on_bar`` can arrive, and the matching engine rejects ``AT_THE_OPEN`` outright
(measured; the table is in
`ADR-0013 <../docs/decisions/0013-entry-timing-is-evaluated-as-a-bracket.md>`_).

So ``entry_timing`` selects one of the two bounds that *are* expressible:

- ``"signal_close"`` - the **optimistic bound**. A market order submitted from
  ``on_bar(t)`` settles against the book bar t left, so it fills at the close that was
  just used to decide. Not lookahead - ``on_bar`` fires after the close - but it assumes
  the closing print is transactable at that level. Diagnostic only; never promotable
  past RESEARCH and never the holdout candidate.
- ``"next_close"`` - the **pessimistic bound**, and the only spendable one. The decision
  freezes at bar t (trigger, direction, and the ATR the levels are built from); the
  entry submits on bar t+1 and fills at *its* close, giving away a full further session
  of the reversion - deliberately more than the charter's first-hours window would.

Verdicts are never comparable across timing modes, and neither mode is comparable with
trade-copilot's next-open verdicts on the same premise.

The fill assumption at either bound is exactly what the paper run's final step checks -
compare realised fills against the modelled cost. Until then it is an assumption on the
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

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING
from typing import Any

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
DEFAULT_MIN_GAP_ATR = "0.25"
DEFAULT_STOP_ATR = "1.5"
DEFAULT_TARGET_ATR = "1.0"
DEFAULT_RISK_BUDGET = "1000"
"""
Currency risked per trade.

Sizing floors to whole shares, so realised risk sits at or just under this and is
recorded per position rather than assumed.

"""


# The searched axes, and the ceiling on the one that matters.
#
# `min_gap_atr` is searched because the honest answer to "how big is a gap" is that
# nobody knows. `target_1_atr` is searched because how *far* a gap reverts is the second
# genuinely unknown quantity. The stop is not searched: a fade that has not worked within
# 1.5 ATR is a wrong thesis, not a developing one.
#
# The values were chosen by **counting events first**, not by picking a plausible range.
# trade-copilot's V1-31 searched quality gates whose every setting produced no evaluable
# folds, so the run returned the absence of a verdict rather than a verdict. Each value
# here clears the in-sample eligibility floor on its own; 0.40 is the loosest that still
# does, and 0.50 falls under it. `test_gap_reversal.py` pins that ceiling so the axis
# cannot be widened without re-counting.
#
# The size is also deliberate. The best score obtainable from pure noise grows with the
# number of trials, so a six-point space buys real headroom against the deflation
# statistic that an 81-point one does not.
SEARCH_SPACE: dict[str, tuple[Decimal, ...]] = {
    "min_gap_atr": (Decimal("0.15"), Decimal("0.25"), Decimal("0.40")),
    "target_1_atr": (Decimal("1.0"), Decimal("1.5")),
}

MAX_SEARCHABLE_MIN_GAP_ATR = Decimal("0.40")
"""
Loosest threshold that still clears the eligibility floor.

Widening past this produces folds with too few trades to score, which the gate reports
as no verdict rather than a weak one - the failure that looks like a bug.

"""

ENTRY_TIMINGS = ("signal_close", "next_close")
"""
The two expressible bounds of ADR-0013's bracket.

Anything else is a typo.

"""

WARMUP_BARS = DEFAULT_ATR_PERIOD + 2
"""
History the rule needs before it can fire.

The trigger compares this bar's open against the previous close, so two bars is the true
requirement; sized to the ATR window anyway so a warm-up taken from this figure is never
shorter than the indicator needs.

"""


@dataclass(frozen=True)
class Sizing:
    """
    The two numbers a position is sized with, kept together so they cannot come apart.
    """

    risk_budget: Decimal
    max_notional: Decimal | None = None


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
        "max_notional",
        "require_unfilled",
        "long",
        "entry_timing",
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
        max_notional: str = "",
        require_unfilled: bool = False,
        long: bool = True,
        entry_timing: str = "signal_close",
        **_kwargs: object,
    ) -> None:
        """
        Configure one leg of the fade.
        """
        if entry_timing not in ENTRY_TIMINGS:
            # A misspelled mode would otherwise run the optimistic bound while the
            # operator believes the charter-compliant one is being measured.
            raise ValueError(
                f"entry_timing must be one of {ENTRY_TIMINGS}, got {entry_timing!r}",
            )
        super().__init__()
        self.instrument_id = instrument_id
        self.bar_type = bar_type
        self.atr_period = atr_period
        self.min_gap_atr = min_gap_atr
        self.stop_atr = stop_atr
        self.target_1_atr = target_1_atr
        self.risk_budget = risk_budget
        self.max_notional = max_notional
        self.require_unfilled = require_unfilled
        self.long = long
        self.entry_timing = entry_timing


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
        self._sizing = Sizing(
            risk_budget=Decimal(config.risk_budget),
            max_notional=Decimal(config.max_notional) if config.max_notional else None,
        )
        self._deferred_atr: Decimal | None = None
        """
        A decision frozen at the signal bar, awaiting its session (``next_close`` only).

        The ATR is the whole frozen state: direction and level multiples are config, and
        the entry anchors to the fill bar's close, so the signal-time ATR is the one
        input the deferral has to carry across.

        """
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

    def size_against(self, risk_budget: Decimal, max_notional: Decimal | None) -> None:
        """
        Replace the sizing the config seeded with numbers derived from the account.

        The config's ``risk_budget`` is a research R-unit, USD 1,000 by default, chosen so
        scores compare across instruments. It is not an amount anyone decided to risk, and
        a live session sizes with what the account can carry instead: the playbook's
        ``R = A * r`` and its notional cap, derived from the equity the broker reported
        once the session is up. That is after construction by necessity - the strategy
        exists before the connection does - which is why this is a hook like ``warm_up``
        rather than a config field.

        One place holds the numbers ``on_bar`` sizes with, whichever path set them. Two
        would let the config's research budget survive into a live decision by default,
        which is exactly the state this hook was written to end.

        """
        if risk_budget <= 0:
            raise ValueError(f"risk_budget must be positive, got {risk_budget}")
        if max_notional is not None and max_notional <= 0:
            raise ValueError(f"max_notional must be positive when given, got {max_notional}")
        self._sizing = Sizing(risk_budget=risk_budget, max_notional=max_notional)

    def warm_up(self, bars: Sequence[Bar]) -> None:
        """
        Prime the indicator and the previous close from history, before the node runs.

        A backtest gets its warm-up for free: every bar reaches ``on_bar``, so by the
        time the rule can fire the ATR is initialised and ``_previous_close`` holds the
        prior session. Live, the subscription starts empty, and an unwarmed strategy
        spends sixteen sessions logging `insufficient_history` while looking like a
        premise that simply never triggers.

        Fed through ``handle_bar`` - the same call the engine makes - so an indicator
        warmed here holds the value it would hold having been run, rather than an
        approximation of it. ``_previous_close`` takes the last warm bar's close, which
        is exactly what ``on_bar`` would have left there.

        Bars come from :mod:`copilot.live.warmup`, which refuses to supply a stale or
        holed window; nothing is validated again here, because a strategy is the wrong
        place to discover that the catalog is out of date.

        """
        for bar in bars:
            self._atr.handle_bar(bar)
            self._previous_close = Decimal(str(bar.close))

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

        # A deferred entry executes on this bar before anything else is considered: the
        # decision was made at the previous close and this bar is its session. Either
        # way it consumes this bar's action - evaluating a fresh signal on the same bar
        # would stack a second commitment the risk budget was never sized for.
        if self._execute_deferred(bar):
            return

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

        if self._triggered(bar, previous_close, atr):
            if self.config.entry_timing == "next_close":
                # Freeze the decision; the next session executes it. A signal on the
                # last bar of a window simply never fills, the same trade the charter's
                # own rule would have left on the table.
                self._deferred_atr = atr
            else:
                self._enter(bar, atr)

    def _execute_deferred(self, bar: Bar) -> bool:
        """
        Fill a decision frozen on the previous bar.

        Returns whether this bar was consumed by it.

        """
        if self._deferred_atr is None:
            return False
        deferred_atr, self._deferred_atr = self._deferred_atr, None
        if self.portfolio.is_net_flat(self.config.instrument_id):
            self._enter(bar, deferred_atr)
        else:
            self._skip("deferred_entry_blocked_by_position")
        return True

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
            risk_budget=self._sizing.risk_budget,
            max_notional=self._sizing.max_notional,
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
            max_notional=str(parameters.get("max_notional", "")),
            require_unfilled=bool(parameters.get("require_unfilled", False)),
            long=bool(parameters.get("long", True)),
            entry_timing=str(parameters.get("entry_timing", "signal_close")),
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
    "ENTRY_TIMINGS",
    "MAX_SEARCHABLE_MIN_GAP_ATR",
    "SEARCH_SPACE",
    "WARMUP_BARS",
    "GapReversalConfig",
    "GapReversalStrategy",
    "strategy_factory",
]
