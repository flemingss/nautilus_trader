# 22. The always-on host is a dedicated VM, not the production cluster

- **Status:** Accepted
- **Date:** 2026-09-09
- **Deciders:** Project owner; shape proposed by the owner, alternatives argued in-session

## Context

[ADR-0006](0006-ops-progression.md) set the progression - WSL and TWS, then Gateway, then
Kubernetes - without saying when the middle step arrives or what it looks like. The
question became live earlier than expected for a reason that has nothing to do with
strategy: **the paper campaign's system clock needs eight weeks of an always-on host**,
and it is the longest-lead item in the project. Every week the host question stays open
is a week that clock does not run.

The development box cannot be that host. It is a WSL2 instance on a workstation that
reboots, reassigns its NAT address on every restart, needs its TWS Trusted IP re-added
each time, and locked the whole machine up twice on 2026-09-09 under an ordinary Rust
build. None of that is a defect to fix. It is what a development machine is.

Three shapes were considered.

**Into the owner's existing production cluster.** Rejected by the owner: *"making this
project fit as is into my prod set up would not do well at this stage."* The reasoning is
sound and worth recording, because it will be tempting again later. This system is
pre-candidate, changes daily, and holds a broker connection that must not be disturbed by
an unrelated deploy. Putting it on shared infrastructure couples its blast radius to
everything else running there, and couples its change cadence to that cluster's.

**Defer the whole question until a candidate is frozen.** This was the in-session
recommendation and the owner improved on it. Deferring keeps the system clock stopped,
which is the one cost the project cannot buy back later.

**A dedicated always-on VM.** The owner's proposal, and the one adopted.

Two constraints shape any topology and are not negotiable. **IB permits one live session
per login**, which is not a technical limit to engineer around: it is why the first
connection from the second machine on 2026-09-09 refused every quote with IB 10197, and
why market-data sharing binds one paper account to one live user. And **IB Gateway needs a
daily restart** against IB's own nightly server reset, so restart and reconciliation are
the normal case rather than the exception.

## Decision

**A dedicated virtual machine, owned by this project alone, is the always-on host.** It
runs IB Gateway and the overlay. It is not the production cluster and does not share
infrastructure with it.

- **Containers are permitted on that VM where they earn their keep, and are not
  required.** The VM is the unit of isolation. Containerising inside it is an
  implementation choice to be made per component, not a prerequisite.
- **Kubernetes stays late-stage** and is gated on evidence rather than on a date: the
  system running unattended through a full paper campaign with no unresolved critical
  incident. ADR-0006's third step is unchanged, only its trigger is now written down.
- **Exactly one host streams market data at a time.** The VM is that host once it exists.
  The development box connects for development, and not while the VM is live. This
  follows from IB's one-session rule, not from a preference.
- **The VM is stood up when the alerting path exists**, and not before. An always-on
  system nobody is told about when it breaks is precisely what
  [`playbook/OPERATIONS.md`](../playbook/OPERATIONS.md) forbids, and the unattended gate
  reads *unattended paper only after alerts and recovery drills pass*.

## Consequences

- The dev box stops being the trading host. Its reboots, its NAT churn and its build load
  stop being operational risks, and `wsl-tws-networking` becomes a development note rather
  than a production procedure.
- **Gateway's daily restart and IB's nightly reset become first-class test cases**, not
  edge cases. The recovery work already done - order adoption, the cancel-path identity
  routing, `strand_recovery` - is the foundation this needs, and the drills now have a
  host to run on continuously.
- The `IB_V2_HOST` default in `copilot/live/session.py` and its siblings becomes wrong for
  the deployed case. It is already overridden by environment on every machine; the VM
  makes that permanent rather than incidental.
- A second host wanting quotes is now a decision with a cost, not an accident. Two boxes
  cannot both stream.
- **Revisit trigger:** when the system has run unattended through a full paper campaign
  with no unresolved critical incident, reopen the Kubernetes step. Reopen sooner if a
  second strategy needs isolation from the first, or if the VM's single-host constraint
  starts costing more than the isolation buys.
