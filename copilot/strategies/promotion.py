"""
Which parameters a live session may run, and what it must say about them.

The gate scores a parameter set; the holdout spend freezes one; the registry carries an
activation's identity. Three places, and until 2026-09-05 nothing checked that the set
reaching a broker connection was any of them. ``run_activation`` built the strategy from
the activation's ``[parameters]`` alone, so a searched axis the registry left unfixed ran
at the strategy module's default - ``min_gap_atr = 0.25`` while selection had chosen
0.40 - and the session record said nothing.

Nothing was wrong that day, because nothing was frozen and orders were denied. The
failure this module exists for is the next one: a frozen candidate promoted to PAPER, a
registry edit that drifts from the frozen set, and a replay comparison that reports
agreement against a rule the gate never scored.

Two rules, by lifecycle
-----------------------
**RESEARCH runs its seeded identity, and is labelled as such.** The session record names
every searched axis the activation leaves unfixed and the default it ran at. A RESEARCH
session is evidence about the plumbing, not about the premise, and the label keeps it
from being read as the other.

**PAPER and LIVE run the frozen set, or refuse.** Promotion is a reviewed diff (registry
``README.md``): the owner records ``freeze`` in the holdout record and copies its
``frozen_parameters`` into the activation's ``[parameters]``. This module checks that
the diff was made - a holdout record exists, its decision is ``freeze``, and the
activation's parameters equal the frozen set, key for key - and refuses the session
otherwise. A frozen set is not read *into* the live run from the record, because a
promotion the registry does not show is a promotion nobody reviewed.

"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from decimal import InvalidOperation
from pathlib import Path
from typing import Any

from copilot.strategies.activations import Activation
from copilot.strategies.spend_holdout import SPENT_DIR


FREEZE = "freeze"
"""
The one owner decision that makes a frozen set runnable.
"""

SEEDED = "seeded"
FROZEN = "frozen"


class UnscoredParametersError(ValueError):
    """
    A trading activation's parameters are not the set its holdout froze.
    """


@dataclass(frozen=True)
class Provenance:
    """
    Where a session's parameters came from, for the record and the console.
    """

    source: str
    """
    ``seeded`` or ``frozen``.
    """
    record: str | None = None
    """
    The holdout record the frozen set was checked against, when ``frozen``.
    """
    unfixed: Mapping[str, str] | None = None
    """
    Searched axes the activation leaves unfixed, and the default each ran at.
    """

    @property
    def label(self) -> str:
        """
        One line an operator reads, saying what decided and whether the gate scored it.
        """
        if self.source == FROZEN:
            return f"frozen set, checked against holdouts/{self.record}"
        if self.unfixed:
            at = ", ".join(f"{k}={v}" for k, v in self.unfixed.items())
            return f"seeded identity; {at} are strategy defaults, not the gate's selection"
        return "seeded identity; every searched axis fixed by the registry"

    def as_record(self) -> dict[str, object]:
        """
        Return the fields a session record carries.
        """
        return {
            "source": self.source,
            "record": self.record,
            "unfixed_axes": dict(self.unfixed or {}),
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> Provenance:
        """
        Rebuild from a session record's fields, so the report reads what was filed.
        """
        return cls(
            source=str(record["source"]),
            record=record.get("record"),  # type: ignore[arg-type]
            unfixed=dict(record.get("unfixed_axes") or {}),
        )


def unfixed_axes(activation: Activation) -> dict[str, str]:
    """
    Return each searched axis the activation's parameters leave to the strategy default.
    """
    return {
        axis: str(default)
        for axis, default in activation.setup.axis_defaults.items()
        if axis not in activation.parameters
    }


def frozen_parameters(
    activation: Activation,
    spent_dir: Path = SPENT_DIR,
) -> tuple[str, Mapping[str, str]]:
    """
    Return the holdout record's name and the frozen set the owner decided to freeze.

    Raises :class:`UnscoredParametersError` naming which of the three things is missing:
    the record, the decision, or the set.

    """
    path = spent_dir / f"{activation.name}.json"
    if not path.exists():
        raise UnscoredParametersError(
            f"{activation.name} is {activation.lifecycle} but has no spent holdout under "
            f"{spent_dir.name}/; nothing has been scored for it to run. Spend the holdout, "
            f"record the decision, and promote by diff.",
        )
    record = json.loads(path.read_text())
    decision = record.get("owner_decision")
    if decision != FREEZE:
        raise UnscoredParametersError(
            f"{activation.name} is {activation.lifecycle} but its holdout decision is "
            f"{decision!r}, not {FREEZE!r} ({path.name}). Only a frozen candidate trades.",
        )
    frozen = record.get("frozen_parameters")
    if not frozen:
        raise UnscoredParametersError(
            f"{activation.name}: {path.name} is frozen but carries no frozen_parameters; "
            f"the spend selected nothing, so there is no set to run.",
        )
    return path.name, {k: str(v) for k, v in frozen.items()}


def differences(parameters: Mapping[str, Any], frozen: Mapping[str, str]) -> tuple[str, ...]:
    """
    Return one line per key on which the activation and the frozen set disagree.

    Compared as values rather than as strings, so ``0.4`` and ``0.40`` agree and ``True``
    and ``true`` agree; a key present on one side only is a disagreement, because a
    missing key runs at a default the gate did not score.

    """
    out: list[str] = []
    for key in sorted(set(parameters) | set(frozen)):
        if key not in parameters:
            out.append(f"{key}: not fixed in [parameters]; the holdout froze {frozen[key]}")
        elif key not in frozen:
            out.append(f"{key}={parameters[key]}: fixed in [parameters] but not in the frozen set")
        elif not _same(parameters[key], frozen[key]):
            out.append(
                f"{key}: [parameters] has {parameters[key]}, the holdout froze {frozen[key]}",
            )
    return tuple(out)


def _same(left: Any, right: Any) -> bool:
    """
    Compare two parameter values as numbers when both are, else as normalised text.
    """
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except InvalidOperation:
        return str(left).strip().lower() == str(right).strip().lower()


def provenance(activation: Activation, spent_dir: Path = SPENT_DIR) -> Provenance:
    """
    Return where this activation's parameters come from, or refuse the session.

    RESEARCH is always allowed and always labelled. PAPER and LIVE must match their
    frozen set exactly.

    """
    if not activation.trades:
        return Provenance(source=SEEDED, unfixed=unfixed_axes(activation))
    record, frozen = frozen_parameters(activation, spent_dir)
    diffs = differences(activation.parameters, frozen)
    if diffs:
        raise UnscoredParametersError(
            f"{activation.name} is {activation.lifecycle} and its [parameters] are not "
            f"the set {record} froze:\n  " + "\n  ".join(diffs) + "\nPromote by diff: copy "
            "frozen_parameters into the registry file, or return the activation to RESEARCH.",
        )
    return Provenance(source=FROZEN, record=record, unfixed={})


__all__ = [
    "FREEZE",
    "FROZEN",
    "SEEDED",
    "Provenance",
    "UnscoredParametersError",
    "differences",
    "frozen_parameters",
    "provenance",
    "unfixed_axes",
]
