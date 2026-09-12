# 33. The turn of the month's single-use test is forward, not carved

- **Status:** Accepted. Owner concurred 2026-09-12 with re-pinning; the arithmetic refused it,
  and this is what was done instead.
- **Date:** 2026-09-12
- **Deciders:** Project owner (concurred 2026-09-12); measured and proposed in-session
- Applies to `spy-turn-of-month` only. It does not change [ADR-0012](0012-the-holdout-is-carved-at-2022-01-01.md)'s
  shared pin, [ADR-0014](0014-the-holdout-is-spent-as-one-more-fold.md), or
  [ADR-0021](0021-an-unscorable-spend-still-consumes-the-holdout.md).

## Context

On 2026-09-11 the randomised-signal control's first run carved on the activation's own holdout
boundary. `spy-turn-of-month` declares none, so it inherits the shared 2022-01-01 pin, and the
code read *no boundary of its own* as *nothing withheld*: the run covered 2005-01-03 to
2025-12-31, the locked holdout included.

What was seen was an **aggregate** - 251 trades at +0.1080 R against a null mean of +0.0381 R,
percentile 91.8, one-sided p 0.0838 - and not per-trade holdout detail. No parameter was
selected against it and no decision was taken from it. The record was deleted and the reading is
void. The runner now carves through the gate's own `carve(bars, holdout_start=...)` and a test
pins the window. What remained open was not the code but the question of **what that holdout now
is**, and the roadmap carried two honest options: treat it as consumed on ADR-0021's reasoning
that a look is a look, or re-pin this activation's boundary earlier and keep a real single-use
test.

The owner concurred with re-pinning. Executing it showed it does not work, and the reason is
arithmetic rather than judgement.

**Every boundary the charter's band admits carves a holdout the void run already read.** The
band reserves 15-20% of the evaluation window, which for SPY's 5,283 in-window bars is 792 to
1,056 bars. Measured against the catalog:

| Boundary   | Holdout bars | Share of window | In band             |
| ---------- | ------------ | --------------- | ------------------- |
| 2021-10-01 | 1,067        | 20.20%          | No                  |
| 2021-11-01 | 1,046        | 19.80%          | Yes                 |
| 2022-01-01 | 1,003        | 18.99%          | Yes, the shared pin |
| 2022-04-01 | 941          | 17.81%          | Yes                 |
| 2022-11-01 | 794          | 15.03%          | Yes                 |
| 2022-12-01 | 773          | 14.63%          | No                  |

So the boundary may move, at most, from 2021-11-01 to 2022-11-01. Every holdout it can carve
lies wholly inside 2005-01-03 to 2025-12-31, and that whole span is what the void run read.
Moving the pin earlier enlarges the holdout with bars that were **also** in the aggregate; it
buys no unread history. "Re-pin earlier and keep a real single-use test" is not available at any
date, and doing it anyway would produce a holdout that looks pristine in the record and is not.

The one span the void run provably never touched is the other side of the far pin. `carve` clips
bars at or past `EVALUATION_END` (2026-01-01) before it splits, and carries them as
`unevaluated`; the catalog holds **174 such bars, 2026-01-02 to 2026-09-11**, and the paper VM
starts producing more from 2026-09-15.

## Decision

- **The carved holdout is compromised, and is not this premise's single-use test.** It is not
  "spent" in ADR-0021's sense - nothing was decided from it and nothing was selected against it
  - but it is not pristine either, and a pass over it must never be quoted as a clean
  out-of-sample result.
- **The boundary stays at the shared pin.** No re-pin, because no in-band re-pin helps. The
  activation continues to declare no `holdout_start`, so nothing in the fold geometry or any
  filed digest moves on account of this decision.
- **The single-use test is forward.** The premise earns its out-of-sample evidence on the VM's
  paper clock from stand-up, against sessions that did not exist when the look happened. That is
  the one test this incident cannot have contaminated.
- **`spend_holdout` refuses this activation by name**, citing this ADR. Today it is refused
  anyway, because a `signal_close` activation may not spend a holdout at all
  ([ADR-0013](0013-entry-timing-is-evaluated-as-a-bracket.md)) and this one declares no
  `entry_timing`. That protection is **accidental**: the card contemplates re-expressing the
  premise on intraday bars, and the day it becomes a `next_close` activation the ADR-0013
  refusal stops firing and the look would be forgotten. The named refusal is what survives that.

## Consequences

- **This premise cannot be advanced by a carved holdout pass.** Its only route to a frozen
  candidate is forward evidence, which is also what its own interval asked for: 144 scored
  trades, 111.7 effective, cannot resolve an edge near 0.1 R.
- **Forward testing it is a monitoring commitment, not a fast path, and the arithmetic should be
  stated rather than discovered.** The rule trades about twice a month, so roughly 24 trades a
  year. Reaching the couple of hundred trades the interval needs is a multi-year wait. The
  premise is therefore something the paper campaign *carries* while other work proceeds, not
  something that resolves in a sprint - which is precisely why running it on an always-on host
  costs little.
- **The guard is a named table, not a registry field.** A `holdout_looked_at` flag in the
  activation's `[validation]` table would enter `identity_digest`, moving every activation's
  identity and forcing twelve recomputes for a fact that changes no number. The fingerprint
  exists to avoid exactly that, so the marker lives in `spend_holdout.py` beside the other
  refusals, where `refusal()` can read it without a catalog.
- **Revisit trigger: any move of `EVALUATION_END`.** This decision rests on the forward span
  being unread. Moving the far pin forward pulls those bars inside the window and into the
  development side of the carve, at which point the claim has to be re-checked against whatever
  has been looked at by then, and this ADR revisited rather than assumed.
- **The incident's real lesson is already fixed in code**, not here: a boundary of `None` meant
  "the shared pin", and one caller read it as "no holdout". Everything that carves now goes
  through `carve`.
