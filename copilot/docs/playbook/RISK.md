# Risk and sizing

Position sizing, the limits around it, and the gate that asks whether a premise survives at
the account that would actually trade it.

Strategy risk controls and system safety controls are **separate**. This page is the first.
The kill switch, which is the second, lives in [`OPERATIONS.md`](OPERATIONS.md) and does not
sit inside the strategy.

## Size from the stop, not from a share count

A fixed share count puts the instrument's price level inside its own score: the same
percentage move on a name at 450 and one at 180 produces different currency swings, an
absolute threshold means a different strictness per instrument, and no two instruments'
expectancies are comparable. Sizing from the stop makes a stop-out cost the same everywhere,
which is what makes R a scale-free unit.

Given account equity `A`, planned risk fraction `r`, entry `P`, an **executable** protective
stop trigger `S`, and a stressed per-share allowance `g` for gaps, slippage and fees:

```text
R = A * r
q = floor( R / (|P - S| + g) )
```

Then apply every cap, and take the smallest:

```text
q = min( q,
         floor(A * c / P),               # max fraction of equity in one position
         floor(C_settled_net / P),       # settled USD cash after fees and working buys
         floor(G_remaining / P) )        # remaining permitted gross notional
```

Recompute all three after **every** fill, cancel, FX conversion and corporate action.

Two constraints that are easy to lose:

- `S` must be a stop that can actually be acted on. An end-of-day signal or invalidation
  level that cannot be traded until the next session is not a stop, and using it as one
  understates risk by the size of the overnight gap.
- If the strategy exits only after a completed daily bar, `g` must come from a **tested
  next-session stress loss**, not from an assumption that `|P - S| + g` bounds the loss.

Implemented in `copilot/risk/sizing.py`. Quantity is **floored, never rounded** - rounding
up puts more at risk than the budget allows, which is the one direction a risk control must
not err in. Realised risk is recorded per trade rather than assumed equal to the budget,
because flooring leaves it at or just under.

### Worked example

USD 8,000 account, 0.25% planned risk, so `R` is USD 20. Entry USD 100, stop distance USD 4,
buffer USD 0.50:

```text
q = floor(20 / 4.50) = 4 shares, USD 400 notional, 5% of equity
```

A stop is not a loss guarantee. Gaps produce larger losses, which is what `g` is for and
why it is stressed rather than typical.

## Initial live-risk defaults

Conservative starting policy, not law. Declare any change before testing it.

| Limit                            | Default                             |
| -------------------------------- | ----------------------------------- |
| Planned risk per position        | 0.10% to 0.25% of equity            |
| Maximum total open planned risk  | 0.50% to 1.00%                      |
| Maximum single-position notional | 10% until diversification is proven |
| Maximum daily new entries        | Small and predefined                |
| Maximum daily loss               | Independent of the strategy         |
| Leverage, shorts, averaging down | None                                |

If prudent quantity rounds to zero, **skip the trade**. If estimated net edge is not
comfortably larger than estimated all-in cost, skip it too.

## Account-wide breakers

> **`max_notional_per_order` does not work on Interactive Brokers.** Measured at paper stage
> six on 2026-09-01. The risk engine resolves the account by the *instrument's* venue; on IB
> instruments resolve on `SMART` while the account sits on `IB`, so the lookup fails and the
> whole check returns "allow" - including a cap the operator configured explicitly. It is
> reported at `DEBUG` and nowhere else.
>
> Until that is fixed, **`TradingState::HALTED` is the only pre-trade control this system
> has.** It is enforced natively in the Rust risk engine and was verified surviving node
> startup at stage one, so the account-wide breakers in `risk/guard.py` are real. Do not
> write a limit that depends on the per-order cap and assume it holds.

Beyond per-order limits, `copilot/risk/protections.py` enforces sequence-aware limits: a run
of consecutive stop-outs, and peak-to-trough realised drawdown over a rolling window, each
opening a cooldown with the longest-running breach winning.

