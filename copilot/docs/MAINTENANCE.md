# Maintenance plan

How this fork stays current with upstream without being dragged by it, and who owns what
in the runtime. The decisions behind this are
[ADR-0003](decisions/0003-registered-upstream-deltas.md),
[ADR-0004](decisions/0004-quarterly-upstream-sync.md) and
[ADR-0007](decisions/0007-self-sourced-images.md); this document is the procedure.

## The shape of the problem

We want upstream's improvements. We do not want upstream's schedule.

`nautechsystems/nautilus_trader` is actively developed - 15 commits landed against our
merge base within a day of forking, one of which rewrote 224 lines of a file we change.
Tracking `develop` would mean debugging our own work and someone else's refactor at once,
on two moving bases. Never syncing is the other failure: the delta ages, and the cost of
adopting an upstream improvement grows with the distance.

So: **draw from upstream deliberately, on our cadence, as a scheduled piece of work.**

## Cadence

**Superseded 2026-09-01 by [ADR-0010](decisions/0010-the-repository-is-ours.md).** There is
no sync cadence, because there are no syncs. The repository has been detached from the fork
network; upstream is a source we read and harvest from, never a base we merge.

What remains is **harvesting on demand**: when there is a specific upstream fix worth having,
take it deliberately, on its own branch, with its own tests here. `git fetch upstream` stays
useful for diffing to decide whether something is worth taking.

The quarterly review this section used to schedule is dropped. Its purpose was to keep the
delta from ageing into an unmergeable state, and that no longer applies to a repository that
will never merge.

## When the delta stops being maintainable

**Reached, and acted on, the day it was written.** These triggers were set on 2026-09-01 to
fire later; the paper campaign's findings answered them the same afternoon, and
[ADR-0010](decisions/0010-the-repository-is-ours.md) is the outcome they pointed at. Kept as
the reasoning behind that decision rather than as a live tripwire.

The delta is **10 upstream files** and the paper campaign has already named an eleventh
worth making - the reconciliation gap that leaves an external `SUBMITTED` order
uncancellable, which sits in `crates/execution` rather than in an adapter and so has a wider
blast radius than anything we hold today.

The reason the count grows is structural rather than accidental. NautilusTrader's integration
list is almost entirely crypto exchanges: one symbol namespace, no entitlements, no routing
layer, 24/7 sessions, one account model. Interactive Brokers is close to the only traditional
multi-asset brokerage in it, so the abstractions bend hardest exactly where we work - venue
identity, routing, entitlements, session state. **We should expect to keep finding things**,
and expect the fixes to be ours to make.

That is sustainable at ten files. It is not sustainable indefinitely. Two triggers, either of
which means the lifecycle model itself gets reconsidered rather than the next patch simply
being written:

- **The delta exceeds roughly twenty files**, or reaches into a third crate beyond the IB
  adapter and the risk engine.
- **A quarterly review costs more than a working week**, or concludes "too disruptive" twice
  running - at which point we are effectively maintaining a hard fork while paying the
  overhead of pretending otherwise.

The options at that point are the ones worth naming in advance: upstream the changes and
carry only what is rejected; pin to a release and stop tracking `develop` at all; or accept
the hard fork explicitly and drop the sync machinery. **Each is a defensible choice; drifting
into one is not.**

## Harvest procedure

Replaces the quarterly review and sync procedures this file used to carry, both of which
described merges that will not happen ([ADR-0010](decisions/0010-the-repository-is-ours.md)).

Taking a specific upstream fix:

1. `git fetch upstream` and read the change. **Read it, do not merge it** - a cherry-pick that
   applies cleanly is still someone else's reasoning arriving unreviewed.
2. Decide whether we want the behaviour, not whether the diff applies. Upstream's answer was
   shaped by its own priorities, and ours differ where they differ.
3. Take it on its own branch, by cherry-pick when the diff is clean or by reimplementation
   when it is not.
4. **Write a test here that fails without it.** Upstream's suite does not run for us, so an
   untested harvest is a change nobody checked.
