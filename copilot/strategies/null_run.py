"""
Run a premise against the randomised-signal control, and file what it says.

    python -m copilot.strategies.null_run spy-turn-of-month
    python -m copilot.strategies.null_run spy-turn-of-month --write --replicates 500

Read-only against the market: it replays stored bars and writes a JSON record. It constructs no
execution client and cannot place an order.

What it compares, and why that shape
------------------------------------
The premise is run **once over the whole development window** at its activation's own seeded
parameters, and the control then draws the same number of entry sessions at random from that same
window, running the same rule over the same bars with the same stop, sizing and costs. Only the
entry dates differ, which is what makes the comparison a test of *timing*.

That is deliberately not the walk-forward's number. The gate scores test windows under
per-fold selections, so its mean mixes several parameter sets over several windows; a single null
matched against it would compare two different things. The verdict's fold mean is carried in the
record for context, and the control's reference is the single-window run beside it.

Why any of this exists
----------------------
[ADR-0026](../docs/decisions/0026-attribution-is-measured-per-trade.md)'s per-trade attribution
cannot credit timing: a long position in a broad ETF returns a market loading near one and an
alpha near zero however well its entries were chosen. The playbook has always required a
randomised-signal baseline; :mod:`copilot.validation.null_control` is it, and this is the runner
that points it at a premise.

**A premise whose entry sessions are not a rule cannot be controlled this way.** The strategy has
to accept an entry schedule - ``entry_sessions`` on the turn-of-month rule - or there is nothing
to randomise.

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
from typing import Any

from copilot.calibration.cost_model import CostModel
from copilot.data.catalog import bar_type_for
from copilot.data.catalog import equity_for
from copilot.data.catalog import read_series
from copilot.paths import add_catalog_argument
from copilot.strategies.activations import find_activation
from copilot.validation.evidence import net_r
from copilot.validation.holdout import carve
from copilot.validation.nautilus_replay import run_nautilus_replay
from copilot.validation.null_control import DEFAULT_REPLICATES
from copilot.validation.null_control import run_null_control


if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from copilot.strategies.activations import Activation
    from copilot.validation.types import DailyBar


OUT_DIR = Path(__file__).parent / "out"

MIN_SCOREABLE_TRADES = 20
"""
Trades a replicate needs before its mean counts.

A draw that produced a handful has a mean dominated by one trade, and averaging those in
would narrow the null distribution with noise. Below this the replicate is skipped and
counted, which is what the control's withheld reading watches.

