# 2. Fork-local work lives in `copilot/`

- **Status:** Accepted
- **Date:** 2026-09-01 (records a decision taken 2026-08-31)
- **Deciders:** Project owner

## Context

This is a fork of `nautechsystems/nautilus_trader`, an actively developed project. Every
upstream file changed is a file that can conflict on a future merge, and the conflict
arrives at the worst time: when trying to adopt an upstream improvement.

The alternative to a fork - a separate package depending on the published wheel - was
rejected because the work needs the engine's internals: a `BacktestEngine`-backed replay,
the risk engine's `TradingState`, and adapter fixes.

## Decision

All fork-local code lives under a single top-level `copilot/` directory: a path upstream
will never create, so `git merge upstream/develop` cannot conflict with it.

The overlay imports from `nautilus_trader`; nothing in `nautilus_trader` imports from
`copilot`. The dependency runs one way, so the overlay can be deleted without breaking
the engine.

## Consequences

- A merge conflict is always in an upstream file we chose to change, never in overlay
  code, which makes the carrying cost countable. See `0003`.
- Overlay tests, lint scope and docs live under the same path, so the whole fork-local
  surface is one directory listing.
- Code ported from trade-copilot is **ported, not imported**: that project is a separate
  repository which is not on this path, and the overlay must stand alone.
