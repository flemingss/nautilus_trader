"""
The breaker's evidence, kept where a restart cannot reach it.

:func:`~copilot.risk.protections.evaluate_protections` is a pure function of closed trades
and the time, and its cooldown already runs from the breaching trade rather than from when
the breach was noticed. So a cooldown survives a restart **if its evidence does**. Before
this module the evidence was the node's cache - ``positions_closed()`` - and every
``copilot.live.day`` step is its own process with a fresh cache. Each restart therefore
ended a cooldown early and, worse, reset the consecutive-stops count: a daily strategy
running one process per session could lose on every session and never trip the breaker.

The ledger is append-only JSON lines, one per closed round trip, keyed so that the same
close reported twice - once live, again by reconciliation after a restart - is recorded
once. The key includes the close time, not only the position id, because under a netting
OMS Nautilus reuses one position id for successive round trips on an instrument.

A ledger that cannot be read is refused rather than skipped. A torn last line after a
crash would, if skipped, under-count the losses the breaker exists to count - the unsafe
direction - so the guard refuses to start and the operator repairs the file. Each append is
flushed and synced to make that rare.

"""

from __future__ import annotations

import json
import os
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from copilot.risk.protections import TradeOutcome


if TYPE_CHECKING:
    from pathlib import Path


class UnreadableLedgerError(ValueError):
    """
    A ledger line cannot be parsed, so the breaker's evidence is incomplete.
    """


def outcome_key(position_id: str, closed_at: datetime) -> str:
    """
    Identify one closed round trip: the position and the instant it closed.
    """
    return f"{position_id}@{closed_at.isoformat()}"


class OutcomeLedger:
    """
    Closed round trips, one JSON line each, readable across processes and restarts.
    """

    def __init__(self, path: Path) -> None:
        """
        Point at ``path``; nothing is read or created until asked.
        """
        self.path = path

    def read(self) -> dict[str, TradeOutcome]:
        """
        Return every recorded outcome by key, or refuse if any line is unreadable.
        """
        if not self.path.exists():
            return {}
        outcomes: dict[str, TradeOutcome] = {}
        for number, line in enumerate(self.path.read_text().splitlines(), start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                outcomes[row["key"]] = TradeOutcome(
                    closed_at=datetime.fromisoformat(row["closed_at"]),
                    realized_pnl=Decimal(row["realized_pnl"]),
                    stopped_out=bool(row["stopped_out"]),
                )
            except (ValueError, KeyError, TypeError) as e:
                raise UnreadableLedgerError(
                    f"{self.path} line {number} cannot be read ({e}). The protection "
                    "breaker will not start on incomplete evidence: repair or remove the "
                    "line deliberately, knowing a removed loss is one the breaker no "
                    "longer counts.",
                ) from e
        return outcomes

    def record(self, key: str, outcome: TradeOutcome, *, known: dict[str, TradeOutcome]) -> bool:
        """
        Append ``outcome`` unless its key is already known; return whether it wrote.

        ``known`` is the caller's latest :meth:`read`, passed in so a burst of closes does
        not re-read the file once per line.

        """
        if key in known:
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "key": key,
            "closed_at": outcome.closed_at.isoformat(),
            "realized_pnl": str(outcome.realized_pnl),
            "stopped_out": outcome.stopped_out,
        }
        with self.path.open("a") as handle:
            handle.write(json.dumps(row) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        known[key] = outcome
        return True


__all__ = ["OutcomeLedger", "UnreadableLedgerError", "outcome_key"]
