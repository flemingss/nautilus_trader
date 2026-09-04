# 21. An unscorable spend still consumes the holdout

Date: 2026-09-05

## Status

Accepted. Amends [ADR-0014](0014-the-holdout-is-spent-as-one-more-fold.md), which is not
superseded: the spend is still one more fold, still single-use, still decided by the
owner. This records what "single-use" means when the fold returns no score, and the one
case in which a spend is void.

## Context

The SCHX holdout was spent on 2026-09-04 and produced four trades against a floor of
five: `insufficient_test_trades`. No expectancy was scored, so nothing was learned about
the premise - and the argument that nothing was therefore spent writes itself. It is the
wrong argument. The pre-spend projection had estimated 34 trades; the count came in at
four because the window was short and the regime quiet. Letting a spend that "scored
nothing" be repeated is exactly the mechanism by which a boundary gets moved, or a floor
lowered, until the count is convenient. The holdout's value is that it is seen once.

The same evening's operator walk found something else about that spend. Two SCHX splits
were unregistered and sitting in the stored prices, and the 3:1 of 2024-10-11 fell inside
the holdout window as a -67% gap-down. The four trades were scored on a series that did
not exist.

Those are two different situations and they need two different answers.

## Decision

**An unscorable spend consumes the holdout.** A spend that returns too few trades to
score, on a series that is what it claims to be, is spent. The activation's holdout is
seen, the record stands with its reason, and the owner's decision is made on that record
like any other. The tool already refuses a spend whose projected count cannot clear the
floor; a projection that was wrong is a measurement, not a reason to look again.

**A spend on a series later shown not to exist is void.** When a corporate action inside
the holdout window was unregistered at the time of the spend, the bars the fold scored
were not the instrument's history, and the spend measured nothing about the premise. The
record moves to `holdouts/voided/` with a `voided` block naming the date and the reason,
`is_spent` no longer sees it, and the holdout may be spent **once more**, on the corrected
series. The exception is the data, never the count. A voided record is kept, and the
verdict it cites is kept with it.

The SCHX spend of 2026-09-04 is voided under this decision. The AAPL spend of the same
day, which passed thinly and was decided `revise`, is unaffected.

## Consequences

- `is_spent` reads the top level of `holdouts/` only. A voided record refuses nothing.
- The retention rule for verdicts counts a citation from a voided record.
- A second spend on a corrected series is a spend like any other: `next_close` only, a
  clean tree, the name retyped, and the projection refusing a window that cannot score.
- **Revisit trigger:** a second class of "the series did not exist" - a substituted bar
  found wrong, a vendor rewrite of history - would have to be argued into this exception
  by its own ADR, not read into it.
