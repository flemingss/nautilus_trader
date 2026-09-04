# Holdout records

One JSON file per **spent** holdout, written by
`python -m copilot.strategies.spend_holdout <activation> --confirm <activation>`.
See [ADR-0014](../../docs/decisions/0014-the-holdout-is-spent-as-one-more-fold.md).

**The file is the marker.** Its existence is what refuses a second spend, and what makes
`validate` report `holdout_spent: true` for the activation from then on. Deleting one
does not un-spend anything - the holdout has been seen - it only removes the evidence
that it was.

## What a record holds

| Field                                              | Meaning                                                                                                                     |
| -------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `code_commit`                                      | The commit the spend ran from. A dirty tree is refused so this is exact.                                                    |
| `frozen_parameters`                                | What the in-sample search selected on the whole development window - the candidate the holdout actually tested              |
| `selection_audit`                                  | Every candidate, its score, its plateau floor and its rejection reason, so the freeze is a checkable claim                  |
| `holdout`                                          | Trades, net expectancy in R, pass/fail with the evaluator's own reason, and a tearsheet                                     |
| `owner_decision`                                   | `null` when written. The owner fills in `reject`, `revise` or `freeze` in a follow-up commit                                |
| `owner_decision_recorded`, `owner_decision_reason` | Added with the decision: the date, and the reasoning in the owner's words, so the call is readable without the conversation |

## Voided records

`voided/` holds a spend that scored a series later shown not to exist - an unregistered
corporate action inside the window - with a `voided` block naming the date, the reason
and [ADR-0021](../../docs/decisions/0021-an-unscorable-spend-still-consumes-the-holdout.md).
A voided record is not a marker: `is_spent` reads only this directory, so the activation
may be spent **once more**, on the corrected series. A low trade count is never grounds
for voiding; ADR-0021 is explicit that an unscorable spend on a true series stands.

The voided record still names the walk-forward verdict it answered, and that verdict is
kept under the retention rule for the same reason any cited one is.

## Only `next_close` activations appear here

The optimistic timing bound is diagnostic and can never spend a holdout
([ADR-0013](../../docs/decisions/0013-entry-timing-is-evaluated-as-a-bracket.md)). The
tool refuses anything else before touching a bar.
