"""
Compare what a live session decided against an offline replay of the same bars.

    python -m copilot.live.compare                       # the newest session record
    python -m copilot.live.compare --record copilot/live/out/run_activation_<stamp>.json

The playbook's *After* checklist requires it and, until 2026-09-05, no tool did it: a live
session that silently decided differently from the backtest looked identical to one that
agreed. This is the *system correctness* scorecard's first line - "100% signal-to-intent
parity against an independent replay" - made into a morning command.

What is compared
----------------
A session record carries, per activation, the bars it warmed from, the bar it decided on,
the parameters it ran, the budget it sized against, and the decision the strategy filed
in its own words (``GapReversalStrategy.decision_record``). The offline side reads the
same bars back from the catalog, warms a fresh strategy the same way, hands it the
decision bar through the gate's own ``BacktestEngine``, and reads the same record off it.
Two records, one shape, field by field.

The tolerance is the session's own. Live bars are expressed at the broker's price
precision and catalog bars at the vendor's; the session recorded the largest rounding
that conversion made. A true range moves by at most twice that, and a Wilder-smoothed ATR
is a convex combination of true ranges, so the ATR is compared within twice the recorded
rounding and the previous close within it. Anything larger is a defect, not precision.

What it found the first time it ran
-----------------------------------
That the live path handed the decision bar to ``on_bar`` without updating the indicator
first, so the ATR the rule decided with stood one bar behind the replay's. The engine
updates registered indicators before it calls ``on_bar``; a bar handed to the strategy
directly skipped that, and nothing in nine sessions had said so. Fixed the same day
(``GapReversalStrategy.decide``), and this comparison is what keeps it fixed.

What it cannot compare
----------------------
The session-wide ledger. ``portfolio_risk_capped`` is decided by every strategy in the
node together, and a single-strategy replay has no ledger; a live refusal on that ground
counts as agreement when the offline rule wanted to enter, and the note says so. And
a position carried from a previous session: the live path decides on a flat book and so
does this replay, which is the open state of the live path and not a property of the
comparison.

"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC
from datetime import date
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from copilot.data.catalog import bar_type_for
from copilot.data.catalog import equity_for
from copilot.data.catalog import read_series
from copilot.paths import add_catalog_argument
from copilot.strategies.activations import find_activation
from copilot.strategies.gap_reversal import ENTRY_SUBMITTED
from copilot.validation.nautilus_replay import run_to_decision
from copilot.validation.types import DailyBar


OUT_DIR = Path(__file__).parent / "out"

SESSION_LEVEL_OUTCOMES = frozenset({"portfolio_risk_capped"})
"""
Outcomes decided by the session rather than by the rule, which no single replay can
reach.
"""

BOOLEAN_PARAMETERS = frozenset({"long", "require_unfilled"})
"""
Parameters the record stringifies and the factory reads with ``bool()``.

``bool("False")`` is true. A record replayed without re-typing these would run the long
leg for a short activation and report a disagreement that was the comparison's own.

