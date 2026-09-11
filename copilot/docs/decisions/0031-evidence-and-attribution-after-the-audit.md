# 31. Evidence and attribution, as corrected by the audit

- **Status:** Accepted. Amends the worked example of [ADR-0024](0024-a-holdout-pass-needs-an-interval.md)
  and the reading of [ADR-0026](0026-attribution-is-measured-per-trade.md); both decisions stand.
- **Date:** 2026-09-11
- **Deciders:** Project owner (audit corrections approved 2026-09-11); mechanics proposed in-session

## Context

The audit of 2026-09-11 ([`AUDIT_2026-09-11.md`](../AUDIT_2026-09-11.md), batch C) re-derived
the evidence interval and the attribution from the filed records. Both reproduced to six places,
and the three claims resting on them held: no four-factor alpha in any result, the pooled
interval not clearing zero, the AAPL holdout thin. What did not hold was how the records described
those numbers, and two places where the machinery did less than its ADR said:

- **ADR-0024's worked example is the gross interval**, [-0.126, +0.209] R, while the score beside
  it was net; the net interval is [-0.140, +0.195] R (F21). ADRs are not edited, so the correction
  lives here.
- **ADR-0024's one knob was never set**: every activation filed `minimum_effect_r` empty, so the
  rule against moving the bar after the result guarded nothing (F16).
- **`effective_trades` equalled the raw count** on nine of twelve verdicts, because the dependence
  it measured was in trade order and the clustering that motivated it is by calendar year (F17).
- **ADR-0026's bracket was described as a bound** - including the exit session flatters alpha -
  and the filed AAPL result runs the other way (F19).
- **The fit is unweighted over a leverage of 5 to 380, on a bracketed payoff** (F18), and thirteen
  results sharing trades were read as corroborating each other (F24).
- **The walk-forward dropped a position still open at a fold's end** (F27), and the pool changed
  its membership inside one verdict (F23).

## Decision

- **The AAPL holdout's net interval, as spent, is [-0.140, +0.195] R.** Anything quoting ADR-0024's
  example reads this. Re-scored under the rules below it is [-0.135, +0.198] R over 112 trades,
  still `insufficient_evidence`. The record's reassessment is now written by `spend_holdout --reassess`, with its
  trades, and attribution reads it.
- **A holdout cannot be spent without a predeclared effect size**, and the newest walk-forward
  verdict for the activation must have been filed under the same value. Zero is a valid
  declaration; empty is not.
- **Effective trades divide by the largest of concurrency, the trade-order autocorrelation time,
  and the calendar-year design effect** `1 + (m - 1) rho`. The interval's block length is
  unchanged: whole-year blocks moved no verdict when checked.
- **The exit-session bracket is an agreement test.** A verdict is named where both treatments
  agree, and neither side is read as a bound.
- **The unweighted fit stays the verdict; an exposure-weighted fit is filed beside it** as a
  sensitivity, weighting each trade by `1 / (L^2 h)`. The records say the results are one body of
  evidence, and that no multiplicity adjustment is applied - unnecessary to reject, necessary
  before anything is called alpha.
- **The attribution floor of thirty trades is a refusal floor**, six observations per coefficient;
  a singular resample is skipped and counted, and more than one in twenty withholds the interval.
- **A position open when a replay window ends is marked to that window's last close** and scored,
  with `exit_reason` `WINDOW_END`; one opened on the last bar has no outcome and is counted.
- **A pool holds its membership constant by default**, clipped to the window every member covers.

## Consequences

- **Every verdict, the pool, the holdout reassessment and the attribution were refiled** under
  these rules on 2026-09-11; the changelog has the before and after. The gap-fade family's
  rejection stands under all of them.
- **The constant-membership pool is one fold of 137 trades**, against 38 folds for the shifting
  one, because AAPL's development window ends at 2022-01-01 and the youngest members start after
  2017. That is the honest size of a pooled experiment over this universe; a longer one needs a
  universe with a longer common history. The shifting pool is filed beside it for comparison.
- **Premises from here declare their effect size with their hypothesis.** The research playbook's
  evidence-sufficiency step already asked for it; the spend now refuses without it.
- **The weighted and unweighted fits already disagree between "negative" and "none detected"**
  on several gap-fade results, in both directions; neither finds alpha on any. That is the
  sensitivity doing its job, not a reason to change the verdict's fit.
- **Revisit trigger:** a premise where one fit finds alpha and the other does not - then which fit
  is the verdict becomes a real question, and it should be settled before a holdout is spent.
