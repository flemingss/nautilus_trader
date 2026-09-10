# 23. A critical alert demands acknowledgement, and severity is a contract

- **Status:** Accepted
- **Date:** 2026-09-09
- **Deciders:** Project owner; mechanics proposed in-session

## Context

[`playbook/OPERATIONS.md`](../playbook/OPERATIONS.md) makes alerting a gate rather than a
convenience. Unattended paper opens only *after alerts and recovery drills pass*. The
monitoring-end policy requires an alert on *any order whose status cannot be confirmed*.
Safe mode's second limb is *preserve state and alert*. The kill switch itself triggers on
*a critical alert unacknowledged by deadline while supervised*.

**Until now no code sent an alert anywhere.** `failure_injection` proved the system
notices a problem. Nothing proved a human was told, and the roadmap has carried the gap
since the campaign began.

The requirement is unusual in one respect that decides the whole design. The playbook does
not ask for notification, it asks for **acknowledgement**, and it makes the absence of an
acknowledgement a kill-switch condition. A transport that fires and forgets cannot satisfy
that: sent and seen are different facts, and only one of them is the one the policy needs.

Three failure modes were weighed, all of them observed in practice elsewhere:

1. **The silent notifier.** Alerting that is configured wrongly, swallows everything, and
   is indistinguishable from a quiet system. This is the worst outcome, because it lets
   the unattended gate pass on a system with no alerting at all.
2. **The alert that kills the run.** A notification service outage propagating an
   exception into the trading path, so the safety mechanism becomes the incident.
3. **The flood.** An unattended failure loop sending thousands of identical alerts,
   exhausting the monthly quota and teaching the operator that the app is noise. An
   ignored alert channel is worse than none, because it is trusted.

Pushover is the transport. The owner already runs it for their cluster, so there is no new
account, no new bill and no new thing to learn. It also happens to fit: its emergency
priority retries until acknowledged and issues a receipt that can be polled.

## Decision

**Three severities, with meanings that bind.** They describe what the alert is allowed to
do to a sleeping operator, not how bad the author feels about it.

| Severity   | Means                                             | Pushover priority         |
| ---------- | ------------------------------------------------- | ------------------------- |
| `INFO`     | Record it. Do not wake anyone.                    | `-1`, no sound            |
| `WARNING`  | Show it now. Do not demand a response.            | `1`, bypasses quiet hours |
| `CRITICAL` | Wake the operator and keep asking until answered. | `2`, emergency            |

**`CRITICAL` is delivered at emergency priority and returns a receipt.** The declared
acknowledgement deadline is one hour, retrying every two minutes. The playbook requires
the deadline to be declared rather than assumed, and this is that declaration;
`COPILOT_ALERT_EXPIRE_SECONDS` overrides it. A configuration implying more retries than
Pushover honours is refused at construction rather than silently capped, because a
deadline that will not be met is worse than one that is obviously wrong.

**An unconfigured system is loud, not silent.** With no credentials the fallback writes
every alert to stderr and reports `delivered=False`. Nothing ever reports a delivery that
did not happen.

**Alerting never raises into its caller.** Every failure becomes a `Delivery` carrying
`delivered=False` and a reason. A notification outage must not take a trading session
down, and must not be mistaken for success either.

**Repeats are collapsed, except `CRITICAL`.** Identical severity and title inside a window
are suppressed and counted, and the count rides the next one that gets through, so nothing
is lost quietly. `CRITICAL` is exempt: its whole purpose is to keep asking until a human
answers, and muting it would defeat the acknowledgement the kill switch depends on.

**The transport sits behind a protocol.** Pushover is the first implementation, not the
contract.

## Consequences

- *Acknowledge critical alerts within the deadline* stops being a procedure and becomes a
  fact the code can read. `receipt_status` answers it.
- **The kill switch's *critical alert unacknowledged by deadline* trigger now has an
  input, and still has no consumer.** Wiring it needs the operator kill command, which
  does not exist; both are roadmap rows. This ADR deliberately does not invent the kill
  switch as a side effect of building alerting.
- The unattended-paper gate acquires evidence rather than an assertion:
  `python -m copilot.live.alerting --send-test` exits non-zero on a box where alerting
  does not work.
- **Choosing `CRITICAL` has teeth.** It wakes a human at three in the morning and can
  contribute to halting the system. A severity chosen carelessly is not a style problem.
- Two new environment names, `PUSHOVER_TOKEN` and `PUSHOVER_USER_KEY`, named in
  `copilot/paths.py` and read only at the CLI boundary, so nothing below can reach for
  them. They are **not** yet in the export list the day command refuses without; adding
  them belongs to the wiring row, because a day that refuses to run without alerting is a
  policy decision and not a side effect of this one.
- **Revisit trigger:** if the alert path must reach more than one person, if Pushover's
  free quota becomes a constraint, or if a delivery failure is ever observed that the
  receipt could not explain, reopen the transport choice. The protocol boundary is what
  makes that a small change.
