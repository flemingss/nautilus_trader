# Draft: a day in the life of the operator

**Status: working draft, fourth pass 2026-09-05. Not governance.** The [charter](CHARTER.md), the
[playbook](playbook/README.md) and the ADRs decide things; this file does not. It exists
to be argued with and rewritten a few times, and its job is to force the question the
other documents do not ask: *what does the person actually do, at what hour, with which
command, and what happens when the answer is "nothing exists yet"?*

First pass was an end-to-end *read* of the playbook against the code, 2026-09-03. Second
pass **ran it** on the morning of 2026-09-04. Third pass ran it again that evening and
found a defect the whole day's work had missed. Fourth pass, 2026-09-05 JST, ran the day
as **two commands** - the sequence this file had been carrying in prose now lives in
`copilot/live/day.py` - and the first replay comparison found a second defect that nine
sessions had run on without anyone noticing.

Every **GAP** is tracked in [`ROADMAP.md`](ROADMAP.md); this file is where they are seen
in sequence rather than as a list.

## What the day needs exported

Not a detail. Two of these are not in any `.env`, and one of them fails opaquely. As of
the fourth pass the day command checks for them before it runs a step and refuses by
name; the exports are still the operator's to make.

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
| Scheduled early close, 13:00 ET      | 02:00 JST, next day     | **03:00 JST**, next day |

Two consequences that shape everything else. **The decision and the execution are on
opposite ends of the operator's day** - the signal is fixed by a close that happens while
they sleep, and the order goes in that evening, roughly sixteen hours later. And **the
session ends after the operator has gone to bed**, which is why the playbook's
monitoring-end policy is not optional and why an alerting path is not a nicety.

Never hard-code these. `copilot/data/calendar.py` holds the trading days, the holidays
and the three scheduled early closes; `day evening` prints the session it is preparing
for on both clocks and says **EARLY CLOSE** when the calendar does. All three early closes
fall in standard time, so an early evening ends at 03:00 JST, not 02:00 - the third pass
had that wrong.

## The day, in order

### ~07:00 JST - the close has already happened

The US close was about two hours ago. Nothing is urgent; this is the thinking part.

```bash
python -m copilot.live.day morning
```

| Step                                      | Command                                           | Walked 2026-09-05 (JST)                                    |
| ----------------------------------------- | ------------------------------------------------- | ---------------------------------------------------------- |
| Ingest yesterday's bar                    | `copilot.data.append`                             | **5.4s**, `+0` on all nine, today pending; continues       |
| Check corporate actions on the registry   | `copilot.data.corporate_actions <all> --to today` | **4.8s**, nothing to add; a finding here **stops the day** |
| Recompute the verdict if anything changed | `copilot.strategies.validate --changed --write`   | **168.7s**, 12 recomputed - the code had changed           |
| Compare last night against the replay     | `copilot.live.compare`                            | **0.6s**, **8 of 9 DISAGREE** - see below                  |

Three minutes, one command, and the order is no longer knowledge that lives in this
file. The scan now runs over every registered symbol and up to today: its default window
ended 2025-12-31, so the command written to catch a split would have missed a 2026 one
every morning.

**FOUND - the live ATR stood one bar behind the replay's.** The first run of the
comparison took the record of 2026-09-04's basket and recomputed each decision through
the gate's own `BacktestEngine`, over the same sixteen warm-up bars and the same decision
bar. Eight of nine disagreed on the ATR by more than the session's own rounding: AAPL
6.7257 live against 6.9763 offline, MSFT 9.55 against 10.46. The cause was the order of
two calls. The engine updates a strategy's registered indicators *before* it calls
`on_bar`; the live path, which cannot publish a bar into the node, handed the decision bar
to `on_bar` directly, so the rule decided with the ATR of the last warm-up bar. Nine
sessions had run that way and every one of them read as a clean decision. Fixed the same
hour (`GapReversalStrategy.decide`), and the evening below compared **9 of 9**. That is
what the comparison is for, and it is now a morning step.

### Daytime - nothing

Deliberately. The charter's signal frequency is daily and its holding period is days to
weeks. A design that needs attention here is the wrong design for this operator.

### ~21:30 JST - one hour before the open

```bash
python -m copilot.live.day evening
```

