"""
Run one premise across the whole registered universe as a single experiment.

    python -m copilot.strategies.pool --timing next_close
    python -m copilot.strategies.pool --timing next_close --write

Nine separate walk-forwards over nine symbols are nine chances for a premise to look good
somewhere. This runs **one**: a single parameter set selected on every symbol's training
window at once and scored on every symbol's test window at once, so a fold is one decision
measured against nine instruments.

It exists because of what [ADR-0024](../docs/decisions/0024-a-holdout-pass-needs-an-interval.md)
found. AAPL's holdout returned +0.035 R over 111 trades with a 90% interval of
[-0.126, +0.209] R - not a failure, and not enough evidence to be a pass. The remedy the
playbook names for a sample that thin is to enlarge it, and pooling enlarges it sideways
without touching a spent holdout.

What this reports, and what it refuses to
-----------------------------------------
It reports a **pooled walk-forward verdict** and the evidence interval over the pooled
out-of-sample trades. That is clean: no holdout bar is read, and the folds are the same
geometry the single-symbol gate uses.

It does **not** spend a pooled holdout, and there is no flag to make it. AAPL's holdout
window has been viewed, so for that symbol those bars are development data; a pooled holdout
containing them would not be out of sample, and the honest options - exclude the spent
symbols, or accept a contaminated window and say so - are the owner's call. The refusal is
the point: a tool that quietly picked one would have decided it by default.

What a pooled pass would and would not mean
-------------------------------------------
Nine US equities on the same day are one market with nine labels, so the pooled trade count
is not nine times the evidence. The interval reported here measures that directly, and the
concurrency term will be well above one for the first time in this project - pooled trades
are frequently simultaneous, which is precisely the dependence
:mod:`copilot.validation.evidence` was written to catch.

"""

from __future__ import annotations

import argparse
import json
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
from copilot.paths import DEFAULT_CATALOG
from copilot.paths import add_catalog_argument
from copilot.strategies.activations import Activation
from copilot.strategies.activations import load_activations
from copilot.strategies.validate import stored_bars
from copilot.validation.evidence import assess
from copilot.validation.holdout import carve
from copilot.validation.nautilus_replay import make_replay
from copilot.validation.pooled import PooledMember
from copilot.validation.pooled import pooled_objective
from copilot.validation.pooled import pooled_replay
from copilot.validation.pooled import spine
from copilot.validation.walkforward import WalkForwardReport
from copilot.validation.walkforward import walk_forward


OUT_DIR = Path(__file__).parent / "out"

_SIX = Decimal("0.000001")


MIN_POOL_MEMBERS = 2
"""
One activation is not a pool, and calling it one would hide that nothing was pooled.
"""


class IncoherentPoolError(ValueError):
    """
    The chosen activations are not one premise, so pooling them means nothing.
    """


