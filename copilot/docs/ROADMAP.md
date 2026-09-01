# Roadmap and state

The central record for this fork: what is built, what is deliberately deferred, and what
the paper run needs. Written to be honest about the difference between "tested" and
"proven in the market".

**The kill chain is the organising frame.** Every open item is anchored to a stage of it,
and the detail sections are ordered by stage, so a reference stays true as the backlog
moves. Read "The kill chain" and "Open work" first; everything under *Detail* is the
working record behind them.

Companion documents, each with a distinct job:

| Document                                 | Job                                                        |
| ---------------------------------------- | ---------------------------------------------------------- |
| [`decisions/`](decisions/README.md)      | Why things are the way they are. Immutable once accepted.  |
| [`MAINTENANCE.md`](MAINTENANCE.md)       | How we draw from upstream, and what we own in the runtime. |
| [`UPSTREAM_DELTA.md`](UPSTREAM_DELTA.md) | Every upstream file we change and what it costs to drop.   |
| [`CHANGELOG.md`](CHANGELOG.md)           | What changed and what was measured.                        |
| [`PAPER_CAMPAIGN.md`](PAPER_CAMPAIGN.md) | Getting operational on paper: the gates, and the log.      |

A rendered view of the same two sections is published at
<https://claude.ai/code/artifact/80882028-e15c-4247-a2f4-e08cf2b2ef20>. This file is the
source of truth; regenerate the page from it rather than the other way round.

## Where this came from

Two projects are being fused:

- **NautilusTrader** - a strong backtest engine, order model, live node and
  reconciliation. Its gaps: no screening, no walk-forward or parameter search, and a
  risk engine limited to per-order notional and rate limits.
- **trade-copilot** - a HITL signal advisor with an institutional-grade validation
  gate and account-wide risk breakers. Its gaps: a crude cost model, a small evidence
  base, and no intraday data.

Each side covers the other's gaps almost exactly. Screening is the only stage neither
covers, and it is pinned.

## The kill chain

The organising frame for everything below: the eleven stages between finding a trade and
banking it, ordered as the trade travels, so a break shows where everything downstream
stalls.

| #   | Stage                | Covered by                    | State                                                     |
| --- | -------------------- | ----------------------------- | --------------------------------------------------------- |
| 00  | Historical data      | `copilot/data`                | **Ready (daily).** 105,398 bars, 20 symbols, 2005-2025    |
| 01  | Screening / universe | -                             | **Pinned**, out of repo by decision                       |
| 02  | Research / strategy  | `copilot/strategies`          | **Ready.** Gap fade ported; first verdict below           |
| 03  | Backtest engine      | Nautilus `BacktestEngine`     | Ready. Fill, fee and latency models                       |
| 04  | Validation gate      | `copilot/validation`          | Ready. Proven end to end on real history                  |
| 05  | Position sizing      | `copilot/risk/sizing`         | Ready. Risk-based, floored                                |
| 06  | Risk limits          | `copilot/risk/protections`    | **Ready.** Engine-level halt via the `RiskEngine` binding |
| 07  | Orders / exits       | Nautilus execution            | Ready. 9 order types, brackets, trailing                  |
| 08  | Live deployment      | Nautilus `LiveNode`           | **Stages 1-6 pass** 2026-09-01                            |
| 09  | Monitoring           | Nautilus analysis + tearsheet | Ready                                                     |
| 10  | Cost calibration     | `copilot/calibration`         | **Measured, not wired**                                   |

### Stage 02 - the gap fade, and the first real verdict

`copilot/strategies/gap_reversal.py` ports trade-copilot's overnight-gap fade (V1-32).
Chosen over the RSI reversal and the trend rule for a **structural** reason recorded in
the original: V1-31 found that a 252-bar training window with a 30-trade eligibility
floor needs a signal on ~12% of trading days, and that ANDing a quality filter onto an
RSI(2) trigger dropped every configuration below it - no evaluable folds, so no verdict
at all. A gap's trigger *is* its quality measure, so there is nothing to AND on and
nothing to dilute, and the original's search values were picked so every one clears the
floor.

**First verdict, reproducible from a commit:**

```bash
python -m copilot.strategies.validate --all --write
```

| Symbol | Folds passed | Mean OOS expectancy | Trades | Majority |
| ------ | ------------ | ------------------- | ------ | -------- |
| AAPL   | 20 / 39      | **+0.049150 R**     | 612    | pass     |
| MSFT   | 24 / 39      | **+0.090638 R**     | 571    | pass     |
| SPY    | 22 / 38      | **+0.063737 R**     | 609    | pass     |

Every run files a record under `copilot/strategies/verdicts/` carrying the activation, the
search space as declared at the time, the seeded parameters and the fold geometry, so a
number can be tied to an experiment rather than to a memory of one.

**Read this as a first result, not a green light.** Three things qualify it, and two of
them are flags in the record itself:

1. **`costs_modelled: false`.** No fee or fill model is supplied, so the engine charges
   neither commission nor spread. Wiring the measured spreads in is the last open work
   item, and trade-copilot's own analysis names the cost model as the number that decides
   every verdict.
