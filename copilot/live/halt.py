"""
The halt latch: one file that stops every order-capable node on this host.

The playbook's kill switch is *independent of the strategy*, and in this overlay that has a
specific consequence. ``TradingState.HALTED`` lives in one node's risk engine and dies with
the process, and every ``copilot.live.day`` step is a new process. A halt set in one node is
therefore not a halt for the next, so the switch cannot be a state inside a node; it has to
be something every node reads before it starts.

That is this latch. :func:`~copilot.live.node.build_paper_node` reads it and starts any
order-capable node ``HALTED`` while it is engaged, whatever the session asked for. The one
exemption is a session declared **cancels only** - the sweep - because cancelling is what
safe mode does, and cancel commands do not pass through the risk engine anyway
(``crates/risk/src/engine/mod.rs`` handles submit, modify and account queries only).

Engaged by the operator (``python -m copilot.live.kill``), by a ``CRITICAL`` alert that
expired unacknowledged, or by one that could not be delivered at all. Released only by the
operator, retyping the latch's id. The two automatic triggers mean nobody has been told, so
while one holds the morning withholds its heartbeat (:func:`engaged_automatically`).

"""

from __future__ import annotations

import json
import os
import secrets
import socket
import tempfile
from dataclasses import asdict
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from pathlib import Path

from copilot.live.filelock import exclusive
from copilot.paths import HALT_LATCH_PATH


OPERATOR = "operator"
UNACKNOWLEDGED_CRITICAL = "unacknowledged_critical"
UNDELIVERED_CRITICAL = "undelivered_critical"


@dataclass(frozen=True)
class Latch:
    """
    Why this host is halted, since when, and the id that releases it.
    """

    latch_id: str
    engaged_at: str
    trigger: str
    reason: str
    host: str


def latch_path(path: str | Path = HALT_LATCH_PATH) -> Path:
    """
    Return the latch file's location, with ``~`` expanded.
    """
    return Path(path).expanduser()


def read_latch(path: str | Path = HALT_LATCH_PATH) -> Latch | None:
    """
    Return the engaged latch, or None when the host is not halted.

    **A latch that cannot be read is engaged.** The file existing is the signal; a torn
    write or a hand edit that breaks it must not read as "not halted", which is the one
    reading that could let an order through.

    """
    target = latch_path(path)
    if not target.exists():
        return None
    try:
        return Latch(**json.loads(target.read_text()))
    except (OSError, ValueError, TypeError) as e:
        return Latch(
            latch_id="unreadable",
            engaged_at="unknown",
            trigger="unknown",
            reason=f"{target} exists and cannot be read ({e}); treated as engaged",
            host=socket.gethostname(),
        )


def engage(
    reason: str,
    *,
    trigger: str = OPERATOR,
    path: str | Path = HALT_LATCH_PATH,
    now: datetime | None = None,
) -> tuple[Latch, bool]:
    """
    Engage the latch, or return the one already engaged, and whether this call did.

    An engaged latch is never overwritten: the first reason is the one the recovery
    checklist has to answer, and a second trigger replacing it would erase why it started.
    The check and the write happen under one lock, and the write stages through a file of
    its own, so two engagements at once produce one latch and both callers name it.

    """
    target = latch_path(path)
    with exclusive(_lock_for(target)):
        existing = read_latch(target)
        if existing is not None:
            return existing, False
        latch = Latch(
            latch_id=secrets.token_hex(4),
            engaged_at=(now or datetime.now(UTC)).isoformat(),
            trigger=trigger,
            reason=reason,
            host=socket.gethostname(),
        )
        descriptor, staging = tempfile.mkstemp(dir=target.parent, prefix=".HALT.", suffix=".tmp")
        with os.fdopen(descriptor, "w") as handle:
            handle.write(json.dumps(asdict(latch), indent=2) + "\n")
        Path(staging).replace(target)
        return latch, True


def _lock_for(target: Path) -> Path:
    """
    Return the lock file that serialises every change to ``target``.
    """
    return target.with_name(target.name + ".lock")


def release(latch_id: str, *, path: str | Path = HALT_LATCH_PATH) -> Latch:
    """
    Release the latch if ``latch_id`` is its id, and return what was released.
    """
    target = latch_path(path)
    with exclusive(_lock_for(target)):
        latch = read_latch(target)
        if latch is None:
            raise ValueError("the halt latch is not engaged; nothing to release")
        if latch_id != latch.latch_id:
            raise ValueError(
                f"{latch_id!r} is not the engaged latch's id. Retype the id `kill --status` "
                "prints; a release is not tab-completed.",
            )
        target.unlink()
        return latch


def engaged_automatically(latch: Latch | None) -> bool:
    """
    Whether a latch no operator engaged holds, so nobody may know the host is halted.

    Everything but ``operator``: the two unanswered-alert triggers, and an unreadable latch,
    whose trigger cannot be known.

    """
    return latch is not None and latch.trigger != OPERATOR


def orders_allowed(*, requested: bool, cancels_only: bool, latch: Latch | None) -> bool:
    """
    Return whether a node may leave its risk engine active.

    Orders are allowed only when the session asked for them and no latch is engaged. A
    cancels-only session keeps what it asked for, because cancelling is safe mode.

    """
    if not requested:
        return False
    return cancels_only or latch is None


__all__ = [
    "OPERATOR",
    "UNACKNOWLEDGED_CRITICAL",
    "UNDELIVERED_CRITICAL",
    "Latch",
    "engage",
    "engaged_automatically",
    "latch_path",
    "orders_allowed",
    "read_latch",
    "release",
]
