# 32. A triggered next-close entry is placed as a marketable day limit, cancelled by 10:30

- **Status:** Accepted. Owner concurred 2026-09-11 with the roadmap's recommendation.
- **Date:** 2026-09-11
- **Deciders:** Project owner (concurred 2026-09-11); mechanics proposed in-session
- Applies to a `next_close` trigger. It does not cover a premise that enters on a scheduled
  calendar session, which fills at a close it did not decide on.

## Context

A `next_close` rule decides on the close of bar *t* and the replay fills at the close of *t+1*
([ADR-0013](0013-entry-timing-is-evaluated-as-a-bracket.md)). Live, *t+1* opens about an hour
after the evening command runs, and the charter's execution rule is a **predeclared window in
the first one to two hours of the next session**, as a time-bounded DAY limit or marketable
limit, with a declared maximum concession, a partial-fill rule and a cancellation deadline.

Between those two sits a gap nothing has crossed. The evening records the trigger
(`deferred_atr`) and prints DEFERRED; the process ends; nothing carries the decision into its
session, so a triggered entry has never been placed. The gap fade is rejected, so no candidate
needs this today - which is the reason to decide it now, rather than while a frozen candidate
waits and the choice is made under pressure.

The audit of 2026-09-11 moved this out of *Ready to build* because building it means choosing
it. This is the choice.

## Decision

- **The order is a DAY marketable limit at the decision close plus a declared concession**,
  submitted when the session opens, priced from the close the rule decided on rather than from
  the open. A market order is not used: the first five minutes run several times the spread of
  the rest of the window, measured at 6.2x on AAPL and 23x on PEP, and a market order accepts
  whatever that is.
- **The concession is declared per activation**, as a multiple of the frozen ATR, and a trigger
  whose activation declares none does not trade. A concession chosen after a fill is not a
  concession.
- **It is cancelled by 10:30 Eastern if unfilled**, which is the charter's one-hour window and
  the sweep's own deadline. An unfilled trigger is recorded as such and **is not carried** into
  a later session: the premise that produced it was scored on the next session's close, and a
  fill two sessions later is a different trade.
- **A partial fill is kept**, and its bracket is sized to the filled quantity. The remainder is
  cancelled at the deadline rather than left working.
- **The stop and target are placed from the fill price and the ATR frozen at the decision
  close**, not from the decision close itself. The fill is what the position costs; the ATR is
  what the rule decided with.
- **The verdict stays the `next_close` bound.** A live fill inside the window is not comparable
  with a replay that fills at the close, and the comparison records the difference rather than
  reconciling it. Re-expressing the premise on Databento intraday bars in that same window is
  ADR-0013's own revisit trigger, and it is the thing that would make the two comparable.

## Consequences

- **Nothing is built now.** There is no frozen candidate, and the entry path is stage-seven work
  that a candidate gates. What this ADR removes is the design question from the critical path.
- **An activation that means to trade live carries a declared concession** in its registry file,
  beside its stop and target multiples.
- **The recommended next premise does not use this path.** The turn of the month enters on a
  calendar session at the close, through a market-on-close order, and its decision does not
  depend on the price it fills at. If it is the candidate that reaches paper stage seven, this
  ADR stays unexercised until a `next_close` premise follows it.
- **Revisit trigger:** the first ten live fills. If the concession is used in full on most of
  them, the window or the price is wrong; if the deadline cancels most of them, the premise's
  edge is not reachable at this account and the cost-at-size gate should say so.
