"""
Bring a new symbol from "we would like to trade this" to a filed verdict.

    python -m copilot.data.onboard --symbols SCHX.ARCX,TLT.XNAS
    python -m copilot.data.onboard --symbols SCHX.ARCX --apply
    python -m copilot.data.onboard --symbols SCHX.ARCX --apply --spend --budget 5

Why a command and not a checklist in a document
-----------------------------------------------
Onboarding six ETFs on 2026-09-04 took ten steps - a coverage probe, a backfill, two
metered pulls, a hole patch, a spread recalibration, a repin, six registry files, a
holdout-boundary computation and a validate. Every step was sound. The **order** was
knowledge that existed only in a commit message, and three of the steps have to happen
before a fourth that gives no hint it was skipped.

That is the failure this module exists to prevent, so it is written as a **status
report first and an executor second**. Run it with no flags and it says where each
symbol stands and what the next step is; run it with ``--apply`` and it takes the free
steps it can. Re-running is the point: the report is the same either way, so the command
is also the record of how far the process got.

What it will not do for you
---------------------------
**Spend money silently.** Metered pulls need ``--spend`` and an explicit ``--budget``,
priced first, exactly as [ADR-0015] requires.

**Recalibrate the cost model.** That rebuilds the snapshot every activation is charged
against and repinning it is a deliberate act with its own verification - the incumbents
must come back bit-identical. The report says when a symbol is missing from the pinned
snapshot and what to run; it does not run it.

**Choose the holdout boundary.** It computes the candidates and names the one it would
pick ([ADR-0020]), and writing it into a registry file is a diff someone reads.

**Decide whether a verdict is good.** It reports that one exists.

[ADR-0015]: ../docs/decisions/0015-databento-is-the-intraday-source-only.md
[ADR-0020]: ../docs/decisions/0020-the-holdout-boundary-is-per-activation.md

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
from decimal import Decimal
from decimal import InvalidOperation
from typing import TYPE_CHECKING

from copilot.calibration.cost_model import CostModel
from copilot.data.append import due_sessions
from copilot.data.calendar import trading_days
from copilot.data.catalog import bar_type_for
from copilot.data.catalog import equity_for
from copilot.data.catalog import open_catalog
from copilot.data.catalog import read_daily_bars
from copilot.data.marketstack import MarketstackClient
from copilot.strategies.activations import REGISTRY_DIR
from copilot.strategies.activations import find_activation
from copilot.strategies.fingerprint import fingerprint_for
from copilot.strategies.fingerprint import unchanged_since
from copilot.validation.holdout import EVALUATION_END
from copilot.validation.holdout import MAX_HOLDOUT_SHARE
from copilot.validation.holdout import MIN_HOLDOUT_SHARE


if TYPE_CHECKING:
    from collections.abc import Sequence


API_KEY_ENV = "MARKETSTACK_API_KEY"
CATALOG_PATH_ENV = "COPILOT_CATALOG_PATH"
DEFAULT_CATALOG = "~/.nautilus_copilot/catalog"

EARLIEST_START = date(2005, 1, 1)
"""
The window the survey probes.

Nothing here assumes the vendor reaches it.

"""

TARGET_REJECTION_RATIO = Decimal("0.015")
"""
The rate the recommended start date must come in under.

Below the backfill's own 2% refusal, deliberately. A start chosen to land exactly on the
gate has no room for the sessions the vendor has not published yet, and a backfill that
refuses after the survey said it would pass is worse than no survey.

