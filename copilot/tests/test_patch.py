"""
Tests for filling catalog holes from the Databento store.

The module's whole reason to exist is that two Databento schemas disagree about what a
day closed at, and only one of them is right. Most of what follows pins that: the close
comes from the auction statistic, never from the venue's daily bar, and a day where the
two cannot both be true is refused rather than reconciled.

The other risk is the rewrite. Filling a hole means rewriting a stored series, because
the catalog refuses a write that lands inside an interval it already holds - and a
rewrite that fails halfway is how a good series becomes a short one. The tests here
therefore care as much about what survives a failure as about what a success writes.

"""

from __future__ import annotations

from datetime import UTC
from datetime import date
from datetime import datetime
from decimal import Decimal

import pytest

from copilot.data.calendar import session_close
from copilot.data.patch import TS_UNSET
from copilot.data.patch import Fill
from copilot.data.patch import PatchResult
from copilot.data.patch import _bar_row
from copilot.data.patch import _official_row
from copilot.data.patch import report


DAY = date(2026, 6, 10)
SCALE = 10**9


def fill(**kwargs: object) -> Fill:
    """
    Build a fill, defaulting to a coherent bar whose auction beat the venue print.
    """
    base = {
        "symbol": "TLT",
        "day": DAY,
        "open": Decimal("85.10"),
        "high": Decimal("85.40"),
        "low": Decimal("84.90"),
        "close": Decimal("85.20"),
        "volume": 1_000,
        "venue_close": Decimal("85.15"),
    }
    return Fill(**{**base, **kwargs})  # type: ignore[arg-type]


class TestFill:
    """
    One filled hole: where its numbers come from and what it becomes.
    """

    def test_bar_closes_at_the_session_close(self) -> None:
        """
        The bar is stamped with the exchange's close, not the vendor's timestamp.
        """
        assert fill().to_bar().closed_at == session_close(DAY)

    def test_bar_carries_the_official_close(self) -> None:
        """
        The auction print reaches the catalog; the venue's own print does not.
        """
        bar = fill().to_bar()
        assert bar.close == Decimal("85.20")

    def test_close_gap_is_measured_against_the_venue_print(self) -> None:
        """
        The gap is what the venue's last trade missed the auction by.
        """
        gap = fill(close=Decimal("100.00"), venue_close=Decimal("100.10")).close_gap_bps
        assert gap == pytest.approx(Decimal("9.99"), abs=Decimal("0.01"))

    def test_close_gap_is_zero_when_the_venue_did_not_print(self) -> None:
        """
        A missing venue print reports no gap rather than dividing by zero.
        """
        assert fill(venue_close=Decimal(0)).close_gap_bps == 0

    def test_exact_agreement_reports_no_gap(self) -> None:
        """
        The common case: the venue's last trade was the auction.
        """
        assert fill(venue_close=Decimal("85.20")).close_gap_bps == 0


class TestOfficialRow:
    """
    Reading the closing auction print out of the statistics schema.
    """

    def row(self, **kwargs: str) -> dict[str, str]:
        base = {
            "stat_type": "11",
            "ts_ref": str(int(datetime(2026, 6, 10, 20, tzinfo=UTC).timestamp()) * SCALE),
            "ts_event": "0",
            "price": str(85 * SCALE),
            "instrument_id": "1",
        }
        return {**base, **kwargs}

    def test_reads_the_closing_price_statistic(self) -> None:
        """
        stat_type 11 is the official close and is the only one taken.
        """
        parsed = _official_row(self.row())
        assert parsed is not None
        assert parsed[1] == Decimal(85)

    def test_ignores_every_other_statistic(self) -> None:
        """
        The schema carries opening prices and settlement values too.
        """
        assert _official_row(self.row(stat_type="1")) is None

    def test_unset_reference_falls_back_to_the_event_time(self) -> None:
        """
        The sentinel parses as a valid integer, which is exactly why it must be caught.

        Read literally it lands in the year 2554, where it resolves no symbol at all and
        the row disappears without an error - a silent short series rather than a loud
        failure.

        """
        moment = datetime(2026, 6, 10, 20, tzinfo=UTC)
        parsed = _official_row(
            self.row(ts_ref=str(TS_UNSET), ts_event=str(int(moment.timestamp()) * SCALE)),
        )
        assert parsed is not None
        assert parsed[0].date() == DAY

    def test_prices_are_scaled_out_of_fixed_point(self) -> None:
        """
        Databento sends integers of 1e-9 dollars; a missed scale is a 1e9 error.
        """
        parsed = _official_row(self.row(price="85123456789"))
        assert parsed is not None
        assert parsed[1] == Decimal("85.123456789")


class TestBarRow:
    """
    Reading the venue's own daily bar.
    """

    def test_returns_ohlcv_in_order(self) -> None:
        """
        The tuple order is what plan() indexes into, so it is pinned here.
        """
        moment = datetime(2026, 6, 10, 20, tzinfo=UTC)
        _, values = _bar_row(
            {
                "ts_event": str(int(moment.timestamp()) * SCALE),
                "open": str(10 * SCALE),
                "high": str(12 * SCALE),
                "low": str(9 * SCALE),
                "close": str(11 * SCALE),
                "volume": "500",
                "instrument_id": "1",
            },
        )
        assert values == (
            Decimal(10),
            Decimal(12),
            Decimal(9),
            Decimal(11),
            Decimal(500),
        )


class TestPatchResult:
    """
    What the result reports as still open.
    """

    def result(self, **kwargs: object) -> PatchResult:
        base = {
            "symbol": "TLT",
            "venue": "XNAS",
            "held": 2303,
            "holes": (DAY,),
            "fills": (),
            "unsourced": (),
            "incoherent": (),
        }
        return PatchResult(**{**base, **kwargs})  # type: ignore[arg-type]

    def test_a_filled_hole_is_not_remaining(self) -> None:
        """
        A hole the store priced and the checks accepted is closed.
        """
        assert self.result(fills=(fill(),)).remaining == 0

    def test_an_unsourced_hole_remains(self) -> None:
        """
        Before 2018-05 the store has nothing, and that has to stay visible.
        """
        assert self.result(unsourced=(DAY,)).remaining == 1

    def test_a_refused_hole_remains(self) -> None:
        """
        A refusal is an open hole, not a handled one.

        The distinction matters because refusing is the safe branch, and a safe branch
        that reported success would make disagreement between two sources invisible.

        """
        assert self.result(incoherent=((DAY, "outside range"),)).remaining == 1


class TestReport:
    """
    The exit code an unattended run is judged by.
    """

    def test_a_complete_patch_exits_zero(self) -> None:
        """
        Every hole filled is the only clean result.
        """
        result = PatchResult("TLT", "XNAS", 2303, (DAY,), (fill(),), (), ())
        assert report([result], written=True) == 0

    def test_an_open_hole_exits_nonzero(self) -> None:
        """
        A series still holed must not read as patched.
        """
        result = PatchResult("TLT", "XNAS", 2303, (DAY,), (), (DAY,), ())
        assert report([result], written=True) == 1

    def test_nothing_to_do_exits_zero(self) -> None:
        """
        A series with no holes is a success, not an empty failure.
        """
        assert report([PatchResult("SPY", "ARCX", 5283, (), (), (), ())], written=False) == 0
