# Inherited-code register

Every file outside `copilot/` that we have changed, why, and what it would take to drop the
change.

**The question this answers changed on 2026-09-01.** It used to be *"what do we owe at the
next sync"*. Since [ADR-0010](decisions/0010-the-repository-is-ours.md) it is **"what do we
own and have to test ourselves"** - because this repository is no longer a fork, nothing here
is going upstream, and upstream's CI is not covering any of it.

**This file is enforced.** `copilot/tests/test_upstream_delta.py` compares it against the
real diff and fails when a modified upstream file is missing a row. Adding an entry is
part of making the change, not paperwork afterwards.

```bash
python -m copilot.tools.upstream_delta --fetch     # report, refreshing upstream first
python -m copilot.tools.upstream_delta --check     # exit 1 on an unregistered file
```

## We do not sync

There is no merge, on any cadence. Upstream is a source we **read and harvest from**, never a
base we merge onto ([ADR-0010](decisions/0010-the-repository-is-ours.md)). `git fetch upstream`
stays useful for diffing when deciding whether a particular upstream fix is worth taking; the
push URL is disabled and nothing is ever pushed there.

So the row count is no longer a bill that comes due. It is an **inventory of what we maintain**,
and it grows on purpose whenever a defect is worth fixing where it lives. The paper campaign
found three that are - see the roadmap.

The conflict forecast the report prints is now **advisory only**. It says what would happen in
a merge we are not going to do. Read it as a hint about how actively upstream is reworking
something we have opinions about, and nothing more.

## What a row obliges

Each row means we own that file's behaviour. Two things follow, and they are the point:

- **A regression test lives in this repository.** Upstream's suite is not ours and does not
  run for us. A test that fails without the change is worth more than one that merely passes
  with it, so write it that way round and check.
- **The reasoning is written down here**, because the person who has to judge it later has no
  pull-request thread to read.

## Why a register instead of a rule

The overlay's original rule was that it changed **zero** upstream files, which made the
maintenance cost self-evidently nil and needed no tracking. That rule was relaxed once, to
allow changes that were worth it, and the register was how the cost stayed visible. It has
outlived the fork posture that motivated it and is kept for the better reason above.

## Working rules

1. **Register the file in the same commit that changes it.** The test enforces this.
2. **Prefer a change upstream would accept anyway.** A fix that could be contributed back
   is a delta with an expiry date; a fork-only behaviour change is a permanent bill.
3. **Keep each change to the smallest surface that works.** Fewer touched lines is fewer
   lines to re-apply, and a rename or a drive-by tidy costs the same at sync as a fix but
   buys nothing.
4. **Never edit a generated artifact.** Change its source and regenerate.
5. **Off limits regardless:** `RELEASES.md`, `.github/workflows/`, `.github/actions/`, and
   the root `ROADMAP.md` - all maintainer-owned upstream.
6. **`origin` is the fork.** The `upstream` remote is fetch-only, with its push URL set to
   `DISABLED-never-push-upstream`. Never push to, or open anything on, `nautechsystems/*`.
7. **Pin `gh` to the fork.** `gh` resolves its target repository from the remotes, so
   adding `upstream` silently makes `gh pr create` aim at the upstream project. Run
   `gh repo set-default flemingss/nautilus_trader`, verify with `--view`, and re-run it
   after a fresh clone - it lives in `.git/config` and is not shared.

## Reading the risk column

Assigned by the tool, from how much upstream has moved the same file since our merge base.

| Risk       | Meaning                                                                                                                        |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `quiet`    | Upstream has not touched the file since the base. Nearly free to carry.                                                        |
| `touched`  | Upstream is also changing it. Expect to re-read our hunks at sync.                                                             |
| `churning` | Upstream is rewriting it far faster than we are. Our change is sitting in moving code; consider upstreaming it or dropping it. |

## The register

