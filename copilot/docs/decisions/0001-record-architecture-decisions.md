# 1. Record architecture decisions

- **Status:** Accepted
- **Date:** 2026-09-01
- **Deciders:** Project owner

## Context

This fork accumulates decisions that are expensive to re-litigate and invisible in the
code once made: which directory work lives in, whether upstream files may be changed, how
often to sync, where knobs live. Several were already made and recorded only as prose
scattered through `AGENTS.md`, `ROADMAP.md` and commit messages, where the *conclusion*
survived but the *reasoning* did not.

The cost of losing the reasoning is specific. A rule whose justification is gone reads as
arbitrary, and the next person either follows it superstitiously or discards it without
knowing what it was protecting.

## Decision

Record architecturally significant decisions as numbered files in
`copilot/docs/decisions/`, in the format trade-copilot used, which is a light MADR
variant: Status, Date, Deciders, then Context, Decision, Consequences.

A decision is **never edited once accepted**. When it stops being true, a new ADR
supersedes it and both stay. `0003` superseding the zero-upstream-diff rule is the worked
example: the old rule was correct for its context and the record should show why it was
adopted as well as why it was dropped.

"Architecturally significant" means: it constrains future work, it was expensive to
decide, or a reasonable person would otherwise change it back.

## Consequences

- Decisions in this fork are traceable to reasoning rather than to habit.
- `AGENTS.md` keeps the operational rules; ADRs keep the reasons. When they disagree, the
  ADR is the record and `AGENTS.md` is the bug.
- Some duplication between `ROADMAP.md` and the decision set is accepted. The roadmap is
  the live state and changes constantly; ADRs are immutable history.
