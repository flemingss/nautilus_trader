# 5. A setup is code; activation is data

- **Status:** Accepted
- **Date:** 2026-09-01
- **Deciders:** Project owner
- **Descends from:** trade-copilot ADR-0024 (strategy control layer). The split is kept;
  the substrate is changed.

## Context

Configuration in the overlay is scattered across five mechanisms with no single surface:
environment variables under two prefixes, CLI arguments in one module, roughly forty
module-level constants that mix real policy with true constants like `HTTP_SERVER_ERROR`,
dataclass defaults, and pyo3 `StrategyConfig` fields.

Worse, the distinction that matters most is expressed nowhere: **which knobs the
validation gate may search, and which are the strategy's identity.** trade-copilot learned
that one expensively (its V1-30), where the grid rebuilt every candidate from contract
defaults, so validating a short-leg strategy silently validated the long leg and filed the
result against the wrong row.

The immediate symptom: no `ParameterGrid` is constructed anywhere in committed code. The
first real walk-forward verdict this fork produced **cannot be reproduced from the
repository**, because the search space existed only in a scratch file.

## Decision

Adopt trade-copilot's split - **a setup is code, activation is data** - with the knob
store as version-controlled files rather than database rows.

Every configurable value is classified into exactly one of three buckets:

| Bucket          | Meaning                                                          | Lives in                                |
| --------------- | ---------------------------------------------------------------- | --------------------------------------- |
| **Searchable**  | The gate may move it. Its range is a property of the premise.    | `SEARCH_SPACE` beside the strategy code |
| **Identity**    | Fixed for an activation. Seeds that activation's own validation. | A registry file                         |
| **Environment** | Connection, paths, credentials. Never affects a result.          | Environment variables                   |

- `copilot/strategies/<name>.py` carries `SEARCH_SPACE`, because the reasoning that sized
  it belongs with the code that implements it. The gap fade's thresholds were chosen by
  counting events, and a test pins the ceiling so an axis cannot be widened without
  re-counting.
- `copilot/strategies/registry/<activation>.toml` carries symbol, venue, lifecycle
  (`RESEARCH` / `PAPER` / `LIVE`), risk budget, and any unsearched parameter that differs
  from the contract default.
- **Unsearched parameters seed the activation's own validation.** Searched axes still win,
  so an activation can never quietly narrow its declared search.

Files rather than a database because there is no database and acquiring one for this would
be a large dependency for a small need - and because a git-tracked file is a **better**
audit record than a mutable row: it shows who moved a threshold, when, and what the diff
was.

## Consequences

- A gate run becomes reproducible from a commit, which it currently is not.
- The registry is reviewable in a pull request, so moving a risk budget is a visible act.
- Lifecycle is a field, not a database state. Promotion `RESEARCH` to `PAPER` to `LIVE` is
  a diff.
- The roughly forty module constants must be classified. Genuine constants stay where they
  are; policy defaults move.
- One more file to keep in step with the code, mitigated by the registry being read by the
  same loader the gate and the live node both use.
