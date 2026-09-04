"""
Run the validation gate for a registered activation, and record the verdict.

    python -m copilot.strategies.validate aapl-gap-fade-long
    python -m copilot.strategies.validate --all --write

This is what ADR-0005 was for. Before it, the search space lived in a scratch file and a
verdict could not be reproduced from a commit by anyone, including whoever produced it.

Reads only. It runs a backtest over stored bars and writes a JSON record; it constructs no
execution client and cannot place an order whatever an activation's lifecycle says.

What the verdict is
-------------------
**Net of costs, as of [ADR-0011].** Every fold score charges the measured spread at p95
per side and IB's commission schedule against each replayed trade, through the gate's own
objective - so the in-sample search selects parameters that survive costs, not merely
ones that win gross. The record carries the snapshot name, the percentile and the
coefficient it was charged, so a number ties to its exact cost basis the way it ties to
a commit.

**Over the development window only, as of [ADR-0012].** The most recent slice of history
(from 2022-01-01) is the locked holdout: `carve` withholds it before the walk-forward
ever sees a bar, and the record names what was withheld. The window also has a far end
(2026-01-01, [ADR-0017]): the catalog is kept fresh for the live path, and bars past the
pin are clipped here rather than scored, so a backfill cannot move a verdict.

What the verdict still is not: the holdout result itself (walk-forward is repeatable;
the single-use out-of-sample is not, and spending it is a deliberate separate act -
`spend_holdout`, [ADR-0014]), and not
a viability judgment at the target account size, which is [ADR-0009]'s sweep - costs
here are charged on the trades as replayed, at the research sizing.

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

from copilot.calibration.cost_model import CostModel
from copilot.data.catalog import bar_type_for
from copilot.data.catalog import equity_for
from copilot.data.catalog import open_catalog
from copilot.data.catalog import read_daily_bars
from copilot.strategies.activations import Activation
from copilot.strategies.activations import find_activation
from copilot.strategies.activations import load_activations
from copilot.strategies.spend_holdout import is_spent
from copilot.validation.holdout import EVALUATION_END
from copilot.validation.holdout import HOLDOUT_START
from copilot.validation.holdout import carve
from copilot.validation.nautilus_replay import make_replay
from copilot.validation.walkforward import walk_forward


OUT_DIR = Path(__file__).parent / "verdicts"
DEFAULT_CATALOG = "~/.nautilus_copilot/catalog"


@dataclass(frozen=True)
class Verdict:
    """
    One activation's walk-forward result, in a shape that survives being filed.
    """

    activation: Activation
    report: Any
    bars: int
    first_bar: str
    last_bar: str
    holdout_bars: int
    holdout_range: tuple[str, str]
    unevaluated_bars: int
    seconds: float
    cost_model: CostModel

    def as_record(self) -> dict[str, Any]:
        """
        Return the JSON form written to disk.
        """
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
            # window over the same bars is a different experiment. These name the
            # development window the gate actually saw; the holdout block below names
            # what was withheld from it.
            "bars": self.bars,
            "bar_range": [self.first_bar, self.last_bar],
            "holdout": {
                "start": HOLDOUT_START.date().isoformat(),
                "bars_reserved": self.holdout_bars,
                "range": list(self.holdout_range),
            },
            # The window is pinned at both ends (ADR-0017), so the catalog may hold bars
            # this run never saw. Recorded because a verdict naming only its start would
            # read, to anyone comparing it against a later catalog, as a run over
            # everything available.
            "evaluation_window": {
                "end": EVALUATION_END.date().isoformat(),
                "bars_beyond": self.unevaluated_bars,
            },
            "search_space": {
                k: [str(x) for x in v] for k, v in self.activation.setup.search_space.items()
            },
            "seeded_parameters": {k: str(v) for k, v in self.activation.parameters.items()},
            "validation": vars(self.activation.validation),
            "warmup_bars": self.activation.setup.warmup_bars,
            "costs_modelled": True,
            "cost_model": self.cost_model.as_record(self.activation.symbol),
            # True from the moment a record exists under strategies/holdouts/ (ADR-0014):
            # a walk-forward re-run after the spend is a development run on a premise
            # whose one-time test has been used, and the record must say so.
            "holdout_spent": is_spent(self.activation.name),
            "folds": len(self.report.folds),
            "folds_evaluated": len(evaluated),
            "folds_passed": self.report.passed_count,
            "majority_passed": self.report.majority_passed,
            "mean_oos_net_expectancy_r": str(
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
                    "net_score_r": str(f.test_score.quantize(Decimal("0.000001"))),
                    "passed": f.passed,
                    "reason": f.reason,
                }
                for f in self.report.folds
            ],
        }


def run(
    activation: Activation,
    catalog_path: str = DEFAULT_CATALOG,
    cost_model: CostModel | None = None,
) -> Verdict:
    """
    Run the gate for one activation over stored bars, net of the pinned cost model.
    """
    if cost_model is None:
        cost_model = CostModel.from_snapshot()
    catalog = open_catalog(catalog_path)
    instrument = equity_for(activation.symbol, activation.venue)
    bar_type = bar_type_for(instrument.id)
    bars = read_daily_bars(catalog, bar_type)
    if not bars:
        raise ValueError(
            f"no bars in the catalog for {instrument.id}. Backfill it first: "
            f"python -m copilot.data.backfill --symbols {activation.symbol} --from 2005-01-01",
        )

    # The carve happens here, before the gate sees a bar: everything downstream of this
    # line runs on the development window alone (ADR-0012), clipped to the evaluation
    # window (ADR-0017) so a catalog kept fresh for the live path scores nothing new.
    carved = carve(bars)

    settings = activation.validation
    replay = make_replay(
        instrument=instrument,
        bar_type=bar_type,
        strategy_factory=activation.setup.factory,
    )

    started = time.time()
    report = walk_forward(
        carved.development,
        activation.grid(),
        train_bars=settings.train_bars,
        test_bars=settings.test_bars,
        purge_bars=settings.purge_bars,
        warmup_bars=activation.setup.warmup_bars,
        replay=replay,
        objective=cost_model.net_expectancy_for(activation.symbol),
        min_trades=settings.min_trades,
        fold_min_trades=settings.fold_min_trades,
    )
    return Verdict(
        activation=activation,
        report=report,
        bars=len(carved.development),
        first_bar=carved.development[0].closed_at.date().isoformat(),
        last_bar=carved.development[-1].closed_at.date().isoformat(),
        holdout_bars=len(carved.holdout),
        holdout_range=(
            carved.holdout[0].closed_at.date().isoformat(),
            carved.holdout[-1].closed_at.date().isoformat(),
        ),
        unevaluated_bars=len(carved.unevaluated),
        seconds=time.time() - started,
        cost_model=cost_model,
    )


def _print(verdict: Verdict) -> None:
    record = verdict.as_record()
    print(
        f"{record['activation']:<24} {record['instrument']:<11} "
        f"{record['folds_passed']}/{record['folds_evaluated']} folds  "
        f"mean OOS net {record['mean_oos_net_expectancy_r'] or 'n/a':>10} R  "
        f"{record['total_test_trades']:>5} trades  "
        f"majority={record['majority_passed']}  ({record['runtime_seconds']}s)",
        flush=True,
    )


def main(argv: list[str] | None = None) -> int:
    """
    Validate one activation or all of them.

    Returns a process exit code.

    """
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

    cost_model = CostModel.from_snapshot()
    print(
        f"Net of costs: spread at {cost_model.percentile} per side from "
        f"{cost_model.snapshot}, plus IB commission.\n"
        f"Development window only: bars from {HOLDOUT_START.date().isoformat()} are the "
        f"locked, unspent holdout (ADR-0012).\n",
    )
    for activation in activations:
        verdict = run(activation, args.catalog, cost_model)
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
