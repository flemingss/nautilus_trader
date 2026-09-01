# 6. Ops progression: WSL and TWS, then Gateway, then Kubernetes

- **Status:** Accepted
- **Date:** 2026-09-01
- **Deciders:** Project owner

## Context

Three deployment shapes were on the table, and the temptation was to build the last one
first.

**TWS on the Windows host, reached from WSL** is what exists. It works, and it has two
properties that make it unsuitable for unattended running: TWS is a GUI application that
needs a human to log in, and IB force-logs-out daily. There is also one session per login,
so opening the IB web portal during a run displaces the API's historical data service and
produces error 162 while the socket stays connected.

**A dockerized IB Gateway** removes both. The adapter already supports it:
`DockerizedIBGatewayConfig` targets `ghcr.io/gnzsnz/ib-gateway:stable`, an IBC-based
headless image that handles login and the daily restart, takes credentials from
`TWS_USERNAME` and `TWS_PASSWORD`, exposes a VNC port for when the screen must be seen,
and **defaults `read_only_api` to true**. This is a first-class upstream feature, not
something to invent.

**Kubernetes** is where this ends up if the system proves out, and is where trade-copilot
was heading (its ADR-0021).

## Decision

Adopt the shapes in order, and do not skip ahead.

| Stage | Shape | Gate to leave it |
| --- | --- | --- |
| **1. Now** | WSL, TWS on the host, run by hand | Baseline established and a strategy validated |
| **2. Next** | Dockerized IB Gateway, compose, unattended paper | Stable and viable over a real paper run |
| **3. Later** | Kubernetes | Proven, and worth the operational surface |

Stage 1 is for development and the initial baseline. Its limits are accepted, not
engineered around: it is attended, it needs a human to log in, and the box may sleep.

**Do not treat stage 1 as unattended.** A laptop that sleeps is intermittent, and the
risk breakers assume continuous evaluation. Before any unattended paper run, the guard's
cooldown behaviour across a restart must be reviewed - a cooldown that resets on restart
is not a cooldown.

Where the definitions live follows the rule already set: **in repo, `copilot/ops/`** for
Dockerfile, compose template and entrypoints, because they are code and must version with
the overlay. **Out of repo** for the deploy directory: `.env`, resolved compose overrides,
volumes and logs. In-repo names roles, out-of-repo names instances.

Deliberately **not** extending upstream's `.docker/`. That builds nautilus itself; ours
builds our overlay on top of it. Different job, and our own namespace means zero delta.

## Consequences

- Each stage is usable on its own, so effort is never stranded waiting for the next.
- Stage 2 is the first point at which credentials sit in a deploy environment rather than
  a developer's shell, and is where secret handling has to be got right.
- Kubernetes work is explicitly not started. Recording it here is what stops it being
  quietly assumed into designs before its gate is met.
- The one-session-per-login rule becomes an operating constraint at stage 2: an unattended
  account is a **dedicated** account, and nobody browses the IB portal while it runs.
