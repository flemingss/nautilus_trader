"""
The operator's day as two commands, so the sequence lives in code.

    python -m copilot.live.day morning      # after the US close
    python -m copilot.live.day evening      # an hour before the US open
    python -m copilot.live.day sweep        # monitoring end, once orders are enabled
    python -m copilot.live.day evening --scheduled   # from a timer: decides for itself

The schedule is anchored to the session, not to a wall clock. Written both ways, because
the playbook's tables are in JST and the operator is not always there:

    phase      Eastern        JST
    morning    17:00          07:00 (next day)
    evening    08:30          22:30
    sweep      10:30          00:30 (next day)

``COPILOT_OPERATOR_TZ`` sets the second column each command prints beside Eastern; it
defaults to ``Asia/Tokyo`` and is dropped when it would repeat Eastern. It is display
only - every session decision keys off ``EASTERN`` - and it is **not** the IB connect
alias below, which is required wherever the operator is.

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

Fired by a timer
----------------
A timer fires on a clock, and a clock does not know the exchange's calendar. Without a
guard, a Saturday ``day evening`` prepares Monday's session and Monday's prepares it again,
filing two decision records for one session; a holiday morning recomputes the session
before it. ``--scheduled`` makes each phase decide for itself: it acts only on **today's**
session in Eastern time - a morning after today's close, an evening before today's open, a
sweep after it - and exits 0 saying there is nothing to do otherwise. A morning or evening
that already passed for its session is not run again. Hand-run phases are unchanged.

Who is told
-----------
The day is where every step's exit code is read, so it is where alerting is wired
([ADR-0023](../docs/decisions/0023-a-critical-alert-demands-acknowledgement.md) sets the
severities). A step that fails raises a ``WARNING`` naming the step and what its failure
protects; a stopped day is the safe direction, so it does not wake anyone. The sweep raises
its own ``CRITICAL`` - an order that may still be working is the case that must - so the day
does not repeat it. Every morning that runs ends with an ``INFO`` summary, and every morning,
run or not, pings ``COPILOT_HEARTBEAT_URL`` for a watcher off the host - **except while a
latch no operator engaged holds**: a CRITICAL that expired unanswered or never arrived means
nobody knows, and the missed beat is how the watcher, which does not depend on Pushover,
finds out. A scheduled run refuses without alerting credentials: an unattended run nobody is
told about when it breaks is exactly what the playbook forbids.

The acknowledgement check runs first in every phase and **cannot stop it**. Until 2026-09-11
a Pushover outage with one CRITICAL outstanding raised there, before the session check and
before the sweep (``docs/AUDIT_2026-09-11.md``, F2).

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
from zoneinfo import ZoneInfoNotFoundError

from copilot.data.calendar import EASTERN
from copilot.data.calendar import early_closes
from copilot.data.calendar import is_trading_day
from copilot.data.calendar import session_close
from copilot.data.calendar import session_open
from copilot.data.calendar import trading_days
from copilot.live.alerting import Alert
from copilot.live.alerting import Severity
from copilot.live.alerting import alerter_from_environment
from copilot.live.halt import Latch
from copilot.live.halt import engaged_automatically
from copilot.live.halt import read_latch
from copilot.live.heartbeat import ping
from copilot.live.kill import acknowledgement_check
from copilot.live.kill import describe as describe_latch
from copilot.live.session import PAPER_ACCOUNT_ENV
from copilot.live.session import add_connection_arguments
from copilot.live.warmup import session_to_prepare
from copilot.paths import DEFAULT_OPERATOR_TZ
from copilot.paths import HEARTBEAT_URL_ENV
from copilot.paths import MARKETSTACK_API_KEY_ENV
from copilot.paths import OPERATOR_TZ_ENV
from copilot.paths import PUSHOVER_TOKEN_ENV
from copilot.paths import PUSHOVER_USER_KEY_ENV
from copilot.paths import add_catalog_argument
from copilot.strategies.activations import load_activations


OUT_DIR = Path(__file__).parent / "out"

MORNING = "morning"
EVENING = "evening"
SWEEP = "sweep"
PHASES = (MORNING, EVENING, SWEEP)
"""
Three phases. ``sweep`` is the monitoring-end command on its own.

While orders are denied nothing can be working after the basket, so the evening runs the
sweep as its last step. The day orders are enabled, the sweep moves to the end of the
charter's window - 10:30 Eastern, 00:30 JST, an hour before a Tokyo operator sleeps - and
this phase is that command, existing before it is needed rather than being written the
night it is.

