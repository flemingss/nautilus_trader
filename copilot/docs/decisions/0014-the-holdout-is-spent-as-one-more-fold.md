# 14. The holdout is spent as one more fold

- **Status:** Proposed - awaiting the project owner's acceptance before any spend
- **Date:** 2026-09-03
- **Deciders:** Project owner; mechanics proposed in-session

## Context

[ADR-0012](0012-the-holdout-is-carved-at-2022-01-01.md) carved the holdout and, on
purpose, built no way to spend it. [ADR-0013](0013-entry-timing-is-evaluated-as-a-bracket.md)
closed the conflict that gated the spend and named its candidate,
`spy-gap-fade-long-next-close`. What remained undecided was *how* a one-time test is run
so that its number means what it claims to.

Three things make a holdout result worthless, and each is a way the spend could be built:

1. **A second methodology.** If the holdout is scored by anything other than the
   evaluator behind the walk-forward folds - a different objective, warm-up, scoring
   window or plateau rule - then its number is not comparable with the thirty it is set
   against, and any gap between them is as likely to be the method as the market.
2. **A choice made at spend time.** Any parameter the operator picks when spending -
   which candidate, which window, which threshold - is a degree of freedom exercised
   *after* the development results are known. The charter's rule is that the holdout is
   a test of a frozen candidate, not a tuning surface.
3. **A second look.** Once viewed, the holdout is development data for every future
   decision. A tool that can be re-run, or run on the diagnostic timing bound "just to
   see", converts the single-use test into another fold.

## Decision

**The holdout is scored as exactly one more walk-forward fold**, by the same evaluator
(`evaluate_fold`), with the whole development window minus the activation's purge as the
training slice, the purge as the gap, the holdout as the test slice, and warm-up drawn
from the bars immediately before it. The frozen candidate is whatever the in-sample
search selects on that training slice - the same plateau rule, objective, `min_trades`
and `cliff_drop` as every fold. A longer training window clears the eligibility floor
with more evidence, not a looser rule.

**Nothing is chosen at spend time.** The command takes an activation name and the same
name again as confirmation. Every other input is the activation's committed identity.

**The spend is single-use and refuses the diagnostic bound.** The written record under
`copilot/strategies/holdouts/` is the marker; its existence refuses a second spend. An
activation whose `entry_timing` is not `next_close` is refused outright (ADR-0013). A
dirty working tree is refused, because the record names the commit it was made from.

**The measurement and the decision are separate acts.** The tool writes the result,
pass or fail, and exits 0 either way. The owner records `reject`, `revise` or `freeze` in
the record in a follow-up commit; the tool never decides.

## Consequences

- `validate` reports `holdout_spent: true` for an activation the moment its record
  exists, so walk-forward re-runs after a spend are labelled as what they are:
  development runs on a premise whose out-of-sample test has been used.
- The record carries the full selection audit - every candidate, its score, its plateau
  floor and its rejection reason - so "the frozen set was not the raw peak" is a
  checkable claim.
- The result is never tuned on. A candidate that fails its holdout is rejected or revised
  as a new experiment with a new activation; a parameter change after the spend is not a
  fix, it is a new premise with no holdout.
- **Revisit trigger:** re-deciding the holdout boundary (superseding ADR-0012) restores
  unspent bars only if the new boundary lies entirely after the old spend's window;
  otherwise every spent activation stays spent.
