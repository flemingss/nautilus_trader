"""
Tests for the substituted bars.

A substitution is a bar the daily vendor could not price, taken whole from a second
source. The dangerous version of that is a substitution that becomes a *preference* -
quietly overriding a bar the vendor priced fine, because the second source disagreed - so
several tests below exist only to pin that the table cannot drift in that direction
without a diff someone has to justify.

The other failure is a repair that gets in around the checks rather than past the vendor.
A substituted bar goes through the same gate as any other, and the test that matters is
the one proving a row still has to be coherent, positive and penny-aligned after it is
replaced.

"""

from __future__ import annotations

from datetime import UTC
from datetime import date
from datetime import datetime
from decimal import Decimal

import pytest

from copilot.data.calendar import is_trading_day
from copilot.data.catalog import PRICE_PRECISION
from copilot.data.marketstack import normalize
from copilot.data.substitutions import COVERAGE_START
from copilot.data.substitutions import NULL_CLOSE
from copilot.data.substitutions import SHIFTED_ROW
from copilot.data.substitutions import SUBSTITUTIONS
from copilot.data.substitutions import ZERO_CLOSE
from copilot.data.substitutions import apply_to
from copilot.data.substitutions import substitution_for
from copilot.data.substitutions import unmatched


PENNY = Decimal("0.01")
RECEIVED_AT = datetime(2026, 9, 4, tzinfo=UTC)
KNOWN_REASONS = frozenset({NULL_CLOSE, ZERO_CLOSE, SHIFTED_ROW})


def vendor_row(symbol: str, day: date, close: object) -> dict:
    """
    Build a provider row in the shape Marketstack returns one.
    """
    return {
        "symbol": symbol,
        "date": f"{day.isoformat()}T00:00:00+0000",
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": close,
        "volume": 1_000_000.0,
        "split_factor": 1.0,
        "dividend": 0.0,
        "exchange": "XNAS",
    }


@pytest.mark.parametrize("entry", SUBSTITUTIONS, ids=lambda e: f"{e.symbol}-{e.day}")
def test_every_substituted_bar_is_coherent(entry) -> None:
    assert entry.low <= min(entry.open, entry.close)
    assert max(entry.open, entry.close) <= entry.high
    assert min(entry.open, entry.high, entry.low, entry.close) > 0
    assert entry.volume > 0


@pytest.mark.parametrize("entry", SUBSTITUTIONS, ids=lambda e: f"{e.symbol}-{e.day}")
def test_every_substituted_close_is_a_whole_cent(entry) -> None:
    # These are official auction prints; no auction prints a sub-penny close above a
    # dollar, and three of the vendor's own bad closes were caught by exactly this.
    assert entry.close % PENNY == 0


@pytest.mark.parametrize("entry", SUBSTITUTIONS, ids=lambda e: f"{e.symbol}-{e.day}")
def test_every_substituted_price_fits_the_stored_precision(entry) -> None:
    # The catalog stores four decimals and Price rounds silently past that, so a value
    # with a fifth would be written as something other than what this table says.
    for value in (entry.open, entry.high, entry.low, entry.close):
        assert -value.as_tuple().exponent <= PRICE_PRECISION


@pytest.mark.parametrize("entry", SUBSTITUTIONS, ids=lambda e: f"{e.symbol}-{e.day}")
def test_every_substituted_day_is_a_real_session(entry) -> None:
    assert is_trading_day(entry.day)


@pytest.mark.parametrize("entry", SUBSTITUTIONS, ids=lambda e: f"{e.symbol}-{e.day}")
def test_no_substitution_predates_the_source(entry) -> None:
    # EQUS.SUMMARY begins 2024-07-01; an older entry could not have come from where the
    # table says it came from.
    assert entry.day >= COVERAGE_START


@pytest.mark.parametrize("entry", SUBSTITUTIONS, ids=lambda e: f"{e.symbol}-{e.day}")
def test_every_substitution_names_a_defect_not_a_disagreement(entry) -> None:
    # The line this table must not cross. Every reason is a shape the vendor's row cannot
    # be read at all - null, zero, shifted - never "the other source said something else".
    # Adding a fourth reason is a diff, which is the point.
    assert entry.reason in KNOWN_REASONS


