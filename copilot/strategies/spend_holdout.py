"""
Spend the locked holdout on one activation - once, deliberately, and on the record.

    python -m copilot.strategies.spend_holdout <activation> --confirm <activation>

This is the charter's single-use out-of-sample test. Walk-forward verdicts are repeatable
and free; this is neither. Everything about the command is shaped by that asymmetry.

What it refuses, before touching a bar
--------------------------------------
- **A `signal_close` activation.** Only `next_close` may spend a holdout
  ([ADR-0013]): the optimistic bound is diagnostic, and a one-time test burned on fill
  semantics the charter rejects is a one-time test wasted.
- **An activation already spent.** The record under ``holdouts/`` is the marker; its
  existence is the refusal. Once viewed, the holdout is development data for every
  future decision, and there is no partial reopening.
- **A dirty working tree.** The record names the commit it was made from, and a number
  that cannot be tied to a commit cannot be tied to an experiment.
- **A missing or mismatched ``--confirm``.** Retyping the activation's name is the cost
  of making an irreversible measurement, and it is cheap on purpose - the point is that
  it cannot happen by tab-completing the wrong name.

What it does not decide
-----------------------
The verdict is a measurement. The decision - reject, revise as a new experiment, or
freeze - is the owner's, recorded by filling ``owner_decision`` in the written record in
a follow-up commit. The tool exits 0 whenever it wrote a record, pass or fail: a premise
failing its holdout is a result, not a malfunction.

[ADR-0013]: ../docs/decisions/0013-entry-timing-is-evaluated-as-a-bracket.md
[ADR-0014]: ../docs/decisions/0014-the-holdout-is-spent-as-one-more-fold.md
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict
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
from copilot.validation.holdout import HOLDOUT_START
from copilot.validation.holdout import carve
from copilot.validation.nautilus_replay import make_replay
from copilot.validation.spend import HoldoutResult
from copilot.validation.spend import spend_holdout


SPENT_DIR = Path(__file__).parent / "holdouts"
"""
One record per spent activation.

Existence is the single-use guard.

