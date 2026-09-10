"""
What the shakedown has to get right before a session it only gets one of.

No test here touches the broker. They pin the things a dry read of the module cannot
check, and each of them is a way the day could be quietly wasted:

- **Every flag a step passes is a flag that probe accepts.** The first draft handed
  ``--account`` to ``subscription_interference``, which opens a data client only and takes
  no account. That fails at the moment the step runs, mid-session, with the window gone.
  The source scan below catches the whole class.
- **The phases tile the session without overlapping**, because two phases claiming the same
  hour means one of them is being run at the wrong time by construction.
- **A phase refuses to run outside its window**, which is the `warmup` lesson: a check that
  is wrong at the hour it is used trains you to ignore it.
- **The sweep is not a stopping step**, because the monitoring-end policy has to run
  whatever happened before it.

"""

from __future__ import annotations

import re
from datetime import UTC
from datetime import datetime
from datetime import time as clock_time
from decimal import Decimal
from itertools import pairwise
from pathlib import Path

import pytest

from copilot.data.calendar import EASTERN
from copilot.live.day import Connection
from copilot.live.shakedown import Phase
from copilot.live.shakedown import ShakedownStep
from copilot.live.shakedown import main
from copilot.live.shakedown import missing_environment
from copilot.live.shakedown import phase_named
from copilot.live.shakedown import phases
from copilot.live.shakedown import reference_price
from copilot.live.shakedown import run_phase


OVERLAY = Path(__file__).resolve().parents[1]

CONNECTION = Connection("10.0.0.1", 7497, "DUT067974")
PRICE = Decimal("328.21")

ALIAS_SET = {"IBAPI_TIMEZONE_ALIASES": "JST=Asia/Tokyo"}


def _phases() -> tuple[Phase, ...]:
    return phases(CONNECTION, PRICE)


def _eastern(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 10, hour, minute, tzinfo=EASTERN).astimezone(UTC)


def _step(**kwargs: object) -> ShakedownStep:
    base = {"name": "s", "module": "copilot.live.preflight"}
    return ShakedownStep(**{**base, **kwargs})


# ------------------------------------------------------------ the flags actually exist


SHARED_ARGUMENT_HELPERS = {
    "add_broker_arguments": "live/session.py",
    "add_connection_arguments": "live/session.py",
    "add_catalog_argument": "paths.py",
}
"""
Helpers that register flags on someone else's parser.

A scan of one module's source misses these, and missing them would make the check below
pass by seeing nothing rather than by finding everything.

"""


def _flags_in(path: Path) -> set[str]:
    return set(re.findall(r'add_argument\(\s*"(--[a-z0-9-]+)"', path.read_text()))


def _accepted_flags(module: str) -> set[str]:
    """
    Every ``--flag`` a module's parser ends up with, its shared helpers included.
    """
    source_path = OVERLAY / Path(*module.split(".")[1:]).with_suffix(".py")
    source = source_path.read_text()
    accepted = _flags_in(source_path)
    for helper, relative in SHARED_ARGUMENT_HELPERS.items():
        if helper in source:
            accepted |= _flags_in(OVERLAY / relative)
    return accepted


def test_the_shared_helpers_are_still_where_the_scan_looks():
    """
    If a helper moves, the scan above silently stops finding its flags.
    """
    for helper, relative in SHARED_ARGUMENT_HELPERS.items():
        path = OVERLAY / relative
        assert path.exists(), relative
        assert f"def {helper}(" in path.read_text(), helper


def test_every_step_passes_only_flags_its_probe_accepts():
    """
    The regression for handing ``--account`` to a probe that takes none.

    A mismatch here does not fail until the step runs, which is mid-session, once, with
    the window gone. It is exactly the kind of thing that has to fail in the suite.

    """
    offenders = []
    for phase in _phases():
        for step in phase.steps:
            accepted = _accepted_flags(step.module)
            passed = {a for a in step.argv if a.startswith("--")}
            offenders.extend(
                f"{phase.name}/{step.name}: {step.module} rejects {flag}"
                for flag in sorted(passed - accepted)
            )
    assert offenders == []