2. **`holdout_spent: false`.** This is walk-forward, which is repeatable. The single-use
   out-of-sample has never been spent, and spending it is a deliberate separate act.
3. **Entry fills at the signal bar's close**, not the next open as in the original - a
   different and slightly more favourable execution assumption, documented in the module.
   The two systems' verdicts on this premise are not comparable.

Trade counts land at 21-29 per 252 bars against the original's 30-37, because this port
holds one position at a time and a run of gap days therefore blocks its own re-entries.

214 tests, all passing: `PYTHONPATH=. pytest copilot/tests/ -q`.

### Stage 08 - what a paper run actually needs

**No paper node exists.** The only IB connection code in the overlay is
`calibration/spread_snapshot.py`, which builds a **data-only** `LiveNode` - one data client,
no execution client, no account, no strategy. Everything from stage 00 to 07 is research
plumbing that has never had a broker on the other end of it.

That matters less than it sounds, because [`playbook/OPERATIONS.md`](playbook/OPERATIONS.md)
splits the work in two and only one half is blocked:

> Broker-integration testing and strategy forward testing are different activities.
> Controlled connectivity, read-only reconciliation and minimum-size order-lifecycle tests
> may begin before a strategy passes the research gate, and they validate no edge whatever.

**Paper stages 1 through 6 are integration testing.** They need a paper account, delayed
quotes and a connection. They do **not** need a market-data subscription, settled cash,
margin, a chosen spread coefficient, or a strategy anyone believes in - they answer "does
the machine behave", and the answer is currently unknown.

**Paper stages 7 and 8 are forward testing.** They need a frozen candidate that passed the
research gate. We do not have one: the gap fade is negative at the target account size
([ADR-0009](decisions/0009-cost-is-modelled-at-the-target-account-size.md)) and no holdout
has been carved out. Forward-testing it would measure a premise already known to lose.

The campaign, its gates and its evidence log live in [`PAPER_CAMPAIGN.md`](PAPER_CAMPAIGN.md).

What stages 1 to 6 need built, none of it blocked:

| Piece                           | State                                                                                                                                                                           |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Execution client wiring         | **Built**, `live/node.py`. First execution client in the overlay.                                                                                                               |
| A paper node builder            | **Built**, `live/session.py`. Paper and live differ by a port number on this deployment shape, so two independent checks must agree before a session is called paper. 17 tests. |
| An orders-disabled mode         | **Built.** Strategies run normally and the risk engine is halted, so the real path is exercised and every order is denied inside the engine rather than never submitted.        |
| Guard handle taken before start | **Built.** `build_paper_node` returns it rather than leaving the caller to find it.                                                                                             |
| A preflight check script        | **Built, run, passing.** Stages 1 and 2 both pass against the paper account.                                                                                                    |
| Map catalog ids to broker ids   | **Built**, `live/symbology.py`. Research `AAPL.XNAS` to broker `AAPL=STK.SMART`, with the routing table listed rather than defaulted so an unmapped venue raises.               |
| Failure injection               | **Open.** Stage 6: stale data, disconnect, reject, reconciliation mismatch. The reconciliation gap below is one of these, found early.                                          |

## Open work, grouped by what unblocks it

Thirteen items. Grouped by blocking condition rather than by component, because that is
the axis that decides what can move today. A final group records the standing carrying
cost of the upstream changes this fork already holds - not work, but the bill that
arrives at every sync.

### Waiting on the account (3)

Recorded 2026-09-01. **The operator's to close, not the repository's.** Three items in the
groups below inherit their block, which is why they sit first.

| Item                                                              | Stage  | The action                                                                                                                                                                                                                                                              |
| ----------------------------------------------------------------- | ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Clear the IBKR market-data equity minimum**                     | 10     | Market-data subscriptions are gated on account equity and the account is below the bar. Funds have been added; settlement expected **on or after 2026-09-08**. Until then the only US equity quotes available are the complimentary **delayed, non-consolidated** feed. |
| **Resolve margin, or confirm cash is permanent**                  | 05, 06 | The account is **cash**. Cash cannot sell short, so the gap fade's short leg is unavailable at any price, and sizing must come from **settled USD** rather than headline equity.                                                                                        |
| **Confirm settlement and buying-power rules on the real account** | 06     | T+1 is the general US rule, but PREFLIGHT requires it verified with the carrying entity rather than assumed. Decides whether a settled-cash check has to sit in front of order submission.                                                                              |

### Waiting on a decision (2)

Investigated as far as they can be. **No further work is useful until each is called.**

| Item                                    | Stage  | The decision                                                                                                                                    |
| --------------------------------------- | ------ | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| Pick the spread coefficient             | 10     | Median, p75 or p95. The distribution has a real tail, so a median model understates bad days. Should be chosen, not defaulted to.               |
| Buy consolidated US equity data, or not | 00, 10 | Prices confirmed in Client Portal, then buy or skip. Marketstack already covers daily bars, so this is only worth it if intraday comes with it. |

