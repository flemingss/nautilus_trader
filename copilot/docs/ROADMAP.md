# Roadmap and state

What is built, what is deliberately deferred, and what the paper run needs. Written to
be honest about the difference between "tested" and "proven in the market".

## Where this came from

Two projects are being fused:

- **NautilusTrader** — a strong backtest engine, order model, live node and
  reconciliation. Its gaps: no screening, no walk-forward or parameter search, and a
  risk engine limited to per-order notional and rate limits.
- **trade-copilot** — a HITL signal advisor with an institutional-grade validation
  gate and account-wide risk breakers. Its gaps: a crude cost model, a small evidence
  base, and no intraday data.

Each side covers the other's gaps almost exactly. Screening is the only stage neither
covers, and it is pinned.

## Status

| # | Item | State |
| --- | --- | --- |
| 1 | Spread calibration from IB | **Built and run.** First measurement recorded below. |
| 2 | Rolling-window risk breakers | **Ported and tested** (22 tests). Reactive, not engine-enforced — see below. |
| 3 | Nautilus-backed `Replay` | **Built and tested** (8 tests). The gate itself is not yet ported. |
| 4 | Walk-forward gate port | **Not started.** ~1,400 LOC. Next major piece. |
| 5 | `set_trading_state` pyo3 binding | **Blocked** — needs a Rust toolchain. |
| 6 | Screener / universe selection | **Pinned**, out of repo by decision. |

## 1. Spread calibration — first result

trade-copilot's `PaperFillConfig.spread_bps` is **5 bps per side**, described in its
own source as "a deliberately conservative ceiling ~10x the quoted spread, pending a
live-quote snapshot". Its `SYSTEM.md` §14 names this the top blocker, ahead of premise
supply, because the modelled cost is what decides every verdict.

Measurement, AAPL over 654s of IB quotes (111 usable, 18 rejected as crossed/locked):

| | full spread (bps) | per side (bps) |
| --- | --- | --- |
| median | 0.6381 | **0.3190** |
| p75 | 1.2748 | 0.6374 |
| p95 | 2.8685 | 1.4343 |
| max | 5.7374 | 2.8687 |
| incumbent | — | 5.0 |
| **overstatement at the median** | — | **~15.7x** |

**Sample size moved this number by 2x, which is itself the finding.** A first 148s run
over 24 quotes gave 1.2753 bps full / 7.8x; the 654s run over 111 quotes gives 0.6381
bps / 15.7x. Do not set a coefficient from a short run.

Two caveats remain:

- **Delayed data.** The account has no realtime US equity subscription, so these are
  delayed quotes — a genuine bid/ask, but updating slowly and possibly wider than the
  realtime NBBO. This is an *upper bound*, the conservative direction.
- **The distribution has a tail.** Median 0.64 bps but p95 2.87 and max 5.74. A cost
  model set at the median will understate the bad days. Choosing the coefficient is a
  policy decision — the median is the honest central estimate, p75 or p95 the
  defensible conservative ones — and should be made explicitly rather than by
  defaulting to whichever number is at hand.

### Entitlement change, 2026-09-01

IB market data **release forms** were completed. Retested immediately; the effect is
partial and worth recording precisely, because it changes what is calibratable but not
what is backtestable.

| | before forms | after forms |
| --- | --- | --- |
| AAPL delayed quotes | works | works |
| MSFT / SPY delayed quotes | **no data, no error** | **works** |
| Any realtime quotes | no data | **still no data** |
| US equity historical bars | IB 2188 | **still IB 2188** |
| Index (`^SPX`), futures | IB 2188 | still IB 2188 |
| Forex (IDEALPRO) | full | full |

So the forms unlocked **delayed quotes across the US equity universe** — the earlier
MSFT/SPY silence was an entitlement gap, not the adapter bug it resembled. Realtime
streaming and historical bars still need a paid subscription, which the forms alone do
not grant. IB subscriptions also typically activate at a trading-day boundary, so
recheck after the next session before concluding a purchase has not landed.