def test_the_pre_open_phase_exercises_alerting():
    """
    The scorecard wants alerts arriving and acknowledged, and nothing had ever fired
    one.
    """
    step = next(s for s in phase_named("pre-open", _phases()).steps if s.name == "alerting")

    assert step.module == "copilot.live.alerting"
    assert "--send-test" in step.argv
    assert "critical" in step.argv, "CRITICAL is what exercises the receipt and the deadline"
    assert step.stops_on_failure is False
    assert step.places_orders is False


def test_the_plan_says_it_is_not_the_operating_day(capsys, monkeypatch):
    """
    The drill files no session record, so it must not read as a substitute for the day.
    """
    monkeypatch.setattr("copilot.live.shakedown.reference_price", lambda *_: PRICE)
    main(["--plan", "--account", "DUT067974"])
    out = capsys.readouterr().out

    assert "not the operating day" in out
    assert "copilot.live.day morning" in out
    assert "copilot.live.day evening" in out


def test_the_interference_probe_is_given_no_account():
    """
    It opens a data client only, which is why it can run beside anything else.
    """
    step = next(s for s in phase_named("midday", _phases()).steps if "interference" in s.name)

    assert "--account" not in step.argv
    assert "--host" in step.argv


def test_the_probes_that_need_a_reference_price_are_given_one():
    needs = {"controlled-order", "order-types", "strand-recovery"}
    seen = set()
    for phase in _phases():
        for step in phase.steps:
            if step.name in needs:
                assert "--reference-price" in step.argv, step.name
                assert str(PRICE) in step.argv
                seen.add(step.name)

    assert seen == needs


# ------------------------------------------------------------------- the phases tile


def test_the_phases_do_not_overlap():
    """
    Two phases claiming an hour means one of them runs at the wrong time by
    construction.
    """
    ordered = sorted(_phases(), key=lambda p: p.opens)
    for earlier, later in pairwise(ordered):
        assert earlier.closes <= later.opens, f"{earlier.name} overlaps {later.name}"


def test_the_execution_window_phases_sit_inside_the_charters_first_two_hours():
    """
    The charter puts the order into the first one to two hours of the session.
    """
    for name in ("open", "order-window"):
        phase = phase_named(name, _phases())
        assert phase.opens >= clock_time(9, 30)
        assert phase.closes <= clock_time(11, 30)


def test_the_pre_open_phase_ends_before_the_bell():
    """
    Its whole question is about pre-market, so a minute past the open voids the answer.
    """
    assert phase_named("pre-open", _phases()).closes < clock_time(9, 30)


@pytest.mark.parametrize(
    ("hour", "minute", "inside"),
    [(9, 30, True), (10, 30, True), (9, 29, False), (10, 31, False)],
)
def test_a_phase_knows_its_own_window(hour, minute, inside):
    assert phase_named("open", _phases()).covers(_eastern(hour, minute)) is inside


def test_an_unknown_phase_names_the_ones_that_exist():
    with pytest.raises(ValueError, match="pre-open"):
        phase_named("afternoon", _phases())


# ------------------------------------------------------------------- what stops what


def test_the_sweep_never_stops_and_runs_last():
    """
    The monitoring-end policy is not optional and must survive whatever preceded it.
    """
    close = phase_named("close", _phases())
    sweep = close.steps[-1]

    assert sweep.name == "sweep"
    assert sweep.stops_on_failure is False
    assert "--all" in sweep.argv


def test_only_the_first_order_probe_stops_its_phase():
    """
    A shakedown that halts on the first surprise learns one thing where it could learn
    five.
    """
    stopping = [(p.name, s.name) for p in _phases() for s in p.steps if s.stops_on_failure]

    assert stopping == [("order-window", "controlled-order")]


def test_a_failing_step_skips_the_rest_only_when_it_stops():
    phase = Phase(
        name="t",
        opens=clock_time(9, 30),
        closes=clock_time(10, 0),
        why="",
        steps=(
            _step(name="first", stops_on_failure=True),
            _step(name="second"),
        ),
    )

    results = run_phase(phase, runner=lambda _: 1)

    assert results[0].passed is False
    assert results[1].skipped is True