"""


@dataclass(frozen=True)
class PremiseRun:
    """
    The premise over the whole development window, at its own seeded parameters.
    """

    mean_r: Decimal
    trades: int
    first: date
    last: date


def development_bars(activation: Activation, catalog_path: str) -> tuple[DailyBar, ...]:
    """
    Return the bars the gate saw: the evaluation window, less this activation's holdout.

    Through :func:`carve`, the same call the gate makes, because an activation that declares no
    boundary of its own inherits the **shared pin** of ADR-0012 rather than having no holdout at
    all. A first version of this read the activation's own boundary and
    treated ``None`` as "nothing withheld": it ran the control over 2005-2025 on an activation
    carved at 2022-01-01, which is a look at a locked holdout
    (``experiments/EXP-2026-001-turn-of-month.md`` records it). Reusing the gate's own carve is
    what makes that unrepeatable.

    """
    bars = read_series(catalog_path, activation.symbol, activation.venue)
    return carve(bars, holdout_start=activation.validation.holdout_boundary).development


def score(trades: Sequence[Any], symbol: str, model: CostModel) -> Decimal | None:
    """
    Return the mean net R of a run's trades, or None when there are too few.
    """
    series = net_r(trades, cost_r=lambda trade: model.cost_r(trade, symbol))
    if len(series) < MIN_SCOREABLE_TRADES:
        return None
    return sum(series, Decimal(0)) / Decimal(len(series))


def run_premise(
    activation: Activation,
    bars: Sequence[DailyBar],
    model: CostModel,
) -> PremiseRun:
    """
    Run the premise once over the whole window, at the activation's seeded parameters.
    """
    instrument = equity_for(activation.symbol, activation.venue)
    result = run_nautilus_replay(
        bars,
        dict(activation.parameters),
        instrument=instrument,
        bar_type=bar_type_for(instrument.id),
        strategy_factory=activation.setup.factory,
    )
    mean = score(result.trades, activation.symbol, model)
    if mean is None:
        raise ValueError(
            f"{activation.name} produced {len(result.trades)} scoreable trades over its "
            f"development window, fewer than {MIN_SCOREABLE_TRADES}: there is nothing for a "
            f"control to be read against",
        )
    return PremiseRun(
        mean_r=mean,
        trades=len(result.trades),
        first=bars[0].closed_at.date(),
        last=bars[-1].closed_at.date(),
    )


def main(argv: list[str] | None = None) -> int:
    """
    Run one activation against the control, print the reading, and optionally file it.
    """
    parser = argparse.ArgumentParser(
        prog="python -m copilot.strategies.null_run",
        description="Read a premise against entry dates drawn at random from its own window.",
    )
    parser.add_argument("activation", help="Activation name")
    add_catalog_argument(parser)
    parser.add_argument("--replicates", type=int, default=DEFAULT_REPLICATES)
    parser.add_argument("--seed", type=int, default=20260911)
    parser.add_argument("--write", action="store_true", help="File the reading as JSON")
    args = parser.parse_args(argv)

    activation = find_activation(args.activation)
    model = CostModel.from_snapshot()
    bars = development_bars(activation, args.catalog)
    if not bars:
        print(f"error: no bars for {activation.name}", file=sys.stderr)
        return 2

    instrument = equity_for(activation.symbol, activation.venue)
    bar_type = bar_type_for(instrument.id)
    premise = run_premise(activation, bars, model)
    sessions = [bar.closed_at.date() for bar in bars]

    print(
        f"{activation.name}: {premise.trades} trades, mean {premise.mean_r:.6f} R net over "
        f"{premise.first}..{premise.last}, at its seeded parameters.\n"
        f"Drawing {args.replicates} sets of {premise.trades} entry sessions from the same "
        f"window; the rule, the stop, the sizing and the costs are unchanged.\n",
        flush=True,
    )

    def replicate(days: tuple[date, ...]) -> Decimal | None:
        result = run_nautilus_replay(
            bars,
            {**activation.parameters, "entry_sessions": ",".join(d.isoformat() for d in days)},
            instrument=instrument,
            bar_type=bar_type,
            strategy_factory=activation.setup.factory,
        )
        return score(result.trades, activation.symbol, model)

    control = run_null_control(
        replicate,
        sessions,
        entries=premise.trades,
        premise_mean_r=premise.mean_r,
        premise_trades=premise.trades,
        replicates=args.replicates,
        seed=args.seed,
    )

    record = control.as_record()
    print(
        f"  null mean      {record['null_mean_r']} R over {record['scored']} scored replicates\n"
        f"  premise        {record['premise_mean_r']} R over {record['premise_trades']} trades\n"
        f"  percentile     {record['percentile']}\n"
        f"  p (one-sided)  {record['p_value']} against {record['significance']}\n"
        f"  reading        {record['reading']}",
    )

    if args.write:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
        path = OUT_DIR / f"null_control_{activation.name}_{stamp}.json"
        path.write_text(
            json.dumps(
                {
                    "activation": activation.name,
                    "instrument": f"{activation.symbol}.{activation.venue}",
                    "run_at": datetime.now(tz=UTC).isoformat(),
                    "window": {
                        "first": premise.first.isoformat(),
                        "last": premise.last.isoformat(),
                        "sessions": len(sessions),
                    },
                    "parameters": {k: str(v) for k, v in sorted(activation.parameters.items())},
                    "cost_model": {"snapshot": model.snapshot, "percentile": model.percentile},
                    "min_scoreable_trades": MIN_SCOREABLE_TRADES,
                    **record,
                },
                indent=2,
            )
            + "\n",
        )
        print(f"\nfiled {path}")
    return 0


__all__ = ["MIN_SCOREABLE_TRADES", "PremiseRun", "development_bars", "run_premise", "score"]


if __name__ == "__main__":
    sys.exit(main())
