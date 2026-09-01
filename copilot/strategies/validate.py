"""
Run the validation gate for a registered activation, and record the verdict.

    python -m copilot.strategies.validate aapl-gap-fade-long
    python -m copilot.strategies.validate --all --write

This is what ADR-0005 was for. Before it, the search space lived in a scratch file and a
verdict could not be reproduced from a commit by anyone, including whoever produced it.

Reads only. It runs a backtest over stored bars and writes a JSON record; it constructs no
execution client and cannot place an order whatever an activation's lifecycle says.

What the verdict is not
-----------------------
The gate scores against whatever cost model the replay is given. **No fee or fill model is
supplied yet**, so the engine charges neither commission nor spread and every number below
is gross of costs. trade-copilot's own analysis names the cost model as the number that
decides every verdict, so these are a measurement of the machinery rather than of an edge.
The record carries `costs_modelled: false` so a file cannot be read later as more than it
was.

This is also not the holdout. Walk-forward is repeatable; the single-use out-of-sample is
not, and spending it is a deliberate separate act.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from copilot.data.catalog import bar_type_for
from copilot.data.catalog import equity_for
from copilot.data.catalog import open_catalog
from copilot.data.catalog import read_daily_bars
from copilot.strategies.activations import Activation
from copilot.strategies.activations import find_activation
from copilot.strategies.activations import load_activations
from copilot.validation.nautilus_replay import make_replay
from copilot.validation.walkforward import walk_forward


OUT_DIR = Path(__file__).parent / "verdicts"
DEFAULT_CATALOG = "~/.nautilus_copilot/catalog"


@dataclass(frozen=True)
class Verdict:
    """One activation's walk-forward result, in a shape that survives being filed."""

    activation: Activation
    report: Any
    bars: int
    first_bar: str
    last_bar: str
    seconds: float

    def as_record(self) -> dict[str, Any]:
        """Return the JSON form written to disk."""
        evaluated = self.report.evaluated
        scores = [f.test_score for f in evaluated]
        return {
            "activation": self.activation.name,
            "strategy": self.activation.strategy,
            "lifecycle": str(self.activation.lifecycle),
            "instrument": f"{self.activation.symbol}.{self.activation.venue}",
            "run_at": datetime.now(tz=UTC).isoformat(),
            "runtime_seconds": round(self.seconds, 1),
            # Recorded so a verdict can be tied back to the exact experiment. A different
            # window over the same bars is a different experiment.
            "bars": self.bars,
            "bar_range": [self.first_bar, self.last_bar],
            "search_space": {
                k: [str(x) for x in v] for k, v in self.activation.setup.search_space.items()
            },
            "seeded_parameters": {k: str(v) for k, v in self.activation.parameters.items()},
            "validation": vars(self.activation.validation),
            "warmup_bars": self.activation.setup.warmup_bars,
            "costs_modelled": False,
            "holdout_spent": False,
            "folds": len(self.report.folds),
            "folds_evaluated": len(evaluated),
            "folds_passed": self.report.passed_count,
            "majority_passed": self.report.majority_passed,
            "mean_oos_expectancy_r": str(
                (sum(scores, Decimal(0)) / len(scores)).quantize(Decimal("0.000001")),
            )
            if scores
            else None,
            "total_test_trades": sum(f.test_trades for f in evaluated),
            "fold_detail": [
                {
                    "index": f.index,
                    "test_from": f.test_from.date().isoformat(),
                    "test_to": f.test_to.date().isoformat(),
                    "selected": {k: str(v) for k, v in dict(f.selected.parameters).items()}
                    if f.selected
                    else None,
                    "trades": f.test_trades,
                    "score_r": str(f.test_score.quantize(Decimal("0.000001"))),
                    "passed": f.passed,
                    "reason": f.reason,
                }
                for f in self.report.folds
            ],
        }


def run(activation: Activation, catalog_path: str = DEFAULT_CATALOG) -> Verdict:
    """Run the gate for one activation over stored bars."""
    catalog = open_catalog(catalog_path)
    instrument = equity_for(activation.symbol, activation.venue)
    bar_type = bar_type_for(instrument.id)
    bars = read_daily_bars(catalog, bar_type)
    if not bars:
        raise ValueError(
            f"no bars in the catalog for {instrument.id}. Backfill it first: "
            f"python -m copilot.data.backfill --symbols {activation.symbol} --from 2005-01-01",
        )

    settings = activation.validation
    replay = make_replay(
        instrument=instrument,
        bar_type=bar_type,
        strategy_factory=activation.setup.factory,
    )

    started = time.time()
    report = walk_forward(
        bars,
        activation.grid(),
        train_bars=settings.train_bars,
        test_bars=settings.test_bars,
        purge_bars=settings.purge_bars,
        warmup_bars=activation.setup.warmup_bars,
        replay=replay,
        min_trades=settings.min_trades,
        fold_min_trades=settings.fold_min_trades,
    )
    return Verdict(
        activation=activation,
        report=report,
        bars=len(bars),
        first_bar=bars[0].closed_at.date().isoformat(),
        last_bar=bars[-1].closed_at.date().isoformat(),
        seconds=time.time() - started,
    )


def _print(verdict: Verdict) -> None:
    record = verdict.as_record()
    print(
        f"{record['activation']:<24} {record['instrument']:<11} "
        f"{record['folds_passed']}/{record['folds_evaluated']} folds  "
        f"mean OOS {record['mean_oos_expectancy_r'] or 'n/a':>10} R  "
        f"{record['total_test_trades']:>5} trades  "
        f"majority={record['majority_passed']}  ({record['runtime_seconds']}s)",
        flush=True,
    )


def main(argv: list[str] | None = None) -> int:
    """Validate one activation or all of them. Returns a process exit code."""
    parser = argparse.ArgumentParser(
        prog="python -m copilot.strategies.validate",
        description="Run the walk-forward gate for a registered activation.",
    )
    parser.add_argument("activation", nargs="?", help="Activation name, or use --all")
    parser.add_argument("--all", action="store_true", help="Validate every activation")
    parser.add_argument("--catalog", default=DEFAULT_CATALOG, help="Catalog directory")
    parser.add_argument(
        "--write",
        action="store_true",
        help="File the verdict as JSON under strategies/verdicts/",
    )
    args = parser.parse_args(argv)

    if args.all:
        activations = load_activations()
    elif args.activation:
        activations = (find_activation(args.activation),)
    else:
        parser.error("name an activation or pass --all")

    print("Gross of costs: no fee or fill model is supplied yet. Not the holdout.\n")
    for activation in activations:
        verdict = run(activation, args.catalog)
        _print(verdict)
        if args.write:
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
            path = OUT_DIR / f"{activation.name}_{stamp}.json"
            path.write_text(json.dumps(verdict.as_record(), indent=2) + "\n")
            print(f"    filed {path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