def test_a_failing_step_that_does_not_stop_lets_the_phase_continue():
    phase = Phase(
        name="t",
        opens=clock_time(9, 30),
        closes=clock_time(10, 0),
        why="",
        steps=(_step(name="first"), _step(name="second")),
    )

    results = run_phase(phase, runner=lambda step: 0 if step.name == "second" else 1)

    assert [r.skipped for r in results] == [False, False]
    assert [r.passed for r in results] == [False, True]


def test_every_order_placing_step_says_so():
    """
    ``--plan`` prints this before anything runs, so it has to be true.
    """
    placing = {s.name for p in _phases() for s in p.steps if s.places_orders}

    assert placing == {
        "controlled-order",
        "order-types",
        "round-trip",
        "failure-injection",
        "strand-recovery",
    }


# ----------------------------------------------------------------- the environment


def test_the_timezone_alias_is_required():
    missing = missing_environment({}, "DUT067974")

    assert len(missing) == 1
    assert "IBAPI_TIMEZONE_ALIASES" in missing[0]


def test_the_account_is_required():
    missing = missing_environment(ALIAS_SET, "")

    assert len(missing) == 1
    assert "COPILOT_PAPER_ACCOUNT" in missing[0]


def test_a_complete_environment_is_missing_nothing():
    assert missing_environment(ALIAS_SET, "DUT067974") == ()


# --------------------------------------------------------------- the reference price


def test_an_empty_catalog_names_the_command_that_fixes_it(tmp_path):
    with pytest.raises(ValueError, match=re.escape("copilot.data.append")):
        reference_price(str(tmp_path), "AAPL", "XNAS")


# ------------------------------------------------------------------------- the CLI


def test_the_plan_runs_nothing(monkeypatch, capsys):
    """
    Printing the day must never be able to place an order.
    """
    monkeypatch.setattr("copilot.live.shakedown.reference_price", lambda *_: PRICE)
    monkeypatch.setattr(
        "copilot.live.shakedown.run_phase",
        lambda *_, **__: pytest.fail("--plan ran a phase"),
    )

    assert main(["--plan", "--account", "DUT067974"]) == 0
    assert "places orders" in capsys.readouterr().out


def test_no_phase_prints_the_plan_and_stops(monkeypatch, capsys):
    monkeypatch.setattr("copilot.live.shakedown.reference_price", lambda *_: PRICE)

    assert main(["--account", "DUT067974"]) == 0
    assert "Nothing has run" in capsys.readouterr().out


def test_running_outside_the_window_refuses_and_explains(monkeypatch, capsys):
    """
    The refusal has to say what the right hour is, or it is just an obstacle.
    """
    monkeypatch.setattr("copilot.live.shakedown.reference_price", lambda *_: PRICE)
    monkeypatch.setenv("IBAPI_TIMEZONE_ALIASES", "JST=Asia/Tokyo")
    monkeypatch.setattr("copilot.live.shakedown.datetime", _FrozenAt(_eastern(14, 0)))
    monkeypatch.setattr(
        "copilot.live.shakedown.run_phase",
        lambda *_, **__: pytest.fail("ran outside its window"),
    )

    assert main(["--phase", "open", "--account", "DUT067974"]) == 2
    out = capsys.readouterr().out
    assert "09:30-10:30 ET" in out
    assert "--force" in out


def test_a_missing_environment_refuses_before_connecting(monkeypatch, capsys):
    monkeypatch.setattr("copilot.live.shakedown.reference_price", lambda *_: PRICE)
    monkeypatch.delenv("IBAPI_TIMEZONE_ALIASES", raising=False)
    monkeypatch.setattr(
        "copilot.live.shakedown.run_phase",
        lambda *_, **__: pytest.fail("connected without the alias"),
    )

    assert main(["--phase", "open", "--account", "DUT067974"]) == 2
    assert "IBAPI_TIMEZONE_ALIASES" in capsys.readouterr().out


class _FrozenAt:
    """
    A stand-in for ``datetime`` whose ``now`` is fixed, leaving the rest intact.
    """

    def __init__(self, moment: datetime) -> None:
        self._moment = moment

    def now(self, tz=None):
        return self._moment
