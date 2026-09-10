"""
What the evidence check has to get right to be worth having.

It exists because ``score > 0`` passed the AAPL next-close holdout on +0.035 R at a
third of a standard error from zero. A replacement that is merely a different arbitrary
rule would be no better, so these pin the properties that make it *not* arbitrary:

- **A wider interval under dependence**, because that is the entire claim. A block
  bootstrap that reports the same interval for clustered returns as for independent ones
  is an ordinary bootstrap wearing a costume.
- **A verdict that separates *not proven* from *disproven***, because collapsing them is
  what threw away the AAPL result's real content.
- **Reproducibility from a seed**, because ``validate`` recomputes verdicts from a commit
  and a number that moves each run cannot be checked.

"""

from __future__ import annotations

import random
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

import pytest

from copilot.validation.evidence import DEFAULT_SEED
from copilot.validation.evidence import assess
from copilot.validation.evidence import autocorrelation_time
from copilot.validation.evidence import block_length
from copilot.validation.evidence import concurrency
from copilot.validation.evidence import r_multiples
from copilot.validation.types import ClosedTrade
from copilot.validation.types import Direction


BASE = datetime(2022, 1, 3, tzinfo=UTC)


def trade(index: int, r: float, *, hold_days: int = 1) -> ClosedTrade:
    """
    One trade returning ``r`` on a fixed risk, opened one day after the last.
    """
    opened = BASE + timedelta(days=index)
    return ClosedTrade(
        symbol="AAPL",
        direction=Direction.LONG,
        quantity=1,
        entry_price=Decimal(100),
        exit_price=Decimal(101),
        exit_reason="target",
        signal_created_at=opened,
        opened_at=opened,
        closed_at=opened + timedelta(days=hold_days),
        realized_pnl=Decimal(str(r)) * 1000,
        risk_amount=Decimal(1000),
    )


def series(values: list[float], *, hold_days: int = 1) -> list[ClosedTrade]:
    return [trade(i, v, hold_days=hold_days) for i, v in enumerate(values)]


def independent(n: int, *, mean: float = 0.0, sd: float = 1.0, seed: int = 11) -> list[float]:
    """
    A memoryless series whose **sample** mean is exactly ``mean``.

    Recentred on purpose. A draw of 111 from N(0.035, 1) has a standard error near 0.1,
    so an uncentred draw lands wherever it lands and the test would be measuring the
    seed rather than the interval.

    """
    rng = random.Random(seed)  # noqa: S311 - deterministic fixture, not a secret
    drawn = [rng.gauss(0, sd) for _ in range(n)]
    shift = mean - sum(drawn) / n
    return [round(v + shift, 6) for v in drawn]


def clustered(runs: int, per_run: int, *, seed: int = 11) -> list[float]:
    """
    Returns drawn in regimes, which is the shape `cost_impact` reports on real data.
    """
    rng = random.Random(seed)  # noqa: S311 - deterministic fixture, not a secret
    out: list[float] = []
    for _ in range(runs):
        level = rng.gauss(0, 0.8)
        out += [round(rng.gauss(level, 0.4), 6) for _ in range(per_run)]
    return out


# ------------------------------------------------------------------ the central claim


def test_clustered_returns_get_a_wider_interval_than_independent_ones() -> None:
    """
    The whole reason for a block bootstrap.

    Same length, same marginal spread, different dependence. An interval that ignores
    the dependence is the one that let a coin-flip result through.

    """
    loose = assess(series(independent(120, sd=0.9)))
    tight = assess(series(clustered(6, 20)))

    assert tight.standard_error_r > loose.standard_error_r
    assert (tight.upper_r - tight.lower_r) > (loose.upper_r - loose.lower_r)


def test_clustered_returns_are_worth_fewer_effective_trades() -> None:
    loose = assess(series(independent(120, sd=0.9)))
    tight = assess(series(clustered(6, 20)))

    assert tight.effective_trades < loose.effective_trades / 2
    assert tight.trades == loose.trades, "the raw count is what hides the difference"


