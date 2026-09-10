# 25. Commission is modelled per plan, and the plan is pinned

- **Status:** Accepted
- **Date:** 2026-09-10
- **Deciders:** Project owner (switch approved 2026-09-10); mechanics proposed in-session

## Context

[ADR-0009](0009-cost-is-modelled-at-the-target-account-size.md) made cost at the target
account size the defining economic constraint of this project, and commission is the half of
it that does not shrink with size. The model charged one hard-coded schedule:

```python
COMMISSION_PER_SHARE = Decimal("0.005")
COMMISSION_MIN = Decimal("1.00")
COMMISSION_MAX_PCT = Decimal("0.01")
```

Three constants with no plan attached, so a verdict's R figure did not say which broker
plan priced it.

**The 2026-09-10 shakedown measured the real thing.** Its `round-trip` step opened and
closed one share of AAPL on purpose, and the account was charged **USD 2.01**. A three-share
trip the same session cost **USD 2.02**. A bare `max(1.00, 0.005/share)` charged twice is
2.00 exactly, both times. The account is therefore on **IBKR Pro Fixed** - the USD 1.00
per-order minimum, twice - and the model was one to two cents light on every trade.

The cent is the **regulatory pass-through**, which both plans levy on the sale:

| Fee | Rate |
| --- | --- |
| SEC transaction fee | 0.0000206 x value of sales |
| FINRA Trading Activity Fee | 0.000195 per share sold |
| FINRA Consolidated Audit Trail | 0.000003 per share |

On one share at 322.52 that is 0.0068, and on three it is 0.0205 - which rounds to exactly
the cent and the two cents the broker charged. **The arithmetic reproduces both measured
trips, so the omission is established rather than suspected.**

The plan choice matters far more than the cent. IBKR Lite is disqualified outright: it has
no API access, and this system is an API. Inside Pro:

| | Fixed | Tiered (<=300k shares/month) |
| --- | --- | --- |
| Per share | 0.005 | 0.0035 |
| Minimum per order | **1.00** | **0.35** |
| Maximum per order | 1% of value | 1% of value |
| Exchange and clearing | absorbed | **passed through** |

**At this account's order sizes the minimum is the entire cost.** USD 20 of risk on a stock
near USD 320 with a stop a few dollars wide is two or three shares; even USD 62.50 of risk
on a USD 25,000 account is about nine. There the two minimums differ threefold, and the
round trip moves from **0.100 R to 0.036 R** at the charter's USD 20 risk. The pooled
walk-forward's edge in the era that actually pooled was +0.017 R. Fixed eats that four
times over.

## Decision

**Commission is a `CommissionSchedule`, and there are two of them.** `FIXED` and `TIERED`
carry their own per-share rate, minimum, cap and pass-through. `CostModel` takes one, so a
plan change can be *measured* before it is made.

**Tiered is not Fixed with a lower minimum, and must not be modelled as one.** Dropping
`COMMISSION_MIN` to 0.35 and stopping there would have been the obvious change and would
have been wrong: Tiered passes the exchange access fee and clearing through, so its all-in
per-share rate is *above* Fixed's flat one. The crossover is real and measured:

| Shares | Fixed | Tiered |
| --- | --- | --- |
| 1 | 2.000 | **0.706** |
| 9 | 2.000 | **0.758** |
| 149 | 2.000 | **1.997** |
| 200 | **2.000** | 2.680 |
| 1000 | **10.000** | 13.400 |

**Around 150 shares Fixed becomes cheaper.** A model that skipped the pass-through would
have claimed Tiered wins everywhere and understated it by 34% at a thousand shares.

**The pass-through is charged at the liquidity-removing rate and is not capped with the
commission.** IBKR's *maximum per order* caps what IBKR charges; an exchange's access fee is
not IBKR's to cap, and folding it inside would under-charge the small high-priced orders
this account actually trades. Charging the taker rate rather than a maker rebate matches the
conservatism [ADR-0011](0011-spread-is-charged-at-p95-from-a-pinned-snapshot.md) and
[ADR-0019](0019-spread-is-charged-from-measured-history.md) already chose for spread: a
resting limit can earn a rebate instead, and pricing the favourable half of a mix we cannot
predict is how a cost model flatters itself.