@dataclass(frozen=True)
class PooledVerdict:
    """
    One pooled walk-forward result, with the evidence its trades are worth.
    """

    members: tuple[Activation, ...]
    report: WalkForwardReport
    spine_bars: int
    first_bar: str
    last_bar: str
    seconds: float
    cost_model: CostModel
    from_year: int | None = None

    @property
    def trades(self) -> tuple[Any, ...]:
        """
        Every scored out-of-sample trade, in signal order across all folds.
        """
        scored = [t for fold in self.report.folds for t in fold.test_trade_details]
        scored.sort(key=lambda t: (t.signal_created_at, t.symbol))
        return tuple(scored)

    def as_record(self) -> dict[str, Any]:
        """
        Return the filed form, carrying enough to audit the pool as well as the verdict.
        """
        evidence = assess(self.trades)
        by_symbol: dict[str, int] = {}
        for trade in self.trades:
            by_symbol[trade.symbol] = by_symbol.get(trade.symbol, 0) + 1
        return {
            "run_at": datetime.now(UTC).isoformat(),
            "runtime_seconds": round(self.seconds, 1),
            "members": [a.name for a in self.members],
            "symbols": [a.symbol for a in self.members],
            "from_year": self.from_year,
            "spine_bars": self.spine_bars,
            "spine_range": [self.first_bar, self.last_bar],
            "folds": len(self.report.folds),
            "folds_evaluated": len(self.report.evaluated),
            "folds_passed": self.report.passed_count,
            "majority": self.report.majority_passed,
            "mean_oos_net_r": str(self.report.mean_score.quantize(_SIX)),
            "trades": len(self.trades),
            "trades_by_symbol": by_symbol,
            "symbols_per_fold": [
                len({t.symbol for t in fold.test_trade_details})
                for fold in self.report.folds
                if fold.selected is not None
            ],
            # Per fold, because "pooled over nine" is false for most of this run: the
            # universe's long-history names start in 2005 and two of the ETFs after 2017,
            # so a reader has to be able to ask whether the passes came from the wide
            # folds or from the three-symbol era that pooling was supposed to improve on.
            "fold_detail": [
                {
                    "index": fold.index,
                    "test_from": fold.test_from.date().isoformat(),
                    "test_to": fold.test_to.date().isoformat(),
                    "symbols": sorted({t.symbol for t in fold.test_trade_details}),
                    "trades": fold.test_trades,
                    "net_score_r": str(fold.test_score.quantize(_SIX)),
                    "passed": fold.passed,
                }
                for fold in self.report.folds
                if fold.selected is not None
            ],
            "evidence": {
                "effective_trades": str(evidence.effective_trades),
                "concurrency": str(evidence.concurrency),
                "block_trades": evidence.block_bars,
                "confidence": str(evidence.confidence),
                "lower_r": str(evidence.lower_r),
                "upper_r": str(evidence.upper_r),
                "standard_error_r": str(evidence.standard_error_r),
                "clears_zero": evidence.clears(Decimal(0)),
            },
            "cost_model": {
                "snapshot": self.cost_model.snapshot,
                "percentile": self.cost_model.percentile,
            },
            "holdout": (
                "not spent, and not spendable here. AAPL's holdout window has been viewed "
                "(ADR-0014), so a pooled holdout containing it is not out of sample. "
                "Excluding the spent symbols or accepting a contaminated window is an "
                "owner decision, not a default."
            ),
        }


def coherent(activations: list[Activation]) -> tuple[Activation, ...]:
    """
    Return the activations, or refuse if they are not one premise.

    Pooling assumes a single experiment. Activations that differ in strategy, entry
    timing or search space are different experiments, and averaging their trades
    produces a number that describes nothing.

    """
    if len(activations) < MIN_POOL_MEMBERS:
        raise IncoherentPoolError(
            f"a pool needs at least {MIN_POOL_MEMBERS} activations, got {len(activations)}",
        )

    def shape(a: Activation) -> tuple[Any, ...]:
        return (
            a.strategy,
            str(a.parameters.get("entry_timing", "")),
            tuple(sorted(a.grid().axes_by_name)),
        )

    shapes = {shape(a) for a in activations}
    if len(shapes) > 1:
        names = ", ".join(sorted(a.name for a in activations))
        raise IncoherentPoolError(
            f"these activations are not one premise and cannot be pooled: {names}. They "
            f"differ in strategy, entry timing or search space, so a pooled score would "
            f"average trades made under different rules.",
        )
    return tuple(sorted(activations, key=lambda a: a.symbol))


