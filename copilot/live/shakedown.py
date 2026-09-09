"""
One trading day spent deliberately proving the broker path, phase by phase.

The paper campaign's system clock wants **eight weeks and at least thirty reconciled
order-lifecycle or injected-failure events with no unresolved critical incident**, and it
does not need a frozen candidate to start ([`PAPER_CAMPAIGN.md`](../docs/PAPER_CAMPAIGN.md),
the two-clocks argument). A day the operator can watch open to close is the cheapest chance
to bank a large share of that, and there are questions no other day can answer.

Why this is a command and not a checklist
-----------------------------------------
The operator-day draft learned it the hard way: the corporate-actions scan was built, was
listed in the morning table, and did not run during an onboarding drill, so a symbol passed
the gate on a fake gap. **"A tool that exists is not a tool that runs."** The fix was not a
better scan, it was that a command owns the sequence. The same reasoning applies here, with
more at stake, because a shakedown is run once and its gaps are invisible until something
needs them.

Why the phases are pinned to the clock
---------------------------------------
A probe run at the wrong hour returns a number that means something else. `warmup` shipped
defaulting to "the next trading day after today", reported BLOCKED at 21:30 JST for a
catalog that was ready, and taught the rule this module enforces: **a check that is wrong at
the hour it is used trains you to ignore it.** So each phase declares the window it is valid
in and refuses to run outside it unless the operator overrides deliberately.

Three of these questions have been open for days:

- **Does the feed quote before the open?** The nine-quote check passed 15/15 with the
  session open at 11:32 ET. The evening gate runs at 08:30 ET, and if delayed data is silent
  pre-market the gate blocks every night. The ``pre-open`` phase settles it, and now asks it
  of the realtime feed too, which only became possible on 2026-09-09.
- **Does the broker's own tape agree with the spread the cost model charges?**
  [ADR-0019](../docs/decisions/0019-spread-is-charged-from-measured-history.md) charges p95
  of the worst measured year **in the first two hours of the session**, from Databento
  history. The ``open`` phase measures the same window live, for the first time, so the two
  can be compared rather than assumed.
- **What does a round trip actually cost?** The stage-five trips measured USD 2.01 and 2.02
  of commission on one and three shares, which is IB Pro **Fixed**'s USD 1.00 per-order
  minimum charged twice. ``order-window`` re-measures it, and it is the empirical read on
  whether a pricing-structure change did what it claims.

Orders are placed on purpose
-----------------------------
This is a paper account with a million dollars in it and no frozen candidate, so every order
here is a **drill order**, not a strategy order. Each step says whether it places one, and
``--plan`` prints that before anything runs. The sweep is unconditional and runs last, for
the same reason the evening's does: a stopping failure stops the sequence, and it still must
not leave anything working at the broker.

Usage::

    python -m copilot.live.shakedown --plan
    python -m copilot.live.shakedown --phase pre-open
    python -m copilot.live.shakedown --phase open
    python -m copilot.live.shakedown --phase order-window
    python -m copilot.live.shakedown --phase midday
    python -m copilot.live.shakedown --phase close

"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from datetime import time as clock_time
from decimal import Decimal
from typing import TYPE_CHECKING

from copilot.data.calendar import EASTERN
from copilot.data.calendar import is_trading_day
from copilot.data.catalog import read_series
from copilot.live.day import OUT_DIR
from copilot.live.day import Connection
from copilot.live.day import StepResult
from copilot.live.day import summarise
from copilot.paths import add_catalog_argument
from copilot.paths import catalog_path


if TYPE_CHECKING:
    from collections.abc import Mapping
    from collections.abc import Sequence


PAPER_ACCOUNT_ENV = "COPILOT_PAPER_ACCOUNT"
TIMEZONE_ALIASES_ENV = "IBAPI_TIMEZONE_ALIASES"
REQUIRED_TIMEZONE_ALIAS = "JST=Asia/Tokyo"

CAL_SECONDS_ENV = "COPILOT_CAL_SECONDS"
CAL_MARKET_DATA_TYPE_ENV = "COPILOT_CAL_MARKET_DATA_TYPE"

DRILL_SYMBOL = "AAPL"
DRILL_VENUE = "XNAS"

SPREAD_WINDOW_SECONDS = 3000
"""
Fifty minutes of quotes for the open phase.

ADR-0011 recorded that sample size moved its own number twofold between a 148-second run
and a 654-second one, and warned against setting a coefficient from a short run. This is
the longest stretch that still leaves the execution window's back half for the order
probes.

"""

SPREAD_CLOSE_SECONDS = 900
"""
Fifteen minutes near the close, as a contrast rather than a coefficient.

ADR-0019 says charging the close would price a window no order uses and would charge
roughly **half** what the execution window costs. That claim was made from history; this
measures it live on one day, which is not proof but is a cheap check on the direction.