**The access fee is 0.003, the cap in force today, not the 0.001 that has been adopted.**
The SEC amended Reg NMS Rule 610(c) in September 2024 to cut the cap from 0.003 to 0.001 per
share for NMS stocks at or above USD 1.00. **The compliance date is 2026-11-02** - pushed
back from November 2025 by exemptive order, and SIFMA was still asking for a further
extension in May 2026. Charging 0.001 today would price a rule that is not yet operative,
in the direction that flatters Tiered.

**The pinned plan stays `FIXED` until the account is actually switched.** A verdict priced
on a plan the broker is not running is a verdict about a different account. The switch is a
Client Portal action and it is the owner's; this ADR records the decision and the model that
will price it.

## The revalidation says Tiered is marginally worse, and that is the crossover talking

Run over all twelve activations under both plans, the walk-forward verdicts move by
thousandths of an R and they move *against* Tiered. That is not a contradiction and it is
not a reason to reconsider. It is the crossover, and it is worth stating exactly because a
reader comparing the two tables would otherwise conclude the switch is a mistake.

The activations set `risk_budget = 1000`. At a five-dollar stop that is **200 shares** - the
one place on the curve where Fixed is cheaper:

| Risk per trade | Shares | Fixed | Tiered | Cheaper |
| --- | --- | --- | --- | --- |
| 20 (the charter's) | 4 | 0.1014 R | **0.0376 R** | Tiered, by 2.7x |
| 62.50 (0.25% of USD 25,000) | 12 | 0.0333 R | **0.0137 R** | Tiered, by 2.4x |
| 250 | 50 | 0.0094 R | **0.0054 R** | Tiered |
| 1000 (research sizing) | 200 | **0.0034 R** | 0.0040 R | Fixed, by 0.0006 R |

**The verdicts are priced at the only sizing on this curve where Fixed wins, and they win by
six ten-thousandths of an R.** At the sizing the charter actually trades, Tiered is cheaper
by 0.064 R - a hundred times that difference, and nearly four times the pooled premise's
whole measured edge.

This is [ADR-0009](0009-cost-is-modelled-at-the-target-account-size.md)'s point arriving from
a new direction. Spread in R is size-independent, because quantity cancels out of it.
Commission is not, and at a research sizing of USD 1,000 per trade it looks **thirty times
cheaper** than at the charter's USD 20. `calibration/account_sweep.py` is where the at-size
question is asked properly, and running it under both plans is the row this decision leaves
open.

## Consequences

- **The switch is right for trading and near-neutral for the verdicts.** Nothing in the
  walk-forward record moves by more than a few thousandths of an R, so no verdict flips on
  the plan alone; the gain is entirely in live execution at the charter's sizing.
- **Every verdict's cost basis moves, twice.** Once now, by the regulatory cents the model
  omitted, and again when the plan is switched. The verdict record names its schedule, so a
  number can no longer be read without its plan. A revalidation pass under both plans is
  filed beside this decision.
- **The switch is approved and is the owner's to make.** After it, `SCHEDULE` moves to
  `TIERED` in a commit of its own and every verdict is recomputed - the same sequence
  ADR-0011 and ADR-0019 followed for spread.
- **The crossover is a constraint on growth, not just a fact.** If sizing ever reaches
  roughly 150 shares an order, Tiered stops being the cheaper plan. At a five-dollar stop
  that is USD 750 of risk per trade, which at the charter's 0.25% is an account near
  **USD 300,000** - far off, but the kind of thing that silently stops being true, so it is
  written down here rather than rediscovered.
- **Three modules read `COMMISSION_PER_SHARE`, `COMMISSION_MIN` and `COMMISSION_MAX_PCT`.**
  They are kept as aliases of the pinned schedule so this change does not ripple, and they
  will be retired when the second caller of a non-default plan appears.
- **Revisit trigger: 2026-11-02.** If the access fee cap takes effect at 0.001, Tiered's
  all-in rate falls to roughly 0.0047 per share, below Fixed's flat 0.005, and **the
  crossover disappears entirely** - Tiered becomes cheaper at every size. Re-measure then. If
  the compliance date is extended again, this ADR stays correct and the trigger moves with
  it.