5. Register the file in [`UPSTREAM_DELTA.md`](UPSTREAM_DELTA.md) if it is not already there,
   and update the row's reasoning if the change alters it.
6. Full suite green before merge, same as any other work.

The one thing worth watching upstream for is a **fix to something we already changed** - two
answers to the same problem, and a reason to check whether theirs is better than ours.

## The shape of the codebase, and the build

Recorded so a session can start from this instead of re-deriving it. Measured 2026-09-02.

**This is one Rust project with a thin Python skin, not a two-language codebase.** The split:

|                              |                                       |
| ---------------------------- | ------------------------------------- |
| Rust                         | ~1.82M lines across 43 crates         |
| Python (the shipped package) | ~5.8k lines across 49 files           |
| Generated `.pyi` stubs       | 41 files, built from the compiled lib |

Most of the 49 Python files are re-export facades (`from nautilus_trader._libnautilus.live
import *` plus a module-name fixup); the only substantial hand-written Python is the
tearsheet and the test-data providers. Everything that trades - order model, matching,
cache, risk, execution, live node, and every venue adapter - is Rust. Version 1.x was
Cython-heavy; 2.0 removed Cython entirely, so there is no third layer.

**The build is one artifact.** `maturin` compiles `crates/pyo3` (which depends on 38 of the
other crates) into a single extension module, `nautilus_trader._libnautilus`, placed inside
the Python package. `make build-debug` runs: sync deps -> generate stubs -> `maturin
develop`. Cap'n Proto is a native prerequisite, and a patched `pyo3-stub-gen` is vendored
under `patches/`.

Consequences that repeatedly matter here:

- **The `.pyi` stubs are generated by introspecting the compiled library**, then shipped
  with it. A Rust signature change is compile -> regenerate -> recompile, and the stubs are
  generated artifacts: change the Rust, never the file. This is why a one-method binding
  change touches exactly the Rust source and a stub.
- **Engine and adapter defects are Rust fixes with their own Rust tests.** There is no
  Python layer to patch, which is why `UPSTREAM_DELTA.md` tracks Rust paths almost
  exclusively. Run them through **cargo-nextest** (`make cargo-test*`, or
  `cargo nextest run -p <crate>`), never bare `cargo test`: nextest gives each test its
  own process, and at least one inherited test (the msgbus republish log capture)
  requires that isolation because `log::set_logger` is process-global.
- **The feature matrix is where build complexity lives**, not the language boundary. The
  wheel builds with `extension-module, arrow, betfair, high-precision, mimalloc, redis,
  postgres, defi, hypersync, tracing-bridge`, and each of ~20 adapters sits behind its own
  flag. `high-precision` changes the numeric representation - an ABI decision, not a knob.
- **Rust tests that exercise the bindings embed a Python interpreter.** Running them needs
  `PYO3_PYTHON` pointed at the repo venv's interpreter and that interpreter's `LIBDIR` on
  `LD_LIBRARY_PATH`, or the link fails hunting for the wrong `libpython`.
- **`target/` is ~26 GB and a clean build is long** - 43 crates over DataFusion, Arrow and
  rustls, not a Python package.

Toolchain versions and install steps live in `ROADMAP.md` under "Rust toolchain
prerequisites"; the inherited developer guides under `docs/developer_guide/` remain accurate
for environment setup.

## What we own in the runtime

Accepting upstream deltas has a consequence beyond merges, and it is easy to miss:

```
published wheel  ->  LiveNode.risk_engine: False
source build     ->  LiveNode.risk_engine: True
```

**There is no path that consumes a published `nautilus_trader` wheel.** An image built on
one would run, connect, trade, and silently fall back to risk breakers that cannot stop the
next order.

So we source our own images ([ADR-0007](decisions/0007-self-sourced-images.md)), and with
that comes ownership of:

| Concern                 | Owner       | Note                                              |
| ----------------------- | ----------- | ------------------------------------------------- |
| Runtime image           | Us          | Built from our source, versioned by commit        |
| Base image currency     | Us          | Not upstream's cadence any more                   |
| CVE response            | Us          | Including transitive Rust and Python dependencies |
| `ib-gateway` image      | Third party | Pinned **by digest**, never by tag                |
| Rust toolchain in build | Us          | Pinned; a silent bump changes the artifact        |

This is the strongest argument for keeping the delta small, and the reason "retire a delta"
sits alongside "sync" as a first-class review outcome. Retiring the Rust deltas restores
the published-wheel path and hands the supply chain back.

## Standing up a new machine

Written 2026-09-02 against the question "can this operate from a laptop tomorrow". The
repository is self-contained; what does not travel is machine state, and all of it is
listed here. `trade-copilot` is **not** required: the validation types were vendored
precisely so the overlay never imports it, and the only thing ever drawn from its
directory was an API key.

1. **Clone and re-arm the git guards.** A fresh clone loses everything in `.git/config`:

   ```bash
   git clone https://github.com/flemingss/nautilus_trader.git
   git remote add upstream https://github.com/nautechsystems/nautilus_trader.git
   git remote set-url --push upstream DISABLED-never-push-upstream
   gh repo set-default flemingss/nautilus_trader
   ```

   Not every `gh` subcommand honours the default - pass `-R flemingss/nautilus_trader`
   explicitly anyway.

2. **Toolchain.** Follow "Rust toolchain prerequisites" in `ROADMAP.md`: the apt packages
   (the one sudo step), rustup (`rust-toolchain.toml` pins 1.98.0 automatically), uv,
   Cap'n Proto via `scripts/install-capnp.sh`, then `make install-tools` and
   `make build-debug`. Budget real time for the first build - 43 crates, ~26 GB of
   `target/` - and remember the published wheel is forbidden by decision
   ([ADR-0007](decisions/0007-self-sourced-images.md)): it lacks this fork's engine
   fixes, and startup asserts on one of them.

3. **Data.** The catalog lives *outside* the repository at `~/.nautilus_copilot/catalog`
   (override: `COPILOT_CATALOG_PATH`). It is ~6 MB - copy it, or rebuild it with
   `python -m copilot.data.backfill`, which needs `MARKETSTACK_API_KEY` in the
   environment. That key is the only secret the overlay uses, and it lives in a password
   manager, never in either repository.

4. **Broker environment, per shell.** Three variables:

   ```bash
   export IBAPI_TIMEZONE_ALIASES=JST=Asia/Tokyo   # required while TWS reports JST
   export IB_V2_HOST=...   # where TWS listens; scripts default to this WSL's host IP
   export IB_V2_PORT=7497  # TWS paper
   ```

   The scripts' built-in host default (`172.17.112.1`) is **this machine's** WSL gateway
   and means nothing elsewhere - code and TWS on the same OS is plain `127.0.0.1`. The
   timezone alias is not optional: without it every connect fails as a generic
   "Failed to connect to IB Gateway/TWS", and this session lost ten minutes to exactly
   that in a fresh shell.

5. **TWS settings, which live in TWS and not here.** Enable ActiveX/socket API; add the
   connecting machine's IP as a Trusted IP (under WSL that is the WSL address, not
   localhost); Read-Only API **off** for anything past paper stage one; and know the
   precautionary size settings exist - a large API order can sit untransmitted in the
   GUI, invisible to the API. One login session per set of credentials: a portal login
   kills the API session with error 162.

6. **Verify before trusting.** `PYTHONPATH=. pytest copilot/tests/ -q` (fast, no broker),
   then `python -m copilot.live.preflight --account DUT067974` inside a session. Stage
   one passing proves the whole path: build, guards, environment, broker.