"""


def operator_zone() -> ZoneInfo:
    """
    Return the operator's own clock, from ``COPILOT_OPERATOR_TZ`` or Tokyo.

    Read per call rather than bound at import, so a change takes effect without a
    reinstall - the operator this serves has already moved once mid-campaign.

    An unknown or malformed zone falls back to the default and says so on stderr. The
    second column of a clock line is a convenience; refusing to run the trading day
    because a display preference is misspelt would be the tail wagging the dog, and
    failing silently would leave the operator reading Tokyo while believing otherwise.

    """
    name = os.environ.get(OPERATOR_TZ_ENV, DEFAULT_OPERATOR_TZ)
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        print(
            f"warning: {OPERATOR_TZ_ENV}={name!r} is not a known timezone; "
            f"showing {DEFAULT_OPERATOR_TZ}",
            file=sys.stderr,
        )
        return ZoneInfo(DEFAULT_OPERATOR_TZ)


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
    alerts_itself: bool = False
    """
    The step raises its own alert, so the day does not repeat it.
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

    def lines(self, zone: ZoneInfo | None = None) -> list[str]:
        """
        Return the clock as an operator reads it: Eastern, then their own.
        """
        zone = zone or operator_zone()
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
    """
    One line of the clock: the session's time in Eastern, then the operator's own.

    The second column is dropped when it would repeat the first. An operator sitting in
    Eastern reading ``09:30 EDT   09:30 EDT`` learns nothing from the repetition and has
    to check every line to be sure it *is* a repetition, which is the same noise the
    fixed Tokyo column made, wearing different clothes.

    """
    eastern = instant.astimezone(EASTERN)
    local = instant.astimezone(zone)
    first = f"{eastern.strftime('%H:%M')} {eastern.tzname()}"
    second = f"{local.strftime('%H:%M')} {local.tzname()}"
    if first == second:
        return f"{label:<14}{first}"
    day_shift = " (next day)" if local.date() > eastern.date() else ""
    return f"{label:<14}{first}   {second}{day_shift}"


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


def scheduled_session(phase: str, now: datetime) -> tuple[date | None, str]:
    """
    Return the session a timer-fired ``phase`` acts on, or None and why there is none.

    Only ever **today's** session, by the Eastern date. The morning after it closes, the
    evening before it opens, the sweep once it has opened. A late evening - the timer
    fired after the open because the host was down - has nothing to do: a decision made
    inside the session it was meant to precede is not the decision the research scored.
    A late sweep still runs, because cancelling is safe at any hour and skipping it is not.

    """
    today = now.astimezone(EASTERN).date()
    if not is_trading_day(today):
        return None, f"{today.isoformat()} ({today:%A}) is not a trading session"
    named = f"the session of {today.isoformat()}"
    opens, closes = session_open(today), session_close(today)
    blocked, reason = {
        MORNING: (now < closes, f"{named} has not closed yet"),
        EVENING: (
            now >= opens,
            (
                f"{named} has already opened; an evening run now would decide inside the "
                "session it was meant to precede"
            ),
        ),
        SWEEP: (now < opens, f"{named} has not opened yet"),
    }[phase]
    return (None, reason) if blocked else (today, "")


def monitoring_session(now: datetime) -> date:
    """
    Return the session a hand-run sweep is ending: today's once it has opened.

    ``session_to_prepare`` answers the evening's question and returns the *next* session
    once today's has opened, so a sweep at 10:30 using it named tomorrow in its record.

    """
    today = now.astimezone(EASTERN).date()
    if is_trading_day(today) and now >= session_open(today):
        return today
    return session_to_prepare(now)


def completed_record(phase: str, session: date, out_dir: Path = OUT_DIR) -> Path | None:
    """
    Return a filed record of ``phase`` passing for ``session``, if there is one.

    Passing means exit 0 and not a dry run. A failed run is not a completed one, so a timer
    that fires again after a fix - or an operator re-running by hand - is not refused.

    """
    for path in sorted(out_dir.glob(f"day_{phase}_*.json"), reverse=True):
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if (
            record.get("session") == session.isoformat()
            and record.get("exit_code") == 0
            and not record.get("dry_run")
        ):
            return path
    return None


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
    scheduled: bool = False,
) -> tuple[str, ...]:
    """
    Return what the phase needs exported and does not have, one line each.
    """
    missing: list[str] = []
    if scheduled and not (environ.get(PUSHOVER_TOKEN_ENV) and environ.get(PUSHOVER_USER_KEY_ENV)):
        missing.append(
            f"{PUSHOVER_TOKEN_ENV} and {PUSHOVER_USER_KEY_ENV}: a scheduled run must be able to "
            "tell someone when it breaks",
        )
    if phase in (MORNING, EVENING) and not environ.get(MARKETSTACK_API_KEY_ENV):
        missing.append(f"{MARKETSTACK_API_KEY_ENV}: append and the corporate-actions scan need it")
    if phase in (EVENING, SWEEP):
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


def _append_step(catalog: str) -> Step:
    return Step(
        "append",
        "copilot.data.append",
        ("--catalog", catalog),
        stops_on_failure=False,
        why="the catalog is behind the last published session; the warm-up will refuse",
    )


