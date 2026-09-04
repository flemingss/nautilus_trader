"""
Say whether a verdict could have changed, so the morning skips one that cannot.

    python -m copilot.strategies.validate --changed --write

The operator-day walk found the morning running `validate --all` for nothing: `append`
had printed `+0`, so no fold could have moved, and two minutes went on proving it. With
twelve activations that is closer to five. Nothing connected the two commands, so the
operator either paid every day or decided by memory whether to bother - and deciding by
memory is how a verdict goes stale without anyone noticing.

A verdict is a function of four inputs and nothing else
--------------------------------------------------------
- **Data**: the bars inside the evaluation window, as the gate reads them - corporate
  actions applied ([ADR-0016]), clipped at both ends ([ADR-0017]). A bar appended past
  the window is not an input, which is the whole reason the daily append cannot move a
  verdict and the whole reason this check is worth having.
- **Identity**: the activation's parameters, its fold geometry, its holdout boundary,
  and the search space its strategy declares.
- **Cost**: the pinned snapshot and the symbol's charged spread.
- **Code**: the source that turns the first three into a number.

Hash each, and a verdict filed with the same four digests was computed from the same
inputs. The digests live **in the verdict record**, so the question "is this still
current" is answered by reading the record against the world, not by remembering.

Code is hashed from the working tree
------------------------------------
Not from the commit. A commit hash changes on every documentation edit, which would
force a recompute for nothing; a tree hash of `HEAD` ignores an uncommitted edit to the
strategy, which would skip a recompute that mattered. Hashing the files that actually
run - under the roots named below, working tree as it stands - is honest to what a
`validate` would execute right now.

[ADR-0016]: ../docs/decisions/0016-corporate-actions-are-applied-on-read.md
[ADR-0017]: ../docs/decisions/0017-the-evaluation-window-is-pinned-at-both-ends.md

"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from copilot.validation.holdout import EVALUATION_END
from copilot.validation.holdout import HOLDOUT_START


if TYPE_CHECKING:
    from collections.abc import Iterable
    from collections.abc import Sequence

    from copilot.calibration.cost_model import CostModel
    from copilot.strategies.activations import Activation
    from copilot.validation.types import DailyBar


OVERLAY = Path(__file__).resolve().parent.parent

CODE_ROOTS: tuple[str, ...] = (
    "strategies",
    "validation",
    "risk",
    "calibration/cost_model.py",
    "data/catalog.py",
    "data/corporate_actions.py",
)
"""
The source a verdict depends on, relative to the overlay.

Everything under ``strategies`` and ``validation`` is the gate and the rules it scores;
``risk`` is how a signal becomes a size; the cost model is what a trade is charged; the
catalog and corporate-actions modules are how a stored bar becomes the bar the gate
sees. Absent on purpose: the ingestion path, which produces the stored bars this hashes
directly, and the live path, which reads verdicts and never writes them.

"""

EXCLUDED_SUFFIXES = (".json", ".toml", ".md", ".pyc")
"""
Filed outputs and registry data are not code.

The registry's parameters are an input, and they reach the identity digest through the
loaded activation rather than through the file bytes.

