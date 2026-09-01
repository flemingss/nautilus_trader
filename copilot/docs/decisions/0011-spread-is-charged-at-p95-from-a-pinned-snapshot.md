# 11. Spread is charged at p95 from a pinned snapshot

- **Status:** Accepted
- **Date:** 2026-09-02
- **Deciders:** Project owner

## Context

The validation gate scored gross of costs while the coefficient decision - median, p75 or
p95 of the measured spread - waited to be called. Pricing the gate's own scored trades at
each candidate against the first reproducible-from-a-commit snapshot
(`cost_impact_20260901T164624Z.json`) showed the choice moves net edge by only 3-14% of
gross at daily-bar frequency, while the unmeasured 5 bps placeholder it replaces was
distorting verdicts outright - it prices SPY's edge to zero on spread alone.

Three biases in the measurement all point the same direction, and each argues for the
conservative end:

1. **The wrong moment is sampled.** Snapshots record mid-session quotes; the gap fade
   trades after overnight gaps, and under the charter's next-eligible-session rule at or
   near the open - the widest spreads of the day.
2. **The centre is not stable across sessions.** AAPL's median doubled between the two
   measurement days (0.64 to 1.22 bps full) while one session's p75 approximated the
   other's median. A central estimate from one session inherits that session.
3. **2026 spreads are applied to trades from 2006 onward**, and large-cap spreads were
   several times wider early in that window.

The delayed-quote upper bound is conservative in the opposite direction and already
banked; it does not offset moment-of-entry bias, which realtime data would not fix either.

## Decision

**The gate charges spread at p95 of the measured full spread, per instrument, halved to
per side, from one snapshot pinned by name** - currently
`spread_snapshot_20260901T154744Z.json` in `copilot/calibration/cost_model.py`. Moving
the pin or the percentile is a deliberate act in a commit touching that line, never a
side effect of re-running the calibrator.

- **Costs run through the gate's objective**, not the engine: mean R per trade net of
  each trade's round trip. This makes the in-sample search select parameters that survive
  costs rather than merely win gross, and it is exact for this trade shape - one entry,
  one exit, no path dependence.
- **Commission is IB Pro fixed tier, modelled explicitly**: max(USD 1.00, USD 0.005 per
  share) per order, capped at 1% of notional, on split-corrected share counts. The
  minimum is measured live twice (USD 2.02 and 2.01 round trips at 3 and 1 shares).
- **An uncalibrated symbol refuses to score.** No fallback coefficient, no silent gross.
- **Every verdict records its cost basis**: snapshot name, percentile, the coefficient
  charged, and the commission schedule. `costs_modelled: true` means exactly this.
- Costs are charged on the trades **as replayed** (research sizing). Whether a premise
  survives at the target account size remains [ADR-0009]'s sweep, where the per-order
  minimum dominates everything the coefficient measures.

## Consequences

- **The first net verdicts moved a result**: AAPL flipped from majority-pass (20/39
  gross) to majority-fail (19/39 net); MSFT (24/39) and SPY (21/38) held. Cost-aware
  selection also chose different parameters on two symbols, visible as changed trade
  counts. Records before 2026-09-02 carry `costs_modelled: false` and are not comparable
  to records after it.
- The conservatism is bounded and known: p95 gives up 3-14% of gross edge relative to the
  median at daily-bar frequency.
- **Revisit triggers, named now:** an intraday strategy requires re-measuring at its
  entry time-of-day rather than inflating the percentile further - a percentile is not a
  substitute for sampling the right moment; a realtime-quote entitlement landing warrants
  a re-measurement and a deliberate re-pin; and per the decision's own framing, the
  percentile can be revisited whenever the evidence changes, as a new pin in a new
  commit.

[ADR-0009]: 0009-cost-is-modelled-at-the-target-account-size.md