**A temporary macOS machine needs neither WSL nor Docker.** Surveyed 2026-09-02
against the question "can a Mac carry this for a trip". WSL is an accident of the
current host, not a requirement: the engine must compile and run on Linux, macOS and
Windows (upstream policy, kept), macOS ARM64 is a supported build target, and
`docs/developer_guide/environment_setup.md` carries the macOS quick-setup
(`xcode-select --install`, then the same rustup/uv/capnp/`make build-debug` path -
the `LD_LIBRARY_PATH` step is Linux-only and is skipped on macOS). Docker is not part
of development at all: `.docker/` is deployment image packaging under
[ADR-0007](decisions/0007-self-sourced-images.md), and **no dev container definition
exists in this repository** - a devcontainer + Docker Desktop route is viable in
principle (Linux ARM64 is also a supported target) but would have to be authored first
and buys parity nothing requires, at the cost of slower I/O for a ~26 GB `target/`
inside the VM and `host.docker.internal` networking to reach TWS. Prefer the native
build. The toolchain itself is macOS-aware end to end: `rust-toolchain.toml` auto-installs
the pinned 1.98.0 for the Mac target on first use, `.cargo/config.toml` carries dedicated
`aarch64-apple-darwin` link flags (lld is Linux-only and not wanted), and
`scripts/install-capnp.sh` has a full Darwin branch (Homebrew first, source fallback,
version-checked against the pin). For a *temporary* machine, skip the full
`make install-tools` (it compiles a dozen cargo tools and is the slow step): the daily
loop needs only `cargo install cargo-nextest --locked` and
`cargo binstall prek --no-confirm --locked` at the pinned versions, with the rest
(fuzz, codspeed, llvm-cov, flamegraph, lychee, vet) mattering only for
`make pre-flight`-grade assurance. Apple's bundled GNU make 3.81 should handle this
Makefile (no 4.x-only features are used); `brew install make` is cheap insurance if a
target misbehaves. The whole footprint stays in the user's home:
`CAPNP_PREFIX="$HOME/.local"` now works on macOS too (a requested prefix also bypasses
Homebrew and sudo), rustup takes `--no-modify-path`, and the only system-level piece is
the Xcode Command Line Tools - Apple's compiler and SDK, often already present, and
removable by deleting `/Library/Developer/CommandLineTools`. Two things get *simpler* on a Mac with TWS on the same OS:
`IB_V2_HOST` is plain `127.0.0.1`, and the WSL trusted-IP arrangement disappears. Everything else on this
checklist applies unchanged - and research work (the gate, the cost model, the
entry-timing experiment) needs only steps 1-3 plus `pytest`: no TWS, no broker, no
Marketstack key if the ~6 MB catalog is copied rather than rebuilt.

The agent-side note that belongs with this: Claude's auto-memory is machine-local and
does not travel. The repository documents are the durable copy - which is why this file
and `ROADMAP.md` carry the warm-start records rather than leaving them in memory.

## Inherited governance surfaces

Surveyed 2026-09-02, after ownership. These files came with the copy and describe how to
work *with upstream*, which no one here does. Each is either aligned, kept deliberately, or
queued - the danger of leaving them untouched is that the repository's own front matter
answers questions about the wrong project.

