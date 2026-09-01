# 7. We build and source our own images

- **Status:** Accepted
- **Date:** 2026-09-01
- **Deciders:** Project owner

## Context

The obvious runtime is the published `nautilus_trader` wheel in a slim image. That is no
longer available to us, and the reason is worth stating precisely rather than discovering
at deploy time:

```
published wheel  ->  LiveNode.risk_engine: False
source build     ->  LiveNode.risk_engine: True
```

Accepting upstream deltas (`0003`) means the fork carries Rust changes. The `PyRiskEngine`
binding that makes the risk breakers preventive is one of them, and it exists only in a
build from our source. Any image built on the published wheel would run, connect, trade,
and silently fall back to breakers that cannot stop the next order.

## Decision

**This fork builds its own runtime image from its own source. There is no path that
consumes a published `nautilus_trader` wheel.**

- Two-stage build. Stage one compiles the wheel from this repository: heavy, cacheable,
  and rebuilt only when Rust changes. Stage two is a slim runtime that installs that wheel
  plus the overlay. The large `target/` directory never ships.
- The image is versioned by the commit it was built from. A running container must be
  traceable to a source revision, because the delta means "nautilus 2.0.0rc4" no longer
  identifies what is running.
- Third-party images are pinned by digest, not by tag. `ib-gateway:stable` moving
  underneath an unattended trading system is not a risk worth carrying for the
  convenience.
- **Startup asserts the binding is present.** A missing `LiveNode.risk_engine` must fail
  loudly at boot rather than degrade quietly at the first breach.

## Consequences

- Deployment requires a Rust toolchain in the build environment, and image builds are
  slow. This is a direct, permanent cost of `0003` and belongs in its cost-to-drop column.
- We own the supply chain for our runtime: patch cadence, base image currency and CVE
  response are ours, not upstream's. This is the main argument for keeping the delta small
  and for the quarterly review in `0004`.
- Retiring the Rust deltas - by upstreaming them - would restore the published-wheel path.
  That is a concrete benefit to weigh at each sync review, beyond merge convenience.