def test_a_thin_edge_over_many_trades_does_not_clear_zero() -> None:
    """
    The AAPL case, in miniature: a positive mean the sample cannot support.
    """
    trades = series(independent(111, mean=0.035, sd=1.0))
    result = assess(trades)

    assert sum(r_multiples(trades)) > 0, "the point estimate is positive, as AAPL's was"
    assert result.clears(Decimal(0)) is False
    assert result.lower_r < 0 < result.upper_r


def test_a_strong_edge_clears_zero() -> None:
    """
    The check must be passable, or it is a refusal rather than a gate.
    """
    result = assess(series(independent(200, mean=0.60, sd=1.0)))

    assert result.clears(Decimal(0)) is True
    assert result.lower_r > 0


def test_a_strong_edge_does_not_clear_a_bar_set_above_it() -> None:
    result = assess(series(independent(200, mean=0.60, sd=1.0)))

    assert result.clears(Decimal("2.0")) is False


# ------------------------------------------------------------------------ the inputs


def test_concurrency_is_one_when_positions_never_overlap() -> None:
    assert concurrency(series([0.1] * 20, hold_days=1)) == Decimal(1)


def test_concurrency_counts_positions_open_together() -> None:
    """
    Five-day holds entered daily means about five open at once.
    """
    overlap = concurrency(series([0.1] * 40, hold_days=5))

    assert Decimal(4) < overlap < Decimal("5.5")


def test_concurrency_needs_two_trades_to_mean_anything() -> None:
    assert concurrency(series([0.1])) == Decimal(1)
    assert concurrency([]) == Decimal(1)


def test_the_autocorrelation_time_is_one_for_a_series_with_no_memory() -> None:
    assert autocorrelation_time(independent(200, seed=3)) < Decimal("1.6")


def test_the_autocorrelation_time_grows_with_the_run_length() -> None:
    short = autocorrelation_time(clustered(20, 6))
    long_ = autocorrelation_time(clustered(6, 20))

    assert long_ > short


def test_the_autocorrelation_time_survives_a_single_noisy_lag() -> None:
    """
    The regression for the rule this replaced.

    The first-crossing rule read one lag and reported an independence horizon of three
    trades for a series built from runs of twenty, because the sample autocorrelation
    dipped under the threshold at lag 3 and climbed back at lag 4.

    """
    assert autocorrelation_time(clustered(6, 20)) > Decimal(4)


def test_the_block_never_swallows_the_sample() -> None:
    """
    A block near the sample length leaves nothing to resample.
    """
    assert block_length(20, Decimal(50), independent(20)) <= 5


def test_the_block_covers_the_overlap() -> None:
    assert block_length(120, Decimal("5.4"), independent(120)) >= 6


def test_r_multiples_ignore_trades_that_risked_nothing() -> None:
    """
    A zero risk amount would divide by zero, and it is not an observation either.
    """
    trades = series([0.5, 0.5])
    trades[0] = ClosedTrade(**{**vars(trades[0]), "risk_amount": Decimal(0)})

    assert r_multiples(trades) == (0.5,)


# ------------------------------------------------------------------ reproducibility


def test_the_same_trades_give_the_same_interval() -> None:
    """
    ``validate`` recomputes verdicts from a commit; a moving number cannot be checked.
    """
    trades = series(independent(120))

    assert assess(trades) == assess(trades)


def test_a_different_seed_gives_a_different_draw() -> None:
    """
    Reproducible because the seed is fixed, not because the resample is degenerate.
    """
    trades = series(independent(120))

    assert assess(trades, seed=DEFAULT_SEED + 1) != assess(trades)


# --------------------------------------------------------------------- degenerate input


@pytest.mark.parametrize("count", [0, 1])
def test_too_few_trades_report_no_interval_rather_than_a_narrow_one(count: int) -> None:
    """
    A zero-width interval around one trade would read as certainty.
    """
    result = assess(series([0.5] * count))

    assert result.replicates == 0
    assert result.clears(Decimal(0)) is False


def test_identical_trades_give_a_zero_width_interval() -> None:
    """
    Not a degenerate case to guard against: the sample really does say one thing.
    """
    result = assess(series([0.5] * 50))

    assert result.lower_r == result.upper_r == Decimal("0.5")
    assert result.clears(Decimal(0)) is True
