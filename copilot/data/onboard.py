"""
Bring a new symbol from "we would like to trade this" to a filed verdict.

    python -m copilot.data.onboard --symbols SCHX.ARCX,TLT.XNAS
    python -m copilot.data.onboard --symbols SCHX.ARCX --survey --apply
    python -m copilot.data.onboard --symbols IJH.ARCX --survey --apply \
        --like spy-gap-fade-long-next-close --minimum-effect-r 0

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

What ``--apply`` takes
----------------------
The status is recomputed after every step, and the first stage not done is taken **only if
it is free**: the backfill from the recommended start, the patch when the Databento store
can fill at least one hole, the registry file when ``--like`` names an activation to copy,
and the validate. Each runs as its own process, the way the operator day runs its steps. It
stops at the first stage that is a deliberate act, at a step that fails, and at a step that
ran and is still not done, which means the step is not recording its own completion.

It needs ``--survey``, because the corporate-actions scan is a stage it may not skip: the
drill that motivated this module passed a symbol over a fake gap for want of it.

What it will not do for you
---------------------------
**Spend money.** The metered pull is named and priced by its own command, never run from
here, exactly as [ADR-0015] requires.

**Recalibrate the cost model.** That rebuilds the snapshot every activation is charged
against and repinning it is a deliberate act with its own verification - the incumbents
must come back bit-identical. The report says when a symbol is missing from the pinned
snapshot and what to run; it does not run it.

**Change code.** A corporate action sitting in the prices is registered by hand.

**Choose the holdout boundary.** It computes the candidates and names the one it would
pick ([ADR-0020]). With ``--like`` it writes the registry file at that boundary, and the file
is still a diff someone reads before it is committed.

**Declare the effect size.** ``--like`` refuses without ``--minimum-effect-r``: zero is a
declaration and empty is not ([ADR-0031]).

**Decide whether a verdict is good.** It reports that one exists.

[ADR-0015]: ../docs/decisions/0015-databento-is-the-intraday-source-only.md
[ADR-0020]: ../docs/decisions/0020-the-holdout-boundary-is-per-activation.md
[ADR-0031]: ../docs/decisions/0031-evidence-and-attribution-after-the-audit.md

"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import date
from datetime import datetime
from decimal import Decimal
from decimal import InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

from copilot.calibration.cost_model import CostModel
from copilot.data.append import due_sessions
from copilot.data.calendar import trading_days
from copilot.data.catalog import bar_type_for
from copilot.data.catalog import equity_for
from copilot.data.catalog import open_catalog
from copilot.data.catalog import read_daily_bars
from copilot.data.corporate_actions import scan
from copilot.data.databento import DEFAULT_STORE
from copilot.data.marketstack import MarketstackClient
from copilot.data.patch import plan
from copilot.paths import MARKETSTACK_API_KEY_ENV
from copilot.paths import add_catalog_argument
from copilot.strategies.activations import REGISTRY_DIR
from copilot.strategies.activations import Lifecycle
from copilot.strategies.activations import find_activation
from copilot.strategies.activations import parse_activation
from copilot.strategies.fingerprint import fingerprint_for
from copilot.strategies.fingerprint import unchanged_since
from copilot.validation.holdout import EVALUATION_END
from copilot.validation.holdout import MAX_HOLDOUT_SHARE
from copilot.validation.holdout import MIN_HOLDOUT_SHARE


if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Mapping
    from collections.abc import Sequence


API_KEY_ENV = MARKETSTACK_API_KEY_ENV

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
    action: tuple[str, ...] = ()
    """
    The free command that takes this step, as a module and its arguments.

    Empty when the step is a deliberate act - money, a code change, or a judgement - which
    is where ``--apply`` stops.

    """


@dataclass(frozen=True)
class StepOptions:
    """
    What the status may consult beyond the catalog, and what ``--apply`` may write.
    """

    client: object | None = None
    """
    The daily vendor's client; the corporate-actions scan needs it.
    """
    store: Path | None = None
    """
    The Databento store, so the holes stage can say how many the patch could fill.
    """
    like: str = ""
    """
    An activation to register a new symbol like.
    """
    minimum_effect_r: str | None = None
    """
    The effect size a registration declares; required with ``like``.
    """


MAX_ACTIONS = 8
"""
More actions than there are stages means a stage is not recording its own completion.
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


