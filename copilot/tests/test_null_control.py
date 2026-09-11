"""
Tests for the randomised-signal control.

The control exists because attribution cannot credit timing, so the failure that matters most
is a reading that looks like evidence when it is not: too few replicates, replicates that
scored nothing, or a draw that quietly repeats a session. Each of those is pinned here.

The replicate function is a fake throughout. What the control owns is the draw, the skip
accounting, the arithmetic and the refusals; running a replay is the caller's.

"""

from __future__ import annotations

import random
from datetime import date
from datetime import timedelta
from decimal import Decimal

import pytest

from copilot.validation.null_control import BEATS_CHANCE
from copilot.validation.null_control import INDISTINGUISHABLE
from copilot.validation.null_control import MIN_REPLICATES
from copilot.validation.null_control import WITHHELD
from copilot.validation.null_control import NullControl
from copilot.validation.null_control import random_entry_days
from copilot.validation.null_control import run_null_control


SESSIONS = tuple(date(2020, 1, 1) + timedelta(days=i) for i in range(300))


def rng(seed: int) -> random.Random:
    """
    A seeded generator, the way the control itself builds one.
    """
    return random.Random(seed)  # noqa: S311 - deterministic fixture, not a secret


def control(**kwargs: object) -> NullControl:
    """
    Build a control whose null distribution is a hundred means from 0.00 to 0.99 R.
    """
    base = {
        "replicates": 100,
        "skipped": 0,
        "premise_mean_r": Decimal("0.50"),
        "premise_trades": 200,
        "null_means": tuple(Decimal(i) / 100 for i in range(100)),
        "seed": 1,
    }
    return NullControl(**{**base, **kwargs})  # type: ignore[arg-type]


class TestDraw:
    def test_a_draw_is_in_date_order_and_inside_the_window(self) -> None:
        days = random_entry_days(SESSIONS, count=20, rng=rng(7))
        assert len(days) == 20
        assert list(days) == sorted(days)
        assert set(days) <= set(SESSIONS)

    def test_no_session_is_drawn_twice(self) -> None:
        """
        The premise cannot enter the same session twice, so nor can the control.
        """
        days = random_entry_days(SESSIONS, count=200, rng=rng(7))
        assert len(set(days)) == 200

    def test_the_same_seed_draws_the_same_dates(self) -> None:
        first = random_entry_days(SESSIONS, count=20, rng=rng(3))
        assert first == random_entry_days(SESSIONS, count=20, rng=rng(3))

    def test_a_different_seed_draws_differently(self) -> None:
        first = random_entry_days(SESSIONS, count=20, rng=rng(3))
        assert first != random_entry_days(SESSIONS, count=20, rng=rng(4))

    def test_more_entries_than_sessions_is_refused(self) -> None:
        with pytest.raises(ValueError, match="cannot draw"):
            random_entry_days(SESSIONS[:10], count=11, rng=rng(1))

    @pytest.mark.parametrize("count", [0, -1])
    def test_a_non_positive_count_is_refused(self, count: int) -> None:
        with pytest.raises(ValueError, match="count must be positive"):
            random_entry_days(SESSIONS, count=count, rng=rng(1))


class TestReading:
    def test_a_premise_above_every_replicate_beats_chance(self) -> None:
        reading = control(premise_mean_r=Decimal("2.00"))
        assert reading.beaten == 0
        assert reading.percentile == Decimal("100.00")
        assert reading.p_value == Decimal("0.0099")
        assert reading.reading == BEATS_CHANCE

    def test_a_premise_in_the_middle_is_indistinguishable(self) -> None:
        reading = control()
        assert reading.percentile == Decimal("50.00")
        assert reading.p_value > Decimal("0.10")
        assert reading.reading == INDISTINGUISHABLE

    def test_the_p_value_counts_the_premise_itself(self) -> None:
        """
        It cannot report zero: 500 replicates can say rarer than one in 501, no more.
        """
        reading = control(replicates=100, null_means=tuple(Decimal(0) for _ in range(100)))
        assert reading.p_value == Decimal("0.0099")

    def test_the_null_mean_is_what_chance_earned(self) -> None:
        assert control().null_mean == Decimal("0.495000")

    def test_too_many_skipped_replicates_withhold_the_reading(self) -> None:
        """
        A distribution built from the replicates that happened to score is a selected
        sample.
        """
        reading = control(replicates=100, skipped=6, premise_mean_r=Decimal("2.00"))
        assert reading.withheld
        assert reading.reading == WITHHELD

    def test_a_few_skipped_replicates_do_not(self) -> None:
        reading = control(replicates=100, skipped=5, premise_mean_r=Decimal("2.00"))
        assert not reading.withheld
        assert reading.reading == BEATS_CHANCE

    def test_the_record_carries_the_seed_and_the_reading(self) -> None:
        record = control().as_record()
        assert record["seed"] == 1
        assert record["reading"] == INDISTINGUISHABLE
        assert record["premise_mean_r"] == "0.50"
        assert record["scored"] == 100
        assert "null_means" not in record


class TestRun:
    def test_every_replicate_gets_its_own_draw_and_is_scored(self) -> None:
        seen: list[tuple[date, ...]] = []

        def replicate(days: tuple[date, ...]) -> Decimal:
            seen.append(days)
            return Decimal(len(seen)) / 1000

        reading = run_null_control(
            replicate,
            SESSIONS,
            entries=12,
            premise_mean_r=Decimal("0.05"),
            premise_trades=12,
            replicates=MIN_REPLICATES,
            seed=11,
        )

        assert len(seen) == MIN_REPLICATES
        assert len(set(seen)) == MIN_REPLICATES
        assert reading.scored == MIN_REPLICATES
        assert reading.skipped == 0

    def test_a_replicate_that_scored_nothing_is_counted_not_dropped(self) -> None:
        def replicate(days: tuple[date, ...]) -> Decimal | None:
            return None if days[0].day % 2 else Decimal("0.01")

        reading = run_null_control(
            replicate,
            SESSIONS,
            entries=5,
            premise_mean_r=Decimal("0.05"),
            premise_trades=5,
            replicates=MIN_REPLICATES,
            seed=5,
        )

        assert reading.skipped > 0
        assert reading.scored + reading.skipped == MIN_REPLICATES

    def test_the_same_seed_runs_the_same_control(self) -> None:
        def replicate(days: tuple[date, ...]) -> Decimal:
            return Decimal(sum(day.toordinal() for day in days) % 97) / 100

        first, second = (
            run_null_control(
                replicate,
                SESSIONS,
                entries=8,
                premise_mean_r=Decimal("0.40"),
                premise_trades=8,
                replicates=MIN_REPLICATES,
                seed=42,
            )
            for _ in range(2)
        )
        assert first.as_record() == second.as_record()

    def test_too_few_replicates_are_refused(self) -> None:
        with pytest.raises(ValueError, match="cannot support a percentile"):
            run_null_control(
                lambda _days: Decimal("0.01"),
                SESSIONS,
                entries=5,
                premise_mean_r=Decimal("0.05"),
                premise_trades=5,
                replicates=MIN_REPLICATES - 1,
            )
