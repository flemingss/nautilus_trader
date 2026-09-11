"""
The scored trades themselves, filed beside the verdict they produced.

A verdict has always carried its counts and its scores and not the trades behind them, so
every question asked after the fact has cost a replay. Two of those questions are now
waiting on it: the evidence interval beside an ordinary walk-forward verdict, and
attribution - whether a premise's return is anything but the market's, which needs each
trade's dates to line up against a factor series. A pooled replay takes ten minutes to
answer a question the record could have answered in a second.

So the rows go in the record, and they go in **recoverable**: every field of a
:class:`~copilot.validation.types.ClosedTrade` is written exactly, so :func:`from_rows`
rebuilds trades equal to the ones scored, and the round-trip cost is written beside each
one so the net series can be recomputed without the cost snapshot that priced it.

What is filed, and what is not
------------------------------
**Every evaluated fold's scored test trades, in signal order.** The same trades the fold
scores were computed from and the same set ``WalkForwardReport.tearsheet`` pools, so a
number recomputed from the rows is the number in the record rather than a neighbour of it.
Training-window trades are not filed: they are the search's inputs, not its evidence.

**Cost to twelve places, not exactly.** The charge is a quotient and its exact ``Decimal``
runs to twenty-eight digits. Twelve places moves a mean over any number of trades by less
than a trillionth of an R, which is six orders below the six places every R figure is
written to, and it keeps a nine-symbol pool's record readable.

**``net_r`` is for the reader.** It is rounded to six places for display and never read
back; :attr:`FiledTrade.net_r` recomputes it from the exact fields.

"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from copilot.validation.types import ClosedTrade
from copilot.validation.types import Direction


if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Iterable
    from collections.abc import Sequence

    from copilot.validation.walkforward import FoldResult


COST_PLACES = Decimal("0.000000000001")
"""
Precision the round-trip cost is filed to.

See the module docstring for why twelve.

"""

DISPLAY_PLACES = Decimal("0.000001")


@dataclass(frozen=True)
class FiledTrade:
    """
    One scored trade as a record carries it: the trade, its fold, and what it cost.
    """

    fold: int
    trade: ClosedTrade
    cost_r: Decimal

    @property
    def net_r(self) -> Decimal:
        """
        The trade's return in R after its round-trip cost, the unit every score is in.
        """
        return self.trade.r_multiple - self.cost_r


def scored_trades(folds: Iterable[FoldResult]) -> tuple[tuple[int, ClosedTrade], ...]:
    """
    Return every evaluated fold's test trades with their fold index, in signal order.

    Sorted by signal instant and then symbol, which is the order the trades happened in
    and the order a block bootstrap has to see: grouped by fold or by symbol, the blocks
    would measure the grouping's dependence instead of the market's.

    """
    tagged = [
        (fold.index, trade)
        for fold in folds
        if fold.selected is not None
        for trade in fold.test_trade_details
    ]
    tagged.sort(key=lambda pair: (pair[1].signal_created_at, pair[1].symbol))
    return tuple(tagged)


def to_rows(
    folds: Iterable[FoldResult],
    *,
    cost_r: Callable[[ClosedTrade], Decimal],
) -> list[dict[str, object]]:
    """
    Return the filed rows for every scored trade in ``folds``.
    """
    rows: list[dict[str, object]] = []
    for index, trade in scored_trades(folds):
        cost = cost_r(trade).quantize(COST_PLACES)
        rows.append(
            {
                "fold": index,
                "symbol": trade.symbol,
                "direction": str(trade.direction),
                "quantity": trade.quantity,
                "entry_price": str(trade.entry_price),
                "exit_price": str(trade.exit_price),
                "exit_reason": trade.exit_reason,
                "signal_created_at": trade.signal_created_at.isoformat(),
                "opened_at": trade.opened_at.isoformat(),
                "closed_at": trade.closed_at.isoformat(),
                "realized_pnl": str(trade.realized_pnl),
                "risk_amount": str(trade.risk_amount),
                "cost_r": str(cost),
                "net_r": str((trade.r_multiple - cost).quantize(DISPLAY_PLACES)),
            },
        )
    return rows


def from_rows(rows: Sequence[dict[str, object]]) -> tuple[FiledTrade, ...]:
    """
    Rebuild the filed trades exactly, in the order they were filed.
    """
    return tuple(
        FiledTrade(
            fold=int(str(row["fold"])),
            trade=ClosedTrade(
                symbol=str(row["symbol"]),
                direction=Direction(str(row["direction"])),
                quantity=int(str(row["quantity"])),
                entry_price=Decimal(str(row["entry_price"])),
                exit_price=Decimal(str(row["exit_price"])),
                exit_reason=str(row["exit_reason"]),
                signal_created_at=datetime.fromisoformat(str(row["signal_created_at"])),
                opened_at=datetime.fromisoformat(str(row["opened_at"])),
                closed_at=datetime.fromisoformat(str(row["closed_at"])),
                realized_pnl=Decimal(str(row["realized_pnl"])),
                risk_amount=Decimal(str(row["risk_amount"])),
            ),
            cost_r=Decimal(str(row["cost_r"])),
        )
        for row in rows
    )


__all__ = [
    "COST_PLACES",
    "FiledTrade",
    "from_rows",
    "scored_trades",
    "to_rows",
]
