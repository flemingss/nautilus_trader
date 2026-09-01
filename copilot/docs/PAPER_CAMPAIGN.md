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

| #   | Stage                                             | Gate: what has to be true to pass                                                                                                                                                                     |
| --- | ------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Connect, orders disabled (**passed 2026-09-01**)  | Node connects to the paper account. Risk engine reads `HALTED` **after startup**, not merely before it. A strategy that decides to trade is denied inside the risk engine and the denial is recorded. |
| 2   | Confirm the environment (**passed 2026-09-01**)   | Instrument ids, account id, base currency, market-data type and session calendar all read back as expected, from the broker rather than from configuration. A mismatch on any one fails the stage.    |
| 3   | One controlled order (**passed 2026-09-01**)      | A single minimum-size order submitted through the strategy path and cancelled. Broker acknowledges both. Client order id, broker order id and permanent id all captured.                              |
| 4   | Order types and TIF (**passed 2026-09-01**)       | Every order type and time-in-force the strategies will use, each submitted and resolved. Brackets included, since the gap fade submits one.                                                           |
| 5   | A full supervised session (**passed 2026-09-01**) | One complete session start to finish with an operator watching. Reconciliation clean at open and close.                                                                                               |
| 6   | Failure injection                                 | Stale data, disconnect, reject and a deliberate position mismatch. Each detected, each handled as documented, each alerted.                                                                           |
| 7   | Repeat sessions, frozen strategy                  | **Blocked.** Needs a candidate that passed the research gate. None exists.                                                                                                                            |
| 8   | Unattended                                        | **Blocked.** Needs stage 7, plus alerts and recovery drills passed.                                                                                                                                   |

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

Five things, none of which were visible from reading code.

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

**The account is not on the instrument's venue.** Instruments resolve on `SMART`; the
execution client registers its account under its own client name, so the id reads
`IB-DUT067974`. A venue-keyed lookup that searches only the instrument venues finds nothing
and reports a missing account that was in the cache the whole time - which is exactly what
my second attempt did. Searching both is now pinned in `preflight.py`.

**The login name and the account id are the same string here.** `DUT067974` is both. That
was an open question worth settling rather than assuming, since IB paper logins and account
ids are not required to match.

**Research instrument ids are not broker instrument ids.** The catalog names the instrument
`AAPL.XNAS` (MIC venue, via `data/catalog.equity_for`); the broker resolves
`AAPL=STK.SMART` and reports the venue as `SMART`. The first attempt failed on exactly this,
and it is not cosmetic - an activation names `symbol="AAPL", venue="XNAS"`, and that
instrument cannot be traded. **Nothing in the overlay maps between the two**, and stage
three cannot place an order until something does.

## What paper cannot reproduce

Recorded from the stage-two evidence, because these are the ways a green paper run will
still mislead.

**The paper account is `MARGIN`; the live account is cash.** The broker reports
`account_type=MARGIN` on `DUT067974`. So paper will happily accept a short sale and will
size against buying power, and **neither is true of the live cash account**, where Reg T
puts short sales in a margin account and settled cash caps concurrent positions. A strategy
that passes every paper stage can still be untradeable live for reasons paper never tested.
Do not read a paper pass as evidence about the cash constraints.

**The paper balance is USD 1,000,000.** Three orders of magnitude above the target account,
and cost-at-size is the constraint that decides whether anything here is worth trading
([ADR-0009](decisions/0009-cost-is-modelled-at-the-target-account-size.md)). Sizing must
come from the configured risk budget, never from reported equity, or every paper trade will
be sized in a regime the real account will never see.

**Paper fills are not evidence about fill quality**, as IBKR states and OPERATIONS repeats.
Nothing measured here is admissible against the cost model.

## What stage three found

Stage three took three attempts. None of the failures were the broker's.

**`Strategy.cancel_order` takes a `ClientOrderId`, not an `Order`.**
`ExecutionAlgorithm.cancel_order` takes the order, which is where the wrong habit came
from. Passing an order to the strategy method did nothing at all - no cancel, no exception,
no log line - and left a working GTC order at the broker after the node stopped. The only
thing distinguishing that run from a clean one was a missing event.

