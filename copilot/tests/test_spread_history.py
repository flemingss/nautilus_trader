"""
Tests for the historical spread measurement.

The number this produces goes straight into what a verdict is charged, so the two
failures that matter are silent ones: a sentinel price read as a real quote, which
would put a nine-billion-basis-point spread in the sample; and a quote attributed to
the wrong window, which would let a mid-session spread stand in for the closing one the
strategy actually pays.

"""

from __future__ import annotations

from array import array
from datetime import UTC
from datetime import date
from datetime import datetime

from copilot.calibration.spread_history import UNDEFINED
from copilot.calibration.spread_history import bucket_for
from copilot.calibration.spread_history import resolve
from copilot.calibration.spread_history import summarise


def eastern(month: int, day: int, hour: int, minute: int) -> datetime:
    """
    Build a UTC instant from a wall clock reading in New York.
    """
    from zoneinfo import ZoneInfo

    return datetime(2024, month, day, hour, minute, tzinfo=ZoneInfo("America/New_York")).astimezone(
        UTC,
    )


def test_the_first_minutes_are_the_open_window() -> None:
    assert bucket_for(eastern(6, 3, 9, 30)) == "open"
    assert bucket_for(eastern(6, 3, 9, 34)) == "open"
    assert bucket_for(eastern(6, 3, 9, 35)) == "session"


def test_the_last_minutes_are_the_close_window() -> None:
    """
    ADR-0013 restricts a holdout spend to a next-close entry, so this is the window
    whose spread the strategy actually pays.
    """
    assert bucket_for(eastern(6, 3, 15, 54)) == "session"
    assert bucket_for(eastern(6, 3, 15, 55)) == "close"
    assert bucket_for(eastern(6, 3, 15, 59)) == "close"


def test_quotes_outside_regular_hours_are_dropped() -> None:
    assert bucket_for(eastern(6, 3, 9, 29)) is None
    assert bucket_for(eastern(6, 3, 16, 0)) is None
    assert bucket_for(eastern(6, 3, 4, 15)) is None
    assert bucket_for(eastern(6, 3, 19, 30)) is None


def test_the_window_follows_the_exchange_clock_not_utc() -> None:
    """
    The session opens at 13:30 UTC in summer and 14:30 in winter.

    Bucketing on UTC would put January's open in the pre-market bin and lose it
    entirely.

    """
    summer = datetime(2024, 6, 3, 13, 30, tzinfo=UTC)
    winter = datetime(2024, 1, 3, 14, 30, tzinfo=UTC)

    assert bucket_for(summer) == "open"
    assert bucket_for(winter) == "open"


def test_an_empty_bucket_summarises_to_nothing_rather_than_zero() -> None:
    """
    A zero spread would read as a free trade, which is the most expensive possible way
    to be wrong here.
    """
    assert summarise(array("f")) is None


def test_percentiles_come_off_the_sorted_sample() -> None:
    measured = summarise(array("f", [float(x) for x in range(1, 101)]))

    assert measured is not None
    assert measured.samples == 100
    assert measured.median == 51
    assert measured.p95 == 96
    assert measured.maximum == 100


def test_input_order_does_not_change_the_answer() -> None:
    rising = summarise(array("f", [1.0, 2.0, 3.0, 4.0]))
    falling = summarise(array("f", [4.0, 3.0, 2.0, 1.0]))

    assert rising is not None
    assert falling is not None
    assert rising.p95 == falling.p95
    assert rising.median == falling.median


def test_the_sentinel_is_the_value_that_must_never_be_priced() -> None:
    """
    Databento writes INT64_MAX for "no quote".

    Treated as a price against a $200 stock it is a spread of nine hundred million basis
    points, and one of them in the sample moves the p99.

    """
    assert UNDEFINED == 9223372036854775807
    assert (UNDEFINED - 200_000_000_000) / 200_000_000_000 * 10000 > 100_000_000


def symbology(entries: list[tuple[int, str, str, str]]) -> dict:
    """
    Build a symbology map from (id, symbol, from, to) tuples.
    """
    out: dict = {}
    for instrument_id, symbol, first, last in entries:
        out.setdefault(instrument_id, []).append(
            (date.fromisoformat(first), date.fromisoformat(last), symbol),
        )
    for spans in out.values():
        spans.sort()
    return out


def test_an_id_shared_by_two_symbols_resolves_by_date() -> None:
    """
    Measured on the stored XNAS pull: 525 instrument ids are used by more than one of
    eight symbols, GOOGL and INTC among them. Flattening the map to id -> symbol files
    one company's quotes under the other's name, and the spread still looks plausible.
    """
    mapping = symbology(
        [
            (4065, "GOOGL", "2019-01-02", "2019-06-01"),
            (4065, "INTC", "2019-06-01", "2020-01-01"),
        ],
    )

    assert resolve(mapping, 4065, date(2019, 3, 1)) == "GOOGL"
    assert resolve(mapping, 4065, date(2019, 9, 1)) == "INTC"


def test_the_handover_date_belongs_to_the_incoming_symbol() -> None:
    """
    The vendor's ranges abut, so an inclusive upper bound matches both on that day.
    """
    mapping = symbology(
        [
            (13, "AAPL", "2018-05-01", "2018-08-15"),
            (14, "AAPL", "2018-08-15", "2019-10-07"),
        ],
    )

    assert resolve(mapping, 13, date(2018, 8, 14)) == "AAPL"
    assert resolve(mapping, 13, date(2018, 8, 15)) is None
    assert resolve(mapping, 14, date(2018, 8, 15)) == "AAPL"


def test_an_id_outside_every_range_resolves_to_nothing() -> None:
    """
    A pull covers the symbols asked for; every other id on the venue must be dropped,
    not guessed at.
    """
    mapping = symbology([(13, "AAPL", "2018-05-01", "2018-08-15")])

    assert resolve(mapping, 13, date(2020, 1, 2)) is None
    assert resolve(mapping, 9999, date(2018, 6, 1)) is None
