"""
A ``Replay`` backed by a Nautilus ``BacktestEngine``.

The trade-copilot validation gate takes its replay as an argument:

    walk_forward(bars, grid, replay=...)
    search_in_sample(bars, grid, replay=...)

so swapping the engine underneath the gate is a matter of supplying a different
callable, not of rewriting the methodology. This module provides that callable.

Why bother, when trade-copilot already has a working replay? Because its fill model
is a flat spread proxy plus a fixed commission, and trade-copilot's own SYSTEM.md §14
records that this is *the number deciding every verdict*. Nautilus ships volume-,
size- and probability-sensitive fill models, fee models and a latency model, so the
same gate run on this replay is scored against a materially better cost model.

Scope
-----
This builds and runs the engine and maps its positions onto ``ClosedTrade``. It does
**not** supply the strategy - that is the caller's, because the parameter set under
search is exactly what varies per gate candidate. Provide a ``StrategyFactory`` that
turns one parameter set into a configured Nautilus ``Strategy``.

Risk amount
-----------
R requires the currency at risk when the position opened, which Nautilus does not
record: it knows the fill, not the stop that sized it. The strategy therefore has to
report it. Any strategy used with this replay must publish its per-position risk
through :class:`RiskAmountRegistry`, or every trade scores ``r_multiple == 0`` and the
gate silently sees no edge anywhere. :func:`run_nautilus_replay` raises rather than
returning that silently.

"""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from typing import Any
from typing import Protocol

from copilot.validation.types import BacktestRunResult
from copilot.validation.types import ClosedTrade
from copilot.validation.types import DailyBar
from copilot.validation.types import Direction
from nautilus_trader.backtest import BacktestEngine
from nautilus_trader.backtest import BacktestEngineConfig
from nautilus_trader.model import AccountType
from nautilus_trader.model import Bar
from nautilus_trader.model import BarType
from nautilus_trader.model import Money
from nautilus_trader.model import OmsType
from nautilus_trader.model import OrderSide
from nautilus_trader.model import TraderId
from nautilus_trader.model import Venue


class RiskAmountRegistry:
    """
    Per-run record of what each position put at risk.

    A plain dict behind a named type, because the coupling it represents - the strategy
    must tell the replay what it risked - is easy to forget and expensive to get wrong,
    and a named type is somewhere to say so.

    """

    def __init__(self) -> None:
        """
        Start with nothing recorded.
        """
        self._by_position_id: dict[str, Decimal] = {}

    def record(self, position_id: str, risk_amount: Decimal) -> None:
        """
        Note what one position put at risk when it opened.
        """
        self._by_position_id[str(position_id)] = Decimal(risk_amount)

    def get(self, position_id: str) -> Decimal | None:
        """
        Return recorded risk for a position, or ``None`` if the strategy never said.
        """
        return self._by_position_id.get(str(position_id))

    def __len__(self) -> int:
        """
        How many positions reported their risk.
        """
        return len(self._by_position_id)


class StrategyFactory(Protocol):
    """
    Builds a configured strategy for one parameter set.
    """

    def __call__(
        self,
        parameters: Any,
        *,
        instrument_id: Any,
        bar_type: BarType,
        risk_registry: RiskAmountRegistry,
    ) -> Any:
        """
        Return a strategy configured for ``parameters``.
        """
        ...


@dataclass(frozen=True)
class ReplayVenue:
    """
    Venue setup for the simulated exchange.
    """

    name: str | None = None
    """
    Defaults to the instrument's own venue.

    A venue that does not match the
    instrument's is rejected by the engine at ``add_instrument``, so guessing a name
    here can only ever be wrong.

    """

    oms_type: Any = OmsType.HEDGING
    """
    HEDGING, not NETTING, and the gate's scoring depends on it.

    Under NETTING, Nautilus reuses one position id per instrument and strategy, so
    ``cache.positions_closed()`` holds a single position object that is reopened and
    closed over and over. Only the final round trip survives to be scored. Measured on
    60 bars of real AAPL history with a strategy that alternates in and out every bar:
    NETTING yields **1** scoreable trade, HEDGING yields **30**.

    That is not a reporting detail. ``expectancy_r`` over one trade is noise, the
    gate's ``min_trades`` floor rejects every candidate, and a walk-forward run comes
    back "selected nothing" on every fold while looking like it worked.
    ``RiskAmountRegistry`` is keyed by position id and aliases the same way.

    A strategy that genuinely wants netting semantics should net its own exposure;
    the *record* of what was traded has to stay per round trip for R to mean anything.

    """
    account_type: Any = AccountType.MARGIN
    starting_balance: str = "100_000"
    currency: str = "USD"
    fill_model: Any = None
    fee_model: Any = None
    latency_model: Any = None
    modules: tuple[Any, ...] = field(default_factory=tuple)


