# 9. Cost is modelled at the target account size

- **Status:** Accepted
- **Date:** 2026-09-01
- **Deciders:** Project owner

## Context

The gap fade's first walk-forward returned +0.049 to +0.091 R gross across three symbols,
and a cost analysis reported that 78% to 91% of that survived at the measured spread. Both
figures used the research default of **USD 1,000 risked per trade**, a number chosen for
backtest convenience and never examined.

The operating charter specifies an account under USD 10,000 at 0.10% to 0.25% planned risk,
which is **USD 8 to 20 per trade**. Repricing the same trades at that budget:

| Risk budget | AAPL | MSFT | SPY |
| --- | --- | --- | --- |
| USD 1,000 (research default) | +0.040 | +0.071 | +0.053 |
| USD 20 (8k at 0.25%) | **-0.065** | **-0.037** | **-0.059** |
| USD 8 (8k at 0.10%) | -0.246 | -0.202 | -0.263 |
| USD 125 (50k at 0.25%) | +0.026 | +0.062 | +0.040 |

**The premise is negative on every symbol at the account size it would actually trade.**

The mechanism is Interactive Brokers' USD 1.00 per-order minimum. At USD 20 of risk the
position is 5 to 23 shares, so a round trip costs USD 2.00 regardless of size - **0.11 R
against a gross edge of 0.05 to 0.09 R**. At 0.10% risk, between 31 and 131 trades per
symbol do not size at all.

Two consequences follow that were invisible at the research default:

- **Spread is not the binding cost at small size.** It is 0.007 R against commission's
  0.11 R, a factor of fifteen. The choice between modelling spread at the median, p75 or
  p95 - which had its own analysis - is noise beside the order minimum.
- **Cost in R is not scale-free, even though the spread component is.** Quantity cancels
  out of the spread term, so it was easy to assume the whole cost model was
  size-independent. The per-order minimum breaks that, and it breaks it hardest exactly
  where a beginner account operates.

## Decision

**A cost model is only meaningful at a stated account size, and a premise is only a
candidate if it survives at the size it would actually trade.**

- Every backtest declares the account equity and planned risk fraction it assumes.
  `risk_budget` as a bare currency amount is a research convenience and must not be
  reported as though it were a policy.
- Cost analysis reports a **sweep across account sizes**, not a single figure. The useful
  output is the equity at which the premise crosses zero.
- **A premise that is only viable above the current account is not a candidate.** It is a
  finding about what this account can trade, recorded and shelved, not a strategy waiting
  for capital.
- Per-order minimums, per-share floors and any other fixed cost are modelled explicitly.
  Percentage-of-notional approximations hide precisely the term that dominates here.

## Consequences

- The gap fade is **not** a candidate for this account as configured. Its viability
  threshold sits somewhere between USD 20 and USD 125 of risk per trade, which is roughly
  a USD 8,000 to USD 50,000 account at 0.25%. Establishing that number is worth doing
  before any further work on the premise.
- Premise selection acquires a criterion it did not have: **cost per trade must be small
  relative to the edge at the target size.** That favours wider stops, longer holds and
  fewer trades - the opposite of what a naive search for higher expectancy selects.
- Two earlier reports were true of their stated budget and misleading as summaries. Both
  are corrected in place rather than deleted, because the error is instructive: the R unit
  is scale-free for spread and not for commission, and it is easy to carry that assumption
  across the whole model without noticing.
- Fractional shares, if the account supports them, change this materially by removing the
  rounding-to-zero failure. They do not remove the per-order minimum.
