# Operations

Running a session, and every way one can go wrong. Covers the system, paper and canary
gates from [`../CHARTER.md`](../CHARTER.md).

The deployment shape this assumes progresses in stages and does not skip ahead
([ADR-0006](../decisions/0006-ops-progression.md)): WSL with host TWS now, dockerized IB
Gateway once a strategy is validated, Kubernetes once proven. **Stage one is not
unattended.** A machine that sleeps is intermittent, and a cooldown that resets on restart
is not a cooldown.

## Testing before a broker is involved

### Unit and property tests

Indicator values against hand calculations. No use of future bars. Signal timestamp versus
next-eligible-execution timestamp. Position-size rounding and zero-quantity skips. Maximum
position and loss limits. Duplicate event and duplicate intent idempotency. Session and
holiday rules. Configuration validation.

### Synthetic adverse scenarios

Deterministic tests for: gap through stop; missing, stale, duplicated and out-of-order data;
split or extreme bad tick; partial fill and no fill; reject, cancel, cancel-replace race and
delayed acknowledgement; disconnect before acknowledgement; restart with and without
persisted state; broker position not matching local state; unknown working broker order;
and limit, notional, exposure and daily-loss breaches.

### Reproducibility

The same code, data hash and configuration must produce identical decisions, identical
client order intents, and identical metrics within declared tolerances.

## Broker integration, separately from strategy testing

**Broker-integration testing and strategy forward testing are different activities.**
Controlled connectivity, read-only reconciliation and minimum-size order-lifecycle tests may
begin before a strategy passes the research gate, and they validate no edge whatever.
Strategy forward testing starts only after the candidate is frozen and the offline gates
pass.

IBKR states its paper environment relies on more simulated technology and that execution
behaviour can differ from live. **Paper validates behaviour far more than it validates fill
quality.**

Connection detail is version-specific: the default local paper IB Gateway endpoint, and the
constraint that an execution client ID cannot be divisible by 1,000 because of order-ID
partitioning. **Verify both against the pinned version** before each release rather than
trusting any document, including this one.

### Paper stages

1. Connect with strategy orders disabled.
2. Confirm instruments, account, currency, feed type and session calendar.
3. Submit and cancel one controlled order through the strategy path.
4. Test every planned order type and time-in-force.
5. Run a full supervised session.
6. Trigger stale-data, disconnect, reject and reconciliation tests.
7. Run multiple supervised sessions with the frozen strategy.
8. Unattended paper only after alerts and recovery drills pass.

### Two scorecards, two thresholds

**System correctness.** 100% signal-to-intent parity against an independent replay; zero
duplicate orders; zero unresolved position or working-order mismatches; zero unhandled
critical exceptions; a recorded reason for every reject and cancel; every risk limit tested
and enforced; all alerts arriving and acknowledged inside the declared deadline; restart and
reconnect drills finishing in the expected safe state.

**Strategy forward evidence.** Real-time signals match offline recomputation; frequency,
holding time, exposure and turnover stay inside preregistered ranges; paper slippage recorded
but not treated as proof of live fill quality; outcomes inside broad research prediction
intervals; **no parameter or logic change for P&L reasons**.

Preregister the two separately. For operational readiness: at least eight weeks **plus** at
least 30 completed and reconciled order-lifecycle or injected failure events, with zero
unresolved critical incidents. For strategy evidence: count **scheduled decision points,
including valid no-trade decisions**, not fills, and require enough non-overlapping
observations to compare against research ranges. A weekly or monthly strategy may need far
longer than eight weeks. **Do not manufacture trades to reach a threshold.**

## Running a session

Two commands own the sequence, so the order below is enforced rather than remembered:
`python -m copilot.live.day morning` after the close and `day evening` an hour before the
open. Each step is one of the commands named below, run in order with its exit code read;
the corporate-actions scan blocks the verdict, the preflight blocks the basket, and the
sweep runs whatever happened before it.

### Before

- Verify strategy ID, code commit, config hash and data hash.
- Confirm broker connection, account, permissions, market-data type and cash.
- Confirm US holiday, early close, DST state and allowed session.
- Reconcile positions, cash and working orders.
- Complete indicator warm-up from the local catalog.
- Before enabling entries, require a **fresh, correctly timestamped, non-crossed bid and
  ask** from the expected feed for every tradable instrument. Check quote age, spread,
  session status and instrument mapping. One isolated quote is not sufficient.
- Verify the kill switch and remote broker access.

### During

- Watch data freshness, clock synchronisation, connection state, acknowledgements, rejects,
  partial fills and slippage.
- Enforce order, symbol, gross, net, risk and daily-loss limits **outside** the strategy.
- Preserve decision IDs, client order IDs, broker order IDs and broker permanent IDs.
- Acknowledge critical alerts within the deadline.

