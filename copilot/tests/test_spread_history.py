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

from copilot.calibration.spread_history import EXECUTION_PREFIX
from copilot.calibration.spread_history import EXECUTION_WINDOW_MINUTES
from copilot.calibration.spread_history import UNDEFINED
from copilot.calibration.spread_history import bucket_for
from copilot.calibration.spread_history import execution_key
from copilot.calibration.spread_history import execution_years
from copilot.calibration.spread_history import resolve
from copilot.calibration.spread_history import summarise
from copilot.calibration.spread_history import worst_execution_year


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


# --- the execution window, and the year it charges from ------------------------------


def dist(p95: float, samples: int = 1_000):
    """
    Build a distribution with only the field the charge reads.
    """
    from copilot.calibration.spread_history import Distribution

    return Distribution(samples=samples, median=p95 / 2, p75=p95, p95=p95, p99=p95, maximum=p95)


def test_the_execution_window_starts_at_the_open() -> None:
    # It includes the first five minutes, which are the widest of the day. Excluding
    # them understates the charge on exactly the moment the order is most likely to
    # cross, and did - the first attempt at this cut returned early on an "open" bucket.
    assert execution_key(eastern(6, 3, 9, 30)) == f"{EXECUTION_PREFIX}:2024"
    assert execution_key(eastern(6, 3, 9, 29)) is None


def test_the_execution_window_is_two_hours_long() -> None:
    assert EXECUTION_WINDOW_MINUTES == 120
    assert execution_key(eastern(6, 3, 11, 29)) is not None
    assert execution_key(eastern(6, 3, 11, 30)) is None


def test_the_execution_window_is_not_part_of_the_partition() -> None:
    # It overlaps open and session on purpose; a cost is not a census.
    moment = eastern(6, 3, 9, 31)
    assert bucket_for(moment) == "open"
    assert execution_key(moment) is not None


def test_the_execution_key_carries_the_eastern_year() -> None:
    # 2025-01-01 00:30 UTC is still 2024 in New York, and a quote is filed under the
    # session's own calendar rather than under UTC's.
    from datetime import UTC
    from datetime import datetime

    moment = datetime(2025, 1, 1, 0, 30, tzinfo=UTC)
    assert execution_key(moment) is None  # outside regular hours either way


def test_execution_years_ignores_the_partition_buckets() -> None:
    windows = {"open": dist(9.0), "session": dist(2.0), f"{EXECUTION_PREFIX}:2024": dist(3.0)}
    assert sorted(execution_years(windows)) == [2024]


def test_the_charge_takes_the_widest_year_not_the_average() -> None:
    # Spreads here are set by volatility regime rather than by a trend, so the pooled
    # number prices a year that did not happen.
    windows = {
        f"{EXECUTION_PREFIX}:2021": dist(1.0),
        f"{EXECUTION_PREFIX}:2020": dist(4.0),
        f"{EXECUTION_PREFIX}:2023": dist(2.0),
    }
    year, chosen = worst_execution_year(windows)
    assert year == 2020
    assert chosen.p95 == 4.0


def test_a_symbol_with_no_execution_samples_charges_nothing() -> None:
    # Absent rather than defaulted: the cost model turns that into a refusal to score.
    assert worst_execution_year({"session": dist(2.0)}) is None
