# Changelog

Overlay-local. Upstream NautilusTrader releases are not tracked here.

## 2026-09-05, later (the decisions, and the day's last quick wins)

### Decided

- **All five owner decisions, in one sitting.** AAPL: `revise`, recorded in the holdout
  record with the reasoning. SCHX: the spend is void - it scored a series that did not
  exist - and one re-spend on the corrected series is allowed
  ([ADR-0021](decisions/0021-an-unscorable-spend-still-consumes-the-holdout.md)); the
  record moved to `holdouts/voided/`. An unscorable spend on a true series consumes the
  holdout (the same ADR). Marketstack stays until EODHD passes the probes that caught it.
  The consolidated data purchase is skipped. Roadmap: fifteen items, ten ready to build,
  nothing waiting on a decision.

### Built

- **Every session record carries a manifest**: the commit (marked dirty when it is), the
  newest verdict, the four input digests as of the session, and which of the verdict's
  digests moved. The playbook's *verify strategy ID, code commit, config hash and data
  hash*, printed per activation. Lives under `live/manifest.py` so it cannot move the
  code digest it reports.
- **`day sweep`**: monitoring end as its own phase, for the night orders are enabled.
- **`corporate_actions` scans to today by default.** The default ended 2025-12-31.

## 2026-09-05 (the day as two commands, and what the replay comparison found)

### Built

- **`live/day.py` owns the sequence.** `python -m copilot.live.day morning` runs append,
  the corporate-actions scan over the registry to today, `validate --changed --write`,
  and the replay comparison; `day evening` runs preflight, warm-up, the basket, and the
  sweep, printing the session's clock on both sides of the Pacific and naming an early
  close. The scan blocks the verdict, the preflight blocks the basket, and the sweep runs
  whatever happened before it. It checks what the day needs exported before it starts a
  step. Walked 2026-09-04 (JST 2026-09-05): morning 3m00s, evening 2m11s.
- **`cancel_working --all`** sweeps every registered instrument in one node: nine in
  40.3s where the walk had measured 41.5s *each*. Naming nothing is an error, not AAPL.
- **`preflight` checks a quote per instrument**: at least two, the newest within five
  minutes of arrival, a positive bid strictly below the ask. Passed 9/9 with the session
  open on a delayed feed; pre-market is unverified until Tuesday.
- **`live/compare.py`** recomputes every decision in a session record through the gate's
  own engine, over the same bars, warmed the same way, and compares field by field
  within the session's own recorded rounding. The playbook's *compare live decisions
  against offline replay*, as a morning step.
- **`strategies/promotion.py`**: a PAPER or LIVE activation runs only when its registry
  parameters equal the frozen set of a holdout the owner decided to `freeze`, key for
  key; a RESEARCH activation is labelled `seeded identity; min_gap_atr=0.25,
  target_1_atr=1.0 are strategy defaults, not the gate's selection` in the record and on
  the console. Nothing is frozen, so every session today is labelled.

### Found

- **The live ATR stood one bar behind the replay's.** The first comparison, over the
  record of 2026-09-04's basket, disagreed on eight activations of nine: the live path
  handed the decision bar to `on_bar`, and the engine updates registered indicators
  *before* `on_bar`. `GapReversalStrategy.decide` now does what the engine does; the
  next session compared 9/9. Nine sessions had run on the lagged value and nothing said
  so.
- **A triggered `next_close` decision goes nowhere live.** The deferral the rule sets
  dies with the process, and the next evening's warm-up never replays the bar. Recorded
  as `deferred_atr` so a trigger is visible; carrying it into its session is a roadmap
  row and a stage-seven question.
- **The scan's default window ended 2025-12-31**, so a 2026 split would have been missed
  by the morning command written to catch it. The day passes `--to` today.

## 2026-09-04, later (the shape, before it compounded)

### Reshaped

- **`live/probes/`** holds the six investigations that produced a finding -
  `controlled_order`, `order_types`, `supervised_session`, `subscription_interference`,
  `failure_injection`, `strand_recovery` - kept runnable because two of them are how the
  adapter fixes get re-verified. `live/` is now the operator's day and what it stands on.
- **`databento.py` split by question**: the client, its wire format and the pull stay;
  `databento_probe.py` is the intraday-fidelity probe and `databento_audit.py` the
  catalog audit. One CLI entry still dispatches to all of them.
- **One of each**: `catalog.read_series` replaces four readers of a stored series;
  `live/account.py` replaces two searches for the account, carrying the venue trap the
  first preflight fell into; `session.add_broker_arguments` replaces five flags worded
  three times. `validate`'s selection is a function, and tested.
- `MAINTENANCE.md` has a layout section, so the map has one place to be wrong.

## 2026-09-04 (a strategy on a broker connection; six symbols onboarded; the morning made cheap)

### Built

- **The live path warms from the catalog and runs a strategy on a broker connection.**
  `live/warmup.py` refuses a stale or holed window; `live/run_activation.py` hands the
  strategy its history and the last completed bar, orders denied. IB 2188 refuses daily
  bars on this account, so the catalog is the data client - which is what ADR-0017's
  two-ended window made possible.
- **The evaluation window is pinned at both ends** (ADR-0017), so one catalog serves
  frozen research and fresh execution. Proved by appending 169 sessions and every
  verdict staying bit-identical.
- **`data/append.py`** keeps the catalog current on a schedule; **`data/patch.py`** fills
  holes from the Databento store, taking the close from the auction statistic and the
  rest of the bar from the venue, because the listing venue's `ohlcv-1d` close runs 3.5
  to 11.4 bps from the official print and the `statistics` schema matches it exactly.
- **Six ETFs onboarded** (SCHX, TLT, GLDM, XLF, HYG, EEM) for $2.42 of Databento credit.
  Nine defects found by running the process rather than reading it; **`data/onboard.py`**
  is that process as a status report. SPLG turned out not to be absent from the vendor
  but stopped on 2026-07-17 - a narrow probe reads that as absence.
- **The holdout boundary is per activation** (ADR-0020). A 2017 series put the shared
  2022-01-01 pin at 44%; the pin moves into the registry as a date. SCHX's holdout was
  spent and **failed on four trades**; `spend_holdout` now projects the count first
  (AAPL: 110.7 projected, 111 actual) and refuses a window too short to score.
- **Sizing comes from the account.** `risk/budget.py` derives `R = A * r` and the 10%
  notional cap from the equity the broker reports; `risk/exposure.py` caps total open
  planned risk and daily entries across every strategy in the session, which now runs
  the whole basket in one node. The research R-unit of USD 1,000 had been reaching the
  live strategy verbatim.
- **`onboard` has a corporate-actions stage.** The evening walk found three splits sitting
  in the prices of two symbols onboarded that morning, one inside a spent holdout; the scan
  existed and the sequence had not included it. It blocks on anything sitting in the prices.
- **Every verdict carries the four digests it was computed from**, and
  `validate --changed` skips what cannot have moved: 12 recomputed in 2m54s, then 0 in
  half a second. `preflight` derives its instrument list from the registry (it had
  passed 3 of 9).

### Measured

- Spread repinned to 7.6 years of top-of-book in the execution window (ADR-0019). Nothing
  moved: the IB per-order minimum is 3-15x the whole spread charge at these sizes.
- Marketstack's 2026 rows carry 11 unreadable bars, substituted whole (ADR-0018); its
  ETF closes before 2017 are largely sub-penny consolidated values, and TLT's 2026 is
  unusable in 122 of 169 sessions.
- The AAPL next-close holdout passed thinly (+0.035 R over 111 trades); crossing equity
  moved to USD 25,000. `owner_decision` is unfilled in both spent records.

### Confirmed against the broker

- **The adopted-order cancel fix is confirmed against IB.** `live/strand_recovery.py`
  re-run twice on a rebuilt extension: the stranded order is adopted through
  reconciliation (`Created external order ... (PERM-868822148) [SUBMITTED]`), and the
  failure this fix was written for - `Instrument ID not found for pending cancel order` -
  **does not appear**. The adopted order now reaches `PENDING_CANCEL`, so the identity
  routed and `OrderPendingCancel` emitted. Evidence:
  `live/out/strand_recovery_20260903T08{2058,2836}Z.json`.
- **Off-hours turned out to be the stricter test.** IB reports a resting order as
  `PreSubmitted`, which the adapter maps to Nautilus `Submitted` - exactly the status the
  2026-09-01 adoption fix handles. During RTH the same order reports as IB `Submitted`
  and maps to `Accepted`, a path that already worked. The 2026-09-02 run tested the
  easier case.
- **A cancel takes effect from the originating client id, not a foreign one.** From
  client id 832 (which placed the order) the order left the broker; from 821/822 and
  841/842 it did not, within the probe's settle window. The acknowledgement never arrived
  inside that window either time, which is why the probe still records FAIL while the
  broker ends up clean - the sweep verifies by event, and IB does not send one to a
  client that did not place the order. **A recovery sweep should use the originating
  client id when it is known.** Broker confirmed empty afterwards by a third client id
  reporting no working orders.

### Fixed

