"""
The equity at which a premise stops paying for itself.

Read-only. It re-prices trades the gate already scored and writes a JSON report; it
constructs no execution client and cannot place an order.

    python -m copilot.calibration.account_sweep --all
    python -m copilot.calibration.account_sweep aapl-gap-fade-long --write

Why this exists
---------------
[ADR-0009] decided that cost analysis **reports a sweep across account sizes, not a
single figure**, and that the useful output is *the equity at which the premise crosses
zero*. That sweep was never built. Every verdict since has been reported at the research
default of USD 1,000 risked per trade - a number the ADR names as a backtest convenience
that must not be read as policy - while the charter describes an account under USD
10,000 risking 0.10% to 0.25%, which is USD 8 to 20 a trade.

The mechanism, and why nothing else exposes it
----------------------------------------------
Spread cost in R is scale-free: quantity cancels out of it entirely. Commission does
not, because Interactive Brokers charges a **USD 1.00 per-order minimum**. At USD 20 of
risk a position is single-digit shares, so the round trip costs USD 2.00 whatever the
size - and against a gross edge near 0.05 R that is more than the edge.

Below a certain budget a trade stops sizing at all: one share already risks more than
the budget allows, and :func:`position_size` floors to zero. Those trades are not taken,
so they leave both the cost **and** the gross, which is why a sweep cannot be done by
scaling a single figure and has to re-price trade by trade.

What is exact here and what is not
----------------------------------
The re-pricing is arithmetic on trades the gate already scored, not a new backtest.
Stop distance is recovered per trade as ``risk_amount / quantity``, which is exactly
what sized it, and the same :func:`position_size` the strategy uses re-sizes it. So the
sweep reproduces what the gate would have done at another budget **given the same
signals**.

What it cannot show is a strategy that would have traded differently at another size -
skipping a signal it could not afford changes no later signal here, because the gap fade
holds one position at a time and its entries do not depend on its fills. A premise where
that is untrue needs a re-run, not this.

"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from copilot.calibration.cost_impact import scored_trades
from copilot.calibration.cost_model import CostModel
from copilot.calibration.cost_model import commission
from copilot.calibration.cost_model import split_factor
from copilot.paths import add_catalog_argument
from copilot.risk.sizing import position_size
from copilot.strategies.activations import find_activation
from copilot.strategies.activations import load_activations


if TYPE_CHECKING:
    from collections.abc import Sequence

OUT_DIR = Path(__file__).parent / "out"

# The charter's band, and the research default that has stood in for it.
RISK_FRACTIONS = (Decimal("0.0010"), Decimal("0.0025"))

# Equities to price. Dense where the crossing is expected and sparse above it, because
# the answer being sought is a boundary rather than a curve.
EQUITIES = (
    Decimal(5_000),
    Decimal(8_000),
    Decimal(10_000),
    Decimal(15_000),
    Decimal(20_000),
    Decimal(25_000),
    Decimal(35_000),
    Decimal(50_000),
    Decimal(75_000),
    Decimal(100_000),
    Decimal(150_000),
    Decimal(250_000),
    Decimal(500_000),
)


@dataclass(frozen=True)
class Priced:
    """
    One symbol's economics at one risk budget.
    """

    risk_budget: Decimal
    trades_taken: int
    trades_unsized: int
    gross_r: Decimal
    spread_r: Decimal
    commission_r: Decimal

    @property
    def net_r(self) -> Decimal:
        """
        Return expectancy after costs, in R.
        """
        return self.gross_r - self.spread_r - self.commission_r

    @property
    def viable(self) -> bool:
        """
        Return whether the premise pays for itself at this budget.
        """
        return self.net_r > 0

    def as_record(self) -> dict[str, object]:
        """
        Return the JSON form.
        """
        q = Decimal("0.000001")
        return {
            "risk_budget": str(self.risk_budget),
            "trades_taken": self.trades_taken,
            "trades_unsized": self.trades_unsized,
            "gross_r": str(self.gross_r.quantize(q)),
            "spread_r": str(self.spread_r.quantize(q)),
            "commission_r": str(self.commission_r.quantize(q)),
            "net_r": str(self.net_r.quantize(q)),
            "viable": self.viable,
        }


def reprice(
    trades: Sequence[object],
    symbol: str,
    risk_budget: Decimal,
    bps_per_side: Decimal,
) -> Priced:
    """
    Re-price scored trades at a different risk budget.

    Trades that cannot be sized at this budget are dropped from **both** the cost and
    the gross, because a position that is never opened earns nothing and costs nothing.
    Reporting them as zero-return trades would understate the edge; reporting the
    original gross beside the new cost would overstate it.

    """
    spread_total = Decimal(0)
    commission_total = Decimal(0)
    gross_total = Decimal(0)
    taken = 0
    unsized = 0

    for trade in trades:
        quantity = Decimal(trade.quantity)
        if quantity <= 0:
            continue
        distance = trade.risk_amount / quantity
        resized = position_size(risk_budget=risk_budget, distance=distance)
        if resized <= 0:
            unsized += 1
            continue

        realised_risk = resized * distance
        notional = resized * trade.entry_price
        real_shares = resized / split_factor(symbol, trade.opened_at)
        spread_total += 2 * (bps_per_side / 10_000) * notional / realised_risk
        commission_total += 2 * commission(real_shares, notional) / realised_risk
        gross_total += trade.r_multiple
        taken += 1

    if taken == 0:
        return Priced(risk_budget, 0, unsized, Decimal(0), Decimal(0), Decimal(0))

    count = Decimal(taken)
    return Priced(
        risk_budget=risk_budget,
        trades_taken=taken,
        trades_unsized=unsized,
        gross_r=gross_total / count,
        spread_r=spread_total / count,
        commission_r=commission_total / count,
    )


def crossing_equity(rows: Sequence[tuple[Decimal, Priced]]) -> Decimal | None:
    """
    Return the lowest priced equity at which the premise is viable, or None.

    Reported as a priced point rather than interpolated. The curve is not smooth - it
    steps every time another trade starts sizing - so a straight line between two
    points would invent a precision the sweep does not have.

    """
    for equity, priced in sorted(rows, key=lambda r: r[0]):
        if priced.viable:
            return equity
    return None


def sweep(
    trades: Sequence[object],
    symbol: str,
    bps_per_side: Decimal,
    risk_fraction: Decimal,
    equities: Sequence[Decimal] = EQUITIES,
) -> list[tuple[Decimal, Priced]]:
    """
    Price one symbol across account sizes at a fixed planned risk fraction.
    """
    return [
        (equity, reprice(trades, symbol, equity * risk_fraction, bps_per_side))
        for equity in equities
    ]


def main(argv: list[str] | None = None) -> int:
    """
    Sweep account size for one activation or all of them.

    Returns a process exit code.

    """
    parser = argparse.ArgumentParser(
        prog="python -m copilot.calibration.account_sweep",
        description="Find the equity at which a premise stops paying for itself.",
    )
    parser.add_argument("activation", nargs="?", help="Activation name, or use --all")
    parser.add_argument("--all", action="store_true", help="Sweep every activation")
    add_catalog_argument(parser)
    parser.add_argument("--write", action="store_true", help="File the report as JSON")
    args = parser.parse_args(argv)

    if args.all:
        activations = load_activations()
    elif args.activation:
        activations = (find_activation(args.activation),)
    else:
        parser.error("name an activation or pass --all")

    model = CostModel.from_snapshot()
    print(
        f"Spread at {model.percentile} per side from {model.snapshot}, plus the IB "
        f"schedule including its USD 1.00 per-order minimum.\n"
        f"Gross is the mean R of the trades that still size at each budget "
        f"(ADR-0009).\n",
    )

    report: dict[str, object] = {
        "run_at": datetime.now(tz=UTC).isoformat(),
        "cost_model": {"snapshot": model.snapshot, "percentile": model.percentile},
        "activations": {},
    }

    for activation in activations:
        symbol = activation.symbol
        trades, _ = scored_trades(activation, args.catalog)
        bps = model.spread_bps_for(symbol)
        entry: dict[str, object] = {"symbol": symbol, "scored_trades": len(trades), "by_risk": {}}

        for fraction in RISK_FRACTIONS:
            rows = sweep(trades, symbol, bps, fraction)
            crossing = crossing_equity(rows)
            pct = f"{fraction * 100:.2f}%"
            print(f"-- {activation.name}  risk {pct} of equity --")
            print(
                f"  {'equity':>10}{'budget':>9}{'taken':>8}{'unsized':>9}"
                f"{'gross R':>10}{'spread R':>10}{'comm R':>9}{'net R':>10}",
            )
            for equity, priced in rows:
                mark = "  <-- crosses" if crossing is not None and equity == crossing else ""
                print(
                    f"  {equity:>10,.0f}{priced.risk_budget:>9,.0f}{priced.trades_taken:>8}"
                    f"{priced.trades_unsized:>9}{priced.gross_r:>10.4f}{priced.spread_r:>10.4f}"
                    f"{priced.commission_r:>9.4f}{priced.net_r:>10.4f}{mark}",
                )
            print(
                f"  crosses zero at: "
                f"{f'USD {crossing:,.0f}' if crossing else 'nowhere in the swept range'}\n",
            )
            entry["by_risk"][pct] = {
                "crossing_equity": str(crossing) if crossing else None,
                "rows": [{"equity": str(e), **p.as_record()} for e, p in rows],
            }

        report["activations"][activation.name] = entry

    if args.write:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
        path = OUT_DIR / f"account_sweep_{stamp}.json"
        path.write_text(json.dumps(report, indent=2) + "\n")
        print(f"filed {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