def test_the_table_holds_no_duplicate_sessions() -> None:
    keys = [(s.symbol, s.day) for s in SUBSTITUTIONS]
    assert len(keys) == len(set(keys))


def test_the_table_is_ordered_so_an_addition_reads_as_one() -> None:
    keys = [(s.symbol, s.day) for s in SUBSTITUTIONS]
    assert keys == sorted(keys)


def test_lookup_is_case_insensitive() -> None:
    entry = SUBSTITUTIONS[0]
    assert substitution_for(entry.symbol.lower(), entry.day) == entry


def test_lookup_returns_none_for_an_untouched_session() -> None:
    assert substitution_for("AAPL", date(2026, 6, 11)) is None


def test_apply_replaces_only_the_price_fields() -> None:
    entry = SUBSTITUTIONS[0]
    row = vendor_row(entry.symbol, entry.day, close=None)
    row["split_factor"] = 4.0
    row["dividend"] = 0.25
    (out,), applied = apply_to([row])

    assert applied == (entry,)
    assert Decimal(out["close"]) == entry.close
    assert Decimal(out["open"]) == entry.open
    # Databento sells no corporate actions, so these must stay with the vendor that does.
    assert out["split_factor"] == 4.0
    assert out["dividend"] == 0.25


def test_apply_does_not_mutate_the_callers_rows() -> None:
    # The rows are the vendor's answer. A run reporting "11 substituted" while having
    # rewritten its own input would be describing something that no longer exists.
    entry = SUBSTITUTIONS[0]
    row = vendor_row(entry.symbol, entry.day, close=None)
    apply_to([row])
    assert row["close"] is None


def test_apply_leaves_an_untouched_session_alone() -> None:
    row = vendor_row("AAPL", date(2026, 6, 11), close=123.45)
    (out,), applied = apply_to([row])
    assert applied == ()
    assert out["close"] == 123.45


def test_a_row_the_vendor_could_not_price_is_rejected_without_the_table() -> None:
    entry = SUBSTITUTIONS[0]
    result = normalize([vendor_row(entry.symbol, entry.day, close=None)], received_at=RECEIVED_AT)
    assert result.bars == ()
    assert len(result.rejected) == 1


def test_the_same_row_passes_the_gate_once_substituted() -> None:
    # The whole point, and the shape it has to keep: substituted rows go through the
    # gate, they do not go around it.
    entry = SUBSTITUTIONS[0]
    rows, applied = apply_to([vendor_row(entry.symbol, entry.day, close=None)])
    result = normalize(rows, received_at=RECEIVED_AT)
    assert applied == (entry,)
    assert len(result.bars) == 1
    assert result.bars[0].close == entry.close


def test_a_substituted_row_that_is_still_incoherent_is_still_rejected(monkeypatch) -> None:
    # If the table were ever wrong, the gate has to be the thing that catches it. A
    # repair that bypassed the checks would be a much worse defect than the one it fixes.
    entry = SUBSTITUTIONS[0]
    row = vendor_row(entry.symbol, entry.day, close=None)
    rows, _ = apply_to([row])
    rows[0]["high"] = str(entry.low - Decimal(1))
    result = normalize(rows, received_at=RECEIVED_AT)
    assert result.bars == ()
    assert "incoherent" in result.rejected[0].reason


def test_unmatched_reports_entries_no_vendor_row_carried() -> None:
    # A table entry that never matches leaves the session missing and rejects nothing, so
    # the ingestion report would otherwise be silent about it.
    entry = SUBSTITUTIONS[0]
    _, applied = apply_to([vendor_row(entry.symbol, entry.day, close=None)])
    missed = unmatched(applied)
    assert entry not in missed
    assert len(missed) == len(SUBSTITUTIONS) - 1


def test_unmatched_is_empty_when_every_entry_applied() -> None:
    rows = [vendor_row(s.symbol, s.day, close=None) for s in SUBSTITUTIONS]
    _, applied = apply_to(rows)
    assert unmatched(applied) == ()