- **An open-order update for an order this client never placed no longer logs at ERROR**
  (`execution/core_updates.rs`). Surfaced by the verification run itself: every connect
  logged `Trader ID not found for Interactive Brokers order 832000003` **before**
  reconciliation had run. TWS pushes `openOrder` for every working order on the account
  at connect, including a previous run's; those have no route and nothing to attribute an
  update to, and reconciliation adopts them moments later with identity it builds itself.
  A startup race reported as a failure, and the same `shutdown_on_error` landmine as the
  reduce-only noise. New `get_optional_order_actor_ids` distinguishes "no route,
  legitimately" from "route should exist", treating a half-built route as no route.
  One test; 420/420 in the crate; registered in the delta.
- **Confirmed by re-run: the rebuilt extension logs zero ERROR lines** across a full
  strand, recover and sweep cycle.

## 2026-09-03 (the startup errors that were not errors)

### Fixed

- **A reduce-only fill with no position to reduce is now classified by where it came
  from** (`crates/execution/src/engine/mod.rs`). Every startup whose reconciliation
  lookback held a completed round trip logged `ERROR Cannot open NETTING position ...
  from reduce-only fill` - a historical exit fill whose position closed long ago and is
  not in the cache. Nothing was wrong, and it is exactly the class of noise
  `shutdown_on_error=true` would have turned into a restart loop. The reconciliation case
  now logs `WARN` and says the condition is expected; the live case, where something
  really did try to reduce a position that is not there, stays `ERROR`. Reconciliation
  fills already carry `reconciliation: true` at all four constructors, so the two are
  distinguishable without inference, and **the rejection itself is unchanged** - a
  reduce-only fill still never opens a position.

### Testing

- One test in `exec_engine.rs` captures the engine's own log records and drives an orphan
  reduce-only fill twice, live and reconciled, asserting the level split, the "expected"
  wording, and that neither opened a position. **Verified to fail on the unfixed engine.**
  1,153 tests in `nautilus-execution` pass under nextest; clippy clean. Registered in the
  delta.

## 2026-09-03 (the adapter can route an order it did not place)

### Fixed

- **Cancel-all now routes an adopted order's identity before it cancels**
  (`crates/adapters/interactive_brokers/src/execution/`). The two cancel paths had
  diverged: single-order cancel called `cache_cancel_order_tracking` to put the order's
  instrument, trader and strategy into the adapter's ID maps before emitting, while
  cancel-all went straight from resolve to cancel to emit. Those maps are populated only
  when *this* client submits an order, so an order adopted from the venue by
  reconciliation - present in the cache, placed by an earlier run - had no route, and
  the emission died with "Instrument ID not found for pending cancel order". Cancel-all
  now carries each order's identity out of the cache (where an adopted order does exist)
  and routes it through a shared `cache_order_identity`, extracted behaviour-preservingly
  from the single-order helper.
- **A failed cancel is reported as `OrderCancelRejected` rather than only logged.** The
  swallowed failure is what left the strand recovery's sweep waiting on an event that
  was never coming.

### Testing

- Three tests in `core_tests.rs`: an adopted order routes and emits (carrying its own
  negative control - the unrouted emit is asserted to fail exactly as it did against the
  broker, with the real IB order ID from the 2026-09-02 run); a mapped IB order ID cannot
  be re-pointed to another client order; a failed cancel reports a rejection. 419/419 in
  the crate under nextest, clippy clean. Registered in `UPSTREAM_DELTA.md`, three rows.
- **Not yet verified against the broker.** A `live/strand_recovery.py` re-run is the
  confirmation, and needs TWS. The second half of the original finding - that IB may
  refuse a cancel for an order in another client ID's partition - is a broker rule this
  fix does not change; what changes is that the refusal now arrives as an event.

## 2026-09-03 (AI assistance is attributed, and the day is closed)

### Changed

- **The no-attribution convention is retired.** Root `AGENTS.md` and `copilot/AGENTS.md`
  now require the opposite: a `Co-Authored-By:` trailer naming the model on commits an
  agent wrote or co-wrote, and the tool's footer on pull requests it drafted. The
  inherited rule was written for a project with many human contributors; this is a
  one-person project that leans heavily on LLM agents, and the record should say who did
  what. Commits before this entry carry no trailer for that reason, not by omission.

### Recorded

- `MAINTENANCE.md` step 1 gains the two `gh`/push gotchas from this machine: `gh auth
  login` under `sudo` writes root's config and looks like a failed login, and WSL pushes
  need the repo-local credential helper pointed at Windows Git Credential Manager.

### State at close of day

- `develop` at the merge of #38. **The holdout is unspent**; ADR-0014 is accepted and
  the command is the owner's to run. Eleven open items in the roadmap, unchanged since
  the strand-recovery grooming. Broker order list confirmed clean by the owner after the
  stranded order was cancelled by hand. No background work in flight.

## 2026-09-03 (the holdout can be spent, and nothing about the spend is chosen)

### Decided

- **[ADR-0014](decisions/0014-the-holdout-is-spent-as-one-more-fold.md): the holdout is
  spent as one more fold.** Accepted 2026-09-03; no holdout has been spent yet.
  The single-use test is scored by the same evaluator as every walk-forward fold, with
  the whole development window minus the purge as training and the holdout as test, so
  its number was made by the code path it is compared against. Nothing is chosen at
  spend time; the record is the single-use marker; the diagnostic timing bound and a
  dirty tree are refused; the owner's reject/revise/freeze is a separate act recorded in
  a follow-up commit.

### Added

- **`validation/spend.py`** - `spend_holdout`, built on `evaluate_fold`, which was
  extracted from `walk_forward` behaviour-preservingly (the six recorded verdicts
  reproduce to six decimals afterwards). Holdout bars reach the replay exactly once,
  for scoring, after selection - pinned by a test that watches every replay window.
- **`strategies/spend_holdout.py`** - the command, with its four refusals and a record
  carrying the frozen parameters, the full selection audit, the holdout tearsheet, the
  commit, and `owner_decision: null`. `validate` now reports `holdout_spent: true` the
  moment a record exists under `strategies/holdouts/`. Eleven tests.

## 2026-09-02 (the stranded order comes back, and cannot be cancelled)

### Added

- **`live/strand_recovery.py`** - strands a one-share far-from-market GTC limit on
  purpose, then recovers it from a fresh node on different client ids. Stage three's
  accident, made deliberately and at the smallest size that asks the question. The record
  says which phase failed rather than collapsing three claims into one bit, and the
  recovery node runs with orders disabled - cancels bypass the risk engine, so a node
  that cannot submit can still sweep, and cannot make the situation worse.

### Measured

- **Adoption is confirmed at the broker.** A fresh node with an empty cache received the
  stranded external `SUBMITTED` order through reconciliation. The 2026-09-01 engine fix
  is now watched working against IB, not merely unit-tested - the half of
  `recover_unknown_working_order` that reconciliation owns is closed.

### Found

- **The adapter cannot cancel the order it just adopted, and fails silently.** The
  stranded order's IB id (`832000002`) belongs to the stranding client's id partition
  (`client_id % 1000`); the recovering client's adapter maps have no entry for it, and
  the sweep's cancel dies inside the adapter with "Instrument ID not found for pending
  cancel order" - **without raising any order event**, so nothing above the log line
  learns the cancel failed. The order was cancelled by hand in TWS. Net operational
  state: an unknown working order is now *visible* to a recovering node, which makes the
  manual step findable instead of a surprise, and the remaining fix is in the adapter's
  execution core, ours under ADR-0010. Tracked in the roadmap alongside a second
  surfaced item: reconciliation logs false `Cannot open NETTING position ... from
  reduce-only fill` ERROR lines for historical round trips in the lookback, which must be
  quieted before `shutdown_on_error` is ever considered.

## 2026-09-02 (entry timing becomes a bracket, and the bracket surprises)

### Decided

- **[ADR-0013](decisions/0013-entry-timing-is-evaluated-as-a-bracket.md): entry timing is
  evaluated as a bracket.** The plan to move the gap fade's entry to the next session's
  *open* died against the engine, measured five ways: an order submitted from ``on_bar``
  settles against the book the signal bar left (market and marketable limit both fill at
  the signal close), a deferred market order fills at the *next* session's close, a
  resting limit fills at its own price with no opening improvement, and the matching
  engine rejects ``AT_THE_OPEN`` outright. Next-open entry - and the charter's
  concession-bounded window - is not expressible on a daily-bar replay. So the premise
  runs at both bounds that are: ``signal_close`` (diagnostic only, never promotable) and
  ``next_close`` (charter-compliant, the only mode a holdout may be spent on). The
  entry-timing charter conflict closes, and the holdout spend is un-gated.

### Added

- **`entry_timing` on the gap fade** - identity, not a searchable axis (ADR-0005): it
  lives in an activation's `[parameters]`, never in `SEARCH_SPACE`. In ``next_close``
  mode the decision freezes at the signal bar (trigger and the ATR the levels are built
  from) and the entry submits on the following bar, consuming its action; a signal on a
  window's final bar simply never fills, as the charter's own rule would have it. An
  unknown mode raises rather than silently measuring the wrong bound. Five tests,
  including one verified to fail against a sabotaged (live-ATR) deferral.
- **Three `*-gap-fade-long-next-close` activations** - new experiments per the charter,
  not edits to the existing three, which keep their names and their history.

