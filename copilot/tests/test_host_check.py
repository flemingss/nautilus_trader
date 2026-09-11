"""
The host check and the ops package it checks, kept honest against the code they drive.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

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
    (env, _heartbeat) = check_environment(without)
    assert env.ok is False
    assert PUSHOVER_TOKEN_ENV in env.detail


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


def test_every_timer_fires_a_phase_that_exists() -> None:
    names = {p.name for p in shakedown_phases(Connection("h", 1, "a"), Decimal(100))}
    for timer in SYSTEMD.glob("*.timer"):
        unit = re.search(r"^Unit=(\S+)$", timer.read_text(), re.MULTILINE).group(1)
        template, instance = re.fullmatch(r"(copilot-\w+)@([\w-]+)\.service", unit).groups()
        assert (SYSTEMD / f"{template}@.service").exists(), unit
        known = DAY_PHASES if template == "copilot-day" else names
        assert instance in known, f"{timer.name} fires {instance!r}, which is not a phase"


def test_every_service_runs_scheduled_and_bounded() -> None:
    for service in SYSTEMD.glob("*.service"):
        text = service.read_text()
        assert "--scheduled" in text, service.name
        assert "MemoryMax=" in text, f"{service.name}: a runaway must not take the host"
        assert "EnvironmentFile=%h/.config/copilot/secrets.env" in text


def test_every_timer_is_anchored_to_new_york() -> None:
    for timer in SYSTEMD.glob("*.timer"):
        assert "America/New_York" in timer.read_text(), timer.name
