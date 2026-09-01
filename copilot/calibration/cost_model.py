"""
The cost model the validation gate charges: measured spread plus IB commission.

[ADR-0011] pins the two choices this module embodies. The spread coefficient is **p95 of
the measured full spread, per instrument, halved to per side**, taken from one named
snapshot rather than from whichever file is newest - a verdict has to tie to the exact
numbers it was charged with, the same way it ties to a commit. The commission schedule is
IB Pro fixed tier for US equities, whose per-order minimum has been measured live twice
(USD 2.02 and USD 2.01 round trips at 3 and 1 shares).

Why p95 rather than the median: the choice moves net edge by only 3-14% of gross at
daily-bar frequency, and three known biases all point the same way. The snapshots sample
mid-session while the strategy trades after overnight gaps; the median moved 2x between
measurement sessions, so any central estimate from one session inherits that session; and
2026 spreads are applied to trades from 2006 onward. Conservatism here is nearly free, so
it is bought.

Costs are charged on the trades **as replayed** - the research sizing. Whether the
premise survives at the target account size is a separate judgment owned by
[ADR-0009], where the per-order minimum dominates everything this module measures.

"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Mapping

    from copilot.validation.types import BacktestRunResult


SNAPSHOT_DIR = Path(__file__).parent / "out"

CANONICAL_SNAPSHOT = "spread_snapshot_20260901T154744Z.json"
"""
The pinned calibration source: the first snapshot reproducible from a commit.

Pinned by name rather than "the newest file" so that re-running the calibrator cannot
silently change what every verdict is charged. Moving the pin is a deliberate act that
belongs in a commit touching this line.

"""

PERCENTILE = "p95"
"""The coefficient chosen in [ADR-0011]. ``full_spread_bps`` key in the snapshot."""

# IB Pro, fixed tier, US equities.
COMMISSION_PER_SHARE = Decimal("0.005")
COMMISSION_MIN = Decimal("1.00")
COMMISSION_MAX_PCT = Decimal("0.01")

SPLITS: dict[str, list[tuple[datetime, Decimal]]] = {
    "AAPL": [
        (datetime(2005, 2, 28, tzinfo=UTC), Decimal(2)),
        (datetime(2014, 6, 9, tzinfo=UTC), Decimal(7)),
        (datetime(2020, 8, 31, tzinfo=UTC), Decimal(4)),
    ],
}
"""
Splits inside the catalog window, from the backfill's corporate-action report.

Catalog prices are back-adjusted, so an early trade is recorded in today's share count.
Charging per-share commission on the adjusted count overstates early commission by the
cumulative split factor - 56x for 2006 AAPL. Only symbols that split need an entry; a
symbol absent here is charged on the recorded count, which is the actual one when no
split intervened.