**Resolved 2026-09-02:** the two items that needed a live session are settled - realtime
quotes are still not entitled, and the sibling-subscription stall does not reproduce. Both
are written up under stage 00 and stage 10 below. `spread_snapshot.build_node` runs. `LiveNode.add_actor` is
now exposed to Python, so the calibrator records quotes from committed code and its
snapshots are reproducible from a commit for the first time.

**Resolved 2026-09-01:** which setup ports first (gap fade, chosen on V1-31 evidence), and
that upstream files may be changed. `set_trading_state` moves to
ready-to-build below. The condition attached to the clearance is that every upstream file
this fork touches is tracked, which is what `docs/UPSTREAM_DELTA.md` and
`tools/upstream_delta.py` now do.

### Ready to build, nothing blocking (1)

| Item                               | Stage | Notes                                                                                                                                        |
| ---------------------------------- | ----- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| Make the cost model per-instrument | 10    | SPY and MSFT differ by 4x; a single global `spread_bps` is structurally wrong. Wants the coefficient decision first, or it gets built twice. |

### Charter conflicts, opened 2026-09-01 (3)

Adopting [`CHARTER.md`](CHARTER.md) surfaced four places where the code does not match the
process it is now governed by. One is resolved as
[ADR-0009](decisions/0009-cost-is-modelled-at-the-target-account-size.md); these three are
open.

| Item                                 | Stage | Notes                                                                                                                                                                                                                                                                                    |
| ------------------------------------ | ----- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Carve out the locked holdout**     | 04    | The charter reserves the most recent 15-20%. `walk_forward` runs over the whole history and no holdout exists, while every verdict record carries `holdout_spent: false`, which implies one does. **No verdict from this repository currently has an out-of-sample estimate behind it.** |
| Move entry to the next session       | 02    | The gap fade fills at the signal bar's close. The charter requires next-eligible-session entry. Changing it is a new experiment rather than a fix, so it resets the premise's evidence.                                                                                                  |
| Correct the survivor-biased universe | 00    | The 20-symbol catalog is today's large caps backfilled to 2005, which the charter names as the error to avoid. Needs point-in-time membership and delisted securities.                                                                                                                   |

### Waiting on spend (2)

No code closes these.

| Item                         | Stage | Notes                                                                                                                                  |
| ---------------------------- | ----- | -------------------------------------------------------------------------------------------------------------------------------------- |
| US equity history through IB | 00    | All 16 request shapes return 2188. No client-side workaround. Redundant with Marketstack unless intraday comes with it.                |
| Intraday history             | 00    | Marketstack EOD cannot support anything acting within a session. Databento was preferred and deferred until the system earns its cost. |

### Deferred by decision (2)

**Groom CI for ownership.** Actions is disabled as of 2026-09-02 pending a deliberate
pass over the inherited workflows. The reason CI has never run is diagnosed and recorded
in [`MAINTENANCE.md`](MAINTENANCE.md) under "CI, and why it has never run", so the pass
starts warm: the harden-runner egress allowlist lives in repository variables that did
not travel with the copy, and the workflows still carry upstream's wheel-publication and
release machinery, none of which applies here.

**Tabletop: subscriptions, operations, strategy.** Under way. Operations and strategy
governance are settled and recorded in [`CHARTER.md`](CHARTER.md), the
[playbook](playbook/README.md) and ADRs 0004 through 0009. **Subscriptions remain open**,
and both of the decisions above resolve inside that half.

### Carrying cost, tracked (9 files)

Not work items - the standing bill. Reported by `python -m copilot.tools.upstream_delta`,
with the reasoning for each in `docs/UPSTREAM_DELTA.md`.

| Path                                                           | Ours      | Upstream since base | Risk                    |
| -------------------------------------------------------------- | --------- | ------------------- | ----------------------- |
| `crates/risk/src/python/engine.rs`                             | new file  | n/a                 | **cannot conflict**     |
| `crates/risk/src/python/mod.rs`                                | +2        | untouched           | quiet                   |
| `crates/adapters/interactive_brokers/src/historical/client.rs` | +36 -2    | untouched           | quiet                   |
| `crates/live/Cargo.toml`                                       | +1        | +3                  | touched                 |
| `python/pyproject.toml`                                        | +54       | untouched           | quiet                   |
| `.typos.toml`                                                  | +1        | untouched           | quiet                   |
| `crates/live/src/python/node.rs`                               | +14       | +166 -88            | **churning**            |
| `crates/adapters/interactive_brokers/src/data/core.rs`         | +16 -12   | +160 -64            | **churning, conflicts** |
| `python/nautilus_trader/{live,risk}/__init__.pyi`              | generated | -                   | regenerate, never edit  |

It grew from 2 files to 9 in one session, all to reach `set_trading_state`. Six of the
nine are additive-only or new files, which is the cheapest shape a delta can take; two
sit in files upstream is actively rewriting.

**Nothing is due.** Syncing is on demand only - the fork is deliberately held still while
development is active, so the conflict in `data/core.rs` is a forecast for a sync that has
not been scheduled. Upstreaming the two IB fixes and the `RiskEngine` binding would retire
three entries rather than carry them, and all three are additive capability or straight bug
fixes upstream would plausibly accept - but that opens a review front on someone else's
schedule, so it is deferred on the same reasoning.