"""


@dataclass(frozen=True)
class ShakedownStep:
    """
    One command in a phase, and what it costs to skip it.
    """

    name: str
    module: str
    argv: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    stops_on_failure: bool = False
    places_orders: bool = False
    why: str = ""

    @property
    def command(self) -> tuple[str, ...]:
        """
        The full command line, as the operator would have typed it.
        """
        return ("python", "-m", self.module, *self.argv)


@dataclass(frozen=True)
class Phase:
    """
    A named stretch of the session, the steps that belong in it, and why there.
    """

    name: str
    opens: clock_time
    closes: clock_time
    why: str
    steps: tuple[ShakedownStep, ...]

    def covers(self, moment: datetime) -> bool:
        """
        Whether an instant in Eastern time falls inside this phase's window.
        """
        return self.opens <= moment.astimezone(EASTERN).timetz().replace(tzinfo=None) <= self.closes

    @property
    def window(self) -> str:
        """
        The phase's window as an operator reads it.
        """
        return f"{self.opens:%H:%M}-{self.closes:%H:%M} ET"


def reference_price(catalog: str, symbol: str, venue: str) -> Decimal:
    """
    Return the last close the catalog holds, the probes' idea of a reference.

    Every order-placing probe takes ``--reference-price`` and documents it as *last known
    price, for the offset*: it places a limit a fixed fraction away so the order rests
    rather than fills. A stored close is exactly that, it needs no node of its own, and it
    is deterministic, so the same drill re-run from the same catalog places the same order.

    A stale close moves how far the limit sits from the market, never which side, so the
    probe keeps its meaning. The filed record names the price used.

    """
    bars = read_series(catalog, symbol, venue)
    if not bars:
        raise ValueError(
            f"no bars in the catalog for {symbol}.{venue}, so no reference price. "
            f"Run: python -m copilot.data.append",
        )
    return Decimal(str(bars[-1].close))


def pre_open_steps(connection: Connection) -> tuple[ShakedownStep, ...]:
    """
    Build the questions that can only be asked before the bell.
    """
    return (
        ShakedownStep(
            name="quotes-realtime",
            module="copilot.live.preflight",
            argv=(*connection.argv, "--market-data-type", "REALTIME"),
            why="whether the consolidated feed quotes pre-market, which the evening gate assumes",
        ),
        ShakedownStep(
            name="quotes-delayed",
            module="copilot.live.preflight",
            argv=(*connection.argv, "--market-data-type", "DELAYED"),
            why=(
                "the control. Zero realtime quotes is indistinguishable from a broken "
                "subscription without it, which is the lesson of 2026-09-02"
            ),
        ),
        ShakedownStep(
            name="entitlements",
            module="copilot.calibration.entitlements",
            why=(
                "IB activates a subscription at a trading-day boundary, so today is the "
                "first honest reading of yesterday's purchase"
            ),
        ),
    )


def open_steps() -> tuple[ShakedownStep, ...]:
    """
    Build the execution window's front half, measuring what the cost model charges for.
    """
    return (
        ShakedownStep(
            name="spread-window",
            module="copilot.calibration.spread_snapshot",
            env={
                CAL_SECONDS_ENV: str(SPREAD_WINDOW_SECONDS),
                CAL_MARKET_DATA_TYPE_ENV: "REALTIME",
            },
            why=(
                "the first realtime measurement inside the window ADR-0019 charges from "
                "history, so the two can finally be compared"
            ),
        ),
    )


def order_window_steps(connection: Connection, price: Decimal) -> tuple[ShakedownStep, ...]:
    """
    Order behaviour, measured inside the charter's own execution window.
    """
    broker = (*connection.argv, "--reference-price", str(price))
    return (
        ShakedownStep(
            name="controlled-order",
            module="copilot.live.probes.controlled_order",
            argv=broker,
            places_orders=True,
            stops_on_failure=True,
            why="if one order cannot round trip, nothing after this means anything",
        ),
        ShakedownStep(
            name="order-types",
            module="copilot.live.probes.order_types",
            argv=broker,
            places_orders=True,
            why=(
                "the matrix has only ever run pre-open, and the charter's orders go in "
                "here, in the first hours"
            ),
        ),
        ShakedownStep(
            name="round-trip",
            module="copilot.live.probes.supervised_session",
            argv=connection.argv,
            places_orders=True,
            why=(
                "the empirical read on what a round trip costs, which is the largest "
                "single number in this account's economics"
            ),
        ),
    )


def midday_steps(connection: Connection, price: Decimal) -> tuple[ShakedownStep, ...]:
    """
    Build the refusals and the recoveries, run while the market is open, not around it.
    """
    broker = (*connection.argv, "--reference-price", str(price))
    return (
        ShakedownStep(
            name="failure-injection",
            module="copilot.live.probes.failure_injection",
            argv=connection.argv,
            places_orders=True,
            why="the refusal probes are what stand between a bug and an account",
        ),
        ShakedownStep(
            name="strand-recovery",
            module="copilot.live.probes.strand_recovery",
            argv=broker,
            places_orders=True,
            why=(
                "adoption was confirmed 2026-09-03; a cancel from a foreign client id "
                "still reports FAIL while the broker ends clean, and that is unfinished"
            ),
        ),
        ShakedownStep(
            name="subscription-interference",
            module="copilot.live.probes.subscription_interference",
            argv=("--host", connection.host, "--port", str(connection.port)),
            why=(
                "the withdrawn sibling-stall claim, re-checked on the consolidated feed "
                "it was never tested against. Takes no --account: it opens a data client "
                "only, which is why it can run beside anything else"
            ),
        ),
    )


def close_steps(connection: Connection) -> tuple[ShakedownStep, ...]:
    """
    Build the contrast measurement, then leave the broker clean.
    """
    return (
        ShakedownStep(
            name="spread-close",
            module="copilot.calibration.spread_snapshot",
            env={
                CAL_SECONDS_ENV: str(SPREAD_CLOSE_SECONDS),
                CAL_MARKET_DATA_TYPE_ENV: "REALTIME",
            },
            why="ADR-0019 claims the close costs roughly half the window; this looks",
        ),
        ShakedownStep(
            name="sweep",
            module="copilot.live.cancel_working",
            argv=(*connection.argv, "--all"),
            why=(
                "the monitoring-end policy is not optional, and a drill that leaves an "
                "order working is worse than one that never ran"
            ),
        ),
    )


def phases(connection: Connection, price: Decimal) -> tuple[Phase, ...]:
    """
    Build the day in the order it happens, each phase pinned to when it means something.
    """
    return (
        Phase(
            name="pre-open",
            opens=clock_time(7, 30),
            closes=clock_time(9, 29),
            why="before the bell, while the answer is still about pre-market",
            steps=pre_open_steps(connection),
        ),
        Phase(
            name="open",
            opens=clock_time(9, 30),
            closes=clock_time(10, 30),
            why="the front half of the charter's execution window, including its first minutes",
            steps=open_steps(),
        ),
        Phase(
            name="order-window",
            opens=clock_time(10, 30),
            closes=clock_time(11, 30),
            why="still inside the execution window, after the spread measurement has its sample",
            steps=order_window_steps(connection, price),
        ),
        Phase(
            name="midday",
            opens=clock_time(11, 30),
            closes=clock_time(15, 15),
            why="liquid, unhurried, and far from the close if a probe leaves something behind",
            steps=midday_steps(connection, price),
        ),
        Phase(
            name="close",
            opens=clock_time(15, 15),
            closes=clock_time(16, 30),
            why="the closing contrast, then the sweep",
            steps=close_steps(connection),
        ),
    )


def phase_named(name: str, available: Sequence[Phase]) -> Phase:
    """
    Look one phase up by name, or say what the names are.
    """
    for phase in available:
        if phase.name == name:
            return phase
    known = ", ".join(p.name for p in available)
    raise ValueError(f"unknown phase {name!r}; expected one of: {known}")


def missing_environment(environ: Mapping[str, str], account: str) -> tuple[str, ...]:
    """
    Return what the day needs exported and does not have, one line each.
    """
    missing: list[str] = []
    if REQUIRED_TIMEZONE_ALIAS not in environ.get(TIMEZONE_ALIASES_ENV, ""):
        missing.append(
            f'{TIMEZONE_ALIASES_ENV}="{REQUIRED_TIMEZONE_ALIAS}": without it every IB '
            f"connect fails opaquely",
        )
    if not account:
        missing.append(f"{PAPER_ACCOUNT_ENV} (or --account): the paper account to connect to")
    return tuple(missing)


def run_step(step: ShakedownStep) -> int:
    """
    Run one step as its own process, inheriting the environment plus the step's own.

    A process each, for the same reasons the day command gives: each builds its own node
    and its own logging, and its exit code is the contract it was written to.

    """
    sys.stdout.flush()
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", step.module, *step.argv],
        check=False,
        env={**os.environ, **step.env},
    )
    return completed.returncode


def run_phase(phase: Phase, *, runner=run_step) -> tuple[StepResult, ...]:  # noqa: ANN001
    """
    Run a phase's steps in order, recording each, stopping only where a step says to.

    Most steps do **not** stop the phase. A shakedown is there to find out what breaks, and
    a day that halts on the first surprise learns one thing where it could have learned
    five. The exceptions say so in their own ``why``.

    """
    results: list[StepResult] = []
    stopped = False
    for index, step in enumerate(phase.steps, start=1):
        if stopped:
            results.append(StepResult(step.name, step.command, None, 0.0, skipped=True))
            continue
        rule = "=" * 78
        orders = "  [places orders]" if step.places_orders else ""
        print(
            f"\n{rule}\n  {index}/{len(phase.steps)}  {step.name:<24}"
            f"{' '.join(step.command)}{orders}\n{rule}\n",
        )
        started = time.monotonic()
        code = runner(step)
        seconds = time.monotonic() - started
        results.append(StepResult(step.name, step.command, code, seconds))
        if code != 0 and step.stops_on_failure:
            print(f"\n  {step.name} exited {code}: {step.why}. Stopping this phase.", flush=True)
            stopped = True
    return tuple(results)


def print_plan(available: Sequence[Phase], price: Decimal) -> None:
    """
    Print the whole day before any of it runs, orders marked.
    """
    print(f"\nShakedown plan. Reference price {price} from the catalog's last close.\n")
    for phase in available:
        print(f"  {phase.name:<14}{phase.window:<18}{phase.why}")
        for step in phase.steps:
            orders = " [places orders]" if step.places_orders else ""
            print(f"      {step.name:<26}{' '.join(step.command)}{orders}")
            for name, value in sorted(step.env.items()):
                print(f"      {'':<26}{name}={value}")
        print()


@dataclass
class ShakedownRecord:
    """
    What one phase did, filed beside every other live record.
    """

    phase: str
    run_at: str
    session: str
    reference_price: str
    forced: bool
    steps: list[dict[str, object]] = field(default_factory=list)
    exit_code: int = 0


def main(argv: list[str] | None = None) -> int:
    """
    Operator entry point: print the day, or run one phase of it.
    """
    parser = argparse.ArgumentParser(
        description="Spend one open session proving the broker path, phase by phase.",
    )
    parser.add_argument("--phase", help="which phase to run")
    parser.add_argument("--plan", action="store_true", help="print the whole day and exit")
    parser.add_argument("--host", default=os.getenv("IB_V2_HOST", "172.17.112.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("IB_V2_PORT", "7497")))
    parser.add_argument("--account", default=os.getenv(PAPER_ACCOUNT_ENV, ""))
    parser.add_argument("--symbol", default=DRILL_SYMBOL, help="the drill instrument")
    parser.add_argument("--venue", default=DRILL_VENUE)
    parser.add_argument(
        "--force",
        action="store_true",
        help="run the phase outside its window, knowing the reading means something else",
    )
    add_catalog_argument(parser)
    args = parser.parse_args(argv)

    connection = Connection(args.host, args.port, args.account)
    price = reference_price(args.catalog or catalog_path(), args.symbol, args.venue)
    available = phases(connection, price)

    if args.plan or not args.phase:
        print_plan(available, price)
        if not args.phase:
            print("Pick one with --phase. Nothing has run.")
        return 0

    phase = phase_named(args.phase, available)
    now = datetime.now(UTC)
    session = now.astimezone(EASTERN).date()

    missing = missing_environment(os.environ, args.account)
    if missing:
        print(f"\nThe {phase.name} phase needs these exported first:\n")
        for line in missing:
            print(f"  {line}")
        return 2

    if not is_trading_day(session):
        print(f"\n{session} is not a trading day. Nothing has run.")
        return 2

    if not phase.covers(now) and not args.force:
        print(
            f"\n{phase.name} is a {phase.window} phase and it is "
            f"{now.astimezone(EASTERN):%H:%M %Z}.\n\n"
            f"  {phase.why}\n\n"
            "A check that is wrong at the hour it is used trains you to ignore it. "
            "Run it in its window, or pass --force and read the result knowing what it is.",
        )
        return 2

    print(f"\n{phase.name}  {phase.window}  {phase.why}")
    print(f"session {session}, reference price {price}\n")
    results = run_phase(phase)
    code = summarise(results)

    record = ShakedownRecord(
        phase=phase.name,
        run_at=now.isoformat(),
        session=session.isoformat(),
        reference_price=str(price),
        forced=bool(args.force),
        steps=[asdict(r) | {"command": list(r.command)} for r in results],
        exit_code=code,
    )
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"shakedown_{phase.name}_{now:%Y%m%dT%H%M%SZ}.json"
    path.write_text(json.dumps(asdict(record), indent=2) + "\n")
    print(f"\nWrote {path}")
    return code


__all__ = [
    "Phase",
    "ShakedownRecord",
    "ShakedownStep",
    "close_steps",
    "main",
    "midday_steps",
    "missing_environment",
    "open_steps",
    "order_window_steps",
    "phase_named",
    "phases",
    "pre_open_steps",
    "reference_price",
    "run_phase",
    "run_step",
]


if __name__ == "__main__":
    raise SystemExit(main())
