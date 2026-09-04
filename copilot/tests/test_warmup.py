"""
Tests for the live warm-up read.

The failure this module is built against is not an exception, it is a plausible trade.
A strategy warmed from a catalog that stops three weeks back still runs, still fires, and
measures a gap that is mostly the intervening drift; one warmed across a missing session
does the same thing on a smaller scale and looks entirely normal in a log. So the tests
that matter are the refusals - stale, holed, and short - and the one that pins the warmed
indicator to the value the engine would have produced from the same bars.

"""

from __future__ import annotations

from datetime import UTC
from datetime import date
from datetime import datetime
from decimal import Decimal

import pytest

from copilot.data.calendar import trading_days
from copilot.data.catalog import bar_type_for
from copilot.data.catalog import equity_for
from copilot.data.catalog import open_catalog
from copilot.data.catalog import to_nautilus_bars
from copilot.data.catalog import write_ingestion
from copilot.data.marketstack import IngestionResult
from copilot.live.warmup import WarmupUnavailableError
from copilot.live.warmup import expected_sessions
from copilot.live.warmup import inspect
from copilot.live.warmup import load
from copilot.live.warmup import report
from copilot.strategies.activations import find_activation
from copilot.strategies.gap_reversal import WARMUP_BARS
from copilot.strategies.gap_reversal import GapReversalConfig
from copilot.strategies.gap_reversal import GapReversalStrategy
from copilot.validation.types import DailyBar
from nautilus_trader.indicators import AverageTrueRange


ACTIVATION = find_activation("aapl-gap-fade-long")
INSTRUMENT = equity_for(ACTIVATION.symbol, ACTIVATION.venue)
BAR_TYPE = bar_type_for(INSTRUMENT.id)
FIRST_SESSION = date(2025, 6, 16)


def sessions_before(day: date, count: int) -> tuple[date, ...]:
    """
    Return the ``count`` real trading sessions before ``day``.
    """
    return expected_sessions(day, count)


def daily(day: date, close: str) -> DailyBar:
    """
    Build one bar whose range is wide enough for an ATR to be non-zero.
    """
    value = Decimal(close)
    return DailyBar(
        symbol=ACTIVATION.symbol,
        closed_at=datetime(day.year, day.month, day.day, tzinfo=UTC),
        open=value,
        high=value + Decimal("2.0"),
        low=value - Decimal("1.5"),
        close=value,
        volume=1_000_000,
    )


def catalog_of(tmp_path, days) -> str:
    """
    Write a catalog holding one bar per given session, and return its path.
    """
    bars = tuple(daily(day, str(100 + i)) for i, day in enumerate(days))
    store = tmp_path / "store"
    write_ingestion(
        open_catalog(store),
        IngestionResult(bars=bars, fetched=len(bars)),
        venues={ACTIVATION.symbol: ACTIVATION.venue},
    )
    return str(store)


def test_expected_sessions_excludes_the_session_being_traded() -> None:
    # The first live bar has not closed; warming on it would feed the strategy the close
    # it is about to decide against.
    assert FIRST_SESSION not in expected_sessions(FIRST_SESSION, WARMUP_BARS)


def test_expected_sessions_returns_exactly_the_count_asked_for() -> None:
    assert len(expected_sessions(FIRST_SESSION, WARMUP_BARS)) == WARMUP_BARS


def test_expected_sessions_skips_weekends_and_holidays() -> None:
    # Independence Day 2025 fell on a Friday; the sessions around it must not include it.
    days = expected_sessions(date(2025, 7, 8), 5)
    assert date(2025, 7, 4) not in days
    assert all(d in trading_days(days[0], days[-1]) for d in days)


def test_expected_sessions_rejects_a_non_positive_count() -> None:
    with pytest.raises(ValueError, match="count must be positive"):
        expected_sessions(FIRST_SESSION, 0)


def test_a_current_catalog_warms(tmp_path) -> None:
    days = sessions_before(FIRST_SESSION, WARMUP_BARS)
    bars = load(catalog_of(tmp_path, days), ACTIVATION, first_session=FIRST_SESSION)
    assert len(bars) == WARMUP_BARS


def test_the_warm_up_ends_on_the_session_before_the_first(tmp_path) -> None:
    days = sessions_before(FIRST_SESSION, WARMUP_BARS)
    window = inspect(catalog_of(tmp_path, days), ACTIVATION, first_session=FIRST_SESSION)
    assert window.ready
    assert window.present[-1] == days[-1]


def test_extra_history_is_trimmed_to_the_window(tmp_path) -> None:
    # A catalog holding years is the normal case; the warm-up takes the tail alone.
    days = sessions_before(FIRST_SESSION, WARMUP_BARS + 200)
    bars = load(catalog_of(tmp_path, days), ACTIVATION, first_session=FIRST_SESSION)
    assert len(bars) == WARMUP_BARS


def test_bars_after_the_first_session_are_never_warmed_from(tmp_path) -> None:
    # A catalog can legitimately run past the session being traded - it is kept current,
    # and a backfill covers whole days. Warming from a later bar would be lookahead.
    days = (*sessions_before(FIRST_SESSION, WARMUP_BARS), FIRST_SESSION)
    window = inspect(catalog_of(tmp_path, days), ACTIVATION, first_session=FIRST_SESSION)
    assert FIRST_SESSION not in window.present


def test_a_stale_catalog_refuses(tmp_path) -> None:
    # The catalog stops well before the session: every expected day is missing.
    days = sessions_before(date(2025, 1, 15), WARMUP_BARS)
    with pytest.raises(WarmupUnavailableError, match="cannot warm"):
        load(catalog_of(tmp_path, days), ACTIVATION, first_session=FIRST_SESSION)