## Shortest route to a paper run

**Correction to the prerequisites listed further down this document.** They named a
multi-symbol spread calibration and a ported validation gate. Both have landed, so on
paper the paper run is unblocked. That list was incomplete: it never named a strategy,
and without one there is nothing to validate or deploy.

1. **Pick a setup** and port it, reporting risk through `RiskAmountRegistry`.
2. **Set the cost coefficient** and wire it per instrument, so the gate scores against a
   measured model rather than a placeholder.
3. **Run the gate for real** - in-sample, walk-forward, then the single-use holdout,
   which has never been spent.
4. **Two to four weeks on IB paper** with the guard enabled. This is the first time the
   breakers can fire; they cannot fire in a backtest by design.
5. **Compare realised fills** against the modelled cost and close the loop.

Nothing here goes near live capital.

---

**Detail follows.** Everything below is the working record behind the tables above: what was measured,
what was tried, and what cost time. Anchored to kill-chain stages rather than to a
numbered backlog, so the references stay true as the backlog moves.

## Stage 00 - the Marketstack to catalog bridge

`copilot/data/` fetches Marketstack EOD, gates it, and writes Nautilus bars into a
`ParquetDataCatalog`. Run over AAPL, MSFT and SPY for 2005-2025: 15,851 rows fetched,
**15,849 bars written**, 2 rejected.

### The vendor's adjusted prices are not usable

Marketstack returns two OHLC sets. Measured over 15,851 rows:

| Property                  | `open/high/low/close` | `adj_*`     |
| ------------------------- | --------------------- | ----------- |
| Rows with incoherent OHLC | 12 (0.011%)           | 3,553 (22%) |
| Rows with null fields     | 0                     | 1,751       |
| Back-adjusted for splits  | yes                   | yes         |
| Adjusted for dividends    | no                    | yes         |

AAPL 2022-11-03 reports `adj_close` 138.65 under an `adj_low` of 138.75 - a close
outside its own bar. A backtest fed that fills at a price the bar says never traded.

**Correction.** An earlier version of this section said the raw set had *zero*
incoherent rows. That held over the 15,851 rows first measured; over 105,414 it has
twelve, every one an open a few cents outside the day's range (GOOGL 2025-12-29 opens
at 314.52 against a high of 314.02). 0.011% against 22% leaves the choice unchanged,
and the gate rejects them either way - but the claim was stronger than the evidence
supported once the universe widened.

So the overlay stores the **raw** set, which is the reverse of the obvious choice. It is
safe because the vendor has *already back-adjusted it for splits*: AAPL's close on
2020-08-28 is reported as 124.8075, the pre-split 499.23 divided by the 4:1 split that
settled on the 31st. Read back off disk, the split day moves **+3.39%**, not -75%. What
the raw set lacks is dividend adjustment, which understates total return by the yield
without manufacturing a discontinuity a strategy could mistake for a signal. `dividend`
and `split_factor` are carried per row so a dividend-adjusted series can be derived
later from a coherent base.

### The vendor emits bars on days the market was shut

SPY comes back with a complete-looking bar for **Thanksgiving 2023-11-23** and **Good
Friday 2024-03-29** - plausible OHLC, nine-figure volume, nothing marking them as
phantom. A phantom bar lets a strategy enter, exit and be *scored* on a day no order
could have been placed.

`copilot/data/calendar.py` is a rule-based US equity calendar, written rather than
imported because the overlay adds no dependency. It is validated against the data, not
by assertion: over 2005-2025 it reproduces AAPL's and MSFT's session sets exactly - 5,283
sessions, zero extra, zero missing - and flags only SPY's two phantom rows.

That test earned its keep immediately. The first version closed 31 December when
1 January fell on a Saturday, following the federal observance rule; the exchanges stay
open, and all three symbols traded on 2010-12-31 and 2021-12-31.

### Other findings worth keeping

- **`price_currency` is unreliable.** MSFT returns 18 rows tagged `EUR`, 59 tagged
  lowercase `usd`, and 5,023 untagged - all the same continuous USD series (513.71 tagged
  USD, then 512.50 tagged EUR the next session). Collected for reporting, never used to
  decide what is stored.
- **Precision 4 is exact, and measured.** Across 63,404 price values the vendor never
  returns more than four decimal places. The conversion compares every value against its
  source and raises rather than storing a rounded price.
- **Never verify a `Quantity` through `as_double()`.** AAPL's 1,020,062,400-share session
  on 2005-02-02 round-trips as 1020062399.9999999. An exactness guard written against the
  float rejected a volume the catalog stores perfectly well; the check goes through `str`.
- **v2 reports `total` truthfully**, which v1 capped at `limit`. Paging to exhaustion is
  kept regardless - it is correct either way - and `total` is now a cross-check, so a run
  that ends early with rows outstanding fails instead of writing a partial history that
  the next run reads as complete.

The catalog lives at `~/.nautilus_copilot/catalog`, outside the repository: a parquet
store inside the tree would need a `.gitignore` entry, and `.gitignore` is an upstream
file this fork does not touch.

