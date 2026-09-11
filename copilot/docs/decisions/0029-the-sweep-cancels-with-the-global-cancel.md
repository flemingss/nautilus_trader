# 29. The sweep cancels with IB's global cancel, because a client can cancel only its own orders

- **Status:** Accepted
- **Date:** 2026-09-11
- **Deciders:** Project owner (audit corrections approved 2026-09-11); mechanics proposed in-session

## Context

The monitoring-end sweep (`copilot.live.cancel_working`) runs on its own client ids, 821 and
822, so that it can run after the process that placed an order has gone. It adopts every open
order on the account through reconciliation and sends a cancel for each. The kill switch's
cancel limb is the same sweep ([ADR-0027](0027-the-kill-switch-is-a-host-latch.md)).

**Those cancels never worked on an order another client placed.** Measured 2026-09-11 against
paper TWS, the first run after the audit found the sweep had not been starting at all
([`AUDIT_2026-09-11.md`](../AUDIT_2026-09-11.md), F1): one share of AAPL was stranded at half
the market from client 832, and two sweeps from 822 sent cancels at 14:33 and 14:41 UTC. IB
returned no error and no acknowledgement, and four broker censuses to 14:54 still read the order
open. One cancel from client 832 at 14:55 cleared it by 14:58. IB's API documentation says the
same thing plainly: `cancelOrder` cancels only orders the calling client placed, and
`reqGlobalCancel` is the one request that cancels every open order, however it was placed -
another API client, or TWS itself.

That also settles the question [`PAPER_CAMPAIGN.md`](../PAPER_CAMPAIGN.md) left open on
2026-09-10, whether the acknowledgement that landed then came from the foreign cancels or the
native one: it was the native one.

Three shapes were considered.

- **Cancel from the placing client id.** Recoverable in principle, since every module here uses
  fixed ids, but the sweep would have to connect once per id in the table, would miss an order
  placed on an id not in it or from the TWS window, and would collide with IB 326 if the placing
  process were still alive.
- **Make every order-placing process cancel its own orders before it exits.** Necessary and not
  sufficient: the case the sweep exists for is the process that died without doing so.
- **IB's global cancel.** One request, every open order on the account.

## Decision

**A session that exists only to cancel sends IB's global cancel.** The IB execution client gains
`global_cancel_on_cancel_all`, off by default; with it on, each cancel-all command also sends
`reqGlobalCancel` before reading the cache, so an order reconciliation never adopted is swept
too. `copilot/live/node.py` turns it on exactly when the session is `cancels_only`, which today
is the sweep and nothing else.

- **Account-wide on purpose.** The sweep's job is *nothing left working*, and the account is
  this system's alone. An order placed by hand in TWS on the paper account is swept as well.
- **The census stays the verdict.** The global cancel returns no per-order acknowledgement
  either, so `BROKER CLEAR` still means a fresh connection read nothing open.
- **The global cancel replaces the per-order cancels** for that client. Sent alongside them, the
  per-order path then failed to resolve an order the global cancel had already removed and
  logged it at ERROR on every sweep that found one - measured on the verification run. The
  sweep places no orders of its own, so nothing is lost.
- **An inherited-code change**, registered in [`UPSTREAM_DELTA.md`](../UPSTREAM_DELTA.md): the
  option in the Rust config and its Python binding, the send in the execution core, and the
  generated stub.

## Consequences

- **The sweep and the kill switch cancel what they claim to**, for the first time: before
  2026-09-10 the sweep started and could reach only its own orders, and from 2026-09-10 to
  2026-09-11 it did not start.
- **On a live account shared with manual trading, this would cancel the human's orders too.**
  The charter's live account is this system's alone; a second use of the account is a reason to
  revisit, not a detail.
- **Nine cancel-all commands send nine global cancels.** IB returned no error for the repeats
  on the verification run, 2026-09-11 11:07 ET: an order placed by client 832 was gone at the
  first census from 822, `BROKER CLEAR`, `live/out/sweep_20260911T150722Z.json`.
- **Revisit trigger:** the account carries orders this system does not own, or IB changes the
  global cancel's scope.