| Surface                                         | State 2026-09-02 | Call                                                                                                                                                                                                                                       |
| ----------------------------------------------- | ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `ROADMAP.md` (root)                             | **Aligned**      | Replaced with a pointer to `copilot/docs/ROADMAP.md`; upstream's original is linked for harvest decisions.                                                                                                                                 |
| `CONTRIBUTING.md`                               | **Aligned**      | Replaced: this repository takes no contributions; points at the charter, `AGENTS.md` and the inherited coding standards.                                                                                                                   |
| `CLA.md`                                        | **Removed**      | Removed 2026-09-02 with the rest of the queue; each carries a register row.                                                                                                                                                                |
| `CODE_OF_CONDUCT.md`                            | **Removed**      | Removed 2026-09-02.                                                                                                                                                                                                                        |
| `SECURITY.md`                                   | **Aligned**      | Replaced 2026-09-02: no external reports; links upstream's original, which `docs/developer_guide/security.md` still references relatively.                                                                                                 |
| `AI_POLICY.md`                                  | **Removed**      | Removed 2026-09-02; `AGENTS.md` is the one policy.                                                                                                                                                                                         |
| `.github/CODEOWNERS`                            | **Removed**      | Removed 2026-09-02; `.github/OVERVIEW.md` records why.                                                                                                                                                                                     |
| `.github/ISSUE_TEMPLATE`, PR template           | **Removed**      | Removed 2026-09-02.                                                                                                                                                                                                                        |
| `README.md`                                     | Rewritten        | Was upstream's front page - badges, community links, install story. Now states what this copy is, points at the charter and roadmap, and credits upstream. Done 2026-09-02.                                                                |
| `TRADEMARK.md`                                  | **Keep**         | NautilusTrader is Nautech Systems' registered trademark. This file is the reminder that the *name* is not ours even though the copy is. Private use is fine; anything public-facing built from this repository must not trade on the mark. |
| `RELEASES.md`, `MIGRATION_V2.md`, `ADAPTERS.md` | **Keep, frozen** | Accurate history and reference for the code we inherited.                                                                                                                                                                                  |
| `docs/`, `BENCHMARKING.md`                      | **Keep**         | Engine documentation and developer guides; still correct and still used.                                                                                                                                                                   |
| `LICENSE`                                       | **Keep**         | LGPL-3.0 obliges us regardless of ownership.                                                                                                                                                                                               |

The queue landed 2026-09-02 as one PR. The register now understands deletions - a removed
file stays in the diff against the merge base forever, so its row stays too, marked
"Removed", and the path test enforces both directions: a registered file must exist and a
removed one must not. What remains is the README rewrite, whose stale references to
CODEOWNERS and the old security policy get fixed there.

## CI, and why it has never run

Diagnosed 2026-09-01, from the run logs; **Actions is disabled again as of 2026-09-02**
pending a deliberate grooming pass. Recorded so that pass starts warm:

- Every run to date (four) failed at `Checkout repository` with git exit 128. The runner
  says why: `egress-policy is set to block (default) and allowed-endpoints is empty. No
  outbound traffic will be allowed for job steps.`
- The workflows wrap every job in `step-security/harden-runner` and read the egress
  allowlist from **repository variables** - `COMMON_ALLOWED_ENDPOINTS`,
  `CI_ALLOWED_ENDPOINTS`, with `STEP_SECURITY_EGRESS_POLICY` as the mode override. Those
  variables live in upstream's repository settings and did not travel with the copy;
  `gh variable list -R flemingss/nautilus_trader` returns nothing. So the policy falls back
  to `block` with an empty allowlist, and even github.com is unreachable.
- The cheap restore is one variable, `STEP_SECURITY_EGRESS_POLICY=audit` - the mode
  upstream itself uses for untrusted PRs; audit runs then reveal the real endpoint set if a
  proper allowlist is wanted later. Deliberately **not** applied: it loosens a
  supply-chain control, and the workflows deserve a grooming pass of their own first -
  they carry upstream's wheel-publication, release and docs-deploy machinery, none of
  which applies here.
- One operational note: `gh` subcommands do not all honour `gh repo set-default` - a bare
  `gh variable list` resolved to `nautechsystems/*` and drew a 403. Pass
  `-R flemingss/nautilus_trader` explicitly.

## Invariants

These hold between reviews, not only during them.

- `python -m copilot.tools.upstream_delta --check` exits 0. A test enforces it, so an
  unregistered upstream change cannot survive a test run.
- `origin` is the fork. The `upstream` remote is fetch-only with its push URL disabled,
  and `gh` is pinned to the fork - **`gh` resolves its target repository from the remotes**,
  so merely adding `upstream` re-points `gh pr create` at the upstream project. Both guards
  live in `.git/config` and do **not** survive a fresh clone.
- Never push to, or open anything on, `nautechsystems/*`.
- Startup asserts `LiveNode.risk_engine` exists. A wheel-built runtime fails loudly at boot
  rather than quietly at the first breach.