## Stage 04 - the `Replay` seam

The trade-copilot gate takes its replay as an argument:

```python
Replay = Callable[[Sequence[DailyBar], StrategyParameters], BacktestRunResult]
walk_forward(bars, grid, replay=...)
```

That injection point is why the fusion is tractable: the methodology is
engine-agnostic. `copilot/validation/nautilus_replay.py` supplies a replay backed by a
Nautilus `BacktestEngine`, so the same gate can be scored against Nautilus's
volume-, size- and probability-sensitive fill models instead of a flat proxy.

**The strategy must report what it risked.** Nautilus records the fill, not the stop
that sized it, so R cannot be derived from a position alone. Strategies register
per-position risk through `RiskAmountRegistry`; a missing record raises rather than
silently scoring `r_multiple == 0`, which would make the gate report "no edge"
everywhere.

### The replay scored one trade per run - fixed

Found by running the gate on real AAPL history for the first time: all five folds came
back `in_sample_selected_nothing`, with no error anywhere.

`ReplayVenue` defaulted to `OmsType.NETTING`. Under netting, Nautilus reuses **one
position id per instrument and strategy**, so `cache.positions_closed()` holds a single
position object that is reopened and closed over and over, and only the final round trip
survives to be scored. `RiskAmountRegistry` is keyed by position id and aliased the same
way.

Measured on 60 bars of real AAPL with a strategy that alternates in and out every bar:

| OMS       | scoreable trades |
| --------- | ---------------- |
| `NETTING` | 1                |
| `HEDGING` | 30               |

The consequence was not a reporting detail. `expectancy_r` over a single trade is noise,
the `min_trades` floor then rejects every candidate, and the gate returns "selected
nothing" on every fold while looking like it ran correctly. Any verdict it produced
would have been meaningless.

The default is now `HEDGING`, and `ReplayVenue.name` defaults to the instrument's own
venue rather than `"SIM"` - a mismatched venue is rejected by the engine, so guessing a
name could only ever be wrong.

**Why it survived the test suite:** the existing test asserted `result.trades` was
non-empty. One trade satisfies that. The replacement asserts an exact count (four round
trips over eight bars) and a second test pins the netting behaviour explicitly, so the
cost is visible to anyone who sets it deliberately.

## Stage 06 - risk breakers, and what they actually enforce

Ported from trade-copilot ADR-0025. Both breakers are pure functions over closed
trades, so they are tested against hand-built losing streaks rather than a live
account.

- Consecutive stop-outs (default 4), streak must be current
- Peak-to-trough realised drawdown (default 6% in 14 days)
- 3-day cooldown, longest-running breach wins

**Enforcement is reactive, not preventive.** Nautilus has exactly the right primitive

- `TradingState` with `HALTED`/`REDUCING`, enforced natively in the Rust risk engine -
but `set_trading_state` has no pyo3 binding and no production caller, so Python cannot
reach it. The guard therefore cancels working orders account-wide, flattens configured
instruments, and publishes a signal. A strategy that keeps submitting will keep
getting orders accepted between evaluations.

Closing that gap is the `set_trading_state` decision above, and is the single change
that would touch upstream files.

## Stage 10 - spread calibration

trade-copilot's `PaperFillConfig.spread_bps` is **5 bps per side**, described in its
own source as "a deliberately conservative ceiling ~10x the quoted spread, pending a
live-quote snapshot". Its `SYSTEM.md` §14 names this the top blocker, ahead of premise
supply, because the modelled cost is what decides every verdict.

Measurement, AAPL over 654s of IB quotes (111 usable, 18 rejected as crossed/locked):

|                                 | full spread (bps) | per side (bps) |
| ------------------------------- | ----------------- | -------------- |
| median                          | 0.6381            | **0.3190**     |
| p75                             | 1.2748            | 0.6374         |
| p95                             | 2.8685            | 1.4343         |
| max                             | 5.7374            | 2.8687         |
| incumbent                       | -                 | 5.0            |
| **overstatement at the median** | -                 | **~15.7x**     |

**Sample size moved this number by 2x, which is itself the finding.** A first 148s run
over 24 quotes gave 1.2753 bps full / 7.8x; the 654s run over 111 quotes gives 0.6381
bps / 15.7x. Do not set a coefficient from a short run.

Two caveats remain:

- **Delayed data.** The account has no realtime US equity subscription, so these are
  delayed quotes - a genuine bid/ask, but updating slowly and possibly wider than the
  realtime NBBO. This is an *upper bound*, the conservative direction.
- **The distribution has a tail.** Median 0.64 bps but p95 2.87 and max 5.74. A cost
  model set at the median will understate the bad days. Choosing the coefficient is a
  policy decision - the median is the honest central estimate, p75 or p95 the
  defensible conservative ones - and should be made explicitly rather than by
  defaulting to whichever number is at hand.

### The first reproducible snapshot, 2026-09-02

With `add_actor` exposed, the calibrator ran from committed code for the first time: 25
minutes, delayed quotes, all three names in one session
(`calibration/out/spread_snapshot_20260901T154744Z.json`).

