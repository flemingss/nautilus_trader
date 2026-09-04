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
| 6   | Failure injection (**passed 2026-09-01**)         | Stale data, disconnect, reject and a deliberate position mismatch. Each detected, each handled as documented, each alerted.                                                                           |
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

### Repeated at a third of the size, 2026-09-02

The premise underneath [ADR-0009](decisions/0009-cost-is-modelled-at-the-target-account-size.md)
is that IB's per-order minimum makes cost **fixed per order rather than proportional to size**.
One measurement cannot show that; two at different sizes can.

|                   | 3 shares (2026-09-01) | 1 share (2026-09-02) |
| ----------------- | --------------------- | -------------------- |
| Deployed          | USD 947.13            | USD 326.28           |
| Commission        | **2.02 USD**          | **2.01 USD**         |
| Price change      | -0.27 USD             | -0.05 USD            |
| Realised          | -2.29 USD             | -2.06 USD            |
| Commission share  | 88%                   | **98%**              |
| At USD 20 of risk | 0.1010 R              | **0.1030 R**         |

**Cutting the position to a third changed the commission by one cent.** That is the per-order
minimum, measured rather than modelled, and it is why cost in R is essentially invariant to
size in this range - and why trading smaller does not help.

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

## What stage six found, and it invalidates a safety claim

Stages one to five confirmed the happy path. Stage six is the one that was worth the eight
weeks, and it failed on its first run - which is the outcome that justifies the stage.

### `max_notional_per_order` is silently inert on Interactive Brokers

The probe submitted 10 AAPL at a USD 158 limit - USD 1,580 against a configured cap of
USD 1,000. **It was accepted.**

The cause, confirmed by running the node at `DEBUG` rather than inferred:

```text
[DEBUG] nautilus_risk::engine: Cannot find account for venue SMART (account_id=None)
```

`RiskEngine::check_orders_risk_for_account` resolves the account with
`cache.account_for_venue(&instrument.id().venue)`. Instruments resolve on **`SMART`**; the
execution client registers the account under its own client name, so the account is
`IB-DUT067974` on venue **`IB`**. The lookup returns nothing, and the function then
**`return true`** - passing every order, including one that exceeds a cap the operator
explicitly configured.

The notional cap needs no account. It is a statement about the order. It is skipped anyway,
because it lives past an account guard that fails for an unrelated reason.

**Consequences, in order of seriousness:**

- Nautilus ships two pre-trade risk controls. On IB, **one of them does nothing**, and says
  so only at `DEBUG`.
- The `max_notional_per_order` backstop in
  [`../live/probes/supervised_session.py`](../live/probes/supervised_session.py) was fiction. It was
  described in stage five as denying a grossly wrong order before it reached the broker. It
  would not have.
- The same venue split has now caused three separate failures: the account lookup in stage
  two, order routing in stage three, and this. **It is not a series of coincidences, it is
  one unmodelled distinction** between a listing venue, a routing destination, and an
  account's home venue.

**What still works, and now matters more than it looked:** `TradingState::HALTED` is
enforced natively and was verified surviving node startup in stage one. Account-wide halting

- which is what [`../risk/guard.py`](../risk/guard.py) uses - is real. The per-order cap is
not. Until the cap is fixed, **the halt is the only pre-trade control this system actually
has.**

### IB paper accepted a USD 24M order on a USD 1M account

The second probe deliberately carried no cap, so it reached IB: 100,000 MSFT at a USD 240
limit. The broker accepted it.

Paper does not enforce buying power on a far-from-market limit. So **paper cannot validate
buying-power protection**, and a rejection path that has only ever been tested on paper has
not been tested. Add it to what paper cannot reproduce, beside the margin-versus-cash account
type and the million-dollar balance.

### What passed

Stale-feed detection fired at 20.2 seconds after the subscription was cut. That tests the
detector against a real feed going quiet; it does not test IB going quiet, which is a
different fault with the same symptom.

### The fourth case was a known failure at this point

Recovering an unknown working order was impossible on this run - reconciliation dropped an
external order reported as `SUBMITTED`. Not re-run, because re-running strands another live
order to re-learn something already evidenced.

