"""
Tests for the daily-versus-intraday check.

The comparison is one-sided and that is the whole subtlety: the daily bars are
consolidated, the intraday path is one venue, so only a daily bar that is too *narrow*
is evidence. A test suite that asserted agreement in both directions would be asserting
something the data cannot support.

"""

from __future__ import annotations

from datetime import UTC
from datetime import date
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from copilot.data.intraday_check import Envelope
from copilot.data.intraday_check import compare
from copilot.data.intraday_check import session_date


def stamp(month: int, day: int, hour: int, minute: int, year: int = 2025) -> int:
    """
    Build a nanosecond epoch from a wall clock reading in New York.
    """
    moment = datetime(year, month, day, hour, minute, tzinfo=ZoneInfo("America/New_York"))
    return int(moment.astimezone(UTC).timestamp() * 1_000_000_000)


def test_regular_hours_map_to_their_session() -> None:
    assert session_date(stamp(6, 3, 9, 30)) == date(2025, 6, 3)
    assert session_date(stamp(6, 3, 15, 59)) == date(2025, 6, 3)


def test_prints_outside_the_session_are_dropped() -> None:
    assert session_date(stamp(6, 3, 9, 29)) is None
    assert session_date(stamp(6, 3, 16, 0)) is None
    assert session_date(stamp(6, 3, 7, 15)) is None


def test_an_early_close_ends_the_session_at_one() -> None:
    """
    Without this, the three hours after a half-day close are read as session range and
    the extended-hours prints in them are reported as defects in the daily bar.
    """
    assert session_date(stamp(11, 28, 12, 59)) == date(2025, 11, 28)
    assert session_date(stamp(11, 28, 13, 30)) is None
    assert session_date(stamp(12, 1, 13, 30)) == date(2025, 12, 1)


def test_a_daily_bar_containing_the_venue_path_is_not_a_finding() -> None:
    """
    A consolidated bar is expected to be wider than one venue's.

    Containment is the normal case and proves nothing on its own.

    """
    daily = {date(2025, 6, 3): (Decimal("101.00"), Decimal("99.00"))}
    venue = {date(2025, 6, 3): Envelope(high=Decimal("100.50"), low=Decimal("99.50"))}

    assert compare("AAPL", daily, venue) == []


def test_a_daily_high_below_a_venue_print_is_a_finding() -> None:
    """
    The venue's trade is part of the consolidated tape by construction, so a
    consolidated high beneath it cannot be right.
    """
    daily = {date(2025, 6, 3): (Decimal("100.00"), Decimal("99.00"))}
    venue = {date(2025, 6, 3): Envelope(high=Decimal("100.50"), low=Decimal("99.50"))}

    (found,) = compare("AAPL", daily, venue)

    assert found.field == "high"
    assert found.bps == Decimal(50)


def test_a_daily_low_above_a_venue_print_is_a_finding() -> None:
    daily = {date(2025, 6, 3): (Decimal("101.00"), Decimal("100.00"))}
    venue = {date(2025, 6, 3): Envelope(high=Decimal("100.50"), low=Decimal("99.50"))}

    (found,) = compare("AAPL", daily, venue)

    assert found.field == "low"


def test_a_hair_of_disagreement_is_inside_tolerance() -> None:
    """
    Sub-basis-point differences are the floor: the vendor's range is not necessarily
    the consolidated extreme, and a back-adjusted price does not share a quantum with a
    stored one.
    """
    daily = {date(2025, 6, 3): (Decimal("100.0000"), Decimal("99.0000"))}
    venue = {date(2025, 6, 3): Envelope(high=Decimal("100.0005"), low=Decimal("99.0000"))}

    assert compare("AAPL", daily, venue) == []


def test_only_days_both_sources_carry_are_compared() -> None:
    """
    The venue files start in 2018 and the catalog in 2005.

    The missing years are not findings.

    """
    daily = {
        date(2010, 6, 3): (Decimal("50.00"), Decimal("49.00")),
        date(2025, 6, 3): (Decimal("101.00"), Decimal("99.00")),
    }
    venue = {date(2025, 6, 3): Envelope(high=Decimal("100.50"), low=Decimal("99.50"))}

    assert compare("AAPL", daily, venue) == []