Enforcement is **at the engine**, not after the fact. On breach the guard halts the Rust
risk engine first, so subsequent orders are denied before reaching an execution client, then
cancels working orders account-wide and flattens configured instruments. Cancelling emits
orders of its own, which is why the halt lands first.

Supply the engine handle or this degrades to cancel-and-flatten and says so in a warning.
Take it **before** starting a run - see
[`nautilus-risk-engine-binding`](../decisions/0003-registered-upstream-deltas.md) and
`copilot/risk/guard.py`.

## The cost-at-size gate

**Measured, 2026-09-01.** This gate was built from a fee schedule. Paper stage five then put
a real round trip through the broker and charged it: 3 shares of AAPL, **USD 2.02 commission**
on USD 947 deployed, against a price move of 27 cents. Commission was **88% of the loss**.

At USD 20 of risk that is **0.1010 R**, against the 0.11 R this gate predicted - within ten
percent. Against AAPL's walk-forward gross expectancy of +0.0492 R it leaves **-0.0519 R
before spread**. The gate's conclusion is now an observation rather than an inference.

**A cost model is only meaningful at a stated account size, and a premise is only a
candidate if it survives at the size it would actually trade**
([ADR-0009](../decisions/0009-cost-is-modelled-at-the-target-account-size.md)).

This is not a refinement. It is the gate that has so far rejected the only premise this
repository has researched.

### Why R is not scale-free after all

Quantity cancels out of the spread term:

```text
spread_R = 2 * (bps_per_side / 10_000) * notional / risk
         = 2 * (bps_per_side / 10_000) * price / stop_distance
```

so spread cost in R depends on the ratio of price to stop distance and on nothing else -
not on the account, not on the budget. It is tempting to carry that conclusion across the
whole cost model.

**Commission breaks it.** A per-order minimum does not scale down with position size, so in
R terms it grows without limit as the budget shrinks.

### What that does to a real premise

The gap fade, repriced across account sizes at the p95 spread:

| Risk per trade               | AAPL       | MSFT       | SPY        |
| ---------------------------- | ---------- | ---------- | ---------- |
| USD 1,000 (research default) | +0.040     | +0.071     | +0.053     |
| **USD 20** (8k at 0.25%)     | **-0.065** | **-0.037** | **-0.059** |
| USD 8 (8k at 0.10%)          | -0.246     | -0.202     | -0.263     |
| USD 125 (50k at 0.25%)       | +0.026     | +0.062     | +0.040     |
| USD 500 (200k at 0.25%)      | +0.038     | +0.070     | +0.052     |

At USD 20 of risk the position is 5 to 23 shares and a round trip costs USD 2.00 whatever
its size: **0.11 R against a gross edge of 0.05 to 0.09 R**. At 0.10% risk, 31 to 131 trades
per symbol do not size at all.

Spread at the same point is 0.007 R. **Commission is fifteen times larger**, which makes the
median-versus-p95 spread question - which had its own analysis - noise beside the order
minimum.

### What follows for premise selection

The gate favours **wider stops, longer holds and fewer trades**, because fixed cost per
trade is what dominates at small size. That is the opposite of what a naive search for
higher expectancy selects, and it is worth knowing before choosing the next premise rather
than after researching it.

Run it with:

```bash
python -m copilot.calibration.cost_impact --write
```

Report the **sweep**, not a single figure. The useful output is the equity at which the
premise crosses zero. A premise viable only above the current account is a finding about
what this account can trade - recorded and shelved, not a strategy waiting for capital.

## Checklist

- [ ] `S` is an executable stop, not an end-of-day invalidation level
- [ ] `g` comes from a tested stress loss where exits are next-session
- [ ] Quantity floored, and all three caps applied
- [ ] Caps recomputed after every fill, cancel, FX conversion and corporate action
- [ ] Cost swept across account sizes, with the zero-crossing equity reported
- [ ] Per-order minimums modelled explicitly, not as a percentage of notional
- [ ] Trades that round to zero are skipped, not rounded up
- [ ] Engine handle supplied to the guard, verified at startup
