# Draft: standing up the paper VM

**Status: plan, written 2026-09-10 in prep mode.** The owner has no access to the home lab
until **2026-09-15**, when a hand-configured Ubuntu VM is stood up as the always-on paper
host ([ADR-0022](decisions/0022-the-always-on-host-is-a-dedicated-vm.md)). Until then the
work is preparation that can be built and tested from the dev box; everything that needs the
VM itself waits for it and is listed here so the stand-up day is a checklist, not a design
session. Every item is tracked in [`ROADMAP.md`](ROADMAP.md).

Hand configuration is deliberate. This is paper and pre-candidate; the owner's cluster is
infrastructure as code, and this host moves toward those standards as it matures rather than
starting there ([ADR-0006](decisions/0006-ops-progression.md)'s stage two, not stage three).

## What the VM is for

**The system clock, not the strategy clock.** The paper campaign needs eight weeks plus at
least 30 reconciled order-lifecycle or injected-failure events with zero unresolved critical
incidents ([`playbook/OPERATIONS.md`](playbook/OPERATIONS.md)). Nothing gates that except a
host. The strategy clock stays stopped: the gap-fade family is rejected and no premise is in
research, so strategies run with **orders denied** and their decisions are checked against
replay, while real orders come only from the shakedown battery's minimum-size probes.

**Edge is not refined here.** The charter forbids changing a strategy for paper P&L. Research
stays on the dev box - hypothesis, walk-forward, net evidence interval, attribution, holdout -
and the VM proves that whatever comes out of it can be run.

## The daily loop

Anchored to the NYSE session in Eastern time, fired by systemd timers, each command gated on
its own exit code and its own session check.

| Eastern      | What runs                        | Produces                                                           |
| ------------ | -------------------------------- | ------------------------------------------------------------------ |
| 07:35        | `shakedown --phase pre-open`     | Connection, account and quote freshness before the bell            |
| 08:30        | `day evening`                    | Preflight, warm-up, the basket with orders denied, decision record |
| 09:31        | `shakedown --phase open`         | Fifty minutes of live quotes in the execution window               |
| 10:30        | `day sweep`                      | Monitoring-end cancel, confirmed against the broker                |
| 10:45        | `shakedown --phase order-window` | A rested and cancelled minimum-size order: lifecycle events        |
| 11:35        | `shakedown --phase midday`       | Failure injection: the denial, reject and reconciliation cases     |
| 15:20        | `shakedown --phase close`        | The closing contrast against the measured spread                   |
| 17:00        | `day morning`                    | Append, corporate actions, verdicts, live-versus-replay comparison |
| 17:15        | heartbeat                        | One `INFO` summary; an external check alerts if it does not arrive |
| Gateway sets | IB Gateway restart               | The daily recovery drill, whether we want one or not               |

**Built 2026-09-10** as `copilot/ops/systemd/` - every service runs with `--scheduled`, and the
commands for each stand-up stage are [`copilot/ops/README.md`](../ops/README.md). Shakedown phases
fire a few minutes into their windows. The order-window phase moves to 10:45 because it would
otherwise open at 10:30 with the sweep,
and the sweep cancels working orders - including the probe's. Early closes move everything
after 13:00; `day` knows the calendar and the timers must defer to it rather than to the
clock.

## Decided

| Question           | Answer                                                                                                                                              |
| ------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| OS                 | Ubuntu 26.04, the release the source build was measured on                                                                                          |
| Size               | 8 vCPU, 32 GB RAM, 150 GB disk. The build's `target/` is ~26 GB, and 15 GB of RAM locked WSL up four times on 2026-09-10                            |
| Gateway            | IBC-based `ghcr.io/gnzsnz/ib-gateway:stable` in Docker, as ADR-0006 names                                                                           |
| Secrets            | `~/.config/copilot/secrets.env`, mode 600, outside the tree: IB paper login, Marketstack, Databento, Pushover. Placed by the owner; never committed |
| Pushover           | Shaken out on the VM with real credentials, not before                                                                                              |
| The dev box's role | Research only once the VM streams. It stops running `day`, and reaches the broker through the VM's Gateway rather than a second login               |
| Configuration      | By hand for now, from the runbook in `copilot/ops/`; converging on the cluster's standards later                                                    |

## Before the 15th: prep, from the dev box

In order. Each is code or configuration that can be written and tested without the VM; the
last column is what makes it done.

| #   | Item                                                             | Done when                                                                                                                            |
| --- | ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| 1   | **Make `day` safe to fire from a timer** (done)                  | On a weekend, a holiday or a second run for the same session, each phase exits 0 saying there is nothing to do, and a test proves it |
| 2   | **Fix the evening's ordering** (done)                            | `day evening` at 08:30 passes on a normal day; the vendor's bar lag no longer skips the basket and the sweep                         |
| 3   | **Wire alerting into its callers** (done)                        | The sweep's unconfirmed order, safe mode and `day`'s stopping failures each call the alerter; unconfigured, each prints to stderr    |
| 4   | **The heartbeat's summary** (done)                               | `day morning` ends with one `INFO` summary of what ran and what passed                                                               |
| 5   | **Review the guard's cooldown across a restart** (done)          | A decision on persisting breach state, and a test that a restart cannot end a cooldown early                                         |
| 6   | **`copilot/ops/`: runbook, units, compose, env template** (done) | The stand-up below is written as commands; systemd units and timers, the Gateway compose file and an environment template exist      |
| 7   | **A host check** (done)                                          | One command reports what a fresh VM is missing: variables, secrets file mode, Docker, Gateway port, clock sync, catalog, disk        |
| 8   | **Confirm the sweep against the broker** (done)                  | *Clear* means the broker's own open orders are empty, not that no rejection arrived; testable against TWS on the dev box             |
| 9   | **The operator kill command** (done)                             | One command halts, cancels per policy, alerts and prints the recovery checklist; testable against TWS                                |

Items 1 to 7 are what the stand-up needs. Items 8 and 9 are what unattended running needs, and
can land in the VM's first week if time runs out. The next premise (a separate roadmap row)
runs alongside all of it on the dev box.

## On the 15th and after: stand-up, in stages

Each stage has a check that must pass before the next starts. Nothing is scheduled until
stage six.

| Stage | Do                                                                                                                                     | Passes when                                                                                                           |
| ----- | -------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| 1     | Provision; clone; re-arm the git guards; toolchain; `make build-debug` with the linker fix                                             | The build links, and `pytest copilot/tests` passes on the VM                                                          |
| 2     | Copy `~/.nautilus_copilot` from the dev box; place `secrets.env`                                                                       | **`validate --changed` reports every verdict unchanged**: the VM reproduces the dev box from the same commit and data |
| 3     | Pushover: `alerting --send-test`, then a `CRITICAL` acknowledged from the phone; set `COPILOT_HEARTBEAT_URL` to the watcher's push URL | Delivery and the acknowledgement receipt both read back; a scheduled morning's ping arrives at the watcher            |
| 4     | Gateway container: paper, `READ_ONLY_API=no`, host port 4002 to the container's 4004                                                   | `preflight` passes 15 of 15 against Gateway; the host check is clean                                                  |
| 5     | One supervised day by hand, the loop above in order                                                                                    | Every phase exits as designed; the sweep is clear against the broker; morning's comparison matches replay             |
| 6     | Enable the timers; watch the first Gateway restart                                                                                     | The loop runs a second day untouched, and the node reconnects and reconciles across the restart                       |
| 7     | Unattended                                                                                                                             | The playbook's gate: alerts and recovery drills passed, the kill command exists, a week of stage six clean            |

## To settle at stand-up, not before

- **How records get back to the repository.** Session records, verdicts and comparisons are
  written into the working tree and committed. On an unattended host nobody commits them.
  Recommendation: a deploy key scoped to this repository, and a daily push of records to a
  branch that is merged by pull request, so evidence survives the VM and nothing lands on
  `develop` unreviewed.
- **Where the heartbeat is watched.** Recommendation: the owner's existing cluster monitoring,
  as an observer of the VM rather than its host.
- **Whether the timezone alias is still needed.** `day` refuses without
  `IBAPI_TIMEZONE_ALIASES="JST=Asia/Tokyo"`, a workaround for a TWS configured in Japan.
  Export it, measure against an Eastern-configured Gateway, and relax the check only on
  evidence.
- **Two-factor login for the paper user under IBC.** Confirm it does not prompt; if it does,
  stage four needs the owner present.

## Traps carried in

- **The Gateway takes the session.** The adapter's container settings include
  `EXISTING_SESSION_DETECTED_ACTION=primary`, so the VM's login displaces TWS on the dev box.
  One session per login ([ADR-0022](decisions/0022-the-always-on-host-is-a-dedicated-vm.md)).
- **Bypass order precautions for API orders** in the Gateway's API settings. An order held
  untransmitted by a precautionary size setting never reaches the broker, so neither the
  sweep's census nor any other API call can see or cancel it.
- **`READ_ONLY_API` defaults to on**, and read-only blocks the execution client with IB 321,
  whose symptom is a missing account rather than an error.
- **`session.py` guards paper with two checks that must agree**, and the port is one of them:
  Gateway paper is 4002, not TWS's 7497.
- **Hook runs**: `SKIP=python-test-collection` whenever a `python/` path is in scope; on the VM
  the memory is larger but the rule costs nothing ([`../AGENTS.md`](../AGENTS.md)).
- **The published wheel is forbidden** ([ADR-0007](decisions/0007-self-sourced-images.md)):
  the VM builds from source.
- **One operator day per catalog.** Once the VM runs `day`, the dev box does not: two hosts
  appending and filing verdicts from one repository produce conflicting records.
