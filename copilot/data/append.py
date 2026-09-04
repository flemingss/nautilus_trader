"""
Bring the catalog to the last published session, and say plainly when it could not.

    export MARKETSTACK_API_KEY=...
    python -m copilot.data.append

The live warm-up reads the catalog and refuses a window that is stale or holed
([ADR-0017]), so something has to keep it current. This is that something: safe to run on
a clock, and written so an unattended run is worth trusting.

Why this is not `backfill --from yesterday`
-------------------------------------------
Three reasons, and each is a defect the naive version would have.

**The catalog refuses an overlapping write.** ``ParquetDataCatalog`` raises on
non-disjoint intervals rather than de-duplicating, so a re-run over a window that is
already stored fails outright. The append therefore computes, per symbol, the sessions
strictly after what is stored - and a run with nothing to do writes nothing and exits
zero, which is the only kind of idempotence that matters on a schedule.

**Per symbol, because they diverge.** A session the vendor could not price for SPY but
could for AAPL leaves the two at different last-stored dates, and one window for both
would either skip SPY's gap or collide with AAPL's history.

**A rejection is routine here, not fatal.** ``backfill`` refuses the whole batch past a
rejection ratio, and it is right to: over twenty years it cannot tell a hole from the end
of history, so a partial write would be read as complete by everything downstream. The
append *can* tell - it derives the sessions it expects from the exchange calendar before
it fetches anything - so it writes what is good and names what is missing. Refusing
everything because one session is unreadable would leave the catalog further behind on
each run, which is the failure the vendor's 2026 rows would have caused every day
([ADR-0018]).

Missing, or merely not published yet
------------------------------------
An absent session is only a defect once its data should exist. A session that closed an
hour ago is *pending*; one absent long after publication is a **hole**, and the two get
different exit codes because an operator woken at 3am should have been woken for
something real.

The threshold is measured rather than assumed: on 2026-09-04 the vendor's row for the
2026-09-03 session, which closed at 20:00 UTC, was available 9.5 hours later.
:data:`PUBLICATION_GRACE_HOURS` is roughly double that. It is deliberately generous,
because this classification decides whether to raise an alarm and **not** whether a
session is safe to trade on - the warm-up's own refusal does that, and it does not
consult this.

[ADR-0017]: ../docs/decisions/0017-the-evaluation-window-is-pinned-at-both-ends.md
[ADR-0018]: ../docs/decisions/0018-an-unusable-bar-is-substituted-whole.md

"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import date
from datetime import datetime
from datetime import timedelta
from typing import TYPE_CHECKING

from copilot.data.calendar import session_close
from copilot.data.calendar import trading_days
from copilot.data.catalog import bar_type_for
from copilot.data.catalog import equity_for
from copilot.data.catalog import open_catalog
from copilot.data.catalog import read_daily_bars
from copilot.data.catalog import write_ingestion
from copilot.data.marketstack import IngestionResult
from copilot.data.marketstack import MarketstackClient
from copilot.data.marketstack import normalize
from copilot.data.substitutions import apply_to
from copilot.strategies.activations import load_activations


if TYPE_CHECKING:
    from collections.abc import Sequence

    from copilot.data.substitutions import Substitution


API_KEY_ENV = "MARKETSTACK_API_KEY"
CATALOG_PATH_ENV = "COPILOT_CATALOG_PATH"
DEFAULT_CATALOG = "~/.nautilus_copilot/catalog"

PUBLICATION_GRACE_HOURS = 18
"""
Hours after a session's close before its absence counts as a hole rather than a wait.

Measured, then doubled: on 2026-09-04 the row for the 2026-09-03 session - which closed
20:00 UTC - was available 9.5 hours later. Generous on purpose, because this decides
whether to alarm and not whether a session may be traded on.

