# 24. A holdout pass needs an interval, and "not proven" is a third outcome

- **Status:** Accepted
- **Date:** 2026-09-10
- **Deciders:** Project owner; mechanics proposed in-session

## Context

The walk-forward gate asks each fold to beat zero and then counts the folds that did.
`DEFAULT_FOLD_PASS_THRESHOLD` is zero deliberately, and its comment gives the reason:
costs are already modelled in the fill, so *better than not trading* is the honest bar,
and raising it would quietly re-tune the gate rather than the strategy.

**That reasoning is sound for a fold and does not transfer to the holdout.** A fold is one
of thirty-one, and the aggregation across folds is what carries the statistical weight. The
holdout is [one more fold](0014-the-holdout-is-spent-as-one-more-fold.md) run exactly once,
with nothing to aggregate over. Applying `score > 0` to a single sample asks whether a coin
came up heads.

The AAPL next-close premise is the worked example, and it is not hypothetical - it is the
only holdout this project has spent:

| Measure                     | Value                           |
| --------------------------- | ------------------------------- |
| Net expectancy              | **+0.035130 R** over 111 trades |
| Bootstrap standard error    | **0.1034 R**                    |
| Score in standard errors    | **0.34**                        |
| 90% interval                | **[-0.126, +0.209] R**          |
| Win rate against breakeven  | 54.05% against 51.68%           |
| Drawdown against total made | 8.80 R against 5.44 R           |

It passed. The rule it passed under could not distinguish it from zero, and nothing in the
record said so - the written verdict was `score 0.03512995832956881644386648191 above 0`,
which is twenty-nine significant figures of precision about a quantity known to none.

[`RESEARCH.md`](../playbook/RESEARCH.md) already required the missing half. Under *Evidence
sufficiency* it says: do not use a fixed closed-trade count, predeclare the requirement from
**effective** rather than raw sample size, report **dependence-aware intervals**, and if the
effective sample is too small or the intervals too wide, *extend the history, forward test
for longer, simplify the claim, or reject*. **No code did any of it.** The gap was between
the playbook and the evaluator, not in the playbook.

## Decision

**A holdout passes only if the score clears the bar and the interval clears it too.**
The point estimate remains necessary and stops being sufficient.

**The interval is a moving-block bootstrap**, which is the playbook's *dependence-aware
resampling*. Its block length is the largest of three quantities, none chosen at spend time:
`n**(1/3)` (the textbook order for a mean), the measured concurrency of the trades' holding
intervals, and the integrated autocorrelation time of the return series. Taking the largest
always widens the interval; each term is a separate reason the raw trade count overstates the
evidence, and a rule that averaged them could be argued down by whichever happened to be small.

**The verdict has three outcomes, not two.**

| Verdict                 | Means                                                 |
| ----------------------- | ----------------------------------------------------- |
| `pass`                  | Score above the bar, and the interval stays above it. |
| `insufficient_evidence` | Score above the bar, interval straddles it.           |
| `fail`                  | Score at or below the bar.                            |

The middle outcome is the substance of this ADR. A thin positive result is not a failure -
the premise was not disproven - and calling it one discards what was learned. It is also not
a pass. Collapsing the two into a bit is precisely what let AAPL through, and it is what made
the owner's `revise` decision an act of judgement against the tool rather than with it.

**The bar itself stays at zero and is predeclared per activation.** `minimum_effect_r` lives
in the activation's committed TOML, defaults to zero, and can only be raised. Setting it above
zero is a claim about economics - *an edge smaller than this is not worth trading at the
account this premise is aimed at* - not about statistics, and it must be written down before
the number is known.

**The interval is reproducible.** Fixed seed, fixed replicate count, no operator input at
spend time, so `copilot.strategies.validate` can recompute it from a commit like every other
number in a verdict.

## Consequences

- **The AAPL holdout would have returned `insufficient_evidence`.** Recomputed on
  2026-09-10 from the original cost snapshot, it reproduces +0.035130 R over 111 trades
  exactly and the interval is [-0.126, +0.209] R. The record is **not** rewritten: it says
  what was decided on the evidence available, and the owner's `revise` decision was the
  right one. A clearly-labelled retrospective block is appended beside it.
- **This is a strictly higher bar, and it is meant to be.** Nothing that passed before fails
  now for a reason unrelated to evidence, and some things that passed before will not pass.
  That is the correction, not a side effect of one.
- **It applies to the holdout only.** Fold thresholds are untouched, so no walk-forward
  verdict moves and no verdict needs recomputing. The concern that raising a bar re-tunes the
  gate is real, and confining the change to the single-shot test is what answers it.
- **A premise can now be told it needs more evidence rather than a different premise.**
  Pooling across instruments, extending history and simplifying the claim are the playbook's
  named remedies, and `insufficient_evidence` is the verdict that points at them. The pooled
  cross-symbol gap fade already on the roadmap is the first candidate.
- **A holdout is still single-use.** Nothing here creates a second attempt: an
  `insufficient_evidence` result spends the holdout exactly as a pass or a fail does
  ([ADR-0021](0021-an-unscorable-spend-still-consumes-the-holdout.md)). The remedy is a new
  experiment, not a re-run.
- **Revisit trigger:** if a premise reaches an interval that clears zero and the pass still
  looks unsafe, the missing constraint is a predeclared *effect size* rather than a wider
  interval, and `minimum_effect_r` is where it goes. Reopen this if that knob starts being
  set case by case after results are known, which would be the failure mode this ADR is
  trying to prevent.
