# 4. Sync with upstream on demand, reviewed quarterly

- **Status:** Superseded by [ADR-0010](0010-the-repository-is-ours.md)
- **Date:** 2026-09-01
- **Deciders:** Project owner

## Context

Upstream is actively developed: 15 commits landed against our merge base within a day,
and one of them rewrote 224 lines of a file this fork changes. Tracking `develop`
continuously would mean debugging our own work and someone else's refactor at the same
time, on two moving bases.

Never syncing is the other failure. The delta does not shrink on its own; it ages, and the
cost of adopting an upstream improvement grows with the distance.

## Decision

**Upstream is a source we draw from deliberately, not a branch we track.**

- Do not merge or rebase onto `upstream/develop` unless the sync is the task.
- `git fetch upstream` is always safe: it only refreshes the snapshot the delta report
  reads, and changes nothing.
- **Review quarterly** whether to sync, starting from 2026-09-01. Quarterly is a review
  cadence, not a commitment to merge: the review may conclude that nothing upstream is
  worth the disruption this quarter, and that is a valid outcome to record.
- Sync out of cycle when there is a reason: a security fix, or an upstream change we
  actually want.
- **A sync is a scheduled piece of work with its own branch, not a step inside another
  task.** It ends with the full suite green and the delta register re-verified.

Between syncs the delta report's conflict line is a **forecast**, not a work item. It
appearing is not a reason to act.

## Consequences

- The fork is deliberately behind, and that is a chosen position rather than neglect.
  `upstream_delta` prints the age of the local snapshot so a stale conflict verdict is
  never mistaken for a current one.
- Each sync is larger than a continuous merge would be, and is budgeted as such.
- Contributing our fixes upstream would retire register entries rather than carry them,
  and remains the cheapest way to shrink the delta. It is **deferred**, not rejected: a
  pull request opens a review front on someone else's schedule, which is what this
  decision exists to avoid while the system is being built.
- The review is where that deferral gets revisited.
