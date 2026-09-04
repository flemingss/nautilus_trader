# Architecture decisions

One file per decision, numbered, never edited once accepted. A decision that stops being
true is **superseded by a new one**, not rewritten, so the reasoning that led here stays
readable after the conclusion changes.

## Re-derived, not lifted

trade-copilot carries 25 ADRs. They were **not copied**. Its architecture rests on an
assumption that no longer holds - a human placing every order by hand, because Fidelity
has no retail trading API - and lifting the set wholesale would import that assumption
into decisions that look unrelated to it. Each ADR here was decided again on its own
merits; where one descends from a trade-copilot ADR, it says so and says what changed.

## Index

| #                                                               | Decision                                                       | Status     |
| --------------------------------------------------------------- | -------------------------------------------------------------- | ---------- |
| [0001](0001-record-architecture-decisions.md)                   | Record architecture decisions                                  | Accepted   |
| [0002](0002-fork-local-overlay.md)                              | Fork-local work lives in `copilot/`                            | Accepted   |
| [0003](0003-registered-upstream-deltas.md)                      | Upstream changes are permitted but registered                  | Accepted   |
| [0004](0004-quarterly-upstream-sync.md)                         | Sync with upstream on demand, reviewed quarterly               | Superseded |
| [0005](0005-setup-is-code-activation-is-data.md)                | A setup is code; activation is data                            | Accepted   |
| [0006](0006-ops-progression.md)                                 | Ops progression: WSL and TWS, then Gateway, then Kubernetes    | Accepted   |
| [0007](0007-self-sourced-images.md)                             | We build and source our own images                             | Accepted   |
| [0008](0008-direct-api-execution.md)                            | Direct API execution supersedes the HITL assumption            | Accepted   |
| [0009](0009-cost-is-modelled-at-the-target-account-size.md)     | Cost is modelled at the target account size                    | Accepted   |
| [0010](0010-the-repository-is-ours.md)                          | The repository is ours; upstream is a source we read           | Accepted   |
| [0011](0011-spread-is-charged-at-p95-from-a-pinned-snapshot.md) | Spread is charged at p95 from a pinned snapshot                | Accepted   |
| [0012](0012-the-holdout-is-carved-at-2022-01-01.md)             | The holdout is carved at 2022-01-01                            | Accepted   |
| [0013](0013-entry-timing-is-evaluated-as-a-bracket.md)          | Entry timing is evaluated as a bracket                         | Accepted   |
| [0014](0014-the-holdout-is-spent-as-one-more-fold.md)           | The holdout is spent as one more fold                          | Accepted   |
| [0015](0015-databento-is-the-intraday-source-only.md)           | Databento is the intraday source, and only the intraday source | Accepted   |
| [0016](0016-corporate-actions-are-applied-on-read.md)           | Corporate actions are applied on read, from one table          | Accepted   |
