# 27. The kill switch is a latch on the host, read by every node at build

- **Status:** Accepted
- **Date:** 2026-09-10
- **Deciders:** Project owner (prep work approved 2026-09-10); mechanics proposed in-session

## Context

[`playbook/OPERATIONS.md`](../playbook/OPERATIONS.md) makes the kill switch *independent of
the strategy*, with a safe mode of five limbs, and
[ADR-0023](0023-a-critical-alert-demands-acknowledgement.md) made an unacknowledged
`CRITICAL` alert one of its triggers while leaving that trigger without a consumer. The
paper VM stands up on 2026-09-15 to run unattended, and unattended running needs both.

The mechanism was already proven: `TradingState.HALTED` in the risk engine denies every
new order, measured in paper stages one and six ([`PAPER_CAMPAIGN.md`](../PAPER_CAMPAIGN.md)). **But it lives in one
node and dies with the process**, and in this overlay every `copilot.live.day` step is its
own process. A halt set inside the evening's basket is gone by the sweep, and gone again by
the next evening. A kill switch that is a state inside a node is not a switch.

## Decision

**The switch is a file, `~/.nautilus_copilot/HALT.json`, and every node reads it at build.**
`build_paper_node` starts any order-capable node `HALTED` while it exists, whatever its
session requested. Nothing that builds a node can forget to check, because the check is in
the one function they all call.

- **Per host, not per account.** A switch that halted one account while another traded on
  the same machine is a switch whose scope the operator has to remember at the worst moment.
- **A session that only cancels is exempt.** Cancelling is what safe mode does, and cancel
  commands do not pass through the risk engine in this build (`crates/risk/src/engine`
  handles submit, modify and account queries only), so halting the sweep would protect
  nothing and forbid the one action wanted.
- **An unreadable latch is engaged.** The file existing is the signal. A torn write must
  never read as "not halted", which is the one reading that lets an order through.
- **The first reason stands.** A second trigger does not overwrite an engaged latch; the
  recovery checklist has to answer why it started.
- **Release is the operator's, by retyping the latch's id**, the same deliberate-friction
  pattern as spending a holdout. The release is filed under `live/out/`.
- **An expired, unacknowledged `CRITICAL` engages it.** The alerter records every
  `CRITICAL` receipt on disk; `copilot.live.day` settles outstanding receipts before every
  phase, including scheduled runs that have nothing else to do, and one that expired
  unanswered engages the latch.
- **Nothing flattens.** `python -m copilot.live.kill` engages the latch, alerts, runs the
  broker-confirmed sweep and prints the recovery checklist. Positions are the checklist's
  first question, answered against the broker, never an automatic action taken while broker
  truth is uncertain.

## Consequences

- **A halt now survives the process boundary** that made the risk engine's state useless as
  a switch, including the daily Gateway restart on the VM.
- **Every probe and shakedown that places an order is denied while the latch is engaged.**
  That is correct and will look like failures in a shakedown run during a halt; the day's
  banner and the reminder alert say why.
- **The latch is only as good as the rule that every order-capable node is built by
  `build_paper_node`.** `calibration/spread_snapshot.py` and
  `probes/subscription_interference.py` build their own nodes, safely, because neither has
  an execution client. A future builder that adds one would bypass the switch.
- **Drilled 2026-09-10 against paper TWS.** Engaged through the command: the cancels-only
  sweep ran under the latch to `BROKER CLEAR`. Nodes built while latched read `HALTED` when
  they requested orders and `ACTIVE` when cancels only, from the real risk engine; after
  release the order-requesting node read `ACTIVE`. A wrong release id was refused.
- **Revisit trigger:** a second host trading the same account, or a move to Kubernetes
  ([ADR-0006](0006-ops-progression.md)'s third stage), where a local file stops being
  something every trading process can see. The latch then needs a shared store.
