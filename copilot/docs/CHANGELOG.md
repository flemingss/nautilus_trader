# Changelog

Overlay-local. Upstream NautilusTrader releases are not tracked here.

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
