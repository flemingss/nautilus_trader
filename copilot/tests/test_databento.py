"""
Tests for the Databento client and its intraday fidelity probe.

The probe exists because the previous intraday vendor returned rows that were shaped
like OHLCV bars and were not: one distinct price repeated across the session, and a
cumulative volume counter wearing a per-interval name. Reading those as bars hands a
backtest the day's close at 09:31.

So the tests that matter are the ones that reproduce that exact payload and assert the
probe rejects it. A probe that only passes clean data would not have caught the failure
it was written for.

"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from decimal import Decimal

import pytest

from copilot.data.databento import API_KEY_ENV
from copilot.data.databento import DatabentoClient
from copilot.data.databento import DatabentoError
from copilot.data.databento import Minute
from copilot.data.databento import measure
from copilot.data.databento import normalize_minute


NANOS_PER_SECOND = 1_000_000_000
SCALE = 10**9


def raw_minute(
    index: int,
    open_: str,
    high: str,
    low: str,
    close: str,
    volume: int,
) -> dict[str, object]:
    """
    Build one vendor row, prices scaled the way the wire format scales them.
    """
    started = datetime(2024, 1, 2, 14, 30, tzinfo=UTC).timestamp()
    return {
        "ts_event": int((started + index * 60) * NANOS_PER_SECOND),
        "open": int(Decimal(open_) * SCALE),
        "high": int(Decimal(high) * SCALE),
        "low": int(Decimal(low) * SCALE),
        "close": int(Decimal(close) * SCALE),
        "volume": volume,
    }


def real_bars(count: int = 200) -> list[Minute]:
    """
    Build a sample that behaves like a genuine minute series.

    Prices walk and volume rises and falls, which is the whole distinction being drawn.

    """
    rows = []
    for i in range(count):
        base = Decimal(180) + Decimal(i) / 100
        rows.append(
            raw_minute(
                i,
                open_=str(base),
                high=str(base + Decimal("0.05")),
                low=str(base - Decimal("0.05")),
                close=str(base + Decimal("0.01")),
                volume=1000 + (i % 7) * 130,
            ),
        )
    return [normalize_minute(row) for row in rows]


def repeated_day_bars(count: int = 200) -> list[Minute]:
    """
    Reproduce the previous vendor's failure: the day's OHLC repeated, volume cumulative.
    """
    rows = []
    running = 0
    for i in range(count):
        running += 5000
        rows.append(
            raw_minute(
                i,
                open_="180.00",
                high="182.50",
                low="179.10",
                close="181.75",
                volume=running,
            ),
        )
    return [normalize_minute(row) for row in rows]


def test_prices_survive_the_wire_format_exactly() -> None:
    minute = normalize_minute(raw_minute(0, "180.12", "180.99", "179.87", "180.45", 1234))

    assert minute.open == Decimal("180.12")
    assert minute.close == Decimal("180.45")
    assert minute.volume == 1234
    # Exactness is the point: a float round trip would not compare equal here.
    assert isinstance(minute.open, Decimal)


def test_timestamp_is_utc() -> None:
    minute = normalize_minute(raw_minute(0, "180", "181", "179", "180.5", 10))

    assert minute.opened_at.tzinfo is UTC
    assert minute.opened_at == datetime(2024, 1, 2, 14, 30, tzinfo=UTC)


def test_a_bar_whose_close_sits_outside_its_range_is_incoherent() -> None:
    outside = normalize_minute(raw_minute(0, "180", "181", "179", "181.5", 10))
    inside = normalize_minute(raw_minute(0, "180", "181", "179", "180.5", 10))

    assert not outside.coherent
    assert inside.coherent


def test_the_probe_accepts_a_genuine_minute_series() -> None:
    finding = measure("AAPL", "EQUS.SUMMARY", real_bars())

    assert finding.looks_like_real_bars
    assert all(count > 1 for count in finding.distinct.values())
    assert not finding.volume_is_monotonic
    assert finding.incoherent_rows == 0


def test_the_probe_rejects_the_repeated_day_payload() -> None:
    """
    This is the regression that matters: the exact shape that slipped through before.
    """
    finding = measure("AAPL", "EQUS.SUMMARY", repeated_day_bars())

    assert not finding.looks_like_real_bars
    assert finding.distinct == {"open": 1, "high": 1, "low": 1, "close": 1}
    assert finding.volume_is_monotonic


def test_a_repeated_price_alone_fails_even_when_volume_looks_fine() -> None:
    """
    The two defects arrived together, but either one on its own is disqualifying.
    """
    rows = [
        normalize_minute(raw_minute(i, "180", "182.5", "179.1", "181.75", 900 + (i % 5) * 40))
        for i in range(50)
    ]

    finding = measure("AAPL", "EQUS.SUMMARY", rows)

    assert not finding.looks_like_real_bars
    assert not finding.volume_is_monotonic


def test_cumulative_volume_alone_fails_even_when_prices_vary() -> None:
    rows = []
    running = 0
    for i in range(50):
        running += 5000
        base = Decimal(180) + Decimal(i) / 100
        rows.append(
            normalize_minute(
                raw_minute(
                    i,
                    str(base),
                    str(base + Decimal("0.05")),
                    str(base - Decimal("0.05")),
                    str(base + Decimal("0.01")),
                    running,
                ),
            ),
        )

    finding = measure("AAPL", "EQUS.SUMMARY", rows)

    assert not finding.looks_like_real_bars
    assert finding.volume_is_monotonic


def test_an_incoherent_row_fails_the_probe() -> None:
    rows = real_bars(20)
    rows.append(normalize_minute(raw_minute(20, "180", "181", "179", "185", 500)))

    finding = measure("AAPL", "EQUS.SUMMARY", rows)

    assert finding.incoherent_rows == 1
    assert not finding.looks_like_real_bars


def test_measuring_nothing_is_an_error_not_a_pass() -> None:
    """
    An empty response must not read as a clean probe.
    """
    with pytest.raises(DatabentoError, match="no rows"):
        measure("AAPL", "EQUS.SUMMARY", [])


def test_a_client_without_a_key_refuses_to_be_built() -> None:
    with pytest.raises(DatabentoError, match=API_KEY_ENV):
        DatabentoClient(api_key="")


def test_the_report_names_the_verdict() -> None:
    good = measure("AAPL", "EQUS.SUMMARY", real_bars(30)).report()
    bad = measure("AAPL", "EQUS.SUMMARY", repeated_day_bars(30)).report()

    assert "real bars" in good
    assert "NOT USABLE" in bad
