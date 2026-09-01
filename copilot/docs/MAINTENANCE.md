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

**Review quarterly**, starting 2026-09-01. Out of cycle for a security fix or an upstream
change we actually want.

A review is not a commitment to merge. Concluding "nothing upstream is worth the
disruption this quarter" is a valid outcome, and recording it is the point - an unrecorded
skip is indistinguishable from forgetting.

| Review | Due        | Outcome |
| ------ | ---------- | ------- |
| Q1     | 2026-12-01 | -       |

Between reviews, the delta report's conflict line is a **forecast**. It appearing is not a
reason to act.

## When the delta stops being maintainable

Raised 2026-09-01, before it became a problem, so the trigger is written down rather than
argued about later.

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

## Quarterly review procedure

Roughly an hour when nothing has moved; budget a day when something has.

**1. Refresh the picture.** Nothing here changes any tracked file.

```bash
git fetch upstream develop
python -m copilot.tools.upstream_delta        # delta, upstream activity, conflict forecast
```

**2. Read what upstream did to files we carry.** Not the whole changelog - only the
overlap, which is the only part that can cost us.

```bash
BASE=$(git merge-base HEAD upstream/develop)
git log --oneline $BASE..upstream/develop -- <each registered path>
```

**3. Decide, and write the decision down.** One of three, recorded in the table above:

- **Skip.** Nothing wanted, nothing forced. Note why.
- **Sync.** Something is wanted, or the delta is drifting far enough that waiting costs
  more than acting.
- **Retire a delta instead.** If a registered change is a straight bug fix upstream would
  accept, contributing it back removes the row permanently. This is the only action that
  makes the fork *cheaper* rather than merely current, and the review is where it gets
  reconsidered - it is deferred by default, not rejected.

**4. If syncing, it gets its own branch and its own PR.** Never a step inside another
task.

## Sync procedure

```bash
git checkout -b sync-upstream-YYYY-QN
git merge upstream/develop
```

Then, in this order:

1. **Resolve conflicts against the register.** `UPSTREAM_DELTA.md` says what each of our
   changes was for. Re-apply the *intent* over upstream's new version rather than
   restoring our old lines - the surrounding code may have changed underneath, which is
   exactly what happened when upstream moved the IB adapter to `parking_lot` beneath our
   subscription re-keying.
2. **Rebuild.** `make build-debug`. The delta means a stale build hides breakage.
3. **Re-verify the delta.** `python -m copilot.tools.upstream_delta --check`. A change we
   dropped during conflict resolution shows up as a stale register row.
4. **Full suite.** `pytest copilot/tests/ -q`, `cargo clippy` and `cargo +nightly fmt` on
   every crate we touch, `prek run --all-files`.
5. **Re-run one gate verdict** against a known activation and compare. An engine change
   that alters fills will not fail a unit test but will move a verdict, and that is
   exactly what must not pass unnoticed.
6. **Update the review table** with what was taken and what it cost.

A sync is done when the suite is green **and** the register is accurate. Not before.

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