| Full spread, bps of mid | samples | median     | p75    | p95    |
| ----------------------- | ------- | ---------- | ------ | ------ |
| AAPL                    | 251     | 1.2241     | 1.5312 | 3.9805 |
| MSFT                    | 301     | 1.9964     | 2.3970 | 3.5932 |
| SPY                     | 248     | **0.2618** | 0.3928 | 1.0476 |

Two things this adds to the 2026-08-31 numbers above. **The cross-name spread is the
stable fact**: MSFT quotes ~8x wider than SPY in every run, which is the case for a
per-instrument coefficient regardless of which percentile is chosen. **The day-to-day
movement is not noise to average away**: AAPL's median doubled against 08-31 (1.22 vs
0.64) while SPY's did not move (0.26 both days), so a coefficient set from any single
session inherits that session. The 5 bps per-side incumbent remains 4x-38x conservative
depending on the name.

### Entitlement change, 2026-09-01

IB market data **release forms** were completed. Retested immediately; the effect is
partial and worth recording precisely, because it changes what is calibratable but not
what is backtestable.

|                           | before forms          | after forms       |
| ------------------------- | --------------------- | ----------------- |
| AAPL delayed quotes       | works                 | works             |
| MSFT / SPY delayed quotes | **no data, no error** | **works**         |
| Any realtime quotes       | no data               | **still no data** |
| US equity historical bars | IB 2188               | **still IB 2188** |
| Index (`^SPX`), futures   | IB 2188               | still IB 2188     |
| Forex (IDEALPRO)          | full                  | full              |

So the forms unlocked **delayed quotes across the US equity universe** - the earlier
MSFT/SPY silence was an entitlement gap, not the adapter bug it resembled. Realtime
streaming and historical bars still need a paid subscription, which the forms alone do
not grant. IB subscriptions also typically activate at a trading-day boundary, so
recheck after the next session before concluding a purchase has not landed.

Practical effect: multi-symbol spread calibration is now possible, which was a stated
prerequisite for the paper run. The backtest evidence base is unchanged - there is
still no route to US equity history through IB.

**Rechecked 2026-09-01 after the session close: unchanged.** AAPL, MSFT, SPY and ^SPX
all still return IB 2188 for historical bars under both REALTIME and DELAYED; forex
still returns bars normally. Release forms do not grant historical data, and a paid
subscription is required. `copilot/calibration/entitlements.py` runs this check.

**Realtime quote entitlement, settled 2026-09-02 inside a live session: still absent.**
`spread_snapshot` recorded **zero** usable quotes across AAPL, MSFT and SPY over 107s under
`REALTIME`, and **55** across the same three over 106s under `DELAYED` two minutes later.
The delayed run is what makes the realtime run mean anything - on its own, zero quotes is
indistinguishable from a broken subscription, which is why the earlier closed-session
attempt proved nothing and this one does.

`entitlements.py` probes historical bars only, which is not what this question was about.
Its docstring now says so and points at the two-run procedure above.

### Why 2188 happens, and what would fix it

The account's complimentary feed is **"US Real-Time Non Consolidated Streaming
Quotes"** - IB's free IEX-sourced feed. IB's own API documentation states that
historical data carries *the same subscription requirement as streaming top-of-book*,
and that **a SMART-routed historical request requires subscriptions to every exchange
the instrument trades on**. A non-consolidated (single-venue) entitlement cannot
satisfy that for a name like AAPL, which is exactly what 2188 reports.

So the fix is **consolidated** US equity data - the Network A (NYSE/CTA), Network B
(NYSE American) and Network C (NASDAQ/UTP) tapes, which IB normally sells as a value
bundle plus a streaming add-on. Prices were not verifiable from here (the IB pricing
page returns HTTP 403 to automated fetches) and must be confirmed in Client Portal.

**Answered 2026-09-02: a directed-exchange request is not satisfied either.**
`AAPL=STK.IEX` and `AAPL=STK.ISLAND` both return 2188 under `REALTIME` and `DELAYED`, the
same as the SMART-routed request. So the hoped-for free route to some history does not
exist: the non-consolidated entitlement does not cover historical bars even aimed at the
single venue it does cover. Consolidated data remains the only route, and it is a purchase.

### Blocked 2026-09-01: IB error 162

All historical requests - **including forex, which had worked 25 minutes earlier** -
now fail with:

```text
[162] Historical Market Data Service error message:
Trading TWS session is connected from a different IP address
```

This is a session fault, not an entitlement one: the connection succeeds and contract
resolution still works, only the historical service refuses. It appeared after the IB
web portal was accessed while TWS was running. **Do not read any 2188 result taken
while 162 is active** - the two are unrelated failures and conflating them will produce
a wrong conclusion about entitlements.

**Confirmed and cleared 2026-09-01 by logging out of the IB web session.** Forex
historical went straight back to returning bars and AAPL returned to the honest 2188,
with no TWS restart needed.

