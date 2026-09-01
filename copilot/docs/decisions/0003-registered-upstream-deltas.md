# 3. Upstream changes are permitted but registered

- **Status:** Accepted
- **Date:** 2026-09-01
- **Deciders:** Project owner
- **Supersedes:** the "change zero upstream files" rule recorded in `AGENTS.md`

## Context

The original rule forbade changing any file outside `copilot/`. It had one great
property: it was **self-enforcing**. The carrying cost was visibly zero, so nothing had to
track it.

The rule cost more than it saved. Two Interactive Brokers adapter defects were found and
fixed but could not be adopted; the risk engine's `TradingState` was enforced natively in
Rust and unreachable from Python, leaving the account-wide breakers able only to cancel
and flatten after the fact.

Permitting changes removes the self-enforcing property. The cost stops being zero and
stops being visible, and a register that nobody checks decays into fiction within a few
commits.

## Decision

Upstream changes are permitted where they are worth it, subject to registration.

- Every file outside `copilot/` that this fork changes has a row in
  `copilot/docs/UPSTREAM_DELTA.md` giving what changed, why, and **what it would cost to
  drop**.
- `copilot/tests/test_upstream_delta.py` compares the register against the real diff,
  including uncommitted work, and fails on a file with no row. Registration is part of
  making the change, not paperwork afterwards.
- Prefer changes upstream would plausibly accept: those are deltas with an expiry date.
  A fork-only behaviour change is a permanent bill.
- Keep each change to the smallest surface that works, and prefer new files, which cannot
  conflict at all.
- Off limits regardless: `RELEASES.md`, `.github/workflows/`, `.github/actions/`, and the
  root `ROADMAP.md`. All are maintainer-owned upstream.

## Consequences

- The delta is countable and its risk is reportable:
  `python -m copilot.tools.upstream_delta` shows whether upstream has since touched the
  same files and whether a merge would conflict.
- It grew from 2 files to 9 within a day of the rule changing, which is the expected
  direction and the reason the register exists.
- **We can no longer run against a published wheel.** Carrying Rust changes means the
  runtime must be built from source; see `0007`.
- The register is the review list at sync time, so a sync is reading a short list rather
  than an excavation.
