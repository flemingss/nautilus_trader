# Holdout records

One JSON file per **spent** holdout, written by
`python -m copilot.strategies.spend_holdout <activation> --confirm <activation>`.
See [ADR-0014](../../docs/decisions/0014-the-holdout-is-spent-as-one-more-fold.md).

**The file is the marker.** Its existence is what refuses a second spend, and what makes
`validate` report `holdout_spent: true` for the activation from then on. Deleting one
does not un-spend anything - the holdout has been seen - it only removes the evidence
that it was.

## What a record holds

| Field               | Meaning                                                                                                        |
| ------------------- | -------------------------------------------------------------------------------------------------------------- |
| `code_commit`       | The commit the spend ran from. A dirty tree is refused so this is exact.                                       |
| `frozen_parameters` | What the in-sample search selected on the whole development window - the candidate the holdout actually tested |
| `selection_audit`   | Every candidate, its score, its plateau floor and its rejection reason, so the freeze is a checkable claim     |
| `holdout`           | Trades, net expectancy in R, pass/fail with the evaluator's own reason, and a tearsheet                        |
| `owner_decision`    | `null` when written. The owner fills in `reject`, `revise` or `freeze` in a follow-up commit                   |

## Only `next_close` activations appear here

The optimistic timing bound is diagnostic and can never spend a holdout
([ADR-0013](../../docs/decisions/0013-entry-timing-is-evaluated-as-a-bracket.md)). The
tool refuses anything else before touching a bar.
