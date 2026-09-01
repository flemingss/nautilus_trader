"""
US equity trading calendar, computed from rules rather than fetched.

Why this exists
---------------
Marketstack returns rows for days the US market was closed. Verified against the
live API: SPY has a complete-looking bar for **2023-11-23 (Thanksgiving)** and
**2024-03-29 (Good Friday)**, each with a plausible OHLC and nine-figure volume.
Nothing in the payload marks them as phantom.

A phantom bar is not a cosmetic defect. The walk-forward gate would let a strategy
enter, exit and be *scored* on a day no order could have been placed, and the
resulting edge would be unreproducible in live trading. So the calendar is a
correctness gate, not a tidiness one.

Why rules instead of a library
------------------------------
`exchange_calendars` and `pandas_market_calendars` both solve this, but the overlay
deliberately adds no dependency (see the pydantic-to-dataclasses port note in
`risk/protections.py`). US equity holidays are rule-based and stable, so the rules
are ~100 lines and fully auditable, and the ad-hoc closures are a short list.

The rules are validated empirically rather than by assertion: `tests/test_calendar.py`
checks this calendar against 15,851 real Marketstack rows spanning 2005-2025 and
requires that it flags **only** the two known phantom rows. A calendar that wrongly
marks a real session closed would drop live data, so that test is the load-bearing
one — treat a failure as a defect in this file, not in the fixture.

Scope
-----
Regular sessions only. Early closes (1pm on the day after Thanksgiving, Christmas
Eve) are still trading days and are not distinguished, which is correct for daily
bars and would not be for intraday.
"""

from __future__ import annotations

from datetime import date, timedelta

SATURDAY = 5
SUNDAY = 6
"""Named so the weekend check reads as a weekend check rather than a bare 5."""

# Ad-hoc, non-rule closures. Each one is a day the exchange was shut for a reason no
# rule predicts, so it can only be enumerated. Restricted to the window the overlay
# actually ingests; add to this list rather than inventing a rule for it.
UNSCHEDULED_CLOSURES: frozenset[date] = frozenset(
    {
        date(2004, 6, 11),  # National day of mourning, President Reagan
        date(2007, 1, 2),  # National day of mourning, President Ford
        date(2012, 10, 29),  # Hurricane Sandy
        date(2012, 10, 30),  # Hurricane Sandy
        date(2018, 12, 5),  # National day of mourning, President G. H. W. Bush
        date(2025, 1, 9),  # National day of mourning, President Carter
    },
)

# Juneteenth became a US federal holiday in 2021; the exchanges first observed it as a
# market holiday in 2022. Before that the market traded on 19 June.
JUNETEENTH_FIRST_OBSERVED = 2022


def easter_sunday(year: int) -> date:
    """
    Easter Sunday by the anonymous Gregorian algorithm.

    Needed only to locate Good Friday, which is the one moveable US market holiday
    that does not follow a weekday-of-month rule.
    """
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (a + 11 * h) // 319
    r = (2 * e + 2 * i - h + m - k + 32) % 7
    month = (h - m + r + 90) // 25
    day = (h - m + r + month + 19) % 32
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """Return the ``n``-th ``weekday`` (Mon=0) of a month."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """Return the last ``weekday`` (Mon=0) of a month."""
    next_month = date(year + month // 12, month % 12 + 1, 1)
    last = next_month - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(day: date) -> date:
    """
    Shift a fixed-date holiday to the weekday the exchange actually closes.

    Saturday moves back to Friday, Sunday forward to Monday. This is the rule the US
    exchanges use, and it is why 4 July can close the market on the 3rd or the 5th.
    """
    if day.weekday() == SATURDAY:
        return day - timedelta(days=1)
    if day.weekday() == SUNDAY:
        return day + timedelta(days=1)
    return day


def market_holidays(year: int) -> frozenset[date]:
    """Every scheduled full-day US equity market closure in ``year``."""
    holidays = {
        _observed(date(year, 1, 1)),  # New Year's Day
        _nth_weekday(year, 1, 0, 3),  # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),  # Washington's Birthday
        easter_sunday(year) - timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),  # Memorial Day
        _observed(date(year, 7, 4)),  # Independence Day
        _nth_weekday(year, 9, 0, 1),  # Labor Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
        _observed(date(year, 12, 25)),  # Christmas
    }
    if year >= JUNETEENTH_FIRST_OBSERVED:
        holidays.add(_observed(date(year, 6, 19)))

    # Deliberately NOT here: 31 December when 1 January falls on a Saturday. The
    # federal holiday shifts back to that Friday, but the exchanges stay open — an
    # earlier version of this file closed it and the fixture test caught the error,
    # because AAPL, MSFT and SPY all traded on 2010-12-31 and 2021-12-31.

    return frozenset(holidays)


def is_trading_day(day: date) -> bool:
    """
    Whether the US equity market held a regular session on ``day``.

    Half days count as trading days: the session opened, so an order could be placed.
    """
    if day.weekday() >= SATURDAY:
        return False
    if day in UNSCHEDULED_CLOSURES:
        return False
    return day not in market_holidays(day.year)


def trading_days(start: date, end: date) -> tuple[date, ...]:
    """Every trading day in ``[start, end]``, inclusive and in order."""
    out: list[date] = []
    day = start
    while day <= end:
        if is_trading_day(day):
            out.append(day)
        day += timedelta(days=1)
    return tuple(out)


__all__ = [
    "JUNETEENTH_FIRST_OBSERVED",
    "UNSCHEDULED_CLOSURES",
    "easter_sunday",
    "is_trading_day",
    "market_holidays",
    "trading_days",
]
