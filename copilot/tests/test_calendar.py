"""
Tests for the US equity trading calendar.

The load-bearing test is `test_calendar_agrees_with_twenty_years_of_vendor_data`. The
rules in `calendar.py` are only worth having if they reproduce the real session dates,
and a hand-written calendar that wrongly closes a real session would silently drop
live market data. An earlier version of the file did exactly that - it closed
31 December when 1 January fell on a Saturday, following the federal rule rather than
the exchange one - and this test is what caught it.

"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from copilot.data.calendar import easter_sunday
from copilot.data.calendar import is_trading_day
from copilot.data.calendar import market_holidays
from copilot.data.calendar import trading_days


FIXTURE = Path(__file__).parent / "fixtures" / "marketstack_session_dates.json"

# The two rows Marketstack reports on days the US market was shut. Both are SPY, both
# carry a plausible OHLC and nine-figure volume, and nothing in the payload marks them
# as phantom. They are the reason the calendar gate exists.
KNOWN_PHANTOM_SESSIONS = {
    ("SPY", date(2023, 11, 23)),  # Thanksgiving
    ("SPY", date(2024, 3, 29)),  # Good Friday
}


@pytest.fixture(scope="module")
def vendor_session_dates() -> dict[str, list[date]]:
    """
    Dates Marketstack returned per symbol for 2005-2025, captured from the live API.
    """
    raw = json.loads(FIXTURE.read_text())
    return {symbol: [date.fromisoformat(d) for d in dates] for symbol, dates in raw.items()}


def test_calendar_agrees_with_twenty_years_of_vendor_data(vendor_session_dates):
    """
    Every vendor row falls on a session, and every session has a vendor row.

    Checked in both directions on purpose. Only flagging extras would pass a calendar
    that called every weekday a trading day; only checking for gaps would pass one that
    called every day a holiday.

    """
    phantom: set[tuple[str, date]] = set()
    missing: dict[str, list[date]] = {}

    for symbol, dates in vendor_session_dates.items():
        reported = set(dates)
        phantom |= {(symbol, d) for d in reported if not is_trading_day(d)}
        expected = set(trading_days(min(reported), max(reported)))
        gaps = sorted(expected - reported)
        if gaps:
            missing[symbol] = gaps

    assert phantom == KNOWN_PHANTOM_SESSIONS
    assert missing == {}


def test_the_calendar_finds_the_same_session_count_as_the_clean_symbols(vendor_session_dates):
    """
    AAPL and MSFT carry no phantom rows, so their row count is the session count.
    """
    aapl = vendor_session_dates["AAPL"]
    assert len(trading_days(min(aapl), max(aapl))) == len(aapl) == 5283


@pytest.mark.parametrize(
    ("year", "expected"),
    [
        (2024, date(2024, 3, 31)),
        (2023, date(2023, 4, 9)),
        (2020, date(2020, 4, 12)),
        (2005, date(2005, 3, 27)),
        (2038, date(2038, 4, 25)),
    ],
)
def test_easter_sunday(year, expected):
    assert easter_sunday(year) == expected


@pytest.mark.parametrize(
    ("day", "why"),
    [
        (date(2024, 3, 29), "Good Friday"),
        (date(2023, 11, 23), "Thanksgiving"),
        (date(2024, 6, 19), "Juneteenth, observed from 2022"),
        (date(2021, 7, 5), "Independence Day observed, 4 July was a Sunday"),
        (date(2020, 12, 25), "Christmas"),
        (date(2025, 1, 9), "unscheduled: national day of mourning"),
        (date(2012, 10, 30), "unscheduled: Hurricane Sandy"),
        (date(2024, 1, 6), "Saturday"),
    ],
)
def test_closed_days(day, why):
    assert not is_trading_day(day), why


@pytest.mark.parametrize(
    ("day", "why"),
    [
        (date(2021, 6, 18), "Juneteenth was not yet a market holiday in 2021"),
        (date(2021, 12, 31), "the exchange trades when the federal holiday shifts back"),
        (date(2010, 12, 31), "same rule, an earlier decade"),
        (date(2024, 11, 29), "the half day after Thanksgiving is still a session"),
        (date(2024, 12, 24), "Christmas Eve is a half day, not a closure"),
    ],
)
def test_open_days(day, why):
    assert is_trading_day(day), why


def test_juneteenth_is_not_backdated():
    """
    The holiday exists from 2022; applying it to 2005 would delete a real session.
    """
    assert date(2019, 6, 19) not in market_holidays(2019)
    assert date(2022, 6, 20) in market_holidays(2022)  # 19th was a Sunday


def test_trading_days_is_inclusive_and_ordered():
    days = trading_days(date(2024, 3, 25), date(2024, 4, 1))
    assert days == (
        date(2024, 3, 25),
        date(2024, 3, 26),
        date(2024, 3, 27),
        date(2024, 3, 28),
        date(2024, 4, 1),
    )
    assert list(days) == sorted(days)