### Measured

- **All six activations majority-pass net, and the bounds did not order as assumed.**
  Deferring entry a full session raised AAPL (+0.0469 to **+0.1017 R**, 20/30) and SPY
  (+0.0498 to **+0.0530 R**, 19/31) and lowered only MSFT (+0.0895 to +0.0660 R, 17/31).
  The reversion this premise captures is therefore not concentrated in session t+1, and
  the fear that charter-compliant entry kills the edge is answered: it does not. The
  AAPL jump is to be read with suspicion, not excitement - the modes trade materially
  different populations (deferral blocks consecutive-gap re-entries), which is why
  ADR-0013 forbids cross-mode comparison. Verdicts filed for all six from one catalog
  and commit state.

## 2026-09-02 (rehydrating on a bare WSL box, and what it cost)

### Added

- **`MAINTENANCE.md` "Standing up a new machine" now survives a stock Ubuntu box.** The
  checklist was written from a machine that already worked; running it on a bare one
  found four gaps, each recorded with the derivation rather than the value:

  - **`make build-debug` fails linking `nautilus-pyo3`** with
    `rust-lld: error: unable to find library -lpython3.14`. Ubuntu ships
    `libpython3.N.so.1.0` but not the unversioned symlink `-lpython3.N` resolves against;
    that comes with `libpython3.N-dev`. Python's own config dir (`sysconfig` `LIBPL`)
    carries the symlink, so putting it on `LIBRARY_PATH` fixes the link with no sudo.
    Distinct from the existing `PYO3_PYTHON` note, which is about embedding the
    interpreter in Rust tests; this one stops the build outright.
  - **The two WSL addresses are now derived, not copied.** `IB_V2_HOST` is the default
    route's gateway; the TWS Trusted IPs entry is `eth0`. Both change on reboot, and the
    hardcoded `172.17.112.1` default belongs to a different machine. Recorded with it:
    a container appears to TWS as `127.0.0.1` and needs no Trusted IPs entry, while a
    native WSL process appears as the real `eth0` address and does - so a setup that
    worked from a container fails on first connect after moving to a native build.
  - **`MARKETSTACK_API_KEY` has a documented home** for machines that need it across
    sessions: `~/.config/copilot/secrets.env`, mode 600, outside the tree so it cannot be
    committed by construction, sourced at the point of use.
  - **`make install-tools` is not needed** to build or to run the suite, and is the slow
    step. Only `cargo-nextest` and `prek` are, at their pinned versions.

### Measured

- **A bare machine reproduces the recorded verdicts exactly.** Fresh toolchain, catalog
  refetched from the vendor, then `validate --all`: AAPL 16/31 at +0.046877 R, MSFT 20/31
  at +0.089455 R, SPY 17/30 at +0.049848 R - identical to six decimal places, with fetch
  counts matching too (15,851 fetched, 15,849 written, 2 rejected). The
  "reproducible from a commit" claim is now demonstrated end to end rather than asserted,
  and a rehydration that differs on these numbers has a real problem.

### Found

- **The catalog can no longer be rebuilt "to today", and the guard is what says so.**
  `--from 2005-01-01` with no `--to` fetches ~5,450 bars per symbol, which puts the
  2022-01-01 holdout at **21.5%** - outside the charter's 15-20% band - so `carve` raises
  `HoldoutCarveError` and every `validate` run stops.
  [ADR-0012](decisions/0012-the-holdout-is-carved-at-2022-01-01.md) predicted exactly
  this and chose a date pin over a percentage so catalog growth would force a re-decision
  in a commit instead of silently moving the boundary. Reproduce the recorded window with
  `--to 2025-12-31` until that re-decision is made.
- **The vendor's 2026 rows carry defects the earlier history does not.** Extending the
  same fetch to today rejects 11 additional rows as `schema_or_value_error` (AAPL
  2026-06-09 and 06-10, MSFT 2026-06-15 among them) against **zero** over 2005-2025.

## 2026-09-02 (six surfaces still described a defect that was fixed)

### Fixed

- **The unknown-working-order gap was fixed on 2026-09-01 and six surfaces still called it
  a known failure.** `crates/execution/src/reconciliation/orders.rs` adopts an external
  order reported as `SUBMITTED`, and `live/node.py` sets `fetch_all_open_orders=True`, both
  landed in PR #23 with a Rust test. Meanwhile `live/cancel_working.py` told the operator in
  its docstring *and at runtime* that such an order "is never adopted into the cache and is
  invisible here"; `live/failure_injection.py` carried it as `KNOWN FAILURE` in a report key
  literally named `known_failure`; and `ROADMAP.md`, `PAPER_CAMPAIGN.md` (twice),
  `OPERATIONS.md` and `MAINTENANCE.md` each asserted the pre-fix behaviour as current. A
  tool that understates what it can see is the same class of defect as one that overstates
  it - both leave the operator with a wrong model of what is protecting them.

- **The correction is not "it works now".** The engine defect is fixed and unit-tested;
  **nobody has watched a stranded order come back and be cancelled**, because confirming it
  strands a live order on purpose. Stage six now records the case as `UNCONFIRMED` rather
  than `KNOWN FAILURE`, which is the honest label and the one that points at the right next
  action: a known failure wants a fix, an unconfirmed fix wants a session.

- **`cancel_working.py`'s caveat survives, with its justification replaced.** A clean sweep
  is still not proof the broker has nothing working - but the reason is now the TWS
  precautionary size setting, which holds a large order in the GUI where no API call can see
  or cancel it, plus the unconfirmed fix. The old reason had been fixed out from under it.

### Changed

- `failure_injection.py`: `UNRECOVERABLE_CASE` renamed `UNCONFIRMED_CASE`, and the report
  key `known_failure` renamed `unconfirmed`. Reports under `live/out/` written before today
  keep the old key and stand as dated records.
- Two further stale rows found in the same sweep: `PAPER_CAMPAIGN.md` listed failure
  injection as "Stage 6, **failing**" and `ROADMAP.md` listed it as "the last unbuilt piece
  of paper stages 1-6". It is built, run, and passing on the second run. The "Ready to
  build" group now holds the broker confirmation instead, keeping the open-item count at
  eleven under the grooming rule.

## 2026-09-02 (the capnp installer stays out of the system)

### Fixed

- **`scripts/install-capnp.sh`: the macOS branch now honours `CAPNP_PREFIX`.** The Linux
  branch always did, but on a Mac the same documented invocation hardcoded
  `/usr/local` with sudo, or reached for Homebrew - a silent system modification where a
  self-contained user-directory install was asked for. A requested prefix now bypasses
  Homebrew entirely and sudo is used only when the destination is genuinely unwritable.
  Registered in the delta; three contract tests pin the prefix contract across both
  branches and fail on the pre-fix script. Found surveying the temporary-macOS question:
  the trip machine can now take the entire toolchain without permanent system changes
  beyond Apple's own Command Line Tools.

## 2026-09-02 (the failing msgbus test was the runner's fault)

### Fixed

- **`test_republish_external_msgbus_message_logs_topic_and_error_chain` passes: 328/328
  in `nautilus-live` under cargo-nextest.** The test installs a process-global logger
  (`log::set_logger` succeeds once per process), so under bare `cargo test` - 328 tests,
  one process - whichever LiveNode test initialises logging first wins and the capture
  test panics. The project's own runner has been cargo-nextest all along
  (`.config/nextest.toml`, every `make cargo-test*` target), which isolates each test in
  its own process; it simply was not installed on this machine, so earlier sessions ran
  the wrong harness and recorded a "pre-existing failure" that was actually a
  harness artifact. Upstream carries the identical test. No code changed; cargo-nextest
  (0.9.143, the pinned version) is now installed, the toolchain instructions install it,
  and MAINTENANCE.md says to never run Rust tests with bare `cargo test`.

## 2026-09-02 (the front page says what this is)

### Changed

- **`README.md` rewritten for ownership.** The inherited front page was upstream's
  product page - their badges, release tables, install story and community links - so
  the first thing a reader saw answered "what is this repository" with someone else's
  product. The rewrite states what this copy is (a privately operated system on a
  detached copy of NautilusTrader, per ADR-0010), routes readers to the charter,
  roadmap and working rules, and credits upstream, whose LGPL-3.0 license continues to
  apply. Registered in the delta; the last item of the ownership pass that started with
  PR #26.

## 2026-09-02 (the holdout is carved)

### Decided

