"""
An exclusive lock on a file, held for a block, across processes and threads on one host.

The halt latch and the alert receipts log are each written by more than one process - a
``day`` phase, the acknowledgement timer, the operator's ``kill`` - and until 2026-09-11 none
of them locked. Two engagements could interleave through one staging file, so the latch id
printed need not be the one on disk, and two acknowledgement checks could both resolve one
receipt (``docs/AUDIT_2026-09-11.md``, F13).

``fcntl.flock`` locks an open file description, so a second acquisition from the same process
through a new ``open`` waits like any other; callers that nest must not re-acquire.

"""

from __future__ import annotations

import fcntl
from contextlib import contextmanager
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@contextmanager
def exclusive(lock_path: Path) -> Iterator[None]:
    """
    Hold an exclusive lock on ``lock_path`` for the block, creating the file if needed.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


__all__ = ["exclusive"]
