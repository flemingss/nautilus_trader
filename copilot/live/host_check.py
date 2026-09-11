"""
What a fresh host is missing before it can run the paper campaign unattended.

    python -m copilot.live.host_check

The stand-up plan (``docs/DRAFT_PAPER_VM.md``) is a sequence of stages, each gated on a
check; this is the check that can be automated, run at stage four and again whenever the
host is touched. Each line is one condition, **required** or **advised**, and the exit code is
non-zero when any required one fails. It reads and probes; it changes nothing.

It exists because the dev box's own setup gaps were each found by a failure rather than a
list - a linker path, a catalog band, a read-only API, a missing account - and a VM that
runs while the operator sleeps should meet its gaps here instead.

"""

from __future__ import annotations

import os
import shutil
import socket
import stat
import subprocess
import sys
from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from copilot.live.alerting import alert_settings
from copilot.live.day import EVENING
from copilot.live.day import MORNING
from copilot.live.day import required_environment
from copilot.live.halt import read_latch
from copilot.paths import CATALOG_PATH_ENV
from copilot.paths import DEFAULT_CATALOG
from copilot.paths import HEARTBEAT_URL_ENV
from copilot.paths import OPS_CONFIG_DIR


REPO = Path(__file__).resolve().parents[2]

MIN_FREE_GIB = 20
"""
Free disk the host must keep: session records, verdicts, the Databento store, and room.
"""

MIN_MEMORY_GIB = 16
"""
Below this the source build locked a 15 GB WSL box up four times on 2026-09-10.
"""

TIMERS = (
    "copilot-day-morning.timer",
    "copilot-day-evening.timer",
    "copilot-day-sweep.timer",
    "copilot-shakedown-pre-open.timer",
    "copilot-shakedown-open.timer",
    "copilot-shakedown-order-window.timer",
    "copilot-shakedown-midday.timer",
    "copilot-shakedown-close.timer",
)

REQUIRED = "required"
ADVISED = "advised"

Run = Callable[[list[str]], tuple[int, str]]


@dataclass(frozen=True)
class Finding:
    """
    One condition, whether it holds, and what to do if not.
    """

    name: str
    ok: bool
    level: str
    detail: str


def run(command: list[str]) -> tuple[int, str]:
    """
    Run a probe command and return its exit code and output; a missing binary is 127.
    """
    try:
        done = subprocess.run(command, capture_output=True, text=True, check=False, timeout=30)  # noqa: S603
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return 127, str(e)
    return done.returncode, (done.stdout + done.stderr).strip()


def check_environment(environ: Mapping[str, str]) -> list[Finding]:
    """
    Check every variable a scheduled morning and evening refuse without.
    """
    account = environ.get("COPILOT_PAPER_ACCOUNT", "")
    missing = sorted(
        {
            line
            for phase in (MORNING, EVENING)
            for line in required_environment(
                phase,
                environ=environ,
                account=account,
                scheduled=True,
            )
        },
    )
    findings = [
        Finding(
            "environment",
            not missing,
            REQUIRED,
            "; ".join(missing) or "everything a scheduled day needs",
        ),
    ]
    heartbeat = environ.get(HEARTBEAT_URL_ENV, "")
    findings.append(
        Finding(
            "heartbeat URL",
            bool(heartbeat),
            ADVISED,
            heartbeat or f"{HEARTBEAT_URL_ENV} unset: nothing off the host will notice a stop",
        ),
    )
    deadline = alert_settings(environ)
    findings.append(
        Finding(
            "alert deadline",
            not deadline.problems,
            REQUIRED,
            "; ".join(deadline.problems)
            or f"{deadline.retry_seconds}s retries over {deadline.expire_seconds}s",
        ),
    )
    return findings


def check_secret_files(config_dir: Path) -> list[Finding]:
    """
    Check the secret files exist and nobody but their owner can read them.
    """
    findings = []
    for name in ("secrets.env", "gateway.env", "tws_password"):
        path = config_dir / name
        if not path.exists():
            findings.append(
                Finding(name=name, ok=False, level=REQUIRED, detail=f"{path} does not exist"),
            )
            continue
        mode = stat.S_IMODE(path.stat().st_mode)
        private = mode & 0o077 == 0
        findings.append(
            Finding(
                f"{name}",
                private,
                REQUIRED,
                f"{path} mode {mode:03o}" + ("" if private else "; chmod 600 it"),
            ),
        )
    return findings


def check_gateway_pin(compose: Path) -> Finding:
    """
    Check the Gateway image is pinned by digest (ADR-0007), not by a tag that moves.
    """
    text = compose.read_text() if compose.exists() else ""
    image = next(
        (line.split("image:", 1)[1].strip() for line in text.splitlines() if "image:" in line),
        "",
    )
    pinned = "@sha256:" in image and "PIN-AT-STAND-UP" not in image
    if pinned:
        detail = image
    else:
        detail = f"{image or f'no image in {compose}'}: pin it by digest (ADR-0007)"
    return Finding("gateway image pinned", pinned, REQUIRED, detail)