**Superseded the same day.** The engine defect was fixed under
[ADR-0010](decisions/0010-the-repository-is-ours.md); see "The residue problem" below for
the three causes and the fix. What has still never happened is a confirmation at the
broker.

## Stage six, second run: the engine was fixed rather than the test

The first run failed because `max_notional_per_order` does nothing on IB. Under
[ADR-0010](decisions/0010-the-repository-is-ours.md) that is now ours to fix, so it was
fixed.

`RiskEngine::check_orders_risk_for_account` resolved the account by the instrument's venue
and, on failure, returned `true` for every order - skipping the balance, margin **and**
notional checks together. A per-order notional cap is a bound on the order, not on the
account, so it now applies whether an account resolves or not. The account-resolution failure
also moved from `DEBUG` to `WARN`: an operator who cannot see it believes risk checks are
running when they are not.

Two Rust tests pin it, and the pair was checked by stashing the fix - the denial test fails
without it, the companion still passes, so they pin the behaviour rather than the
implementation.

```text
denied_by_risk_engine   PASS   NOTIONAL_EXCEEDS_MAX_PER_ORDER: max=1000.00 USD
stale_feed_detected     PASS   no quote for 20.6s
left working: none
```

### Two cases are excluded, and named rather than scored

**`rejected_by_broker` - paper cannot decide it.** IB paper accepted a 100,000-share order
worth USD 24M on a USD 1M account. The probe is still submitted, because that is how we would
notice if IB ever started enforcing it, but scoring it would make stage six permanently
unpassable for a reason that has nothing to do with our system. **The rejection path must be
verified on the live account before any size increase.**

**`recover_unknown_working_order` - measured 2026-09-02, and the verdict splits.**
`live/probes/strand_recovery.py` stranded a one-share far-from-market GTC limit on purpose
(client ids 831/832), then started a fresh node on different ids (841/842) with an empty
cache. **Adoption is confirmed**: the external `SUBMITTED` order arrived in the fresh
node's cache through reconciliation - the 2026-09-01 engine fix watched working at the
broker, not merely unit-tested. **The cancel is a known failure**: the IB adapter could
not execute the sweep's cancel because the order's IB id (`832000002`) belongs to the
stranding client's id partition and the adapter's own maps have no entry for it -

```text
Error handling order update: Trader ID not found for Interactive Brokers order 832000002
Failed to emit pending cancel for order O-20260902-144719-001-000-1
    (IB order ID: 832000002): Instrument ID not found for pending cancel order
```

Two aggravations. The failure is **event-silent** - no `on_order_cancel_rejected` fires,
so the only trace is a Rust ERROR log and the residual-order warning at shutdown; a sweep
that trusts order events waits forever. And it means the operational recovery path for an
unknown working order is still "cancel it by hand in TWS" - what changed is that the
order is now *visible* to the recovering node, which is the half that makes the manual
step findable rather than a surprise. The remaining fix lives in the adapter's execution
core (`crates/adapters/interactive_brokers/src/execution/`), ours under ADR-0010, and is
tracked in the roadmap.

### The bug the reclassification introduced

Moving `rejected_by_broker` out of the scored list left its order in the id map, and the
probe lookup used a bare `next()`. That raised `StopIteration` inside the accepted handler,
which **skipped the cancel** and left a live order at the broker - reported only as "left
working", with no hint of the cause.

Two changes, and the second matters more than the first: the lookup returns `None` for an
unscored case, and **the cancel now runs before any bookkeeping**. Recording is bookkeeping;
leaving a live order at the broker is the failure, so nothing that might go wrong in
bookkeeping is allowed to run first.

The runner also waits for cancellations to be acknowledged before stopping, which
`OPERATIONS.md` requires and the first version skipped - reading `orders_open` while a cancel
is in flight reported a live order that was already on its way out. **A false alarm from a
safety check is as corrosive as a missed one.**

## The residue problem, and what actually caused it

Four MSFT orders from stage-six runs were sitting in the TWS GUI hours after the runs that
placed them. The sweep tool reported "nothing working" every time it was asked. Three
separate causes, found in the order of least to most interesting.