"""


class NotBackfilledError(ValueError):
    """
    The catalog holds no history for a symbol, so there is nothing to append to.
    """


@dataclass(frozen=True)
class SymbolResult:
    """
    What one symbol's append did, and what it could not do.
    """

    symbol: str
    venue: str
    last_stored: date
    written: tuple[date, ...] = ()
    substituted: tuple[Substitution, ...] = ()
    rejected: tuple[tuple[str, str], ...] = field(default=())
    """
    Sessions the gate refused, as ``(session or "?", reason)``.
    """
    missing: tuple[date, ...] = ()
    """
    Sessions due and still absent after the write: holes, and the reason for exit 1.
    """
    pending: tuple[date, ...] = ()
    """
    Sessions absent but too recent to judge.
    """

    @property
    def current(self) -> bool:
        """
        Return whether the catalog holds every session that should exist by now.
        """
        return not self.missing


def due_sessions(after: date, as_of: datetime) -> tuple[tuple[date, ...], tuple[date, ...]]:
    """
    Split the sessions since ``after`` into those due and those merely pending.

    ``after`` is exclusive: it is the last session already stored, and asking for it
    again is what the catalog refuses.

    """
    candidates = trading_days(after + timedelta(days=1), as_of.date())
    grace = timedelta(hours=PUBLICATION_GRACE_HOURS)
    due = tuple(day for day in candidates if as_of >= session_close(day) + grace)
    pending = tuple(day for day in candidates if day not in set(due))
    return due, pending


def last_stored_session(catalog_path: str, symbol: str, venue: str) -> date:
    """
    Return the newest session the catalog holds for one instrument.

    Raises rather than defaulting to some start date: an append with no history to append
    to is a backfill, and quietly turning into one would pull twenty years on a schedule.

    """
    instrument = equity_for(symbol, venue)
    bars = read_daily_bars(open_catalog(catalog_path), bar_type_for(instrument.id))
    if not bars:
        raise NotBackfilledError(
            f"the catalog holds no bars for {instrument.id}. This appends to a history, "
            f"it does not create one: python -m copilot.data.backfill --symbols {symbol} "
            f"--from 2005-01-01",
        )
    return max(bar.closed_at.date() for bar in bars)


def append_symbol(
    catalog_path: str,
    symbol: str,
    venue: str,
    client: MarketstackClient,
    *,
    as_of: datetime,
) -> SymbolResult:
    """
    Append one symbol's missing sessions.

    Writes what the gate accepts and reports what it did not, rather than refusing the
    batch: the expected session set comes from the exchange calendar, so a hole is
    nameable here in a way it is not during a backfill.

    """
    last = last_stored_session(catalog_path, symbol, venue)
    due, pending = due_sessions(last, as_of)
    if not due and not pending:
        return SymbolResult(symbol=symbol, venue=venue, last_stored=last)

    wanted = due + pending
    rows = list(client.fetch_eod([symbol], wanted[0], wanted[-1]))
    rows, applied = apply_to(rows)
    result = normalize(rows, received_at=as_of)

    # Strictly after what is stored. The gate can return a bar for a session already
    # held - the fetch window is inclusive at both ends - and writing it raises on
    # non-disjoint intervals rather than being ignored.
    fresh = tuple(bar for bar in result.bars if bar.closed_at.date() > last)
    if fresh:
        write_ingestion(
            open_catalog(catalog_path),
            IngestionResult(bars=fresh, fetched=result.fetched),
            venues={symbol: venue},
        )

    written = tuple(sorted(bar.closed_at.date() for bar in fresh))
    landed = set(written)
    return SymbolResult(
        symbol=symbol,
        venue=venue,
        last_stored=last,
        written=written,
        substituted=applied,
        rejected=tuple(
            (row.closed_at.date().isoformat() if row.closed_at else "?", row.reason)
            for row in result.rejected
        ),
        missing=tuple(day for day in due if day not in landed),
        pending=tuple(day for day in pending if day not in landed),
    )


def append(
    catalog_path: str,
    targets: Sequence[tuple[str, str]],
    client: MarketstackClient,
    *,
    as_of: datetime,
) -> tuple[SymbolResult, ...]:
    """
    Append every target, in order, without letting one symbol's failure stop the rest.

    A vendor that cannot price SPY today has no bearing on AAPL, and a run that
    abandoned the remaining symbols would turn one bad session into a catalog-wide
    stall.

    """
    out: list[SymbolResult] = []
    for symbol, venue in targets:
        out.append(append_symbol(catalog_path, symbol, venue, client, as_of=as_of))
    return tuple(out)


def report(results: Sequence[SymbolResult], *, as_of: datetime) -> int:
    """
    Print one line per symbol and return the exit code a scheduler should act on.
    """
    print(f"Catalog append at {as_of.isoformat(timespec='seconds')}\n")
    holes = 0
    for r in results:
        newest = max(r.written).isoformat() if r.written else r.last_stored.isoformat()
        state = "current" if r.current else "HOLE"
        print(f"  {r.symbol + '.' + r.venue:12} {state:8} +{len(r.written):>2} to {newest}")
        for entry in r.substituted:
            print(f"      substituted {entry.day}  close {entry.close}  [{entry.reason}]")
        for day, reason in r.rejected:
            print(f"      rejected    {day}  {reason}")
        if r.missing:
            holes += 1
            print(f"      MISSING     {', '.join(d.isoformat() for d in r.missing)}")
        if r.pending:
            print(f"      pending     {', '.join(d.isoformat() for d in r.pending)}")
    if holes:
        print(
            f"\n{holes} symbol(s) have sessions the vendor should have published and did "
            f"not. The warm-up will refuse them; fix the source or add a substitution "
            f"(copilot/data/substitutions.py) before the next session.",
        )
    return 1 if holes else 0


def main(argv: list[str] | None = None) -> int:
    """
    Append every activation's instrument, and report.
    """
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument(
        "--catalog",
        default=os.environ.get(CATALOG_PATH_ENV, DEFAULT_CATALOG),
        help=f"Catalog directory (default: ${CATALOG_PATH_ENV} or {DEFAULT_CATALOG})",
    )
    parser.add_argument(
        "--symbols",
        help="Comma-separated SYMBOL.VENUE pairs; default is every registered activation",
    )
    args = parser.parse_args(argv)

    access_key = os.environ.get(API_KEY_ENV)
    if not access_key:
        print(f"error: {API_KEY_ENV} is not set", file=sys.stderr)
        return 2

    targets = _targets(args.symbols)
    if not targets:
        print("error: no symbols to append", file=sys.stderr)
        return 2

    as_of = datetime.now(tz=UTC)
    try:
        results = append(args.catalog, targets, MarketstackClient(access_key), as_of=as_of)
    except NotBackfilledError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return report(results, as_of=as_of)


def _targets(spec: str | None) -> tuple[tuple[str, str], ...]:
    """
    Return the instruments to append: the given pairs, or every activation's.

    Activations by default because the point of appending is to keep the live path
    warmable, and the live path trades activations.

    """
    if spec:
        pairs = []
        for token in spec.split(","):
            symbol, _, venue = token.strip().partition(".")
            if not symbol or not venue:
                raise ValueError(f"expected SYMBOL.VENUE, got {token.strip()!r}")
            pairs.append((symbol.upper(), venue.upper()))
        return tuple(dict.fromkeys(pairs))
    return tuple(dict.fromkeys((a.symbol, a.venue) for a in load_activations()))


__all__ = [
    "PUBLICATION_GRACE_HOURS",
    "NotBackfilledError",
    "SymbolResult",
    "append",
    "append_symbol",
    "due_sessions",
    "last_stored_session",
    "report",
]


if __name__ == "__main__":
    sys.exit(main())
