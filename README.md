# nautilus_trader - a privately operated trading system

This repository is a **personally owned copy of
[NautilusTrader](https://github.com/nautechsystems/nautilus_trader)**, detached from the
fork network on 2026-09-01
([ADR-0010](copilot/docs/decisions/0010-the-repository-is-ours.md)) and operated as the
engine of a private algorithmic trading system. It is not the NautilusTrader product
page, it publishes no packages, and it takes no contributions
([CONTRIBUTING.md](CONTRIBUTING.md)). Anyone wanting NautilusTrader itself should go to
the upstream project; everything here exists for one operator's trading.

## Where to start

| Document                                                      | Job                                                                       |
| ------------------------------------------------------------- | ------------------------------------------------------------------------- |
| [`copilot/docs/CHARTER.md`](copilot/docs/CHARTER.md)          | **The entry point.** The process the project is run by; outranks all else |
| [`copilot/docs/ROADMAP.md`](copilot/docs/ROADMAP.md)          | What is built, what is open, and what unblocks each open item             |
| [`AGENTS.md`](AGENTS.md)                                      | Working rules: inherited code, the delta register, git conventions        |
| [`copilot/docs/MAINTENANCE.md`](copilot/docs/MAINTENANCE.md)  | The codebase's shape, standing up a new machine, drawing from upstream    |
| [`copilot/docs/playbook/`](copilot/docs/playbook/README.md)   | How research, risk and operations are actually done                       |
| [`copilot/docs/decisions/`](copilot/docs/decisions/README.md) | Why things are the way they are; immutable once accepted                  |

## What lives where

- **`copilot/`** - everything this project adds: data bridge, validation gate,
  strategies and their verdicts, risk protections, live/paper session plumbing, spread
  calibration, and the governing documents above. Start here; nothing in it came from
  upstream.
- **Everything else** - the inherited NautilusTrader engine: a Rust core (43 crates)
  under a thin Python facade, built by maturin into the single
  `nautilus_trader._libnautilus` extension. Inherited code is changed on its merits,
  and every such change is registered in
  [`copilot/docs/UPSTREAM_DELTA.md`](copilot/docs/UPSTREAM_DELTA.md) and tested here,
  because no one upstream is testing this copy for us.

The inherited [developer guides](docs/developer_guide/index.md) and
[coding standards](docs/developer_guide/coding_standards.md) still govern work on engine
code - kept because they are good, not because conformance is owed to anyone.

## Relationship to upstream

Upstream is **a source we read, never a base we merge**. `git fetch upstream` informs
harvest decisions; syncs are deliberate, reviewed quarterly, and nothing is pushed to or
opened on `nautechsystems/*`. The full policy is in
[`copilot/docs/MAINTENANCE.md`](copilot/docs/MAINTENANCE.md), and the standing bill for
the changes we carry is the register above.

This project would not exist without NautilusTrader, which remains the best open-source
trading engine we know of. Its [LGPL-3.0 license](LICENSE) continues to apply to this
copy; see [SECURITY.md](SECURITY.md) for where vulnerability handling diverges.