**Operating rule: IB allows one active session per login.** Opening Client Portal,
Account Management, or the mobile app while TWS is running can displace the API's
historical data service and produce 162, even though the socket stays connected and
contract resolution keeps working. Do not browse the IB website during a data run.
When 162 appears, log out of the web session first - that alone is usually enough.

Re-run `entitlements.py` after clearing it, before trusting any data verdict.

Three-symbol measurement (delayed, 846s, 139-150 usable quotes each):

| symbol | n   | median full (bps) | per side | p95 full | vs 5 bps/side        |
| ------ | --- | ----------------- | -------- | -------- | -------------------- |
| SPY    | 149 | 0.3917            | 0.1958   | 1.1752   | **25.5x** overstated |
| AAPL   | 139 | 0.6375            | 0.3188   | 2.2284   | 15.7x                |
| MSFT   | 150 | 1.5662            | 0.7831   | 3.5203   | 6.4x                 |

**AAPL reproduces to four decimal places** across two independent runs (0.6381 over
654s, 0.6375 over 846s), which is the first evidence that the measurement is stable
rather than a sampling artefact.

The spread differs by **4x between SPY and MSFT**, so a single global `spread_bps` is
the wrong shape for the model - it should be per-instrument. That conclusion is
structural and does not depend on sample size.

### Calibrator shutdown behaviour

An interrupted run *does* write its report, but only after the node finishes unwinding,
which took minutes on an 846s run. A check made immediately after signalling therefore
looks like data loss when it is not. Worth a signal handler that snapshots accumulated
state promptly, so an operator can interrupt and see results without waiting on node
teardown - but no samples are actually lost today.

## The `set_trading_state` decision, in detail

Now that the toolchain exists this is no longer blocked on tooling, but it is not a small
binding either, and the shape matters more than the code.

What the investigation found:

- The kernel holds `risk_engine: Rc<RefCell<RiskEngine>>` with a public accessor, so a
  same-thread caller can reach it.
- `LiveNodeHandle` is the thread-safe control surface, but it **cannot** hold the risk
  engine: `Rc` is not `Send`, and the message bus is thread-local too.
- `PyLiveNode` can reach the kernel, but its borrow is unavailable while a hosted run owns
  the node - which is exactly when a halt would be wanted.
- `TradingCommand` has no variant for trading state, and adding one touches the enum and
  every match arm across the execution path.
- A Python `Strategy` has no message bus access at all, so the guard cannot send a command
  even if one existed.

The least invasive option that would actually work is an additive message bus endpoint -
`MessagingSwitchboard` entries are just named strings with a `OnceCell`, so adding
`risk_engine_set_trading_state` and registering a handler is contained. It still needs a
way for a Python component to send to an endpoint.

That is three upstream files including `crates/common`, which is a larger commitment than
the two adapter fixes.

**Cleared 2026-09-01.** Upstream changes are permitted, subject to registration in
`docs/UPSTREAM_DELTA.md`. This would become the fork's largest single delta and the only
one in `crates/common` - a crate far more central than the IB adapter, so more exposed to
upstream churn. Build it as one minimal commit, register every file it touches, and keep
the surface additive: a new switchboard endpoint and handler alongside the existing ones,
never a change to an existing signature. Until it lands, the guard stays reactive and
says so.

## Bugs found upstream - fixed

Both are fixed and proposed on their own branch. **One of them corrects a claim made
earlier in this document.**

### 1. `request_ticks` ignored its `timeout`

The timeout wrapped only the request that opens the subscription, not the loop that drains
it, so a request IB accepts but never answers hung forever. Reachable by asking for
`TRADES` ticks on a forex pair, which IDEALPRO does not have.

Measured before and after against paper TWS: `timeout=20` was still running when killed at
**97s**; it now returns empty at **22.0s**, while `BID_ASK` over the same window still
returns its **1022 ticks in 0.4s**.

### 2. Subscriptions were keyed by instrument alone - *not* what was claimed here before

**Correction.** This document previously stated that a failed subscription silently tears
down sibling subscriptions on the same instrument. Reading the code does not support that
mechanism: every subscription gets its own `child_token()` from the client's cancellation
token, and a per-task error is logged rather than propagated, so one stream failing cannot
cancel another. The claim was inferred from three correlated observations and should not
have been stated as a mechanism.

What the code *does* contain is a different, provable defect. The subscriptions map was
keyed by `InstrumentId` while its value carried a `subscription_type`, so it could hold
only one subscription per instrument. Subscribing to trades on an instrument that already
had quotes silently evicted the quote entry - leaving that task running untracked, and
sending a later `unsubscribe_quotes` to cancel the *trades* stream instead. The key is now
`(InstrumentId, SubscriptionType)`.

**The original observation no longer reproduces.** Tested 2026-09-02 inside a live session
with `live/subscription_interference.py`, which treats one instrument with the second
subscription and leaves a control instrument alone, then counts quotes for both across the
same two windows. Quotes stopping on both would be a session-wide event and evidence about
nothing; only the treated instrument stopping is the reported behaviour.