| Check                                                          | Command                                        | Walked 2026-09-05 (JST)                                                       |
| -------------------------------------------------------------- | ---------------------------------------------- | ----------------------------------------------------------------------------- |
| Strategy id, commit, config hash, data hash                    | in every verdict's `inputs` block              | Narrowed: the four digests exist per verdict; no single manifest yet          |
| Broker connection, account, permissions, data type, cash       | `copilot.live.preflight`                       | **40.3s**, **15/15 PASS**, 9/9 instruments                                    |
| Fresh, non-crossed, correctly timestamped quote per instrument | `copilot.live.preflight`, the nine quote lines | **Closed**: 5 quotes each, 2.3s old, bid < ask, delayed feed                  |
| Holiday, early close, DST, allowed session                     | `day evening` prints the clock                 | Closed: both zones, early close named                                         |
| Reconcile positions, cash, working orders                      | `copilot.live.cancel_working --all`            | **40.3s for nine**, one node, was 41.5s each                                  |
| Indicator warm-up from the local catalog                       | `copilot.live.warmup`                          | **0.1s**, 12/12 ready                                                         |
| Parameters are the ones the gate scored                        | `copilot.strategies.promotion`, in the basket  | Closed: every session labelled `seeded identity ... not the gate's selection` |
| Kill switch and remote broker access verified                  | -                                              | **GAP** - no operator kill command                                            |

**What the gating did on its first run.** The evening was first run for the session of
2026-09-08 while Friday's session was still open, and the warm-up **blocked** all twelve
activations on Friday's missing bar - correctly, and the basket never ran. Run for the
session of 2026-09-04 instead, everything passed: preflight 40.3s, warm-up 0.1s, basket
50.6s, sweep 40.3s, **2m11s** for the evening. A stopping failure stops; the sweep runs
regardless.

**Unverified - the quote check before the open.** The nine quotes were read at 11:32 ET
with the session open. The evening runs the check at 08:30 ET on a delayed feed, and IB's
delayed data may not quote pre-market. If it does not, the check blocks every evening,
and the first honest reading is Tuesday 2026-09-08 at 21:30 JST.

### 22:30-00:30 JST - the execution window

The charter's predeclared window, and the only part of the day where money moves.

**What the spread actually costs here was unknown until 2026-09-03.** Measured over 7.6
years, the first two hours run a p95 of 2.455 bps on AAPL, 2.958 on MSFT and 0.785 on
SPY - against the 3.981, 3.593 and 1.048 the pinned cost model charges. The model is
conservative against the window an order really goes into. The **first five minutes** are
a different animal, 6.2x the closing spread on AAPL and 23x on PEP, which is an argument
for the window being an hour wide rather than a moment.

`python -m copilot.live.run_activation --all` ran nine next-close activations in one
node: equity 1,000,253.87 reported, sized against 1,000, risk budget 1.00 per position,
notional cap 100.00, session cap 5.00 with two entries. No rule fired on 2026-09-03's bar,
so the ledger read `0 of 5.00 reserved, entries 0/2` after every decision. Orders stay
denied: nothing is frozen.

**FOUND - a triggered `next_close` decision goes nowhere.** The rule decides on bar *t*
and enters on bar *t+1*, which live is the session opening an hour after the evening
command runs. The strategy sets a deferral on the trigger; the process ends; tomorrow's
warm-up never calls `on_bar` on bar *t*. So the entry is never made, and until the
fourth pass the record could not show a trigger at all - no skip, no order, a quiet bar.
It now records `deferred_atr` and prints **DEFERRED**. Carrying the decision into its
session is a real design question (the replay fills at *t+1*'s close, the charter's
window is its first hours, and the bracket's levels need a price the fill has not yet
given) and is stage-seven work that needs a frozen candidate to be worth deciding.

### 00:30 JST - monitoring end

The policy that matters most, because the operator is about to stop watching while the
market keeps trading. Block new entries; cancel every working entry; **verify the broker
agrees**; leave only approved protective orders; alert on anything unconfirmed.

Two measured facts sit behind that wording. A clean sweep is not proof the broker has
nothing working - an order held by a TWS precautionary setting never reaches the broker
and no API call can see it. And a cancel takes effect **from the client id that placed
the order**, not from a foreign one.

