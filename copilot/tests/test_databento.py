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

import argparse
from datetime import UTC
from datetime import date
from datetime import datetime
from decimal import Decimal

import pytest

from copilot.data.databento import API_KEY_ENV
from copilot.data.databento import DatabentoClient
from copilot.data.databento import DatabentoError
from copilot.data.databento import Minute
from copilot.data.databento import _cost
from copilot.data.databento import _symbol_for
from copilot.data.databento import _wanted_symbols
from copilot.data.databento import audit_symbol
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


def series(
    closes: dict[str, str],
    volumes: dict[str, int] | None = None,
) -> dict[date, tuple[Decimal, int]]:
    """
    Build one source's daily closes, keyed by date.
    """
    volumes = volumes or {}
    return {
        date.fromisoformat(day): (Decimal(close), volumes.get(day, 1_000_000))
        for day, close in closes.items()
    }


def test_an_exact_match_audits_clean() -> None:
    held = series({"2024-08-01": "218.36", "2024-08-02": "219.86"})
    vendor = series({"2024-08-01": "218.36", "2024-08-02": "219.86"})

    result = audit_symbol("AAPL", vendor, held)

    assert result.clean
    assert result.days_compared == 2
    assert result.max_deviation_bps == Decimal("0.00")


def test_trailing_zeros_do_not_count_as_a_mismatch() -> None:
    """
    The catalog stores 218.3600 and the vendor sends 218.36.

    Same price.

    """
    result = audit_symbol(
        "AAPL",
        series({"2024-08-01": "218.36"}),
        series({"2024-08-01": "218.3600"}),
    )

    assert result.clean


def test_a_cent_of_disagreement_is_reported_in_basis_points() -> None:
    held = series({"2024-08-01": "100.00"})
    vendor = series({"2024-08-01": "100.05"})

    result = audit_symbol("AAPL", vendor, held)

    assert not result.clean
    assert result.exact_closes == 0
    assert result.max_deviation_bps == Decimal("5.00")
    assert result.worst_day == "2024-08-01"


def test_only_overlapping_days_are_compared() -> None:
    """
    A vendor window shorter than the catalog narrows the audit; it does not fail it.
    """
    held = series({"2024-08-01": "100.00", "2024-08-02": "101.00", "2024-08-05": "102.00"})
    vendor = series({"2024-08-02": "101.00"})

    result = audit_symbol("AAPL", vendor, held)

    assert result.days_compared == 1
    assert result.clean


def test_no_overlap_is_never_clean() -> None:
    """
    Comparing nothing must not read as agreement.
    """
    result = audit_symbol(
        "AAPL",
        series({"2023-01-03": "100.00"}),
        series({"2024-08-01": "100.00"}),
    )

    assert result.days_compared == 0
    assert not result.clean


def test_volume_ratio_is_reported_because_prices_can_agree_while_volume_does_not() -> None:
    """
    EQUS.MINI matched prices closely and carried ~3% of the tape's volume.
    """
    held = series(
        {"2024-08-01": "100.00", "2024-08-02": "100.00"},
        {"2024-08-01": 100, "2024-08-02": 100},
    )
    vendor = series(
        {"2024-08-01": "100.00", "2024-08-02": "100.00"},
        {"2024-08-01": 3, "2024-08-02": 3},
    )

    result = audit_symbol("AAPL", vendor, held)

    assert result.clean
    assert result.median_volume_ratio == Decimal("0.030")


def test_an_instrument_id_resolves_to_the_symbol_it_stood_for() -> None:
    """
    Rows carry a numeric id and no symbol, so a wrong mapping silently attributes one
    company's prices to another.

    That is the worst failure this module can have.

    """
    intervals = [
        ("AAPL", 38, date(2024, 7, 1), date(2025, 12, 31)),
        ("MSFT", 10888, date(2024, 7, 1), date(2025, 12, 31)),
    ]

    assert _symbol_for(intervals, 38, date(2024, 8, 1)) == "AAPL"
    assert _symbol_for(intervals, 10888, date(2024, 8, 1)) == "MSFT"


def test_an_id_reassigned_between_symbols_resolves_by_date() -> None:
    intervals = [
        ("OLDCO", 500, date(2020, 1, 1), date(2021, 12, 31)),
        ("NEWCO", 500, date(2022, 1, 1), date(2025, 12, 31)),
    ]

    assert _symbol_for(intervals, 500, date(2021, 6, 1)) == "OLDCO"
    assert _symbol_for(intervals, 500, date(2023, 6, 1)) == "NEWCO"


def test_an_unmapped_id_resolves_to_nothing_rather_than_a_guess() -> None:
    intervals = [("AAPL", 38, date(2024, 7, 1), date(2025, 12, 31))]

    assert _symbol_for(intervals, 999, date(2024, 8, 1)) == ""
    assert _symbol_for(intervals, 38, date(2019, 1, 1)) == ""


class TestPullScope:
    """
    Which symbols a bulk pull buys, and which schema a price is quoted for.

    Both were found by the ETF onboarding drill on 2026-09-04 and both cost real money.
    An unrestricted pull bought the whole catalog when six symbols were wanted - $9.96
    against $2.40 - and ``--cost`` answered for ``ohlcv-1m`` no matter which schema was
    about to be bought, which is the one thing [ADR-0015] asks that flag to prevent.

    """

    def args(self, **kwargs: object) -> argparse.Namespace:
        return argparse.Namespace(**{"only": None, **kwargs})

    def test_no_restriction_means_the_whole_catalog(self) -> None:
        """
        The default has to keep meaning what it did, or a scheduled pull changes
        silently.
        """
        assert _wanted_symbols(self.args()) is None

    def test_a_restriction_is_parsed_into_symbols(self) -> None:
        """
        The onboarding case: buy the new arrivals, not the universe again.
        """
        assert _wanted_symbols(self.args(only="SCHX,TLT,GLDM")) == {"SCHX", "TLT", "GLDM"}

    def test_symbols_are_upper_cased_and_trimmed(self) -> None:
        """
        The catalog keys on upper case, and a typed list carries spaces.
        """
        assert _wanted_symbols(self.args(only=" schx , tlt ")) == {"SCHX", "TLT"}

    def test_empty_entries_are_dropped(self) -> None:
        """
        A trailing comma must not become an empty symbol that matches nothing.
        """
        assert _wanted_symbols(self.args(only="SCHX,,TLT,")) == {"SCHX", "TLT"}

    def test_an_empty_restriction_is_no_restriction(self) -> None:
        """
        Passing nothing is not the same as asking for nothing.
        """
        assert _wanted_symbols(self.args(only="")) is None

    def test_cost_prices_the_schema_it_is_given(self) -> None:
        """
        A quote schema costs multiples of a bar schema; quoting the wrong one is the
        bug.
        """
        seen: list[str] = []

        class Recorder:
            dataset = "ARCX.PILLAR"

            def cost(self, symbols, schema, start, end):
                seen.append(schema)
                return Decimal("1.94")

        _cost(Recorder(), ["SCHX"], "2018-05-01", "2026-09-03", "bbo-1m")
        assert seen == ["bbo-1m"]

    def test_cost_still_defaults_to_one_minute_bars(self) -> None:
        """
        The default is the shape the flag has always priced.
        """
        seen: list[str] = []

        class Recorder:
            dataset = "ARCX.PILLAR"

            def cost(self, symbols, schema, start, end):
                seen.append(schema)
                return Decimal("0.02")

        _cost(Recorder(), ["SCHX"], "2018-05-01", "2026-09-03")
        assert seen == ["ohlcv-1m"]
