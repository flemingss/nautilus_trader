# Draft: a day in the life of the operator

**Status: working draft, 2026-09-04. Not governance.** The [charter](CHARTER.md), the
[playbook](playbook/README.md) and the ADRs decide things; this file does not. It exists
to be argued with and rewritten a few times, and its job is to force the question the
other documents do not ask: *what does the person actually do, at what hour, with which
command, and what happens when the answer is "nothing exists yet"?*

Written after an end-to-end read of the playbook against the code on 2026-09-03. Every
**GAP** below is tracked in [`ROADMAP.md`](ROADMAP.md); this file is where they are seen
in sequence rather than as a list.

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

| Step                                      | Command                                                             | State                                            |
| ----------------------------------------- | ------------------------------------------------------------------- | ------------------------------------------------ |
| Ingest yesterday's bar                    | `python -m copilot.data.backfill --symbols ... --from ... --to ...` | Built, and **cannot be run** - see the gap below |
| Check corporate actions on anything new   | `python -m copilot.data.corporate_actions NVDA,AVGO`                | Built 2026-09-03                                 |
| Recompute the verdict if anything changed | `python -m copilot.strategies.validate --all`                       | Built                                            |

**GAP - the live path has no source of today's bar.** The catalog is frozen at
2025-12-31 because extending it pushes the holdout past the charter's 20% band and every
`validate` run then raises `HoldoutCarveError` ([ADR-0012](decisions/0012-the-holdout-is-carved-at-2022-01-01.md)).
Research needs the freeze; execution needs freshness; one catalog serves both. This is a
design decision, not a bug to fix quietly - two catalogs, a live-only window, or a
re-decided carve are all defensible and they are not the same choice.

### Daytime - nothing

Deliberately. The charter's signal frequency is daily and its holding period is days to
weeks. A design that needs attention here is the wrong design for this operator.

### ~21:30 JST - one hour before the open

The playbook's **Before** checklist. This is the densest hour of the day and most of it
is built.

| Check                                                          | Command                                 | State                                     |
| -------------------------------------------------------------- | --------------------------------------- | ----------------------------------------- |
| Strategy id, commit, config hash, data hash                    | -                                       | **GAP** - no manifest tool                |
| Broker connection, account, permissions, data type, cash       | `python -m copilot.live.preflight`      | Built, passing                            |
| Holiday, early close, DST, allowed session                     | `copilot.data.calendar`                 | Built; **not wired into a session check** |
| Reconcile positions, cash, working orders                      | `python -m copilot.live.cancel_working` | Built for orders                          |
| Indicator warm-up from the local catalog                       | -                                       | Blocked by the data gap above             |
| Fresh, non-crossed, correctly timestamped quote per instrument | part of `preflight`                     | Built                                     |
| Kill switch and remote broker access verified                  | -                                       | **GAP** - no operator kill command        |

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
- Is one catalog for research and execution ever going to work, or is the freeze telling
  us they are different products?
- What is the smallest alerting path that satisfies the playbook - a phone push on a
  handful of conditions is probably enough, and probably an evening's work.
- What does the operator do on a scheduled early close, when the evening ends at 02:00
  JST instead of 05:00?
