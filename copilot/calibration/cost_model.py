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

**Measured 2026-09-03, and the conservatism is real but not uniform.**
:mod:`copilot.calibration.spread_history` now measures the same quantity from 7.6 years
of historical top-of-book - roughly 750,000 samples per symbol against this snapshot's
248 to 301, from real quotes rather than delayed ones, and separable by time of day.
Against the closing window this snapshot overcharges by 1.5x (SPY) to 2.5x (AAPL),
which is the direction it intended. Against the **opening** window it undercharges by
up to 4x, because the open is where the spread actually lives: it runs 1.7x the closing
spread on SPY and **23x on PEP**. Nothing charges the first five minutes today - the entry
bracket ([ADR-0013]) runs at the signal close and the next close - and the charter's
actual execution window, the **first one to two hours**, was measured on 2026-09-03 and
is far milder than its first five minutes: p95 of **2.455 bps on AAPL, 2.958 on MSFT and
0.785 on SPY**, against the 3.981, 3.593 and 1.048 this snapshot charges. **So the pinned
snapshot is conservative against the window the charter says an order will actually go
into**, which is the question that matters and had never been asked. It stays pinned
until repinning is decided deliberately, a change that moves every filed verdict.

The same measurement carries a warning about widening the universe. This snapshot covers
three symbols; the other seventeen raise :class:`UncalibratedSymbolError`, which is the
right failure. Their execution-window spreads are not small - GOOGL 10.1 bps, CVX 9.2,
AMZN 8.0 - so a wider universe needs its own calibration before it needs anything else.

Costs are charged on the trades **as replayed** - the research sizing. Whether the
premise survives at the target account size is a separate judgment owned by
[ADR-0009], where the per-order minimum dominates everything this module measures.

"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from copilot.data.corporate_actions import cumulative_factor
from copilot.data.corporate_actions import split_actions


if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Mapping

    from copilot.validation.types import BacktestRunResult


SNAPSHOT_DIR = Path(__file__).parent / "out"

CANONICAL_SNAPSHOT = "spread_history_20260904T085055Z.json"
"""
The pinned calibration source: measured historical top of book, charged per [ADR-0019].

**7.6 years of real quotes, ~750,000 samples per symbol**, cut to the charter's
predeclared execution window and charged at the worst measured year's p95. It replaces
`spread_snapshot_20260901T154744Z.json` - 248 to 301 **delayed** quotes from one
27-minute session, sampled mid-session while the order goes in near the open - which was
the weakest input in this model by some distance.

Pinned by name rather than "the newest file" so that re-running the calibrator cannot
silently change what every verdict is charged. Moving the pin is a deliberate act that
belongs in a commit touching this line.

[ADR-0019]: ../docs/decisions/0019-spread-is-charged-from-measured-history.md

"""

PERCENTILE = "p95"
"""
The coefficient chosen in [ADR-0011] and kept by [ADR-0019].

The repin changed the *source* of the number and not the conservatism of it. Read from
the snapshot's ``basis`` block, which refuses if the file was measured at anything else.

"""

# IB Pro, fixed tier, US equities.
COMMISSION_PER_SHARE = Decimal("0.005")
COMMISSION_MIN = Decimal("1.00")
COMMISSION_MAX_PCT = Decimal("0.01")


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

    Catalog prices are back-adjusted - by the vendor for AAPL, on read for everyone else
    ([ADR-0015]) - so an early trade is recorded in today's share count. Charging
    per-share commission on that count overstates early commission by the cumulative
    factor: 28x for 2006 AAPL, 40x for 2013 GOOGL.

    **The factors come from :mod:`copilot.data.corporate_actions` rather than a table
    kept here, and that is the repair, not a tidy-up.** The price adjustment and the
    share-count correction describe the same events. While they lived in two places this
    module listed AAPL alone and was right only by luck: the four symbols whose splits
    were missing were also the four whose prices were never adjusted, so a recorded
    quantity happened to be the real one. Repairing the prices without repairing this
    would have made that luck run out silently - a real price charged an invented
    commission.

    Only share-count events count. A spinoff - MRK's Organon, T's Warner Bros Discovery,
    VZ's three - moves the price without issuing a share, so it adjusts the series and
    leaves the count alone. :func:`split_actions` filters those out.

    """
    return cumulative_factor(split_actions(symbol), when)


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


def _from_measured(data: dict, percentile: str) -> dict[str, Decimal]:
    """
    Read the charged coefficient from a measured-history snapshot.

    The ``charged`` block is the file's own answer, not a percentile picked here: the
    measurement decides which year is worst and states it, so this cannot silently take
    a different one. A symbol the measurement could not price has no block and is
    absent, which is what makes an uncalibrated symbol refuse rather than guess.

    """
    if data["basis"]["percentile"] != percentile:
        raise ValueError(
            f"snapshot is measured at {data['basis']['percentile']} and the model asked "
            f"for {percentile}; re-measure rather than reinterpreting the file",
        )
    return {
        row["symbol"]: Decimal(str(row["charged"]["p95_per_side_bps"]))
        for row in data["symbols"]
        if row.get("charged")
    }


def _from_broker_snapshot(data: dict, percentile: str) -> dict[str, Decimal]:
    """
    Read the coefficient from a live broker snapshot, the basis [ADR-0011] pinned.

    Kept after the repin so that ADR-0011's own record stays reproducible. A superseded
    decision whose numbers can no longer be recomputed is a claim rather than a record.

    [ADR-0011]: ../docs/decisions/0011-spread-is-charged-at-p95-from-a-pinned-snapshot.md

    """
    bps: dict[str, Decimal] = {}
    for summary in data["symbols"]:
        if not summary.get("samples"):
            continue
        symbol = summary["instrument_id"].split("=")[0].split(".")[0]
        bps[symbol] = Decimal(str(summary["full_spread_bps"][percentile])) / 2
    return bps


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
        reader = _from_measured if "basis" in data else _from_broker_snapshot
        return cls(
            bps_per_side=reader(data, percentile),
            snapshot=resolved.name,
            percentile=percentile,
        )

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
