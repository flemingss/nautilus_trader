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

Engaged by the operator (``python -m copilot.live.kill``) or by an unacknowledged
``CRITICAL`` alert. Released only by the operator, retyping the latch's id.

"""

from __future__ import annotations

import json
import secrets
import socket
from dataclasses import asdict
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from pathlib import Path

from copilot.paths import HALT_LATCH_PATH


OPERATOR = "operator"
UNACKNOWLEDGED_CRITICAL = "unacknowledged_critical"


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

    """
    existing = read_latch(path)
    if existing is not None:
        return existing, False
    latch = Latch(
        latch_id=secrets.token_hex(4),
        engaged_at=(now or datetime.now(UTC)).isoformat(),
        trigger=trigger,
        reason=reason,
        host=socket.gethostname(),
    )
    target = latch_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_suffix(".tmp")
    staging.write_text(json.dumps(asdict(latch), indent=2) + "\n")
    staging.replace(target)
    return latch, True


def release(latch_id: str, *, path: str | Path = HALT_LATCH_PATH) -> Latch:
    """
    Release the latch if ``latch_id`` is its id, and return what was released.
    """
    latch = read_latch(path)
    if latch is None:
        raise ValueError("the halt latch is not engaged; nothing to release")
    if latch_id != latch.latch_id:
        raise ValueError(
            f"{latch_id!r} is not the engaged latch's id. Retype the id `kill --status` "
            "prints; a release is not tab-completed.",
        )
    latch_path(path).unlink()
    return latch


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
    "Latch",
    "engage",
    "latch_path",
    "orders_allowed",
    "read_latch",
    "release",
]
