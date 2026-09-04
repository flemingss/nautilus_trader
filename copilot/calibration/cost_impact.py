"""
What the spread coefficient costs, in R, against the trades the gate actually scored.

    python -m copilot.calibration.cost_impact
    python -m copilot.calibration.cost_impact --write

Expectancy is in R, so costs are expressed in R and subtract directly from a verdict. This
exists to turn "median, p75 or p95?" from a policy abstraction into a number.

The arithmetic
--------------
::

    spread_R = 2 * (bps_per_side / 10_000) * notional / risk_amount
    commission_R = 2 * fee(real_shares, notional) / risk_amount

Note what cancels. Quantity cancels out of the spread term entirely, so spread cost in R
is set by the ratio of price to stop distance and by nothing else - not by the risk budget,
and not by account size. It does **not** cancel out of commission, because IB's per-order
minimum bites on small positions.

Split-adjusted prices break per-share commission
------------------------------------------------
Catalog prices are back-adjusted, so a 2006 AAPL trade is recorded in today's share count:
8,792 shares at $2.50 rather than roughly 314 at $70. Charging $0.005 per share on the
adjusted count overstates 2006 commission by the cumulative split factor, which for AAPL
is 56.

An earlier version of this analysis did exactly that and reported AAPL's commission drag as
0.0191 R. It is 0.0024 R. The error was visible as a monotonic fall in commission across
twenty years - from 0.0863 R in 2006 to 0.0020 R in 2025 - tracking nothing but the
split-adjusted price rising. Spread cost is unaffected, because quantity cancels there.

MSFT and SPY have no splits in the window, so their figures were right either way.

As of [ADR-0016] AAPL is no longer the only symbol this applies to. Seven more are
back-adjusted on read, so ``split_factor`` now covers AMZN, GOOGL, KO and WMT as well -
and deliberately does **not** cover MRK, T or VZ, whose corporate actions moved the
price without issuing a share. Both this module and the read path take those factors
from one table, so the recorded quantity and the commission charged on it cannot
disagree.

"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from copilot.calibration.cost_model import commission
from copilot.calibration.cost_model import split_factor
from copilot.data.catalog import bar_type_for
from copilot.data.catalog import equity_for
from copilot.data.catalog import open_catalog
from copilot.data.catalog import read_daily_bars
from copilot.paths import add_catalog_argument
from copilot.strategies.activations import load_activations
from copilot.validation.nautilus_replay import make_replay
from copilot.validation.walkforward import walk_forward


OUT_DIR = Path(__file__).parent / "out"


def measured_spreads(path: Path) -> dict[str, dict[str, Decimal]]:
    """
    Per-side spread in bps by symbol, for each candidate coefficient.

    Per side is half the full quoted spread: a round trip crosses half on the way in and
    half on the way out.

    """
    data = json.loads(path.read_text())
    out: dict[str, dict[str, Decimal]] = {}
    for symbol_summary in data["symbols"]:
        symbol = symbol_summary["instrument_id"].split("=")[0]
        full = symbol_summary["full_spread_bps"]
        out[symbol] = {
            choice: Decimal(str(full[choice])) / 2 for choice in ("median", "p75", "p95")
        }
        out[symbol]["incumbent"] = Decimal("5.0")
    return out


def cost_in_r(trades: list[Any], symbol: str, bps_per_side: Decimal) -> tuple[Decimal, Decimal]:
    """
    Mean spread cost and mean commission cost per round trip, in R.
    """
    spread = commission_total = Decimal(0)
    for trade in trades:
        quantity = Decimal(trade.quantity)
        notional = quantity * trade.entry_price
        real_shares = quantity / split_factor(symbol, trade.opened_at)
        spread += 2 * (bps_per_side / 10_000) * notional / trade.risk_amount
        commission_total += 2 * commission(real_shares, notional) / trade.risk_amount
    count = Decimal(len(trades))
    return spread / count, commission_total / count


def scored_trades(activation: object, catalog_path: str) -> tuple[list[Any], Decimal]:
    """
    Every trade the gate scored for one activation, and its gross expectancy.
    """
    catalog = open_catalog(catalog_path)
    instrument = equity_for(activation.symbol, activation.venue)
    bar_type = bar_type_for(instrument.id)
    settings = activation.validation
    report = walk_forward(
        read_daily_bars(catalog, bar_type),
        activation.grid(),
        train_bars=settings.train_bars,
        test_bars=settings.test_bars,
        purge_bars=settings.purge_bars,
        warmup_bars=activation.setup.warmup_bars,
        replay=make_replay(
            instrument=instrument,
            bar_type=bar_type,
            strategy_factory=activation.setup.factory,
        ),
        min_trades=settings.min_trades,
        fold_min_trades=settings.fold_min_trades,
    )
    trades = [t for fold in report.evaluated for t in fold.test_trade_details]
    scores = [fold.test_score for fold in report.evaluated]
    gross = sum(scores, Decimal(0)) / len(scores) if scores else Decimal(0)
    return trades, gross


def concentration(trades: list[Any]) -> dict[str, Any]:
    """
    How much of the total R comes from how few years.

    A mean is only a summary if the series behind it is not dominated by a couple of
    observations. This reports whether it is, because a fold-level pass rate cannot show
    it - a fold passes on its mean too.

    """
    by_year: dict[int, Decimal] = defaultdict(Decimal)
    for trade in trades:
        by_year[trade.opened_at.year] += trade.r_multiple
    total = sum(by_year.values(), Decimal(0))
    ranked = sorted(by_year.items(), key=lambda item: -item[1])
    top_two = sum(r for _, r in ranked[:2])
    return {
        "total_r": str(total.quantize(Decimal("0.01"))),
        "years": len(by_year),
        "by_year": {str(y): str(r.quantize(Decimal("0.01"))) for y, r in sorted(by_year.items())},
        "best_two_years": [str(y) for y, _ in ranked[:2]],
        "best_two_share_pct": str((top_two / total * 100).quantize(Decimal(1))) if total else None,
        "losing_years": [str(y) for y, r in sorted(by_year.items()) if r < 0],
    }


def build_report(calibration: Path, catalog_path: str) -> dict[str, Any]:
    """
    Run every activation and price it at each candidate coefficient.
    """
    spreads = measured_spreads(calibration)
    symbols: dict[str, Any] = {}

    for activation in load_activations():
        if activation.symbol not in spreads:
            continue
        trades, gross = scored_trades(activation, catalog_path)
        recent = [t for t in trades if t.opened_at.year >= 2021]  # noqa: PLR2004
        choices: dict[str, Any] = {}
        for choice, bps in spreads[activation.symbol].items():
            spread, comm = cost_in_r(trades, activation.symbol, bps)
            total = spread + comm
            choices[choice] = {
                "bps_per_side": str(bps),
                "spread_r": str(spread.quantize(Decimal("0.0001"))),
                "commission_r": str(comm.quantize(Decimal("0.0001"))),
                "total_r": str(total.quantize(Decimal("0.0001"))),
                "net_r": str((gross - total).quantize(Decimal("0.0001"))),
                "edge_remaining_pct": str(((gross - total) / gross * 100).quantize(Decimal(1)))
                if gross
                else None,
            }
        symbols[activation.symbol] = {
            "gross_r": str(gross.quantize(Decimal("0.0001"))),
            "trades": len(trades),
            "gross_r_since_2021": str(
                (sum((t.r_multiple for t in recent), Decimal(0)) / len(recent)).quantize(
                    Decimal("0.0001"),
                ),
            )
            if recent
            else None,
            "recent_trades": len(recent),
            "choices": choices,
            "concentration": concentration(trades),
        }

    return {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "calibration_source": calibration.name,
        "commission_model": "IB Pro fixed tier, USD 0.005/share, USD 1.00 minimum, 1% cap",
        "caveats": [
            (
                "Spreads are measured in 2026 and applied to trades from 2006 onward. "
                "Large-cap spreads were several times wider early in that window, so cost "
                "is understated for the early years at every coefficient."
            ),
            (
                "Spreads are measured on delayed quotes, which is an upper bound on the "
                "realtime NBBO and therefore conservative in the other direction."
            ),
            (
                "Per-share commission is charged on split-corrected share counts; see the "
                "module docstring."
            ),
        ],
        "symbols": symbols,
    }


def main(argv: list[str] | None = None) -> int:
    """
    Print the cost impact table.

    Returns a process exit code.

    """
    parser = argparse.ArgumentParser(
        prog="python -m copilot.calibration.cost_impact",
        description="Price the gate's scored trades at each candidate spread coefficient.",
    )
    add_catalog_argument(parser)
    parser.add_argument(
        "--calibration",
        type=Path,
        default=max(OUT_DIR.glob("spread_snapshot_*.json"), default=None)
        if OUT_DIR.exists()
        else None,
        help="Spread snapshot to price against (default: the most recent)",
    )
    parser.add_argument("--write", action="store_true", help="File the report as JSON")
    args = parser.parse_args(argv)

    if args.calibration is None or not args.calibration.exists():
        print("error: no spread snapshot found; run the calibrator first")
        return 2

    report = build_report(args.calibration, args.catalog)

    for choice in ("median", "p75", "p95", "incumbent"):
        print(f"\n-- {choice} --")
        header = f"{'sym':<6}{'bps/side':>10}{'spread R':>11}{'comm R':>10}"
        print(f"{header}{'total R':>10}{'gross R':>11}{'net R':>11}{'left':>8}")
        for symbol, data in report["symbols"].items():
            c = data["choices"][choice]
            print(
                f"{symbol:<6}{c['bps_per_side'][:8]:>10}{c['spread_r']:>11}{c['commission_r']:>10}"
                f"{c['total_r']:>10}{data['gross_r']:>11}{c['net_r']:>11}"
                f"{c['edge_remaining_pct'] + '%':>8}",
            )

    print("\n-- concentration: how few years carry the result --")
    print(f"{'sym':<6}{'total R':>10}{'best 2 years':>16}{'their share':>13}{'since 2021':>12}")
    for symbol, data in report["symbols"].items():
        con = data["concentration"]
        print(
            f"{symbol:<6}{con['total_r']:>10}{','.join(con['best_two_years']):>16}"
            f"{con['best_two_share_pct'] + '%':>13}{data['gross_r_since_2021'] or 'n/a':>12}",
        )

    if args.write:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
        path = OUT_DIR / f"cost_impact_{stamp}.json"
        path.write_text(json.dumps(report, indent=2) + "\n")
        print(f"\nWrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