**`fetch_all_open_orders` defaults to `false`.** The adapter then calls `reqOpenOrders`, which
returns **only orders bound to the calling client id**. Every run used a fresh client id, so
each sweep was structurally blind to every previous run's orders and reported "nothing
working" truthfully and uselessly. Now set to `True` in `live/node.py`.

**Reconciliation dropped external orders reported as `SUBMITTED`.** Even once fetched they
never entered the cache, so `cancel_all_orders` had nothing to act on. Fixed in
`crates/execution/src/reconciliation/orders.rs`.

**And the orders were still not reachable afterwards**, because they had never left TWS. A
100,000-share order trips a TWS **precautionary size setting**, which holds it in the GUI
awaiting a manual transmit. That state is the worst of both: our system recorded an
acceptance, the broker never received the order, and no API call can see or cancel it. It is
the only failure so far that no amount of code on our side can recover from.

The fix for that one is not code. **An injected fault should be the smallest one that asks
the question.** The reject probe went from 100,000 shares to 5,000 - still USD 1.2M against
USD 1M of buying power, so it still asks - and stage six now finishes with nothing left
working. A larger fault tests the same thing and leaves more behind.

Orders already stranded in the GUI have to be cancelled there. Check **TWS, Global
Configuration, Presets, precautionary settings** before assuming an accepted order is one the
broker actually holds.

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

| Date       | Stage           | Result                                                                                                                                                                                                                                                                                                                           | Evidence                                                                         |
| ---------- | --------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| 2026-09-05 | 1-2             | **Pass, 15/15**, on the delayed feed with the session open. Nine instruments resolved from the registry; nine quotes, five each, 2.3s old, bid below ask. Pre-market quoting unverified until 2026-09-08.                                                                                                                        | `live/out/preflight_20260904T153311Z.json`                                       |
| 2026-09-05 | session, denied | Nine next-close activations in one node, orders denied, sized from USD 1,000 of a USD 1,000,253 account. No rule fired on 2026-09-03's bar. The replay comparison agreed **9/9** after the indicator-order fix; the record from the night before had disagreed 8/9.                                                              | `live/out/run_activation_20260904T153353Z.json`, `compare_20260904T153556Z.json` |
| 2026-09-05 | sweep           | `cancel_working --all`: nine instruments, one node, 40.3s, CACHE CLEAR.                                                                                                                                                                                                                                                          | `live/out/day_evening_20260904T153311Z.json`                                     |
| 2026-09-02 | 6-confirmation  | **Split.** Stranded `O-20260902-144719-001-000-1` on purpose; a fresh node on different client ids **adopted it through reconciliation** (the engine fix confirmed at the broker), but the adapter could not cancel it - IB order id in another client's partition, no order event raised. Cancelled by hand in TWS.             | `live/out/strand_recovery_20260902T144708Z.json`                                 |
| 2026-09-01 | 1               | **Pass.** Connected to paper on `172.17.112.1:7497`. Halt applied before start and **still `HALTED` after startup**. Three instruments resolved. Clean shutdown.                                                                                                                                                                 | `live/out/preflight_20260901T121340Z.json`                                       |
| 2026-09-01 | 2               | **Pass** on the third attempt. Account `IB-DUT067974` reported by the broker, USD 1,000,000, reconciliation clean (0 orders, 0 positions). Two earlier attempts failed: Read-Only API, then a venue-lookup bug of mine.                                                                                                          | `live/out/preflight_20260901T122835Z.json`                                       |
| 2026-09-01 | 3               | **Pass** on the third attempt. `AAPL=STK.SMART` BUY LIMIT 1 @ 135.93 submitted, accepted (venue order id `832000001`), cancelled. No fill, no reject, no deny. **One order from an earlier attempt is still working at the broker and cannot be cancelled through Nautilus** - see below.                                        | `live/out/controlled_order_20260901T124521Z.json`                                |
| 2026-09-01 | 4               | **Pass**, second attempt. Five shapes round tripped: LIMIT/GTC, LIMIT/DAY, STOP_MARKET/GTC, STOP_LIMIT/GTC and a three-order bracket. Run **pre-open**, and MARKET is untested by design - both noted below.                                                                                                                     | `live/out/order_types_20260901T130102Z.json`                                     |
| 2026-09-02 | 5               | **Pass**, repeated at a third of the size to test whether commission scales. 1 AAPL at market inside USD 500 of capital. Entry filled 326.28, both children accepted, closed on purpose at 326.23, nothing left working. Realised **-2.06 USD**, of which **2.01 USD was commission** - one cent less than the three-share trip. | `live/out/supervised_session_20260901T155038Z.json`                              |
| 2026-09-01 | 5               | **Pass**, first attempt, during RTH. 3 AAPL at market inside USD 1,000 of capital. Entry filled 315.71, both bracket children accepted and working, position closed on purpose at 315.62, nothing left working. Realised **-2.29 USD**, of which **2.02 USD was commission**.                                                    | `live/out/supervised_session_20260901T133242Z.json`                              |
| 2026-09-01 | 6               | **FAIL, and correctly so.** Stale-feed detection passed. Both refusal probes were **accepted**: our own `max_notional_per_order` never fired, and IB paper accepted a USD 24M order on a USD 1M account.                                                                                                                         | `live/out/failure_injection_20260901T134539Z.json`                               |
| 2026-09-01 | 6               | **Pass**, after fixing the engine it failed against. Both scored probes pass: the notional cap now denies (`NOTIONAL_EXCEEDS_MAX_PER_ORDER`), stale-feed detection fires at 20.6s, nothing left working. Two cases excluded and named: one paper cannot decide, one is a known gap.                                              | `live/out/failure_injection_*.json`                                              |

