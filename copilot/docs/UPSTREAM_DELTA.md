# Upstream delta register

Every file outside `copilot/` that this fork changes, why it changes, and what it would
take to drop the change.

**This file is enforced.** `copilot/tests/test_upstream_delta.py` compares it against the
real diff and fails when a modified upstream file is missing a row. Adding an entry is
part of making the change, not paperwork afterwards.

```bash
python -m copilot.tools.upstream_delta --fetch     # report, refreshing upstream first
python -m copilot.tools.upstream_delta --check     # exit 1 on an unregistered file
```

## Syncing is on demand

The fork does not track `develop`. A merge is a deliberate, scheduled act, and while
development is active the fork is held still on purpose — chasing upstream mid-feature
means debugging our own work and someone else's refactor on two moving bases at once.

So this file is not a to-do list. It is the review list for whenever a sync *is* chosen,
and in the meantime the count of rows is the number that matters: each one is a change
that will have to be re-applied by hand some day.

## Why a register instead of a rule

The overlay's original rule was that it changed **zero** upstream files, which made the
maintenance cost self-evidently nil and needed no tracking. That rule is now relaxed:
upstream changes are allowed where they are worth it. The cost is therefore no longer
zero and no longer visible on its own, so it is written down instead.

The goal is not to minimise the delta at any price. It is to keep every entry
**deliberate, small, and individually justified**, so that a sync is a review of a short
list rather than an archaeology exercise.

## Working rules

1. **Register the file in the same commit that changes it.** The test enforces this.
2. **Prefer a change upstream would accept anyway.** A fix that could be contributed back
   is a delta with an expiry date; a fork-only behaviour change is a permanent bill.
3. **Keep each change to the smallest surface that works.** Fewer touched lines is fewer
   lines to re-apply, and a rename or a drive-by tidy costs the same at sync as a fix but
   buys nothing.
4. **Never edit a generated artifact.** Change its source and regenerate.
5. **Off limits regardless:** `RELEASES.md`, `.github/workflows/`, `.github/actions/`, and
   the root `ROADMAP.md` — all maintainer-owned upstream.
6. **`origin` is the fork.** The `upstream` remote is fetch-only, with its push URL set to
   `DISABLED-never-push-upstream`. Never push to, or open anything on, `nautechsystems/*`.
7. **Pin `gh` to the fork.** `gh` resolves its target repository from the remotes, so
   adding `upstream` silently makes `gh pr create` aim at the upstream project. Run
   `gh repo set-default flemingss/nautilus_trader`, verify with `--view`, and re-run it
   after a fresh clone — it lives in `.git/config` and is not shared.

## Reading the risk column

Assigned by the tool, from how much upstream has moved the same file since our merge base.

| Risk | Meaning |
| --- | --- |
| `quiet` | Upstream has not touched the file since the base. Nearly free to carry. |
| `touched` | Upstream is also changing it. Expect to re-read our hunks at sync. |
| `churning` | Upstream is rewriting it far faster than we are. Our change is sitting in moving code; consider upstreaming it or dropping it. |

## The register

| Path | Change | Why | Cost to drop |
| --- | --- | --- | --- |
| `crates/adapters/interactive_brokers/src/historical/client.rs` | Bound the tick-stream drain by the caller's `timeout`, at two sites | `tokio::time::timeout` wrapped only the request that opens the subscription, not the loop draining it, so `request_ticks` hung forever on a tick type an instrument does not have. Verified live: 97 s hang became a clean 22.0 s return, with `BID_ASK` unchanged at 1022 ticks in 0.4 s. | Any backfill requesting an absent tick type hangs the job. **Upstream would likely accept this** — it is a straight bug fix with no behaviour change on the working path. |
| `crates/adapters/interactive_brokers/src/data/core.rs` | Key the subscriptions map by `(InstrumentId, SubscriptionType)` rather than `InstrumentId` | The value already carried a `subscription_type`, so the map could hold only one subscription per instrument. Subscribing to trades on an instrument that already had quotes silently evicted the quote entry, leaving that task running untracked, and a later `unsubscribe_quotes` cancelled the *trades* stream instead. | Multi-stream subscriptions on one instrument are silently lossy. **Upstream would likely accept this** too. |

## Known state at the last sync check

Recorded 2026-09-01 against `upstream/develop`, 15 commits ahead of our merge base
`7df531101a` (2026-08-31).

- `historical/client.rs` — **quiet.** Upstream has not touched it. Free to carry.
- `data/core.rs` — **churning.** Upstream changed +160/-64 against our +16/-12, across
  two commits: `05eae9fa43` "Replace Rust standard locks with parking_lot" and
  `4c18691277` "Standardize adapter task lifecycles".

A merge today conflicts in **one hunk**, the `subscriptions` field declaration: upstream
dropped the doc comments while we changed the key type. Resolution is to keep our tuple
key and take upstream's surrounding form — mechanical, but it must be done by hand and
re-verified, because the surrounding locking model changed underneath it.

**That is the whole argument for this register in one example.** The delta is two files
and 52 lines; the file upstream is rewriting is the one that already conflicts, one merge
in.

**No action is due.** Syncing is on demand only and the fork is deliberately held still
during development, so this conflict is a forecast for whenever a sync is actually chosen
— not work waiting to be done. Upstreaming both fixes would retire the entry instead of
carrying it, and it stays the cheapest option available, but an upstream pull request
opens a review front on someone else's schedule. Deferred on the same reasoning.
