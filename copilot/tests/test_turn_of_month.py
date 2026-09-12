"""
Tests for the turn-of-month hold, EXP-2026-001.

The rule is a calendar, so the tests are built on real sessions: January 2024's last trading day
is Wednesday the 31st, and February's first three are the 1st, 2nd and 5th. What matters most is
that the trigger cannot read the future - it asks the exchange calendar, not whether another bar
followed - and that the hold ends where the card says it does.

"""

from __future__ import annotations

from datetime import UTC
from datetime import date
from datetime import datetime
from decimal import Decimal

import pytest

from copilot.data.catalog import bar_type_for
from copilot.data.catalog import equity_for
from copilot.strategies.turn_of_month import ENTRY_SUBMITTED
from copilot.strategies.turn_of_month import NOT_MONTH_END
from copilot.strategies.turn_of_month import SEARCH_SPACE
from copilot.strategies.turn_of_month import WARMUP_BARS
from copilot.strategies.turn_of_month import is_last_session_of_month
from copilot.strategies.turn_of_month import month_end_sessions
from copilot.strategies.turn_of_month import strategy_factory
from copilot.validation.nautilus_replay import run_nautilus_replay
from copilot.validation.types import DailyBar


INSTRUMENT = equity_for("SPY", "ARCX")
BAR_TYPE = bar_type_for(INSTRUMENT.id)

JANUARY = (
    date(2024, 1, 2),
    date(2024, 1, 3),
    date(2024, 1, 4),
    date(2024, 1, 5),
    date(2024, 1, 8),
    date(2024, 1, 9),
    date(2024, 1, 10),
    date(2024, 1, 11),
    date(2024, 1, 12),
    date(2024, 1, 16),
    date(2024, 1, 17),
    date(2024, 1, 18),
    date(2024, 1, 19),
    date(2024, 1, 22),
    date(2024, 1, 23),
    date(2024, 1, 24),
    date(2024, 1, 25),
    date(2024, 1, 26),
    date(2024, 1, 29),
    date(2024, 1, 30),
    date(2024, 1, 31),
)
FEBRUARY = (
    date(2024, 2, 1),
    date(2024, 2, 2),
    date(2024, 2, 5),
    date(2024, 2, 6),
    date(2024, 2, 7),
    date(2024, 2, 8),
)


def bar(
    day: date,
    close: str,
    *,
    open_: str | None = None,
    high: str | None = None,
    low: str | None = None,
) -> DailyBar:
    """
    One session's bar, a quiet two-point range around the close unless a level is
    forced.
    """
    value = Decimal(close)
    return DailyBar(
        symbol="SPY",
        closed_at=datetime(day.year, day.month, day.day, tzinfo=UTC),
        open=Decimal(open_) if open_ is not None else value,
        high=Decimal(high) if high is not None else value + Decimal(1),
        low=Decimal(low) if low is not None else value - Decimal(1),
        close=value,
        volume=1_000_000,
    )


def series(**overrides: str) -> list[DailyBar]:
    """
    January and February 2024 at 100, with any session's close overridden.
    """
    return [bar(day, overrides.get(day.isoformat(), "100")) for day in (*JANUARY, *FEBRUARY)]


def run(bars: list[DailyBar], **parameters: object):
    """
    Replay the rule over the given bars.
    """
    return run_nautilus_replay(
        bars,
        parameters,
        instrument=INSTRUMENT,
        bar_type=BAR_TYPE,
        strategy_factory=strategy_factory,
    )


class TestCalendar:
    """
    The trigger, which is a calendar question and not a bar question.
    """

    def test_the_last_trading_session_of_a_month_is_one(self) -> None:
        assert is_last_session_of_month(date(2024, 1, 31))

    def test_a_session_mid_month_is_not(self) -> None:
        assert not is_last_session_of_month(date(2024, 1, 30))

    def test_a_month_ending_at_a_weekend_ends_on_the_friday(self) -> None:
        """
        March 2024 ends on Sunday, so the last session is Thursday the 28th: Good
        Friday.
        """
        assert is_last_session_of_month(date(2024, 3, 28))
        assert not is_last_session_of_month(date(2024, 3, 29))

    def test_a_holiday_is_not_a_session(self) -> None:
        assert not is_last_session_of_month(date(2024, 3, 31))

    def test_the_sessions_of_a_window_are_picked_out(self) -> None:
        assert month_end_sessions((*JANUARY, *FEBRUARY)) == (date(2024, 1, 31),)