## Where the code is

| Piece                         | Location                                                                       | State                                                                                                                          |
| ----------------------------- | ------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------ |
| Paper/live discrimination     | [`../live/session.py`](../live/session.py)                                     | Built, 17 tests                                                                                                                |
| Node builder and order switch | [`../live/node.py`](../live/node.py)                                           | Built; runs every preflight, basket and sweep                                                                                  |
| Preflight check script        | [`../live/preflight.py`](../live/preflight.py)                                 | Built and run. Stages 1 and 2, plus a quote per instrument since 2026-09-05.                                                   |
| Instrument id bridge          | [`../live/symbology.py`](../live/symbology.py)                                 | Built, 10 tests. Research `AAPL.XNAS` to broker `AAPL=STK.SMART`.                                                              |
| Controlled order              | [`../live/probes/controlled_order.py`](../live/probes/controlled_order.py)     | Built and run. Stage 3.                                                                                                        |
| Working-order sweep           | [`../live/cancel_working.py`](../live/cancel_working.py)                       | Built and run. `--all` sweeps the registry in one node; the adoption fix confirmed against IB 2026-09-03.                      |
| Order type matrix             | [`../live/probes/order_types.py`](../live/probes/order_types.py)               | Built and run. Stage 4.                                                                                                        |
| Supervised round trip         | [`../live/probes/supervised_session.py`](../live/probes/supervised_session.py) | Built and run. Stage 5.                                                                                                        |
| Failure injection             | [`../live/probes/failure_injection.py`](../live/probes/failure_injection.py)   | Built and run. Stage 6 passes on the second run.                                                                               |
| Strand recovery               | [`../live/probes/strand_recovery.py`](../live/probes/strand_recovery.py)       | Built and run. Adoption confirmed; foreign cancel fails.                                                                       |
| Catalog warm-up               | [`../live/warmup.py`](../live/warmup.py)                                       | Built and run. Refuses a stale or holed window.                                                                                |
| A strategy on the broker      | [`../live/run_activation.py`](../live/run_activation.py)                       | Built and run. The basket in one node, orders denied, sized from the account; parameters checked by `strategies/promotion.py`. |
| Live vs replay comparison     | [`../live/compare.py`](../live/compare.py)                                     | Built and run. Found the live ATR one bar behind the engine's on its first run.                                                |
| The operator's day            | [`../live/day.py`](../live/day.py)                                             | Built and run. `morning` and `evening`: the sequence and its gates.                                                            |
