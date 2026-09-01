# 10. The repository is ours; upstream is a source we read

- **Status:** Accepted
- **Date:** 2026-09-01
- **Deciders:** Project owner
- **Supersedes:** [ADR-0004](0004-quarterly-upstream-sync.md)

## Context

ADR-0004 kept the repository posed as a fork: upstream was a base we would sync with
deliberately, and the delta register was the bill that would come due at the next sync. That
framing shaped how work was done. Changes to upstream files were weighed against "blast
radius at sync", which is why the reconciliation gap found at paper stage three was filed
rather than fixed.

The paper campaign changed what we know.

**The problems are not incidental.** Stages one to six found three separate failures caused
by one unmodelled distinction between a listing venue, an IB routing destination, and an
account's home venue: the account lookup at stage two, order routing at stage three, and at
stage six a configured `max_notional_per_order` that **silently does nothing**, leaving
`TradingState::HALTED` as the only working pre-trade control. Nautilus ships two.

**They are structural.** Upstream's integration list is roughly twenty crypto venues plus
Interactive Brokers. Crypto venues have one symbol namespace, no entitlements, no routing
layer, continuous sessions and one account model. The abstractions were shaped by that world
and bend where we work. The IB adapter is not neglected - 98 commits in six months, marked
stable, comparable in size to Bybit's - but the seams we live on are not where upstream's
attention is, and there is no reason to expect that to change.

**The fork posture was costing more than it saved.** `AGENTS.md` carried rules written for
people submitting patches to nautechsystems: minimal deltas, hands off `RELEASES.md` and
`.github`, contribution etiquette. None of it applied to us, and all of it made every fix in
an upstream file feel like a transgression to be justified.

## Decision

**We own this repository.** It has been detached from the fork network on GitHub. There is no
parent, no pull-request relationship, and no expectation that any change here goes anywhere
else.

- **Upstream is a source we read and harvest from, never a base we merge.** Take a specific
  fix when we want it, by cherry-pick or by reading and reimplementing. Do not merge or
  rebase onto `upstream/develop`, ever - not on a schedule and not on demand. The quarterly
  review of ADR-0004 is dropped.
- **`upstream` stays as a fetch remote** and its push URL stays disabled. Being able to diff
  against upstream is useful for deciding whether to harvest something; it just no longer
  drives anything.
- **Any file in this repository may be changed on its merits.** No file is off limits because
  of its provenance. Changes to code we inherited are held to the same standard as code we
  wrote, which for a trading system is a high one.
- **The delta register survives, with a different job.** It stops being "what we owe at the
  next sync" and becomes **"what we own and must test ourselves"** - which matters more after
  this decision, not less. `tools/upstream_delta.py` keeps reporting, and the conflict
  forecast becomes advisory only.

## Consequences

**What we gain.** Fixes happen where the defect is. The risk engine fix that makes
`max_notional_per_order` work, and the reconciliation fix that makes an unknown working order
recoverable, both become ordinary work rather than decisions about fork politics.

**What we accept.** We no longer receive upstream's fixes for free. Nautilus is a large
codebase we do not want to own the correctness of, and a bug fixed upstream in the matching
engine, cache or order model will not reach us unless someone notices and harvests it. That
is a real cost and it is the price of the decision, not an oversight.

**What follows.** `AGENTS.md` is rewritten for ownership rather than contribution.
`UPSTREAM_DELTA.md` is repurposed. `MAINTENANCE.md`'s quarterly cadence is replaced by
harvesting on demand; its "when the delta stops being maintainable" triggers are moot, since
this decision is the outcome those triggers pointed at, reached earlier and for better
reasons.

**What does not change.** Commit and pull-request conventions we chose for ourselves stay:
no Conventional Commits, no issue numbers in subjects, no AI attribution or co-author
trailers, no branded footers. Those were our preferences, not upstream's rules.