def steps_for(
    catalog_path: str,
    symbol: str,
    venue: str,
    coverage: Coverage | None,
    options: StepOptions | None = None,
) -> list[Step]:
    """
    Return the ordered stages for one symbol, each answering whether it is done.

    Ordered because the order is the thing being captured. A later step reads state a
    earlier one wrote, and several give no sign they were skipped - a spread that is
    missing from the pinned snapshot does not fail until a validate run raises, by which
    point the operator is debugging the wrong thing.

    """
    options = options or StepOptions()
    client = options.client
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
            action=(
                (
                    "copilot.data.backfill",
                    "--symbols",
                    symbol,
                    "--from",
                    str(coverage.recommended_start),
                    "--catalog",
                    catalog_path,
                )
                if not bars and coverage is not None and coverage.recommended_start
                else ()
            ),
        ),
    )

    if bars:
        held = {bar.closed_at.date() for bar in bars}
        holes = [d for d in trading_days(min(held), max(held)) if d not in held]
        steps.append(_holes_step(catalog_path, symbol, venue, len(holes), options.store))

    if bars and client is not None:
        findings = scan(
            catalog_path,
            [symbol],
            min(b.closed_at.date() for b in bars),
            date.today(),  # noqa: DTZ011
            client=client,
        )
        blocking = [f for f in findings if f.blocks]
        steps.append(
            Step(
                "corporate actions",
                done=not blocking,
                detail=(
                    f"{len(blocking)} sitting in the prices: "
                    + ", ".join(
                        f"{f.action.effective.date()} x{f.action.factor}"
                        for f in blocking
                        if f.action
                    )
                    if blocking
                    else f"{sum(1 for f in findings if f.action)} vendor action(s), "
                    "all registered or adjusted"
                ),
                command=(
                    f"add the action(s) to ACTIONS in copilot/data/corporate_actions.py, then "
                    f"python -m copilot.data.corporate_actions {symbol}"
                    if blocking
                    else ""
                ),
            ),
        )
    elif bars:
        steps.append(
            Step(
                "corporate actions",
                done=False,
                detail="not checked without --survey",
                command=f"python -m copilot.data.corporate_actions {symbol}",
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
            action=() if names else _registration_action(pair, boundary, options),
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
            action=(
                ("copilot.strategies.validate", "--changed", "--write", "--catalog", catalog_path)
                if names and stale
                else ()
            ),
        ),
    )
    return steps


def fillable_holes(catalog_path: str, symbol: str, venue: str, store: Path | None) -> int | None:
    """
    Return how many holes the Databento store can fill, or None without a store.
    """
    if store is None or not store.is_dir():
        return None
    try:
        return len(plan(catalog_path, symbol, venue, store).fills)
    except (KeyError, FileNotFoundError):
        # No listing dataset for the venue, or nothing pulled for it: the store prices none.
        return 0


def _holes_step(
    catalog_path: str,
    symbol: str,
    venue: str,
    missing: int,
    store: Path | None,
) -> Step:
    """
    Return the holes stage, saying how many of them the store could fill.

    Counted because the answer decides what ``--apply`` may do. A hole the store can price
    is a free step. One it cannot is a refusal to look at, and running the patch again would
    not change it: GLDM's thirteen 2018-2019 holes are sessions with no closing auction on
    the listing venue, so no official close exists to fill them with.

    """
    pair = f"{symbol}.{venue}"
    if not missing:
        return Step("holes filled", done=True, detail="contiguous")
    fillable = fillable_holes(catalog_path, symbol, venue, store)
    if fillable is None:
        return Step(
            "holes filled",
            done=False,
            detail=f"{missing} sessions missing",
            command=f"python -m copilot.data.patch --symbols {pair} --write  "
            f"(needs the Databento pull below first)",
        )
    if fillable == 0:
        return Step(
            "holes filled",
            done=False,
            detail=f"{missing} sessions missing, none fillable from the store",
            command=f"python -m copilot.data.patch --symbols {pair} says why; a hole no "
            f"source can price is looked at, not forced",
        )
    return Step(
        "holes filled",
        done=False,
        detail=f"{missing} sessions missing, {fillable} fillable from the store",
        command=f"python -m copilot.data.patch --symbols {pair} --write",
        action=(
            "copilot.data.patch",
            "--symbols",
            pair,
            "--store",
            str(store),
            "--write",
            "--catalog",
            catalog_path,
        ),
    )


