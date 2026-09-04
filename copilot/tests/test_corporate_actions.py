"""
Tests for corporate action classification and back-adjustment.

Two failures are being guarded against, and they are opposite in kind. Leaving a split
in the series puts a -95% day in front of a strategy that trades gaps. Treating a
spinoff as a split divides a share count that never changed, which understates
commission on every early trade.

The factors used here are the real ones measured from the catalog on 2026-09-03, so a
change in the classifier's behaviour shows up against the data it was written for.

"""

from __future__ import annotations

from datetime import UTC
from datetime import date
from datetime import datetime
from decimal import Decimal

import pytest

from copilot.data.corporate_actions import DISTRIBUTION
from copilot.data.corporate_actions import SPLIT
from copilot.data.corporate_actions import Action
from copilot.data.corporate_actions import CorporateActionError
from copilot.data.corporate_actions import back_adjust
from copilot.data.corporate_actions import classify
from copilot.data.corporate_actions import cumulative_factor
from copilot.data.corporate_actions import unadjusted_actions


def when(year: int, month: int, day: int) -> datetime:
    """
    Build a UTC instant.
    """
    return datetime(year, month, day, tzinfo=UTC)


AAPL_4_FOR_1 = Action("AAPL", when(2020, 8, 31), Decimal(4))
GOOGL_20_FOR_1 = Action("GOOGL", when(2022, 7, 18), Decimal(20))
GOOGL_2_FOR_1 = Action("GOOGL", when(2014, 4, 3), Decimal(2))
MRK_ORGANON = Action("MRK", when(2021, 6, 3), Decimal("1.05"))
T_WBD = Action("T", when(2022, 4, 11), Decimal("1.32"))


@pytest.mark.parametrize("factor", ["2", "3", "4", "7", "20", "1.5"])
def test_a_ratio_of_small_integers_is_a_split(factor: str) -> None:
    assert classify(Decimal(factor)) is SPLIT


@pytest.mark.parametrize("factor", ["1.05", "1.32", "1.07", "1.005"])
def test_the_real_spinoff_factors_are_distributions(factor: str) -> None:
    """
    These are the measured factors for MRK, T and VZ.

    Classifying any of them as a split would divide a share count that never changed.

    """
    assert classify(Decimal(factor)) is DISTRIBUTION


def test_an_action_knows_its_own_kind() -> None:
    assert AAPL_4_FOR_1.kind is SPLIT
    assert MRK_ORGANON.kind is DISTRIBUTION


def test_a_factor_of_zero_or_less_is_refused() -> None:
    with pytest.raises(CorporateActionError, match="positive"):
        Action("AAPL", when(2020, 8, 31), Decimal(0))


def test_the_cumulative_factor_counts_only_later_actions() -> None:
    actions = [GOOGL_2_FOR_1, GOOGL_20_FOR_1]

    assert cumulative_factor(actions, when(2013, 1, 1)) == Decimal(40)
    assert cumulative_factor(actions, when(2015, 1, 1)) == Decimal(20)
    assert cumulative_factor(actions, when(2023, 1, 1)) == Decimal(1)


def test_a_price_on_the_effective_day_is_not_adjusted_by_its_own_action() -> None:
    """
    The action is effective at that day's open, so its close is already in new terms.
    """
    assert cumulative_factor([AAPL_4_FOR_1], when(2020, 8, 31)) == Decimal(1)
    assert cumulative_factor([AAPL_4_FOR_1], when(2020, 8, 28)) == Decimal(4)


def test_back_adjustment_removes_the_discontinuity() -> None:
    """
    GOOGL's real closes across its 20:1.

    Before: a -95% day. After: a normal one.

    """
    closes = [
        (when(2022, 7, 15), Decimal("2235.55")),
        (when(2022, 7, 18), Decimal("109.03")),
    ]

    adjusted = back_adjust(closes, [GOOGL_20_FOR_1])

    assert adjusted[0][1] == Decimal("111.7775")
    assert adjusted[1][1] == Decimal("109.03")
    move = (adjusted[1][1] - adjusted[0][1]) / adjusted[0][1] * 100
    assert -5 < move < 0


def test_back_adjustment_leaves_the_most_recent_price_alone() -> None:
    """
    The series is anchored to what a share costs now, because that is the number every
    cost and sizing decision downstream is expressed in.
    """
    closes = [(when(2013, 1, 2), Decimal(700)), (when(2025, 1, 2), Decimal(190))]

    adjusted = back_adjust(closes, [GOOGL_2_FOR_1, GOOGL_20_FOR_1])

    assert adjusted[-1][1] == Decimal(190)
    assert adjusted[0][1] == Decimal("17.5")


