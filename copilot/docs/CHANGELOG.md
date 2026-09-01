# Changelog

Overlay-local. Upstream NautilusTrader releases are not tracked here.

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
  Reactive rather than engine-enforced; see ROADMAP item 5.
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
