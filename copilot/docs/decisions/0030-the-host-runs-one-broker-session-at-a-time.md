# 30. The paper host runs one broker session at a time, and tells someone when a unit dies

- **Status:** Accepted
- **Date:** 2026-09-11
- **Deciders:** Project owner (audit corrections approved 2026-09-11); mechanics proposed in-session

## Context

[ADR-0022](0022-the-always-on-host-is-a-dedicated-vm.md) put the paper campaign on one VM, and
`copilot/ops/` runs it from systemd timers: three `day` phases, five shakedown phases, each a
one-shot process. The audit of 2026-09-11 ([`AUDIT_2026-09-11.md`](../AUDIT_2026-09-11.md))
found that the package assumed each unit ran alone, on time, and exited through its own code:

- **Two units could overlap on the broker.** The 10:30 sweep's census retries run about ten
  minutes and more when something is working; the 10:45 order window rests a probe order inside
  that time (F6). Nothing serialised them.
- **Two commands shared client ids** - the basket and the failure-injection probe, the
  order-type matrix and strand recovery - kept apart only by the timers' spacing (F12).
- **A unit that died for a reason its code did not foresee told nobody** (F11): a traceback, an
  out-of-memory kill, the start timeout.
- **The unacknowledged-CRITICAL consumer ran only inside `day` phases**, three times a weekday,
  against a one-hour deadline (F4).
- **`kill` could not stop a node already running**, because the latch was read at build (F7).

## Decision

**One broker session at a time on the host.** Every `day` and shakedown unit runs under
`flock` on one lock in the user's runtime directory, waiting up to thirty minutes. A phase
that waits past its window is refused by its own `--scheduled` check, so waiting never runs a
phase at the wrong hour. Each unit first waits up to five minutes for the Gateway's port and
goes on either way, so a reboot's slow login is waited for and the phase's own preflight still
decides.

- **Every order-capable command takes its client ids from one table**
  (`copilot/live/client_ids.py`), held unique by a test; each census in a sweep has its own pair.
- **Every unit reports its own death.** `OnFailure=` starts `copilot-unit-failed@`, which sends
  `CRITICAL` for a unit that sweeps or places orders - an order may be working - and `WARNING`
  otherwise. Start timeouts are sized from the sweep's own deadlines plus the lock's wait, and a
  test holds them there.
- **The acknowledgement check has its own timer**, every fifteen minutes, every day, and it
  keeps running while the phase timers are paused.
- **A node that may place orders re-reads the latch while it runs**, every five seconds, and
  halts its engine when the latch appears. It never releases; resuming is a new process.
- **Locks on the host's shared state**: the latch's check-and-write, the receipts log's
  settle, and the alert flood guard's memory, which now lives on disk because every sender is a
  one-shot process (F10, F13).

## Consequences

- **A slow phase delays the next one rather than colliding with it.** The order window may
  start a few minutes late on a day the sweep found something, which is the day it must.
- **Pausing the schedule is `systemctl --user stop copilot-day-*.timer copilot-shakedown-*.timer`**,
  not `copilot-*`: stopping the acknowledgement timer stops the kill switch's consumer.
- **Hand runs take no lock.** The lock serialises timers; an operator running a command while a
  timer fires is running two sessions, as before.
- **Revisit trigger:** a second host on the same account ([ADR-0027](0027-the-kill-switch-is-a-host-latch.md)'s
  trigger too), or a move to Kubernetes, where a local `flock` stops being a lock.