def test_a_stale_catalog_names_the_newest_session_it_holds(tmp_path) -> None:
    days = sessions_before(date(2025, 1, 15), WARMUP_BARS)
    with pytest.raises(WarmupUnavailableError, match=days[-1].isoformat()):
        load(catalog_of(tmp_path, days), ACTIVATION, first_session=FIRST_SESSION)


def test_a_holed_catalog_refuses_and_names_the_hole(tmp_path) -> None:
    # The failure that is nearly invisible: current, one session short in the middle,
    # exactly what a vendor's null close leaves behind. Without this check the tail
    # steps over the hole and the previous close is two sessions old.
    days = sessions_before(FIRST_SESSION, WARMUP_BARS + 1)
    holed = days[:5] + days[6:]
    with pytest.raises(WarmupUnavailableError, match=days[5].isoformat()):
        load(catalog_of(tmp_path, holed), ACTIVATION, first_session=FIRST_SESSION)


def test_a_hole_is_reported_as_missing_not_as_a_short_read(tmp_path) -> None:
    days = sessions_before(FIRST_SESSION, WARMUP_BARS + 1)
    holed = days[:5] + days[6:]
    window = inspect(catalog_of(tmp_path, holed), ACTIVATION, first_session=FIRST_SESSION)
    assert window.missing == (days[5],)


def test_an_empty_catalog_refuses_without_crashing(tmp_path) -> None:
    with pytest.raises(WarmupUnavailableError, match="nothing held"):
        load(catalog_of(tmp_path, ()), ACTIVATION, first_session=FIRST_SESSION)


def test_a_stale_catalog_is_distinguishable_from_an_empty_one(tmp_path) -> None:
    # Both read no bars inside the window, and telling an operator "nothing held" when
    # the catalog stops in January sends them looking for the wrong problem.
    days = sessions_before(date(2025, 1, 15), WARMUP_BARS)
    window = inspect(catalog_of(tmp_path, days), ACTIVATION, first_session=FIRST_SESSION)
    assert window.present == ()
    assert window.newest_held == days[-1]


def test_a_ready_window_does_not_diagnose_the_catalog(tmp_path) -> None:
    # The unbounded read is the price of a good error message, not of every check.
    days = sessions_before(FIRST_SESSION, WARMUP_BARS)
    window = inspect(catalog_of(tmp_path, days), ACTIVATION, first_session=FIRST_SESSION)
    assert window.ready
    assert window.newest_held is None


def test_the_refusal_names_the_command_that_fixes_it(tmp_path) -> None:
    days = sessions_before(date(2025, 1, 15), WARMUP_BARS)
    with pytest.raises(WarmupUnavailableError, match=r"copilot\.data\.backfill"):
        load(catalog_of(tmp_path, days), ACTIVATION, first_session=FIRST_SESSION)


def test_report_returns_non_zero_when_an_activation_cannot_warm(tmp_path, capsys) -> None:
    days = sessions_before(date(2025, 1, 15), WARMUP_BARS)
    code = report(catalog_of(tmp_path, days), (ACTIVATION,), first_session=FIRST_SESSION)
    assert code == 1
    assert "BLOCKED" in capsys.readouterr().out


def test_report_returns_zero_when_every_activation_warms(tmp_path, capsys) -> None:
    days = sessions_before(FIRST_SESSION, WARMUP_BARS)
    code = report(catalog_of(tmp_path, days), (ACTIVATION,), first_session=FIRST_SESSION)
    assert code == 0
    assert "ready" in capsys.readouterr().out


def strategy() -> GapReversalStrategy:
    """
    Build one strategy at the registered warm-up length.
    """
    return GapReversalStrategy(
        GapReversalConfig(
            instrument_id=INSTRUMENT.id,
            bar_type=BAR_TYPE,
            atr_period=ACTIVATION.setup.warmup_bars - 2,
        ),
    )


def nautilus_bars(count: int) -> list:
    """
    Build ``count`` Nautilus bars ending the session before the first live one.
    """
    days = sessions_before(FIRST_SESSION, count)
    source = [daily(day, str(100 + i)) for i, day in enumerate(days)]
    return to_nautilus_bars(source, INSTRUMENT, BAR_TYPE)


def test_warm_up_initialises_the_indicator() -> None:
    fade = strategy()
    assert not fade._atr.initialized
    fade.warm_up(nautilus_bars(WARMUP_BARS))
    assert fade._atr.initialized


def test_warm_up_leaves_the_previous_close_of_the_last_session() -> None:
    # What on_bar would have left there, and what the gap is measured against.
    bars = nautilus_bars(WARMUP_BARS)
    fade = strategy()
    fade.warm_up(bars)
    assert fade._previous_close == Decimal(str(bars[-1].close))


def test_warm_up_reproduces_the_indicator_the_engine_would_have_built() -> None:
    # Pins that the warm-up feeds handle_bar rather than approximating the update: a
    # strategy warmed here must hold the value it would hold having been run.
    bars = nautilus_bars(WARMUP_BARS)
    fade = strategy()
    fade.warm_up(bars)

    reference = AverageTrueRange(ACTIVATION.setup.warmup_bars - 2)
    for bar in bars:
        reference.handle_bar(bar)

    assert fade._atr.value == reference.value


def test_a_short_warm_up_leaves_the_indicator_uninitialised() -> None:
    # The strategy does not second-guess the loader, so this is what a caller that
    # skipped the refusal would get: a rule that declines every bar for insufficient
    # history, which is exactly the symptom the warm-up exists to remove.
    fade = strategy()
    fade.warm_up(nautilus_bars(3))
    assert not fade._atr.initialized
