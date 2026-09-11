# 28. An undelivered critical alert halts the host, and the heartbeat carries it

- **Status:** Accepted
- **Date:** 2026-09-11
- **Deciders:** Project owner (audit corrections approved 2026-09-11); mechanics proposed in-session

## Context

[ADR-0023](0023-a-critical-alert-demands-acknowledgement.md) made a `CRITICAL` alert one that
demands acknowledgement, and [ADR-0027](0027-the-kill-switch-is-a-host-latch.md) gave the
unacknowledged one a consumer: the halt latch, engaged when a receipt expires unanswered.
Both assume the alert arrived. The audit of 2026-09-11
([`AUDIT_2026-09-11.md`](../AUDIT_2026-09-11.md), F3) found the case neither covers: a
`CRITICAL` that **could not be delivered** - Pushover unreachable, credentials wrong - was
printed to the journal and forgotten. It produced no receipt, so the acknowledgement check
never saw it; the sweep that sent it was marked as alerting for itself, so the day raised
nothing; and the morning's heartbeat still told the watcher the host was healthy.

That is the worst shape an alerting failure can take: the one condition severe enough to wake
the operator, on the one night the path to the operator is down, leaves every signal reading
normal.

## Decision

**A `CRITICAL` that cannot be delivered engages the halt latch at once**, with the trigger
`undelivered_critical`. Nobody can acknowledge an alert that never reached them, so ADR-0027's
condition - a critical alert unacknowledged by its deadline - is already met, and waiting out
a deadline that cannot be answered buys nothing.

- **Wired where alerters are built for sessions.** `alerter_from_environment` passes the
  escalation; an `Alerter` built by hand, such as the self-test's, carries none, so a drill
  cannot halt the host.
- **A box with no transport halts on its first `CRITICAL`.** That includes the dev box. An
  unconfigured notifier reports `delivered=False` by design (ADR-0023), and a `CRITICAL`
  nobody heard is the same fact wherever it happens.
- **While a latch no operator engaged holds, the morning withholds its heartbeat.** Both
  automatic triggers - expired and undelivered - mean Pushover has failed to reach anyone, and
  the watcher off the host is the one path that does not depend on it. A missed beat is how it
  learns. A latch the operator engaged is one somebody knows about, so the beat continues.
  An unreadable latch, whose trigger cannot be known, counts as automatic.
- **The acknowledgement check cannot stop the phase that runs it.** An unreachable transport
  leaves a receipt outstanding rather than raising; an unreadable receipt log is reported and
  settles nothing; a malformed alert setting falls back to the declared default and says so,
  on stderr and in the host check. The check runs first in every `day` phase, and one of those
  phases is the sweep.

## Consequences

- **The kill switch has three triggers**: the operator, an expired `CRITICAL`, and an
  undelivered one. Release is unchanged - the operator, retyping the id - and the recovery
  checklist's last item, *every CRITICAL since the halt is acknowledged*, now also means
  *delivered*.
- **The external watcher is load-bearing.** Without `COPILOT_HEARTBEAT_URL` the withheld beat
  reaches no one; the host check already advises it, and unattended running should treat it as
  required.
- **A transient Pushover outage during a sweep that found an order halts the host until the
  operator releases it.** That is intended. The alternative is an order possibly working with
  nobody told, which is the case this system exists not to have.
- **Revisit trigger:** a second transport. With two independent paths a single undelivered
  send is not the same as nobody told, and escalation should wait for both to fail.