`cancel_working --all` swept nine instruments in one node in 40.3s, `RESULT: CACHE
CLEAR`, each instrument on its own line. While orders are denied the sweep runs as the
evening's last step; once they are enabled it is the 00:30 command, and the day will
need a third phase for it.

**GAP - nothing alerts.** The playbook makes alerting a limb of the kill switch and a
gate for unattended paper. No code in this repository notifies anyone of anything.
`failure_injection` proves the system notices; it does not prove the operator is told.
The operator is asleep in ninety minutes.

### Next morning - the After checklist

| Step                                                                    | State                                                 |
| ----------------------------------------------------------------------- | ----------------------------------------------------- |
| Reconcile positions, cash, commissions, executions, open orders         | Partly built                                          |
| Compare live decisions against offline replay                           | **Closed** - `compare`, in `day morning`; 9/9 tonight |
| Archive logs, config, data manifest, identifier map                     | Partial - sessions and days write to `live/out/`      |
| Attribute P&L to move, signal, spread, slippage, fees, FX, intervention | **GAP**                                               |

## What running it exposed that reading it did not

**A check that is wrong at the hour it is used trains you to ignore it.** The first
version of `warmup` defaulted to "the next trading day after today", which at 21:30 JST
reported BLOCKED for a catalog that was completely ready. Fixed 2026-09-04.

**A verdict now needs to say what it did not score.** With the catalog running past the
window's end, `validate` reported folds over 2005-2021 with nothing on screen saying 169
bars were clipped. Fixed 2026-09-04.

**A tool that exists is not a tool that runs.** The corporate-actions scan was built on
2026-09-03, listed in the morning table on the first pass, and did not run during the
onboarding drill, so a symbol passed the gate on a fake gap. The fix was not a better
scan; it was that a command owns the sequence. Done 2026-09-05.

**Agreement you have not measured is a claim.** Nine sessions decided with an ATR one bar
stale, every record read cleanly, and the defect was invisible until something recomputed
the decision independently and compared. The comparison found it in 0.6 seconds on its
first run. Everything the live path does that the replay also does should be compared
this way, and the comparison should run every morning whether or not anyone expects a
difference.

**Gating has to be run to be believed.** The first evening blocked on a missing bar that
was missing because the session was still open. Right answer, and the kind of answer a
draft cannot give.

## What this walk exposes that a list does not

**The blocking constraint is not any of the gaps above.** It is that Track A has still
not produced a frozen candidate. Two holdouts are spent: AAPL passed thinly and its
decision is unfilled; SCHX failed, on a series that turned out to contain an unregistered
split. Nothing is frozen, so paper stages seven and eight cannot honestly begin, and
everything in the evening column - built, gated, and measured at two minutes - is
machinery waiting for a premise that has earned its way through the first gate. The
promotion check makes that visible on every session: `seeded identity ... not the gate's
selection`, nine times a night, until someone freezes something.

**And the premise is sitting on the boundary.** The account-size sweep puts the best
crossing at **USD 10,000** for `aapl-gap-fade-long-next-close`, against a charter that
describes an account *under* USD 10,000.

**Two gaps can only be found in live.** Sizing from settled cash cannot be tested on the
paper account, which is margin with a million dollars in it. Neither can the cash
account's real buying-power behaviour.

## To argue with next time

- Does the execution window want to be a window at all, or a single time? The spread
  evidence says the first five minutes are expensive and the rest of the two hours is not.
- ~~Should the day have two commands instead of eight?~~ **Done 2026-09-05.** Two, and
  the ordering is code.
- ~~Should `run_activation --all` run the morning too?~~ **Answered:** no - the morning
  and the evening are separated by the vendor's publication lag and by the operator's
  sleep, and `day` makes each one command without merging them.
- How does a `next_close` trigger become a live order? The deferral is now visible; the
  execution of it is not designed.
- What is the smallest alerting path that satisfies the playbook - a phone push on a
  handful of conditions is probably enough, and probably an evening's work.
- Does the delayed feed quote before the open? Tuesday answers it.
- When orders are enabled, the sweep becomes its own phase at 00:30 JST; on an early
  close it is 03:00 JST at the latest, not a moment later.