**The execution client needs explicit routing.** Without a `RoutingConfig` every order was
denied with `NO_EXECUTION_CLIENT: client_id=NONE, venue=SMART`. Orders route by the
instrument's venue, and the execution client does not register under one - the same
venue split that hid the account behind `IB` while instruments resolved on `SMART`.

The routing lists its destinations rather than setting `default=True`. A default would
route *any* venue to IB, including a research-form id like `AAPL.XNAS` that nothing should
be able to trade. Listing them keeps that denial as a backstop.

**Nautilus cannot cancel an external order in `SUBMITTED` status, and this is the
serious one.** `crates/execution/src/reconciliation/orders.rs` matches `Accepted`,
`Triggered`, `PartiallyFilled`, `Filled`, `Canceled`, `Expired` and `Rejected`; anything
else falls through to a warning and an empty event list. The order left working by the
first attempt was reported by IB on every subsequent connect, logged as *"Unhandled order
status SUBMITTED for external order"*, never entered the cache, and so was invisible to
`cancel_all_orders` - which then reported success, because the cache it consulted was empty.

That is precisely the *"unknown working broker order"* scenario `OPERATIONS.md` lists as a
required stage-six test, and **the framework currently cannot recover from it**. A tool that
reports "nothing working" while an order is live at the broker is worse than no tool, so
`cancel_working.py` now reports `CACHE CLEAR` rather than `PASS` and says what it cannot
see. The order itself has to be cancelled in TWS by hand.

## What stage four found, and what it deliberately did not test

**All five testable shapes were accepted**: LIMIT/GTC, LIMIT/DAY, STOP_MARKET/GTC,
STOP_LIMIT/GTC, and the gap fade's bracket as a three-order list submitted and cancelled as
one. Buy limits sat at half the reference and buy stops at twice it, so nothing could fill.

**MARKET is untested, and that is the point of recording it.** The gap fade's bracket uses
`entry_order_type=OrderType.MARKET`, which is the single most important type in the system
and **cannot be tested without filling** - there is no far-from-market price for a market
order. Submitting one opens a real paper position, and stage three established that this
project cannot yet reliably clean up after itself. So the market path waits for stage five,
under supervision, where a position needing to be closed is expected rather than a surprise.
The matrix prints it as `N/A` with the reason, because a matrix with an invisible hole reads
as complete.

The bracket was tested with a limit entry, which proves the *shape* - parent plus two
contingent children, submitted and cancelled as one list - and not child activation, since
children activate on the parent filling. Also stage five.

**The run was pre-open**, at 13:01 UTC against a 13:30 UTC cash open. IB's acceptance rules
for `DAY` and for stop orders are not necessarily the same inside a session as outside one,
so this matrix is evidence about submission, not about a live session. Stage five re-covers
it during RTH.

### The bug worth keeping

`order_factory.bracket()` returns a plain `list`, not an object with `.orders`. The first
attempt raised on `.orders` **after** the four single orders had already been submitted,
which aborted node startup with four orders on their way and no strategy left running to
cancel them.

The fix is not the attribute. It is that **everything is now constructed before anything is
sent**, so a construction error cannot leave a half-submitted matrix behind. That failure
mode is general: any batch of orders built and submitted in the same loop has it.

## What stage five measured, and it is the important one

The round trip completed: market entry filled, **both bracket children reached the broker and
sat working** - the thing stage four could not reach - the position closed on purpose, and the
sweep found nothing left. That is the machine working.

But the number that matters is the commission.

|              |                                                              |
| ------------ | ------------------------------------------------------------ |
| Deployed     | USD 947.13 (3 shares at 315.71, inside USD 1,000 of capital) |
| Price change | **-0.27 USD**                                                |
| Commission   | **2.02 USD**                                                 |
| Realised     | -2.29 USD                                                    |

**Commission was 88% of the loss.** The position moved nine cents against us across the hold
and cost two dollars and two cents to open and close.

### The cost model was predicting this, and now it is measured

[ADR-0009](decisions/0009-cost-is-modelled-at-the-target-account-size.md) rests on IB's USD
1.00 per-order minimum dominating everything at small size. That was modelled from a fee
schedule. It is now observed:

| Risk per trade                   | Measured commission |
| -------------------------------- | ------------------- |
| USD 1,000 (the research default) | 0.0020 R            |
| USD 125 (50k at 0.25%)           | 0.0162 R            |
| **USD 20 (8k at 0.25%)**         | **0.1010 R**        |
| USD 8 (8k at 0.10%)              | 0.2525 R            |

The model predicted 0.11 R at USD 20 of risk. The broker charged **0.101 R**. Within ten
percent, from a fee schedule, before any trade had ever been placed.

Against AAPL's walk-forward gross expectancy of **+0.0492 R**, commission alone leaves
**-0.0519 R** - and that is before spread. **The gap fade's negative verdict at the target
account size is now an empirical result, not an inference.**

### Two things this did not measure

**Slippage.** The mid came from a **delayed** quote, so the entry filling at 315.71 against a
316.155 "mid" is not evidence of a favourable fill - it is evidence that a 15-minute-old
quote is not a benchmark. Nothing here is admissible about fill quality, which is also what
IBKR says about its paper environment.

**Diversification.** Three shares of one instrument was **95% of the deployable capital**. At
this account size there is no second position. That is a real constraint on any strategy
design that assumes a portfolio.

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

| Date       | Stage | Result                                                                                                                                                                                                                                                                                    | Evidence                                            |
| ---------- | ----- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------- |
| 2026-09-01 | 1     | **Pass.** Connected to paper on `172.17.112.1:7497`. Halt applied before start and **still `HALTED` after startup**. Three instruments resolved. Clean shutdown.                                                                                                                          | `live/out/preflight_20260901T121340Z.json`          |
| 2026-09-01 | 2     | **Pass** on the third attempt. Account `IB-DUT067974` reported by the broker, USD 1,000,000, reconciliation clean (0 orders, 0 positions). Two earlier attempts failed: Read-Only API, then a venue-lookup bug of mine.                                                                   | `live/out/preflight_20260901T122835Z.json`          |
| 2026-09-01 | 3     | **Pass** on the third attempt. `AAPL=STK.SMART` BUY LIMIT 1 @ 135.93 submitted, accepted (venue order id `832000001`), cancelled. No fill, no reject, no deny. **One order from an earlier attempt is still working at the broker and cannot be cancelled through Nautilus** - see below. | `live/out/controlled_order_20260901T124521Z.json`   |
| 2026-09-01 | 4     | **Pass**, second attempt. Five shapes round tripped: LIMIT/GTC, LIMIT/DAY, STOP_MARKET/GTC, STOP_LIMIT/GTC and a three-order bracket. Run **pre-open**, and MARKET is untested by design - both noted below.                                                                              | `live/out/order_types_20260901T130102Z.json`        |
| 2026-09-01 | 5     | **Pass**, first attempt, during RTH. 3 AAPL at market inside USD 1,000 of capital. Entry filled 315.71, both bracket children accepted and working, position closed on purpose at 315.62, nothing left working. Realised **-2.29 USD**, of which **2.02 USD was commission**.             | `live/out/supervised_session_20260901T133242Z.json` |

## Where the code is

| Piece                         | Location                                                         | State                                                             |
| ----------------------------- | ---------------------------------------------------------------- | ----------------------------------------------------------------- |
| Paper/live discrimination     | [`../live/session.py`](../live/session.py)                       | Built, 17 tests                                                   |
| Node builder and order switch | [`../live/node.py`](../live/node.py)                             | Built, unrun against a broker                                     |
| Preflight check script        | [`../live/preflight.py`](../live/preflight.py)                   | Built and run. Stages 1 and 2.                                    |
| Instrument id bridge          | [`../live/symbology.py`](../live/symbology.py)                   | Built, 10 tests. Research `AAPL.XNAS` to broker `AAPL=STK.SMART`. |
| Controlled order              | [`../live/controlled_order.py`](../live/controlled_order.py)     | Built and run. Stage 3.                                           |
| Working-order sweep           | [`../live/cancel_working.py`](../live/cancel_working.py)         | Built. Cannot see external SUBMITTED orders; says so.             |
| Order type matrix             | [`../live/order_types.py`](../live/order_types.py)               | Built and run. Stage 4.                                           |
| Supervised round trip         | [`../live/supervised_session.py`](../live/supervised_session.py) | Built and run. Stage 5.                                           |
| Failure injection             | -                                                                | Not built. Stage 6.                                               |
