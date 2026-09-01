# Agent Instructions

**Read [`copilot/docs/CHARTER.md`](copilot/docs/CHARTER.md) first.** It and the
[playbook](copilot/docs/playbook/README.md) govern the out-of-repo half of this project too,
so they outrank this file where the two overlap.

This repository is **ours**, not a fork ([ADR-0010](copilot/docs/decisions/0010-the-repository-is-ours.md)).
It began as a copy of NautilusTrader and has been detached from the fork network. There is no
parent repository, no pull-request relationship with anyone, and no change here is a
contribution to another project.

Follow the [coding standards](docs/developer_guide/coding_standards.md) and the relevant
developer guide for the area you change. Those documents were inherited and are good; they
are kept because they are good, not because we owe anyone conformance.

## Working rules

**This system can execute live trades involving real capital. Hold every change to a very
high standard for correctness, reliability, testing, clarity, and maintainability.**

- Read the affected code and search for existing patterns before proposing or making changes.
- Keep each change focused on the requested outcome. Note unrelated issues instead of fixing
  them.
- Match the existing style and use established functions, types, names, and dependencies.
- Preserve exact arithmetic for prices, quantities, money, fees, and other discrete values.
  Use the project domain types or `Decimal`.
- Do not add test-only behavior, branches, attributes, or interfaces to production code.
- Do not weaken, remove, bypass, or rewrite tests or required behavior merely to obtain a
  passing result. Fix the underlying problem and preserve the behavior the tests are intended
  to protect. Change a test only when the task intentionally changes the required behavior or
  when you can independently verify that the test is wrong.
- Change generated artifacts through their source and generator. Never edit them by hand.

## Inherited code

**Any file here may be changed on its merits.** Nothing is off limits because of where it came
from, and a defect in inherited code is a defect in our code.

Two obligations come with that, and they are the whole substance of the register:

- **Record it.** Every file we change outside `copilot/` is listed in
  [`copilot/docs/UPSTREAM_DELTA.md`](copilot/docs/UPSTREAM_DELTA.md), enforced by
  `copilot/tests/test_upstream_delta.py`. The register answers *"what do we own and have to
  test ourselves"*, which is the question that matters now that no one else is testing it for
  us.
- **Test it here.** Upstream's CI is not ours. A change to inherited code carries its own
  regression test in this repository, and a test that fails without the change is worth more
  than one that merely passes with it.

**Upstream is a source we read, never a base we merge.** `git fetch upstream` is safe and
useful for deciding whether to harvest a fix. Do not merge or rebase onto it. If an upstream
change is worth having, take it deliberately, with its own branch and its own tests.

## Pull request readiness

**Prepare a complete, review-ready change before opening a pull request.**

Run the smallest relevant test while developing. Before opening or updating a pull request,
run `make format`, `make pre-commit`, and all tests relevant to the change locally. For higher
assurance, run `make pre-flight`.

Some hooks need tooling that may not be installed (`cargo` on `PATH`, `ty`, `pyarrow` in the
virtualenv). **Declare what did not run rather than implying it passed.**

CI confirms a locally validated change. It is not a development loop.

## Git

- Work on a branch off `develop` and target `develop`.
- Commit, amend, push, and change remote state freely on this repository. The project owner
  has standing authorization for pull requests here, including merge and branch deletion.
- **Never push to, or open anything on, `nautechsystems/*`.** The `upstream` remote's push URL
  is disabled and `gh` is pinned with `gh repo set-default`. Both live in `.git/config` and do
  **not** survive a fresh clone, so re-establish them after one.
- Never commit credentials. `trade-copilot/` holds a real `.env` and is excluded locally only,
  which likewise does not survive a fresh clone.

## Conventions we chose

These were our preferences, not obligations to anyone, so they stay:

- No Conventional Commits syntax in commit messages or pull request titles.
- No issue or pull request number in a commit subject or pull request title; a squash merge
  appends the number, and issues are referenced from the body.
- No AI tool or model as an author, co-author or contributor, and no `Co-authored-by:` trailers
  for them.
- No branded footers such as `Generated with ...`.
- Disclosure of AI assistance is optional; if used, keep it general and vendor-neutral.
