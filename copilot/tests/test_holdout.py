"""
Tests for the locked holdout carve.

The dangerous failures are quiet ones: held-out bars leaking into development because
the catalog changed under a boundary, or a carve that returns everything when the
history does not straddle the pin. The pin itself is asserted so that moving it is a
diff someone must justify against ADR-0012.

The window's far end (ADR-0017) is tested for the property it was added to guarantee:
appending sessions to the catalog - which is now routine, because the live path warms
from the same file - must move no share, no split and no bar. A test that only checked
the clip happened would miss the failure that matters, which is a verdict quietly
computed over a longer history than the one it names.

"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

import pytest

from copilot.validation.holdout import EVALUATION_END
from copilot.validation.holdout import HOLDOUT_START
from copilot.validation.holdout import MAX_HOLDOUT_SHARE
from copilot.validation.holdout import MIN_HOLDOUT_SHARE
from copilot.validation.holdout import HoldoutCarveError
from copilot.validation.holdout import carve
from copilot.validation.types import DailyBar


def bar(closed_at: datetime) -> DailyBar:
    """
    Build one bar; only its close instant matters to the carve.
    """
    price = Decimal(100)
    return DailyBar(
        symbol="TEST",
        closed_at=closed_at,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=1_000,
    )


def history(development: int, holdout: int) -> list[DailyBar]:
    """
    Build a history straddling the real pin with the given bar counts per side.
    """
    before = [bar(HOLDOUT_START - timedelta(days=i + 1)) for i in range(development)]
    after = [bar(HOLDOUT_START + timedelta(days=i)) for i in range(holdout)]
    return before + after


def beyond(count: int) -> list[DailyBar]:
    """
    Build bars past the window's far end, as a fresh backfill would leave behind.
    """
    return [bar(EVALUATION_END + timedelta(days=i)) for i in range(count)]


def test_the_pin_is_a_deliberate_diff() -> None:
    # Moving the boundary re-decides ADR-0012; this failing is the justification prompt.
    assert datetime(2022, 1, 1, tzinfo=UTC) == HOLDOUT_START


def test_the_window_end_is_a_deliberate_diff() -> None:
    # Moving it forward enlarges the single-use holdout; ADR-0017 says growth must not.
    assert datetime(2026, 1, 1, tzinfo=UTC) == EVALUATION_END


def test_the_window_ends_after_the_pin() -> None:
    # A window closing before the boundary would make every carve refuse to straddle.
    assert HOLDOUT_START < EVALUATION_END


def test_carve_splits_exactly_on_the_pin() -> None:
    carved = carve(history(100, 20))
    assert len(carved.development) == 100
    assert len(carved.holdout) == 20
    assert all(b.closed_at < HOLDOUT_START for b in carved.development)
    assert all(b.closed_at >= HOLDOUT_START for b in carved.holdout)


def test_a_bar_closing_at_the_pin_instant_is_holdout() -> None:
    carved = carve(history(100, 20))
    assert min(b.closed_at for b in carved.holdout) == HOLDOUT_START


def test_input_order_does_not_matter() -> None:
    # Reversed input: the carve sorts on closed_at rather than trusting the caller.
    carved = carve(history(100, 20)[::-1])
    assert len(carved.development) == 100
    assert carved.development == tuple(sorted(carved.development, key=lambda b: b.closed_at))


def test_a_history_entirely_before_the_pin_refuses() -> None:
    with pytest.raises(HoldoutCarveError, match="does not straddle"):
        carve(history(100, 0))


def test_a_history_entirely_after_the_pin_refuses() -> None:
    with pytest.raises(HoldoutCarveError, match="does not straddle"):
        carve(history(0, 20))


def test_a_grown_catalog_pushes_the_share_high_and_refuses() -> None:
    # New bars land on the holdout side of a date pin, so growth raises the share.
    with pytest.raises(HoldoutCarveError, match="re-decide the pin"):
        carve(history(100, 40))


def test_a_share_below_the_band_refuses() -> None:
    with pytest.raises(HoldoutCarveError, match="re-decide the pin"):
        carve(history(100, 10))


def test_the_band_edges_are_inside_the_band() -> None:
    # Exactly 15% and exactly 20% both carve: the charter says 15-20%, inclusive.
    assert carve(history(85, 15)).holdout_share == Decimal("0.15")
    assert carve(history(80, 20)).holdout_share == Decimal("0.20")


def test_bars_past_the_window_are_clipped_not_split() -> None:
    carved = carve(history(100, 20) + beyond(50))
    assert len(carved.development) == 100
    assert len(carved.holdout) == 20
    assert len(carved.unevaluated) == 50
    assert all(b.closed_at < EVALUATION_END for b in carved.development + carved.holdout)


def test_a_bar_closing_at_the_window_end_is_unevaluated() -> None:
    # Same convention as the holdout pin: the instant belongs to the far side.
    carved = carve(history(100, 20) + beyond(1))
    assert carved.unevaluated[0].closed_at == EVALUATION_END


def test_catalog_growth_moves_neither_the_share_nor_the_split() -> None:
    # The property ADR-0017 exists for: the live path keeps the catalog fresh, and the
    # gate must score the identical history it scored before the append.
    frozen = carve(history(100, 20))
    grown = carve(history(100, 20) + beyond(400))
    assert grown.development == frozen.development
    assert grown.holdout == frozen.holdout
    assert grown.holdout_share == frozen.holdout_share


def test_growth_past_the_window_no_longer_refuses() -> None:
    # Before the far pin this raised: new bars landed on the holdout side and pushed the
    # share past 20%. Appending a year of sessions must now be uneventful.
    carved = carve(history(85, 15) + beyond(250))
    assert MIN_HOLDOUT_SHARE <= carved.holdout_share <= MAX_HOLDOUT_SHARE


def test_the_share_denominator_is_the_window_not_the_catalog() -> None:
    # Counting clipped bars would let growth drag the share down and out of the band.
    carved = carve(history(85, 15) + beyond(1_000))
    assert carved.holdout_share == Decimal("0.15")


def test_clipped_bars_are_carried_in_order() -> None:
    # Carried so a caller can report the clip; a silently shortened history is the
    # failure this module exists to prevent.
    carved = carve((history(100, 20) + beyond(10))[::-1])
    assert len(carved.unevaluated) == 10
    assert carved.unevaluated == tuple(sorted(carved.unevaluated, key=lambda b: b.closed_at))


def test_a_history_only_beyond_the_window_refuses() -> None:
    # Everything clipped means nothing to score, and the message must say the window
    # swallowed it rather than blaming the boundary alone.
    with pytest.raises(HoldoutCarveError, match="beyond it"):
        carve(beyond(500))


def test_an_unclipped_carve_reports_nothing_beyond() -> None:
    assert carve(history(100, 20)).unevaluated == ()
