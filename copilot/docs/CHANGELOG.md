# Changelog

Overlay-local. Upstream NautilusTrader releases are not tracked here.

## 2026-09-01 (upstream delta policy)

### Changed

- **The prime directive is now "every upstream change is registered", not "change zero
  upstream files".** Upstream changes are permitted where they are worth it. The
  guarantee they replaced was self-enforcing; a register is not, so it is enforced by a
  test instead.

### Added

- **`copilot/docs/UPSTREAM_DELTA.md`** — the register. One row per upstream file this
  fork changes: what, why, and what it would cost to drop.
- **`copilot/tools/upstream_delta.py`** — reports the delta, whether upstream has since
  touched the same files, and whether a merge would conflict today. `--check` exits 1 on
  an unregistered file.
- **`copilot/tests/test_upstream_delta.py`** — 5 tests. Fails on an unregistered file, a
  stale row, a path typo, or a maintainer-owned path being touched. Skips cleanly when no
  `upstream` remote is configured, so a fresh clone does not fail.

### Measured

- Current delta: **2 files, 52 lines**, both in the IB adapter.
- `historical/client.rs` is **quiet** — upstream has not touched it since our merge base.
- `data/core.rs` is **churning**: upstream changed +160/-64 against our +16/-12, and
  **a merge already conflicts** one sync in, in the `subscriptions` field declaration.
  Upstream moved the adapter to `parking_lot` and standardised task lifecycles
  underneath our re-keying.

### Fixed

- The delta check originally compared only `base..HEAD`, so it stayed silent through an
  entire editing session and only fired after a change was committed. It now counts
  working-tree, index and untracked changes too — the useful moment to be told a file
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
- **The paper-run prerequisites were incomplete.** Both listed items — a multi-symbol spread
  calibration and a ported walk-forward gate — have landed, which read as "unblocked". The
  list never named a strategy, without which there is nothing to validate or deploy.

## 2026-09-01 (later)

### Added

- **`copilot/data/marketstack.py`** — Marketstack EOD client and normalizer, ported
  from trade-copilot `services/ingestion/marketstack.py`. Pages to exhaustion, retries
  transient failures only, and rejects rows with the reason they failed rather than
  dropping them.
- **`copilot/data/calendar.py`** — rule-based US equity trading calendar. No new
  dependency; validated against 15,851 real vendor rows rather than by assertion.
- **`copilot/data/catalog.py`** — Nautilus `Bar` conversion and `ParquetDataCatalog`
  read/write, with an exactness guard on every converted price.
- **`copilot/data/backfill.py`** — operator CLI. Reports the rejection breakdown and
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
- **`price_currency` is unreliable** — MSFT's single continuous USD series is tagged
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
  strategy is the suite's coin-flip, so the verdict is meaningless — the machinery is
  what was proven.

## 2026-09-01

### Added

- **`copilot/calibration/spread_snapshot.py`** — measures real quoted spreads from
  Interactive Brokers and writes a JSON record per run. Read-only: it constructs no
  execution client, so it cannot place an order. Configurable symbols, duration,
  market data type and symbology.
- **`copilot/risk/protections.py`** — account-wide rolling-window circuit breakers
  ported from trade-copilot `libs/risk/protections.py` (ADR-0025): consecutive
  stop-outs, peak-to-trough realised drawdown, cooldown, longest-breach-wins. Pure
  functions, no I/O or clock. Contract types converted from pydantic to stdlib
  dataclasses so the overlay adds no dependency.
- **`copilot/risk/guard.py`** — wires the breakers into a running node. Cancels
  working orders account-wide, flattens configured instruments, publishes a signal.
  Reactive rather than engine-enforced; see the `set_trading_state` decision in ROADMAP.
- **`copilot/validation/types.py`** — `DailyBar`, `ClosedTrade`, `BacktestRunResult`
  and `expectancy_r`, vendored so the overlay does not depend on trade-copilot being
  on the path.
- **`copilot/validation/nautilus_replay.py`** — a `Replay` backed by a Nautilus
  `BacktestEngine`, matching the signature the trade-copilot gate injects. Includes
  `RiskAmountRegistry`, the contract by which a strategy reports what each position
  put at risk.
- **30 tests** across the ported logic and the replay plumbing.

### Measured

- AAPL median quoted spread **0.6381 bps full / 0.3190 bps per side** over 654s of
  delayed IB quotes (111 usable), against an incumbent model of 5 bps/side — roughly
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
  historical bars are unchanged — still no data and still IB 2188 respectively, so a
  paid subscription is still required for both. Recheck after the next trading session
  before concluding a purchase has not landed.

### Corrected

- The claim that a failed subscription tears down sibling subscriptions is **withdrawn**.
  The code gives each subscription its own `child_token()` and logs per-task errors without
  propagating them, so that mechanism does not exist. It was inferred from correlated
  observations and stated with more confidence than the evidence supported. The underlying
  observation — quotes stopping when an unpermissioned subscription is added to the same
  instrument — remains real and unexplained, most likely IB-side.

### Known issues, not fixed

- Two IB adapter defects, both now **fixed and verified**: `request_ticks` ignored its
  timeout and hung, and the subscriptions map was keyed by instrument alone so one
  subscription type silently evicted another. A remaining collision between two bar types
  on one instrument is recorded but out of scope.
- No route to US equity historical bars. All 16 request shapes tried return IB 2188,
  so the backtest evidence base must come from another vendor.

### Repo hygiene

- `trade-copilot/` added to `.git/info/exclude` — local only, so it produces no diff
  against upstream. It is a 376MB nested git repository containing a real `.env`; it
  must never be committed.