def run(
    activations: list[Activation],
    catalog_path: str = DEFAULT_CATALOG,
    cost_model: CostModel | None = None,
    from_year: int | None = None,
) -> PooledVerdict:
    """
    Build the pool, run one walk-forward over it, and score it net of costs.

    ``from_year`` clips every member to the same start so the pool's **composition** is
    constant across folds. Without it the first run over this universe was a three-symbol
    pool for 22 of its 38 folds and a nine-symbol pool for three of them, which makes a
    single verdict over the whole span an average of two different experiments.

    It is a window chosen from **data availability**, and choosing it from where the
    results improve would be the same mistake as picking a block length that flatters a
    series. The record carries the value so the choice is visible.

    """
    members_meta = coherent(activations)
    if cost_model is None:
        cost_model = CostModel.from_snapshot()

    members: list[PooledMember] = []
    for activation in members_meta:
        instrument = equity_for(activation.symbol, activation.venue)
        bars = stored_bars(activation, catalog_path)
        # Each symbol keeps its own holdout boundary (ADR-0020), and only its development
        # window enters the pool. A shared boundary would read a spent window for one
        # symbol or discard a usable one for another.
        carved = carve(bars, holdout_start=activation.validation.holdout_boundary)
        development = tuple(carved.development)
        if from_year is not None:
            development = tuple(b for b in development if b.closed_at.year >= from_year)
        members.append(
            PooledMember(
                symbol=activation.symbol,
                bars=development,
                replay=make_replay(
                    instrument=instrument,
                    bar_type=bar_type_for(instrument.id),
                    strategy_factory=activation.setup.factory,
                ),
            ),
        )

    axis = spine(members)
    reference = members_meta[0]
    settings = reference.validation

    started = time.time()
    report = walk_forward(
        axis,
        reference.grid(),
        train_bars=settings.train_bars,
        test_bars=settings.test_bars,
        purge_bars=settings.purge_bars,
        warmup_bars=reference.setup.warmup_bars,
        replay=pooled_replay(members),
        objective=pooled_objective(cost_model.cost_r),
        # Deliberately **not** scaled by the pool size. Scaling them by nine was the first
        # thing tried and it silently discarded 24 of 38 folds: the pool's composition
        # grows over time - three symbols have history from 2005 and two begin after 2017 -
        # so a floor set for nine members is unreachable in every early window. A verdict
        # computed over the folds that survive an eligibility rule invented after seeing
        # the data is not a verdict. These are the activation's own predeclared floors.
        min_trades=settings.min_trades,
        fold_min_trades=settings.fold_min_trades,
    )
    return PooledVerdict(
        members=members_meta,
        report=report,
        spine_bars=len(axis),
        first_bar=axis[0].closed_at.date().isoformat(),
        last_bar=axis[-1].closed_at.date().isoformat(),
        seconds=time.time() - started,
        cost_model=cost_model,
        from_year=from_year,
    )


def main(argv: list[str] | None = None) -> int:
    """
    Pool every activation of one timing mode and report the single verdict.
    """
    parser = argparse.ArgumentParser(
        prog="python -m copilot.strategies.pool",
        description="Run one premise across the registered universe as a single experiment.",
    )
    parser.add_argument(
        "--timing",
        default="next_close",
        help="Entry timing to pool; activations of other modes are different experiments",
    )
    parser.add_argument("--only", nargs="*", help="Symbols to include, default all")
    parser.add_argument(
        "--from-year",
        type=int,
        help="Start the spine here, to hold the pool's composition constant. Pick it "
        "from when the members' histories begin, never from where the results improve",
    )
    parser.add_argument("--write", action="store_true", help="File the verdict as JSON")
    add_catalog_argument(parser)
    args = parser.parse_args(argv)

    chosen = [
        a
        for a in load_activations()
        if str(a.parameters.get("entry_timing", "")) == args.timing
        and (not args.only or a.symbol in {s.upper() for s in args.only})
    ]
    try:
        verdict = run(chosen, args.catalog, from_year=args.from_year)
    except IncoherentPoolError as e:
        print(f"refused: {e}")
        return 2

    record = verdict.as_record()
    evidence = record["evidence"]
    print(
        f"pooled {len(record['symbols'])} symbols at entry_timing={args.timing}: "
        f"{', '.join(record['symbols'])}",
    )
    print(
        f"{record['folds_passed']}/{record['folds_evaluated']} folds  "
        f"mean OOS net {record['mean_oos_net_r']} R  "
        f"{record['trades']} trades  majority={record['majority']}  "
        f"({record['runtime_seconds']}s)",
    )
    print(
        f"evidence: {evidence['effective_trades']} effective trades  "
        f"concurrency {evidence['concurrency']}  "
        f"{Decimal(evidence['confidence']):%} interval "
        f"[{evidence['lower_r']}, {evidence['upper_r']}] R  "
        f"clears zero: {evidence['clears_zero']}",
    )
    print(
        "trades by symbol: "
        + ", ".join(f"{k}={v}" for k, v in sorted(record["trades_by_symbol"].items())),
    )
    print(f"\nholdout: {record['holdout']}")

    if args.write:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        window = f"_from{args.from_year}" if args.from_year else ""
        path = OUT_DIR / f"pooled_{args.timing}{window}_{stamp}.json"
        path.write_text(json.dumps(record, indent=2) + "\n")
        print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