- **[ADR-0012](decisions/0012-the-holdout-is-carved-at-2022-01-01.md): the holdout is
  carved at 2022-01-01, pinned by date.** Bars closing at or after the pin (1,003 of
  5,283 per symbol, 18.99% - inside the charter's 15-20%) are withheld before the gate
  sees them. A date rather than a percentage for the same reason the cost snapshot is
  pinned by name: a percentage boundary moves silently when the catalog grows, and would
  leak held-out bars into development. The carve refuses a history that does not
  straddle the pin or a share outside the band, so catalog growth forces a re-decision
  in a commit instead of a quiet drift. No spend tool exists, deliberately: the spend
  also now waits on the entry-timing conflict, so the one-time test is not burned on
  fill semantics the charter rejects.

### Added

- **`validation/holdout.py`** - `carve` splits every history at `HOLDOUT_START` and is
  the only path into the walk-forward from `validate`. Nine tests, including that the
  pin itself is asserted (moving it fails a test and demands an ADR), that a bar closing
  exactly at the pin instant is holdout, and that the band guard refuses in both
  directions. Verdict records gain a `holdout` block (start, bars reserved, range) so
  `holdout_spent: false` finally points at something real.

### Measured

- **The development-window verdicts (2005-2021, 31 folds): all three majority-pass
  net.** AAPL 16/31 at +0.0469 R, MSFT 20/31 at +0.0895 R, SPY 17/30 at +0.0498 R.
  Every earlier verdict is superseded - including the same morning's full-window net
  run, where AAPL was majority-fail: the folds that dragged it under sit in what is now
  the holdout. A verdict that changes when the window does is the argument, in one
  datum, for treating single-name results as provisional and leading with SPY.

## 2026-09-02 (the gate goes net of costs)

### Decided

- **[ADR-0011](decisions/0011-spread-is-charged-at-p95-from-a-pinned-snapshot.md): spread
  is charged at p95 from a pinned snapshot.** The choice moves net edge by only 3-14% of
  gross at daily-bar frequency, and three measurement biases all point the conservative
  way: snapshots sample mid-session while the strategy trades after overnight gaps, the
  median moved 2x between measurement sessions, and 2026 spreads are applied to trades
  from 2006 onward. Revisit triggers are named in the ADR - an intraday strategy
  re-measures at its entry time-of-day rather than inflating the percentile.

### Clarified

- **SPY is the lead candidate, per the charter's own scope table.** *"Liquid US-listed
  ETFs first; large caps only once the pipeline handles point-in-time universes and
  corporate actions"* - and the pipeline handles neither yet. SPY's net pass leans on no
  splits table and no survivor-chosen membership, so it is the verdict whose evidence is
  whole; the MSFT and AAPL results are provisional until the pipeline graduates. The
  holdout carve starts with SPY.

### Added

- **`calibration/cost_model.py`** - the cost model the gate charges. Per-instrument
  per-side spread from the snapshot pinned **by name** (re-running the calibrator cannot
  silently change what verdicts are charged), plus IB Pro fixed-tier commission with the
  measured USD 1.00 minimum, on split-corrected share counts. An uncalibrated symbol
  refuses to score rather than borrowing another symbol's number. Costs run through the
  walk-forward **objective**, so the in-sample search selects parameters that survive
  costs - exact for a one-entry-one-exit trade shape, with the engine untouched. Twelve
  tests; `cost_impact` now imports the shared arithmetic instead of duplicating it.

### Measured

- **The first net verdicts moved a result.** AAPL flipped from majority-pass to
  majority-fail (19/39 folds, net +0.0345 R against +0.0492 gross); MSFT (24/39, +0.0682)
  and SPY (21/38, +0.0541) held. Cost-aware selection chose different parameters on two
  symbols, visible as changed trade counts. Verdict records now carry their exact cost
  basis - snapshot, percentile, coefficient, schedule - and score keys are renamed
  (`mean_oos_net_expectancy_r`, `net_score_r`) so a net number can never be compared to a
  gross one under the same name.

## 2026-09-02 (making the calibrator runnable, and two market-session answers)

### Fixed

- **`LiveNode.add_actor` is now exposed to Python** (`crates/live/src/python/node.rs`). The
  Rust node has always accepted actors and `BacktestEngine` already exposed the instance form,
  but the live node offered only `add_actor_from_config` - so an actor configured with state
  decided at runtime could be backtested and not deployed. `calibration/spread_snapshot.py`
  was exactly that shape and raised `AttributeError` before recording a quote, which meant the
  spread numbers under the cost analysis came from code that was not in the repository.

  **The calibrator now runs from a commit, and its snapshots are reproducible for the first
  time.** Three tests, the load-bearing one being that state assigned to the actor after
  construction survives registration - which is the whole difference between the two entry
  points, and the thing `add_actor_from_config` cannot do.

- **A connection failure in `calibration/entitlements.py` is now a probe verdict, not an
  exception.** The client is constructed inside the `try`, because constructing it connects.
  Sixteen probes were reduced to one traceback about the first when the timezone alias was
  unset.

### Measured

- **Realtime quotes are still not entitled, and this time the run can prove it.**
  `spread_snapshot` recorded **zero** usable quotes across AAPL, MSFT and SPY over 107s under
  `REALTIME`, and **55** across the same three over 106s under `DELAYED`, two minutes apart in
  the same session. The delayed run is the
  control, and without it a realtime run recording nothing cannot be told apart from a broken
  subscription. Historical bars agree: all five US equity probes still return `[2188]` under
  both market data types, while both FX probes return bars.

  Release forms unlocked **delayed** quotes across the US equity universe. They did not
  unlock realtime, and the equity minimum is still the gate.

- **The sibling-subscription stall does not reproduce.** Both treatments the original
  observation named were run against a control instrument: tick-by-tick trades left AAPL at
  36 quotes against a 39 baseline (control 36 / 38), and an L2 book subscription left it at
  37 against 39 (control 38 / 38). Neither run drew an IB refusal, and the original stall came
  with 10189 and a depth-entitlement refusal - so the reading is that the account's widened
  entitlements mean IB no longer refuses these requests and therefore cannot trigger it. The
  mechanism is untested rather than disproven, and the practical risk is gone.

- **The per-order commission minimum is measured, not modelled.** A second supervised round
  trip at a third of the size: 1 AAPL against USD 500 of capital cost **2.01 USD** in
  commission, against **2.02 USD** for the three-share trip the day before. Cutting the
  position to a third changed the cost by one cent, which is what
  [ADR-0009](decisions/0009-cost-is-modelled-at-the-target-account-size.md) rests on and had
  only ever asserted from a fee schedule. Commission was **98%** of the realised loss, and the
  cost in R moved from 0.1010 to 0.1030 - **trading smaller does not help.**

- **A directed-exchange historical request is not satisfied by the non-consolidated feed.**
  `AAPL=STK.IEX` and `AAPL=STK.ISLAND` both return 2188 under both market data types, the
  same as SMART. There is no free route to some history; consolidated data is a purchase.

- **The first reproducible-from-a-commit spread snapshot: 27 minutes, 250-300 samples per
  name** (full spread, bps of mid): AAPL median 1.22 / p95 3.98, MSFT 2.00 / 3.59, SPY 0.26
  / 1.05. The cross-name ratio is the stable fact - MSFT ~8x wider than SPY in every run -
  while AAPL's median doubled against 2026-08-31 with SPY unmoved, so a coefficient set from
  one session inherits that session. The incumbent 5 bps per side stays 4x-38x conservative
  depending on the name; both numbers argue the coefficient must be per-instrument.

### Ownership housekeeping

- **The queued inherited surfaces are retired.** `CLA.md`, `CODE_OF_CONDUCT.md`,
  `AI_POLICY.md`, `.github/CODEOWNERS`, the issue templates and the PR template are removed;
  `SECURITY.md` is replaced (no external reports; upstream's original stays linked because
  `docs/developer_guide/security.md` references it relatively); `.github/OVERVIEW.md`'s
  change-controls bullet now records the CODEOWNERS removal instead of pointing at it. The
  delta register learned deletions to make this enforceable: a removed file never leaves the
  diff against the merge base, so its row stays, marked "Removed", and the path test checks
  both directions - a registered file must exist and a removed one must not. The README
  rewrite remains queued as its own PR and inherits the two stale references.

- **The repository's front matter now answers for this project, not upstream.** Root
  `ROADMAP.md` is a pointer to `copilot/docs/ROADMAP.md` (upstream's original stays linked
  for harvest decisions), and `CONTRIBUTING.md` states that this repository takes no
  contributions and points at the charter and `AGENTS.md`. The full survey of inherited
  governance surfaces - what is aligned, kept deliberately, or queued for removal - is in
  `MAINTENANCE.md`, alongside a new record of the codebase's actual shape (one Rust project,
  ~1.82M lines across 43 crates, under a ~5.8k-line Python facade) so a session can start
  from the doc instead of re-deriving it.
- **Why CI has never run is diagnosed and recorded** in `MAINTENANCE.md`: the harden-runner
  egress allowlist lives in repository variables that did not travel with the copy, so every
  job blocks all traffic and dies at checkout. Actions is disabled again as of 2026-09-02;
  the grooming pass is a roadmap item and starts warm.

### Added

- **`copilot/live/subscription_interference.py`** - a controlled test of the reported stall
  where quotes stop once a second subscription is added to the same instrument. A treated
  instrument gets the second subscription, a control instrument does not, and both are counted
  across the same two windows. Without the control, quotes stopping everywhere at once reads
  as a result; with it, that is visibly a session-wide event and says nothing about the
  subscription. A baseline with no quotes is reported inconclusive rather than as evidence in
  either direction.

  **A treatment that raised also reports inconclusive**, which the first depth run needed:
  `subscribe_book_depth10` is not implemented for Interactive Brokers, the call raised on a
  missing argument, quotes carried on undisturbed, and the run read NOT REPRODUCED from an
  experiment with no treatment in it. The treatment is now an L2 `subscribe_book_deltas`,
  which the adapter does forward, and a clean negative can no longer come from a test that
  never ran.

