"""
What a session ran, tied to what the gate scored: commit, verdict, and the four digests.

The playbook's first *Before* line is *verify strategy ID, code commit, config hash and
data hash*. Every verdict has carried its four input digests since 2026-09-04, and the
operator's-day draft called this "narrowed: the digests exist per verdict; no single
manifest yet". This is the manifest, computed per activation when the basket runs and
filed in the session record.

The check it makes is the one the playbook means. The newest verdict for an activation
names the data, identity, cost basis and code it scored; this recomputes all four for the
session about to run and names any that moved. A moved digest is not a refusal - a
RESEARCH session may legitimately run ahead of its verdict - but a session record that
cannot say whether tonight's rule is the scored one is the record the comparison later
has to distrust.

The commit is the working tree's, marked dirty when it is, for the same reason
``spend_holdout`` refuses a dirty tree: a number that cannot be tied to a commit cannot be
tied to an experiment. Lives under ``live/`` rather than ``strategies/`` on purpose - the
code digest covers ``strategies/`` and ``validation/``, and a helper that only reports the
digests must not move them.

"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import TYPE_CHECKING

from copilot.strategies.fingerprint import VERDICTS_DIR
from copilot.strategies.fingerprint import filed_fingerprint
from copilot.strategies.fingerprint import fingerprint_for
from copilot.strategies.fingerprint import latest_verdict


if TYPE_CHECKING:
    from collections.abc import Sequence

    from copilot.calibration.cost_model import CostModel
    from copilot.strategies.activations import Activation
    from copilot.validation.types import DailyBar


SHORT_COMMIT = 10


@dataclass(frozen=True)
class Manifest:
    """
    One activation's provenance for one session.
    """

    code_commit: str
    tree_clean: bool
    verdict: str | None
    inputs: dict[str, str] = field(default_factory=dict)
    """
    The four digests of the session as it is about to run.
    """
    moved: tuple[str, ...] = ()
    """
    Which of the newest verdict's digests the session no longer matches.
    """

    @property
    def label(self) -> str:
        """
        One line an operator reads.
        """
        commit = f"{self.code_commit[:SHORT_COMMIT]}{'' if self.tree_clean else ' (dirty)'}"
        if self.verdict is None:
            return f"commit {commit}; no verdict filed for this activation"
        if self.moved:
            return f"commit {commit}; {', '.join(self.moved)} moved since {self.verdict}"
        return f"commit {commit}; inputs unchanged since {self.verdict}"

    def as_record(self) -> dict[str, object]:
        """
        Return the fields the session record carries.
        """
        return {
            "code_commit": self.code_commit,
            "tree_clean": self.tree_clean,
            "verdict": self.verdict,
            "inputs": dict(self.inputs),
            "moved": list(self.moved),
        }


def current_commit() -> tuple[str, bool]:
    """
    Return the working tree's commit and whether the tree is clean of tracked changes.
    """
    return _git("rev-parse", "HEAD"), _git("status", "--porcelain", "--untracked-files=no") == ""


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


def manifest_for(
    activation: Activation,
    bars: Sequence[DailyBar],
    cost_model: CostModel,
    *,
    commit: tuple[str, bool],
    verdicts_dir: Path = VERDICTS_DIR,
) -> Manifest:
    """
    Build the manifest: the session's digests against the newest verdict's.

    Pure apart from reading the verdict file, so the comparison is testable with a
    temporary directory and a fabricated record.

    """
    current = fingerprint_for(activation, bars, cost_model)
    path = latest_verdict(activation.name, verdicts_dir)
    filed = filed_fingerprint(path) if path is not None else None
    return Manifest(
        code_commit=commit[0],
        tree_clean=commit[1],
        verdict=path.name if path is not None else None,
        inputs=current.as_record(),
        moved=current.differs_from(filed) if filed is not None else (),
    )


__all__ = ["SHORT_COMMIT", "Manifest", "current_commit", "manifest_for"]