def test_a_series_with_no_actions_is_returned_unchanged() -> None:
    closes = [(when(2020, 1, 2), Decimal("100.25"))]

    assert back_adjust(closes, []) == closes


def test_an_unadjusted_split_is_detected() -> None:
    closes = [
        (when(2022, 7, 15), Decimal("2235.55")),
        (when(2022, 7, 18), Decimal("109.03")),
    ]

    assert unadjusted_actions(closes, [GOOGL_20_FOR_1]) == [GOOGL_20_FOR_1]


def test_an_already_adjusted_split_is_not_flagged() -> None:
    """
    AAPL's real closes across its 4:1: the vendor had already adjusted these.
    """
    closes = [
        (when(2020, 8, 28), Decimal("124.8075")),
        (when(2020, 8, 31), Decimal("129.04")),
    ]

    assert unadjusted_actions(closes, [AAPL_4_FOR_1]) == []


def test_an_action_outside_the_series_is_skipped_not_guessed() -> None:
    closes = [(when(2023, 1, 3), Decimal(90)), (when(2023, 1, 4), Decimal(91))]

    assert unadjusted_actions(closes, [GOOGL_20_FOR_1]) == []


def test_detection_finds_the_distribution_a_threshold_scan_misses() -> None:
    """
    T's spinoff moved the price -18.7%, which is an ordinary market move.

    No scan for large drops finds this; only the action list does.

    """
    closes = [
        (when(2022, 4, 8), Decimal("24.14")),
        (when(2022, 4, 11), Decimal("19.63")),
    ]

    assert unadjusted_actions(closes, [T_WBD]) == [T_WBD]


class TestScan:
    """
    The scan as the onboarding command sees it: findings, not a printed table.
    """

    def test_a_split_sitting_in_the_prices_blocks(self, tmp_path) -> None:
        """
        SCHX's shape on 2026-09-04: the vendor reports a 3:1, the stored series still
        carries the jump, and nothing in ACTIONS knows.
        """
        from copilot.data.corporate_actions import SITTING_IN_PRICES

        class Vendor:
            def fetch_splits(self, symbols, start, end):
                return {"ZZZ": ((date(2024, 10, 11), Decimal(3)),)}

        class Catalog:
            pass

        import copilot.data.corporate_actions as module

        closes = [
            (datetime(2024, 10, 10, tzinfo=UTC), Decimal("68.17")),
            (datetime(2024, 10, 11, tzinfo=UTC), Decimal("22.88")),
        ]
        found = module.unadjusted_actions(
            closes,
            [module.Action("ZZZ", datetime(2024, 10, 11, tzinfo=UTC), Decimal(3))],
        )
        assert len(found) == 1
        # The finding type carries the decision the stage needs.
        finding = module.Finding("ZZZ", found[0], SITTING_IN_PRICES)
        assert finding.blocks

    def test_an_already_adjusted_action_does_not_block(self) -> None:
        from copilot.data.corporate_actions import ALREADY_ADJUSTED
        from copilot.data.corporate_actions import Action
        from copilot.data.corporate_actions import Finding

        finding = Finding(
            "ZZZ",
            Action("ZZZ", datetime(2005, 6, 9, tzinfo=UTC), Decimal(3)),
            ALREADY_ADJUSTED,
        )
        assert not finding.blocks

    def test_an_untestable_action_blocks(self) -> None:
        """
        No stored bars to check against is not a pass; it is a symbol the gate would
        meet unprepared.
        """
        from copilot.data.corporate_actions import UNTESTABLE
        from copilot.data.corporate_actions import Action
        from copilot.data.corporate_actions import Finding

        assert Finding(
            "ZZZ",
            Action("ZZZ", datetime(2024, 1, 2, tzinfo=UTC), Decimal(2)),
            UNTESTABLE,
        ).blocks

    def test_the_three_found_by_the_walk_are_registered(self) -> None:
        from copilot.data.corporate_actions import ACTIONS

        assert {(a.effective.date(), a.factor) for a in ACTIONS["SCHX"]} == {
            (date(2022, 3, 11), Decimal(2)),
            (date(2024, 10, 11), Decimal(3)),
        }
        assert [(a.effective.date(), a.factor) for a in ACTIONS["GLDM"]] == [
            (date(2022, 2, 23), Decimal("0.5")),
        ]


def test_the_scan_window_ends_today_unless_told_otherwise() -> None:
    # The default ended 2025-12-31 until 2026-09-05; a hand run would have missed every
    # split of the current year.
    from datetime import date

    from copilot.data.corporate_actions import window_end

    assert window_end(None, today=date(2026, 9, 5)) == date(2026, 9, 5)
    assert window_end("2025-12-31", today=date(2026, 9, 5)) == date(2025, 12, 31)