## 2026-09-01 (recovering an unknown working order)

### Fixed

- **Reconciliation now adopts an external order reported as `SUBMITTED`**
  (`crates/execution/src/reconciliation/orders.rs`). The status match dropped everything
  outside seven statuses with a warning and no events, so an order working at the venue that
  we did not place produced no cache entry - it existed at the broker and nowhere else, and
  nothing could query, cancel or reconcile it. Projecting it as accepted is mildly optimistic
  and that is the right direction to err: an order we can see and try to cancel is
  recoverable, an invisible working one is not. Test verified by stashing the fix.
- **`fetch_all_open_orders=True`** on the execution client. The default is `false`, which
  makes the adapter call `reqOpenOrders` and see **only orders bound to the calling client
  id**. Every run used a fresh client id, so the sweep tool was structurally blind to every
  previous run's orders and reported "nothing working" while orders were live.

### Found, and not fixable in code

- **A 100,000-share order trips a TWS precautionary size setting**, which holds it in the GUI
  awaiting a manual transmit. Our system records an acceptance, the broker never receives the
  order, and no API call can see or cancel it. Four orders from earlier stage-six runs ended
  up there and had to be cancelled by hand.

  The reject probe is now 5,000 shares - USD 1.2M against USD 1M of buying power, so it still
  asks the question. **An injected fault should be the smallest one that asks the question.**

## 2026-09-01 (ownership, and the risk engine fix)

### Decided

- **[ADR-0010](decisions/0010-the-repository-is-ours.md): the repository is ours; upstream is
  a source we read.** Detached from the fork network on GitHub - no parent, no pull-request
  relationship. Supersedes [ADR-0004](decisions/0004-quarterly-upstream-sync.md); there is no
  sync cadence because there are no syncs. `AGENTS.md` rewritten for ownership,
  `UPSTREAM_DELTA.md` repurposed from *"what we owe at the next sync"* to **"what we own and
  must test ourselves"**, `MAINTENANCE.md`'s review and sync procedures replaced by a harvest
  procedure.

  What we accept, stated rather than glossed: upstream's fixes no longer arrive for free, and
  a bug fixed there in the matching engine, cache or order model will not reach us unless
  someone notices and harvests it.

### Fixed

- **`max_notional_per_order` now applies when no account resolves**
  (`crates/risk/src/engine/mod.rs`). The check resolved an account by the instrument's venue
  and, on failure, returned `true` for every order - skipping the balance, margin **and**
  notional checks together. On IB the lookup always fails. A per-order cap is a bound on the
  order, not on the account, so it now applies either way. The account-resolution failure also
  moved from `DEBUG` to `WARN`: an operator who cannot see it believes risk checks are running
  when they are not.

  Two Rust tests, verified by stashing the fix - the denial test fails without it and the
  companion still passes, so the pair pins behaviour rather than implementation.

### Passed

- **Paper stage six.** Both scored probes: the cap denies with
  `NOTIONAL_EXCEEDS_MAX_PER_ORDER`, stale-feed detection fires at 20.6s, nothing left working.

### Excluded and named

- **`rejected_by_broker` - paper cannot decide it.** IB paper accepted a USD 24M order on a
  USD 1M account. Still submitted, so we would notice if that changed, but not scored: scoring
  it would make the stage permanently unpassable for a reason unrelated to our system. **The
  rejection path must be verified live before any size increase.**
- **`recover_unknown_working_order`** remains a known failure, now ours to fix and the
  highest-value item on the board.

### Found in our own code

- Reclassifying a probe left its order in the id map while removing it from the scored list,
  so a bare `next()` raised `StopIteration` inside the accepted handler, **skipped the cancel**
  and left a live order at the broker - visible only as "left working". The lookup now returns
  `None` for an unscored case, and **the cancel runs before any bookkeeping**: recording is
  bookkeeping, a live order is the failure.
- The runner now waits for cancellations to be acknowledged before stopping, which
  `OPERATIONS.md` requires. Reading `orders_open` mid-cancel reported an order that was already
  on its way out; a false alarm from a safety check is as corrosive as a missed one.

## 2026-09-01 (paper stage 6)

### Added

- **`live/failure_injection.py`** - paper stage six. Three injected faults whose expected
  outcome is a refusal or an alarm, plus one case reported from the record rather than re-run.
- `build_paper_node` accepts a logging config, which is how a `DEBUG`-only failure was found.

### Failed, and the failure is the point

- **`max_notional_per_order` is silently inert on Interactive Brokers.** An order for
  USD 1,580 against a configured USD 1,000 cap was accepted. Confirmed at `DEBUG` rather than
  inferred: `Cannot find account for venue SMART (account_id=None)`.
  `RiskEngine::check_orders_risk_for_account` resolves the account with
  `account_for_venue(instrument.venue)`; instruments resolve on `SMART` while the account is
  `IB-DUT067974` on venue `IB`, so the lookup fails and the function returns `true`, passing
  every order. **The notional cap needs no account** - it is a statement about the order - but
  it sits past an account guard that fails for an unrelated reason.

  Nautilus ships two pre-trade risk controls; on IB one of them does nothing. The backstop
  described in the stage-five entry was fiction. `TradingState::HALTED`, verified at stage
  one, is now the only pre-trade control this system actually has.

  This is the **third** failure caused by the same unmodelled distinction between listing
  venue, routing destination and the account's home venue - after the stage-two account
  lookup and stage-three order routing.

- **IB paper accepted a USD 24M order on a USD 1M account** (100,000 MSFT at a USD 240
  limit). Paper does not enforce buying power on a far-from-market limit, so a rejection path
  tested only on paper has not been tested.

### Passed

- Stale-feed detection fired 20.2s after the subscription was cut. That tests the detector
  against a real feed going quiet, not IB going quiet.

## 2026-09-01 (paper stage 5)

### Added

- **`live/supervised_session.py`** - a full supervised round trip, **sized for the account we
  will have rather than the one paper gives us**. `--capital` is the deployable figure;
  quantity is whole shares inside it, and `max_notional_per_order` is a backstop above it
  rather than the budget itself. Setting the cap at exactly `capital` would deny the
  take-profit, since a bracket's target is a sell above the entry.

### Passed

- **Stage five, first attempt, during RTH.** Market entry filled, **both bracket children
  reached the broker and sat working** - the thing stage four could not test - position closed
  on purpose, nothing left working. 3 AAPL inside USD 1,000 of capital; entry 315.71, exit
  315.62.

### Measured

- **Commission was 2.02 USD on a USD 947 round trip, and 88% of the total loss.** The price
  moved 27 cents against us across the hold; the broker charged more than seven times that to
  open and close.
- **The cost model is confirmed empirically.** At USD 20 of risk that is **0.1010 R** against
  the 0.11 R predicted from the fee schedule before any trade existed - within ten percent.
  Against AAPL's walk-forward gross expectancy of +0.0492 R, commission alone leaves
  **-0.0519 R before spread**. The gap fade's negative verdict at the target account size is
  now an observation rather than an inference.

### Not measured, and stated so

- **Slippage.** The mid came from a delayed quote, so the entry filling below it is evidence
  that a 15-minute-old quote is not a benchmark, not evidence of a good fill.
- **Diversification.** Three shares of one instrument was 95% of deployable capital. At this
  account size there is no second position.

## 2026-09-01 (paper stage 4)

### Added

- **`live/order_types.py`** - paper stage four. Submits every planned order type and time in
  force at prices the market cannot reach, cancels each on acknowledgement, and prints a
  matrix with the untestable shapes named rather than omitted.

### Passed

- **Five shapes round tripped**: LIMIT/GTC, LIMIT/DAY, STOP_MARKET/GTC, STOP_LIMIT/GTC, and
  the gap fade's bracket as a three-order list submitted and cancelled as one.

### Not tested, deliberately

- **MARKET, and the bracket's real market entry.** There is no far-from-market price for a
  market order, so it cannot be submitted without filling, and stage three established that
  this project cannot yet reliably clean up after itself. Deferred to stage five under
  supervision, and printed as `N/A` with the reason so the hole in the matrix is visible.
- **Child activation** in the bracket, which needs the parent to fill. Same deferral.
- The run was **pre-open** (13:01 UTC against a 13:30 UTC cash open). IB's acceptance rules
  for `DAY` and for stops are not necessarily the same inside a session as outside one, so
  this is evidence about submission rather than about a live session.

### Found

- **`order_factory.bracket()` returns a plain `list`**, not an object with `.orders`. The
  first attempt raised on `.orders` *after* the four single orders were already submitted,
  aborting node startup with four orders on their way and no strategy left to cancel them.
  The fix that matters is not the attribute: **everything is now constructed before anything
  is sent**, so a construction error cannot leave a half-submitted batch behind. Any batch
  built and submitted in one loop has that failure mode.

## 2026-09-01 (paper stage 3)

### Added