| Path                                                           | Change                                                                                                                             | Why                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | Cost to drop                                                                                                                                                                                                                                                                                                 |
| -------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `crates/risk/src/python/engine.rs`                             | New file: `PyRiskEngine`, a Python handle to the shared `Rc<RefCell<RiskEngine>>` exposing `set_trading_state` and `trading_state` | Nautilus enforces `TradingState` natively in the Rust risk engine but never exposed it to Python, so the account-wide breakers could only cancel and flatten after the fact - a strategy that kept submitting kept getting orders accepted. Mirrors the existing `PyCache` pattern exactly, including `unsendable`.                                                                                                                                                                                                                                                                                              | The risk guard degrades to reactive-only. **New file, so it cannot conflict** - the cheapest shape a delta can take. Genuinely upstreamable: it adds a capability without changing one.                                                                                                                      |
| `crates/risk/src/python/mod.rs`                                | Register `PyRiskEngine` on the `risk` pymodule                                                                                     | The class is unreachable from Python otherwise.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | Two additive lines beside the existing registrations.                                                                                                                                                                                                                                                        |
| `crates/live/src/python/node.rs`                               | Add a `risk_engine` getter and an `add_actor` binding to `PyLiveNode`                                                              | Two additions. The `risk_engine` getter is where the `TradingState` handle comes from, and mirrors the `cache` and `portfolio` getters line for line, including that it must be taken before a hosted run takes ownership of the node. `add_actor` closes an asymmetry: the Rust node has always accepted actors and `BacktestEngine` exposes the instance form to Python, but the live node exposed only `add_actor_from_config`, so an actor configured with runtime state could be backtested and not deployed. Found when `calibration/spread_snapshot.py` raised `AttributeError` before recording a quote. | 239 added lines including three tests, no existing line touched. **Upstream is churning this file** (+166/-88 since our base), so expect to re-read it at sync. Both are additive capability upstream would plausibly accept.                                                                                |
| `crates/live/Cargo.toml`                                       | Add `nautilus-risk/python` to the `python` feature                                                                                 | Without it the live crate cannot see `nautilus_risk::python` and the build fails.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | One line in a list, beside `nautilus-portfolio/python`.                                                                                                                                                                                                                                                      |
| `python/nautilus_trader/live/__init__.pyi`                     | Generated stub                                                                                                                     | Regenerated by `make build-debug` for the new getter. **Not hand-edited** - change the Rust and rebuild.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | Drops automatically once the source change goes.                                                                                                                                                                                                                                                             |
| `python/nautilus_trader/risk/__init__.pyi`                     | Generated stub                                                                                                                     | Regenerated by `make build-debug` for `RiskEngine`. **Not hand-edited.**                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | As above.                                                                                                                                                                                                                                                                                                    |
| `python/pyproject.toml`                                        | Add `copilot/**` entries to `[tool.ruff.lint.per-file-ignores]`                                                                    | The repository's pre-commit runs `ruff --config python/pyproject.toml` over **every** Python file, `copilot/` included. A scoped `copilot/ruff.toml` therefore never governed these files: `make pre-commit` reported 728 errors on them, mostly upstream's own `tests/**` exemptions failing to match `copilot/tests/**`. Relocating the ignores to the config that actually applies fixed that and let `copilot/ruff.toml` be deleted.                                                                                                                                                                         | `make pre-commit` fails on the overlay. Purely additive - new table keys, no existing line touched - so it is the lowest-conflict shape available. Not upstreamable: the paths do not exist there.                                                                                                           |
| `.typos.toml`                                                  | Allowlist `CPY` in `[default.extend-words]`                                                                                        | `CPY001` is a real Ruff rule id (flake8-copyright), which the overlay's lint documentation has to name. The typos checker reads it as a misspelling of `COPY`/`CPU` and fails the hook.                                                                                                                                                                                                                                                                                                                                                                                                                          | One line. Overlay docs cannot name the rule they exempt. Purely additive and genuinely upstreamable - upstream uses Ruff too.                                                                                                                                                                                |
| `crates/adapters/interactive_brokers/src/historical/client.rs` | Bound the tick-stream drain by the caller's `timeout`, at two sites                                                                | `tokio::time::timeout` wrapped only the request that opens the subscription, not the loop draining it, so `request_ticks` hung forever on a tick type an instrument does not have. Verified live: 97 s hang became a clean 22.0 s return, with `BID_ASK` unchanged at 1022 ticks in 0.4 s.                                                                                                                                                                                                                                                                                                                       | Any backfill requesting an absent tick type hangs the job. **Upstream would likely accept this** - it is a straight bug fix with no behaviour change on the working path.                                                                                                                                    |
| `crates/adapters/interactive_brokers/src/data/core.rs`         | Key the subscriptions map by `(InstrumentId, SubscriptionType)` rather than `InstrumentId`                                         | The value already carried a `subscription_type`, so the map could hold only one subscription per instrument. Subscribing to trades on an instrument that already had quotes silently evicted the quote entry, leaving that task running untracked, and a later `unsubscribe_quotes` cancelled the *trades* stream instead.                                                                                                                                                                                                                                                                                       | Multi-stream subscriptions on one instrument are silently lossy. **Upstream would likely accept this** too.                                                                                                                                                                                                  |
| `crates/risk/src/engine/mod.rs`                                | Apply `max_notional_per_order` when no account resolves, and warn instead of debug when it does not                                | `check_orders_risk_for_account` resolves the account with `account_for_venue(instrument.venue)` and, on failure, returned `true` for every order - skipping the balance, margin **and** notional checks. On IB the lookup always fails: instruments resolve on `SMART`, the account is `IB-DUT067974` on venue `IB`. Measured at paper stage six: an order for USD 1,580 against a configured USD 1,000 cap was accepted, and the only trace was a `DEBUG` line. A per-order cap is a bound on the order, not on the account, so it now applies either way.                                                      | Nautilus's per-order notional cap does nothing on IB, leaving `TradingState::HALTED` as the only pre-trade control. Additive: one new private helper plus a call inside the existing early-return branch. **Genuinely upstreamable** - it is a straight bug fix affecting any adapter with this venue split. |
| `crates/risk/tests/risk_engine.rs`                             | Two tests: the cap denies with no account, and an order inside the cap still passes                                                | The fix above is worthless without a test that fails without it. Verified by stashing the fix: `test_max_notional_applies_when_no_account_resolves` fails, the companion still passes, so the pair pins the behaviour rather than the implementation.                                                                                                                                                                                                                                                                                                                                                            | The fix becomes unprotected. Additive test functions only.                                                                                                                                                                                                                                                   |
| `crates/execution/src/reconciliation/orders.rs`                | Adopt an external order reported as `Submitted`, alongside `Accepted` and `Triggered`                                              | The status match dropped everything else with a warning and no events, so a venue holding a working order we did not place produced **no cache entry** - it existed at the venue and nowhere else, and nothing could query, cancel or reconcile it. Measured against IB, where an order left by a previous run was reported on every reconnect and could only be cancelled by hand. Projecting it as accepted is mildly optimistic; that is the right direction to err, because an order we can see and try to cancel is recoverable and an invisible working order is not.                                      | The unknown-working-order recovery path disappears again. One arm of a match. **Genuinely upstreamable** - it affects any venue reporting that status.                                                                                                                                                       |
| `crates/execution/tests/exec_engine.rs`                        | One test: a `Submitted` external order produces an acceptance                                                                      | Verified by stashing the fix - it fails without it.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | The fix becomes unprotected. Additive test function only.                                                                                                                                                                                                                                                    |
| `AGENTS.md`                                                    | Rewritten for ownership rather than contribution                                                                                   | The inherited file carried rules for people submitting patches to nautechsystems: minimal deltas, hands off `RELEASES.md` and `.github`, contribution etiquette. None applied once the repository was detached from the fork network, and all of it made fixing a defect in inherited code feel like a transgression needing justification - which is why the stage-three reconciliation gap was filed rather than fixed. See [ADR-0010](decisions/0010-the-repository-is-ours.md).                                                                                                                              | None. It is our instruction file. Registered because the register's job is to list what we own, and this is the file that says so.                                                                                                                                                                           |

## Known state at the last sync check

Recorded 2026-09-01 against `upstream/develop`, 15 commits ahead of our merge base
`7df531101a` (2026-08-31).

- `historical/client.rs` - **quiet.** Upstream has not touched it. Free to carry.
- `data/core.rs` - **churning.** Upstream changed +160/-64 against our +16/-12, across
  two commits: `05eae9fa43` "Replace Rust standard locks with parking_lot" and
  `4c18691277` "Standardize adapter task lifecycles".

A merge today conflicts in **one hunk**, the `subscriptions` field declaration: upstream
dropped the doc comments while we changed the key type. Resolution is to keep our tuple
key and take upstream's surrounding form - mechanical, but it must be done by hand and
re-verified, because the surrounding locking model changed underneath it.

**That is the whole argument for this register in one example.** The delta is two files
and 52 lines; the file upstream is rewriting is the one that already conflicts, one merge
in.

**No action is due.** Syncing is on demand only and the fork is deliberately held still
during development, so this conflict is a forecast for whenever a sync is actually chosen

- not work waiting to be done. Upstreaming both fixes would retire the entry instead of
carrying it, and it stays the cheapest option available, but an upstream pull request
opens a review front on someone else's schedule. Deferred on the same reasoning.