"""


class UncalibratedSymbolError(KeyError):
    """
    The snapshot carries no spread measurement for a requested symbol.
    """

    def __init__(self, symbol: str, snapshot: str) -> None:
        """
        Name the symbol and the snapshot that lacks it.
        """
        super().__init__(
            f"no spread measured for {symbol} in {snapshot}. Run the calibrator with the "
            f"symbol included (COPILOT_CAL_SYMBOLS) during a market session, commit the "
            f"snapshot, and pin it deliberately - do not fall back to another symbol's "
            f"number.",
        )


def split_factor(symbol: str, when: datetime) -> Decimal:
    """
    Return the cumulative split factor after ``when``: adjusted shares over real shares.
    """
    factor = Decimal(1)
    for date, multiple in SPLITS.get(symbol, []):
        if when < date:
            factor *= multiple
    return factor


def commission(real_shares: Decimal, notional: Decimal) -> Decimal:
    """
    Return one side of an IB Pro fixed-tier US equity order.
    """
    fee = max(COMMISSION_MIN, COMMISSION_PER_SHARE * real_shares)
    return min(fee, COMMISSION_MAX_PCT * notional)


def round_trip_cost_r(
    *,
    symbol: str,
    quantity: Decimal,
    entry_price: Decimal,
    opened_at: datetime,
    risk_amount: Decimal,
    bps_per_side: Decimal,
) -> Decimal:
    """
    Return one round trip's cost in R: spread both ways plus commission both legs.

    Both commission legs are priced at the entry notional. The exit notional differs by
    the trade's own move, which touches only the 1% cap - never the minimum or the
    per-share rate - and is far below the measurement error in the spread itself.

    """
    notional = quantity * entry_price
    real_shares = quantity / split_factor(symbol, opened_at)
    spread = 2 * (bps_per_side / 10_000) * notional
    fees = 2 * commission(real_shares, notional)
    return (spread + fees) / risk_amount


@dataclass(frozen=True)
class CostModel:
    """
    Per-instrument spread coefficients from one pinned snapshot, plus the IB schedule.
    """

    bps_per_side: Mapping[str, Decimal]
    snapshot: str
    percentile: str

    @classmethod
    def from_snapshot(
        cls,
        path: Path | None = None,
        percentile: str = PERCENTILE,
    ) -> CostModel:
        """
        Build the model from a snapshot file, defaulting to the pinned canonical one.
        """
        resolved = path if path is not None else SNAPSHOT_DIR / CANONICAL_SNAPSHOT
        data = json.loads(resolved.read_text())
        bps: dict[str, Decimal] = {}
        for summary in data["symbols"]:
            if not summary.get("samples"):
                continue
            symbol = summary["instrument_id"].split("=")[0].split(".")[0]
            bps[symbol] = Decimal(str(summary["full_spread_bps"][percentile])) / 2
        return cls(bps_per_side=bps, snapshot=resolved.name, percentile=percentile)

    def spread_bps_for(self, symbol: str) -> Decimal:
        """
        Return the per-side coefficient for one symbol, refusing to guess for others.
        """
        try:
            return self.bps_per_side[symbol]
        except KeyError:
            raise UncalibratedSymbolError(symbol, self.snapshot) from None

    def cost_r(self, trade: object, symbol: str) -> Decimal:
        """
        Return one replayed trade's round-trip cost in R.
        """
        return round_trip_cost_r(
            symbol=symbol,
            quantity=Decimal(trade.quantity),  # type: ignore[attr-defined]
            entry_price=trade.entry_price,  # type: ignore[attr-defined]
            opened_at=trade.opened_at,  # type: ignore[attr-defined]
            risk_amount=trade.risk_amount,  # type: ignore[attr-defined]
            bps_per_side=self.spread_bps_for(symbol),
        )

    def net_expectancy_for(self, symbol: str) -> Callable[[BacktestRunResult], Decimal]:
        """
        Return a walk-forward objective scoring mean R per trade **net of costs**.

        Handing this to ``walk_forward`` makes both halves of the gate cost-aware: the
        in-sample search selects parameters that survive costs rather than merely win
        gross, and fold scores are net. An uncalibrated symbol raises here, before any
        replay runs, rather than scoring silently gross.

        """
        self.spread_bps_for(symbol)  # fail fast, not on the first fold

        def net_expectancy(result: BacktestRunResult) -> Decimal:
            if not result.trades:
                return Decimal(0)
            total = sum(
                (trade.r_multiple - self.cost_r(trade, symbol) for trade in result.trades),
                Decimal(0),
            )
            return total / Decimal(len(result.trades))

        return net_expectancy

    def as_record(self, symbol: str) -> dict[str, str]:
        """
        Return the fields a verdict carries so it ties to its exact cost basis.
        """
        return {
            "snapshot": self.snapshot,
            "percentile": self.percentile,
            "bps_per_side": str(self.spread_bps_for(symbol)),
            "commission": (
                f"IB Pro fixed tier: max({COMMISSION_MIN}, {COMMISSION_PER_SHARE}/share) "
                f"per order, capped at {COMMISSION_MAX_PCT * 100}% of notional, "
                "split-corrected share counts"
            ),
        }
