# 26. Attribution is measured per trade, against pinned factor returns, bracketed on the exit session

- **Status:** Accepted
- **Date:** 2026-09-10
- **Deciders:** Project owner (attribution approved as the next stage 02 item, 2026-09-10); mechanics proposed in-session

## Context

Every premise this project has measured is long-only on US equities and ETFs. A rule like
that earns the equity premium on every session it holds, whether or not its signal means
anything, so a positive expectancy is not yet evidence of an edge. The question the roadmap
has carried since 2026-09-05 is how much of the return survives being charged for the market,
and for the size, value and momentum tilts that cost a few basis points a year to own.

Two things made it answerable on 2026-09-10. Every verdict now files its scored trades with
their dates and round-trip cost (`trade_rows`), so the question is a read of a record rather
than a replay. And the evidence interval was corrected onto the net series the same evening,
which moved the pool from *clears zero* to *straddles it*: +0.030 R per trade net, 90%
interval [-0.018, +0.079]. The pool's gross return, +0.067 R per trade, is what attribution
has to explain.

## Decision

**Per trade, in R.** Each trade's excess R is regressed on the factor returns it was exposed
to, each scaled by that trade's own notional per unit of risk:

```text
R_i - L_i * rf_i  =  alpha  +  sum_k  beta_k * L_i * F_k,i  +  e_i,    L_i = +/- N_i / K_i
```

A position with notional `N` and risk `K` earns `N / K` R per unit of return on the stock, so
the scaling is what puts a return in R and a factor return in fractions on the same footing.
The loadings are then ordinary betas and alpha is in R per trade, the unit every verdict
reports. Checked on the filed rows: `realized_pnl = quantity * (exit - entry)` on all 439
SPY next-close trades, so the bridge is exact. A daily time series of portfolio returns was
not chosen: it needs every open position marked each session, which the filed rows do not
carry, and it would answer the same question with a coarser unit.

**The factors are the Kenneth French daily library, pinned.** Market excess return, SMB, HML,
momentum and the one-month T-bill, from the 202607 CRSP vintage, committed under
`copilot/validation/factors/` and checked by SHA-256 on every read. The library is rebuilt
from each vintage and past values move, so a result that fetched on read would change under
a commit that changed nothing - the failure [ADR-0011](0011-spread-is-charged-at-p95-from-a-pinned-snapshot.md)
pins the spread snapshot against. Alpha is judged in the four-factor model; the market alone
is reported beside it. Factor returns over a window are summed, which is second order in
daily returns and below a basis point over these holding periods.

**A session is the timestamp's UTC date.** The catalog stamps vendor bars at midnight UTC on
the session date and patched bars at the real close; the UTC date is right for both and is
the convention every other reader uses. A first draft converted to Eastern, which moves
midnight stamps to the previous day and shifted every window one session early. It measured
a long SPY position's market beta at 0.46.

**The exit session is counted both ways, and a verdict is named only where the two agree.**
Every entry fills at a close, so each full session held is exact. Exits do not: on SPY
next-close, 417 of 439 fill at a stop or target during the session and 22 at the open. No
daily method can say how much of the exit session was held. Counting it in full attenuates
the loadings - a stop fills on the day the market falls, so the regressor carries the whole
fall and the trade only part - and **flatters alpha**. Leaving it out puts the partial move in
the residue instead. `bracketed_verdict` returns `alpha` or `negative_alpha` only when both
constructions return it, and `bracket_disagrees` otherwise, so a conclusion cannot be an
artefact of the approximation.

**Inference is ADR-0024's bootstrap, refitting the regression.** The same block length, the
same seed and the same sequence of block draws. A model with no factors is a regression on a
constant, and its interval is the evidence interval to the digit; a test holds that identity,
so the two intervals stay comparable by construction.

**It reports and does not decide.** Nothing gates on attribution. Whether a premise whose
return is its factor exposure should be revised or rejected is an owner decision, informed by
the result.

## What it measured, 2026-09-10

`strategies/out/attribution_20260911T012725Z.json`, over the twelve walk-forward verdicts and
the pooled run filed the same evening. R per trade, net of costs.

| Result                   | Net      | Four-factor alpha, exit included | Exit excluded           | Market beta       | Verdict             |
| ------------------------ | -------- | -------------------------------- | ----------------------- | ----------------- | ------------------- |
| Pooled, nine symbols     | +0.030   | -0.038 [-0.072, -0.002]          | -0.042 [-0.083, +0.002] | 0.38              | `bracket_disagrees` |
| AAPL next-close          | +0.091   | -0.026 [-0.090, +0.034]          | +0.001 [-0.079, +0.076] | 1.16              | `no_alpha_detected` |
| MSFT next-close          | +0.055   | -0.012 [-0.067, +0.036]          | -0.011 [-0.077, +0.054] | 0.98              | `no_alpha_detected` |
| SPY next-close           | +0.042   | -0.034 [-0.062, -0.004]          | -0.058 [-0.116, +0.002] | 0.79              | `bracket_disagrees` |
| SCHX next-close          | +0.055   | -0.075 [-0.113, -0.024]          | -0.120 [-0.197, -0.037] | 0.79              | `negative_alpha`    |
| EEM, HYG, TLT next-close | negative | all below zero                   | all below zero          | 0.66, 0.20, -0.13 | `negative_alpha`    |

- **No result shows positive alpha under either construction.** Five are `negative_alpha` on
  both - EEM, HYG, TLT, SCHX and SPY's signal-close variant - and the largest point estimate
  anywhere is +0.001 R.
- **The pool's gross return is its exposure.** +0.067 R per trade gross decomposes into
  +0.017 from cash, +0.051 from factor exposure and -0.001 of alpha. Costs of 0.037 R then
  take the residue negative.
- **The one walk-forward whose interval cleared zero is beta.** AAPL next-close's gross
  +0.106 R is +0.005 cash, +0.112 factor exposure and -0.011 alpha, at a market loading of
  1.16 - the premise rode AAPL's market exposure over 2005-2021.

## Consequences

- **The gap-fade family has no measured alpha.** That is the input to an owner decision
  recorded on the roadmap: reject the family, or revise it into a premise that is not long
  the market by construction. The code does not choose.
- **A pooled holdout should not be spent on it.** The clean pooled window is the largest
  piece of out-of-sample evidence left and it is single-use
  ([ADR-0021](0021-an-unscorable-spend-still-consumes-the-holdout.md)). Spending it to confirm
  a premise with no alpha buys nothing.
- **Every future verdict can be attributed for free.** Records carry their trades, so the
  command reads the newest record per activation and the newest pool in about three seconds.
- **The factor pin ages.** The 202607 vintage covers trades to 2026-07-31. A trade past the
  last pinned session is refused rather than attributed over the covered part.
- **Revisit trigger:** a premise that is not long-only, or that holds through the exit
  session by design, removes the bracket's reason to exist and should be attributed on
  whichever construction matches it. And if intraday exit times are ever filed, the exit
  session can be apportioned rather than bracketed.