def bars_to_nautilus(
    daily_bars: Sequence[DailyBar],
    instrument: Any,
    bar_type: BarType,
) -> list[Bar]:
    """
    Convert vendored ``DailyBar`` records into Nautilus ``Bar`` objects.

    Prices and quantities go through the instrument's own precision so the engine sees
    values it can represent exactly, rather than floats that round differently from the
    venue.

    """
    out: list[Bar] = []
    for daily in sorted(daily_bars, key=lambda b: b.closed_at):
        ts = int(daily.closed_at.timestamp() * 1e9)
        out.append(
            Bar(
                bar_type=bar_type,
                open=instrument.make_price(daily.open),
                high=instrument.make_price(daily.high),
                low=instrument.make_price(daily.low),
                close=instrument.make_price(daily.close),
                volume=instrument.make_qty(daily.volume),
                ts_event=ts,
                ts_init=ts,
            ),
        )
    return out


def _engine_over(
    daily_bars: Sequence[DailyBar],
    *,
    instrument: Any,
    bar_type: BarType,
    venue: ReplayVenue,
) -> BacktestEngine:
    """
    Build an engine holding the venue, the instrument and the bars, and no strategy.

    Shared by the scoring replay and the decision replay so the two run the identical
    engine: a comparison between a live decision and an offline one is only worth
    making if "offline" means the engine the gate scores with.

    """
    venue_obj = Venue(venue.name) if venue.name else instrument.id.venue
    engine = BacktestEngine(
        BacktestEngineConfig(trader_id=TraderId.from_str("COPILOT-WFA-001")),
    )
    add_venue_kwargs: dict[str, Any] = {
        "venue": venue_obj,
        "oms_type": venue.oms_type,
        "account_type": venue.account_type,
        "base_currency": instrument.quote_currency,
        "starting_balances": [
            Money(Decimal(venue.starting_balance.replace("_", "")), instrument.quote_currency),
        ],
    }
    # Only pass the optional models when supplied: the engine's own defaults are
    # better than a None we invented.
    add_venue_kwargs.update(
        {
            key: value
            for key, value in (
                ("fill_model", venue.fill_model),
                ("fee_model", venue.fee_model),
                ("latency_model", venue.latency_model),
            )
            if value is not None
        },
    )
    if venue.modules:
        add_venue_kwargs["modules"] = list(venue.modules)

    engine.add_venue(**add_venue_kwargs)
    engine.add_instrument(instrument)
    engine.add_data(bars_to_nautilus(daily_bars, instrument, bar_type))
    return engine


def run_nautilus_replay(
    daily_bars: Sequence[DailyBar],
    parameters: Any,
    *,
    instrument: Any,
    bar_type: BarType,
    strategy_factory: StrategyFactory,
    venue: ReplayVenue | None = None,
    require_risk_amounts: bool = True,
) -> BacktestRunResult:
    """
    Run one parameter set over ``daily_bars`` and return scoreable trades.
    """
    if not daily_bars:
        return BacktestRunResult(diagnostics={"reason": "no bars supplied"})

    venue = venue or ReplayVenue()
    registry = RiskAmountRegistry()
    engine = _engine_over(daily_bars, instrument=instrument, bar_type=bar_type, venue=venue)
    strategy = strategy_factory(
        parameters,
        instrument_id=instrument.id,
        bar_type=bar_type,
        risk_registry=registry,
    )
    engine.add_strategy(strategy)

    try:
        engine.run()
        trades = _closed_trades(engine, registry, require_risk_amounts=require_risk_amounts)
        return BacktestRunResult(
            trades=tuple(trades),
            diagnostics={"positions": len(trades), "risk_records": len(registry)},
        )
    finally:
        engine.reset()
        engine.dispose()


