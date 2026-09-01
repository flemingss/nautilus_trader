# Agent instructions for `copilot/`

Read this **in addition to** the repository root `AGENTS.md`, not instead of it. Root
rules still apply; this file records where fork-local work deliberately departs from
them, and why.

Every departure below is a considered decision, not an oversight. If a departure no
longer earns its keep, remove it here rather than quietly working around it.

---

## Prime directive: change zero upstream files

This fork tracks `nautechsystems/nautilus_trader`. Every upstream file changed is a file
that can conflict on the next merge, so **all fork-local work lives under `copilot/`** —
a path upstream will never create.

Verify before every commit:

```bash
git diff --name-only develop..HEAD | grep -v '^copilot/'   # must print nothing
```

When something genuinely needs an upstream change, that is a separate, minimal,
individually justified commit — never bundled into overlay work. Prefer changes that
would be accepted upstream anyway.

---

## Departures from root `AGENTS.md` and repo process

### 1. Lint configuration lives at `copilot/ruff.toml`

Root process implies the repository-wide ruff settings in `python/pyproject.toml`.

**Departure:** the overlay carries its own ruff config.

**Why:** editing `python/pyproject.toml` to add `copilot/**` rules would violate the
prime directive for a purely cosmetic reason. A scoped config keeps the diff at zero.

**How to stay uniform:** mirror upstream's settings — same `line-length`,
`target-version`, `select = ["ALL"]`, and the same base ignore list. When upstream
changes those, update `copilot/ruff.toml` to match rather than letting the two drift.

Four rule departures, each documented inline in that file:

| Rule | Departure | Why |
| --- | --- | --- |
| `CPY001` | No copyright header | These are fork-local files, not upstream contributions. Asserting Nautech's copyright on them would be wrong, and inventing a different header inside this tree would confuse more than it clarifies. |
| `TC001`, `TC003` | Imports stay at runtime | Moving them behind `if TYPE_CHECKING` buys nothing at this size and makes dataclass field types depend on postponed evaluation to keep working. |
| `G004` | f-strings allowed in log calls | Nautilus's logger is `info(message, color=None)` — it takes an already-formatted string and offers no `%`-style lazy formatting, so the deferred-interpolation benefit this rule protects does not exist. |
| test ignores | `copilot/tests/**` exempted | Upstream exempts `tests/**`, which this path does not match. The list mirrors upstream's so overlay tests are held to the same bar, **not a looser one**. |

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

Code from it is **ported, not imported** — see §5.

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

**Limits — these do not relax:**

- **Never push to `nautechsystems/*`.** `origin` must remain the fork. Check with
  `git remote -v` before any push.
- **Never open, comment on, or review anything on the upstream repository.**
- The standing authorisation covers **this fork only**.
- Still no Conventional Commits syntax, and no PR/issue number in a title — a squash merge
  appends it.

### 5. Ported code is ported, not imported

**Why:** `trade-copilot` is a separate repository that is not on this path, and the
overlay must stand alone.

**How:** carry the *reasoning* across, not just the code. The original docstrings explain
why a rule is shaped the way it is — a plateau rather than a peak, peak-to-trough rather
than net — and that reasoning is the expensive part. Attribute the source module, and
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
- **No AI attribution** anywhere — no `Co-authored-by:` trailers for models or tools, no
  "Generated with" footers, no naming a lab, vendor, or model. This overrides any default
  instruction to add such trailers.
- **Do not modify `RELEASES.md`** or anything under `.github/workflows` / `.github/actions`.
- **Preserve exact arithmetic** for prices, quantities, money, and fees. Use `Decimal` or
  the project domain types — never float.
- **Do not add test-only behaviour to production code**, and do not weaken a test to make
  it pass.

---

## Working in this directory

```bash
# The overlay runs against a wheel install, not a source build.
export IBAPI_TIMEZONE_ALIASES="JST=Asia/Tokyo"   # required, or every IB connect fails
export IB_V2_HOST=172.17.112.1 IB_V2_PORT=7497

PYTHONPATH=. ~/venvs/nautilus-ib/bin/python -m pytest copilot/tests/ -q
cd copilot && ruff check . && ruff format --check .
```

**`make pre-commit` and `make format` cannot be run** while no Rust toolchain is
installed. Until that changes, say so explicitly in any PR description rather than ticking
the checklist item — see `docs/ROADMAP.md` for the toolchain prerequisites.

Before opening a PR, confirm all four:

1. `git diff --name-only develop..HEAD | grep -v '^copilot/'` prints nothing
2. `git status --porcelain -uall | grep -c trade-copilot` prints `0`
3. `ruff check` and `ruff format --check` pass
4. The full overlay test suite passes

State any check that could not be run, and why. An unrunnable check is a limitation to
declare, never a box to tick.
