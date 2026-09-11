"""
Tests for the EODHD probe.

Each check exists because Marketstack failed it, so each is pinned against the shape of the
failure rather than against a clean series alone. The AAPL rows are EODHD's own, measured
2026-09-11 on the demo token: a close of 63.2912 on 2005-01-03 is 1.1302 times 56, an
adjusted series multiplied back up, and it is the reason the whole-cent check exists here too.

"""

from __future__ import annotations

import io
import json
from datetime import date
from decimal import Decimal

from copilot.data.eodhd_probe import EodhdClient
from copilot.data.eodhd_probe import SplitMove
from copilot.data.eodhd_probe import agreement
from copilot.data.eodhd_probe import compare_splits
from copilot.data.eodhd_probe import incoherent_days
from copilot.data.eodhd_probe import missing_days
from copilot.data.eodhd_probe import parse_bars
from copilot.data.eodhd_probe import parse_vendor_splits
from copilot.data.eodhd_probe import phantom_days
from copilot.data.eodhd_probe import probe
from copilot.data.eodhd_probe import split_moves
from copilot.data.eodhd_probe import sub_penny_closes


def row(day: str, close: float, **kwargs: float) -> dict[str, object]:
    """
    Build one vendor row, a coherent bar around ``close`` unless told otherwise.
    """
    base = {"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1000}
    return {"date": day, **base, **kwargs}


def week(start_close: float = 100.0) -> list[dict[str, object]]:
    """
    Five real sessions, 2024-01-08 to 2024-01-12.
    """
    return [row(f"2024-01-{d:02d}", start_close + i) for i, d in enumerate(range(8, 13))]


class TestParse:
    def test_json_floats_keep_their_digits(self) -> None:
        """
        A binary float printed back must not grow a tail of digits the vendor never
        sent.
        """
        (bar,) = parse_bars([row("2005-01-03", 63.2912)])
        assert bar.close == Decimal("63.2912")

    def test_bars_come_back_in_date_order(self) -> None:
        bars = parse_bars([row("2024-01-09", 101.0), row("2024-01-08", 100.0)])
        assert [b.day for b in bars] == [date(2024, 1, 8), date(2024, 1, 9)]

    def test_a_vendor_split_is_a_factor(self) -> None:
        splits = parse_vendor_splits([{"date": "2014-06-09", "split": "7.000000/1.000000"}])
        assert splits == ((date(2014, 6, 9), Decimal(7)),)


class TestCoherentBars:
    def test_a_close_above_its_own_high_is_incoherent(self) -> None:
        bars = parse_bars([row("2024-01-08", 100.0, high=99.5)])
        assert incoherent_days(bars) == (date(2024, 1, 8),)

    def test_a_coherent_week_has_none(self) -> None:
        assert incoherent_days(parse_bars(week())) == ()


class TestRealSessions:
    def test_a_holiday_bar_is_phantom(self) -> None:
        """
        Marketstack's shape: a plausible bar on a day the market was shut.
        """
        bars = parse_bars([*week(), row("2024-01-15", 105.0)])
        assert phantom_days(bars) == (date(2024, 1, 15),)

    def test_a_session_left_out_is_missing(self) -> None:
        rows = [r for r in week() if r["date"] != "2024-01-10"]
        assert missing_days(parse_bars(rows)) == (date(2024, 1, 10),)


class TestWholeCentCloses:
    def test_a_rebuilt_close_is_not_a_whole_cent(self) -> None:
        bars = parse_bars([row("2005-01-03", 63.2912), row("2005-01-04", 63.94)])
        assert sub_penny_closes(bars) == (date(2005, 1, 3),)

    def test_a_sub_dollar_security_quotes_below_a_cent(self) -> None:
        assert sub_penny_closes(parse_bars([row("2024-01-08", 0.5123)])) == ()


class TestSplits:
    def test_a_split_shows_as_a_jump_on_its_day(self) -> None:
        """
        AAPL's 7-for-1: 645.57 at the close, 92.70 at the open.
        """
        bars = parse_bars([row("2014-06-06", 645.57), row("2014-06-09", 92.70)])
        (move,) = split_moves(bars)
        assert move.day == date(2014, 6, 9)
        assert move.ratio == Decimal("6.964")

    def test_a_registered_split_seen_on_its_day_passes(self) -> None:
        findings = compare_splits(
            (SplitMove(date(2014, 6, 9), Decimal("6.964")),),
            registered=[(date(2014, 6, 9), Decimal(7))],
            vendor=[(date(2000, 6, 21), Decimal(2)), (date(2014, 6, 9), Decimal(7))],
            first=date(2005, 1, 3),
            last=date(2026, 9, 11),
        )
        assert findings.passed

    def test_a_jump_no_split_explains_fails(self) -> None:
        findings = compare_splits(
            (SplitMove(date(2012, 8, 13), Decimal("2.004")),),
            registered=[],
            vendor=[],
            first=date(2005, 1, 3),
            last=date(2026, 9, 11),
        )
        assert findings.unexplained[0].day == date(2012, 8, 13)
        assert not findings.passed

    def test_a_registered_split_the_series_never_shows_fails(self) -> None:
        """
        A series already adjusted for the split has no jump, and is not as-traded.
        """
        findings = compare_splits(
            (),
            registered=[(date(2020, 8, 31), Decimal(4))],
            vendor=[(date(2020, 8, 31), Decimal(4))],
            first=date(2005, 1, 3),
            last=date(2026, 9, 11),
        )
        assert findings.unseen == (date(2020, 8, 31),)

    def test_the_vendor_and_the_registry_must_list_the_same_dates(self) -> None:
        findings = compare_splits(
            (SplitMove(date(2020, 8, 31), Decimal("3.913")),),
            registered=[(date(2020, 8, 31), Decimal(4))],
            vendor=[(date(2020, 8, 31), Decimal(4)), (date(2022, 6, 6), Decimal(20))],
            first=date(2005, 1, 3),
            last=date(2026, 9, 11),
        )
        assert findings.vendor_only == (date(2022, 6, 6),)


class TestAgreement:
    def test_counts_exact_and_material_closes(self) -> None:
        bars = parse_bars([row("2020-07-08", 381.37), row("2020-07-09", 383.01)])
        official = {date(2020, 7, 8): Decimal("381.37"), date(2020, 7, 9): Decimal("380.00")}

        measured = agreement(bars, official)

        assert measured.compared == 2
        assert measured.exact == 1
        assert [m[0] for m in measured.material] == [date(2020, 7, 9)]
        assert not measured.passed

    def test_nothing_measured_is_not_a_pass(self) -> None:
        """
        A vendor cannot pass on closes the store holds no official print for.
        """
        assert not agreement(parse_bars(week()), {}).passed


class TestProbe:
    def test_a_clean_series_passes_every_check(self) -> None:
        rows = week()
        official = {date.fromisoformat(str(r["date"])): Decimal(str(r["close"])) for r in rows}

        result = probe("SPY", "ARCX", rows=rows, vendor_splits=[], official=official, demo=False)

        assert result.passed, result.failures
        assert result.as_record()["passed"] is True

    def test_the_failures_are_named(self) -> None:
        rows = [*week(), row("2024-01-15", 105.2912)]

        result = probe("SPY", "ARCX", rows=rows, vendor_splits=[], official={}, demo=True)

        assert result.failures == ("real sessions", "whole-cent closes", "official close")
        record = result.as_record()
        assert record["token"] == "demo"
        assert record["real_sessions"]["phantom"] == ["2024-01-15"]


class TestClient:
    def test_reads_json_and_puts_the_token_in_the_query_only(self) -> None:
        seen = {}

        def opener(request: object, timeout: int) -> io.BytesIO:
            seen["url"] = request.full_url  # type: ignore[attr-defined]
            seen["timeout"] = timeout
            return io.BytesIO(json.dumps([row("2024-01-08", 100.0)]).encode())

        rows = EodhdClient("secret", opener=opener).eod("AAPL.US", date(2005, 1, 1))

        assert rows[0]["date"] == "2024-01-08"
        assert seen["url"].startswith("https://eodhd.com/api/eod/AAPL.US?")
        assert "api_token=secret" in seen["url"]
