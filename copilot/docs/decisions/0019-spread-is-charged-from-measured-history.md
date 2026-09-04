# 19. Spread is charged from measured history, in the window the order goes into

- **Status:** Accepted
- **Date:** 2026-09-04
- **Deciders:** Project owner, after the movement was measured rather than argued
- **Supersedes the basis of:** [ADR-0011](0011-spread-is-charged-at-p95-from-a-pinned-snapshot.md),
  whose percentile, pinning discipline and commission model all stand

## Context

[ADR-0011] charged spread at p95 from `spread_snapshot_20260901T154744Z.json`, and named
its own weakness precisely: **248 to 301 delayed quotes from one 27-minute session**,
sampled mid-session while the strategy's order goes in near the open, applied to trades
running back to 2005. It listed three biases, argued all three pointed conservative, and
chose p95 to cover them.

The Databento pull ([ADR-0015](0015-databento-is-the-intraday-source-only.md)) replaced
the evidence available: **7.6 years of real top of book, ~750,000 samples per symbol**,
cuttable by time of day and by year. Two of ADR-0011's three biases are now measurable
rather than argued, and one is not:

- **The wrong moment is sampled** - now fixable, by cutting to the window that matters.
- **The centre is not stable across sessions** - now moot; the sample is 7.6 years.
- **Modern spreads applied to 2005 trades** - **still live and unmeasurable here.** The
  record begins 2018-05 and the gate scores from 2005.

## Decision

**Spread is charged at p95 of the worst measured year, within the charter's predeclared
execution window, from `spread_history_20260904T085055Z.json`.**

- **The window is the first two hours of the session, not the close.** The replay fills
  at a bar close because a daily bar has nothing else to fill against, and
  [ADR-0013](0013-entry-timing-is-evaluated-as-a-bracket.md) makes that a bracket rather
  than a claim about the world; the charter puts the order into the first one to two
  hours of the next session. `spread_history.py` said the opposite in its own docstring -
  that the strategy "enters at the close, so the closing spread is the number it pays" -
  and that was wrong. Charging the close would price a window no order uses, and would
  charge roughly **half** what the execution window costs.
- **The window includes its first five minutes.** They are the widest of the day - AAPL
  runs 3.1 to 6.1 bps per side there against 0.7 to 1.8 for the window as a whole - and
  they are exactly when an order placed at the open crosses. The first cut of this
  measurement excluded them by an ordering slip and understated MSFT by 10%; the
  committed tool includes them and a test pins the boundary.
- **The charge is the worst measured year, not the pooled figure.** Spreads here are set
  by volatility regime, not by a trend: AAPL's window p95 runs 0.762 in 2023 and 1.911 in
  2020. A pooled number prices a year that did not happen. The worst measured year is the
  same conservatism ADR-0011 chose in taking p95 over the median, applied to the axis the
  new data exposed - and it is the honest stand-in for the pre-2018 years there is no
  data for.
- **p95, the pinning discipline and the IB commission model are unchanged.** The
  percentile is read from the snapshot's own `basis` block, and a file measured at
  anything else refuses to load rather than being reinterpreted.
- **The superseded broker snapshot still reads.** `CostModel` keeps the reader for it, so
  ADR-0011's numbers remain reproducible. A superseded decision whose figures can no
  longer be recomputed is a claim rather than a record.

## Consequences

- **The coefficient moved, and nothing else did.** AAPL 1.99025 to 1.9107 per side
  (-4%), MSFT 1.7966 to 2.1468 (**+19%**), SPY 0.5238 to 0.5466 (+4%). All six verdicts
  keep their fold counts, their trade counts and their majorities; mean OOS net moves by
  at most 0.0025 R. **All twelve account-size crossings are identical** - AAPL next-close
  still USD 10,000 at 0.25% risk.
- **That non-movement is the finding, not a disappointment.** At the account sizes in
  question the IB per-order minimum is **3 to 15 times** the entire spread charge:
  0.1801 R of commission against 0.0124 R of spread at USD 5,000, 0.0326 against 0.0124
  at USD 25,000. ADR-0009 and ADR-0011 both predicted this in words; it is now measured.
  **The lever on net edge at this account size is the number of orders, not the spread.**
- **The pin is no longer the weakest input in the model.** That distinction now belongs
  to the pre-2018 extrapolation, which no purchase surveyed can close.
- **Twenty symbols are calibrated instead of three.** The broker snapshot covered only
  the instruments a live session could subscribe to. The measured history covers the
  whole store, which the survivor-bias universe correction will need and could not
  previously have had.
- **A holdout spent under ADR-0011's basis would have survived this repin.** That was not
  known when the sequencing was set, and it is worth recording: the repin was treated as
  a gate on the holdout spend, and measurement showed it was not one.
- **`spread_history` gained a committed `--write` basis rather than a scratch script.**
  The per-year figures behind this decision reproduce from a commit, which is the
  condition on any number this repository charges against.

[ADR-0011]: 0011-spread-is-charged-at-p95-from-a-pinned-snapshot.md
