"""
Tests for the Marketstack-to-catalog bridge.

These run a real ``ParquetDataCatalog`` against a temporary directory rather than a
mock. The catalog is a Rust component reached through pyo3, and the failure this
suite most needs to catch - a price quietly rounded on the way to disk - only happens
in the real conversion.

"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from decimal import Decimal

import pytest

from copilot.data.catalog import PRICE_PRECISION
from copilot.data.catalog import bar_type_for
from copilot.data.catalog import equity_for
from copilot.data.catalog import open_catalog
from copilot.data.catalog import read_daily_bars
from copilot.data.catalog import to_nautilus_bars
from copilot.data.catalog import venues_from_rows
from copilot.data.catalog import write_ingestion
from copilot.data.marketstack import IngestionResult
from copilot.validation.types import DailyBar


def daily(
    symbol="AAPL",
    day=(2025, 6, 6),
    *,
    open_="203.0",
    high="205.7",
    low="202.05",
    close="203.92",
    volume=46539200,
):
    return DailyBar(
        symbol=symbol,
        closed_at=datetime(*day, tzinfo=UTC),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=volume,
    )


@pytest.fixture
def instrument():
    return equity_for("AAPL", "XNAS")


@pytest.fixture
def catalog(tmp_path):
    return open_catalog(tmp_path / "store")


def test_precision_covers_what_the_vendor_returns(instrument):
    """
    Four decimal places, measured against 63,404 real vendor prices.

    The deepest value the vendor returns is 4 dp (12,924 of them), so this is exact
    rather than generous.

    """
    assert PRICE_PRECISION == 4
    assert instrument.price_precision == 4


def test_a_price_that_would_be_rounded_raises(instrument):
    """
    Storing a rounded price is the failure this guard exists for.

    A transient replay can tolerate it; a stored history cannot, because every later run
    reads the file as ground truth without ever seeing the vendor's number.

    """
    bar_type = bar_type_for(instrument.id)
    too_deep = daily(open_="203.00001", high="205.7", low="202.05", close="203.92")

    with pytest.raises(ValueError, match="does not survive"):
        to_nautilus_bars([too_deep], instrument, bar_type)


def test_conversion_preserves_every_price_exactly(instrument):
    bar_type = bar_type_for(instrument.id)
    source = daily(open_="1.1302", high="126.5225", low="1.1302", close="124.8075")

    (bar,) = to_nautilus_bars([source], instrument, bar_type)

    assert Decimal(str(bar.open)) == source.open
    assert Decimal(str(bar.high)) == source.high
    assert Decimal(str(bar.low)) == source.low
    assert Decimal(str(bar.close)) == source.close
    assert int(Decimal(str(bar.volume))) == source.volume


def test_a_billion_share_session_survives_conversion(instrument):
    """
    Regression: AAPL traded 1,020,062,400 split-adjusted shares on 2005-02-02.

    `Quantity` stores it exactly, but `Quantity.as_double()` returns
    1020062399.9999999. A guard written against the float rejected a volume the
    catalog would have stored perfectly well, so the check goes through `str`.
    """
    bar_type = bar_type_for(instrument.id)
    source = daily(
        day=(2005, 2, 2),
        open_="1.13",
        high="1.20",
        low="1.10",
        close="1.15",
        volume=1020062400,
    )

    (bar,) = to_nautilus_bars([source], instrument, bar_type)

    assert int(Decimal(str(bar.volume))) == 1020062400


def test_conversion_sorts_chronologically(instrument):
    bar_type = bar_type_for(instrument.id)
    bars = to_nautilus_bars(
        [daily(day=(2025, 6, 6)), daily(day=(2025, 6, 4)), daily(day=(2025, 6, 5))],
        instrument,
        bar_type,
    )
    assert [b.ts_event for b in bars] == sorted(b.ts_event for b in bars)


def test_write_then_read_round_trips(catalog):
    """The full path: gate bars in, parquet on disk, gate bars back out unchanged."""
    source = [daily(day=(2025, 6, 4)), daily(day=(2025, 6, 5)), daily(day=(2025, 6, 6))]
    result = IngestionResult(bars=tuple(source), fetched=3)

    (report,) = write_ingestion(catalog, result, venues={"AAPL": "XNAS"})
    assert report.bars_written == 3
    assert report.instrument_id == "AAPL.XNAS"

    back = read_daily_bars(catalog, bar_type_for(equity_for("AAPL", "XNAS").id))
    assert back == tuple(source)


def test_the_instrument_is_written_with_its_bars(catalog):
    """
    Bars without an instrument are half a dataset.

    ``BacktestNode`` needs both, and finding the instrument missing at run time is a
    much worse place to discover it than at ingestion.

    """
    write_ingestion(catalog, IngestionResult(bars=(daily(),), fetched=1), venues={"AAPL": "XNAS"})

    instruments = catalog.instruments()
    assert [str(i.id) for i in instruments] == ["AAPL.XNAS"]


def test_each_symbol_gets_its_own_series(catalog):
    result = IngestionResult(
        bars=(daily(symbol="AAPL"), daily(symbol="SPY", day=(2025, 6, 6))),
        fetched=2,
    )
    reports = write_ingestion(catalog, result, venues={"AAPL": "XNAS", "SPY": "ARCX"})

    assert {r.instrument_id for r in reports} == {"AAPL.XNAS", "SPY.ARCX"}
    assert all(r.bars_written == 1 for r in reports)


def test_an_unknown_venue_stops_the_write(catalog):
    """
    Guessing a venue would file the series under an instrument id that means nothing.
    """
    with pytest.raises(KeyError, match="no venue known"):
        write_ingestion(catalog, IngestionResult(bars=(daily(),), fetched=1), venues={})


def test_reading_a_window_bounds_the_result(catalog):
    source = [daily(day=(2025, 6, 4)), daily(day=(2025, 6, 5)), daily(day=(2025, 6, 6))]
    write_ingestion(
        catalog,
        IngestionResult(bars=tuple(source), fetched=3),
        venues={"AAPL": "XNAS"},
    )

    back = read_daily_bars(
        catalog,
        bar_type_for(equity_for("AAPL", "XNAS").id),
        start=datetime(2025, 6, 5, tzinfo=UTC),
        end=datetime(2025, 6, 5, tzinfo=UTC),
    )
    assert [b.closed_at.date().isoformat() for b in back] == ["2025-06-05"]


# ------------------------------------------------------------------------- venues


def test_venues_come_from_the_vendor_mic():
    rows = [
        {"symbol": "AAPL", "exchange": "XNAS"},
        {"symbol": "SPY", "exchange": "ARCX"},
        {"symbol": "aapl", "exchange": "XNAS"},
    ]
    assert venues_from_rows(rows) == {"AAPL": "XNAS", "SPY": "ARCX"}


def test_a_symbol_on_two_exchanges_raises():
    """
    A relisting re-bases prices the way a split does.

    Merging the two into one series would produce a continuous-looking history that
    never traded.

    """
    rows = [
        {"symbol": "FOO", "exchange": "XNAS"},
        {"symbol": "FOO", "exchange": "XNYS"},
    ]
    with pytest.raises(ValueError, match="more than one exchange"):
        venues_from_rows(rows)


def test_rows_without_an_exchange_are_ignored():
    assert venues_from_rows([{"symbol": "AAPL"}, {"exchange": "XNAS"}]) == {}


def test_bar_type_is_daily_and_externally_sourced():
    """
    ``EXTERNAL``, not ``INTERNAL``: these are the venue's official daily summaries, not
    bars the engine aggregated from ticks.
    """
    assert str(bar_type_for(equity_for("AAPL", "XNAS").id)) == "AAPL.XNAS-1-DAY-LAST-EXTERNAL"