def _corporate_actions_step(catalog: str, *, today: date, why: str) -> Step:
    return Step(
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
        why=why,
    )


def morning_steps(catalog: str, *, today: date) -> tuple[Step, ...]:
    """
    Return the morning: catalog current, actions checked, verdicts recomputed, compared.

    The scan stops the sequence because a verdict filed over a split sitting in the
    prices is the defect that motivated this module. The append does not: a hole in
    yesterday's bar cannot move a verdict (the window is pinned, ADR-0017) and the
    evening's warm-up refuses it on its own, so the morning reports it and carries on.

    """
    return (
        _append_step(catalog),
        _corporate_actions_step(
            catalog,
            today=today,
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
    Return the evening: bring the catalog current, prove it and the broker, run, sweep.

    **The evening appends before it warms.** The vendor publishes a session's bar about
    9.5 hours after its close (measured in :mod:`copilot.data.append`), so the morning's
    append at 17:00 Eastern always finds that session pending, and the warm-up for the next
    open needs exactly that bar. Measured 2026-09-10: every activation refused, every
    evening, because nothing appended between publication and the evening. The append is
    idempotent and reports rather than stops; the warm-up remains the gate.

    **And it scans before it warms**, because the bar just appended is one the morning's
    scan never saw, and an unregistered split in it reads to the strategy as a gap. The scan
    runs to the session being prepared, which on a scheduled evening is today.

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
        _append_step(catalog),
        _corporate_actions_step(
            catalog,
            today=session,
            why="a split is sitting in the newest bars; the warm-up would read it as a gap",
        ),
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
            why="the broker still reports an order working, or could not be asked",
            alerts_itself=True,
        ),
    )