- **`live/symbology.py`** - the bridge between the id research scores (`AAPL.XNAS`) and the
  id the broker trades (`AAPL=STK.SMART`). The MIC venue stays on the research side because
  `SMART` is an order *routing destination*, not a listing venue, and is meaningless in a
  stored bar. Routing is a table rather than a default, so an unmapped venue raises instead
  of quietly acquiring `SMART` at the moment an order is placed. 10 tests, one of which
  asserts every registered activation can reach a broker id.
- **`live/controlled_order.py`** - paper stage three. Four independent safeguards, because
  one is a preference and four are a design: a limit at half the reference price, one share,
  an engine-level `max_notional_per_order` set outside the strategy, and a fill recorded as
  a failure.
- **`live/cancel_working.py`** - cancels working orders across strategies, as
  `OPERATIONS.md` requires at the end of every monitoring window.

### Passed

- **Paper stage three.** `AAPL=STK.SMART` BUY LIMIT 1 @ 135.93 submitted, accepted (venue
  order id `832000001`), cancelled. No fill, no reject, no deny.

### Found

- **`Strategy.cancel_order` takes a `ClientOrderId`, not an `Order`** - unlike
  `ExecutionAlgorithm.cancel_order`, which takes the order. Passing the order did nothing at
  all: no cancel, no exception, no log line, and a working GTC order left at the broker after
  the node stopped. Only a missing event distinguished that run from a clean one.
- **The execution client needs an explicit `RoutingConfig`.** Without it every order was
  denied `NO_EXECUTION_CLIENT: client_id=NONE, venue=SMART`. Destinations are listed rather
  than `default=True`, so a research-form id like `AAPL.XNAS` still gets denied.
- **Nautilus cannot cancel an external order in `SUBMITTED` status.** Reconciliation matches
  seven statuses and drops the rest with a warning, so an order left working by a previous
  run never enters the cache and is invisible to `cancel_all_orders`. That is the "unknown
  working broker order" scenario `OPERATIONS.md` requires a stage-six test for, and the
  framework cannot currently recover from it. Filed as ready-to-build; `cancel_working.py`
  now reports `CACHE CLEAR` rather than `PASS` and states what it cannot see.

## 2026-09-01 (paper stage 2)

### Passed

- **Paper stages one and two both pass** against `DUT067974`. Account reported by the broker
  as `IB-DUT067974`, USD 1,000,000, reconciliation clean: 0 orders, 0 positions, 0 fills.

### Found

- **The account is not on the instrument's venue.** Instruments resolve on `SMART`; the
  execution client registers its account under its own client name, so the id reads
  `IB-DUT067974`. A venue-keyed lookup searching only instrument venues reports a missing
  account that is in the cache the whole time - which is what the second attempt did.
  `preflight.py` now searches both.
- **The paper login name and account id are the same string** here. Settled by observation;
  IB does not require it.
- **The paper account is `MARGIN`; the live account is cash.** Paper will accept a short sale
  and size against buying power, and the live cash account will do neither, so a paper pass
  is not evidence about the cash constraints. Recorded in `PAPER_CAMPAIGN.md` under "What
  paper cannot reproduce", together with the USD 1,000,000 paper balance - three orders of
  magnitude above the target account, and cost-at-size is what decides viability.

### Observed, not diagnosed

- The IB adapter logs `Failed to parse account summary: Account summary currency was empty`
  and skips a margin summary for the same reason, on every connect. Balances still arrive
  and reconciliation still completes, so nothing downstream is known to be affected. Noted
  rather than chased.

## 2026-09-01 (paper stage 1)

### Added

- **`live/preflight.py`** - paper stages one and two, runnable, writing a dated evidence
  record and exiting non-zero if any check failed, so it can gate the next stage rather than
  merely inform it.

### Verified against a real broker

- **The risk engine halt survives node startup.** `HALTED` before the start and `HALTED`
  after it, on paper account `DUT067974` via `172.17.112.1:7497`. Stage one's whole claim,
  and the thing that makes orders-disabled mode real rather than a comment.
- Instruments resolve, and the node shuts down cleanly on demand.

### Found

- **TWS had Read-Only API enabled.** The execution client failed with IB **321** and no
  account or balances ever reached the cache. The two checks that fail say nothing about the
  cause, because the account is missing two steps downstream of a checkbox in the TWS GUI.
  The preflight now names the cause in its own record. Stage two is blocked on the setting,
  not on code.
- **Research instrument ids are not broker instrument ids.** The catalog names `AAPL.XNAS`;
  the broker resolves `AAPL=STK.SMART` on venue `SMART`. Nothing in the overlay maps between
  them, so no activation can currently reach an order. Filed as blocking stage three.
- **`node.cache` raises once a hosted run owns the node**, exactly as `node.risk_engine`
  does. Both must be captured before the run. The risk engine handle already was; the cache
  was not, and the first attempt died on it.

## 2026-09-01 (paper node)

### Added

- **`live/session.py`** - what separates a paper session from a live one, and the checks
  that enforce it. On the stage-one deployment shape paper and live differ by a **port
  number** (7497 against 7496), so two independent facts must agree before a session is
  called paper: the port is a known paper port and not a known live one, and the account
  identifier both carries the paper prefix and matches the configured account. Either check
  alone is insufficient in a way the other covers. Also refuses a client id divisible by
  1,000, and refuses to let the data and execution clients share one. 17 tests.
- **`live/node.py`** - the first execution client in the overlay, and the orders-disabled
  switch. Stage one is implemented by running the strategies normally and **halting the risk
  engine**, so the real path is exercised and every order is denied inside the engine, rather
  than by leaving the strategies out and testing nothing.
- **`docs/PAPER_CAMPAIGN.md`** - the campaign: why the system clock starts before a
  candidate exists, the per-stage gates, the two gates that are easy to wave through, and a
  dated evidence log.

### Verified

- The `RiskEngine` handle resolves on a built node, `set_trading_state` takes effect, and
  the state reads back `HALTED` through a **fresh** `node.risk_engine` - so the binding
  shares one engine rather than handing out copies.
- **Not** verified, and stage one exists to settle it: that the halt survives node
  **startup**. Until it does, orders-disabled mode is an assumption.

### Found

- **`calibration/spread_snapshot.py` cannot run as committed.** It calls
  `node.add_actor(recorder)`, and `LiveNode.add_actor` is not exposed to Python on the pinned
  build - it exists only on the Rust node, whose pyo3 wrapper offers `add_actor_from_config`
  instead. The call raises `AttributeError` before a single quote is recorded, so the spread
  snapshots underneath the cost analysis were produced by code that is not in the repository.
  The measurements are not thereby wrong, but they are not reproducible from a commit. Filed
  as ready-to-build; not fixed here, to keep this change to the paper node.

## 2026-09-01 (account constraints)

### Recorded

- **The account is cash**, pending a margin decision. A cash account cannot sell short, so
  the gap fade's short leg is unavailable at any price and only long activations can ever be
  promoted. All three registered activations are already long-only, so nothing registered is
  blocked. Noted in `playbook/PREFLIGHT.md` and `strategies/registry/README.md`.
- **Market-data subscriptions are gated on account equity**, and the account is below the
  bar. Funds added; settlement expected on or after 2026-09-08. Until then the only US equity
  quotes available are the complimentary delayed, non-consolidated feed.
- **Top of book, not depth.** Both candidate entries are auctions (market-on-close today,
  next-session-open under the charter), which clear at a single price rather than against the
  continuous book; and positions are low single-digit to low tens of shares against inside
  quotes of hundreds to thousands. The live subscription question is **consolidated versus
  non-consolidated at L1**, which re-scopes the open data decision away from depth products.

### Changed

- `docs/ROADMAP.md` gains **"Stage 08 - what a paper run actually needs"**, recording that no
  paper node exists (the only IB code in the overlay is a data-only node in
  `calibration/spread_snapshot.py`) and separating the paper stages that are blocked from the
  six that are not.
- New open-work group **"Waiting on the account (3)"**, carrying the operator's follow-ups.
  Open items 13 to 16.

## 2026-09-01 (charter)

### Added

- **`docs/CHARTER.md`** - the entry point. Purpose, operating model, the three kinds of
  success, the two-track lifecycle, and which gate a candidate stands at. Everything else
  breadcrumbs from it, including the out-of-repo half, so it outranks `AGENTS.md` where the
  two overlap.
- **`docs/playbook/`** - `PREFLIGHT`, `RESEARCH`, `RISK` and `OPERATIONS`, each carrying its
  own checklist beside the process it belongs to rather than collected where they would be
  read out of context.
- **[ADR-0009](decisions/0009-cost-is-modelled-at-the-target-account-size.md)** - cost is
  modelled at the target account size.

### Measured

- **The gap fade is negative at the account size it would actually trade.** Repricing the
  same trades at USD 20 of risk (an 8k account at 0.25%) rather than the research default
  of USD 1,000: AAPL -0.065 R, MSFT -0.037 R, SPY -0.059 R. At 0.10% risk, 31 to 131 trades
  per symbol do not size at all.
- The mechanism is IB's USD 1.00 per-order minimum. At USD 20 of risk the position is 5 to
  23 shares and a round trip costs USD 2.00 whatever its size: **0.11 R against a gross edge
  of 0.05 to 0.09 R**. Spread at the same point is 0.007 R, so **commission is fifteen times
  larger** and the median-versus-p95 question is noise beside it.

