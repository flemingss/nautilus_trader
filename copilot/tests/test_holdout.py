"""
Tests for the locked holdout carve.

The dangerous failures are quiet ones: held-out bars leaking into development because
the catalog changed under a boundary, or a carve that returns everything when the
history does not straddle the pin. The pin itself is asserted so that moving it is a
diff someone must justify against ADR-0012.

"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

import pytest

from copilot.validation.holdout import HOLDOUT_START
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


def test_the_pin_is_a_deliberate_diff() -> None:
    # Moving the boundary re-decides ADR-0012; this failing is the justification prompt.
    assert datetime(2022, 1, 1, tzinfo=UTC) == HOLDOUT_START


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
