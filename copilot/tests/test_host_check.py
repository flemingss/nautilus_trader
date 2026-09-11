"""
The host check and the ops package it checks, kept honest against the code they drive.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from typing import Self

import pytest

from copilot.live.day import PHASES as DAY_PHASES
from copilot.live.day import REQUIRED_TIMEZONE_ALIAS
from copilot.live.day import TIMEZONE_ALIASES_ENV
from copilot.live.day import Connection
from copilot.live.host_check import ADVISED
from copilot.live.host_check import REPO
from copilot.live.host_check import REQUIRED
from copilot.live.host_check import TIMERS
from copilot.live.host_check import Finding
from copilot.live.host_check import check_broker_port
from copilot.live.host_check import check_environment
from copilot.live.host_check import check_gateway_pin
from copilot.live.host_check import check_host
from copilot.live.host_check import check_secret_files
from copilot.live.host_check import report
from copilot.live.shakedown import phases as shakedown_phases
from copilot.paths import MARKETSTACK_API_KEY_ENV
from copilot.paths import PUSHOVER_TOKEN_ENV
from copilot.paths import PUSHOVER_USER_KEY_ENV


SYSTEMD = REPO / "copilot/ops/systemd"

COMPLETE = {
    "COPILOT_PAPER_ACCOUNT": "DUT067974",
    TIMEZONE_ALIASES_ENV: REQUIRED_TIMEZONE_ALIAS,
    MARKETSTACK_API_KEY_ENV: "k",
    PUSHOVER_TOKEN_ENV: "t",
    PUSHOVER_USER_KEY_ENV: "u",
}


# ----------------------------------------------------------------------------- checks


def test_a_complete_environment_passes_and_names_what_is_missing_otherwise() -> None:
    assert all(f.ok for f in check_environment(COMPLETE) if f.level == REQUIRED)

    without = {k: v for k, v in COMPLETE.items() if k != PUSHOVER_TOKEN_ENV}
    env = check_environment(without)[0]
    assert env.ok is False
    assert PUSHOVER_TOKEN_ENV in env.detail


def test_an_alert_deadline_pushover_would_refuse_fails_the_host() -> None:
    """
    Audit F2: it raised inside every phase; now it falls back, and the host check says so.
    """
    findings = {f.name: f for f in check_environment(COMPLETE)}
    assert findings["alert deadline"].ok

    bad = {
        f.name: f for f in check_environment({**COMPLETE, "COPILOT_ALERT_EXPIRE_SECONDS": "10800"})
    }
    assert bad["alert deadline"].ok is False
    assert bad["alert deadline"].level == REQUIRED
    assert "default deadline is in force" in bad["alert deadline"].detail


def test_a_missing_heartbeat_warns_rather_than_fails() -> None:
    heartbeat = check_environment(COMPLETE)[1]

    assert heartbeat.ok is False
    assert heartbeat.level == ADVISED


def test_a_secret_file_anyone_else_can_read_fails(tmp_path: Path) -> None:
    for name in ("secrets.env", "gateway.env", "tws_password"):
        (tmp_path / name).write_text("x")
        (tmp_path / name).chmod(0o600)
    (tmp_path / "tws_password").chmod(0o644)

    findings = {f.name: f for f in check_secret_files(tmp_path)}

    assert findings["secrets.env"].ok
    assert findings["tws_password"].ok is False
    assert "chmod 600" in findings["tws_password"].detail


def test_a_missing_secret_file_fails(tmp_path: Path) -> None:
    assert not any(f.ok for f in check_secret_files(tmp_path))


@pytest.mark.parametrize(
    ("image", "pinned"),
    [
        ("ghcr.io/gnzsnz/ib-gateway:stable", False),
        ("ghcr.io/gnzsnz/ib-gateway@sha256:PIN-AT-STAND-UP", False),
        ("ghcr.io/gnzsnz/ib-gateway@sha256:" + "a" * 64, True),
    ],
)
def test_the_gateway_must_be_pinned_by_digest(tmp_path: Path, image: str, pinned: bool) -> None:
    compose = tmp_path / "compose.yaml"
    compose.write_text(f"services:\n  ib-gateway:\n    image: {image}\n")

    assert check_gateway_pin(compose).ok is pinned


def test_the_committed_compose_is_unpinned_until_stand_up() -> None:
    """
    ADR-0007: the digest is resolved on the VM, and the check refuses until it is.
    """
    assert check_gateway_pin(REPO / "copilot/ops/gateway/compose.yaml").ok is False


def test_an_unreachable_broker_port_fails() -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise ConnectionRefusedError("refused")

    assert check_broker_port("127.0.0.1", 4002, connect=refuse).ok is False


def test_the_host_passes_when_the_machine_is_set_up() -> None:
    answers = {
        "timedatectl": (0, "yes"),
        "loginctl": (0, "yes"),
        "systemctl": (0, "\n".join(["enabled"] * len(TIMERS))),
        "docker": (0, "29.7.2"),
    }

    findings = check_host(lambda command: answers[command[0]])

    assert all(f.ok for f in findings if f.level == REQUIRED and f.name != "disk")


def test_a_host_without_linger_fails_and_says_how_to_fix_it() -> None:
    answers = {
        "timedatectl": (0, "yes"),
        "loginctl": (0, "no"),
        "systemctl": (1, "disabled"),
        "docker": (127, ""),
    }

    findings = {f.name: f for f in check_host(lambda command: answers[command[0]])}

    assert findings["linger"].ok is False
    assert "enable-linger" in findings["linger"].detail
    assert findings["timers enabled"].ok is False
    assert findings["docker"].ok is False


def test_only_a_failing_required_check_fails_the_host(capsys) -> None:
    assert report([Finding("a", False, ADVISED, "x")]) == 0
    assert report([Finding("a", False, REQUIRED, "x")]) == 1


# ------------------------------------------------------------- the ops package itself


def test_the_host_check_knows_every_timer_the_package_ships() -> None:
    assert sorted(p.name for p in SYSTEMD.glob("*.timer")) == sorted(TIMERS)


PHASE_TEMPLATES = ("copilot-day", "copilot-shakedown")
STANDALONE_UNITS = ("copilot-acknowledgements.service",)


def _unit_of(timer: Path) -> str:
    return re.search(r"^Unit=(\S+)$", timer.read_text(), re.MULTILINE).group(1)


def test_every_timer_fires_a_phase_or_a_unit_that_exists() -> None:
    names = {p.name for p in shakedown_phases(Connection("h", 1, "a"), Decimal(100))}
    for timer in SYSTEMD.glob("*.timer"):
        unit = _unit_of(timer)
        if unit in STANDALONE_UNITS:
            assert (SYSTEMD / unit).exists(), unit
            continue
        template, instance = re.fullmatch(r"(copilot-\w+)@([\w-]+)\.service", unit).groups()
        assert template in PHASE_TEMPLATES, unit
        assert (SYSTEMD / f"{template}@.service").exists(), unit
        known = DAY_PHASES if template == "copilot-day" else names
        assert instance in known, f"{timer.name} fires {instance!r}, which is not a phase"


def test_every_service_is_bounded_and_every_phase_runs_scheduled() -> None:
    for service in SYSTEMD.glob("*.service"):
        text = service.read_text()
        assert "MemoryMax=" in text, f"{service.name}: a runaway must not take the host"
        assert "TimeoutStartSec=" in text, service.name
        assert "EnvironmentFile=%h/.config/copilot/secrets.env" in text
        if service.name.split("@")[0] in PHASE_TEMPLATES:
            assert "--scheduled" in text, service.name


def test_every_unit_but_the_failure_handler_reports_its_own_failure() -> None:
    """
    Audit F11: a traceback, a memory kill or a timeout notified nobody.
    """
    handler = SYSTEMD / "copilot-unit-failed@.service"
    assert "--unit-failed %i" in handler.read_text()
    for service in SYSTEMD.glob("*.service"):
        if service == handler:
            continue
        assert re.search(
            r"^OnFailure=copilot-unit-failed@\S+\.service$",
            service.read_text(),
            re.MULTILINE,
        ), service.name


def _flock(service: str) -> re.Match[str]:
    text = (SYSTEMD / service).read_text()
    match = re.search(r"^ExecStart=/usr/bin/flock --wait (\d+) (\S+) ", text, re.MULTILINE)
    assert match is not None, f"{service} does not take the broker lock"
    return match


def test_every_unit_that_touches_the_broker_takes_the_same_lock() -> None:
    """
    Audit F6: the 10:30 sweep's census retries could run into the 10:45 order window.
    """
    locks = {_flock(f"{template}@.service").group(2) for template in PHASE_TEMPLATES}

    assert locks == {"%t/copilot-broker.lock"}


def test_the_timeout_covers_the_lock_wait_and_the_evenings_worst_case() -> None:
    from copilot.live.cancel_working import sweep_worst_case_secs

    evening_other_steps_secs = 20 * 60  # append, scan, preflight, warm-up, basket
    for template in PHASE_TEMPLATES:
        service = f"{template}@.service"
        wait = int(_flock(service).group(1))
        minutes = re.search(
            r"^TimeoutStartSec=(\d+)min$",
            (SYSTEMD / service).read_text(),
            re.MULTILINE,
        )
        assert minutes is not None, service
        needed = wait + sweep_worst_case_secs() + evening_other_steps_secs
        assert int(minutes.group(1)) * 60 >= needed, f"{service}: {needed}s needed"


def test_a_phase_waits_for_the_gateway_but_does_not_fail_on_it() -> None:
    for template in PHASE_TEMPLATES:
        text = (SYSTEMD / f"{template}@.service").read_text()
        assert re.search(
            r"^ExecStartPre=-\S+ -m copilot.live.host_check --wait-for-broker \d+$",
            text,
            re.MULTILINE,
        )


def test_the_acknowledgement_check_runs_often_enough_for_its_deadline() -> None:
    """
    Audit F4: ADR-0023's deadline is an hour, and the day's phases reached it three times a day.
    """
    from copilot.live.alerting import DEFAULT_EXPIRE_SECONDS

    timer = (SYSTEMD / "copilot-acknowledgements.timer").read_text()
    step = re.search(r"^OnCalendar=\*-\*-\* \*:00/(\d+):00 America/New_York$", timer, re.MULTILINE)
    assert step is not None
    assert int(step.group(1)) * 60 <= DEFAULT_EXPIRE_SECONDS / 4
    service = (SYSTEMD / "copilot-acknowledgements.service").read_text()
    assert "SuccessExitStatus=1" in service, "a halted host is the check reporting, not failing"


RUNBOOK = REPO / "copilot/ops/README.md"


def _runbook_schedule() -> dict[str, tuple[str, str]]:
    """
    Return the runbook's schedule table: unit to (time, days).
    """
    rows = {}
    for line in RUNBOOK.read_text().splitlines():
        match = re.fullmatch(
            r"\| ([\d:]+|every \d+ min)\s*\| `([\w@-]+)`\s*\| ([\w ]+?)\s*\|",
            line,
        )
        if match:
            rows[match.group(2)] = (match.group(1), match.group(3))
    return rows


def test_every_timer_fires_when_the_runbook_says() -> None:
    schedule = _runbook_schedule()
    assert len(schedule) == len(TIMERS)
    for timer in SYSTEMD.glob("*.timer"):
        text = timer.read_text()
        unit = _unit_of(timer).removesuffix(".service")
        when, days = schedule[unit]
        calendar = re.search(r"^OnCalendar=(.+)$", text, re.MULTILINE).group(1)
        if when.startswith("every"):
            minutes = when.split()[1]
            assert f"*:00/{minutes}:00" in calendar, timer.name
        else:
            assert f" {when}:00 America/New_York" in calendar, timer.name
        assert calendar.startswith("Mon..Fri ") is (days == "Weekdays"), timer.name


def test_only_the_morning_and_the_sweep_catch_up_after_downtime() -> None:
    """
    The morning keeps the heartbeat alive, and a late sweep still has orders to clear;
    every other phase means nothing outside its window, which the code refuses anyway.
    """
    persistent = {
        _unit_of(timer): "Persistent=true" in timer.read_text() for timer in SYSTEMD.glob("*.timer")
    }

    assert {unit for unit, on in persistent.items() if on} == {
        "copilot-day@morning.service",
        "copilot-day@sweep.service",
    }


def _template_environment() -> dict[str, str]:
    environ = {}
    for name in ("copilot.env.example", "secrets.env.example"):
        for line in (REPO / "copilot/ops/env" / name).read_text().splitlines():
            if line.strip() and not line.startswith("#"):
                key, _, value = line.partition("=")
                environ[key] = value or "filled-from-the-password-manager"
    return environ


@pytest.mark.parametrize("phase", DAY_PHASES)
def test_the_env_templates_give_every_scheduled_phase_what_it_requires(phase: str) -> None:
    """
    A variable renamed in the code and not in the template breaks the VM silently.
    """
    from copilot.live.day import required_environment

    environ = _template_environment()

    missing = required_environment(
        phase,
        environ=environ,
        account=environ["COPILOT_PAPER_ACCOUNT"],
        scheduled=True,
    )

    assert missing == ()


def test_the_env_template_declares_a_deadline_the_host_check_accepts() -> None:
    findings = {f.name: f for f in check_environment(_template_environment())}

    assert findings["alert deadline"].ok


def test_the_gateway_is_paper_writable_and_reachable_from_this_host_only() -> None:
    compose = (REPO / "copilot/ops/gateway/compose.yaml").read_text()

    assert re.search(r"^\s+TRADING_MODE: paper$", compose, re.MULTILINE)
    assert re.search(r'^\s+READ_ONLY_API: "no"$', compose, re.MULTILINE)
    ports = re.findall(r'^\s+- "?([\d.]+:\d+:\d+)"?$', compose, re.MULTILINE)
    assert ports
    assert all(port.startswith("127.0.0.1:") for port in ports), ports


def test_install_units_renders_every_unit_with_this_clones_path(tmp_path: Path) -> None:
    import os
    import subprocess

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "systemctl.calls"
    systemctl = fake_bin / "systemctl"
    systemctl.write_text(f'#!/usr/bin/env bash\necho "$@" >> {calls}\n')
    systemctl.chmod(0o755)
    config = tmp_path / "config"

    completed = subprocess.run(
        ["bash", str(REPO / "copilot/ops/install-units.sh"), "--enable"],  # noqa: S607
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "XDG_CONFIG_HOME": str(config),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    installed = config / "systemd/user"
    shipped = sorted(p.name for p in SYSTEMD.iterdir() if p.suffix in {".service", ".timer"})
    assert sorted(p.name for p in installed.iterdir()) == shipped
    for unit in installed.iterdir():
        text = unit.read_text()
        assert "@REPO@" not in text, unit.name
    assert f"WorkingDirectory={REPO}" in (installed / "copilot-day@.service").read_text()
    enabled = calls.read_text()
    assert all(f"enable --now {timer}" in enabled for timer in TIMERS)


def test_every_timer_is_anchored_to_new_york() -> None:
    for timer in SYSTEMD.glob("*.timer"):
        assert "America/New_York" in timer.read_text(), timer.name


def test_the_broker_wait_returns_as_soon_as_the_gateway_answers() -> None:
    from copilot.live.host_check import wait_for_broker

    attempts = {"n": 0}

    def gateway_logging_in(*_args: object, **_kwargs: object) -> object:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ConnectionRefusedError("refused")
        return _Socket()

    finding = wait_for_broker(
        ("127.0.0.1", 4002),
        deadline_secs=300,
        connect=gateway_logging_in,
        sleep=lambda _s: None,
        clock=lambda: 0.0,
    )

    assert finding.ok
    assert attempts["n"] == 3


def test_the_broker_wait_gives_up_at_its_deadline() -> None:
    from copilot.live.host_check import wait_for_broker

    ticks = iter([0.0, 100.0, 400.0])

    def refuse(*_args: object, **_kwargs: object) -> object:
        raise ConnectionRefusedError("refused")

    finding = wait_for_broker(
        ("127.0.0.1", 4002),
        deadline_secs=300,
        connect=refuse,
        sleep=lambda _s: None,
        clock=lambda: next(ticks),
    )

    assert finding.ok is False


class _Socket:
    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def test_no_overlay_file_is_hidden_by_an_inherited_ignore_rule() -> None:
    """
    Audit batch D: the root ``env/`` and ``*.sh`` rules kept the VM's env templates and its unit
    installer out of the repository from the pull request that wrote them. Every check here
    passed on the machine that had the files, and a fresh clone on the VM would not.
    """
    import subprocess

    listed = subprocess.run(
        ["git", "status", "--ignored", "--porcelain", "--untracked-files=all", "copilot"],  # noqa: S607
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    hidden = [
        line[3:]
        for line in listed
        if line.startswith("!! ") and "__pycache__" not in line and not line.endswith(".pyc")
    ]

    assert hidden == []
