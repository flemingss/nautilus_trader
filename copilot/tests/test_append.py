"""
Tests for the daily catalog append.

Three failures matter here and none of them is an exception in normal use.

An append that re-writes a session already stored **crashes** - the catalog raises on
non-disjoint intervals rather than de-duplicating - so a scheduled run that got its window
wrong would fail every day after the first. An append that stops on a vendor rejection
falls further behind on each run, which is what Marketstack's 2026 rows would have caused
daily. And an append that treats a session the vendor has not published yet as a hole
raises an alarm in the middle of the operator's night for nothing.

So the tests below are mostly about the boundary between those states, and the fake client
exists to put a session on either side of it on demand.

"""

from __future__ import annotations

from datetime import UTC
from datetime import date
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

import pytest

from copilot.data.append import PUBLICATION_GRACE_HOURS
from copilot.data.append import NotBackfilledError
from copilot.data.append import append
from copilot.data.append import append_symbol
from copilot.data.append import due_sessions
from copilot.data.append import last_stored_session
from copilot.data.append import report
from copilot.data.calendar import session_close
from copilot.data.catalog import bar_type_for
from copilot.data.catalog import equity_for
from copilot.data.catalog import open_catalog
from copilot.data.catalog import read_daily_bars
from copilot.data.catalog import write_ingestion
from copilot.data.marketstack import IngestionResult
from copilot.data.substitutions import SUBSTITUTIONS
from copilot.validation.types import DailyBar


SYMBOL, VENUE = "AAPL", "XNAS"
STORED_THROUGH = date(2026, 2, 20)
GRACE = timedelta(hours=PUBLICATION_GRACE_HOURS)


def settled(day: date) -> datetime:
    """
    Return an instant at which ``day``'s data is unambiguously due.
    """
    return session_close(day) + GRACE + timedelta(minutes=1)


def daily(day: date, close: str) -> DailyBar:
    """
    Build one coherent bar for a session.
    """
    value = Decimal(close)
    return DailyBar(
        symbol=SYMBOL,
        closed_at=datetime(day.year, day.month, day.day, tzinfo=UTC),
        open=value,
        high=value + Decimal(2),
        low=value - Decimal(2),
        close=value,
        volume=1_000_000,
    )


def row(day: date, close: object, symbol: str = SYMBOL) -> dict:
    """
    Build a provider row in the shape Marketstack returns one.

    The range is derived from the close rather than fixed, because the gate checks
    coherence and a helper with a constant high would reject every row the moment a test
    picked a different price - which it did, the first time this was written.

    """
    anchor = float(close) if isinstance(close, (int, float)) else 200.0
    return {
        "symbol": symbol,
        "date": f"{day.isoformat()}T00:00:00+0000",
        "open": anchor,
        "high": anchor + 2.0,
        "low": anchor - 2.0,
        "close": close,
        "volume": 1_000_000.0,
        "split_factor": 1.0,
        "dividend": 0.0,
        "exchange": VENUE,
    }


class FakeClient:
    """
    A Marketstack stand-in that returns exactly the rows it was given.
    """

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.windows: list[tuple[date, date]] = []

    def fetch_eod(self, symbols, start, end):
        self.windows.append((start, end))
        return [r for r in self.rows if start <= date.fromisoformat(r["date"][:10]) <= end]


def catalog_through(tmp_path, day: date = STORED_THROUGH) -> str:
    """
    Write a small catalog ending on ``day`` and return its path.
    """
    from copilot.data.calendar import trading_days

    days = trading_days(day - timedelta(days=20), day)
    bars = tuple(daily(d, str(200 + i)) for i, d in enumerate(days))
    store = tmp_path / "store"
    write_ingestion(
        open_catalog(store),
        IngestionResult(bars=bars, fetched=len(bars)),
        venues={SYMBOL: VENUE},
    )
    return str(store)


def stored_days(catalog_path: str) -> list[date]:
    """
    Return every session the catalog now holds.
    """
    instrument = equity_for(SYMBOL, VENUE)
    bars = read_daily_bars(open_catalog(catalog_path), bar_type_for(instrument.id))
    return [b.closed_at.date() for b in bars]


def test_due_sessions_excludes_the_session_already_stored() -> None:
    # Asking for it again is exactly what the catalog refuses.
    due, pending = due_sessions(STORED_THROUGH, settled(date(2026, 2, 25)))
    assert STORED_THROUGH not in due + pending


def test_a_session_past_its_grace_is_due() -> None:
    day = date(2026, 2, 23)
    due, _ = due_sessions(STORED_THROUGH, settled(day))
    assert day in due


def test_a_session_inside_its_grace_is_pending_not_due() -> None:
    # An alarm one hour after the close would fire before the vendor has published.
    day = date(2026, 2, 23)
    moment = session_close(day) + timedelta(hours=1)
    due, pending = due_sessions(STORED_THROUGH, moment)
    assert day in pending
    assert day not in due


def test_the_grace_is_measured_from_the_real_close_not_from_midnight() -> None:
    # February closes at 21:00 UTC and July at 20:00; a constant offset is wrong for a
    # third of the year, and this is the boundary that would hide it.
    day = date(2026, 2, 23)
    just_before = session_close(day) + GRACE - timedelta(minutes=1)
    _, pending = due_sessions(STORED_THROUGH, just_before)
    assert day in pending
    assert due_sessions(STORED_THROUGH, just_before + timedelta(minutes=2))[0][-1] == day


def test_last_stored_session_reads_the_newest_bar(tmp_path) -> None:
    assert last_stored_session(catalog_through(tmp_path), SYMBOL, VENUE) == STORED_THROUGH