### At monitoring end

This policy is not optional and its default is deliberate. **Block new entry intents; cancel
every working entry order; wait for and verify broker cancellation acknowledgements; leave
only explicitly approved protective or exit orders working; alert on any order whose status
cannot be confirmed.**

If broker state is uncertain, **remain in safe mode**. Do not submit replacement or
flattening orders until reconciliation completes.

**What the cancel actually reaches.** `live/cancel_working.py` cancels what is in the
cache, and an order only enters the cache if reconciliation adopted it. Measured
2026-09-01: Nautilus did not adopt an external order the broker reported as `SUBMITTED`, so
an order left working by a previous run was invisible to the sweep, which then reported
success because the cache it consulted was empty. **Fixed the same day** - `Submitted` is
adopted, and the execution client fetches orders from every client id - though the recovery
has never been confirmed against the broker.

`cancel_working --all` sweeps every registered instrument in one node (2026-09-05); a
sweep that took one symbol per invocation was six minutes of commands and a forgotten one.

**A clean sweep is still not proof the broker has nothing working**, and this does not
depend on that fix. An order held by a TWS precautionary size setting never reaches the
broker at all and no API call can see it. Confirm against the broker's own order list, and
treat an order whose status cannot be confirmed as an alert rather than a pass.

### After

- Reconcile positions, cash, commissions, executions and open orders.
- Compare live decisions against offline replay - `python -m copilot.live.compare`, run
  by the morning command, recomputes every decision in the session record through the
  gate's engine over the same bars and reports any field that differs beyond the
  session's own rounding. Its first run found the live ATR one bar behind the replay's.
- Archive logs, config, data manifest, identifier map and any incident report.
- Attribute P&L to market move, signal, spread, slippage, fees, FX and manual intervention.

## Which layer owns a surprise

The first question on any failure, asked before the cause is guessed at. Every surprise
on the broker path lives in one of four layers, each with its own reference and its own
fix, and the week that established this list spent most of its debugging time on the
layer *between* a symptom and its owner.

| Layer                                  | What it decides                                                                                           | Reference                                                                                          |
| -------------------------------------- | --------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| **The broker** - IB's servers and TWS  | Entitlements, sessions, error codes, order binding, GUI-side settings, what the paper environment honours | IB's TWS API reference; `PREFLIGHT.md` and `PAPER_CAMPAIGN.md` for what this account actually does |
| **The wire crate** - `ibapi` (Rust)    | How our code speaks the protocol, what a call returns, which conveniences exist                           | `docs.rs/ibapi`; a third party's reasoning arriving as a dependency                                |
| **Our adapter and engine** - inherited | What we do with the broker's answers: maps, events, reconciliation, risk checks                           | The code, Nautilus's developer guides, `UPSTREAM_DELTA.md`                                         |
| **Our environment** - this machine     | Addresses, timezones, credentials, which venv a hook sees                                                 | `MAINTENANCE.md`, "Standing up a new machine"                                                      |

Sorted from the first week of broker work, so the pattern is visible:

| Symptom                                                                | Layer       | Why it was not the layer it resembled                                                                                      |
| ---------------------------------------------------------------------- | ----------- | -------------------------------------------------------------------------------------------------------------------------- |
| IB 2188 on every historical request                                    | Broker      | Looked like an adapter bug; it is a subscription rule, documented, and unchanged by any code                               |
| IB 162 after opening the web portal                                    | Broker      | Looked like an entitlement change; it is one-session-per-login, and reading 2188 while 162 is active gives a wrong answer  |
| IB 321, then no account, no balances                                   | Broker      | Two failures that named nothing; a Read-Only API checkbox in the TWS GUI                                                   |
| Four orders sitting in TWS hours later                                 | Broker      | A precautionary size setting holding them unsent; invisible to every API call, unfixable in code                           |
| Paper accepts a USD 24M order on a USD 1M account                      | Broker      | Paper does not enforce buying power on a far-from-market limit; the reject path cannot be decided here                     |
| "Failed to connect" on every attempt in a fresh shell                  | Wire crate  | Looked like TWS; `ibapi` has no alias for the `JST` TWS reports, and says nothing useful                                   |
| `max_notional_per_order` never fires                                   | Our engine  | Looked like paper leniency; the account lookup by instrument venue fails on IB and the check was skipped, silently         |
| A working order reported on every reconnect, cancellable by no tool    | Our engine  | Looked like a broker quirk; reconciliation dropped external `SUBMITTED` orders on the floor                                |
| Cancel-all sends the cancel, then nothing - no event, cache still open | Our adapter | Looked like IB refusing a foreign-client cancel; TWS accepted it, and only our bookkeeping failed                          |
| Trusted IPs entry that worked yesterday does not today                 | Environment | Looked like a TWS regression; the WSL address changes on reboot, and a container appears as loopback while native does not |

