# Draft: a day in the life of the operator

**Status: working draft, second pass 2026-09-04. Not governance.** The [charter](CHARTER.md), the
[playbook](playbook/README.md) and the ADRs decide things; this file does not. It exists
to be argued with and rewritten a few times, and its job is to force the question the
other documents do not ask: *what does the person actually do, at what hour, with which
command, and what happens when the answer is "nothing exists yet"?*

First pass was an end-to-end *read* of the playbook against the code, 2026-09-03. Second
pass **ran it** on 2026-09-04 - every command below was executed in order, against the
real catalog and a live TWS, and the timings and outputs are from that walk. Three of the
gaps closed between the two passes; two new ones were found by running rather than
reading, which is the argument for doing this again.

Every **GAP** is tracked in [`ROADMAP.md`](ROADMAP.md); this file is where they are seen
in sequence rather than as a list.

## What the day needs exported

Not a detail. Two of these are not in any `.env`, and one of them fails opaquely.

```bash
set -a; . trade-copilot/.env; set +a          # MARKETSTACK_API_KEY, DATABENTO_API_KEY
export IBAPI_TIMEZONE_ALIASES="JST=Asia/Tokyo"  # or every IB connect fails opaquely
export COPILOT_PAPER_ACCOUNT=DUT067974          # or preflight refuses, clearly, at least
```

## The clock is the hard part

The operator is in Japan. The market is not.

|                                      | US daylight time        | US standard time        |
| ------------------------------------ | ----------------------- | ----------------------- |
| Session opens 09:30 ET               | **22:30 JST**           | **23:30 JST**           |
| Charter execution window, first 1-2h | 22:30-00:30 JST         | 23:30-01:30 JST         |
| Session closes 16:00 ET              | **05:00 JST**, next day | **06:00 JST**, next day |

Two consequences that shape everything else. **The decision and the execution are on
opposite ends of the operator's day** - the signal is fixed by a close that happens while
they sleep, and the order goes in that evening, roughly sixteen hours later. And **the
session ends after the operator has gone to bed**, which is why the playbook's
monitoring-end policy is not optional and why an alerting path is not a nicety.

Never hard-code these. `copilot/data/calendar.py` holds the trading days, the holidays
and, as of 2026-09-03, the three scheduled early closes - on which the market shuts at
13:00 ET and the evening ends three hours sooner.

## The day, in order

### ~07:00 JST - the close has already happened

The US close was about two hours ago. Nothing is urgent; this is the thinking part.

| Step                                      | Command                                              | Walked 2026-09-04          |
| ----------------------------------------- | ---------------------------------------------------- | -------------------------- |
| Ingest yesterday's bar                    | `python -m copilot.data.append`                      | **3s**, exit 0             |
| Check corporate actions on anything new   | `python -m copilot.data.corporate_actions AAPL,MSFT` | **1s**, 0 to add           |
| Recompute the verdict if anything changed | `python -m copilot.strategies.validate --changed`    | **<5s** when nothing moved |

The data gap closed on 2026-09-04. The evaluation window is pinned at both ends
([ADR-0017](decisions/0017-the-evaluation-window-is-pinned-at-both-ends.md)), so the same
catalog is frozen for research and fresh for execution, and `append` keeps it current
without moving a verdict. The first pass framed this as "the strategy warms from a frozen
catalog"; it did not warm from anything, which is a different and worse problem.

~~**GAP - "if anything changed" has no answer.**~~ **Closed 2026-09-04.** The verdict
recompute was two minutes, then five with twelve activations, and it was run here for
nothing: the catalog gained no bars, so no fold could have moved. Every verdict now
carries the four digests it was computed from, and `validate --changed` reads them
against the world - the bars inside the window, the activation's identity, the cost
basis, the code - and skips what cannot have moved, naming what did when it runs. The
append lands past the window and so cannot change the data digest, which is the whole
reason the morning is now cheap.

### Daytime - nothing

Deliberately. The charter's signal frequency is daily and its holding period is days to
weeks. A design that needs attention here is the wrong design for this operator.

### ~21:30 JST - one hour before the open

The playbook's **Before** checklist. This is the densest hour of the day and most of it
is built.

| Check                                                          | Command                                 | Walked 2026-09-04                       |
| -------------------------------------------------------------- | --------------------------------------- | --------------------------------------- |
| Strategy id, commit, config hash, data hash                    | -                                       | **GAP** - no manifest tool              |
| Broker connection, account, permissions, data type, cash       | `python -m copilot.live.preflight`      | **43s**, 6/6 PASS                       |
| Holiday, early close, DST, allowed session                     | `copilot.data.calendar`                 | Now wired - `warmup` picks the session  |
| Reconcile positions, cash, working orders                      | `python -m copilot.live.cancel_working` | Built, **one symbol per run**           |
| Indicator warm-up from the local catalog                       | `python -m copilot.live.warmup`         | **<1s**, 6/6 ready, exit 0              |
| Fresh, non-crossed, correctly timestamped quote per instrument | -                                       | **GAP** - not in `preflight`, see below |
| Kill switch and remote broker access verified                  | -                                       | **GAP** - no operator kill command      |