def check_broker_port(
    host: str,
    port: int,
    *,
    connect: Callable[..., object] = socket.create_connection,
) -> Finding:
    """
    Check something is listening where the sessions will connect.
    """
    try:
        with connect((host, port), timeout=5):  # type: ignore[attr-defined]
            pass
    except OSError as e:
        return Finding(
            name="broker port",
            ok=False,
            level=REQUIRED,
            detail=f"{host}:{port} not reachable ({e}); is the Gateway up?",
        )
    return Finding(
        name="broker port",
        ok=True,
        level=REQUIRED,
        detail=f"{host}:{port} accepts connections",
    )


def check_host(run_command: Run = run) -> list[Finding]:
    """
    Check the machine: clock, linger, timers, Docker, memory and disk.
    """
    findings = []
    code, out = run_command(["timedatectl", "show", "-p", "NTPSynchronized", "--value"])
    findings.append(
        Finding(
            "clock synchronised",
            code == 0 and out == "yes",
            REQUIRED,
            out or "timedatectl unavailable",
        ),
    )
    user = os.environ.get("USER", "")
    code, out = run_command(["loginctl", "show-user", user, "-p", "Linger", "--value"])
    findings.append(
        Finding(
            "linger",
            code == 0 and out == "yes",
            REQUIRED,
            "user timers run without a login"
            if out == "yes"
            else f"sudo loginctl enable-linger {user}",
        ),
    )
    code, out = run_command(["systemctl", "--user", "is-enabled", *TIMERS])
    states = out.split()
    enabled = code == 0 and len(states) == len(TIMERS) and all(s == "enabled" for s in states)
    findings.append(
        Finding(
            "timers enabled",
            enabled,
            REQUIRED,
            "all eight" if enabled else "bash copilot/ops/install-units.sh --enable",
        ),
    )
    code, out = run_command(["docker", "info", "--format", "{{.ServerVersion}}"])
    findings.append(
        Finding("docker", code == 0, REQUIRED, out.splitlines()[-1] if out else "not available"),
    )
    memory = _memory_gib()
    findings.append(
        Finding(
            "memory",
            memory >= MIN_MEMORY_GIB,
            ADVISED,
            f"{memory:.1f} GiB, want {MIN_MEMORY_GIB}",
        ),
    )
    free = shutil.disk_usage(REPO).free / 2**30
    findings.append(
        Finding(
            "disk",
            free >= MIN_FREE_GIB,
            REQUIRED,
            f"{free:.0f} GiB free, want {MIN_FREE_GIB}",
        ),
    )
    return findings


def check_project(environ: Mapping[str, str], run_command: Run = run) -> list[Finding]:
    """
    Check the build, the catalog, the git guards and the halt on this host.
    """
    findings = []
    try:
        from nautilus_trader.live import LiveNode  # noqa: PLC0415 - the check is the import

        bound = hasattr(LiveNode, "risk_engine")
    except ImportError as e:
        bound, detail = False, f"nautilus_trader does not import: {e}"
    else:
        detail = (
            "LiveNode.risk_engine present"
            if bound
            else "built without the fork's risk engine binding"
        )
    # ADR-0007: a build without the binding runs, connects, and cannot stop the next order.
    findings.append(Finding("source build", bound, REQUIRED, detail))

    catalog = Path(environ.get(CATALOG_PATH_ENV, DEFAULT_CATALOG)).expanduser()
    findings.append(Finding("catalog", catalog.is_dir(), REQUIRED, str(catalog)))

    code, out = run_command(["git", "-C", str(REPO), "remote", "get-url", "--push", "upstream"])
    guarded = code != 0 or "DISABLED" in out
    findings.append(
        Finding(
            "upstream push disabled",
            guarded,
            REQUIRED,
            out if code == 0 else "no upstream remote",
        ),
    )
    latch = read_latch()
    findings.append(
        Finding(
            "halt latch",
            latch is None,
            ADVISED,
            "not engaged" if latch is None else f"ENGAGED {latch.latch_id}: {latch.reason}",
        ),
    )
    return findings


def _memory_gib() -> float:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) / 2**20
    except OSError:
        pass
    return 0.0


def report(findings: list[Finding]) -> int:
    """
    Print the findings and return non-zero if any required one fails.
    """
    for finding in findings:
        mark = "ok  " if finding.ok else ("FAIL" if finding.level == REQUIRED else "warn")
        print(f"  {mark}  {finding.name:<24}{finding.detail}")
    failed = [f for f in findings if not f.ok and f.level == REQUIRED]
    print(
        f"\n{len(failed)} required check(s) failing."
        if failed
        else "\nReady: every required check passes.",
    )
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    """
    Check this host.
    """
    del argv
    environ = os.environ
    host = environ.get("IB_V2_HOST", "127.0.0.1")
    port = int(environ.get("IB_V2_PORT", "4002"))
    findings = [
        *check_environment(environ),
        *check_secret_files(Path(OPS_CONFIG_DIR).expanduser()),
        check_gateway_pin(REPO / "copilot/ops/gateway/compose.yaml"),
        check_broker_port(host, port),
        *check_host(),
        *check_project(environ),
    ]
    return report(findings)


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "ADVISED",
    "REQUIRED",
    "TIMERS",
    "Finding",
    "check_broker_port",
    "check_environment",
    "check_gateway_pin",
    "check_host",
    "check_project",
    "check_secret_files",
    "main",
    "report",
]
