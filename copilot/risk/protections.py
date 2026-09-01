"""
Rolling-window circuit breakers, ported from trade-copilot ``libs/risk/protections.py``.

Ported rather than imported: trade-copilot is a separate repository that is not on
this path, and the point of the port is that this overlay stands alone. The logic and
its reasoning are reproduced faithfully; only the contract types change, from pydantic
models to stdlib dataclasses, so this module needs no dependency Nautilus lacks.

The gap these close: every risk control Nautilus ships judges **one order in
isolation** - ``max_notional_per_order`` and the submit/modify rate limits. Nothing
asks *"how has the last fortnight gone"*, so four stop-outs in a row, or a drawdown
that has quietly eaten a tenth of the account, changes no behaviour at all. Over an
unattended paper run that is the difference between a bad week and a bad month.

These functions are **pure**: the same outcomes, policy and instant give the same
decision, with no clock and no I/O of their own. Reading closed trades belongs to the
caller (see :mod:`copilot.risk.guard`); the judgement belongs here, where it can be
tested against a hand-built losing streak rather than against a live account.

Original design rationale: trade-copilot ADR-0025.

"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum


class ProtectionTrigger(StrEnum):
    """
    Which rolling-window breaker refused new trading.

    Named separately from a generic "rejected" because the operator's first question on
    being alerted is exactly which breaker fired.

    """

    CONSECUTIVE_STOPS = "CONSECUTIVE_STOPS"
    MAX_DRAWDOWN = "MAX_DRAWDOWN"


@dataclass(frozen=True)
class ProtectionPolicy:
    """
    Account-wide rolling-window breaker settings.

    **Enabled by default, deliberately.** A breaker the operator has to remember to
    switch on is not a breaker, and its whole purpose is the stretch of an unattended
    run when nobody is watching. The defaults are quiet in ordinary trading and speak
    up before a bad week becomes a bad month: at 1% risk per trade, four consecutive
    stop-outs is about 4% of the account, and the 6% drawdown limit is roughly six
    full stops inside a fortnight.

    This belongs to the operator, not to a strategy: a loss limit states the
    operator's tolerance for loss, not a rule's edge, so two strategies on one account
    share one breaker.

    """

    enabled: bool = True

    max_consecutive_stops: int = 4
    """
    Stop-outs in an unbroken run, counted back from the latest closed trade.

    ``0`` disables this breaker while leaving the drawdown one in force.

    """

    max_drawdown_pct: Decimal = Decimal("0.06")
    """
    Peak-to-trough decline of realised P&L inside the window, as a fraction of account
    value.

    Peak-to-trough rather than net, so a strong start cannot fund a
    collapse in silence. ``0`` disables this breaker.

    """

    window_days: int = 14
    cooldown_days: int = 3

    def __post_init__(self) -> None:
        """
        Refuse settings that cannot mean anything.
        """
        if self.max_consecutive_stops < 0:
            raise ValueError("max_consecutive_stops must be >= 0")
        if not (Decimal(0) <= self.max_drawdown_pct <= Decimal(1)):
            raise ValueError("max_drawdown_pct must be within [0, 1]")
        if self.window_days <= 0:
            raise ValueError("window_days must be > 0")
        if self.cooldown_days < 0:
            raise ValueError("cooldown_days must be >= 0")


@dataclass(frozen=True)
class TradeOutcome:
    """
    One closed round trip, as a breaker needs to see it.

    Deliberately narrow: a breaker judges *when* a position closed, *how much* it made
    or lost, and *whether the stop decided it*. Symbol, direction and prices are not
    its business, and taking them would invite a per-symbol breaker, which is a
    portfolio decision this does not make.

    """

    closed_at: datetime
    realized_pnl: Decimal
    stopped_out: bool


@dataclass(frozen=True)
class ProtectionBreach:
    """
    Why new trading is being refused, and until when.
    """

    trigger: ProtectionTrigger
    detail: str
    """
    Human-readable and specific - it reaches the operator's alert unchanged, and "4
    consecutive stop-outs since 2026-09-02" is actionable where "protection" is not.
    """
    triggered_at: datetime
    """
    When the breaching trade closed, **not** when the breach was noticed.

    The cooldown runs from the event, so a quiet weekend counts toward it rather than
    resetting it by not being observed.

    """
    until: datetime

    def is_active_at(self, moment: datetime) -> bool:
        """
        Whether this breach still bars trading at ``moment``.
        """
        return moment < self.until


def evaluate_protections(
    outcomes: Sequence[TradeOutcome],
    policy: ProtectionPolicy,
    *,
    now: datetime,
    account_value: Decimal,
) -> ProtectionBreach | None:
    """
    Return the breach in force at ``now``, or ``None`` to trade normally.

    ``outcomes`` may be in any order and may extend further back than the window;
    this filters and sorts, so a caller cannot introduce a bug by handing over a
    query result in whatever order it arrived.

    Both breakers are evaluated and the one whose cooldown runs **longest** wins.
    Taking the first to match would let a mild drawdown that happened to be checked
    first mask a severe stop-out streak and release trading earlier than either rule
    intends.

    """
    if not policy.enabled:
        return None

    window_start = now - timedelta(days=policy.window_days)
    recent = sorted(
        (o for o in outcomes if window_start <= o.closed_at <= now),
        key=lambda o: o.closed_at,
    )
    if not recent:
        return None

    breaches = [
        breach
        for breach in (
            _consecutive_stops(recent, policy),
            _drawdown(recent, policy, account_value),
        )
        if breach is not None
    ]
    active = [b for b in breaches if b.is_active_at(now)]
    if not active:
        return None
    return max(active, key=lambda b: b.until)


def _consecutive_stops(
    recent: Sequence[TradeOutcome],
    policy: ProtectionPolicy,
) -> ProtectionBreach | None:
    """
    Report a run of stop-outs ending at the most recent trade.

    The streak must be *current*: a run of four stops followed by a win means the
    strategy has since done something right, and holding it in penalty for a recovered
    streak would be punishing history rather than managing risk. So this counts backward
    from the latest trade and stops at the first non-stop.

    """
    if policy.max_consecutive_stops <= 0:
        return None

    streak = 0
    for outcome in reversed(recent):
        if not outcome.stopped_out:
            break
        streak += 1

    if streak < policy.max_consecutive_stops:
        return None

    triggered_at = recent[-1].closed_at
    return ProtectionBreach(
        trigger=ProtectionTrigger.CONSECUTIVE_STOPS,
        detail=(
            f"{streak} consecutive stop-outs (limit {policy.max_consecutive_stops}) "
            f"in the {policy.window_days}-day window"
        ),
        triggered_at=triggered_at,
        until=triggered_at + timedelta(days=policy.cooldown_days),
    )


def _drawdown(
    recent: Sequence[TradeOutcome],
    policy: ProtectionPolicy,
    account_value: Decimal,
) -> ProtectionBreach | None:
    """
    Report peak-to-trough decline of the window's realised equity curve.

    Peak-to-trough, not the window's net: a run that made 5% and then gave back 9% is
    net down 4% but has just lost 9% from its high, and it is the 9% that says something
    has changed. Measuring the net would let a strong start fund a collapse in silence,
    which is the exact shape this exists to catch.

    Only realised P&L counts. Open positions would make the breaker fire and un-fire
    with every price tick rather than on completed evidence.

    """
    if policy.max_drawdown_pct <= 0 or account_value <= 0:
        return None

    limit = account_value * policy.max_drawdown_pct
    equity = Decimal(0)
    peak = Decimal(0)
    worst = Decimal(0)
    trough_at: datetime | None = None

    for outcome in recent:
        equity += outcome.realized_pnl
        peak = max(peak, equity)
        decline = peak - equity
        if decline > worst:
            worst = decline
            trough_at = outcome.closed_at

    if trough_at is None or worst < limit:
        return None

    return ProtectionBreach(
        trigger=ProtectionTrigger.MAX_DRAWDOWN,
        detail=(
            f"realised drawdown {worst:.2f} exceeds {limit:.2f} "
            f"({policy.max_drawdown_pct:%} of account) within {policy.window_days} days"
        ),
        triggered_at=trough_at,
        until=trough_at + timedelta(days=policy.cooldown_days),
    )


__all__ = [
    "ProtectionBreach",
    "ProtectionPolicy",
    "ProtectionTrigger",
    "TradeOutcome",
    "evaluate_protections",
]
