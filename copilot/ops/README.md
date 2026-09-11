# Paper VM operations

Roles in the repository, instances outside it ([ADR-0006](../docs/decisions/0006-ops-progression.md)).
Everything here is the **paper VM's** configuration as code, rendered onto a hand-configured
Ubuntu host ([ADR-0022](../docs/decisions/0022-the-always-on-host-is-a-dedicated-vm.md)). The
plan and its reasoning are [`DRAFT_PAPER_VM.md`](../docs/DRAFT_PAPER_VM.md); this file is the
commands.

| Path                   | What                                                                                       |
| ---------------------- | ------------------------------------------------------------------------------------------ |
| `systemd/`             | User services for `day` and the shakedown, and the eight timers that fire them, in Eastern |
| `install-units.sh`     | Renders the units with this clone's path and installs them; `--enable` starts the timers   |
| `gateway/compose.yaml` | IB Gateway under IBC, paper, pinned by digest at stand-up                                  |
| `env/*.example`        | Every variable the services read, secrets named and never valued                           |

The host check, `python -m copilot.live.host_check`, reports what a host is missing against all
of it and changes nothing.

## The schedule

| Eastern | Unit                             | Days      |
| ------- | -------------------------------- | --------- |
| 07:35   | `copilot-shakedown@pre-open`     | Weekdays  |
| 08:30   | `copilot-day@evening`            | Weekdays  |
| 09:31   | `copilot-shakedown@open`         | Weekdays  |
| 10:30   | `copilot-day@sweep`              | Weekdays  |
| 10:45   | `copilot-shakedown@order-window` | Weekdays  |
| 11:35   | `copilot-shakedown@midday`       | Weekdays  |
| 15:20   | `copilot-shakedown@close`        | Weekdays  |
| 17:00   | `copilot-day@morning`            | Every day |

Every service runs with `--scheduled`, so a holiday, an early close, a late timer or an
engaged halt latch is *nothing to do* and exit 0, decided by the code rather than the calendar
line. The morning runs on weekends too, so the heartbeat never goes quiet.

## Stand-up

Each stage ends with its check. Do not start the next until it passes.

### 1. Build

Follow `MAINTENANCE.md`, *Standing up a new machine*: clone, re-arm the git guards, the
toolchain, and `make build-debug` with the `LIBRARY_PATH` fix. Then:

```bash
PYTHONPATH=. .venv/bin/python -m pytest copilot/tests -q
```

**Passes when** the suite passes. Hook runs on this host use
`SKIP=python-test-collection prek run --files <files>`.

### 2. Data and configuration

```bash
# from the dev box, once:
rsync -a ~/.nautilus_copilot/ vm:~/.nautilus_copilot/
# on the VM:
mkdir -p ~/.config/copilot && chmod 700 ~/.config/copilot
cp copilot/ops/env/copilot.env.example ~/.config/copilot/copilot.env
cp copilot/ops/env/secrets.env.example ~/.config/copilot/secrets.env
cp copilot/ops/env/gateway.env.example ~/.config/copilot/gateway.env
touch ~/.config/copilot/tws_password
chmod 600 ~/.config/copilot/*.env ~/.config/copilot/tws_password
# fill them from the password manager, then:
set -a && . ~/.config/copilot/copilot.env && . ~/.config/copilot/secrets.env && set +a
.venv/bin/python -m copilot.strategies.validate --changed
```

**Passes when** `validate --changed` reports every verdict unchanged: the VM reproduces the
dev box from the same commit and the same data.

### 3. Alerting

```bash
.venv/bin/python -m copilot.live.alerting --send-test --severity warning
.venv/bin/python -m copilot.live.alerting --send-test --severity critical   # acknowledge on the phone
.venv/bin/python -m copilot.live.alerting --receipt <receipt it printed>
```

Set `COPILOT_HEARTBEAT_URL` in `copilot.env` to the watcher's push URL.

**Passes when** both arrive, and the receipt reads back acknowledged.

### 4. Gateway

```bash
docker pull ghcr.io/gnzsnz/ib-gateway:stable
docker inspect --format '{{index .RepoDigests 0}}' ghcr.io/gnzsnz/ib-gateway:stable
# put that digest in gateway/compose.yaml, commit it, then:
docker compose -f copilot/ops/gateway/compose.yaml up -d
```

Through an SSH tunnel to `127.0.0.1:5900`, once: in the API settings, **bypass order
precautions for API orders**, and confirm read-only is off. The settings volume keeps both.
Confirm IB's current nightly reset time against `AUTO_RESTART_TIME`.

```bash
.venv/bin/python -m copilot.live.preflight --host 127.0.0.1 --port 4002 --account "$COPILOT_PAPER_ACCOUNT"
.venv/bin/python -m copilot.live.host_check
```

**Passes when** preflight is 15 of 15 and the host check's only failures are the timers and
linger, which stage six fixes. Measure here whether `IBAPI_TIMEZONE_ALIASES` is still needed.

### 5. One supervised day, by hand

The loop in the schedule's order, each phase with `--scheduled` so it is the command the timer
will run, watched:

```bash
.venv/bin/python -m copilot.live.shakedown --phase pre-open --scheduled
.venv/bin/python -m copilot.live.day evening --scheduled
# ... through the close, then
.venv/bin/python -m copilot.live.day morning --scheduled
```

**Passes when** every phase exits as designed, the sweep reads `BROKER CLEAR`, and the
morning's comparison matches the replay.

### 6. Timers

```bash
sudo loginctl enable-linger "$USER"
bash copilot/ops/install-units.sh --enable
.venv/bin/python -m copilot.live.host_check
journalctl --user -u 'copilot-*' --since today
```

**Passes when** the host check is clean, the loop runs a second day untouched, and the node
reconnects and reconciles across the Gateway's first restart.

### 7. Unattended

The playbook's gate: alerts and recovery drills passed, a kill drill on this host
(`python -m copilot.live.kill --reason "drill" && python -m copilot.live.kill --release <id>`),
and a week of stage six clean.

## Day to day

| Want to                         | Run                                              |
| ------------------------------- | ------------------------------------------------ |
| Stop all trading on this host   | `python -m copilot.live.kill --reason "..."`     |
| See whether it is halted        | `python -m copilot.live.kill --status`           |
| See what ran                    | `journalctl --user -u 'copilot-*' --since today` |
| See what is scheduled           | `systemctl --user list-timers 'copilot-*'`       |
| Pause the schedule, not trading | `systemctl --user stop 'copilot-*.timer'`        |
| Check the host                  | `python -m copilot.live.host_check`              |
