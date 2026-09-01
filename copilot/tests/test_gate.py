"""Tests for the ported validation gate.

The replay is injected throughout, so these exercise the *selection and verdict rules*
against a controlled score surface rather than against whatever a real engine happens
to produce. That is the point of the injection seam.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import pairwise

import pytest
from copilot.validation.insample import (
    ParameterGrid,
    expectancy_r,
    search_in_sample,
)
from copilot.validation.tearsheet import (
    deflated_pass_probability,
    tearsheet_for,
)
from copilot.validation.types import BacktestRunResult, ClosedTrade, DailyBar, Direction
from copilot.validation.walkforward import build_folds, walk_forward

START = datetime(2026, 1, 1, tzinfo=UTC)


def bar(i: int) -> DailyBar:
    price = Decimal(100)
    return DailyBar(
        symbol="TEST",
        closed_at=START + timedelta(days=i),
        open=price,
        high=price,
        low=price,
        close=price,
        volume=1_000,
    )


def bars(n: int) -> list[DailyBar]:
    return [bar(i) for i in range(n)]


def trade(day: int, r: str, *, risk: str = "100") -> ClosedTrade:
    """A closed trade whose r_multiple is exactly ``r``."""
    risk_amount = Decimal(risk)
    when = START + timedelta(days=day)
    return ClosedTrade(
        symbol="TEST",
        direction=Direction.LONG,
        quantity=1,
        entry_price=Decimal(100),
        exit_price=Decimal(100),
        exit_reason="TEST",
        signal_created_at=when,
        opened_at=when,
        closed_at=when + timedelta(hours=1),
        realized_pnl=Decimal(r) * risk_amount,
        risk_amount=risk_amount,
    )


def result(*r_values: str, start_day: int = 0) -> BacktestRunResult:
    return BacktestRunResult(
        trades=tuple(trade(start_day + i, r) for i, r in enumerate(r_values)),
    )


class TestFoldConstruction:
    def test_tiles_without_overlap_by_default(self):
        folds = build_folds(100, train_bars=50, test_bars=10, purge_bars=5)
        assert len(folds) == 4
        # Test windows must not overlap, or the majority gate divides by an inflated
        # fold count and the same bars are scored twice.
        spans = [(f.purge_end, f.test_end) for f in folds]
        for (_, end), (nxt, _) in pairwise(spans):
            assert nxt >= end

    def test_purge_gap_sits_between_train_and_test(self):
        folds = build_folds(100, train_bars=50, test_bars=10, purge_bars=5)
        for f in folds:
            assert f.purge_end - f.train_end == 5
            assert f.train.stop <= f.test.start

    def test_too_short_a_series_returns_no_folds_rather_than_raising(self):
        assert build_folds(10, train_bars=50, test_bars=10, purge_bars=5) == ()

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"train_bars": 0, "test_bars": 10, "purge_bars": 5},
            {"train_bars": 50, "test_bars": 0, "purge_bars": 5},
            {"train_bars": 50, "test_bars": 10, "purge_bars": -1},
            {"train_bars": 50, "test_bars": 10, "purge_bars": 5, "step_bars": 0},
        ],
    )
    def test_invalid_windows_are_refused(self, kwargs):
        with pytest.raises(ValueError):
            build_folds(100, **kwargs)


class TestInSampleSelection:
    def test_picks_the_plateau_not_the_peak(self):
        # Score surface over one axis: a lone spike at 3, a flat plateau at 6-8.
        # The peak is the spike; the plateau is what should survive out of sample.
        surface = {
            1: "0.10",
            2: "0.10",
            3: "0.90",
            4: "0.10",
            5: "0.30",
            6: "0.50",
            7: "0.50",
            8: "0.50",
        }

        def replay(_bars, params):
            return result(*([surface[params["x"]]] * 40))

        grid = ParameterGrid.of(x=list(surface))
        report = search_in_sample(bars(10), grid, replay=replay, min_trades=1)

        assert report.peak is not None
        assert report.peak.parameters["x"] == 3, "the raw peak is the spike"
        assert report.selected is not None
        assert report.selected.parameters["x"] in (6, 7), "selection must be on the plateau"
        assert not report.selected_the_peak

    def test_cliff_veto_rejects_a_spike(self):
        surface = {1: "0.0", 2: "0.9", 3: "0.0"}

        def replay(_bars, params):
            return result(*([surface[params["x"]]] * 40))

        grid = ParameterGrid.of(x=list(surface))
        report = search_in_sample(bars(10), grid, replay=replay, min_trades=1)

        spike = next(c for c in report.candidates if c.parameters["x"] == 2)
        assert "cliff" in report.rejections[spike.version]

    def test_insufficient_trades_is_rejected_before_it_can_win(self):
        def replay(_bars, params):
            # The best score comes from the smallest sample.
            return result(*(["0.9"] if params["x"] == 1 else ["0.1"] * 40))

        grid = ParameterGrid.of(x=[1, 2, 3])
        report = search_in_sample(bars(10), grid, replay=replay, min_trades=30)

        thin = next(c for c in report.candidates if c.parameters["x"] == 1)
        assert "insufficient_trades" in report.rejections[thin.version]
        assert report.selected is not None
        assert report.selected.parameters["x"] != 1

    def test_isolated_points_cannot_be_selected(self):
        # Two valid candidates that are not adjacent: neither can demonstrate a
        # plateau, so neither may be selected however good it scores. Selecting one
        # would be selecting a peak, which is what this search exists to avoid.
        def factory(**values: object):
            if (values["x"], values["y"]) not in {(0, 0), (2, 2)}:
                raise ValueError("invalid corner")
            return values

        def replay(_bars, _params):
            return result(*(["0.9"] * 40))

        grid = ParameterGrid(axes_by_name={"x": [0, 1, 2], "y": [0, 1, 2]}, factory=factory)
        report = search_in_sample(bars(10), grid, replay=replay, min_trades=1)

        assert len(report.candidates) == 2, "both corners survive the factory"
        assert report.selected is None
        assert all("no_neighbours" in r for r in report.rejections.values())

    def test_a_lone_candidate_is_selectable(self):
        # With one candidate there is no surface to overfit to, so the isolation rule
        # does not apply — the guard is `len(expanded) > 1` for exactly this reason.
        def factory(**values: object):
            if values["x"] != 2:
                raise ValueError("invalid corner")
            return values

        def replay(_bars, _params):
            return result(*(["0.9"] * 40))

        grid = ParameterGrid(axes_by_name={"x": [1, 2, 3]}, factory=factory)
        report = search_in_sample(bars(10), grid, replay=replay, min_trades=1)
        assert report.selected is not None
        assert report.selected.parameters["x"] == 2

    def test_selection_is_deterministic(self):
        surface = {1: "0.5", 2: "0.5", 3: "0.5", 4: "0.5"}

        def replay(_bars, params):
            return result(*([surface[params["x"]]] * 40))

        grid = ParameterGrid.of(x=list(surface))
        picks = {
            search_in_sample(bars(10), grid, replay=replay, min_trades=1).selected.version
            for _ in range(5)
        }
        assert len(picks) == 1

    def test_base_values_are_kept_and_searched_axes_win(self):
        seen = []

        def replay(_bars, params):
            seen.append(dict(params))
            return result(*(["0.1"] * 40))

        grid = ParameterGrid(axes_by_name={"x": [1, 2]}, base={"side": "SHORT", "x": 99})
        search_in_sample(bars(10), grid, replay=replay, min_trades=1)

        assert all(p["side"] == "SHORT" for p in seen), "base must survive"
        assert {p["x"] for p in seen} == {1, 2}, "searched axis must win over base"


class TestWalkForward:
    def _grid(self):
        return ParameterGrid.of(x=[1, 2, 3])

    def test_majority_gate_needs_a_strict_majority(self):
        # Alternating pass/fail across an even number of evaluated folds must fail:
        # a coin flip is not evidence.
        calls = {"n": 0}

        def replay(_bars, _params):
            calls["n"] += 1
            return result(*(["0.5"] * 40))

        report = walk_forward(
            bars(200),
            self._grid(),
            train_bars=50,
            test_bars=25,
            purge_bars=5,
            warmup_bars=5,
            replay=replay,
            min_trades=1,
        )
        assert report.evaluated
        assert report.majority_passed is (report.passed_count * 2 > len(report.evaluated))

    def test_a_losing_rule_fails(self):
        def replay(_bars, _params):
            return result(*(["-0.5"] * 40))

        report = walk_forward(
            bars(200),
            self._grid(),
            train_bars=50,
            test_bars=25,
            purge_bars=5,
            warmup_bars=5,
            replay=replay,
            min_trades=1,
        )
        assert not report.majority_passed
        assert report.passed_count == 0

    def test_folds_selecting_nothing_are_neither_pass_nor_fail(self):
        # min_trades above what any candidate produces, so the search selects nothing.
        def replay(_bars, _params):
            return result("0.5")

        report = walk_forward(
            bars(200),
            self._grid(),
            train_bars=50,
            test_bars=25,
            purge_bars=5,
            warmup_bars=5,
            replay=replay,
            min_trades=30,
        )
        assert report.folds, "folds were built"
        assert report.evaluated == (), "none should count toward the gate"
        assert not report.majority_passed

    def test_only_trades_signalled_inside_the_test_window_are_scored(self):
        # Warm-up bars precede the test window; trades signalled there must not score.
        def replay(window, _params):
            first = window[0].closed_at
            # One trade in the warm-up region, forty inside the test window.
            warm = trade(-999, "9.0")
            warm_dated = ClosedTrade(**{**warm.__dict__, "signal_created_at": first})
            inside = tuple(
                ClosedTrade(
                    **{
                        **trade(0, "0.5").__dict__,
                        "signal_created_at": window[-1].closed_at,
                        "opened_at": window[-1].closed_at,
                        "closed_at": window[-1].closed_at,
                    },
                )
                for _ in range(40)
            )
            return BacktestRunResult(trades=(warm_dated, *inside))

        report = walk_forward(
            bars(200),
            self._grid(),
            train_bars=50,
            test_bars=25,
            purge_bars=5,
            warmup_bars=10,
            replay=replay,
            min_trades=1,
        )
        for fold in report.evaluated:
            for scored in fold.test_trade_details:
                assert fold.test_from <= scored.signal_created_at <= fold.test_to

    def test_negative_warmup_is_refused(self):
        with pytest.raises(ValueError):
            walk_forward(
                bars(200),
                self._grid(),
                train_bars=50,
                test_bars=25,
                purge_bars=5,
                warmup_bars=-1,
                replay=lambda *_: result("0.1"),
            )


class TestTearsheet:
    def test_reports_the_shape_expectancy_hides(self):
        # Same expectancy, opposite shapes: many small wins vs few large ones.
        grind = tearsheet_for(tuple(trade(i, "0.1") for i in range(10)))
        assert grind.win_rate == Decimal(1)
        assert grind.profit_factor is None, "no losses means undefined, not infinite"

        mixed = tearsheet_for(
            (*[trade(i, "1.0") for i in range(3)], *[trade(3 + i, "-0.3") for i in range(7)]),
        )
        assert mixed.wins == 3
        assert mixed.losses == 7
        assert mixed.profit_factor is not None
        assert mixed.average_loss_r > 0, "reported positive so the ratio reads correctly"

    def test_max_drawdown_is_peak_to_trough(self):
        # +2 then -3 leaves net -1 but a 3R fall from the high.
        sheet = tearsheet_for((trade(0, "2.0"), trade(1, "-3.0")))
        assert sheet.max_drawdown_r == Decimal(3)
        assert sheet.total_r == Decimal(-1)

    def test_sharpe_undefined_below_two_trades(self):
        assert tearsheet_for((trade(0, "0.5"),)).sharpe is None

    def test_empty_is_quiet(self):
        sheet = tearsheet_for(())
        assert sheet.trades == 0
        assert sheet.profit_factor is None


class TestDeflation:
    def test_more_attempts_makes_a_result_less_impressive(self):
        few = deflated_pass_probability(attempts=1, folds=13, folds_passed=10)
        many = deflated_pass_probability(attempts=1000, folds=13, folds_passed=10)
        assert many > few

    def test_a_bare_majority_is_not_surprising(self):
        # 7 of 13 against a coin flip null is close to even, so a wide search almost
        # certainly produces it by chance.
        assert deflated_pass_probability(attempts=100, folds=13, folds_passed=7) > Decimal("0.9")

    def test_no_evidence_cannot_be_improbable(self):
        assert deflated_pass_probability(attempts=100, folds=0, folds_passed=0) == Decimal(1)
        assert deflated_pass_probability(attempts=0, folds=13, folds_passed=13) == Decimal(1)

    def test_passing_everything_after_one_attempt_is_the_rarest_case(self):
        assert deflated_pass_probability(attempts=1, folds=13, folds_passed=13) < Decimal("0.001")


class TestObjective:
    def test_expectancy_is_mean_r(self):
        assert expectancy_r(result("1.0", "-1.0", "0.5")) == Decimal("0.5") / Decimal(3)

    def test_no_trades_scores_zero(self):
        assert expectancy_r(BacktestRunResult()) == Decimal(0)
