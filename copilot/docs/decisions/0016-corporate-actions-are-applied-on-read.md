# 16. Corporate actions are applied on read, from one table

- **Status:** Accepted
- **Date:** 2026-09-03
- **Deciders:** Project owner ("repair smartly"); shape proposed in-session against
  measurement

## Context

[ADR-0015]'s audit found that the vendor's daily series is **as-traded for every symbol
except AAPL**, which arrives back-adjusted. Nine corporate actions therefore sat in the
catalog as real price discontinuities, and a gap strategy reads the largest of them,
GOOGL's -95% on 2022-07-18, as the biggest gap in its history.

The obvious repair - rewrite the stored bars - is the wrong one, and the audit is the
reason. A back-adjusted price **cannot be checked against a venue's official closing
auction print**; an as-traded one can, which is the only reason the defect was found at
all. Rewriting the catalog would have fixed the series and destroyed the instrument
that proved it needed fixing.

A second problem sat underneath. `cost_model.SPLITS` was a hand-maintained table
listing AAPL alone, and it was **right only by luck**: the symbols whose splits it
lacked were the same ones whose prices had never been adjusted, so a recorded quantity
happened to be the real share count. Repairing the prices without repairing this would
have made the luck run out silently, charging a real price at an invented commission.

## Decision

**The stored file stays faithful to the vendor. Adjustment is a versioned table applied
when bars are read** (`copilot/data/corporate_actions.py`, `read_daily_bars`).

- **`ACTIONS` is one table answering two questions.** `pending_for` gives what is still
  in the stored prices as a discontinuity and drives the read-time adjustment;
  `split_actions` gives what a recorded quantity must be divided by and drives the cost
  model. AAPL appears in the table and in no pending list, because its prices arrived
  adjusted while its share counts still need the factor. The two can no longer drift,
  because there is nothing to drift from.
- **Splits and distributions are separated, because they need opposite treatment.** A
  split multiplies the share count; a spinoff does not. The vendor reports both as
  `split_factor`, so `classify` splits them on whether the factor is a ratio of small
  integers - shares are indivisible, spun-off value is not. MRK 1.05 (Organon), T 1.32
  (Warner Bros Discovery) and VZ's three are distributions: they adjust the price and
  leave the count alone. Treating one as a split would understate commission on every
  earlier trade.
- **`adjust=False` reads the raw series back**, so the audit keeps working. This is not
  a test seam; it is how a production tool reaches the as-traded prices it has to
  compare against.
- **Volume is scaled by the share-count factor**, so price times volume - the day's
  traded notional - survives the adjustment. A distribution leaves volume alone.

## What the measurement actually showed

**Nine actions, not the four a threshold scan found.** Scanning for large single-day
moves caught AMZN -94.9%, GOOGL -95.1%, WMT -66.1% and KO -50.1%. It missed GOOGL's
2014 2:1 at -49.7% by a hair, and it could never have caught **T's spinoff at -18.7%**
or **MRK's at -2.7%**, which are indistinguishable from an ordinary day. Only the
vendor's corporate-action list finds those, which is the lesson: **the defect class is
found by asking what happened, not by looking for something big.**

**Five were settled definitively**, against official as-traded prints: the catalog's
close on the day before the action equals the venue's print to the cent, so the action
is still in the series. AAPL settled the other way, its 2020-08-28 close of 124.8075
being exactly the official 499.23 over four.

**One is not settled and is listed anyway.** VZ's 2008-04-01 distribution has a factor
of 1.005, so the "already adjusted" and "as-traded" hypotheses sit half a percent apart
and the ratio test has no power there - the day's own move swamps it. It predates the
reference series, so nothing can settle it. It is included because every action this
catalog *can* resolve says the vendor stores as-traded, and consistency is a better
tiebreak than a coin flip; the cost of being wrong either way is bounded by that same
half a percent.

## Consequences

- **The three filed verdicts are unchanged, and were re-run to prove it.** AAPL 16/31
  at +0.046877 R, MSFT 20/31 at +0.089455 R, SPY 17/30 at +0.049848 R - identical to
  the figures [ADR-0012] recorded. AAPL was already adjusted and neither MSFT nor SPY
  acted, so nothing about them could move. That is the point of re-running it.
- **Seven symbols become usable that were not.** AMZN, GOOGL, KO, MRK, T, VZ and WMT
  carried a corporate action as a price move. The universe work [ADR-0009]'s sweep
  needs is no longer blocked by the data.
- **The catalog is no longer self-describing.** A reader who opens the parquet files
  gets as-traded prices for most symbols and adjusted ones for AAPL, and only this
  module knows which. That is the price of keeping the file auditable, and it is worth
  paying, but it means the table is now load-bearing: an unlisted action is a silent
  discontinuity again.
- **New symbols need their actions measured before use.** The table covers the twenty
  symbols in the catalog. Widening the universe means running the same check, which is
  now three lines against a vendor endpoint rather than a discovery.
