# 8. Direct API execution supersedes the HITL assumption

- **Status:** Accepted
- **Date:** 2026-09-01
- **Deciders:** Project owner
- **Descends from:** trade-copilot ADR-0002 (asset-class scope and target broker), whose
  broker assumption no longer holds.

## Context

trade-copilot was built for a Fidelity account with no retail trading API. Every signal
therefore ended at a human placing an order by hand, and the operator is in JST while the
US session runs overnight. Those two facts together forced the architecture: **daily bars
only, swing entries, nothing that acts within a session**, because a signal had to survive
until the operator woke up.

This fork trades through Interactive Brokers, which has a first-class API. The human gate
and the timezone constraint are both gone.

## Decision

- **Execution is direct and unattended-capable.** The system may act within a session
  without a human present.
- **The goals and the validation methodology port from trade-copilot unchanged.** The
  purged walk-forward, the stability-plateau selection, deflated pass probability and the
  R-multiple unit of account were never HITL-specific.
- **The premises do not port unchanged, and must be re-derived.** They were shaped by the
  constraint. The gap fade is the worked example: its own documentation states that
  entering at the next open is a weaker version of the published effect, chosen because
  "a literal buy-the-gap-down-open trade is not something daily bars can simulate". That
  compromise existed because the human could not act intraday. Re-running it under the new
  regime measures the old compromise, not the opportunity.
- **Scope stays US equities and ETFs, daily bars, for now.** The constraint is data, not
  architecture: there is no intraday history at any price yet. Removing the HITL
  constraint widens what is *permissible*, not what is currently *possible*.

## Consequences

- **The risk breakers change status from safety net to load-bearing.** Under HITL a human
  saw every order; unattended, the breakers are the only thing between a defect and the
  account. This is why reaching `set_trading_state` was worth an upstream delta: the guard
  now halts the engine rather than cancelling after the fact.
- Continuous operation is assumed. A machine that sleeps is intermittent, not unattended,
  and the guard's cooldown logic must be reviewed against restart behaviour before any
  paper run. See `0006`.
- Existing trade-copilot verdicts are not comparable to verdicts from this fork, on any
  premise, because the execution assumption differs.
- Intraday premises become worth researching once intraday data is bought. Until then this
  decision records that the door is open, not that we have walked through it.
