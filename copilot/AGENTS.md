# Agent instructions for `copilot/`

Read this **in addition to** the repository root `AGENTS.md`, not instead of it. Root
rules still apply; this file records where fork-local work deliberately departs from
them, and why.

**Read [`docs/CHARTER.md`](docs/CHARTER.md) first.** It says what this system is for, what
counts as success, and which gate a candidate is standing at. The playbook beside it says
how the work is done: preflight, research, risk and operations. Together they govern the
out-of-repo half too, so they outrank this file where the two overlap.

For *why things are this way*, read `docs/decisions/`. Those records are immutable: a
decision that stops being true is superseded by a new one, never rewritten. If this file
and an ADR disagree, the ADR is the record and this file is the bug.

For *how to draw from upstream*, read `docs/MAINTENANCE.md`. There are no syncs and no
cadence ([ADR-0010](docs/decisions/0010-the-repository-is-ours.md)): upstream is a source
we read and harvest from deliberately, never a base we merge, and never a step inside
another task.

For *what to work on*, read `docs/ROADMAP.md` - it is the central record, organised
around the kill chain, and it says which items are blocked on a decision rather than on
work. Do not start an item listed there as awaiting a decision.

Note the two roadmaps. `ROADMAP.md` at the repository root is **upstream's** and is never
edited here; `copilot/docs/ROADMAP.md` is this overlay's.

Every departure below is a considered decision, not an oversight. If a departure no
longer earns its keep, remove it here rather than quietly working around it.

---

## Prime directive: every upstream change is registered

This repository began as a copy of `nautechsystems/nautilus_trader` and is detached from
it ([ADR-0010](docs/decisions/0010-the-repository-is-ours.md)). **Fork-local work lives
under `copilot/`** by default - a path upstream will never create - so what we changed of
the inherited code stays a short, reviewable list rather than an excavation.

Upstream changes **are permitted** where they are worth it. This is a deliberate
relaxation of the earlier rule, which forbade them outright. What replaces the rule is a
register: `docs/UPSTREAM_DELTA.md` lists every file outside `copilot/` this fork changes,
why, and what it would cost to drop.

**Registering is part of making the change, not paperwork afterwards.**
`tests/test_upstream_delta.py` compares the register against the real diff - including
uncommitted work - and fails on a file with no row.

```bash
python -m copilot.tools.upstream_delta --fetch    # report + conflict risk
python -m copilot.tools.upstream_delta --check    # exit 1 on an unregistered file
```

The aim is not a minimal delta at any price. It is that every entry stays deliberate,
small, and individually justified, so a sync is a review of a short list. In practice:

- Keep an upstream change a **separate, minimal commit**, never bundled into overlay work.
- **Prefer a change upstream would accept anyway** - a fix that can be contributed back is
  a delta with an expiry date; a fork-only behaviour change is a permanent bill.
- Avoid renames and drive-by tidying in upstream files. They cost the same at sync as a
  real fix and buy nothing.
- **Off limits regardless:** `RELEASES.md`, `.github/workflows/`, `.github/actions/`, and
  the root `ROADMAP.md`. A test enforces this too.

### Upstream is read, never merged

**There is no sync, on any cadence** ([ADR-0010](docs/decisions/0010-the-repository-is-ours.md)).
Wanted upstream fixes are harvested one at a time, each on its own branch with its own
test here; the procedure is in `docs/MAINTENANCE.md`.

- **Never merge or rebase onto `upstream/develop`.**
- `git fetch upstream` is safe and changes nothing; it only refreshes the snapshot the
  delta report reads. Everything else about upstream is opt-in.
- The report's conflict line is **advisory**: it describes a merge that will not happen.
  Read it as a hint about how actively upstream is reworking something we hold opinions
  on, and nothing more.
- **Do not contribute changes upstream.** Nothing here is prepared as a contribution, and
  an upstream pull request opens a review front on someone else's schedule.

`UPSTREAM_DELTA.md` stays the inventory of what we own and therefore test ourselves; each
row says what the change is for, so judging one later is a judgement about a known change
rather than an excavation.

