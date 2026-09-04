# 20. The holdout boundary is per activation

Date: 2026-09-04

## Status

Accepted. Amends [ADR-0012](0012-the-holdout-is-carved-at-2022-01-01.md), which is not
superseded: the date it chose still governs every activation that does not name its own,
and no verdict computed against it has moved.

## Context

[ADR-0012] reserved the holdout by **date** rather than by percentage, and the reasoning
is still right. A percentage is computed over whatever bars are present, so a
re-backfill would slide the boundary and leak held-out bars into development. A date
cannot move without a diff.

That decision was made against a universe of three US single names with twenty years of
history each, where one date put the holdout at 15-20% for all of them and the shared
constant was indistinguishable from a per-activation one.

Onboarding six low-priced ETFs on 2026-09-04 separated the two. Their histories do not
start in 2005 and they do not start together:

| Activation          | First stored bar | Why                                              |
| ------------------- | ---------------- | ------------------------------------------------ |
| SCHX, TLT, HYG, EEM | 2017-01-03       | Vendor closes before 2017 are not auction prints |
| GLDM                | 2018-06-27       | Fund inception                                   |
| XLF                 | 2020-06-02       | Vendor coverage begins there, not the fund       |

Against a 2017 start, 2022-01-01 reserves **44.36%** of the evaluation window. `carve`
refused, exactly as it was built to, and the refusal was correct: a 44% holdout is not
the charter's reservation and running it would have been a different experiment wearing
the same name.

The instinct is to reach for a percentage again. That would undo [ADR-0012] and
reintroduce the sliding boundary it was written to prevent.

## Decision

**An activation may pin its own holdout boundary, as a date, in its registry file.**
`[validation] holdout_start` carries it; an activation that omits it uses [ADR-0012]'s
2022-01-01.

The boundary is chosen as the **calendar quarter start whose resulting holdout share
sits nearest the middle of the charter's 15-20% band**, computed once against the
evaluation window at onboarding and then fixed. A quarter start rather than an exact
percentile date, so the pin is a date someone chose rather than a number a script fitted.

The boundary is written into the **verdict record** alongside the evaluation window's
end. With one global constant that was implicit; with a per-activation pin, two verdicts
carved at different dates would otherwise be indistinguishable on disk.

The six pins made on 2026-09-04:

| Activation | Boundary   | Holdout share |
| ---------- | ---------- | ------------- |
| SCHX       | 2024-07-01 | 16.72%        |
| TLT        | 2024-07-01 | 16.71%        |
| HYG        | 2024-07-01 | 16.72%        |
| EEM        | 2024-07-01 | 16.72%        |
| GLDM       | 2024-10-01 | 16.74%        |
| XLF        | 2025-01-01 | 17.81%        |

## Consequences

**What [ADR-0012] protected is still protected.** The boundary is a date in a committed
file. It cannot move without a diff, a re-backfill cannot slide it, and the band guard in
`carve` still refuses anything outside 15-20%. The only thing that changed is which file
the date lives in.

**A verdict is now only comparable to another with the same pin.** This was already true
across entry-timing modes ([ADR-0013]) and is the same kind of incomparability: SCHX's
holdout covers 2024-07 onward and AAPL's covers 2022-01 onward, so the two are not folds
of one experiment. The record names the boundary so a reader can tell.

**A short holdout is a weaker holdout, and the pin does not hide that.** SCHX's reserves
378 bars against AAPL's 1003. The share is the same; the evidence is not, and the trade
count in the spend record is what says so.

**A recent holdout is a narrower regime.** Reserving 2024-07 onward tests one market
rather than the several 2022-2025 contains. That is a cost of a short history, not of
this decision - there is no boundary that gives a 2017 series a 2005-2021 development
window - but it belongs in the argument when a spend on one of these is read.

**The incumbents did not move.** AAPL 20/30 folds at +0.102176 R over 401 trades, MSFT
17/31 at +0.063494 over 375, SPY 19/31 at +0.052729 over 439 - bit-identical before and
after, which is the check that this amended [ADR-0012] rather than reinterpreting it.

[ADR-0013]: 0013-entry-timing-is-evaluated-as-a-bracket.md