### Corrected

- **Two earlier cost reports were true of their stated budget and misleading as summaries.**
  Quantity cancels out of the spread term, so spread cost in R is genuinely size-independent
  - and it was easy to carry that across the whole model. A per-order minimum does not
  cancel, and it dominates exactly where a beginner account operates.

### Recorded as open

Adopting the charter surfaced three conflicts with the code, now tracked in `ROADMAP.md`:

- **No locked holdout exists.** `walk_forward` runs over the whole history while every
  verdict record carries `holdout_spent: false`, implying one does. No verdict from this
  repository currently has an out-of-sample estimate behind it.
- **Entry timing conflicts.** The gap fade fills at the signal bar's close; the charter
  requires next-eligible-session entry.
- **The universe is survivor-biased.** Today's large caps backfilled to 2005, which the
  charter names as the error to avoid.

### Fixed

- Restored the "waiting on a market session", "waiting on spend" and "deferred by decision"
  sections of `ROADMAP.md`, lost to an over-broad regex edit two commits earlier.

## 2026-09-01 (strategy registry)

### Added

- **`copilot/strategies/activations.py`** and **`registry/*.toml`** - the knob store from
  ADR-0005. One file per activation: strategy, instrument, lifecycle
  (`RESEARCH`/`PAPER`/`LIVE`), fixed parameters, fold geometry.
- **`SEARCH_SPACE` on `gap_reversal`**, beside the code, because the reasoning that sized
  it belongs with the premise. `MAX_SEARCHABLE_MIN_GAP_ATR` pins the ceiling so the axis
  cannot be widened without re-counting events - trade-copilot's V1-31 searched values
  that produced no evaluable folds, so the run returned the absence of a verdict rather
  than a verdict.
- **`copilot/strategies/validate.py`** - runs the gate for an activation and files a JSON
  record. Read-only: it constructs no execution client whatever a lifecycle says.
- **25 tests**, including both halves of the seeding rule and the search-space ceiling.

### Fixed

- **A verdict is now reproducible from a commit.** No `ParameterGrid` existed anywhere in
  committed code, so the walk-forward result reported on 2026-09-01 could not be
  reproduced by anyone, including whoever produced it. `validate --all` now reproduces it
  exactly: AAPL 20/39 at +0.049150 R, MSFT 24/39 at +0.090638 R, SPY 22/38 at +0.063737 R.

### Recorded

- **Verdict records are committed**, under `strategies/verdicts/`, on the same reasoning
  that keeps `calibration/out/`: a result that exists only in a terminal cannot be
  checked, compared or found again. Each carries `costs_modelled: false` and
  `holdout_spent: false` so a file cannot later be read as more than it was.
- **A test asserts nothing in the registry has left `RESEARCH`.** Promotion should be a
  deliberate diff that fails an assertion and makes someone justify it, not a quiet edit.

## 2026-09-01 (decisions + maintenance)

### Added

- **`docs/decisions/`** - 8 ADRs recording decisions that were previously only prose in
  `AGENTS.md`, `ROADMAP.md` and commit messages, where the conclusion survived but the
  reasoning did not. **Re-derived, not lifted** from trade-copilot's 25: its architecture
  rests on a human placing every order by hand, and copying the set wholesale would import
  that assumption into decisions that look unrelated to it.
- **`docs/MAINTENANCE.md`** - the upstream sync procedure and the runtime ownership map.
  Quarterly review starting 2026-09-01, with "skip" and "retire a delta" as first-class
  outcomes alongside "sync".

### Recorded

- **Ops progresses in three stages and does not skip ahead**: WSL with host TWS now,
  dockerized IB Gateway once a strategy is validated, Kubernetes once proven. Stage 1 is
  explicitly **not** unattended - a machine that sleeps is intermittent, and the guard's
  cooldown across a restart must be reviewed before any unattended run.
- **There is no path that consumes a published `nautilus_trader` wheel.** Carrying Rust
  deltas means an image built on one would run, connect, trade, and silently fall back to
  breakers that cannot stop the next order. We build from source, pin third-party images
  by digest, and assert the binding at startup.
- **Knobs get three buckets** - searchable, identity, environment - with `SEARCH_SPACE`
  beside the strategy code and activation in a version-controlled registry. The immediate
  reason: no `ParameterGrid` exists in committed code, so the first walk-forward verdict
  this fork produced cannot be reproduced from the repository.

## 2026-09-01 (risk enforcement + universe)

### Added

- **`crates/risk/src/python/engine.rs`** (upstream, registered) - `PyRiskEngine`, a
  Python handle to the shared `Rc<RefCell<RiskEngine>>` exposing `set_trading_state`
  and `trading_state`. Mirrors the existing `PyCache` pattern, `unsendable` included.
  Reached from `LiveNode.risk_engine`.
- **8 tests against the real engine** (`test_risk_engine_binding.py`), plus 4 covering
  the guard's halt and release decisions.

### Changed

- **The risk guard is preventive, not reactive.** On breach it now halts the engine
  first, so a strategy that keeps submitting is denied inside the risk engine rather
  than tidied up after. Cancel and flatten follow. `configure(settings, risk_engine=...)`
  takes the handle; **without it the guard degrades to the old reactive behaviour and
  logs a warning** rather than pretending to be an engine-level gate. The state is
  restored to ACTIVE when the cooldown expires, and only if the guard set it.
- **The backfill fetches one symbol at a time.** A 20-symbol, 20-year window blew the
  client's page budget and failed - the guard working, but it made a real universe
  unusable. Per symbol each series is ~6 pages, a failure is isolated to one name, and
  progress is visible on a fetch that otherwise looks hung for minutes.
- **The calibrator writes a rolling snapshot** every 25 quotes to a `.partial.json`,
  removed on clean exit. An interrupted run still reaches its `finally`, but only after
  the node unwinds - minutes on a long run, long enough that an operator reasonably
  concludes the samples were lost.

### Measured

- **Universe widened to 20 symbols**: 105,414 rows fetched, **105,398 bars written**,
  16 rejected (0.015%). Liquid US large caps plus SPY/QQQ/IWM, all with 2005 history,
  since the gate needs 252-bar folds.

### Corrected

- **The raw OHLC set is not perfectly coherent, and this changelog previously said it
  was.** Zero failures held over the 15,851 rows first measured; over 105,414 there are
  twelve, every one an open a few cents outside the day's range (GOOGL 2025-12-29 opens
  at 314.52 against a high of 314.02). 0.011% against 22% for the adjusted set leaves
  the choice unchanged and the gate rejects them either way - but the claim was stronger
  than the evidence supported once the universe widened.
- `price_currency` is worse than first reported: at 20 symbols the vendor tags US large
  caps ARS, MXN, CLP, THB, GBP and CHF. Advisory only, as already documented.

### Upstream delta

Grew from 3 files to 9, all to reach `set_trading_state`. Six are new files or
additive-only. `crates/live/src/python/node.rs` joins `data/core.rs` in the churning
category.

## 2026-09-01 (strategy + toolchain)

### Added

- **`copilot/strategies/gap_reversal.py`** - the overnight-gap fade, ported from
  trade-copilot `libs/setups/gap_reversal.py` (V1-32). Chosen over the RSI reversal and
  the trend rule because its trigger *is* its quality measure, so it clears the gate's
  trade-count floor where a filtered RSI trigger did not. 18 tests, most of them pinning
  the direction discipline the leg split depends on.

### Measured

- **First real verdict**, 20 years of history, three symbols, 39 folds each:
  AAPL 20/39 folds passed at **+0.0492 R** over 612 trades; MSFT 24/39 at **+0.0906 R**
  over 571; SPY 22/38 at **+0.0637 R** over 609. All three majority-pass.
- **Gross of costs**, holdout unspent, and entry fills at the signal bar's close rather
  than the next open. A first result, not a green light.

### Changed

- **`copilot/ruff.toml` deleted; the ignores now live in `python/pyproject.toml`.** The
  scoped config never governed these files: the repository's pre-commit runs
  `ruff --config python/pyproject.toml` over every Python file, `copilot/` included, so
  the first `make pre-commit` reported **728 errors** on the overlay - mostly upstream's
  own `tests/**` exemptions failing to match `copilot/tests/**`. Registered as an
  upstream delta. This removes a documented departure rather than adding one.
- **`make pre-commit` works.** It needs only `prek`, not the whole `make install-tools`
  chain that compiles ten cargo tools this fork does not use:
  `uv tool install prek==$(bash scripts/tool-version.sh prek)`.

### Verified

- The delta register caught its own first real case: changing `python/pyproject.toml`
  failed `test_every_changed_upstream_file_is_registered` until a row was added.

## 2026-09-01 (sync policy)

### Changed

- **Upstream syncing is on demand only.** The fork does not track `develop`. While
  development is active it is held still on purpose: chasing upstream mid-feature means
  debugging our own work and someone else's refactor on two moving bases at once.
- The delta report's conflict line is now stated as a **forecast** for whenever a sync is
  chosen, not a work item. The report also prints the date of the local upstream snapshot,
  which under this policy ages by design - a conflict verdict computed against a stale ref
  must not read as current.