def run_to_decision[T](
    daily_bars: Sequence[DailyBar],
    parameters: Any,
    *,
    instrument: Any,
    bar_type: BarType,
    strategy_factory: StrategyFactory,
    inspect: Callable[[Any], T],
    warmup: int = 0,
    venue: ReplayVenue | None = None,
) -> T:
    """
    Run one parameter set over ``daily_bars`` and return what ``inspect`` reads.

    The scoring replay returns closed trades and disposes the strategy with the engine.
    The playbook's live-versus-replay comparison needs the other thing: the state the
    rule holds after its last bar - triggered, deferred, declined and why - which for a
    ``next_close`` rule is never a trade, because the entry belongs to the bar after the
    window ends. ``inspect`` is called before the engine is disposed and its result is
    all that leaves.

    ``warmup`` bars are handed to the strategy's ``warm_up`` before the engine starts and
    are never seen by ``on_bar``, which is how the live path treats them: history that
    charges the indicator and sets the previous close, on which no decision is made.
    Feeding them through the engine instead would let the rule trade during the warm-up
    and arrive at the decision bar in a state the live session was never in. The bars
    after ``warmup`` go through the engine, so the decision bar reaches the rule the way
    the gate's replay delivers it - indicators updated first, then ``on_bar``.

    """
    if not daily_bars:
        raise ValueError("no bars supplied; a decision needs at least one bar to decide on")
    if not 0 <= warmup < len(daily_bars):
        raise ValueError(f"warmup={warmup} leaves no decision bar out of {len(daily_bars)}")
    venue = venue or ReplayVenue()
    ordered = sorted(daily_bars, key=lambda b: b.closed_at)
    engine = _engine_over(ordered[warmup:], instrument=instrument, bar_type=bar_type, venue=venue)
    strategy = strategy_factory(
        parameters,
        instrument_id=instrument.id,
        bar_type=bar_type,
        risk_registry=RiskAmountRegistry(),
    )
    if warmup:
        strategy.warm_up(bars_to_nautilus(ordered[:warmup], instrument, bar_type))
    engine.add_strategy(strategy)
    try:
        engine.run()
        return inspect(strategy)
    finally:
        engine.reset()
        engine.dispose()


def _closed_trades(
    engine: BacktestEngine,
    registry: RiskAmountRegistry,
    *,
    require_risk_amounts: bool,
) -> list[ClosedTrade]:
    """
    Map the engine's closed positions onto the gate's scoring type.
    """
    trades: list[ClosedTrade] = []
    missing: list[str] = []

    for position in engine.cache.positions_closed():
        risk_amount = registry.get(str(position.id))
        if risk_amount is None:
            missing.append(str(position.id))
            continue

        opened_at = _ns_to_dt(position.ts_opened)
        closed_at = _ns_to_dt(position.ts_closed)
        trades.append(
            ClosedTrade(
                symbol=str(position.instrument_id),
                # `is_long` / `is_short` read the *current* quantity, which is zero
                # once a position closes, so every closed trade would map to SHORT.
                # `entry` records the side the position was opened on and stays
                # valid afterwards.
                direction=Direction.LONG if position.entry == OrderSide.BUY else Direction.SHORT,
                quantity=int(position.peak_qty.as_double()),
                entry_price=Decimal(str(position.avg_px_open)),
                exit_price=Decimal(str(position.avg_px_close)),
                exit_reason="CLOSED",
                signal_created_at=opened_at,
                opened_at=opened_at,
                closed_at=closed_at,
                realized_pnl=Decimal(str(position.realized_pnl.as_double()))
                if position.realized_pnl is not None
                else Decimal(0),
                risk_amount=risk_amount,
            ),
        )

    if missing and require_risk_amounts:
        raise ValueError(
            f"{len(missing)} closed position(s) have no recorded risk amount "
            f"(e.g. {missing[0]}). Every trade would score r_multiple == 0 and the "
            "gate would see no edge anywhere. The strategy must call "
            "RiskAmountRegistry.record() when it opens a position.",
        )
    return trades


def _ns_to_dt(ts_ns: int | None) -> datetime:
    if ts_ns is None:
        return datetime.fromtimestamp(0, tz=UTC)
    return datetime.fromtimestamp(ts_ns / 1e9, tz=UTC)


def make_replay(
    *,
    instrument: Any,
    bar_type: BarType,
    strategy_factory: StrategyFactory,
    venue: ReplayVenue | None = None,
) -> Callable[[Sequence[DailyBar], Any], BacktestRunResult]:
    """
    Bind the fixed arguments and return a gate-compatible ``Replay``.

    The result matches ``Callable[[Sequence[DailyBar], StrategyParameters],
    BacktestRunResult]`` and can be passed straight to ``walk_forward(replay=...)``.

    """

    def _replay(bars: Sequence[DailyBar], parameters: Any) -> BacktestRunResult:
        return run_nautilus_replay(
            bars,
            parameters,
            instrument=instrument,
            bar_type=bar_type,
            strategy_factory=strategy_factory,
            venue=venue,
        )

    return _replay


__all__ = [
    "ReplayVenue",
    "RiskAmountRegistry",
    "StrategyFactory",
    "bars_to_nautilus",
    "make_replay",
    "run_nautilus_replay",
    "run_to_decision",
]