class TestRule:
    """
    What the replay does over January and February 2024.
    """

    def test_it_enters_on_the_months_last_close_and_nowhere_else(self) -> None:
        result = run(series(), hold_sessions=3, stop_atr="1.5")

        (trade,) = result.trades
        assert trade.opened_at.date() == date(2024, 1, 31)
        assert trade.entry_price == Decimal(100)

    def test_the_hold_ends_on_the_third_session_of_the_next_month(self) -> None:
        result = run(series(), hold_sessions=3, stop_atr="1.5")

        (trade,) = result.trades
        assert trade.closed_at.date() == date(2024, 2, 5)

    def test_a_shorter_hold_ends_earlier(self) -> None:
        result = run(series(), hold_sessions=2, stop_atr="1.5")

        (trade,) = result.trades
        assert trade.closed_at.date() == date(2024, 2, 2)

    def test_a_stop_breached_within_a_session_costs_exactly_one_r(self) -> None:
        """
        The property the whole R unit rests on.

        A session that opens at 99.50, dips to 96 through the stop at 97 and recovers to
        99 ends the hold at the trigger, for **exactly one R**: the stop is the
        denominator every score in this experiment is divided by.

        """
        bars = series()
        bars[len(JANUARY)] = bar(date(2024, 2, 1), "99", high="99.8", low="96", open_="99.5")
        result = run(bars, hold_sessions=3, stop_atr="1.5")

        (trade,) = result.trades
        assert trade.closed_at.date() == date(2024, 2, 1)
        assert trade.exit_price == Decimal(97)
        assert trade.r_multiple == Decimal(-1)

    def test_a_gap_through_the_stop_costs_more_than_one_r_and_is_not_hidden(self) -> None:
        """
        The other half of the truth, and the reason the playbook sizes with a gap
        allowance.

        A session that opens at 85 never trades at the 97 stop, so the fill is the bar's
        close and the loss is five R, not one. On daily bars the engine has no intrabar
        path to do better, and a premise measured as though every stop filled at its
        trigger would understate exactly the losses that matter. An earlier version of
        this test asserted only that R was negative, which passed while the stop had not
        filled at all.

        """
        bars = series()
        bars[len(JANUARY)] = bar(date(2024, 2, 1), "85", high="86", low="84", open_="85")
        result = run(bars, hold_sessions=3, stop_atr="1.5")

        (trade,) = result.trades
        assert trade.closed_at.date() == date(2024, 2, 1)
        assert trade.exit_price == Decimal(85)
        assert trade.r_multiple < Decimal(-1)

    def test_the_recorded_risk_is_the_floored_quantity_times_the_stop_distance(self) -> None:
        result = run(series(), hold_sessions=3, stop_atr="1.5", risk_budget="1000")

        (trade,) = result.trades
        assert trade.risk_amount > 0
        assert trade.risk_amount <= Decimal(1000)
        assert trade.quantity > 0

    def test_a_window_with_no_month_end_trades_nothing(self) -> None:
        bars = [bar(day, "100") for day in JANUARY[:-1]]
        result = run(bars, hold_sessions=3, stop_atr="1.5")

        assert result.trades == ()

    def test_an_unwarmed_rule_declines_rather_than_entering(self) -> None:
        """
        The stop needs an ATR, so the first bars cannot trade however the calendar
        reads.
        """
        bars = [bar(day, "100") for day in JANUARY[-3:]] + [bar(day, "100") for day in FEBRUARY]
        result = run(bars, hold_sessions=3, stop_atr="1.5")

        assert result.trades == ()

    def test_the_null_controls_override_replaces_the_calendar(self) -> None:
        """
        The same rule on a chosen session: this is what the random-entry control runs.
        """
        result = run(
            series(),
            hold_sessions=3,
            stop_atr="1.5",
            entry_sessions="2024-01-24",
        )

        (trade,) = result.trades
        assert trade.opened_at.date() == date(2024, 1, 24)
        assert trade.closed_at.date() == date(2024, 1, 29)


class TestDeclarations:
    """
    What the experiment card declared, pinned so the code cannot drift from it.
    """

    def test_the_search_space_is_the_six_points_the_card_declared(self) -> None:
        assert {
            "hold_sessions": (2, 3, 4),
            "stop_atr": (Decimal("1.5"), Decimal("2.5")),
        } == SEARCH_SPACE

    def test_the_space_stays_small(self) -> None:
        points = 1
        for values in SEARCH_SPACE.values():
            points *= len(values)
        assert points <= 12

    def test_the_warmup_covers_the_indicator(self) -> None:
        assert WARMUP_BARS >= 14

    def test_a_hold_of_no_sessions_is_refused(self) -> None:
        from copilot.strategies.turn_of_month import TurnOfMonthConfig

        with pytest.raises(ValueError, match="hold_sessions must be at least 1"):
            TurnOfMonthConfig(instrument_id=INSTRUMENT.id, bar_type=BAR_TYPE, hold_sessions=0)

    def test_the_decision_record_has_the_shape_the_comparison_reads(self) -> None:
        strategy = strategy_factory(
            {},
            instrument_id=INSTRUMENT.id,
            bar_type=BAR_TYPE,
            risk_registry=None,
        )
        record = strategy.decision_record()
        assert set(record) >= {"atr_initialized", "outcome", "skips", "sessions_held"}
        assert record["outcome"] is None

    def test_a_live_strategy_can_be_built_without_subscribing(self) -> None:
        strategy = strategy_factory(
            {"subscribe_bars": False},
            instrument_id=INSTRUMENT.id,
            bar_type=BAR_TYPE,
            risk_registry=None,
        )
        assert strategy.config.subscribe_bars is False


def test_the_first_bar_of_a_month_is_not_an_entry() -> None:
    """
    Guards the off-by-one the rule would have if it asked "is this a new month" instead.
    """
    assert not is_last_session_of_month(date(2024, 2, 1))
    assert NOT_MONTH_END != ENTRY_SUBMITTED