Practical effect: multi-symbol spread calibration is now possible, which was a stated
prerequisite for the paper run. The backtest evidence base is unchanged — there is
still no route to US equity history through IB.

First three-symbol measurement (delayed, 85s — short, treat as indicative):

| symbol | n | median full (bps) | per side | vs 5 bps/side |
| --- | --- | --- | --- | --- |
| SPY | 10 | 0.2611 | 0.1306 | **38.3x** overstated |
| AAPL | 12 | 0.9558 | 0.4779 | 10.5x |
| MSFT | 14 | 1.2711 | 0.6355 | 7.9x |

The spread differs by ~5x across three large-cap US names, so **a single global
`spread_bps` is the wrong shape for the model** — it should be per-instrument. That is
a structural finding, independent of sample size.

### Known defect in the calibrator

A long run interrupted by an external `SIGINT` lost its accumulated samples: the report
is only written after `node.run()` returns, and the signal did not unwind through it.
The internal self-stop path works, so bounded runs are safe; ad-hoc interruption is
not. Fix by installing a signal handler that writes the report from accumulated state
rather than relying on the `finally` after `node.run()`.

## 2. Risk breakers — what they actually enforce

Ported from trade-copilot ADR-0025. Both breakers are pure functions over closed
trades, so they are tested against hand-built losing streaks rather than a live
account.

- Consecutive stop-outs (default 4), streak must be current
- Peak-to-trough realised drawdown (default 6% in 14 days)
- 3-day cooldown, longest-running breach wins

**Enforcement is reactive, not preventive.** Nautilus has exactly the right primitive
— `TradingState` with `HALTED`/`REDUCING`, enforced natively in the Rust risk engine —
but `set_trading_state` has no pyo3 binding and no production caller, so Python cannot
reach it. The guard therefore cancels working orders account-wide, flattens configured
instruments, and publishes a signal. A strategy that keeps submitting will keep
getting orders accepted between evaluations.

Closing that gap is item 5 and is the single change that would touch upstream files.

## 3. The `Replay` seam

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

## Bugs found upstream, worth fixing on the fork

1. **A failed subscription silently kills sibling subscriptions.** AAPL in DELAYED
   mode: quotes-only → 15 ticks; quotes + failing tick-by-tick trades → 0; quotes +
   failing L2 book → 0. The adapter warns about what broke and says nothing about the
   working stream it took down.
2. **`request_ticks` ignores its `timeout` and hangs.** `TRADES` on a forex pair (IB
   never responds, since IDEALPRO has no trade ticks) with `timeout=20` was still
   running when killed at 97s.

Both are in `crates/adapters/interactive_brokers/src/data/`. Neither is fixed here —
both need a Rust build.

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
  across all 16 request shapes tried — end date from 1 to 400 days back, every market
  data type, every bar spec, RTH on and off. No client-side workaround exists.

## The paper run

Not started, and two things must land first.

**Prerequisites**

1. A longer, multi-symbol spread calibration, ideally on realtime data, so the cost
   model rests on a measurement rather than an order-of-magnitude estimate.
2. The walk-forward gate ported (item 4), so a candidate can be validated before it
   is given capital.

**Then, in order**

1. Re-run every existing trade-copilot verdict at the measured spread. Its own
   analysis says this "spends nothing, risks nothing, and is worth more than the next
   premise" — and it flips verdicts, so no current verdict can be read as a statement
   about the market until it is done.
2. Validate a candidate through IS → WFA → OOS on the Nautilus replay. The holdout is
   single-use and has never been spent.
3. Run 2–4 weeks on IB paper with the risk guard enabled, forex first — it is the only
   asset class with complete realtime data on this account, and trade-copilot's own
   review notes that every premise tested so far was a daily-bar pattern on three
   mega-cap US names, "the most heavily arbitraged corner of the market".
4. Compare realised fills against the modelled cost and close the loop.

**Not gated on any of this:** nothing goes near live capital. The paper run is the
first place the breakers will ever fire, since they cannot fire in a backtest by
design.
