"""Tests for the ported rolling-window breakers.

These exercise the pure judgement against hand-built sequences, which is the whole
reason the logic was kept free of I/O and clocks.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from copilot.risk.protections import (
    ProtectionPolicy,
    ProtectionTrigger,
    TradeOutcome,
    evaluate_protections,
)

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
ACCOUNT = Decimal(100000)


def outcome(days_ago: float, pnl: str, *, stopped: bool) -> TradeOutcome:
    return TradeOutcome(
        closed_at=NOW - timedelta(days=days_ago),
        realized_pnl=Decimal(pnl),
        stopped_out=stopped,
    )


def evaluate(outcomes, policy=None, account=ACCOUNT):
    return evaluate_protections(
        outcomes,
        policy or ProtectionPolicy(),
        now=NOW,
        account_value=account,
    )


class TestConsecutiveStops:
    def test_fires_at_the_limit(self):
        outcomes = [outcome(i, "-100", stopped=True) for i in (4, 3, 2, 1)]
        breach = evaluate(outcomes)
        assert breach is not None
        assert breach.trigger is ProtectionTrigger.CONSECUTIVE_STOPS

    def test_below_the_limit_does_not_fire(self):
        outcomes = [outcome(i, "-100", stopped=True) for i in (3, 2, 1)]
        assert evaluate(outcomes) is None

    def test_a_win_breaks_the_streak(self):
        # Four stops, then a win: the strategy has since done something right, so
        # holding it in penalty would punish recovered history.
        outcomes = [outcome(i, "-100", stopped=True) for i in (5, 4, 3, 2)]
        outcomes.append(outcome(1, "50", stopped=False))
        assert evaluate(outcomes) is None

    def test_streak_counts_only_from_the_latest_trade(self):
        # An older run of four is irrelevant once a non-stop intervenes.
        outcomes = [outcome(i, "-100", stopped=True) for i in (9, 8, 7, 6)]
        outcomes.append(outcome(5, "10", stopped=False))
        outcomes += [outcome(i, "-100", stopped=True) for i in (2, 1)]
        assert evaluate(outcomes) is None

    def test_zero_limit_disables_only_this_breaker(self):
        policy = ProtectionPolicy(max_consecutive_stops=0)
        outcomes = [outcome(i, "-1", stopped=True) for i in (4, 3, 2, 1)]
        assert evaluate(outcomes, policy) is None

    def test_cooldown_runs_from_the_event_not_from_now(self):
        # Streak completed 5 days ago with a 3-day cooldown: already expired.
        outcomes = [outcome(i, "-100", stopped=True) for i in (8, 7, 6, 5)]
        assert evaluate(outcomes) is None


class TestDrawdown:
    def test_peak_to_trough_fires_even_when_net_is_positive(self):
        # The shape this exists to catch: a strong start funding a collapse.
        # +10000 then -7000 leaves net +3000 but a 7000 fall from the high,
        # which exceeds 6% of a 100k account.
        outcomes = [
            outcome(5, "10000", stopped=False),
            outcome(1, "-7000", stopped=True),
        ]
        breach = evaluate(outcomes)
        assert breach is not None
        assert breach.trigger is ProtectionTrigger.MAX_DRAWDOWN

    def test_net_loss_below_the_limit_does_not_fire(self):
        outcomes = [outcome(2, "-1000", stopped=True)]
        assert evaluate(outcomes) is None

    def test_zero_pct_disables_only_this_breaker(self):
        policy = ProtectionPolicy(max_drawdown_pct=Decimal(0))
        outcomes = [outcome(5, "10000", stopped=False), outcome(1, "-9000", stopped=False)]
        assert evaluate(outcomes, policy) is None

    def test_non_positive_account_value_disables_it(self):
        outcomes = [outcome(5, "10000", stopped=False), outcome(1, "-9000", stopped=False)]
        assert evaluate(outcomes, account=Decimal(0)) is None


class TestWindowAndPrecedence:
    def test_trades_outside_the_window_are_ignored(self):
        outcomes = [outcome(i, "-100", stopped=True) for i in (40, 39, 38, 37)]
        assert evaluate(outcomes) is None

    def test_empty_input_is_quiet(self):
        assert evaluate([]) is None

    def test_disabled_policy_is_quiet(self):
        policy = ProtectionPolicy(enabled=False)
        outcomes = [outcome(i, "-5000", stopped=True) for i in (4, 3, 2, 1)]
        assert evaluate(outcomes, policy) is None

    def test_unordered_input_is_handled(self):
        # A caller handing over rows in arbitrary order must not change the verdict.
        ordered = [outcome(i, "-100", stopped=True) for i in (4, 3, 2, 1)]
        shuffled = [ordered[2], ordered[0], ordered[3], ordered[1]]
        assert evaluate(shuffled) is not None

    def test_longest_cooldown_wins_when_both_fire(self):
        # Both breakers trip; the one whose cooldown runs longest must be reported,
        # so a mild breach cannot release trading before a severe one intends.
        outcomes = [
            outcome(6, "20000", stopped=False),
            outcome(4, "-9000", stopped=True),  # drawdown trough, oldest
            outcome(3, "-100", stopped=True),
            outcome(2, "-100", stopped=True),
            outcome(1, "-100", stopped=True),  # stop streak ends latest
        ]
        breach = evaluate(outcomes)
        assert breach is not None
        # The stop streak's trigger is more recent, so its cooldown ends later.
        assert breach.trigger is ProtectionTrigger.CONSECUTIVE_STOPS

    def test_breach_is_active_until_cooldown_expires(self):
        outcomes = [outcome(i, "-100", stopped=True) for i in (4, 3, 2, 1)]
        breach = evaluate(outcomes)
        assert breach is not None
        assert breach.is_active_at(NOW)
        assert not breach.is_active_at(breach.until + timedelta(seconds=1))


class TestPolicyValidation:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"max_consecutive_stops": -1},
            {"max_drawdown_pct": Decimal("1.5")},
            {"max_drawdown_pct": Decimal("-0.1")},
            {"window_days": 0},
            {"cooldown_days": -1},
        ],
    )
    def test_invalid_settings_are_refused(self, kwargs):
        with pytest.raises(ValueError):
            ProtectionPolicy(**kwargs)

    def test_defaults_match_the_shipped_policy(self):
        policy = ProtectionPolicy()
        assert policy.enabled is True
        assert policy.max_consecutive_stops == 4
        assert policy.max_drawdown_pct == Decimal("0.06")
        assert policy.window_days == 14
        assert policy.cooldown_days == 3
