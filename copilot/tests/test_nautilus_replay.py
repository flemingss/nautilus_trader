"""End-to-end test of the Nautilus-backed ``Replay``.

Runs a real ``BacktestEngine`` over synthetic bars with a deliberately trivial
strategy, so the thing under test is the *plumbing* — bar conversion, the risk
registry contract, and the position-to-``ClosedTrade`` mapping — rather than any
trading idea.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from nautilus_trader.model import BarType, InstrumentId, OrderSide
from nautilus_trader.testkit.providers import TestInstrumentProvider
from nautilus_trader.trading import Strategy, StrategyConfig

from copilot.validation.nautilus_replay import (
    ReplayVenue,
    RiskAmountRegistry,
    bars_to_nautilus,
    make_replay,
    run_nautilus_replay,
)
from copilot.validation.types import DailyBar, Direction, expectancy_r

INSTRUMENT = TestInstrumentProvider.default_fx_ccy("AUD/USD")
BAR_TYPE = BarType.from_str(f"{INSTRUMENT.id}-1-DAY-LAST-EXTERNAL")
START = datetime(2026, 1, 1, tzinfo=UTC)


def make_bars(closes: list[str]) -> list[DailyBar]:
    bars = []
    for i, close in enumerate(closes):
        price = Decimal(close)
        bars.append(
            DailyBar(
                symbol=str(INSTRUMENT.id),
                closed_at=START + timedelta(days=i),
                open=price,
                high=price + Decimal("0.0010"),
                low=price - Decimal("0.0010"),
                close=price,
                volume=1_000_000,
            ),
        )
    return bars


class _FlipFlopConfig(StrategyConfig):
    """Custom fields follow the pyo3 subclassing pattern used by the tutorials."""

    _CUSTOM_FIELDS = ("instrument_id", "bar_type", "trade_size", "stop_distance")

    def __new__(cls, *args: object, **kwargs: object):  # noqa: ANN204
        for field_name in cls._CUSTOM_FIELDS:
            kwargs.pop(field_name, None)
        return super().__new__(cls, *args, **kwargs)

    def __init__(
        self,
        instrument_id,  # noqa: ANN001
        bar_type,  # noqa: ANN001
        trade_size: int = 10_000,
        stop_distance: str = "0.0050",
        **_kwargs: object,
    ) -> None:
        super().__init__()
        self.instrument_id = instrument_id
        self.bar_type = bar_type
        self.trade_size = trade_size
        self.stop_distance = stop_distance


class _FlipFlop(Strategy):
    """Opens on one bar and closes on the next, recording what it risked."""

    def __init__(self, config: _FlipFlopConfig) -> None:
        super().__init__(config)
        self._registry: RiskAmountRegistry | None = None
        self._bars_seen = 0

    def configure(self, registry: RiskAmountRegistry) -> None:
        self._registry = registry

    def on_start(self) -> None:
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar) -> None:  # noqa: ANN001
        self._bars_seen += 1
        instrument_id = self.config.instrument_id
        if self.portfolio.is_net_flat(instrument_id):
            if self._bars_seen % 2 == 1:
                instrument = self.cache.instrument(instrument_id)
                order = self.order_factory.market(
                    instrument_id,
                    OrderSide.BUY,
                    instrument.make_qty(self.config.trade_size),
                )
                self.submit_order(order)
        else:
            self.close_all_positions(instrument_id)

    def on_position_opened(self, event) -> None:  # noqa: ANN001
        # The contract the replay depends on: report what this position put at risk.
        if self._registry is not None:
            risk = Decimal(self.config.stop_distance) * Decimal(self.config.trade_size)
            self._registry.record(str(event.position_id), risk)


def strategy_factory(parameters, *, instrument_id, bar_type, risk_registry):  # noqa: ANN001,ANN201
    strategy = _FlipFlop(
        _FlipFlopConfig(
            instrument_id=instrument_id,
            bar_type=bar_type,
            trade_size=parameters.get("trade_size", 10_000),
            stop_distance=parameters.get("stop_distance", "0.0050"),
        ),
    )
    strategy.configure(risk_registry)
    return strategy


class TestBarConversion:
    def test_converts_and_orders_by_close_time(self):
        bars = make_bars(["0.7000", "0.7100", "0.6900"])
        shuffled = [bars[2], bars[0], bars[1]]
        converted = bars_to_nautilus(shuffled, INSTRUMENT, BAR_TYPE)
        assert len(converted) == 3
        assert [b.ts_event for b in converted] == sorted(b.ts_event for b in converted)

    def test_prices_use_instrument_precision(self):
        converted = bars_to_nautilus(make_bars(["0.7000"]), INSTRUMENT, BAR_TYPE)
        assert converted[0].close.precision == INSTRUMENT.price_precision


class TestRiskAmountRegistry:
    def test_records_and_reads_back(self):
        registry = RiskAmountRegistry()
        registry.record("P-1", Decimal("50"))
        assert registry.get("P-1") == Decimal("50")
        assert registry.get("P-2") is None
        assert len(registry) == 1


class TestReplay:
    def test_empty_bars_returns_empty_result(self):
        result = run_nautilus_replay(
            [],
            {},
            instrument=INSTRUMENT,
            bar_type=BAR_TYPE,
            strategy_factory=strategy_factory,
        )
        assert result.trades == ()
        assert "no bars" in result.diagnostics["reason"]

    def test_produces_scoreable_trades(self):
        bars = make_bars(["0.7000", "0.7100", "0.7050", "0.7150", "0.7100", "0.7200"])
        result = run_nautilus_replay(
            bars,
            {"trade_size": 10_000, "stop_distance": "0.0050"},
            instrument=INSTRUMENT,
            bar_type=BAR_TYPE,
            strategy_factory=strategy_factory,
            venue=ReplayVenue(starting_balance="1_000_000"),
        )
        assert result.trades, "expected at least one closed round trip"
        for trade in result.trades:
            assert trade.risk_amount > 0
            assert trade.direction is Direction.LONG
            # r_multiple must be derived from the recorded risk, not assumed.
            assert trade.r_multiple == trade.realized_pnl / trade.risk_amount

    def test_expectancy_is_computable_from_the_result(self):
        bars = make_bars(["0.7000", "0.7100", "0.7050", "0.7150"])
        result = run_nautilus_replay(
            bars,
            {},
            instrument=INSTRUMENT,
            bar_type=BAR_TYPE,
            strategy_factory=strategy_factory,
            venue=ReplayVenue(starting_balance="1_000_000"),
        )
        assert isinstance(expectancy_r(result), Decimal)

    def test_missing_risk_amount_raises_rather_than_scoring_zero(self):
        # The failure this guards is silent: without the registry every trade would
        # score r_multiple == 0 and the gate would report "no edge" everywhere.
        def forgetful_factory(parameters, *, instrument_id, bar_type, risk_registry):  # noqa: ANN001,ANN202
            return strategy_factory(
                parameters,
                instrument_id=instrument_id,
                bar_type=bar_type,
                risk_registry=RiskAmountRegistry(),  # discarded, never read back
            )

        bars = make_bars(["0.7000", "0.7100", "0.7050", "0.7150"])
        with pytest.raises(ValueError, match="risk amount"):
            run_nautilus_replay(
                bars,
                {},
                instrument=INSTRUMENT,
                bar_type=BAR_TYPE,
                strategy_factory=forgetful_factory,
                venue=ReplayVenue(starting_balance="1_000_000"),
            )

    def test_make_replay_matches_the_gate_signature(self):
        replay = make_replay(
            instrument=INSTRUMENT,
            bar_type=BAR_TYPE,
            strategy_factory=strategy_factory,
            venue=ReplayVenue(starting_balance="1_000_000"),
        )
        result = replay(make_bars(["0.7000", "0.7100", "0.7050", "0.7150"]), {})
        assert hasattr(result, "trades")


class TestGateOnNautilusReplay:
    """The fusion itself: trade-copilot's gate driven by a Nautilus BacktestEngine.

    This is what the whole overlay exists to make possible, so it is worth an
    end-to-end check rather than trusting that two tested halves compose.
    """

    def test_walk_forward_runs_on_the_nautilus_replay(self):
        from copilot.validation.insample import ParameterGrid
        from copilot.validation.walkforward import walk_forward

        replay = make_replay(
            instrument=INSTRUMENT,
            bar_type=BAR_TYPE,
            strategy_factory=strategy_factory,
            venue=ReplayVenue(starting_balance="1_000_000"),
        )
        # A small grid over a real engine: each point is a full backtest, so keep it
        # tight. The stop distance changes the risk denominator, which is what makes
        # the candidates score differently at all.
        grid = ParameterGrid.of(stop_distance=["0.0040", "0.0050", "0.0060"])

        series = make_bars([f"0.70{i % 90:02d}" for i in range(120)])
        report = walk_forward(
            series,
            grid,
            train_bars=40,
            test_bars=20,
            purge_bars=3,
            warmup_bars=3,
            replay=replay,
            min_trades=1,
            fold_min_trades=1,
        )

        assert report.folds, "the series should support at least one fold"
        assert report.evaluated, "the search should select something to test"
        # The verdict itself is not asserted — this rule has no edge and the point is
        # that the machinery produces a verdict at all, not which one.
        assert isinstance(report.majority_passed, bool)
        assert report.tearsheet.trades >= 0
        for fold in report.evaluated:
            assert fold.selected is not None
            assert fold.selected.parameters["stop_distance"] in {
                "0.0040",
                "0.0050",
                "0.0060",
            }
