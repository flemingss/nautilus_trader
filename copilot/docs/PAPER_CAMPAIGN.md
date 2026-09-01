# The paper campaign

The record of getting this fork operational on a paper account: why it starts now, what
each stage has to prove before the next one opens, and what was actually observed.

[`playbook/OPERATIONS.md`](playbook/OPERATIONS.md) holds the general process. This file is
**this campaign** - concrete gates, and a log with dates in it. Read the playbook for how a
session is run; read this for where we are.

## The story

Two clocks run over a paper account and they measure different things.

**The system clock** asks *does the machine behave*. Does it connect, resolve the right
instruments against the right account, survive a disconnect, refuse an order it should
refuse, and come back from a restart in a safe state. Nothing about it depends on which
strategy is loaded, and it is the longer of the two: OPERATIONS sets operational readiness
at **eight weeks plus at least 30 reconciled order-lifecycle or injected-failure events,
with zero unresolved critical incidents**.

**The strategy clock** asks *does this premise behave as researched*. It needs a frozen
candidate that has already passed the research gate, and it is meaningless without one.

Almost everything currently blocking this project blocks the second clock and not the
first. The market-data subscription is gated on account equity that has not settled. The
account is cash pending a margin decision. The spread coefficient is undecided. The gap
fade is negative at the target account size and no holdout exists, so there is no premise
worth forward-testing.

**None of that touches the system clock.** It needs a paper account, delayed quotes and a
connection, all of which exist today. It is the longest-lead item in the project and the
only long-lead item nothing is gating, so it starts now and runs down while the rest is
resolved. Starting it after a candidate exists would mean waiting eight weeks *then*.

## What a paper account can and cannot prove

IBKR states its paper environment relies on more simulated technology than live and that
execution behaviour can differ. **Paper validates behaviour far more than it validates
fill quality.** A paper fill is not evidence about slippage, and no number from this
campaign is admissible against the cost model.

That is not a caveat on the campaign, it is the campaign's scope. The system clock is
about behaviour, and behaviour is exactly what paper is good for.

## The gates

Each stage opens only when the one before it has a dated row in the log. Stages 1 to 6 are
broker-integration testing and need no subscription, no settled cash, no margin and no
strategy anyone believes in. Stages 7 and 8 need a frozen candidate and are blocked.

| #   | Stage                                                | Gate: what has to be true to pass                                                                                                                                                                     |
| --- | ---------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Connect, orders disabled (**passed 2026-09-01**)     | Node connects to the paper account. Risk engine reads `HALTED` **after startup**, not merely before it. A strategy that decides to trade is denied inside the risk engine and the denial is recorded. |
| 2   | Confirm the environment (**blocked**: Read-Only API) | Instrument ids, account id, base currency, market-data type and session calendar all read back as expected, from the broker rather than from configuration. A mismatch on any one fails the stage.    |
| 3   | One controlled order                                 | A single minimum-size order submitted through the strategy path and cancelled. Broker acknowledges both. Client order id, broker order id and permanent id all captured.                              |
| 4   | Order types and TIF                                  | Every order type and time-in-force the strategies will use, each submitted and resolved. Brackets included, since the gap fade submits one.                                                           |
| 5   | A full supervised session                            | One complete session start to finish with an operator watching. Reconciliation clean at open and close.                                                                                               |
| 6   | Failure injection                                    | Stale data, disconnect, reject and a deliberate position mismatch. Each detected, each handled as documented, each alerted.                                                                           |
| 7   | Repeat sessions, frozen strategy                     | **Blocked.** Needs a candidate that passed the research gate. None exists.                                                                                                                            |
| 8   | Unattended                                           | **Blocked.** Needs stage 7, plus alerts and recovery drills passed.                                                                                                                                   |

### Two gates that are easy to wave through

**Stage 1 is not "it connected".** The claim being tested is that the halt *survives node
startup*. It has been verified against a built node - the handle resolves, the state takes,
and it reads back `HALTED` through a fresh `node.risk_engine`, so the binding shares one
engine rather than handing out copies. It has **not** been verified across a start, because
that needs a broker. If the halt does not survive startup, orders-disabled mode is a
comment and the whole campaign has been running with the safety off.

**Stage 6 is the one worth the eight weeks.** Stages 1 to 5 tell you the happy path works,
which is the part that was never in doubt. Everything this campaign is actually for lives in
stage 6.

## What the first run found

Three things, none of which were visible from reading code.

**The halt survives startup.** Stage one's whole claim, now evidence rather than
assumption: `HALTED` before the start and `HALTED` after it, against a real broker.
Orders-disabled mode is real.

**TWS had Read-Only API enabled.** The execution client failed to connect with IB **321**,
*"The API interface is currently in Read-Only mode"*. Two checks then failed together -
no account, no balances - and neither said why, because the account is missing as a
*consequence* of the execution client never connecting, and that is a checkbox in the TWS
GUI that nothing in the failure text points at. The preflight now names the cause in its
own record so the next run does not rediscover it.

Read-only is the correct setting for stage one and the wrong one from stage two onward.
**Turn it off in TWS: Global Configuration, API, Settings, uncheck Read-Only API.**

**Research instrument ids are not broker instrument ids.** The catalog names the instrument
`AAPL.XNAS` (MIC venue, via `data/catalog.equity_for`); the broker resolves
`AAPL=STK.SMART` and reports the venue as `SMART`. The first attempt failed on exactly this,
and it is not cosmetic - an activation names `symbol="AAPL", venue="XNAS"`, and that
instrument cannot be traded. **Nothing in the overlay maps between the two**, and stage
three cannot place an order until something does.

## Standing rules for every session

- **Do not open the IB web portal, Client Portal or the mobile app while a run is live.**
  It displaces the API's historical data service and produces error 162 while the socket
  stays connected, so the failure looks like anything except what it is.
- `IBAPI_TIMEZONE_ALIASES="JST=Asia/Tokyo"` in every process on this host, or connections
  fail with a generic error that hides the cause.
- Paper and live keep separate accounts, credentials, client ids and alert destinations.
  The node validates the account identifier at startup and refuses to run without it; see
  [`../live/session.py`](../live/session.py).

## The log

Evidence, dated. A stage without a row here has not passed, whatever anyone remembers.

| Date       | Stage | Result                                                                                                                                                                             | Evidence                                   |
| ---------- | ----- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------ |
| 2026-09-01 | 1     | **Pass.** Connected to paper on `172.17.112.1:7497`. Halt applied before start and **still `HALTED` after startup**. Three instruments resolved. Clean shutdown.                   | `live/out/preflight_20260901T121340Z.json` |
| 2026-09-01 | 2     | **Fail.** No account reached the cache and no balances reconciled. Cause found: **TWS had Read-Only API enabled**, so the execution client failed with IB 321 and never connected. | same record                                |

## Where the code is

| Piece                         | Location                                       | State                          |
| ----------------------------- | ---------------------------------------------- | ------------------------------ |
| Paper/live discrimination     | [`../live/session.py`](../live/session.py)     | Built, 17 tests                |
| Node builder and order switch | [`../live/node.py`](../live/node.py)           | Built, unrun against a broker  |
| Preflight check script        | [`../live/preflight.py`](../live/preflight.py) | Built and run. Stages 1 and 2. |
| Failure injection             | -                                              | Not built. Stage 6.            |