**GAP - `preflight` does not check quotes, and the first pass said it did.** Its six
checks are the halt before start, the halt through startup, instruments resolved, the
account reported by the broker, balances present, and a clean shutdown. Not one of them
looks at a price. The playbook asks for a fresh, non-crossed, correctly timestamped quote
per instrument before the session, and nothing provides it - which matters more than it
sounds, because a stale or crossed quote is how a bracket gets placed around the wrong
level and the account is on delayed data anyway.

**GAP - the monitoring-end sweep is one symbol at a time.** `cancel_working` defaults to
`--symbol AAPL` and takes one instrument per invocation, so a three-instrument sweep is
three commands and a forgotten one leaves an order working overnight. That is precisely
the failure the monitoring-end policy exists to prevent, and it is currently prevented by
the operator remembering.

Every one of these needs `IBAPI_TIMEZONE_ALIASES="JST=Asia/Tokyo"` exported first. It is
not optional and its absence fails opaquely.

### 22:30-00:30 JST - the execution window

The charter's predeclared window, and the only part of the day where money moves.

**What the spread actually costs here was unknown until 2026-09-03.** Measured over 7.6
years, the first two hours run a p95 of 2.455 bps on AAPL, 2.958 on MSFT and 0.785 on
SPY - against the 3.981, 3.593 and 1.048 the pinned cost model charges. The model is
conservative against the window an order really goes into. The **first five minutes** are
a different animal, 6.2x the closing spread on AAPL and 23x on PEP, which is an argument
for the window being an hour wide rather than a moment.

**GAP - no strategy has ever run in a live or paper node.** Every piece of the machine is
tested; the strategy has never been on the other end of it. That is the correct order of
work and it means this row of the table is currently theatre.

### 00:30 JST - monitoring end

The policy that matters most, because the operator is about to stop watching while the
market keeps trading. Block new entries; cancel every working entry; **verify the broker
agrees**; leave only approved protective orders; alert on anything unconfirmed.

Two measured facts sit behind that wording. A clean sweep is not proof the broker has
nothing working - an order held by a TWS precautionary setting never reaches the broker
and no API call can see it. And a cancel takes effect **from the client id that placed
the order**, not from a foreign one.

**GAP - nothing alerts.** The playbook makes alerting a limb of the kill switch and a
gate for unattended paper. No code in this repository notifies anyone of anything.
`failure_injection` proves the system notices; it does not prove the operator is told.
The operator is asleep in ninety minutes.

### Next morning - the After checklist

| Step                                                                    | State                                   |
| ----------------------------------------------------------------------- | --------------------------------------- |
| Reconcile positions, cash, commissions, executions, open orders         | Partly built                            |
| **Compare live decisions against offline replay**                       | **GAP** - no tool                       |
| Archive logs, config, data manifest, identifier map                     | Partial - sessions write to `live/out/` |
| Attribute P&L to move, signal, spread, slippage, fees, FX, intervention | **GAP**                                 |

Without the replay comparison, a session that silently decided differently from the
backtest looks exactly like one that agreed.

## What running it exposed that reading it did not

**A check that is wrong at the hour it is used trains you to ignore it.** The first
version of `warmup` defaulted to "the next trading day after today", which at 21:30 JST -
an hour before tonight's open - reported BLOCKED for a catalog that was completely ready.
The correct answer is today's session while today's session has not opened, and the walk
is the only thing that would have caught it: every unit test passed, because they all
passed an explicit date. Fixed 2026-09-04.

**A verdict now needs to say what it did not score.** With the catalog running to
2026-09-03 and the window ending 2026-01-01, `validate` was reporting folds over
2005-2021 with nothing on screen saying 169 bars were clipped. The record carried it; the
console did not, and the console is what the operator reads. Fixed the same day.

**The machine time is not the problem.** The whole morning is 2m07s and the pre-open
block is under a minute. What costs is that it is six commands in an order that exists
only in this document.

## What this walk exposes that a list does not

**The blocking constraint is not any of the gaps above.** It is that Track A has never
finished. The holdout is unspent, so no candidate is frozen, so paper stages seven and
eight cannot honestly begin. Everything in the evening column is machinery waiting for a
premise that has not earned its way through the first gate.

**And the premise is sitting on the boundary.** The account-size sweep built on
2026-09-03 puts the best crossing at **USD 10,000** for `aapl-gap-fade-long-next-close`,
against a charter that describes an account *under* USD 10,000. Not comfortably clear,
and worth remembering before any of the evening's plumbing is treated as urgent.

**Two gaps can only be found in live.** Sizing from settled cash cannot be tested on the
paper account, which is margin with a million dollars in it. Neither can the cash
account's real buying-power behaviour. Those are the two places where the first real
failure is most likely, and the rehearsal cannot rehearse them.

## To argue with next time

- Does the execution window want to be a window at all, or a single time? The spread
  evidence says the first five minutes are expensive and the rest of the two hours is not.
- ~~Is one catalog for research and execution ever going to work?~~ **Answered
  2026-09-04:** yes, with the window pinned at both ends. The freeze was telling us the
  *carve* had one end, not that the products differ.
  ([ADR-0017](decisions/0017-the-evaluation-window-is-pinned-at-both-ends.md))
- Should the day have two commands instead of eight? The walk took six invocations with
  three separate exports, and the ordering is knowledge that lives only in this file.
- What is the smallest alerting path that satisfies the playbook - a phone push on a
  handful of conditions is probably enough, and probably an evening's work.
- What does the operator do on a scheduled early close, when the evening ends at 02:00
  JST instead of 05:00?