"""


@dataclass(frozen=True)
class Disagreement:
    """
    One field on which the live session and the replay differ.
    """

    field: str
    live: str | None
    offline: str | None
    note: str = ""


@dataclass(frozen=True)
class Comparison:
    """
    One activation's live decision against its offline recomputation.
    """

    activation: str
    decision_bar: str
    live_outcome: str | None
    offline_outcome: str | None
    tolerance: str
    disagreements: tuple[Disagreement, ...] = ()
    note: str = ""

    @property
    def agrees(self) -> bool:
        """
        Whether every compared field matched within tolerance.
        """
        return not self.disagreements

    def as_record(self) -> dict[str, object]:
        """
        Return the JSON form filed under ``out/``.
        """
        return {
            "activation": self.activation,
            "decision_bar": self.decision_bar,
            "live_outcome": self.live_outcome,
            "offline_outcome": self.offline_outcome,
            "tolerance": self.tolerance,
            "agrees": self.agrees,
            "disagreements": [vars(d) for d in self.disagreements],
            "note": self.note,
        }


def latest_record(out_dir: Path = OUT_DIR) -> Path | None:
    """
    Return the newest session record, or None when no session has been filed.
    """
    paths = sorted(out_dir.glob("run_activation_*.json"))
    return paths[-1] if paths else None


def entries_of(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    """
    Return the per-activation entries of a session record, whichever shape it has.

    Records filed before the basket ran in one node hold a single activation at the top
    level; later ones hold a list. Both are comparable and both are on disk.

    """
    if "activations" in payload:
        return tuple(payload["activations"])
    return (payload,)


def window_bars(
    bars: tuple[DailyBar, ...],
    *,
    warmup_from: str,
    decision_bar: str,
) -> tuple[DailyBar, ...]:
    """
    Return the stored bars from the first warm-up session to the decision bar.

    Inclusive at both ends.

    """
    start, end = date.fromisoformat(warmup_from), date.fromisoformat(decision_bar)
    return tuple(
        sorted(
            (b for b in bars if start <= b.closed_at.date() <= end),
            key=lambda b: b.closed_at,
        ),
    )


def typed_parameters(recorded: Mapping[str, str], budget: Mapping[str, str]) -> dict[str, Any]:
    """
    Rebuild the factory's parameters from a record's strings and its budget.

    The budget's risk and notional cap replace the research R-unit, as ``size_against``
    did in the session, so the replay sizes the same trade rather than a research one.

    """
    parameters: dict[str, Any] = {}
    for key, value in recorded.items():
        parameters[key] = value == "True" if key in BOOLEAN_PARAMETERS else value
    if budget:
        parameters["risk_budget"] = budget["risk_budget"]
        parameters["max_notional"] = budget["max_notional"]
    return parameters


def offline_decision(entry: Mapping[str, Any], bars: tuple[DailyBar, ...]) -> dict[str, object]:
    """
    Recompute the decision the session filed, over ``bars``, through the gate's engine.
    """
    symbol, _, venue = str(entry["research_instrument"]).partition(".")
    instrument = equity_for(symbol, venue)
    activation = find_activation(str(entry["activation"]))
    return run_to_decision(
        bars,
        typed_parameters(entry["parameters"], entry.get("budget") or {}),
        instrument=instrument,
        bar_type=bar_type_for(instrument.id),
        strategy_factory=activation.setup.factory,
        warmup=len(bars) - 1,
        inspect=lambda strategy: strategy.decision_record(),
    )


def _within(left: str | None, right: str | None, tolerance: Decimal) -> bool:
    if left is None or right is None:
        return left is right
    return abs(Decimal(left) - Decimal(right)) <= tolerance


def compare_decisions(
    live: Mapping[str, Any],
    offline: Mapping[str, Any],
    *,
    rounding: Decimal,
) -> tuple[Disagreement, ...]:
    """
    Compare two decision records field by field, within the session's own rounding.

    Pure, so the rules are testable without a catalog or an engine.

    """
    out: list[Disagreement] = []
    atr_tolerance = 2 * rounding

    if bool(live.get("atr_initialized")) != bool(offline.get("atr_initialized")):
        out.append(
            Disagreement(
                "atr_initialized",
                str(live.get("atr_initialized")),
                str(offline.get("atr_initialized")),
            ),
        )
    for field, tolerance in (
        ("atr_value", atr_tolerance),
        ("previous_close", rounding),
        ("deferred_atr", atr_tolerance),
    ):
        if not _within(live.get(field), offline.get(field), tolerance):
            out.append(
                Disagreement(field, live.get(field), offline.get(field), f"tolerance {tolerance}"),
            )

    live_outcome, offline_outcome = live.get("outcome"), offline.get("outcome")
    if live_outcome in SESSION_LEVEL_OUTCOMES:
        if offline_outcome != ENTRY_SUBMITTED:
            out.append(
                Disagreement(
                    "outcome",
                    live_outcome,
                    offline_outcome,
                    "the session refused an entry the rule did not want",
                ),
            )
    elif live_outcome != offline_outcome:
        out.append(Disagreement("outcome", live_outcome, offline_outcome))

    live_skips = {
        k: v for k, v in (live.get("skips") or {}).items() if k not in SESSION_LEVEL_OUTCOMES
    }
    offline_skips = dict(offline.get("skips") or {})
    if live_skips != offline_skips:
        out.append(Disagreement("skips", str(live_skips), str(offline_skips)))
    return tuple(out)


def compare_entry(entry: Mapping[str, Any], catalog_path: str) -> Comparison:
    """
    Compare one activation's entry in a session record against catalog and engine.
    """
    symbol, _, venue = str(entry["research_instrument"]).partition(".")
    stored = read_series(catalog_path, symbol, venue)
    bars = window_bars(
        stored,
        warmup_from=str(entry["warmup_from"]),
        decision_bar=str(entry["decision_bar"]),
    )
    expected = int(entry["warmup_bars"]) + 1
    rounding = Decimal(str(entry.get("largest_rounding", "0")))
    live = _live_decision(entry)
    if len(bars) != expected:
        # The catalog moved under the record - a patch, a rewrite, a hole opened - and
        # a replay over different bars would compare nothing.
        return Comparison(
            activation=str(entry["activation"]),
            decision_bar=str(entry["decision_bar"]),
            live_outcome=live.get("outcome"),  # type: ignore[arg-type]
            offline_outcome=None,
            tolerance=str(2 * rounding),
            disagreements=(Disagreement("bars", str(expected), str(len(bars)), "in the window"),),
            note="the catalog no longer holds the bars the session ran on",
        )
    offline = offline_decision(entry, bars)
    disagreements = compare_decisions(live, offline, rounding=rounding)
    note = ""
    if live.get("outcome") in SESSION_LEVEL_OUTCOMES and not disagreements:
        note = "the session's ledger refused an entry the rule wanted; that is agreement"
    return Comparison(
        activation=str(entry["activation"]),
        decision_bar=str(entry["decision_bar"]),
        live_outcome=live.get("outcome"),  # type: ignore[arg-type]
        offline_outcome=offline.get("outcome"),  # type: ignore[arg-type]
        tolerance=str(2 * rounding),
        disagreements=disagreements,
        note=note,
    )


def _live_decision(entry: Mapping[str, Any]) -> dict[str, Any]:
    """
    Read the decision fields off a session entry, in the strategy's own shape.

    Records filed before ``outcome`` existed carry skips and orders only; the outcome is
    derived from those so an older session is still comparable.

    """
    outcome = entry.get("outcome")
    if outcome is None:
        skips = entry.get("skips") or {}
        if entry.get("orders"):
            outcome = ENTRY_SUBMITTED
        elif len(skips) == 1:
            outcome = next(iter(skips))
    return {
        "atr_initialized": entry.get("atr_initialized"),
        "atr_value": entry.get("atr_value"),
        "previous_close": entry.get("previous_close"),
        "deferred_atr": entry.get("deferred_atr"),
        "outcome": outcome,
        "skips": dict(entry.get("skips") or {}),
    }


def report(path: Path, session: str, comparisons: tuple[Comparison, ...]) -> None:
    """
    Print one line per activation, in the order the session decided them.
    """
    print(f"Comparing {path.name} (session {session}) against the catalog and the engine\n")
    print(f"  {'activation':<32}{'decision':<12}{'live':<24}{'offline':<24}result")
    for c in comparisons:
        result = "AGREE" if c.agrees else "DISAGREE"
        print(
            f"  {c.activation:<32}{c.decision_bar:<12}{(c.live_outcome or '-'):<24}"
            f"{(c.offline_outcome or '-'):<24}{result}",
        )
        for d in c.disagreements:
            print(f"      {d.field}: live {d.live}  offline {d.offline}  {d.note}")
        if c.note:
            print(f"      {c.note}")
    agreed = sum(1 for c in comparisons if c.agrees)
    print(f"\n  {len(comparisons)} compared, {agreed} agree.")


def main(argv: list[str] | None = None) -> int:
    """
    Compare a session record against the replay and file the result.

    Exit 0 when every activation agrees, 1 when any disagrees, 2 when there is nothing
    to compare.

    """
    parser = argparse.ArgumentParser(
        prog="python -m copilot.live.compare",
        description="Compare a live session's decisions against an offline replay.",
    )
    parser.add_argument("--record", help="Session record to compare (default: the newest)")
    add_catalog_argument(parser)
    args = parser.parse_args(argv)

    path = Path(args.record) if args.record else latest_record()
    if path is None or not path.exists():
        print("nothing to compare: no session record under live/out/")
        return 2
    payload = json.loads(path.read_text())
    session = str(payload.get("session", "?"))
    comparisons = tuple(compare_entry(entry, args.catalog) for entry in entries_of(payload))
    report(path, session, comparisons)

    started = datetime.now(tz=UTC)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"compare_{started.strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(
        json.dumps(
            {
                "compared_at": started.isoformat(),
                "record": path.name,
                "session": session,
                "agrees": all(c.agrees for c in comparisons),
                "activations": [c.as_record() for c in comparisons],
            },
            indent=2,
        )
        + "\n",
    )
    print(f"  filed {out}")
    return 0 if all(c.agrees for c in comparisons) else 1


__all__ = [
    "BOOLEAN_PARAMETERS",
    "SESSION_LEVEL_OUTCOMES",
    "Comparison",
    "Disagreement",
    "compare_decisions",
    "compare_entry",
    "entries_of",
    "latest_record",
    "offline_decision",
    "typed_parameters",
    "window_bars",
]


if __name__ == "__main__":
    sys.exit(main())
