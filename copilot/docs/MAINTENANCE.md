# Maintenance plan

How this fork stays current with upstream without being dragged by it, and who owns what
in the runtime. The decisions behind this are
[ADR-0003](decisions/0003-registered-upstream-deltas.md),
[ADR-0004](decisions/0004-quarterly-upstream-sync.md) and
[ADR-0007](decisions/0007-self-sourced-images.md); this document is the procedure.

## The shape of the problem

We want upstream's improvements. We do not want upstream's schedule.

`nautechsystems/nautilus_trader` is actively developed - 15 commits landed against our
merge base within a day of forking, one of which rewrote 224 lines of a file we change.
Tracking `develop` would mean debugging our own work and someone else's refactor at once,
on two moving bases. Never syncing is the other failure: the delta ages, and the cost of
adopting an upstream improvement grows with the distance.

So: **draw from upstream deliberately, on our cadence, as a scheduled piece of work.**

## Cadence

**Superseded 2026-09-01 by [ADR-0010](decisions/0010-the-repository-is-ours.md).** There is
no sync cadence, because there are no syncs. The repository has been detached from the fork
network; upstream is a source we read and harvest from, never a base we merge.

What remains is **harvesting on demand**: when there is a specific upstream fix worth having,
take it deliberately, on its own branch, with its own tests here. `git fetch upstream` stays
useful for diffing to decide whether something is worth taking.

The quarterly review this section used to schedule is dropped. Its purpose was to keep the
delta from ageing into an unmergeable state, and that no longer applies to a repository that
will never merge.

## When the delta stops being maintainable

**Reached, and acted on, the day it was written.** These triggers were set on 2026-09-01 to
fire later; the paper campaign's findings answered them the same afternoon, and
[ADR-0010](decisions/0010-the-repository-is-ours.md) is the outcome they pointed at. Kept as
the reasoning behind that decision rather than as a live tripwire.

The delta is **10 upstream files** and the paper campaign has already named an eleventh
worth making - the reconciliation gap that leaves an external `SUBMITTED` order
uncancellable, which sits in `crates/execution` rather than in an adapter and so has a wider
blast radius than anything we hold today.

The reason the count grows is structural rather than accidental. NautilusTrader's integration
list is almost entirely crypto exchanges: one symbol namespace, no entitlements, no routing
layer, 24/7 sessions, one account model. Interactive Brokers is close to the only traditional
multi-asset brokerage in it, so the abstractions bend hardest exactly where we work - venue
identity, routing, entitlements, session state. **We should expect to keep finding things**,
and expect the fixes to be ours to make.

That is sustainable at ten files. It is not sustainable indefinitely. Two triggers, either of
which means the lifecycle model itself gets reconsidered rather than the next patch simply
being written:

- **The delta exceeds roughly twenty files**, or reaches into a third crate beyond the IB
  adapter and the risk engine.
- **A quarterly review costs more than a working week**, or concludes "too disruptive" twice
  running - at which point we are effectively maintaining a hard fork while paying the
  overhead of pretending otherwise.

The options at that point are the ones worth naming in advance: upstream the changes and
carry only what is rejected; pin to a release and stop tracking `develop` at all; or accept
the hard fork explicitly and drop the sync machinery. **Each is a defensible choice; drifting
into one is not.**

## Harvest procedure

Replaces the quarterly review and sync procedures this file used to carry, both of which
described merges that will not happen ([ADR-0010](decisions/0010-the-repository-is-ours.md)).

Taking a specific upstream fix:

1. `git fetch upstream` and read the change. **Read it, do not merge it** - a cherry-pick that
   applies cleanly is still someone else's reasoning arriving unreviewed.
2. Decide whether we want the behaviour, not whether the diff applies. Upstream's answer was
   shaped by its own priorities, and ours differ where they differ.
3. Take it on its own branch, by cherry-pick when the diff is clean or by reimplementation
   when it is not.
4. **Write a test here that fails without it.** Upstream's suite does not run for us, so an
   untested harvest is a change nobody checked.
5. Register the file in [`UPSTREAM_DELTA.md`](UPSTREAM_DELTA.md) if it is not already there,
   and update the row's reasoning if the change alters it.
6. Full suite green before merge, same as any other work.

The one thing worth watching upstream for is a **fix to something we already changed** - two
answers to the same problem, and a reason to check whether theirs is better than ours.

## What we own in the runtime

Accepting upstream deltas has a consequence beyond merges, and it is easy to miss:

```
published wheel  ->  LiveNode.risk_engine: False
source build     ->  LiveNode.risk_engine: True
```

**There is no path that consumes a published `nautilus_trader` wheel.** An image built on
one would run, connect, trade, and silently fall back to risk breakers that cannot stop the
next order.

So we source our own images ([ADR-0007](decisions/0007-self-sourced-images.md)), and with
that comes ownership of:

| Concern                 | Owner       | Note                                              |
| ----------------------- | ----------- | ------------------------------------------------- |
| Runtime image           | Us          | Built from our source, versioned by commit        |
| Base image currency     | Us          | Not upstream's cadence any more                   |
| CVE response            | Us          | Including transitive Rust and Python dependencies |
| `ib-gateway` image      | Third party | Pinned **by digest**, never by tag                |
| Rust toolchain in build | Us          | Pinned; a silent bump changes the artifact        |

This is the strongest argument for keeping the delta small, and the reason "retire a delta"
sits alongside "sync" as a first-class review outcome. Retiring the Rust deltas restores
the published-wheel path and hands the supply chain back.

## Invariants

These hold between reviews, not only during them.

- `python -m copilot.tools.upstream_delta --check` exits 0. A test enforces it, so an
  unregistered upstream change cannot survive a test run.
- `origin` is the fork. The `upstream` remote is fetch-only with its push URL disabled,
  and `gh` is pinned to the fork - **`gh` resolves its target repository from the remotes**,
  so merely adding `upstream` re-points `gh pr create` at the upstream project. Both guards
  live in `.git/config` and do **not** survive a fresh clone.
- Never push to, or open anything on, `nautechsystems/*`.
- Startup asserts `LiveNode.risk_engine` exists. A wheel-built runtime fails loudly at boot
  rather than quietly at the first breach.