| Treatment           | Treated AAPL, before / after | Control MSFT, before / after | Verdict        |
| ------------------- | ---------------------------- | ---------------------------- | -------------- |
| Tick-by-tick trades | 39 / 36                      | 38 / 36                      | not reproduced |
| L2 book (`L2_MBP`)  | 39 / 37                      | 38 / 38                      | not reproduced |

**Read this as "the trigger no longer occurs", not as "the mechanism is disproven."** The
original stall came with IB refusals - 10189 for tick-by-tick and a depth-entitlement
refusal - and the hypothesis was that a refusal disturbs the contract's data line. Neither
run drew any refusal: both requests were accepted and simply delivered nothing. The
account's entitlements have widened since (release forms unlocked delayed quotes across the
US equity universe), so the most likely reading is that IB no longer refuses these requests
on this account and therefore cannot trigger the stall. The mechanism is untested, and the
practical risk is gone.

**One method note worth keeping.** The first depth run used `subscribe_book_depth10`, which
the IB adapter does not implement; the call raised on a missing argument, quotes carried on
undisturbed, and the run read *not reproduced* from an experiment that never ran. A
treatment that raised now forces an inconclusive verdict. **A negative result is only worth
as much as the proof that the treatment was applied.**

## Rust toolchain prerequisites

Three items are blocked on a source build: the `set_trading_state` pyo3 binding, the two
IB adapter bugs, and `make pre-commit` / `make format`. This environment currently has
`curl`, `git`, `uv` and `python3` and nothing else from the build chain.

**Status: installed and working.** `rustc 1.98.0`, Cap'n Proto 1.5.0 under `~/.local`,
uv 0.12.6, and `make build-debug` produces an editable install. `target/` is ~26 GB.
`make install-tools` has **not** been run, so `make pre-commit` is still unavailable.

**Needs root - the only step an agent cannot do:**

```bash
sudo apt-get update
sudo apt-get install -y build-essential clang lld curl git make pkg-config
```

**Everything after that is user-level**, following `docs/developer_guide/environment_setup.md`:

```bash
curl https://sh.rustup.rs -sSf | sh          # rust-toolchain.toml pins 1.98.0
source "$HOME/.cargo/env"
cargo install cargo-binstall --locked
CAPNP_PREFIX="$HOME/.local" ./scripts/install-capnp.sh   # 1.5.0; prefix avoids sudo
make install-tools                            # includes prek, needed by make pre-commit
make sync
```

Host has 936 GB free and 33 GB RAM available, so neither disk nor memory is a constraint;
expect the first full workspace build to be long regardless.

`make install-tools` installs the complete dev toolset (cargo-fuzz, llvm-cov, flamegraph,
lychee and more). Only a subset is needed to *build*, but the full set is what
`make pre-commit` expects, so installing it once is what makes that checklist item
satisfiable in future pull requests.

## Environment facts that cost time

- **TWS reports JST.** The `ibapi` crate has no alias for it and it is not IANA, so
  connections fail with a generic "Failed to connect to IB Gateway/TWS" that hides the
  cause. `IBAPI_TIMEZONE_ALIASES="JST=Asia/Tokyo"` is required in every process.
- **WSL2 NAT.** TWS is at `172.17.112.1:7497`; `127.0.0.1` does not work. The trusted
  IP is the WSL `eth0` address and **changes on reboot**. Mirrored networking
  (`networkingMode=mirrored` in `.wslconfig`) makes the source `127.0.0.1`, which TWS
  trusts implicitly, and removes a measured ~4.65s handshake stall on every connect.
- **Account data.** Paper `DUT067974` reads forex fully (realtime quotes, bars, ticks)
  and US equities only as delayed quotes. Historical US equity bars fail with IB 2188
  across all 16 request shapes tried - end date from 1 to 400 days back, every market
  data type, every bar spec, RTH on and off. No client-side workaround exists.

## The paper run

Not started. **Both prerequisites originally listed here have landed** - the multi-symbol
spread calibration and the ported walk-forward gate - but that list was incomplete: it
never named a strategy, and without one there is nothing to validate or deploy. The
corrected route is under "Shortest route to a paper run" above.

**Then, in order**

1. Re-run every existing trade-copilot verdict at the measured spread. Its own analysis
   says this "spends nothing, risks nothing, and is worth more than the next premise" -
   and it flips verdicts, so no current verdict can be read as a statement about the
   market until it is done.
2. Validate a candidate through IS -> WFA -> OOS on the Nautilus replay. The holdout is
   single-use and has never been spent.
3. Run 2-4 weeks on IB paper with the risk guard enabled. Forex is the only asset class
   with complete realtime data on this account, which argues for starting there; against
   that, all three portable setups are daily-bar equity patterns and the daily catalog
   now feeds them, so equities are no longer blocked on data. trade-copilot's own review
   notes that every premise tested so far was a daily-bar pattern on three mega-cap US
   names, "the most heavily arbitraged corner of the market" - which is an argument for
   widening the universe (see the open items), not for switching asset class.
4. Compare realised fills against the modelled cost and close the loop.

**Not gated on any of this:** nothing goes near live capital. The paper run is the first
place the breakers will ever fire, since they cannot fire in a backtest by design.