def sweep_steps(connection: Connection) -> tuple[Step, ...]:
    """
    Return monitoring end on its own: cancel every working entry, and hear the broker.
    """
    return (
        Step(
            "sweep",
            "copilot.live.cancel_working",
            ("--all", *connection.argv),
            why="the broker still reports an order working, or could not be asked",
            alerts_itself=True,
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


def alerts_for(
    phase: str,
    session: str | None,
    steps: tuple[Step, ...],
    results: tuple[StepResult, ...],
) -> list[Alert]:
    """
    Return what the operator is told about one run: each failure, and the morning's summary.
    """
    context = {"phase": phase, "session": session or "?"}
    alerts: list[Alert] = []
    for step, result in zip(steps, results, strict=True):
        if result.skipped or result.passed or step.alerts_itself:
            continue
        stopped = " and stopped the day" if step.stops_on_failure else ""
        alerts.append(
            Alert(
                severity=Severity.WARNING,
                title=f"day {phase}: {step.name} failed",
                body=f"{step.name} exited {result.exit_code}{stopped}: {step.why}.",
                context={**context, "command": " ".join(step.command)},
            ),
        )
    if phase == MORNING:
        passed = sum(1 for r in results if r.passed)
        lines = [
            f"{r.name}: {'skipped' if r.skipped else 'PASS' if r.passed else f'exit {r.exit_code}'}"
            for r in results
        ]
        alerts.append(
            Alert(
                severity=Severity.INFO,
                title="day morning summary",
                body=f"{passed} of {len(results)} steps passed.\n" + "\n".join(lines),
                context=context,
            ),
        )
    return alerts


def notify(
    phase: str,
    session: str | None,
    steps: tuple[Step, ...],
    results: tuple[StepResult, ...],
) -> None:
    """
    Send the run's alerts, and the morning's heartbeat, reporting each delivery.
    """
    alerter = alerter_from_environment()
    pending = alerts_for(phase, session, steps, results)
    latch = read_latch()
    if latch is not None:
        pending.append(
            Alert(
                severity=Severity.WARNING,
                title="halt latch still engaged",
                body=f"Latch {latch.latch_id} since {latch.engaged_at}: {latch.reason}",
                context={"phase": phase, "session": session or "?"},
            ),
        )
    for alert in pending:
        delivery = alerter.send(alert)
        print(f"  alert {alert.severity} {alert.title!r}: {delivery.outcome}")
    if phase == MORNING:
        beat(read_latch())


def _check_the_halt() -> None:
    """
    Settle outstanding CRITICAL receipts, and say plainly if this host is halted.

    First, on every phase and every day a timer fires, so an alert nobody answered on a
    Friday night engages the latch before Monday's evening builds a node. Nothing here may
    stop the phase: a check that cannot finish says so and the phase runs, because the phase
    may be the sweep.

    """
    try:
        lines = acknowledgement_check()
    except Exception as e:  # noqa: BLE001 - the check must never stop the phase that sweeps
        lines = [
            (
                f"WARNING: the acknowledgement check failed ({type(e).__name__}: {e}); this "
                "phase runs, and an unanswered CRITICAL cannot engage the halt until the check "
                "succeeds"
            ),
        ]
    for line in lines:
        print(line)
    latch = read_latch()
    if latch is not None:
        print(describe_latch(latch) + "\n")


def beat(latch: Latch | None) -> None:
    """
    Ping the heartbeat URL and say what happened, unless the host halted itself unheard.

    A latch no operator engaged means a CRITICAL expired unanswered or never arrived, so
    Pushover has already failed to reach anyone. The watcher off the host is the one
    path left, and withholding the beat is how it is used.

    """
    if engaged_automatically(latch):
        print(
            f"  heartbeat withheld: halt latch {latch.latch_id} was engaged by {latch.trigger}, "
            "so the watcher's missed beat is the alert that could not be sent",
        )
        return
    _, line = ping(os.environ.get(HEARTBEAT_URL_ENV, "").strip())
    print(f"  {line}")


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


def _nothing_scheduled(args: argparse.Namespace, now: datetime) -> str:
    """
    Return why a timer-fired phase has nothing to do, or set its session and return "".
    """
    scheduled, reason = scheduled_session(args.phase, now)
    if scheduled is None:
        return reason
    done = completed_record(args.phase, scheduled) if args.phase != SWEEP else None
    if done is not None:
        return f"the {args.phase} for {scheduled.isoformat()} passed ({done.name})"
    args.session = scheduled.isoformat()
    return ""


def _phase_steps(args: argparse.Namespace, now: datetime, record: DayRecord) -> tuple[Step, ...]:
    """
    Print the phase's heading, record its session and clock, and return its steps.
    """
    if args.phase == MORNING:
        closed = closed_session(now)
        record.session = closed.isoformat() if closed else None
        print(
            f"Morning of {now.astimezone(operator_zone()):%Y-%m-%d %a %H:%M %Z}: the session of "
            f"{closed.isoformat() if closed else '?'} has closed; next session "
            f"{session_to_prepare(now).isoformat()}",
        )
        return morning_steps(args.catalog, today=now.astimezone(EASTERN).date())

    connection = Connection(host=args.host, port=args.port, account=args.account)
    if args.phase == SWEEP:
        session = date.fromisoformat(args.session) if args.session else monitoring_session(now)
        heading = f"Monitoring end for the session of {session.isoformat()} ({session:%A})"
    else:
        session = date.fromisoformat(args.session) if args.session else session_to_prepare(now)
        heading = f"Evening for the session of {session.isoformat()} ({session:%A})"
    clock = session_clock(session)
    record.session = session.isoformat()
    record.clock = clock.lines()
    print(heading)
    for line in record.clock:
        print(f"  {line}")
    if args.phase == SWEEP:
        return sweep_steps(connection)
    return evening_steps(
        args.catalog,
        session=session,
        connection=connection,
        allocation=args.allocation,
        risk_fraction=args.risk_fraction,
    )


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
    parser.add_argument(
        "--scheduled",
        action="store_true",
        help="Fired by a timer: act only on today's session, and only once",
    )
    args = parser.parse_args(argv)

    now = datetime.now(tz=UTC)
    if not args.dry_run:
        _check_the_halt()
    if args.scheduled:
        if args.session:
            parser.error("--scheduled decides the session itself; do not pass --session")
        nothing = _nothing_scheduled(args, now)
        if nothing:
            print(f"nothing to do: {nothing}")
            if args.phase == MORNING:
                # A quiet weekend must still read as alive to the watcher.
                beat(read_latch())
            return 0

    missing = required_environment(
        args.phase,
        environ=os.environ,
        account=args.account,
        scheduled=args.scheduled,
    )
    if missing and not args.dry_run:
        print("refused: the day needs these exported first")
        for line in missing:
            print(f"  {line}")
        return 2

    record = DayRecord(phase=args.phase, run_at=now.isoformat(), session=None, dry_run=args.dry_run)
    steps = _phase_steps(args, now, record)

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
    notify(args.phase, record.session, steps, results)
    return exit_code


__all__ = [
    "EVENING",
    "EXECUTION_WINDOW",
    "MORNING",
    "PHASES",
    "REQUIRED_TIMEZONE_ALIAS",
    "SWEEP",
    "TIMEZONE_ALIASES_ENV",
    "Connection",
    "DayRecord",
    "SessionClock",
    "Step",
    "StepResult",
    "alerts_for",
    "beat",
    "closed_session",
    "completed_record",
    "evening_steps",
    "monitoring_session",
    "morning_steps",
    "notify",
    "operator_zone",
    "registered_symbols",
    "required_environment",
    "run_module",
    "run_steps",
    "scheduled_session",
    "session_clock",
    "summarise",
    "sweep_steps",
]


if __name__ == "__main__":
    sys.exit(main())