The tell, in every row: the symptom's wording named the wrong layer. Read the layer off
the *mechanism* once it is found, never off the error text, and write the row here when
it is.

## Disconnect and recovery

IBKR publishes reset windows and warns that login and order management can be interrupted
while execution reports and simulated orders may be delayed. Check the current schedule
rather than hard-coding a reset time.

Code **1101** means connectivity restored **with data lost** and market-data requests need
resubmitting. Code **1102** means connectivity restored with data maintained. They are not
interchangeable.

On connectivity loss:

1. Block new entries.
2. Preserve local state and logs.
3. **Do not blindly resubmit unknown orders.**
4. Determine whether broker-side orders may still be working.
5. On reconnect, request or verify open orders, executions, positions, cash and account
   state.
6. Resubscribe data where required.
7. Reconcile **before** enabling new orders.
8. Require explicit recovery approval after a critical mismatch.

Nautilus provides startup and runtime reconciliation for aligning venue state with internal
event-sourced state. **Treat the broker as authoritative** for current external positions
and working orders, while keeping local evidence for diagnosis.

## Historical data and warm-up

Broker historical data is for bounded requests, not as the research archive or a startup
backfill storm. IBKR documents pacing limits for small bars including no identical request
within 15 seconds, fewer than six for the same contract, exchange and tick type within two
seconds, and no more than 60 in ten minutes - with `BID_ASK` counting twice.

For a daily strategy: download and validate before the session; store an immutable raw copy
and a versioned catalog copy; warm indicators from the local catalog; **block trading until
every instrument has the required history and a current observation**; and never replace
missing history with zeros or default signals.

A cache with a backing database is not equivalent to a research catalog. Verify the
behaviour of the pinned version.

## Kill switch

**Independent of the strategy.** Trigger safe mode on: required data stale beyond tolerance;
broker or market-data connectivity loss; clock synchronisation failure; unknown or duplicate
order; rejection burst or abnormal slippage; position, cash or working-order mismatch; order,
notional, exposure, planned-risk or daily-loss breach; unhandled exception in the decision or
execution path; or a critical alert unacknowledged by deadline while supervised.

Safe mode means:

1. Block new entries.
2. Preserve state and alert.
3. Cancel only orders the documented policy says are safe to cancel.
4. **Avoid automatic flattening when data or broker truth is uncertain.**
5. Require the recovery checklist before re-enabling.

**Test it deliberately in paper.** An untested kill switch is a comment.

## Tiny live canary

Live trading is initially an **execution experiment**, not an income attempt.

Advance only when the frozen candidate passed every research gate; offline replay and paper
signals match; all order types were tested; supervised and unattended recovery drills passed;
paper evidence minimums were met; there are no unresolved critical incidents; and live loss,
pause and retirement rules are **already written**.

Start with one strategy, one or very few instruments, one-share or minimum prudent
whole-share orders, planned risk far below target, supervised execution, and no parameter
changes.

**Do not scale because of early profit.** Scale only after a predefined number of live
decisions and a review of actual slippage, error rates, drawdown and behaviour. Increase one
dimension at a time.

## Drift, pause and retirement

Monitor signal frequency and distribution, exposure and holding time, turnover and costs,
fill rate and slippage, realised versus expected volatility, drawdown depth and duration,
feature and universe drift, concentration of returns, and operational incidents.

Pause automatically when drawdown breaches its preregistered limit; live slippage
persistently exceeds the research stress range; signal or trade frequency leaves its expected
range; data provenance or corporate-action handling becomes uncertain; a material code,
broker or data-source change occurs; or reconciliation and duplicate-order safety is
compromised.

**Retirement is a normal outcome.** Archive the evidence rather than quietly modifying a
strategy until it looks profitable again.

## Checklists

### Paper session

- [ ] Correct paper account and environment
- [ ] Strategy and config hashes verified
- [ ] Calendar and data feed verified
- [ ] Warm-up complete
- [ ] Broker state reconciled
- [ ] Risk limits and kill switch armed
- [ ] Monitoring-end policy active
- [ ] Post-session replay and reconciliation complete

### Live release

- [ ] Research, system, paper and canary criteria documented
- [ ] No material change since paper evidence began
- [ ] Emergency broker access tested
- [ ] Loss and drawdown budgets approved
- [ ] Actual position size rounds safely
- [ ] Tax and recordkeeping process ready
- [ ] Rollback and pause procedure tested
