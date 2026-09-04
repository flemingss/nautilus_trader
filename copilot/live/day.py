"""
The operator's day as two commands, so the sequence lives in code.

    python -m copilot.live.day morning      # ~07:00 JST, after the US close
    python -m copilot.live.day evening      # ~21:30 JST, an hour before the US open

Why a sequence and not a list
-----------------------------
The third walk of ``docs/DRAFT_OPERATOR_DAY.md`` measured the whole day at under four
minutes of machine time and eight commands, in an order that existed only in that file.
The corporate-actions scan had been in its morning table since the first pass and was not
in the sequence the onboarding drill followed, so it did not run, and a symbol passed the
gate on a series with two unregistered splits in it - one inside a holdout that had
already been spent. A tool that exists is not a tool that runs. The fix is not a better
scan; it is that the command owns the order, and the order includes the scan.

Each step is one of the existing commands, run as its own process with its output on the
terminal, its exit code read, and its seconds recorded. Nothing here re-implements a
step; a step that fails prints what it always printed. What this adds is the *gating*:
the scan blocking the verdict, the preflight blocking the basket, and the sweep running
whatever happened before it.

What the day needs exported, checked first
------------------------------------------
Three variables, two of them in no ``.env``, one of which fails opaquely. They are checked
before any step runs, and the refusal names them.

The clock
---------
The operator is in Japan and the market is not. The evening prints the session it is
preparing for on both clocks, and on a scheduled early close says so: the session ends at
13:00 Eastern, three hours sooner, and so does everything that waits on the close.

"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import date
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from copilot.data.calendar import EASTERN
from copilot.data.calendar import early_closes
from copilot.data.calendar import session_close
from copilot.data.calendar import session_open
from copilot.data.calendar import trading_days
from copilot.live.session import PAPER_ACCOUNT_ENV
from copilot.live.session import add_connection_arguments
from copilot.live.warmup import session_to_prepare
from copilot.paths import MARKETSTACK_API_KEY_ENV
from copilot.paths import add_catalog_argument
from copilot.strategies.activations import load_activations


OUT_DIR = Path(__file__).parent / "out"

MORNING = "morning"
EVENING = "evening"
PHASES = (MORNING, EVENING)

OPERATOR_ZONE = ZoneInfo("Asia/Tokyo")
"""
The operator's clock, for the second column of every time printed.
"""

EXECUTION_WINDOW = timedelta(hours=2)
"""
The charter's predeclared execution window: the first one to two hours after the open.
"""

TIMEZONE_ALIASES_ENV = "IBAPI_TIMEZONE_ALIASES"
REQUIRED_TIMEZONE_ALIAS = "JST=Asia/Tokyo"
"""
Without this alias every IB connect fails, and the failure names nothing.
"""

CORPORATE_ACTIONS_FROM = "2005-01-01"


@dataclass(frozen=True)
class Step:
    """
    One command in the day, and whether its failure ends the sequence.
    """

    name: str
    module: str
    argv: tuple[str, ...] = ()
    stops_on_failure: bool = True
    why: str = ""
    """
    One line on what a failure here protects, printed when it stops the day.
    """

    @property
    def command(self) -> tuple[str, ...]:
        """
        The full command line, as the operator would have typed it.
        """
        return ("python", "-m", self.module, *self.argv)


@dataclass
class StepResult:
    """
    What one step did.
    """

    name: str
    command: tuple[str, ...]
    exit_code: int | None
    seconds: float
    skipped: bool = False

    @property
    def passed(self) -> bool:
        """
        Whether the step ran and exited zero.
        """
        return self.exit_code == 0


@dataclass(frozen=True)
class SessionClock:
    """
    The instants that shape one session, so they can be printed on two clocks.
    """

    session: date
    opens: datetime
    window_ends: datetime
    closes: datetime
    early_close: bool

    def lines(self, zone: ZoneInfo = OPERATOR_ZONE) -> list[str]:
        """
        Return the clock as an operator reads it: Eastern, then their own.
        """
        out = [
            _clock_line("open", self.opens, zone),
            _clock_line("window ends", self.window_ends, zone),
            _clock_line("close", self.closes, zone),
        ]
        if self.early_close:
            out.append(
                "EARLY CLOSE: the session ends at 13:00 Eastern, three hours sooner, and "
                "so does everything that waits on the close",
            )
        return out


def _clock_line(label: str, instant: datetime, zone: ZoneInfo) -> str:
    eastern = instant.astimezone(EASTERN)
    local = instant.astimezone(zone)
    day_shift = " (next day)" if local.date() > eastern.date() else ""
    return (
        f"{label:<14}{eastern.strftime('%H:%M')} {eastern.tzname()}   "
        f"{local.strftime('%H:%M')} {local.tzname()}{day_shift}"
    )


def session_clock(session: date) -> SessionClock:
    """
    Return the open, the end of the execution window and the close of one session.
    """
    opens, closes = session_open(session), session_close(session)
    return SessionClock(
        session=session,
        opens=opens,
        window_ends=min(opens + EXECUTION_WINDOW, closes),
        closes=closes,
        early_close=session in early_closes(session.year),
    )


def closed_session(now: datetime) -> date | None:
    """
    Return the most recent session whose close is behind ``now``.

    None when no session closed in the last fortnight.

    """
    today = now.astimezone(EASTERN).date()
    behind = [d for d in trading_days(today - timedelta(days=14), today) if session_close(d) <= now]
    return behind[-1] if behind else None


@dataclass(frozen=True)
class Connection:
    """
    The broker connection the evening passes on to each step.
    """

    host: str
    port: int
    account: str

    @property
    def argv(self) -> tuple[str, ...]:
        """
        The flags every broker command takes.
        """
        return ("--host", self.host, "--port", str(self.port), "--account", self.account)


def required_environment(
    phase: str,
    *,
    environ: Mapping[str, str],
    account: str = "",
) -> tuple[str, ...]:
    """
    Return what the phase needs exported and does not have, one line each.
    """
    missing: list[str] = []
    if phase == MORNING and not environ.get(MARKETSTACK_API_KEY_ENV):
        missing.append(f"{MARKETSTACK_API_KEY_ENV}: append and the corporate-actions scan need it")
    if phase == EVENING:
        aliases = environ.get(TIMEZONE_ALIASES_ENV, "")
        if REQUIRED_TIMEZONE_ALIAS not in aliases:
            missing.append(
                f'{TIMEZONE_ALIASES_ENV}="{REQUIRED_TIMEZONE_ALIAS}": without it every IB '
                f"connect fails opaquely",
            )
        if not account:
            missing.append(f"{PAPER_ACCOUNT_ENV} (or --account): the paper account to connect to")
    return tuple(missing)


def registered_symbols() -> tuple[str, ...]:
    """
    Every symbol the registry names, once, in name order.
    """
    return tuple(dict.fromkeys(a.symbol for a in load_activations()))


def morning_steps(catalog: str, *, today: date) -> tuple[Step, ...]:
    """
    Return the morning: catalog current, actions checked, verdicts recomputed, compared.

    The scan stops the sequence because a verdict filed over a split sitting in the
    prices is the defect that motivated this module. The append does not: a hole in
    yesterday's bar cannot move a verdict (the window is pinned, ADR-0017) and the
    evening's warm-up refuses it on its own, so the morning reports it and carries on.

    """
    return (
        Step(
            "append",
            "copilot.data.append",
            ("--catalog", catalog),
            stops_on_failure=False,
            why="the catalog is behind the last published session; the warm-up will refuse",
        ),
        Step(
            "corporate actions",
            "copilot.data.corporate_actions",
            (
                ",".join(registered_symbols()),
                "--catalog",
                catalog,
                "--from",
                CORPORATE_ACTIONS_FROM,
                "--to",
                today.isoformat(),
            ),
            why="a split is sitting in a stored series; no verdict may be filed over it",
        ),
        Step(
            "validate",
            "copilot.strategies.validate",
            ("--changed", "--write", "--catalog", catalog),
            why="a verdict could not be recomputed",
        ),
        Step(
            "compare",
            "copilot.live.compare",
            ("--catalog", catalog),
            stops_on_failure=False,
            why="the last session decided differently from the replay",
        ),
    )


def evening_steps(
    catalog: str,
    *,
    session: date,
    connection: Connection,
    allocation: Decimal | None,
    risk_fraction: Decimal | None,
) -> tuple[Step, ...]:
    """
    Return the evening: prove the environment and the catalog, run the basket, sweep.

    The sweep runs whatever happened before it. It is the monitoring-end policy's last
    line, and a basket that crashed is the case in which an order is most likely to have
    been left behind.

    """
    sizing: tuple[str, ...] = ()
    if allocation is not None:
        sizing += ("--allocation", str(allocation))
    if risk_fraction is not None:
        sizing += ("--risk-fraction", str(risk_fraction))
    return (
        Step(
            "preflight",
            "copilot.live.preflight",
            connection.argv,
            why="the broker, the account, the instruments or the quotes are not as expected",
        ),
        Step(
            "warmup",
            "copilot.live.warmup",
            ("--catalog", catalog, "--session", session.isoformat()),
            why="the catalog cannot warm every activation for this session",
        ),
        Step(
            "basket",
            "copilot.live.run_activation",
            (
                "--all",
                "--catalog",
                catalog,
                "--session",
                session.isoformat(),
                *connection.argv,
                *sizing,
            ),
            stops_on_failure=False,
            why="a strategy did not run cleanly; the sweep still runs",
        ),
        Step(
            "sweep",
            "copilot.live.cancel_working",
            ("--all", *connection.argv),
            stops_on_failure=False,
            why="an order is still working; confirm against the broker's own list",
        ),
    )


def run_module(step: Step) -> int:
    """
    Run one step as its own process, output on the terminal, and return its exit code.

    The parent's own output is flushed before the child starts, because the child writes
    straight to the terminal and a buffered header would land after the step it names.

    A process each, rather than importing and calling, because each step builds its own
    node and its own logging, and because a step's exit code is the contract every one
    of them was written to.

    """
    sys.stdout.flush()
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", step.module, *step.argv],
        check=False,
    )
    return completed.returncode


def run_steps(
    steps: tuple[Step, ...],
    *,
    runner: Callable[[Step], int] = run_module,
) -> tuple[StepResult, ...]:
    """
    Run the steps in order, stopping after a failure that stops, and record each.
    """
    results: list[StepResult] = []
    stopped = False
    for index, step in enumerate(steps, start=1):
        if stopped:
            results.append(StepResult(step.name, step.command, None, 0.0, skipped=True))
            continue
        rule = "=" * 78
        print(
            f"\n{rule}\n  {index}/{len(steps)}  {step.name:<18}{' '.join(step.command)}\n{rule}\n",
        )
        started = time.monotonic()
        code = runner(step)
        seconds = time.monotonic() - started
        results.append(StepResult(step.name, step.command, code, seconds))
        if code != 0 and step.stops_on_failure:
            print(f"\n  {step.name} exited {code}: {step.why}. Stopping here.", flush=True)
            stopped = True
    return tuple(results)


def summarise(results: tuple[StepResult, ...]) -> int:
    """
    Print the day's table and return its exit code.
    """
    print(f"\n{'step':<20}{'result':<10}{'seconds':>8}")
    for r in results:
        if r.skipped:
            print(f"{r.name:<20}{'skipped':<10}{'-':>8}")
        else:
            print(
                f"{r.name:<20}{'PASS' if r.passed else f'exit {r.exit_code}':<10}{r.seconds:>8.1f}",
            )
    total = sum(r.seconds for r in results)
    print(f"{'':<20}{'':<10}{total:>8.1f}")
    return 0 if all(r.passed for r in results) else 1


@dataclass
class DayRecord:
    """
    What one invocation of the day did, filed under ``out/``.
    """

    phase: str
    run_at: str
    session: str | None
    dry_run: bool
    clock: list[str] = field(default_factory=list)
    steps: list[dict[str, object]] = field(default_factory=list)
    exit_code: int = 0


def main(argv: list[str] | None = None) -> int:
    """
    Run one phase of the operator's day.
    """
    parser = argparse.ArgumentParser(
        prog="python -m copilot.live.day",
        description="The operator's day: morning after the close, evening before the open.",
    )
    parser.add_argument("phase", choices=PHASES)
    add_catalog_argument(parser)
    add_connection_arguments(parser)
    parser.add_argument("--session", help="Session the evening prepares for (default: the next)")
    parser.add_argument("--allocation", type=Decimal, default=None, help="Passed to the basket")
    parser.add_argument("--risk-fraction", type=Decimal, default=None, help="Passed to the basket")
    parser.add_argument("--dry-run", action="store_true", help="Print the sequence; run nothing")
    args = parser.parse_args(argv)

    now = datetime.now(tz=UTC)
    missing = required_environment(args.phase, environ=os.environ, account=args.account)
    if missing and not args.dry_run:
        print("refused: the day needs these exported first")
        for line in missing:
            print(f"  {line}")
        return 2

    record = DayRecord(phase=args.phase, run_at=now.isoformat(), session=None, dry_run=args.dry_run)
    if args.phase == MORNING:
        closed = closed_session(now)
        record.session = closed.isoformat() if closed else None
        print(
            f"Morning of {now.astimezone(OPERATOR_ZONE):%Y-%m-%d %a %H:%M %Z}: the session of "
            f"{closed.isoformat() if closed else '?'} has closed; next session "
            f"{session_to_prepare(now).isoformat()}",
        )
        steps = morning_steps(args.catalog, today=now.astimezone(EASTERN).date())
    else:
        session = date.fromisoformat(args.session) if args.session else session_to_prepare(now)
        clock = session_clock(session)
        record.session = session.isoformat()
        record.clock = clock.lines()
        print(f"Evening for the session of {session.isoformat()} ({session:%A})")
        for line in clock.lines():
            print(f"  {line}")
        steps = evening_steps(
            args.catalog,
            session=session,
            connection=Connection(host=args.host, port=args.port, account=args.account),
            allocation=args.allocation,
            risk_fraction=args.risk_fraction,
        )

    if args.dry_run:
        print("\nWould run, in order:")
        for step in steps:
            gate = "stops the day on failure" if step.stops_on_failure else "reports and continues"
            print(f"  {step.name:<18}{' '.join(step.command)}\n  {'':<18}({gate})")
        return 0

    results = run_steps(steps)
    exit_code = summarise(results)
    record.steps = [vars(r) for r in results]
    record.exit_code = exit_code
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"day_{args.phase}_{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(vars(record), indent=2, default=str) + "\n")
    print(f"\n  filed {path}")
    return exit_code


__all__ = [
    "EVENING",
    "EXECUTION_WINDOW",
    "MORNING",
    "OPERATOR_ZONE",
    "PHASES",
    "REQUIRED_TIMEZONE_ALIAS",
    "TIMEZONE_ALIASES_ENV",
    "Connection",
    "DayRecord",
    "SessionClock",
    "Step",
    "StepResult",
    "closed_session",
    "evening_steps",
    "morning_steps",
    "registered_symbols",
    "required_environment",
    "run_module",
    "run_steps",
    "session_clock",
    "summarise",
]


if __name__ == "__main__":
    sys.exit(main())
