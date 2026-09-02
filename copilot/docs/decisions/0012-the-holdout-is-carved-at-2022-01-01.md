# 12. The holdout is carved at 2022-01-01

- **Status:** Accepted
- **Date:** 2026-09-02
- **Deciders:** Project owner, via the charter's mandate; the specific boundary proposed
  in-session

## Context

The charter reserves the most recent 15-20% of history as a locked, single-use
out-of-sample test, and its adoption opened a conflict: `walk_forward` ran over the
entire history, no holdout existed, and every verdict record carried
`holdout_spent: false` - implying a reservation that was never made. No verdict from
this repository had an out-of-sample estimate behind it.

A percentage boundary cannot be the mechanism. It is computed over whatever bars are
present, so a re-backfill that extends the catalog would silently move the boundary and
leak formerly held-out bars into development - the same quiet-drift failure [ADR-0011]
closed for the cost snapshot by pinning it by name.

## Decision

**The holdout boundary is a date, pinned in code: bars closing at or after 2022-01-01
are holdout, never development** (`copilot/validation/holdout.py`, `HOLDOUT_START`).

- **2022-01-01 reserves 18.99% of the catalog** (1,003 of 5,283 bars per symbol),
  inside the charter's band, and the held-out span carries the 2022 drawdown, the
  2023-2024 recovery and 2025 - regimes the development window's tail does not contain.
- **The carve refuses rather than drifts.** A history that does not straddle the pin,
  or a share outside 15-20%, raises. New bars land on the holdout side of a date pin,
  so catalog growth raises the share until the carve refuses - the forcing function for
  re-deciding the boundary in a commit that supersedes this ADR.
- **One boundary for every activation**, so verdicts stay comparable and the regime
  split is the same calendar everywhere.
- **The gate sees development only.** `validate` carves before the walk-forward sees a
  bar, and each record names what was withheld (`holdout` block: start, bars reserved,
  range) alongside `holdout_spent: false`.
- **No spend tool exists, deliberately.** Building the read-the-holdout path before a
  frozen candidate exists invites peeking. Spending it is a future, separate act, and
  once viewed the holdout is development data for every decision after it.

## Consequences

- **Every verdict filed before this date is superseded**: they were computed over
  history that included the holdout. The development-window verdicts (31 folds,
  2005-2021): AAPL 16/31 net +0.0469 R, MSFT 20/31 net +0.0895 R, SPY 17/30 net
  +0.0498 R - all three majority-pass. AAPL's full-window net fail came from folds that
  now sit in the holdout, a window effect that is itself the argument for treating
  single-name verdicts as provisional.
- **Sequencing falls out of the carve**: the entry-timing conflict (signal-close fill
  against the charter's next-session rule) must be resolved and re-validated *before*
  the holdout is spent, or the one-time test is spent on semantics the charter has
  already rejected. Walk-forward re-runs are free; the holdout is not.
- Fold counts drop from 39 to 31, and the walk-forward now ends at 2021-12-31.

[ADR-0011]: 0011-spread-is-charged-at-p95-from-a-pinned-snapshot.md