### The `upstream` remote re-points `gh` at upstream

Adding the remote for delta tracking has a side effect that is easy to miss and hard to
undo: **`gh` resolves its target repository from the remotes**, so with `upstream`
present, `gh pr list` starts listing upstream's pull requests and `gh pr create` tries to
open one **against `nautechsystems/nautilus_trader`**. This was caught the first time it
happened only because GitHub rejected the request for an unrelated reason.

Both guards must stay in place:

```bash
git remote set-url --push upstream DISABLED-never-push-upstream   # push is impossible
gh repo set-default flemingss/nautilus_trader                     # gh targets the fork
```

`gh repo set-default` writes to `.git/config`, so like `.git/info/exclude` it is
**local-only and does not survive a fresh clone** - re-run it after cloning. Confirm with
`gh repo set-default --view` before opening anything, and prefer an explicit
`--repo flemingss/nautilus_trader` on any `gh` command that creates or comments.

**Never push to, or open anything on, `nautechsystems/*`.**

---

## Departures from root `AGENTS.md` and repo process

### 1. Lint rules for `copilot/**` live in `python/pyproject.toml`

**This departure has been removed**, and the reason is worth keeping.

The overlay used to carry its own `copilot/ruff.toml`, on the reasoning that editing
`python/pyproject.toml` would break the zero-upstream-diff rule for a cosmetic gain. That
config **never governed these files.** The repository's pre-commit runs
`ruff --config python/pyproject.toml` over every Python file, `copilot/` included, so the
scoped config only applied when someone ran ruff by hand inside the directory. The first
`make pre-commit` reported **728 errors** on the overlay, mostly upstream's own
`tests/**` exemptions failing to match `copilot/tests/**`.

The ignores now live in the config that actually applies, as a purely additive block of
`[tool.ruff.lint.per-file-ignores]` keys, and `copilot/ruff.toml` is deleted. It is a
registered upstream delta; see `docs/UPSTREAM_DELTA.md`.

**Run ruff the way pre-commit does**, or the answer means nothing:

```bash
ruff check copilot/ --config python/pyproject.toml
ruff format --check copilot/ --config python/pyproject.toml
```

Four rule departures apply to overlay source, each with its reasoning in the config: no
copyright header (fork-local files are not upstream contributions, so asserting Nautech's
copyright on them would be wrong), runtime imports kept out of type-checking blocks, and
f-strings allowed in log calls because Nautilus's logger takes an already-formatted
string and offers no lazy formatting to defer.

`copilot/tests/**` mirrors upstream's `tests/**` exemption so overlay tests are held to
the same bar, **not a looser one**.

### 2. `trade-copilot/` is excluded via `.git/info/exclude`

**Departure:** not `.gitignore`.

**Why:** `.gitignore` is an upstream file. `.git/info/exclude` is local-only and produces
no diff, which satisfies the prime directive.

**Consequence:** the exclusion is **not shared and not committed**. A fresh clone will see
`trade-copilot/` as untracked. It is a ~376 MB nested git repository containing a real
`.env`, and it must never be committed. Re-add the exclusion after any fresh clone:

```bash
echo "trade-copilot/" >> .git/info/exclude
git status --porcelain -uall | grep -c trade-copilot   # must print 0
```

Code from it is **ported, not imported** - see §5.

### 3. Git identity is repo-local

**Departure:** `git config user.name/user.email` set in this repository only, not globally.

**Why:** the environment had no identity configured and commits could not be made. Setting
it globally would affect unrelated repositories.

### 4. Pull requests are opened directly

Root `AGENTS.md` says not to open or interact with pull requests unless explicitly asked,
and that a maintainer should agree on problem and approach first.

**Departure:** on this fork, the owner has standing authorisation to open PRs directly.

**Why:** those rules govern contributions to the upstream project, where a maintainer's
time is the scarce resource. This fork is the owner's own repository with CI disabled, and
they are both author and reviewer.

**Limits - these do not relax:**

- **Never push to `nautechsystems/*`.** `origin` must remain the fork. Check with
  `git remote -v` before any push.