"""

VERDICTS_DIR = Path(__file__).parent / "verdicts"


@dataclass(frozen=True)
class Fingerprint:
    """
    The four digests a verdict was computed from.
    """

    data: str
    identity: str
    cost: str
    code: str

    @property
    def combined(self) -> str:
        """
        One digest over the four, for a single equality check.
        """
        return _sha256(f"{self.data}:{self.identity}:{self.cost}:{self.code}")

    def as_record(self) -> dict[str, str]:
        """
        Return the JSON form, written into every verdict.
        """
        return {
            "data": self.data,
            "identity": self.identity,
            "cost": self.cost,
            "code": self.code,
            "combined": self.combined,
        }

    def differs_from(self, other: Fingerprint) -> tuple[str, ...]:
        """
        Name the digests that differ, so a recompute can say why it is running.
        """
        return tuple(
            name
            for name in ("data", "identity", "cost", "code")
            if getattr(self, name) != getattr(other, name)
        )


def data_digest(bars: Iterable[DailyBar]) -> str:
    """
    Hash the bars inside the evaluation window, exactly as the gate sees them.

    Clipped here rather than by the caller, so a digest cannot be taken over a whole
    catalog by mistake: a catalog fresh to yesterday would then fingerprint differently
    every morning, and the append would look like a change it is not.

    """
    lines = []
    for bar in sorted(bars, key=lambda b: b.closed_at):
        if bar.closed_at >= EVALUATION_END:
            continue
        lines.append(
            f"{bar.closed_at.isoformat()},{bar.open},{bar.high},{bar.low},{bar.close},{bar.volume}",
        )
    return _sha256("\n".join(lines))


def identity_digest(activation: Activation) -> str:
    """
    Hash what makes this activation the experiment it is.
    """
    payload = {
        "name": activation.name,
        "strategy": activation.strategy,
        "instrument": f"{activation.symbol}.{activation.venue}",
        "parameters": {k: str(v) for k, v in sorted(activation.parameters.items())},
        "validation": {k: str(v) for k, v in sorted(vars(activation.validation).items())},
        "holdout_start": activation.validation.holdout_start or HOLDOUT_START.date().isoformat(),
        "evaluation_end": EVALUATION_END.date().isoformat(),
        "search_space": {
            k: [str(x) for x in v] for k, v in sorted(activation.setup.search_space.items())
        },
        "warmup_bars": activation.setup.warmup_bars,
    }
    return _sha256(json.dumps(payload, sort_keys=True))


def cost_digest(cost_model: CostModel, symbol: str) -> str:
    """
    Hash the cost basis this symbol is charged at.
    """
    return _sha256(json.dumps(cost_model.as_record(symbol), sort_keys=True))


def code_digest(roots: Sequence[str] = CODE_ROOTS, overlay: Path = OVERLAY) -> str:
    """
    Hash the source under the roots, as it stands in the working tree.
    """
    digest = hashlib.sha256()
    for root in roots:
        path = overlay / root
        files = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file())
        for file in files:
            if file.suffix in EXCLUDED_SUFFIXES or "__pycache__" in file.parts:
                continue
            digest.update(str(file.relative_to(overlay)).encode())
            digest.update(b"\0")
            digest.update(file.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def fingerprint_for(
    activation: Activation,
    bars: Iterable[DailyBar],
    cost_model: CostModel,
) -> Fingerprint:
    """
    Compute the four digests for one activation against the bars it would be run on.
    """
    return Fingerprint(
        data=data_digest(bars),
        identity=identity_digest(activation),
        cost=cost_digest(cost_model, activation.symbol),
        code=code_digest(),
    )


def latest_verdict(activation_name: str, directory: Path = VERDICTS_DIR) -> Path | None:
    """
    Return the newest filed verdict for an activation, or None.
    """
    files = sorted(directory.glob(f"{activation_name}_*.json"))
    return files[-1] if files else None


def filed_fingerprint(path: Path) -> Fingerprint | None:
    """
    Read a filed verdict's fingerprint, or None if it was filed before one existed.
    """
    inputs = json.loads(path.read_text()).get("inputs")
    if not inputs:
        return None
    return Fingerprint(
        data=inputs["data"],
        identity=inputs["identity"],
        cost=inputs["cost"],
        code=inputs["code"],
    )


def unchanged_since(
    activation_name: str,
    current: Fingerprint,
    directory: Path = VERDICTS_DIR,
) -> Path | None:
    """
    Return the filed verdict that was computed from exactly these inputs, or None.

    None means "run it": either nothing is filed, or the newest record predates the
    fingerprint, or one of the four digests has moved. All three are the same answer to
    the operator, and the caller says which when it matters.

    """
    path = latest_verdict(activation_name, directory)
    if path is None:
        return None
    filed = filed_fingerprint(path)
    if filed is None or filed.combined != current.combined:
        return None
    return path


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


__all__ = [
    "CODE_ROOTS",
    "Fingerprint",
    "code_digest",
    "cost_digest",
    "data_digest",
    "filed_fingerprint",
    "fingerprint_for",
    "identity_digest",
    "latest_verdict",
    "unchanged_since",
]