- **Contributing the two IB fixes upstream is deferred.** It remains the cheapest way to
  retire a register entry, but a pull request opens a review front on someone else's
  schedule, which is what the policy exists to avoid.

## 2026-09-01 (upstream delta policy)

### Changed

- **The prime directive is now "every upstream change is registered", not "change zero
  upstream files".** Upstream changes are permitted where they are worth it. The
  guarantee they replaced was self-enforcing; a register is not, so it is enforced by a
  test instead.

### Added

- **`copilot/docs/UPSTREAM_DELTA.md`** - the register. One row per upstream file this
  fork changes: what, why, and what it would cost to drop.
- **`copilot/tools/upstream_delta.py`** - reports the delta, whether upstream has since
  touched the same files, and whether a merge would conflict today. `--check` exits 1 on
  an unregistered file.
- **`copilot/tests/test_upstream_delta.py`** - 5 tests. Fails on an unregistered file, a
  stale row, a path typo, or a maintainer-owned path being touched. Skips cleanly when no
  `upstream` remote is configured, so a fresh clone does not fail.

### Measured

- Current delta: **2 files, 52 lines**, both in the IB adapter.
- `historical/client.rs` is **quiet** - upstream has not touched it since our merge base.
- `data/core.rs` is **churning**: upstream changed +160/-64 against our +16/-12, and
  **a merge already conflicts** one sync in, in the `subscriptions` field declaration.
  Upstream moved the adapter to `parking_lot` and standardised task lifecycles
  underneath our re-keying.

### Fixed

- The delta check originally compared only `base..HEAD`, so it stayed silent through an
  entire editing session and only fired after a change was committed. It now counts
  working-tree, index and untracked changes too - the useful moment to be told a file
  needs a row is while it is being edited.
- The register parser read every table in the document, registering `quiet`, `touched`
  and `churning` from the risk legend as if they were paths. It now reads only rows under
  `## The register`. Caught by `test_registered_paths_exist`.

## 2026-09-01 (docs)

### Changed

- **`docs/ROADMAP.md` restructured around the kill chain.** It is now the central record:
  eleven stages ordered as the trade travels, then every open item anchored to a stage and
  grouped by what unblocks it (decision / ready to build / market session / spend /
  deferred). The detail sections are retitled by stage and reordered to match, replacing a
  numbered backlog whose numbers had gone stale and were referenced from three places.

### Corrected

- **Stage 02 is empty and nothing recorded it.** The only `Strategy` subclasses in the tree
  are a test fixture and the risk guard, so the gate, engine, sizing and breakers all
  currently have nothing to evaluate. The status tables read as near-complete because every
  stage that *has* a component was marked ready.
- **The paper-run prerequisites were incomplete.** Both listed items - a multi-symbol spread
  calibration and a ported walk-forward gate - have landed, which read as "unblocked". The
  list never named a strategy, without which there is nothing to validate or deploy.

## 2026-09-01 (later)

### Added

- **`copilot/data/marketstack.py`** - Marketstack EOD client and normalizer, ported
  from trade-copilot `services/ingestion/marketstack.py`. Pages to exhaustion, retries
  transient failures only, and rejects rows with the reason they failed rather than
  dropping them.
- **`copilot/data/calendar.py`** - rule-based US equity trading calendar. No new
  dependency; validated against 15,851 real vendor rows rather than by assertion.
- **`copilot/data/catalog.py`** - Nautilus `Bar` conversion and `ParquetDataCatalog`
  read/write, with an exactness guard on every converted price.
- **`copilot/data/backfill.py`** - operator CLI. Reports the rejection breakdown and
  fails the run past `--max-rejection-ratio` rather than writing a partial history.
- **74 tests** across ingestion, the calendar and the catalog bridge.

### Measured

- **15,849 bars written**, AAPL/MSFT/SPY, 2005-01-03 to 2025-12-31. 15,851 fetched,
  2 rejected (0.013%).
- **The vendor's `adj_*` OHLC set is unusable.** 3,553 rows carry an incoherent bar and
  1,751 carry null fields; the raw set has zero of either. AAPL 2022-11-03 reports
  `adj_close` 138.65 under an `adj_low` of 138.75.
- **The raw set is already split-adjusted**, so it is safe to use: AAPL's 4:1 split of
  2020-08-31 reads **+3.39%** off disk, not -75%.
- **Marketstack emits bars on days the market was closed.** SPY has full bars for
  Thanksgiving 2023 and Good Friday 2024.
- **`price_currency` is unreliable** - MSFT's single continuous USD series is tagged
  `USD`, `usd`, `EUR` and nothing, across 5,283 rows.
- **Price precision 4 is exact**: no more than four decimal places in 63,404 values.

### Fixed

- **The Nautilus replay scored one trade per run.** `ReplayVenue` defaulted to
  `OmsType.NETTING`, under which Nautilus reuses one position id per instrument and
  strategy; `cache.positions_closed()` then keeps a single object and only the last
  round trip is scored. On 60 bars of real AAPL: NETTING 1 trade, HEDGING 30. Every
  walk-forward fold returned "selected nothing" with no error. Default is now
  `HEDGING`, and `ReplayVenue.name` defaults to the instrument's own venue.
  The old test asserted only that trades were non-empty, which one trade satisfies;
  the replacement asserts an exact count and a second test pins the netting behaviour.
- A first version of the trading calendar closed 31 December when 1 January fell on a
  Saturday. The exchanges stay open; the fixture test caught it.

### Verified

- End to end on real market data: catalog -> Nautilus replay -> purged walk-forward.
  1,000 AAPL bars, five folds, 62 scored trades per fold, full tearsheets. The driving
  strategy is the suite's coin-flip, so the verdict is meaningless - the machinery is
  what was proven.

## 2026-09-01

### Added

- **`copilot/calibration/spread_snapshot.py`** - measures real quoted spreads from
  Interactive Brokers and writes a JSON record per run. Read-only: it constructs no
  execution client, so it cannot place an order. Configurable symbols, duration,
  market data type and symbology.
- **`copilot/risk/protections.py`** - account-wide rolling-window circuit breakers
  ported from trade-copilot `libs/risk/protections.py` (ADR-0025): consecutive
  stop-outs, peak-to-trough realised drawdown, cooldown, longest-breach-wins. Pure
  functions, no I/O or clock. Contract types converted from pydantic to stdlib
  dataclasses so the overlay adds no dependency.
- **`copilot/risk/guard.py`** - wires the breakers into a running node. Cancels
  working orders account-wide, flattens configured instruments, publishes a signal.
  Reactive rather than engine-enforced; see the `set_trading_state` decision in ROADMAP.
- **`copilot/validation/types.py`** - `DailyBar`, `ClosedTrade`, `BacktestRunResult`
  and `expectancy_r`, vendored so the overlay does not depend on trade-copilot being
  on the path.
- **`copilot/validation/nautilus_replay.py`** - a `Replay` backed by a Nautilus
  `BacktestEngine`, matching the signature the trade-copilot gate injects. Includes
  `RiskAmountRegistry`, the contract by which a strategy reports what each position
  put at risk.
- **30 tests** across the ported logic and the replay plumbing.

### Measured

- AAPL median quoted spread **0.6381 bps full / 0.3190 bps per side** over 654s of
  delayed IB quotes (111 usable), against an incumbent model of 5 bps/side - roughly
  **15.7x** overstated. Distribution has a tail: p95 2.87 bps full, max 5.74.
- A first 148s / 24-quote run gave 1.2753 bps and 7.8x, so **sample size moved the
  result by 2x**. The short run is superseded and its artifact discarded; do not set
  a coefficient from a short run.

### Fixed

- `nautilus_replay` mapped every closed position to `SHORT`. `Position.is_long` reads
  the *current* quantity, which is zero once a position closes; `Position.entry`
  records the opening side and stays valid. Caught by the integration test.

### Entitlements

- IB market data release forms completed. Delayed quotes now work for MSFT and SPY,
  which previously returned no data and no error. Realtime quotes and US equity
  historical bars are unchanged - still no data and still IB 2188 respectively, so a
  paid subscription is still required for both. Recheck after the next trading session
  before concluding a purchase has not landed.

### Corrected

- The claim that a failed subscription tears down sibling subscriptions is **withdrawn**.
  The code gives each subscription its own `child_token()` and logs per-task errors without
  propagating them, so that mechanism does not exist. It was inferred from correlated
  observations and stated with more confidence than the evidence supported. The underlying
  observation - quotes stopping when an unpermissioned subscription is added to the same
  instrument - remains real and unexplained, most likely IB-side.

### Known issues, not fixed

- Two IB adapter defects, both now **fixed and verified**: `request_ticks` ignored its
  timeout and hung, and the subscriptions map was keyed by instrument alone so one
  subscription type silently evicted another. A remaining collision between two bar types
  on one instrument is recorded but out of scope.
- No route to US equity historical bars. All 16 request shapes tried return IB 2188,
  so the backtest evidence base must come from another vendor.

### Repo hygiene

- `trade-copilot/` added to `.git/info/exclude` - local only, so it produces no diff
  against upstream. It is a 376MB nested git repository containing a real `.env`; it
  must never be committed.
