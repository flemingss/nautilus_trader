"""
Tests for the stressed gap allowance: the measurement, and the pin it produces.

The properties that matter are that the measurement never reads a holdout bar, that a
pinned figure is a gap that actually happened, and that no activation can quietly size
itself with a number the measurement does not support.

"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

import pytest

from copilot.calibration.gap_history import MIN_SAMPLES
from copilot.calibration.gap_history import adverse_gaps
from copilot.calibration.gap_history import average_true_range
from copilot.calibration.gap_history import percentile
from copilot.calibration.gap_history import true_range
from copilot.risk.gap_stress import GAP_ATR
from copilot.risk.gap_stress import UnmeasuredSymbolError
from copilot.risk.gap_stress import allowance_atr
from copilot.risk.gap_stress import allowance_for
from copilot.strategies.activations import load_activations
from copilot.validation.types import DailyBar


def bar(day: int, *, open_: str, high: str, low: str, close: str) -> DailyBar:
    return DailyBar(
        symbol="TEST",
        closed_at=datetime(2020, 1, 1, tzinfo=UTC) + timedelta(days=day),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(1_000),
    )


def flat_series(count: int, *, close: str = "100") -> list[DailyBar]:
    """
    A series with a one-point range every session, so its ATR is exactly one.
    """
    return [
        bar(
            i,
            open_=close,
            high=str(Decimal(close) + Decimal("0.5")),
            low=str(Decimal(close) - Decimal("0.5")),
            close=close,
        )
        for i in range(count)
    ]


class TestPercentile:
    def test_it_is_nearest_rank_not_interpolated(self):
        # Ten observations: the 95th percentile is the tenth, not a blend of two.
        values = [Decimal(i) for i in range(1, 11)]
        assert percentile(values, Decimal("0.95")) == Decimal(10)
        assert percentile(values, Decimal("0.50")) == Decimal(5)

    def test_every_returned_figure_is_an_observation(self):
        values = [Decimal("0.1"), Decimal("7.3"), Decimal("2.2")]
        for share in ("0.10", "0.50", "0.99"):
            assert percentile(values, Decimal(share)) in values

    def test_an_empty_sample_is_refused(self):
        with pytest.raises(ValueError, match="no observations"):
            percentile([], Decimal("0.99"))


class TestTrueRange:
    def test_it_takes_the_widest_of_the_three_measures(self):
        previous = bar(0, open_="100", high="101", low="99", close="100")
        gapped = bar(1, open_="90", high="91", low="89", close="90")
        # The gap from the previous close dominates the bar's own range.
        assert true_range(gapped, previous) == Decimal(11)

    def test_a_flat_series_has_a_unit_atr(self):
        bars = flat_series(20)
        assert average_true_range(bars, 19, period=14) == Decimal(1)

    def test_an_unwarmed_index_has_no_atr(self):
        assert average_true_range(flat_series(20), 5, period=14) == Decimal(0)


class TestAdverseGaps:
    def test_a_favourable_gap_is_zero_not_negative(self):
        bars = flat_series(20)
        # Gap up on the final session: not a risk to a long, so it must not offset one.
        bars[19] = bar(19, open_="105", high="106", low="104", close="105")
        assert adverse_gaps(bars)[-1] == Decimal(0)

    def test_an_adverse_gap_is_measured_in_atr_units(self):
        bars = flat_series(20)
        # ATR is one, and the open is three below the previous close.
        bars[19] = bar(19, open_="97", high="97.5", low="96.5", close="97")
        assert adverse_gaps(bars)[-1] == Decimal(3)

    def test_nothing_is_measured_before_the_atr_is_warm(self):
        assert adverse_gaps(flat_series(15), period=14) == []


class TestTheMeasurementRespectsTheHoldout:
    def test_min_samples_is_not_a_token_floor(self):
        # A 99th percentile decided by two observations is one morning's accident.
        assert MIN_SAMPLES >= 250

    def test_every_measured_symbol_was_carved_short_of_the_evaluation_window(self):
        """
        The filed measurement must not span bars the gate may not see.

        Not a re-measurement - that is the generator's job - but a guard that the pinned
        table's symbols are exactly the ones the registry activates, so a symbol cannot be
        pinned from a window nobody carved.

        """
        activated = {a.symbol for a in load_activations()}
        assert set(GAP_ATR) == activated


class TestThePinnedTable:
    def test_an_unmeasured_symbol_is_refused_rather_than_guessed(self):
        with pytest.raises(UnmeasuredSymbolError, match="no stressed gap allowance"):
            allowance_atr("NVDA")

    def test_the_allowance_scales_with_the_atr(self):
        assert allowance_for("SPY", Decimal(2)) == Decimal("1.23") * Decimal(2)

    def test_an_unwarmed_atr_yields_no_allowance(self):
        assert allowance_for("SPY", Decimal(0)) == Decimal(0)

    def test_every_allowance_is_a_positive_multiple(self):
        for symbol, multiple in GAP_ATR.items():
            assert multiple > 0, symbol

    def test_the_registry_declares_exactly_the_pinned_allowance(self):
        """
        The drift guard, and the reason the declaration may live in the registry at all.

        An activation sizes with its own `gap_atr`, so a figure edited there to flatter a
        result would change every R with nothing to catch it. This is what catches it.

        """
        for activation in load_activations():
            declared = activation.parameters.get("gap_atr")
            assert declared is not None, f"{activation.name} declares no gap_atr"
            assert Decimal(str(declared)) == GAP_ATR[activation.symbol], activation.name