def test_an_empty_catalog_is_a_backfill_not_an_append(tmp_path) -> None:
    # Quietly turning into a backfill would pull twenty years on a schedule.
    (tmp_path / "empty").mkdir()
    with pytest.raises(NotBackfilledError, match=r"copilot\.data\.backfill"):
        last_stored_session(str(tmp_path / "empty"), SYMBOL, VENUE)


def test_append_writes_the_missing_sessions(tmp_path) -> None:
    catalog = catalog_through(tmp_path)
    days = [date(2026, 2, 23), date(2026, 2, 24)]
    client = FakeClient([row(d, 210.0 + i) for i, d in enumerate(days)])
    result = append_symbol(catalog, SYMBOL, VENUE, client, as_of=settled(days[-1]))

    assert result.written == tuple(days)
    assert result.current
    assert stored_days(catalog)[-1] == days[-1]


def test_append_never_rewrites_a_stored_session(tmp_path) -> None:
    # The catalog raises on non-disjoint intervals, so a window that reaches back into
    # stored history does not merely waste work - it fails the run.
    catalog = catalog_through(tmp_path)
    days = [STORED_THROUGH, date(2026, 2, 23)]
    client = FakeClient([row(d, 210.0) for d in days])
    result = append_symbol(catalog, SYMBOL, VENUE, client, as_of=settled(days[-1]))

    assert result.written == (date(2026, 2, 23),)
    assert stored_days(catalog).count(STORED_THROUGH) == 1


def test_a_second_run_with_nothing_new_is_a_no_op(tmp_path) -> None:
    # The only idempotence that matters on a clock: running twice must not raise.
    catalog = catalog_through(tmp_path)
    day = date(2026, 2, 23)
    client = FakeClient([row(day, 210.0)])
    as_of = settled(day)

    first = append_symbol(catalog, SYMBOL, VENUE, client, as_of=as_of)
    second = append_symbol(catalog, SYMBOL, VENUE, client, as_of=as_of)

    assert first.written == (day,)
    assert second.written == ()
    assert second.current


def test_nothing_to_do_writes_nothing_and_is_current(tmp_path) -> None:
    catalog = catalog_through(tmp_path)
    client = FakeClient([])
    result = append_symbol(
        catalog,
        SYMBOL,
        VENUE,
        client,
        as_of=session_close(STORED_THROUGH) + timedelta(hours=1),
    )
    assert result.written == ()
    assert result.current
    assert client.windows == []


def test_a_rejected_session_does_not_stop_the_good_ones(tmp_path) -> None:
    # The failure Marketstack's 2026 rows would have caused daily: refusing everything
    # because one session is unreadable leaves the catalog further behind each run.
    catalog = catalog_through(tmp_path)
    bad, good = date(2026, 2, 23), date(2026, 2, 24)
    client = FakeClient([row(bad, None), row(good, 211.0)])
    result = append_symbol(catalog, SYMBOL, VENUE, client, as_of=settled(good))

    assert result.written == (good,)
    assert result.missing == (bad,)
    assert not result.current
    assert any("close" in reason for _, reason in result.rejected)


def test_a_session_the_vendor_omits_entirely_is_reported_missing(tmp_path) -> None:
    catalog = catalog_through(tmp_path)
    bad, good = date(2026, 2, 23), date(2026, 2, 24)
    client = FakeClient([row(good, 211.0)])
    result = append_symbol(catalog, SYMBOL, VENUE, client, as_of=settled(good))
    assert result.missing == (bad,)


def test_an_unpublished_session_is_pending_not_missing(tmp_path) -> None:
    catalog = catalog_through(tmp_path)
    day = date(2026, 2, 23)
    client = FakeClient([])
    result = append_symbol(
        catalog,
        SYMBOL,
        VENUE,
        client,
        as_of=session_close(day) + timedelta(hours=1),
    )
    assert result.pending == (day,)
    assert result.missing == ()
    assert result.current


def test_a_substituted_session_is_applied_and_reported(tmp_path) -> None:
    entry = next(s for s in SUBSTITUTIONS if s.symbol == "AAPL")
    catalog = catalog_through(tmp_path, entry.day - timedelta(days=7))
    client = FakeClient([row(entry.day, None)])
    result = append_symbol(catalog, SYMBOL, VENUE, client, as_of=settled(entry.day))

    assert entry in result.substituted
    assert entry.day in result.written


def test_one_symbols_failure_does_not_stop_the_next(tmp_path) -> None:
    catalog = catalog_through(tmp_path)
    day = date(2026, 2, 23)
    client = FakeClient([row(day, None)])
    results = append(catalog, [(SYMBOL, VENUE), (SYMBOL, VENUE)], client, as_of=settled(day))
    assert len(results) == 2


def test_report_exits_non_zero_on_a_hole(tmp_path, capsys) -> None:
    catalog = catalog_through(tmp_path)
    day = date(2026, 2, 23)
    client = FakeClient([row(day, None)])
    as_of = settled(day)
    code = report(append(catalog, [(SYMBOL, VENUE)], client, as_of=as_of), as_of=as_of)
    assert code == 1
    assert "HOLE" in capsys.readouterr().out


def test_report_exits_zero_when_only_pending(tmp_path, capsys) -> None:
    # An operator woken at 3am should have been woken for something real.
    catalog = catalog_through(tmp_path)
    day = date(2026, 2, 23)
    as_of = session_close(day) + timedelta(hours=1)
    code = report(append(catalog, [(SYMBOL, VENUE)], FakeClient([]), as_of=as_of), as_of=as_of)
    assert code == 0
    out = capsys.readouterr().out
    assert "pending" in out
    assert "HOLE" not in out