"""

PENNY = Decimal("0.01")


@dataclass(frozen=True)
class Coverage:
    """
    What the daily vendor can actually supply for one symbol.
    """

    symbol: str
    venue: str
    rows: int
    first: date | None
    last: date | None
    recommended_start: date | None
    """
    Earliest year whose window comes in under :data:`TARGET_REJECTION_RATIO`.
    """
    unusable_by_year: dict[int, tuple[int, int]] = field(default_factory=dict)
    """
    Year to ``(unusable, fetched)``: closes that are null, non-numeric or sub-penny.
    """

    @property
    def covered(self) -> bool:
        """
        Whether the vendor returned anything at all for this symbol.
        """
        return self.rows > 0

    @property
    def unusable_tail(self) -> tuple[int, ...]:
        """
        The run of most-recent years the vendor cannot price, which a patch must cover.
        """
        return _unusable_tail(self.unusable_by_year)

    def stale_sessions(self, as_of: datetime) -> int:
        """
        Sessions the vendor should have published after its last row, and has not.

        The check that would have caught SPLG on 2026-09-04: it looks fully covered over
        a long window and its series simply stops on 2026-07-17, which a narrow probe
        reads as "no such symbol" and a long one reads as "1515 rows, fine". Neither is
        the useful answer, and a series this far behind cannot warm a live session
        whatever its history looks like.

        **Due**, not merely absent. Yesterday's session is not late until the vendor has
        had time to publish it, and counting it would make every symbol look one session
        stale every morning - the fastest way to teach an operator to ignore this line.
        The threshold is :mod:`copilot.data.append`'s, measured rather than assumed, so
        the two commands agree about what counts as missing.

        """
        if self.last is None:
            return 0
        due, _pending = due_sessions(self.last, as_of)
        return len(due)


@dataclass(frozen=True)
class Step:
    """
    One stage of onboarding, and whether this symbol has passed it.
    """

    name: str
    done: bool
    detail: str
    command: str = ""
    """
    What to run next, when this step is not done and the operator must act.
    """


def survey(client: MarketstackClient, symbol: str, venue: str) -> Coverage:
    """
    Ask the vendor what it holds for one symbol, and where a usable history starts.

    One fetch over the whole probe window, analysed offline. The alternative - fetching
    per candidate year - costs the same request quota several times over and answers a
    worse question, because the year boundaries only mean something once the whole shape
    is visible.

    """
    rows = list(client.fetch_eod([symbol], EARLIEST_START, date.today()))  # noqa: DTZ011
    if not rows:
        return Coverage(symbol, venue, 0, None, None, None)

    days = sorted(date.fromisoformat(row["date"][:10]) for row in rows)
    unusable: dict[int, list[int]] = {}
    for row in rows:
        year = int(row["date"][:4])
        counts = unusable.setdefault(year, [0, 0])
        counts[1] += 1
        if not _readable(row.get("close")):
            counts[0] += 1

    by_year = {year: (bad, total) for year, (bad, total) in sorted(unusable.items())}
    return Coverage(
        symbol=symbol,
        venue=venue,
        rows=len(rows),
        first=days[0],
        last=days[-1],
        recommended_start=_recommended_start(by_year),
        unusable_by_year=by_year,
    )


def holdout_candidates(
    catalog_path: str,
    symbol: str,
    venue: str,
) -> tuple[tuple[date, Decimal], ...]:
    """
    Return every quarter start whose holdout share lands inside the charter's band.

    Quarter starts rather than an exact percentile date, so the pin reads as a date
    someone chose rather than a number a script fitted ([ADR-0020]).

    """
    bars = read_daily_bars(open_catalog(catalog_path), bar_type_for(equity_for(symbol, venue).id))
    within = [bar for bar in bars if bar.closed_at < EVALUATION_END]
    if not within:
        return ()
    total = Decimal(len(within))
    out = []
    for year in range(within[0].closed_at.year, EVALUATION_END.year + 1):
        for month in (1, 4, 7, 10):
            pin = datetime(year, month, 1, tzinfo=UTC)
            held = sum(1 for bar in within if bar.closed_at >= pin)
            if held == 0 or held == len(within):
                continue
            share = Decimal(held) / total
            if MIN_HOLDOUT_SHARE <= share <= MAX_HOLDOUT_SHARE:
                out.append((pin.date(), share))
    return tuple(out)


def preferred_boundary(
    candidates: Sequence[tuple[date, Decimal]],
) -> tuple[date, Decimal] | None:
    """
    Return the candidate whose share sits nearest the middle of the band.

    A rule rather than a judgement, so two people onboarding the same symbol pin the
    same date and the choice does not quietly encode a preference for more or less
    holdout.

    """
    if not candidates:
        return None
    middle = (MIN_HOLDOUT_SHARE + MAX_HOLDOUT_SHARE) / 2
    return min(candidates, key=lambda pair: abs(pair[1] - middle))


def steps_for(catalog_path: str, symbol: str, venue: str, coverage: Coverage | None) -> list[Step]:
    """
    Return the ordered stages for one symbol, each answering whether it is done.

    Ordered because the order is the thing being captured. A later step reads state a
    earlier one wrote, and several give no sign they were skipped - a spread that is
    missing from the pinned snapshot does not fail until a validate run raises, by which
    point the operator is debugging the wrong thing.

    """
    steps: list[Step] = []
    pair = f"{symbol}.{venue}"

    if coverage is not None:
        if not coverage.covered:
            steps.append(
                Step(
                    "vendor coverage",
                    done=False,
                    detail="the vendor returns no rows for this symbol at all",
                    command="choose a different symbol; this one cannot be sourced",
                ),
            )
            return steps
        start = coverage.recommended_start
        stale = coverage.stale_sessions(datetime.now(tz=UTC))
        tail = coverage.unusable_tail
        notes = []
        if tail:
            notes.append(f"cannot price {', '.join(str(y) for y in tail)} - patch that tail")
        if stale:
            plural = "session" if stale == 1 else "sessions"
            notes.append(f"series ends {coverage.last}, {stale} {plural} behind")
        suffix = ("; " + "; ".join(notes)) if notes else ""
        steps.append(
            Step(
                "vendor coverage",
                done=start is not None and not stale,
                detail=(
                    f"{coverage.rows} rows {coverage.first}..{coverage.last}; "
                    f"usable from {start}{suffix}"
                    if start
                    else f"{coverage.rows} rows {coverage.first}..{coverage.last}; no year "
                    f"comes in under {TARGET_REJECTION_RATIO:.1%} unusable closes{suffix}"
                ),
                command=(
                    ""
                    if start and not stale
                    else "the vendor cannot price this series cleanly in any window"
                    if not start
                    else "a stale series cannot warm a live session; find another source "
                    "for this symbol or drop it"
                ),
            ),
        )

    bars = read_daily_bars(open_catalog(catalog_path), bar_type_for(equity_for(symbol, venue).id))
    start_hint = coverage.recommended_start if coverage else EARLIEST_START
    steps.append(
        Step(
            "catalog history",
            done=bool(bars),
            detail=(
                f"{len(bars)} bars {min(b.closed_at.date() for b in bars)}.."
                f"{max(b.closed_at.date() for b in bars)}"
                if bars
                else "nothing stored"
            ),
            command=(
                ""
                if bars
                else f"python -m copilot.data.backfill --symbols {symbol} "
                f"--from {start_hint or EARLIEST_START}"
            ),
        ),
    )

    if bars:
        held = {bar.closed_at.date() for bar in bars}
        holes = [d for d in trading_days(min(held), max(held)) if d not in held]
        steps.append(
            Step(
                "holes filled",
                done=not holes,
                detail="contiguous" if not holes else f"{len(holes)} sessions missing",
                command=(
                    ""
                    if not holes
                    else f"python -m copilot.data.patch --symbols {pair} --write  "
                    f"(needs the Databento pull below first)"
                ),
            ),
        )

    calibrated = symbol in CostModel.from_snapshot().bps_per_side
    steps.append(
        Step(
            "spread calibrated",
            done=calibrated,
            detail=(
                "in the pinned snapshot"
                if calibrated
                else "absent from the pinned snapshot; the gate will raise on it"
            ),
            command=(
                ""
                if calibrated
                else "python -m copilot.data.databento --pull --schema bbo-1m "
                f"--only {symbol} --from 2018-05-01 --to {date.today()} --spend, then "  # noqa: DTZ011
                "python -m copilot.calibration.spread_history --write, then repin "
                "CANONICAL_SNAPSHOT after checking the incumbents are unchanged"
            ),
        ),
    )

    registered = sorted(REGISTRY_DIR.glob("*.toml"))
    names = [
        path.stem
        for path in registered
        if f'symbol = "{symbol}"' in path.read_text() and f'venue = "{venue}"' in path.read_text()
    ]
    boundary = preferred_boundary(holdout_candidates(catalog_path, symbol, venue)) if bars else None
    steps.append(
        Step(
            "registered",
            done=bool(names),
            detail=", ".join(names) if names else "no activation names this instrument",
            command=(
                ""
                if names
                else f"write {REGISTRY_DIR.name}/<name>.toml"
                + (
                    f' with holdout_start = "{boundary[0]}" ({boundary[1]:.2%} held out)'
                    if boundary
                    else "; the boundary is computable once the history is stored"
                    if not bars
                    else "; no quarter boundary puts the holdout inside the charter's "
                    "band, so this history cannot carry a holdout at all"
                )
            ),
        ),
    )

    current: list[str] = []
    stale: list[str] = []
    if names and bars:
        cost_model = CostModel.from_snapshot()
        for name in names:
            activation = find_activation(name)
            fingerprint = fingerprint_for(activation, bars, cost_model)
            (current if unchanged_since(name, fingerprint) else stale).append(name)
    steps.append(
        Step(
            "validated",
            done=bool(names) and not stale,
            detail=(
                f"{len(current)}/{len(names)} verdicts computed from the current inputs"
                + (f"; stale or unfiled: {', '.join(stale)}" if stale else "")
                if names
                else "nothing to validate yet"
            ),
            command=(
                ""
                if not names or not stale
                else "python -m copilot.strategies.validate --changed --write"
            ),
        ),
    )
    return steps


def report(results: Sequence[tuple[str, list[Step]]]) -> int:
    """
    Print each symbol's stages and return the exit code an operator should act on.
    """
    incomplete = 0
    for pair, steps in results:
        print(f"\n  {pair}")
        for step in steps:
            mark = "done" if step.done else "TODO"
            print(f"    [{mark}] {step.name:18} {step.detail}")
            if not step.done and step.command:
                print(f"           -> {step.command}")
        if not all(step.done for step in steps):
            incomplete += 1
    print()
    if incomplete:
        print(
            f"{incomplete} symbol(s) not ready. The stages are ordered: a later one reads "
            f"what an earlier one wrote, so work down the list rather than across it.",
        )
    else:
        print("every symbol is onboarded: stored, contiguous, calibrated, registered, validated.")
    return 1 if incomplete else 0


def main(argv: list[str] | None = None) -> int:
    """
    Report where each requested symbol stands in the onboarding sequence.
    """
    parser = argparse.ArgumentParser(
        prog="python -m copilot.data.onboard",
        description="Report and advance a symbol's path to a filed verdict.",
    )
    parser.add_argument("--symbols", required=True, help="Comma-separated SYMBOL.VENUE pairs")
    parser.add_argument(
        "--catalog",
        default=os.environ.get(CATALOG_PATH_ENV, DEFAULT_CATALOG),
        help=f"Catalog directory (default: ${CATALOG_PATH_ENV} or {DEFAULT_CATALOG})",
    )
    parser.add_argument(
        "--survey",
        action="store_true",
        help="Ask the vendor what it holds (costs request quota; needs the API key)",
    )
    args = parser.parse_args(argv)

    pairs = []
    for token in args.symbols.split(","):
        symbol, _, venue = token.strip().partition(".")
        if not symbol or not venue:
            print(f"error: expected SYMBOL.VENUE, got {token.strip()!r}", file=sys.stderr)
            return 2
        pairs.append((symbol.upper(), venue.upper()))

    client = None
    if args.survey:
        access_key = os.environ.get(API_KEY_ENV)
        if not access_key:
            print(f"error: --survey needs {API_KEY_ENV}", file=sys.stderr)
            return 2
        client = MarketstackClient(access_key)

    print(f"Onboarding status at {datetime.now(tz=UTC).isoformat(timespec='seconds')}")
    results = []
    for symbol, venue in pairs:
        coverage = survey(client, symbol, venue) if client else None
        results.append((f"{symbol}.{venue}", steps_for(args.catalog, symbol, venue, coverage)))
    return report(results)


def _readable(close: object) -> bool:
    """
    Whether a vendor close is a price the ingestion gate would accept.

    The two failures seen in the wild are a null and a sub-penny value; both are checked
    here rather than in the gate's own words, because this runs before anything is
    fetched in bulk and its job is to predict the gate, not to be it.

    """
    if close is None:
        return False
    try:
        value = Decimal(str(close))
    except (InvalidOperation, ValueError):
        return False
    return value > 0 and value == value.quantize(PENNY)


def _recommended_start(by_year: dict[int, tuple[int, int]]) -> date | None:
    """
    Return the earliest year from which the unusable rate stays under the target.

    Earliest rather than cleanest: history is the scarce input for a walk-forward gate
    and for the holdout band, so the answer wanted is the longest window that passes, not
    the best one.

    Trailing years are excluded from the search, not from the answer. A vendor that has
    stopped pricing a series recently - TLT's 2026, where 122 of 169 sessions are
    unusable - has a problem no start date fixes, and searching over it would report that
    no window is clean when in fact every window before the break is. Those years are
    named separately by :func:`_unusable_tail` and are the patch's business, not the
    backfill's.

    """
    years = [year for year in sorted(by_year) if year not in _unusable_tail(by_year)]
    for index, year in enumerate(years):
        bad = sum(by_year[y][0] for y in years[index:])
        total = sum(by_year[y][1] for y in years[index:])
        if total and Decimal(bad) / Decimal(total) < TARGET_REJECTION_RATIO:
            return date(year, 1, 1)
    return None


def _unusable_tail(by_year: dict[int, tuple[int, int]]) -> tuple[int, ...]:
    """
    Return the run of most-recent years the vendor cannot price, if any.

    A break at the end of a series and a bad patch in the middle need opposite
    responses, and telling them apart is the whole reason this is separate from the
    start search.

    """
    tail = []
    for year in sorted(by_year, reverse=True):
        bad, total = by_year[year]
        if total and Decimal(bad) / Decimal(total) >= TARGET_REJECTION_RATIO:
            tail.append(year)
        else:
            break
    return tuple(sorted(tail))


__all__ = [
    "TARGET_REJECTION_RATIO",
    "Coverage",
    "Step",
    "holdout_candidates",
    "preferred_boundary",
    "report",
    "steps_for",
    "survey",
]


if __name__ == "__main__":
    sys.exit(main())
