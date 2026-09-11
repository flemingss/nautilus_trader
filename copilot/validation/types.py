"""
Contract types for the validation gate, vendored from trade-copilot.

The gate (in-sample search -> purged walk-forward -> single-use holdout) is written
against a small set of value types and an **injected** replay:

    Replay = Callable[[Sequence[DailyBar], StrategyParameters], BacktestRunResult]

That injection point is what makes the gate engine-agnostic, and it is the seam this
overlay uses to run the same methodology on a Nautilus ``BacktestEngine`` instead of
trade-copilot's in-memory service replay. These types are vendored so the overlay
does not depend on trade-copilot being present on the path.

Only ``trades`` is load-bearing for scoring: the objective (``expectancy_r``) reads
``ClosedTrade.r_multiple`` and nothing else. ``features`` and ``signals`` are carried
for parity with the original contract and for diagnostics.

"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any


class Direction(StrEnum):
    """
    Which way a position was opened.
    """

    LONG = "LONG"
    SHORT = "SHORT"


@dataclass(frozen=True)
class DailyBar:
    """
    One daily OHLCV observation.

    ``closed_at`` is the bar's close instant and is the only ordering key the gate
    uses; everything downstream sorts on it rather than trusting input order.

    """

    symbol: str
    closed_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


@dataclass(frozen=True)
class ClosedTrade:
    """
    One completed round trip, as the backtest observed it.
    """

    symbol: str
    direction: Direction
    quantity: int
    entry_price: Decimal
    exit_price: Decimal
    exit_reason: str
    signal_created_at: datetime
    """
    When the trade is attributed to a window: **the entry fill, not the signal bar.**

    Named for the signal, and it was documented as the bar that produced one, but the Nautilus
    replay sees only positions, and a position knows its fill: for a ``next_close`` rule the
    decision is a session earlier than this timestamp (``docs/AUDIT_2026-09-11.md``, F28). Every
    reader treats it as the entry - the walk-forward's window filter, the filed trades' order,
    attribution's windows - so the name stays, filed rows keep reading back, and this says what
    it holds. An audit of an entry against its origin reads the decision from the session
    record, not from here.
    """
    opened_at: datetime
    closed_at: datetime
    realized_pnl: Decimal
    risk_amount: Decimal
    """Currency at risk when the position opened: ``quantity x stop_distance``.

    Recorded per trade rather than assumed constant, because quantity is floored to
    whole units, so realised risk sits at or just under the budget and differs
    slightly per trade. Using the budget as the denominator would overstate R by that
    rounding.
    """

    @property
    def r_multiple(self) -> Decimal:
        """
        P&L in units of the risk taken - the scale-free unit of account.

        Zero risk cannot happen for a trade that opened, but the guard keeps a corrupt
        record from raising here rather than being reported.

        """
        if self.risk_amount <= 0:
            return Decimal(0)
        return self.realized_pnl / self.risk_amount


@dataclass(frozen=True)
class BacktestRunResult:
    """
    Deterministic outputs from one replay of one parameter set.
    """

    trades: tuple[ClosedTrade, ...] = ()
    signals: tuple[Any, ...] = ()
    features: tuple[Any, ...] = ()
    diagnostics: dict[str, Any] = field(default_factory=dict)
    """
    Free-form, never scored.

    Somewhere to record why a run produced nothing.

    """


def expectancy_r(result: BacktestRunResult) -> Decimal:
    """
    Mean R per closed trade - the scale-free objective.

    Scoring dollars instead would put each symbol's price level inside its score and
    leave no two symbols comparable.

    """
    if not result.trades:
        return Decimal(0)
    total = sum((t.r_multiple for t in result.trades), Decimal(0))
    return total / Decimal(len(result.trades))


__all__ = [
    "BacktestRunResult",
    "ClosedTrade",
    "DailyBar",
    "Direction",
    "expectancy_r",
]