"""

VERDICTS_DIR = Path(__file__).parent / "verdicts"
DEFAULT_CATALOG = "~/.nautilus_copilot/catalog"

SPENDABLE_ENTRY_TIMING = "next_close"
"""
The only timing mode a holdout may be spent on (ADR-0013).
"""

OWNER_DECISIONS = ("reject", "revise", "freeze")
"""
The charter's three, and only three, outcomes at a gate.
"""

_SIX = Decimal("0.000001")


def is_spent(activation_name: str, spent_dir: Path = SPENT_DIR) -> bool:
    """
    Whether this activation's holdout has already been spent.
    """
    return (spent_dir / f"{activation_name}.json").exists()


def refusal(activation: Activation, spent_dir: Path = SPENT_DIR) -> str | None:
    """
    Return why this activation may not spend its holdout, or None if it may.

    Pure, so the rules are testable without a catalog or a git checkout.

    """
    timing = str(activation.parameters.get("entry_timing", "signal_close"))
    if timing != SPENDABLE_ENTRY_TIMING:
        return (
            f"activation {activation.name!r} runs at entry_timing={timing!r}; only "
            f"{SPENDABLE_ENTRY_TIMING!r} may spend a holdout (ADR-0013). Spending it on "
            f"the diagnostic bound would burn the one-time test on fill semantics the "
            f"charter rejects."
        )
    if is_spent(activation.name, spent_dir):
        return (
            f"activation {activation.name!r} has already spent its holdout: "
            f"{spent_dir / (activation.name + '.json')} exists. Once viewed, the holdout "
            f"is development data; there is no second spend and no partial reopening."
        )
    return None


def _git(*args: str) -> str:
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", *args],  # noqa: S607 - git resolved from PATH by design
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"git {' '.join(args)} failed")
    return completed.stdout.strip()


def working_tree_is_clean() -> bool:
    """
    Whether the record can be tied to ``HEAD`` exactly.
    """
    return _git("status", "--porcelain", "--untracked-files=no") == ""


def latest_verdict_for(activation_name: str, verdicts_dir: Path = VERDICTS_DIR) -> str | None:
    """
    Name the newest walk-forward record this spend is answering, if one exists.
    """
    files = sorted(verdicts_dir.glob(f"{activation_name}_*.json"))
    return files[-1].name if files else None


def run(
    activation: Activation,
    catalog_path: str = DEFAULT_CATALOG,
    cost_model: CostModel | None = None,
) -> tuple[HoldoutResult, dict[str, Any]]:
    """
    Spend the holdout for one activation and return the result with its record.

    Refusals are the caller's job; this runs whatever it is given.

    """
    if cost_model is None:
        cost_model = CostModel.from_snapshot()
    catalog = open_catalog(catalog_path)
    instrument = equity_for(activation.symbol, activation.venue)
    bar_type = bar_type_for(instrument.id)
    bars = read_daily_bars(catalog, bar_type)
    if not bars:
        raise ValueError(f"no bars in the catalog for {instrument.id}")

    carved = carve(bars)
    settings = activation.validation
    replay = make_replay(
        instrument=instrument,
        bar_type=bar_type,
        strategy_factory=activation.setup.factory,
    )

    started = time.time()
    result = spend_holdout(
        carved,
        activation.grid(),
        purge_bars=settings.purge_bars,
        warmup_bars=activation.setup.warmup_bars,
        replay=replay,
        objective=cost_model.net_expectancy_for(activation.symbol),
        min_trades=settings.min_trades,
        fold_min_trades=settings.fold_min_trades,
    )
    seconds = time.time() - started
    return result, holdout_record(
        activation,
        result,
        cost_model=cost_model,
        seconds=seconds,
        code_commit=_git("rev-parse", "HEAD"),
        walk_forward_reference=latest_verdict_for(activation.name),
    )


def holdout_record(
    activation: Activation,
    result: HoldoutResult,
    *,
    cost_model: CostModel,
    seconds: float,
    code_commit: str,
    walk_forward_reference: str | None,
) -> dict[str, Any]:
    """
    Return the JSON form of a spend: enough to reproduce it, and the audit behind it.

    Pure, so the record's shape is testable without running anything.

    """
    fold = result.fold
    in_sample = fold.in_sample
    return {
        "activation": activation.name,
        "strategy": activation.strategy,
        "lifecycle": str(activation.lifecycle),
        "instrument": f"{activation.symbol}.{activation.venue}",
        "entry_timing": str(activation.parameters.get("entry_timing", "signal_close")),
        "run_at": datetime.now(tz=UTC).isoformat(),
        "runtime_seconds": round(seconds, 1),
        "code_commit": code_commit,
        "holdout_spent": True,
        "owner_decision": None,
        "owner_decision_allowed": list(OWNER_DECISIONS),
        "walk_forward_reference": walk_forward_reference,
        "windows": {
            "development_bars": result.development_bars,
            "development_range": [
                fold.train_from.date().isoformat(),
                fold.test_from.date().isoformat(),
            ],
            "purge_bars": result.purge_bars,
            "warmup_bars": result.warmup_bars,
            "holdout_start": HOLDOUT_START.date().isoformat(),
            "holdout_bars": result.holdout_bars,
            "holdout_range": [fold.test_from.date().isoformat(), fold.test_to.date().isoformat()],
        },
        "search_space": {k: [str(x) for x in v] for k, v in activation.setup.search_space.items()},
        "seeded_parameters": {k: str(v) for k, v in activation.parameters.items()},
        "validation": vars(activation.validation),
        "costs_modelled": True,
        "cost_model": cost_model.as_record(activation.symbol),
        "frozen_parameters": {k: str(v) for k, v in (result.frozen_parameters or {}).items()}
        or None,
        "selection_audit": {
            "selected_the_peak": in_sample.selected_the_peak,
            "candidates": [
                {
                    "version": c.version,
                    "parameters": {k: str(v) for k, v in dict(c.parameters).items()},
                    "trades": c.trades,
                    "score_r": str(c.score.quantize(_SIX)),
                    "plateau_floor_r": str(in_sample.plateau_scores[c.version].quantize(_SIX)),
                    "rejected": in_sample.rejections.get(c.version),
                }
                for c in in_sample.candidates
            ],
        },
        "holdout": {
            "trades": result.trades,
            "net_expectancy_r": str(result.score.quantize(_SIX)),
            "passed": result.passed,
            "reason": fold.reason,
            "tearsheet": {
                k: (str(v) if v is not None else None) for k, v in asdict(result.tearsheet).items()
            },
        },
    }


def main(argv: list[str] | None = None) -> int:
    """
    Spend one activation's holdout, or explain exactly why not.

    Exit 0 when a record was written, whatever it says. Exit 2 on any refusal.

    """
    parser = argparse.ArgumentParser(
        prog="python -m copilot.strategies.spend_holdout",
        description="Spend the locked holdout on one activation. Once.",
    )
    parser.add_argument("activation", help="Activation name")
    parser.add_argument(
        "--confirm",
        required=True,
        help="The activation name again, retyped. Irreversible measurements are not tab-completed.",
    )
    parser.add_argument("--catalog", default=DEFAULT_CATALOG, help="Catalog directory")
    args = parser.parse_args(argv)

    if args.confirm != args.activation:
        print(f"refused: --confirm {args.confirm!r} does not match {args.activation!r}")
        return 2

    activation = find_activation(args.activation)
    why_not = refusal(activation)
    if why_not:
        print(f"refused: {why_not}")
        return 2
    if not working_tree_is_clean():
        print(
            "refused: the working tree has uncommitted changes. A spend is tied to the commit "
            "it was made from; commit or stash first.",
        )
        return 2

    cost_model = CostModel.from_snapshot()
    print(
        f"Spending the holdout for {activation.name} ({activation.symbol}.{activation.venue}), "
        f"bars from {HOLDOUT_START.date().isoformat()}.\n"
        f"Net of costs: spread at {cost_model.percentile} per side from "
        f"{cost_model.snapshot}, plus IB commission.\n",
    )
    result, record = run(activation, args.catalog, cost_model)

    frozen = record["frozen_parameters"]
    print(f"frozen parameters: {frozen or 'none selected'}")
    print(
        f"holdout: {result.trades} trades  net {record['holdout']['net_expectancy_r']} R  "
        f"{'PASS' if result.passed else 'FAIL'}  ({fold_reason(result)})",
    )

    SPENT_DIR.mkdir(parents=True, exist_ok=True)
    path = SPENT_DIR / f"{activation.name}.json"
    path.write_text(json.dumps(record, indent=2) + "\n")
    print(f"\nWrote {path}")
    print(
        "The holdout is now spent and is development data for every future decision. "
        "Record the owner's decision (reject, revise, freeze) in that file in a follow-up "
        "commit.",
    )
    return 0


def fold_reason(result: HoldoutResult) -> str:
    """
    Return the evaluator's own words for the verdict.
    """
    return result.fold.reason


if __name__ == "__main__":
    sys.exit(main())
