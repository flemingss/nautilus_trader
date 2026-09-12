"""
Tests for pointing the randomised-signal control at a premise.

One of these exists because of a defect it would have caught. The first version of
``development_bars`` read the activation's *own* holdout boundary and treated ``None`` as
"nothing withheld", so an activation carved at the shared 2022-01-01 pin had its control run over
2005 to 2025: a look at a locked holdout. The window a control draws from is therefore pinned
here, for both kinds of activation.

"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

import pytest

from copilot.strategies.activations import Activation
from copilot.strategies.activations import Lifecycle
from copilot.strategies.activations import ValidationSettings
from copilot.strategies.null_run import MIN_SCOREABLE_TRADES
from copilot.strategies.null_run import development_bars
from copilot.strategies.null_run import score
from copilot.validation.holdout import HoldoutCarveError
from copilot.validation.types import ClosedTrade
from copilot.validation.types import DailyBar
from copilot.validation.types import Direction


def activation(**validation: object) -> Activation:
    """
    A SPY activation, declaring whatever validation settings the test needs.
    """
    return Activation(
        name="spy-test",
        strategy="turn_of_month",
        lifecycle=Lifecycle.RESEARCH,
        symbol="SPY",
        venue="ARCX",
        validation=ValidationSettings(**validation),  # type: ignore[arg-type]
    )


def series() -> tuple[DailyBar, ...]:
    """
    Daily bars from 2005 through 2025, one a week, so every window has some.
    """
    start = datetime(2005, 1, 3, tzinfo=UTC)
    return tuple(
        DailyBar(
            symbol="SPY",
            closed_at=start + timedelta(weeks=i),
            open=Decimal(100),
            high=Decimal(101),
            low=Decimal(99),
            close=Decimal(100),
            volume=1_000_000,
        )
        for i in range(1_096)
    )


class TestWindow:
    """
    What a control may draw from, which is the development window and nothing else.
    """

    def test_an_activation_with_no_boundary_still_gets_the_shared_pin(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        The defect this file exists for: ``None`` means the shared 2022-01-01 pin, not no
        holdout.
        """
        monkeypatch.setattr("copilot.strategies.null_run.read_series", lambda *_a, **_k: series())

        bars = development_bars(activation(), "unused")

        assert bars[-1].closed_at < datetime(2022, 1, 1, tzinfo=UTC)

    def test_a_boundary_outside_the_charters_band_is_refused_not_quietly_used(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        A control cannot draw from a window the carve itself would not accept.

        2020-01-01 reserves 28.6% of this series, past the charter's 15-20%, and `carve` refuses
        rather than drifting - which is the behaviour a control has to inherit rather than work
        around.

        """
        monkeypatch.setattr("copilot.strategies.null_run.read_series", lambda *_a, **_k: series())

        with pytest.raises(HoldoutCarveError, match="outside the charter"):
            development_bars(activation(holdout_start="2020-01-01"), "unused")

    def test_bars_past_the_evaluation_window_are_excluded(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("copilot.strategies.null_run.read_series", lambda *_a, **_k: series())

        bars = development_bars(activation(), "unused")

        assert all(bar.closed_at < datetime(2026, 1, 1, tzinfo=UTC) for bar in bars)


class TestScore:
    """
    A replicate's score, and when it is not one.
    """

    def trade(self, r: str) -> ClosedTrade:
        moment = datetime(2010, 6, 1, tzinfo=UTC)
        return ClosedTrade(
            symbol="SPY",
            direction=Direction.LONG,
            quantity=100,
            entry_price=Decimal(100),
            exit_price=Decimal(100) + Decimal(r),
            exit_reason="CLOSED",
            signal_created_at=moment,
            opened_at=moment,
            closed_at=moment + timedelta(days=3),
            realized_pnl=Decimal(r) * 100,
            risk_amount=Decimal(100),
        )

    def test_too_few_trades_scores_nothing(self) -> None:
        """
        A draw with a handful of trades has a mean one trade decides; the control counts
        it as skipped rather than averaging it in.
        """
        trades = [self.trade("1")] * (MIN_SCOREABLE_TRADES - 1)
        assert score(trades, "SPY", _FreeCosts()) is None

    def test_enough_trades_score_their_mean_net_of_costs(self) -> None:
        trades = [self.trade("1")] * MIN_SCOREABLE_TRADES
        assert score(trades, "SPY", _FreeCosts()) == Decimal(1)

    def test_costs_come_off_every_trade(self) -> None:
        trades = [self.trade("1")] * MIN_SCOREABLE_TRADES
        assert score(trades, "SPY", _FixedCosts(Decimal("0.25"))) == Decimal("0.75")


class _FreeCosts:
    """
    A cost model that charges nothing, so a score can be checked against arithmetic.
    """

    def cost_r(self, _trade: object, _symbol: str) -> Decimal:
        return Decimal(0)


class _FixedCosts:
    """
    A cost model charging the same R on every trade.
    """

    def __init__(self, charge: Decimal) -> None:
        self._charge = charge

    def cost_r(self, _trade: object, _symbol: str) -> Decimal:
        return self._charge