def _registration_action(
    pair: str,
    boundary: tuple[date, Decimal] | None,
    options: StepOptions,
) -> tuple[str, ...]:
    """
    Return the command that registers ``pair`` like another activation, or nothing.
    """
    if not options.like or options.minimum_effect_r is None or boundary is None:
        return ()
    return (
        "copilot.data.onboard",
        "--register",
        pair,
        "--like",
        options.like,
        "--holdout-start",
        boundary[0].isoformat(),
        "--minimum-effect-r",
        options.minimum_effect_r,
    )


def apply_steps(
    pair: str,
    compute: Callable[[], list[Step]],
    runner: Callable[[tuple[str, ...]], int],
) -> int:
    """
    Take the free stages in order, recomputing the status after each one.

    Returns an exit code, zero only when every stage is done. It stops at a stage that is a
    deliberate act, at a stage whose command fails, and at a stage that ran and is still not
    done - repeating that one would loop on a step that does not record its own completion.

    """
    last = ""
    for _ in range(MAX_ACTIONS):
        pending = next((step for step in compute() if not step.done), None)
        if pending is None:
            print(f"  {pair}: every stage is done")
            return 0
        if not pending.action:
            print(
                f"  {pair}: stopped at {pending.name!r}, a deliberate act: "
                f"{pending.command or pending.detail}",
            )
            return 1
        if pending.name == last:
            print(
                f"  {pair}: {pending.name!r} ran and is still not done ({pending.detail}); "
                f"stopping rather than repeating it",
            )
            return 1
        print(f"  {pair}: taking {pending.name!r}: python -m {' '.join(pending.action)}")
        code = runner(pending.action)
        if code != 0:
            print(f"  {pair}: {pending.name!r} exited {code}; stopping")
            return code
        last = pending.name
    print(f"  {pair}: took {MAX_ACTIONS} actions without finishing; stopping")
    return 1


def run_module(action: tuple[str, ...]) -> int:
    """
    Run one stage's command as its own process, and return its exit code.
    """
    sys.stdout.flush()
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", *action],
        check=False,
    ).returncode


def registration_name(like: str, like_symbol: str, symbol: str) -> str:
    """
    Return the new activation's name: the template's, with its symbol swapped.
    """
    prefix = f"{like_symbol.lower()}-"
    if not like.startswith(prefix):
        raise ValueError(
            f"cannot name a registration like {like!r}: the name does not start with its own "
            f"symbol, {prefix!r}, so there is nothing to swap",
        )
    return f"{symbol.lower()}-{like[len(prefix) :]}"


def render_registration(
    template: Mapping[str, Any],
    *,
    pair: str,
    like: str,
    holdout_start: str,
    minimum_effect_r: str,
) -> str:
    """
    Return a registry file for a new instrument, with the template's strategy and knobs.

    Always ``RESEARCH``: a registration copies a premise onto an instrument that has no
    verdict yet, and promotion is a diff someone makes after one exists.

    """
    symbol, _, venue = pair.upper().partition(".")
    note = (
        f"Registered by copilot.data.onboard like {like}: the same strategy and knobs on a new "
        f"instrument. The holdout starts at the quarter nearest the middle of the charter's "
        f"band (ADR-0020), and the effect size was declared at registration (ADR-0031)."
    )
    try:
        Decimal(minimum_effect_r)
    except (InvalidOperation, ValueError) as e:
        raise ValueError(
            f"minimum_effect_r {minimum_effect_r!r} is not a declaration: zero is, empty is not "
            f"(ADR-0031)",
        ) from e
    validation = {
        **template.get("validation", {}),
        "holdout_start": holdout_start,
        "minimum_effect_r": minimum_effect_r,
    }
    lines = [
        f"strategy = {_toml_value(template['strategy'])}",
        f"lifecycle = {_toml_value(str(Lifecycle.RESEARCH))}",
        f"note = {_toml_value(note)}",
        "",
        "[instrument]",
        f"symbol = {_toml_value(symbol)}",
        f"venue = {_toml_value(venue)}",
        "",
        "[parameters]",
        *(f"{k} = {_toml_value(v)}" for k, v in template.get("parameters", {}).items()),
        "",
        "[validation]",
        *(f"{k} = {_toml_value(v)}" for k, v in validation.items()),
    ]
    return "\n".join(lines) + "\n"