- **Never open, comment on, or review anything on the upstream repository.**
- The standing authorisation covers **this fork only**.
- Still no Conventional Commits syntax, and no PR/issue number in a title - a squash merge
  appends it.

### 5. Ported code is ported, not imported

**Why:** `trade-copilot` is a separate repository that is not on this path, and the
overlay must stand alone.

**How:** carry the *reasoning* across, not just the code. The original docstrings explain
why a rule is shaped the way it is - a plateau rather than a peak, peak-to-trough rather
than net - and that reasoning is the expensive part. Attribute the source module, and
record any adaptation as a "Port note" saying what changed and why.

**Where a port must differ from its original, say so in the code**, not only in a commit
message. `walkforward.py` requiring `warmup_bars` instead of deriving it is the worked
example.

---

## What we deliberately keep from root `AGENTS.md`

Not departures. Restated because they are easy to lose sight of in a fork:

- **This code can execute live trades with real capital.** Hold every change to that
  standard regardless of how fork-local it is.
- **No Conventional Commits** in commit subjects or PR titles.
- **No AI attribution** anywhere - no `Co-authored-by:` trailers for models or tools, no
  "Generated with" footers, no naming a lab, vendor, or model. This overrides any default
  instruction to add such trailers.
- **Do not modify `RELEASES.md`** or anything under `.github/workflows` / `.github/actions`.
- **Preserve exact arithmetic** for prices, quantities, money, and fees. Use `Decimal` or
  the project domain types - never float.
- **Do not add test-only behaviour to production code**, and do not weaken a test to make
  it pass.

---

## Working in this directory

```bash
export IBAPI_TIMEZONE_ALIASES="JST=Asia/Tokyo"   # required, or every IB connect fails
export IB_V2_HOST=...                            # derive it: ip route | awk '/^default/{print $3}'
export IB_V2_PORT=7497

PYTHONPATH=. .venv/bin/python -m pytest copilot/tests/ -q
ruff check copilot/ --config python/pyproject.toml
ruff format --check copilot/ --config python/pyproject.toml
UV_PROJECT_ENVIRONMENT="$PWD/.venv" prek run --all-files   # what `make pre-commit` runs
```

**Bare `prek` is not `make pre-commit` without that export.** The Makefile exports
`UV_PROJECT_ENVIRONMENT` at the repository root, and the `ty` and docformatter hooks run
`uv run --project python`, which without it resolves to a bare `python/.venv` and fails
with `Failed to spawn: ty` - a missing-tool error for a tool that is installed. Cost a
real diagnosis on 2026-09-02.

**`make pre-commit` now runs.** It needs only `prek`, not the whole `make install-tools`
chain, which compiles ten cargo tools this fork does not use:

```bash
uv tool install prek==$(bash scripts/tool-version.sh prek)
```

`make format` additionally needs `cargo +nightly fmt`:
`rustup toolchain install nightly --profile minimal --component rustfmt`.

Its hooks enforce conventions the overlay had been quietly breaking: **no em dashes**
(use an ASCII hyphen), Python exception variables must be named `e`, and markdown tables
are padded to the widest cell. Run it before opening a PR, not after.

Two Python environments exist and they are not interchangeable. `~/venvs/nautilus-ib`
is the wheel install; the repo's own `.venv` is the editable source build and is the
only one with `ParquetDataCatalog`, so anything touching `copilot/data/` runs there.

Secrets come from the environment, never from a file in the tree. `MARKETSTACK_API_KEY`
is read from `os.environ` at the CLI boundary and passed down as an argument, so no
module below it can acquire a way to write one to disk.

Before opening a PR, confirm all five:

1. `python -m copilot.tools.upstream_delta --check` exits 0 - every upstream file
   changed outside `copilot/` has a row in the register
2. `git status --porcelain -uall | grep -c trade-copilot` prints `0`
3. `ruff check` and `ruff format --check` pass **with `--config python/pyproject.toml`**
4. `prek run --all-files` reports nothing on `copilot/` paths
5. The full overlay test suite passes

State any check that could not be run, and why. An unrunnable check is a limitation to
declare, never a box to tick.