def register(
    pair: str,
    *,
    like: str,
    holdout_start: str,
    minimum_effect_r: str,
    directory: Path = REGISTRY_DIR,
) -> Path:
    """
    Write a registry file for ``pair`` like the activation named, and return its path.

    Refuses to overwrite, and parses what it wrote before writing it, so a file this
    puts in the registry is one the registry can load.

    """
    symbol = pair.partition(".")[0]
    template = tomllib.loads((directory / f"{like}.toml").read_text())
    name = registration_name(like, str(template["instrument"]["symbol"]), symbol)
    path = directory / f"{name}.toml"
    if path.exists():
        raise FileExistsError(f"{path} already exists; a registration is written once")
    text = render_registration(
        template,
        pair=pair,
        like=like,
        holdout_start=holdout_start,
        minimum_effect_r=minimum_effect_r,
    )
    parse_activation(name, tomllib.loads(text))
    path.write_text(text)
    return path


def _toml_value(value: object) -> str:
    """
    Return a registry value in TOML: strings quoted, integers and booleans bare.

    A float is refused. The registry writes numbers as strings because a TOML float is a
    binary float, and these values place stops.

    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value)
    raise ValueError(f"cannot write {value!r} into a registry file; numbers are strings there")


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
    parser.add_argument("--symbols", help="Comma-separated SYMBOL.VENUE pairs")
    add_catalog_argument(parser)
    parser.add_argument(
        "--survey",
        action="store_true",
        help="Ask the vendor what it holds (costs request quota; needs the API key)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Take the free stages in order, stopping at the first deliberate act",
    )
    parser.add_argument("--store", default=DEFAULT_STORE, help="The Databento store")
    parser.add_argument("--like", help="Register a new symbol like this activation")
    parser.add_argument("--minimum-effect-r", help="The effect size a registration declares")
    parser.add_argument("--register", metavar="SYMBOL.VENUE", help="Write one registry file")
    parser.add_argument("--holdout-start", help="The boundary a --register file carries")
    args = parser.parse_args(argv)

    problem = _refusal(args)
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 2
    if args.register:
        path = register(
            args.register,
            like=args.like,
            holdout_start=args.holdout_start,
            minimum_effect_r=args.minimum_effect_r,
        )
        print(f"wrote {path}")
        return 0

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

    options = StepOptions(
        client=client,
        store=Path(args.store).expanduser(),
        like=args.like or "",
        minimum_effect_r=args.minimum_effect_r,
    )
    print(f"Onboarding status at {datetime.now(tz=UTC).isoformat(timespec='seconds')}")
    results = []
    for symbol, venue in pairs:
        coverage = survey(client, symbol, venue) if client else None
        pair = f"{symbol}.{venue}"

        def status(
            symbol: str = symbol,
            venue: str = venue,
            coverage: Coverage | None = coverage,
        ) -> list[Step]:
            return steps_for(args.catalog, symbol, venue, coverage, options)

        if args.apply:
            apply_steps(pair, status, run_module)
        results.append((pair, status()))
    return report(results)


def _refusal(args: argparse.Namespace) -> str:
    """
    Return why this combination of flags must not run, or an empty string if it may.

    Each refusal guards a stage or a declaration the flags would otherwise skip.

    """
    if args.register:
        if not (args.like and args.holdout_start and args.minimum_effect_r is not None):
            return "--register needs --like, --holdout-start and --minimum-effect-r"
        return ""
    if not args.symbols:
        return "--symbols is required"
    if args.apply and not args.survey:
        return "--apply needs --survey; the corporate-actions scan is a stage it may not skip"
    if bool(args.like) != (args.minimum_effect_r is not None):
        return "--like and --minimum-effect-r go together; zero is a declaration, empty is not"
    return ""


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
    "MAX_ACTIONS",
    "TARGET_REJECTION_RATIO",
    "Coverage",
    "Step",
    "StepOptions",
    "apply_steps",
    "fillable_holes",
    "holdout_candidates",
    "preferred_boundary",
    "register",
    "registration_name",
    "render_registration",
    "report",
    "run_module",
    "steps_for",
    "survey",
]


if __name__ == "__main__":
    sys.exit(main())
