# Roadmap and state

The central record for this fork: what is built, what is deliberately deferred, and what
the paper run needs. Written to be honest about the difference between "tested" and
"proven in the market".

**The kill chain is the organising frame.** Every open item is anchored to a stage of it,
and the detail sections are ordered by stage, so a reference stays true as the backlog
moves. Read "The kill chain" and "Open work" first; everything under *Detail* is the
working record behind them.

Companion documents, each with a distinct job:

| Document                                 | Job                                                        |
| ---------------------------------------- | ---------------------------------------------------------- |
| [`decisions/`](decisions/README.md)      | Why things are the way they are. Immutable once accepted.  |
| [`MAINTENANCE.md`](MAINTENANCE.md)       | How we draw from upstream, and what we own in the runtime. |
| [`UPSTREAM_DELTA.md`](UPSTREAM_DELTA.md) | Every upstream file we change and what it costs to drop.   |
| [`CHANGELOG.md`](CHANGELOG.md)           | What changed and what was measured.                        |
| [`PAPER_CAMPAIGN.md`](PAPER_CAMPAIGN.md) | Getting operational on paper: the gates, and the log.      |

A rendered view of the same two sections is published at
<https://claude.ai/code/artifact/80882028-e15c-4247-a2f4-e08cf2b2ef20>. This file is the
source of truth; regenerate the page from it rather than the other way round.

## Where this came from

Two projects are being fused:

- **NautilusTrader** - a strong backtest engine, order model, live node and
  reconciliation. Its gaps: no screening, no walk-forward or parameter search, and a
  risk engine limited to per-order notional and rate limits.
- **trade-copilot** - a HITL signal advisor with an institutional-grade validation
  gate and account-wide risk breakers. Its gaps: a crude cost model, a small evidence
  base, and no intraday data.

Each side covers the other's gaps almost exactly. Screening is the only stage neither
covers, and it is pinned.

## The kill chain

The organising frame for everything below: the eleven stages between finding a trade and
banking it, ordered as the trade travels, so a break shows where everything downstream
stalls.

| #   | Stage                | Covered by                    | State as of 2026-09-11                                                                                                 |
| --- | -------------------- | ----------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| 00  | Historical data      | `copilot/data`                | **Ready, with known holes.** 9 registered symbols, 29,740 daily bars to 2026-09-11                                     |
| 01  | Screening / universe | -                             | **Pinned**, out of repo by decision                                                                                    |
| 02  | Research / strategy  | `copilot/strategies`          | **NOT READY.** Gap-fade family rejected; next premise shortlisted, the owner's pick                                    |
| 03  | Backtest engine      | Nautilus `BacktestEngine`     | Ready. Fill, fee and latency models                                                                                    |
| 04  | Validation gate      | `copilot/validation`          | **Ready, and stricter.** Interval, calendar clustering and attribution; the effect size must be predeclared (ADR-0031) |
| 05  | Position sizing      | `copilot/risk/sizing`         | **Settled cash capped on a cash account**, not applicable on margin; binding untested until live                       |
| 06  | Risk limits          | `copilot/risk/protections`    | **Halt proven live; the breaker runs in the basket** with its ledger since 2026-09-11                                  |
| 07  | Orders / exits       | Nautilus execution            | **Exercised; the sweep cancels, confirmed live.** IB's global cancel since 2026-09-11 (ADR-0029)                       |
| 08  | Live deployment      | Nautilus `LiveNode`           | **Packaged for the VM.** One broker session at a time, unit failures alerted (ADR-0030); stand-up 2026-09-15           |
| 09  | Monitoring           | Nautilus analysis + tearsheet | **Alerting wired, kill switch built.** An undelivered CRITICAL halts the host (ADR-0028)                               |
| 10  | Cost calibration     | `copilot/calibration`         | **Strongest stage.** Spread and commission both corroborated against the broker                                        |

**Read the table by where it breaks, not by how much is green.** Ten of eleven stages are
built and seven are proven against a live broker. The one that is not is stage 02, and it is
the one that decides whether any of the rest is worth running: **no premise has an
established edge.** Every number below was refiled on 2026-09-11 under the corrected evidence
and attribution ([ADR-0031](decisions/0031-evidence-and-attribution-after-the-audit.md)), and
none of the readings moved. The AAPL holdout, reassessed through code, returns
`insufficient_evidence` - +0.034 R over 112 trades with a 90% interval of [-0.135, +0.198]. The
nine-symbol pool over the window every member covers is one fold of 137 trades at +0.026 R,
interval [-0.160, +0.172]; the old shifting-membership pool, filed for comparison, passes 20 of
38 folds at +0.031 R per trade and **does not clear zero**, [-0.017, +0.079]. Of the twelve
per-activation walk-forwards, eight pass their majority and one has an interval clearing zero:
AAPL next-close, [+0.010, +0.167], whose spent holdout then could not. EEM, HYG and TLT have
intervals wholly below zero. **And attribution finds no alpha anywhere**
([ADR-0026](decisions/0026-attribution-is-measured-per-trade.md)): no result has positive
four-factor alpha under either treatment of the exit session or under the exposure-weighted
sensitivity; EEM, HYG, SCHX, SPY signal-close and the shifting pool are negative under both
treatments; and the shifting pool's gross +0.068 R per trade is +0.017 cash, +0.053 factor
exposure and -0.002 alpha. The gap-fade family is long the market and paid for it. Nothing is
frozen, so stage 08's supervised readiness has nothing to deploy.

**Corrected 2026-09-10.** Every interval filed before that evening was drawn from the gross
series while its score was net, which is how the pool read as clearing zero and AAPL's interval
as [-0.126, +0.209]. See the changelog.

Three groups of open work, and they gate different things:

- **A candidate worth deploying** - stages 02 and 04. The gap-fade family is rejected
  (2026-09-10). Picking the next premise is now a decision with a shortlist
  ([`DRAFT_NEXT_PREMISE.md`](DRAFT_NEXT_PREMISE.md)), and the random-entry null control it
  needs is ready to build; nothing downstream of research has a candidate until that produces
  one.
- **The paper VM and unattended running** - stages 06, 07, 08 and 09. The nine prep items in
  [`DRAFT_PAPER_VM.md`](DRAFT_PAPER_VM.md) are done: the day fires safely from a timer, the
  evening appends before it warms, alerting is wired and the morning beats, the breaker
  survives a restart, the sweep asks the broker, the kill switch is a host latch, and the VM is
  packaged with a host check. **The 2026-09-11 audit then found the sweep's cancel had not run
  since 2026-09-10 and two ways the alert path failed silently; batch A fixed all three the same
  day**, and in fixing them found the sweep's cancels had never reached another client's order.
  Batch B, what unattended running needs, closed the same day: one broker session at a time,
  unit failures alerted, the acknowledgement check on its own timer, the latch read inside a
  running node, and the protection guard in the basket. IB Gateway headless is the one prep
  item only the VM can close.
- **Real money in a cash account** - stages 05 and 06, two rows. The T+1 rules confirmed with
  the carrying entity, and the margin-or-cash question. Settled-cash sizing is built
  (2026-09-11): the session caps buys at the settled cash IB reports and refuses a cash account
  that reports none. The paper account is MARGIN with USD 1M, and IB sends it no settled figure
  at all, so **paper cannot surface any of this** - which is why the rows stay open however
  well paper goes.

### Stage 02 - the gap fade, and the first real verdict

*Historical, written 2026-09-02.* The verdict below is the first one, over the development window before the net interval, attribution and the 2026-09-10 rejection; the kill chain above is current.

`copilot/strategies/gap_reversal.py` ports trade-copilot's overnight-gap fade (V1-32).
Chosen over the RSI reversal and the trend rule for a **structural** reason recorded in
the original: V1-31 found that a 252-bar training window with a 30-trade eligibility
floor needs a signal on ~12% of trading days, and that ANDing a quality filter onto an
RSI(2) trigger dropped every configuration below it - no evaluable folds, so no verdict
at all. A gap's trigger *is* its quality measure, so there is nothing to AND on and
nothing to dilute, and the original's search values were picked so every one clears the
floor.

**The verdict, net of costs
([ADR-0011](decisions/0011-spread-is-charged-at-p95-from-a-pinned-snapshot.md)) and over
the development window only
([ADR-0012](decisions/0012-the-holdout-is-carved-at-2022-01-01.md)), as of 2026-09-02:**

```bash
python -m copilot.strategies.validate --all --write
```

The premise runs at both bounds of the
[ADR-0013](decisions/0013-entry-timing-is-evaluated-as-a-bracket.md) entry-timing
bracket - `signal_close` is diagnostic only, `next_close` is charter-compliant and the
only spendable mode:

| Symbol | `signal_close` (diagnostic) | `next_close` (spendable)        | Majority   |
| ------ | --------------------------- | ------------------------------- | ---------- |
| AAPL   | 16 / 31, +0.046877 R, 469 t | 20 / 30, **+0.101677 R**, 401 t | pass, both |
| MSFT   | 20 / 31, +0.089455 R, 444 t | 17 / 31, **+0.065973 R**, 375 t | pass, both |
| SPY    | 17 / 30, +0.049848 R, 490 t | 19 / 31, **+0.053016 R**, 439 t | pass, both |

**The bounds did not order the way their assumptions suggested.** Deferring the entry a
full session *raised* AAPL's and SPY's net edge and lowered only MSFT's - so the
reversion this premise captures is not concentrated in session t+1, and the fear that
charter-compliant entry would kill the edge is answered: it does not. Read the AAPL jump
with suspicion rather than excitement: the two modes trade materially different
populations (deferral blocks consecutive-gap re-entries and shifts every subsequent
entry), which is exactly why ADR-0013 forbids comparing verdicts across modes.

The development window is 2005-01-03 to 2021-12-31 (4,280 bars per symbol); the 1,003
bars from 2022-01-03 on are the locked holdout, withheld before the gate sees a bar.
**Every verdict filed before the carve is superseded** - including the full-window net
run of the same morning, where AAPL was majority-fail (19/39). The folds that dragged
AAPL under sit in what is now the holdout, and a verdict that changes when the window
does is exactly why the single-name results stay provisional (qualifier 3). Two earlier
generations of records are likewise not comparable: pre-cost files carry
`costs_modelled: false`, and pre-carve files lack the `holdout` block.

Every run files a record under `copilot/strategies/verdicts/` carrying the activation, the
search space as declared at the time, the seeded parameters, the fold geometry and the
exact cost basis (snapshot, percentile, coefficient), so a number can be tied to an
experiment rather than to a memory of one.

**Still not a green light.** Three qualifiers remain:

1. **`holdout_spent: false`.** This is walk-forward, which is repeatable. The holdout
   now exists - carved at 2022-01-01
   ([ADR-0012](decisions/0012-the-holdout-is-carved-at-2022-01-01.md)), so the flag is
   finally backed by a real reservation - but it has never been spent, and spending it
   is a deliberate separate act: `spend_holdout` exists ([ADR-0014]) and has not been
   run. The entry-timing gate on the spend is resolved (ADR-0013): the
   spend, when chosen, goes to `spy-gap-fade-long-next-close` and to no `signal_close`
   activation ever.
2. **Neither bound models the charter's actual execution rule** - a limit inside the
   first hours of session t+1 lies between `signal_close` and `next_close`, and neither
   bound's verdict is comparable with the original's next-open entry. The cost model's
   spread is also sampled mid-session, not at the entry moment, which is one of the
   reasons [ADR-0011] chose p95. Superseding the bracket by measuring the real window
   is the intraday-data trigger named in ADR-0013.
3. **SPY is the lead candidate; the single-name verdicts are provisional.** The charter's
   instrument default is *"liquid US-listed ETFs first; large caps only once the pipeline
   handles point-in-time universes and corporate actions"* - and the pipeline handles
   neither yet: the universe is survivor-chosen (the open conflict below) and corporate
   actions are a hand-maintained AAPL splits table in the cost model. SPY leans on none
   of that machinery - no splits in the window, no membership question - so its net pass
   is the one whose evidence is whole. MSFT's pass and AAPL's fail both sit ahead of the
   pipeline maturity the charter requires for single names.

Trade counts land at 21-29 per 252 bars against the original's 30-37, because this port
holds one position at a time and a run of gap days therefore blocks its own re-entries.

The overlay suite is `PYTHONPATH=. pytest copilot/tests/ -q`; 1,038 tests passed on
2026-09-11, peak resident memory 0.51 GiB.

### Stage 08 - what a paper run actually needs

*Historical, written 2026-09-01.* A paper node, the shakedown and the whole operator day have been built and run since; the paper campaign log and the kill chain above are current.

**No paper node exists.** The only IB connection code in the overlay is
`calibration/spread_snapshot.py`, which builds a **data-only** `LiveNode` - one data client,
no execution client, no account, no strategy. Everything from stage 00 to 07 is research
plumbing that has never had a broker on the other end of it.

That matters less than it sounds, because [`playbook/OPERATIONS.md`](playbook/OPERATIONS.md)
splits the work in two and only one half is blocked:

> Broker-integration testing and strategy forward testing are different activities.
> Controlled connectivity, read-only reconciliation and minimum-size order-lifecycle tests
> may begin before a strategy passes the research gate, and they validate no edge whatever.

**Paper stages 1 through 6 are integration testing.** They need a paper account, delayed
quotes and a connection. They do **not** need a market-data subscription, settled cash,
margin, a chosen spread coefficient, or a strategy anyone believes in - they answer "does
the machine behave", and the answer is currently unknown.

**Paper stages 7 and 8 are forward testing.** They need a frozen candidate that passed the
research gate. We do not have one: the gap fade is negative at the target account size
([ADR-0009](decisions/0009-cost-is-modelled-at-the-target-account-size.md)) and no holdout
has been carved out. Forward-testing it would measure a premise already known to lose.

The campaign, its gates and its evidence log live in [`PAPER_CAMPAIGN.md`](PAPER_CAMPAIGN.md).

What stages 1 to 6 need built, none of it blocked:

| Piece                           | State                                                                                                                                                                            |
| ------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Execution client wiring         | **Built**, `live/node.py`. First execution client in the overlay.                                                                                                                |
| A paper node builder            | **Built**, `live/session.py`. Paper and live differ by a port number on this deployment shape, so two independent checks must agree before a session is called paper. 17 tests.  |
| An orders-disabled mode         | **Built.** Strategies run normally and the risk engine is halted, so the real path is exercised and every order is denied inside the engine rather than never submitted.         |
| Guard handle taken before start | **Built.** `build_paper_node` returns it rather than leaving the caller to find it.                                                                                              |
| A preflight check script        | **Built, run, passing.** Stages 1 and 2 both pass against the paper account.                                                                                                     |
| Map catalog ids to broker ids   | **Built**, `live/symbology.py`. Research `AAPL.XNAS` to broker `AAPL=STK.SMART`, with the routing table listed rather than defaulted so an unmapped venue raises.                |
| Failure injection               | **Built, run, passing** on the second run, `live/probes/failure_injection.py`. Two cases are excluded and named rather than scored: one paper cannot decide, one is unconfirmed. |

## Open work, grouped by what unblocks it

Twenty items. Grouped by blocking condition rather than by component, because that is
the axis that decides what can move today. A final group records the standing carrying
cost of the upstream changes this fork already holds - not work, but the bill that
arrives at every sync.

**The grooming rule:** anything surfaced as an action - in a session, a review, or a
detail table below - gets a row in one of these groups before the session that surfaced
it ends. Three items sat outside the count because they lived only in a status table, a
conversation, and a test log; the count exists so that cannot happen quietly.

**Corrected 2026-09-09: the count had drifted one below the sum of its groups.** A
checksum that is wrong is worse than no checksum, because it is read as agreement. The
header is now the arithmetic sum of the five groups below it, and the operator kill
command - a GAP named in the operator-day draft's evening table since 2026-09-04 and
never given a row - is one of the two items that were outside it.

### Waiting on the account (1)

**Decided 2026-09-11: the account runs cash, and moves to margin when margin is available.**
So the short leg stays unavailable, `Not yet` in the charter keeps shorts out, and the
settled-cash cap built on 2026-09-11 is the one that will bind in the live account rather than
a term held for later. The paper account is margin and cannot exercise it; the row below is
what would tell us how it behaves.

Recorded 2026-09-01. **The operator's to close, not the repository's.** Two items in the
groups below inherit their block, which is why they sit first.

**Closed 2026-09-09: the market-data equity minimum.** The owner subscribed the live
username to NYSE (Network A/CTA), Network B and NASDAQ (Network C/UTP), USD 1.50 each a
month, and the paper account borrows them. Measured the same hour from the second
machine: `preflight` 15/15 after the close, historical bars for AAPL and SPY under both
`REALTIME` and `DELAYED` where every request had returned 2188, and `spread_snapshot`
recording 47/61/139 quotes under `REALTIME` against a 35/64/120 delayed control. Detail
under stage 10 below; the campaign log has the rows.

| Item                                                              | Stage | The action                                                                                                                                                                                 |
| ----------------------------------------------------------------- | ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Confirm settlement and buying-power rules on the real account** | 06    | T+1 is the general US rule, but PREFLIGHT requires it verified with the carrying entity rather than assumed. Decides whether a settled-cash check has to sit in front of order submission. |

**Fixed and verified live 2026-09-03: the adopted-order cancel path.** The two cancel
paths had diverged - single-order cancel routed an order's identity into the adapter's ID
maps before emitting, cancel-all did not - so an order adopted from the venue had no
route and the emission died with "Instrument ID not found for pending cancel order".
Cancel-all now carries the identity out of the cache and routes it before the cancel, and
reports a failed cancel as `OrderCancelRejected`. Confirmed against IB by re-running
`live/probes/strand_recovery.py` on a rebuilt extension: that error is gone and the adopted order
reaches `PENDING_CANCEL`. Two things were learned that no unit test could decide. **A
cancel takes effect from the originating client id and not from a foreign one**, and IB
sends no acknowledgement to a client that did not place the order - so the probe records
FAIL by its event-based test while the broker ends up clean, and a recovery sweep should
use the originating client id when it is known. The re-run also surfaced a third defect of
the same family, on the open-order update path, closed below. Evidence:
`live/out/strand_recovery_20260903T08{2058,2836}Z.json`.

**Sharpened 2026-09-10, and one half of it corrected.** The order stranded by the
shakedown was followed afterwards rather than abandoned, which had never been done. It
was cancelled, and it was cancelled after a cancel from the *originating* client id
produced no acknowledgement either - so "no acknowledgement to a foreign client" is too
narrow. **No acknowledgement arrives for an adopted order at all**, whichever client
sends the cancel, and the cancel still takes effect. That makes the event-based verdict
unfixable by waiting: `cancel_working` can report `CACHE CLEAR` only because it saw no
events, which is the same thing it sees when nothing happened. The deadline half is fixed
(below); the verdict half needs the broker asked again, which is a row.

**Fixed 2026-09-03: an open-order update for an order this client never placed.** TWS
pushes `openOrder` for every working order on the account at connect, before
reconciliation runs, so a previous run's order logged `Trader ID not found` at ERROR every
time - a startup race reported as a failure, and the same `shutdown_on_error` landmine as
the reduce-only noise. Those orders have no route and nothing to attribute an update to;
reconciliation adopts them moments later with identity it builds itself, so the update is
now skipped at debug. The rebuilt extension logs **zero ERROR lines** across a full
strand, recover and sweep cycle.

**Fixed 2026-09-03: the reduce-only startup errors are reclassified, not muted.**
A reduce-only fill with no position to reduce is two different events. Live, it is a real
defect and stays `ERROR`. Replayed from the venue during startup reconciliation it is the
ordinary shape of a lookback holding a completed round trip - the exit fill is reported,
its position closed long ago and is not in the cache - and it now logs `WARN` saying so.
Reconciliation fills are already flagged at every constructor, so the two cases are
distinguishable without guessing, and the rejection itself is unchanged. `shutdown_on_error`
is no longer blocked by this class of noise. One test drives an orphan fill both ways and
was verified to fail on the unfixed engine; registered in the delta.

### Waiting on a decision (0)

**Decided 2026-09-12 as [ADR-0033](decisions/0033-the-turn-of-month-single-use-test-is-forward.md):
the turn of the month's single-use test is forward, not carved.** The owner concurred with the
recommendation below - re-pin the boundary earlier and keep a real single-use test - and
executing it showed the recommendation was not available. The band admits boundaries only from
2021-11-01 (19.80% of the window) to 2022-11-01 (15.03%), and every holdout any of them carves
lies wholly inside 2005-2025, which is the span the void run read; moving the pin earlier adds
bars that were also in the aggregate. So the carved holdout is **compromised rather than spent**

- nothing was decided from it, and it may never be quoted as a clean out-of-sample pass - the
boundary stays at the shared pin, and the single-use test becomes the forward span the void run
provably never touched: 174 catalog bars past 2026-01-01 and the paper clock from 2026-09-15.
`spend_holdout` refuses the activation by name, because today's ADR-0013 refusal is accidental
and would vanish the day the premise is re-expressed on intraday bars. The reasoning that was
recommended, and the arithmetic that overruled it, are kept below.

**Opened 2026-09-11: is `spy-turn-of-month`'s holdout spent?** The randomised-signal control's
first run carved on the activation's own boundary, which this activation does not declare, so the
code read *no boundary of its own* as *nothing withheld* and ran over 2005-2025 - the locked
holdout included. What was seen is an aggregate, not per-trade detail: 251 trades at +0.1080 R
against a null of +0.0381, p 0.0838. The record is deleted, the reading is void, the runner now
carves through the gate's own call and a test pins it
([EXP-2026-001](../strategies/experiments/EXP-2026-001-turn-of-month.md) records the incident).
**The decision is what the holdout now is.** Two honest options: treat it as consumed, on
[ADR-0021](decisions/0021-an-unscorable-spend-still-consumes-the-holdout.md)'s reasoning that a
look is a look; or re-pin this activation's boundary earlier and carry 2022-2025 as development
data for it, which costs history but keeps a real single-use test. **Recommendation:** re-pin,
because the premise's own interval says it needs *more* evidence, and a consumed holdout on a
premise that was never going to be spendable at 144 trades buys nothing. Until it is decided, the
holdout must not be spent.

**All three earlier decisions were taken on 2026-09-11**, the owner concurring with each
recommendation.

**The charter's mode table now carries the integration-testing carve-out.** Its two paper rows
read *after research gates, or integration testing before them* and *after supervised
stability, integration testing included*, with the playbook's wording quoted beneath. The VM
plan is what the carve-out was always for: the system clock, orders denied, nothing frozen.

**A triggered next-close entry is a DAY marketable limit at the decision close plus a declared
concession, cancelled by 10:30 Eastern**
([ADR-0032](decisions/0032-a-next-close-trigger-is-placed-as-a-marketable-day-limit.md)). Stop
and target come from the fill and the frozen ATR; an unfilled trigger is not carried; the
verdict stays the `next_close` bound until the premise is re-expressed on intraday bars.
Building it is stage-seven work that a frozen candidate gates, so it sits under *Deferred by
decision* with that trigger.

**The next premise is the turn of the month on SPY, after the null control is built.** The
null control is the ready row above it; the experiment card and the premise follow it, and the
effect size is declared with the card. [`DRAFT_NEXT_PREMISE.md`](DRAFT_NEXT_PREMISE.md) holds
the reasoning, the shortlist, and the one discovery look taken so far.

| Item                                                                 | Stage  | The question                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| -------------------------------------------------------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| *All three closed 2026-09-11; the rows are kept for their reasoning* | -      | -                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| **The charter's mode table and the pre-candidate VM**                | 08     | **Decided 2026-09-11: the carve-out is in the table.** Surfaced by the 2026-09-11 audit. [`CHARTER.md`](CHARTER.md)'s mode table admits supervised paper *after research gates*, and the VM plan stands up supervised and then unattended paper with no candidate past a gate, under the playbook's integration-testing carve-out (`OPERATIONS.md`: broker-integration testing may begin before a strategy passes the research gate). `AGENTS.md` makes the charter outrank the playbook, and the table carries no exception. Add the carve-out to the table, or cite it there. The charter is the owner's to edit.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| **Pick the next premise, and build its null control first**          | 02, 04 | **Decided 2026-09-11: the null control, then the turn of the month on SPY.** Moved here from *Ready to build* 2026-09-11, because it is a choice with a recommendation behind it: [`DRAFT_NEXT_PREMISE.md`](DRAFT_NEXT_PREMISE.md). The gap-fade family is rejected, so Track A is empty. Two findings shape the choice. **Per-trade four-factor attribution cannot credit timing**: on a broad index ETF a trade's R is close to its leverage times the market over its own window, so the fit returns a market loading near one and alpha near zero however well the entries were chosen - and every long-only premise on the onboarded ETFs is a timing premise. **The playbook's randomised-signal control is not built**, and it is the test that can judge timing. **Recommendation:** build the random-entry null control first (the row under *Ready to build*), then write the experiment card for the turn of the month on SPY - an institutional flow mechanism, an entry whose price does not decide it, and the lowest turnover on the shortlist. Reject overnight holding on cost arithmetic (2.75 bps a night against 7 bps of Tiered minimums at USD 1,000). About 200 trades cannot show an edge below roughly 0.1 R per trade; the one discovery look taken so far, 8.7 bps over an average four-session window, has t = 0.61. The owner picks, and declares the effect size with the card. |
| **How a triggered next-close entry is placed in its session**        | 07, 08 | **Decided 2026-09-11 as ADR-0032.** Moved here from *Ready to build* by batch B, 2026-09-11, because building it means choosing it. The rule decides on bar *t* and the replay fills at *t+1*'s **close**; the charter's execution rule is a time-bounded DAY limit or marketable limit **in the first one to two hours** of *t+1*, with a declared maximum concession, and the bracket's levels need a price the fill has not yet given. Today the evening records the trigger (`deferred_atr`) and the replay comparison checks it; no entry is carried. **Recommendation:** a DAY marketable limit at the decision close plus a declared concession, submitted at the open and cancelled by 10:30 Eastern if unfilled, with stop and target placed from the fill price and the frozen ATR - and, before orders are enabled, the replay re-expressed on Databento intraday bars in that same window, which is ADR-0013's own revisit trigger. Gated on a frozen candidate either way.                                                                                                                                                                                                                                                                                                                                                                                                                       |

**Resolved 2026-09-10: the gap-fade family is rejected as a source of edge.** Attribution
found no four-factor alpha above zero in any walk-forward or the pool, under either
treatment of the exit session ([ADR-0026](decisions/0026-attribution-is-measured-per-trade.md)).
Four rows that refined the family close with it - revising the AAPL next-close premise,
pooling over a constant membership, respending the SCHX holdout, and building the pooled
spend now - recorded in the changelog. **The pooled holdout stays unspent**, with AAPL
excluded from any future pool. **The assisted-decision layer's second tier stays deferred**;
both move to *Deferred by decision*. One consequence is recorded rather than decided: the
market-neutral revision offered alongside the rejection needs a short leg, and the charter's
operating model is long only with shorts under *Not yet*. It can be run as research into
whether the gap signal carries information, and cannot become a tradable candidate without
a charter change.

**Resolved 2026-09-05, all five, in one sitting.** The AAPL next-close holdout is
**revised**, not frozen: a premise on paper whose edge the account cannot fund proves
nothing (`holdouts/aapl-gap-fade-long-next-close.json`, `owner_decision_reason`). The SCHX
spend is **void** - it scored a series that did not exist - and the holdout may be spent
once more on the corrected series
([ADR-0021](decisions/0021-an-unscorable-spend-still-consumes-the-holdout.md)). An
**unscorable spend on a true series consumes the holdout** (the same ADR). Marketstack
**stays until EODHD passes the same probes** that caught it; cancel nothing first. The
consolidated US equity data purchase is **skipped**: Databento covers intraday from 2018
for cents and the delayed feed passed the quote check nine for nine. The three that were
work moved to *Ready to build*.

**Resolved 2026-09-03: the Databento account.** The key is in `trade-copilot/.env`, the portal cap is USD 100/month warning at 90%, and **$19.65 of $125 credits is spent** - the intraday pull, the catalog audit and the 2026 repair. Row retired 2026-09-04.

**Resolved 2026-09-02:** the two items that needed a live session are settled - realtime
quotes are still not entitled, and the sibling-subscription stall does not reproduce. Both
are written up under stage 00 and stage 10 below. `spread_snapshot.build_node` runs. `LiveNode.add_actor` is
now exposed to Python, so the calibrator records quotes from committed code and its
snapshots are reproducible from a commit for the first time.

**Resolved 2026-09-01:** which setup ports first (gap fade, chosen on V1-31 evidence), and
that upstream files may be changed. `set_trading_state` moves to
ready-to-build below. The condition attached to the clearance is that every upstream file
this fork touches is tracked, which is what `docs/UPSTREAM_DELTA.md` and
`tools/upstream_delta.py` now do.

### Charter conflicts, opened 2026-09-01 (1)

Adopting [`CHARTER.md`](CHARTER.md) surfaced four places where the code does not match the
process it is now governed by. Three are resolved -
[ADR-0009](decisions/0009-cost-is-modelled-at-the-target-account-size.md), the holdout
carve ([ADR-0012](decisions/0012-the-holdout-is-carved-at-2022-01-01.md): pinned at
2022-01-01, 18.99% reserved, unspent), and as of 2026-09-02 entry timing
([ADR-0013](decisions/0013-entry-timing-is-evaluated-as-a-bracket.md): next-open entry is
not expressible on the daily-bar replay, so the premise runs at both bounds that are, and
**only a `next_close` activation may spend a holdout**). The entry-timing resolution
un-gates the holdout spend; the bracket verdict is under stage 02 below.

| Item                                 | Stage | Notes                                                                                                                                                                                                                                                                                      |
| ------------------------------------ | ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Correct the survivor-biased universe | 00    | The 20-symbol catalog is today's large caps backfilled to 2005, which the charter names as the error to avoid. Needs point-in-time membership and delisted securities, which [ADR-0015](decisions/0015-databento-is-the-intraday-source-only.md) prices at Norgate Platinum, USD 630/year. |

### Waiting on spend (1)

No code closes this.

**Pinned 2026-09-11: no EODHD key for now.** The probe is built and its AAPL reading is filed;
Marketstack stays the daily source and nothing is cancelled. The row moves to *Deferred by
decision* with the trigger that would reopen it.

**Closed 2026-09-09: US equity history through IB.** The three Network subscriptions
under *Waiting on the account* were the spend; `entitlements.py` returned bars for every
US equity shape that had returned 2188. IB history is not adopted as a source - Databento
holds intraday ([ADR-0015](decisions/0015-databento-is-the-intraday-source-only.md)) and
Marketstack the daily series - but the wall is gone, and the calibrator can cross-check a
Databento-derived coefficient against the broker's own tape.

| Item                           | Stage | Notes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| ------------------------------ | ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Point-in-time index membership | 00    | Norgate Platinum, USD 630/year, the only verified source of true daily membership for the S&P 500 and Russell 3000 including delisted securities. Deferred until the universe correction starts, not rejected ([ADR-0015](decisions/0015-databento-is-the-intraday-source-only.md)).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| *Moved to Deferred 2026-09-11* | 00    | Owner decision 2026-09-05: probe EODHD, cancel nothing, adopt only on a pass. The probe is built (`python -m copilot.data.eodhd_probe`) and ran 2026-09-11 on the public demo token, which serves AAPL and refuses every other US ticker (`data/out/eodhd_probe_20260911T211118Z.json`). **AAPL passes four checks and fails one**: no incoherent bars, no phantom or missing sessions in 5,457, a jump on each of the three registered splits and nowhere else, and 2,093 of 2,101 closes exactly equal to Databento's official print with none over 10 bps - but its 2005 to 2014-06-06 closes are not whole cents, an adjusted series multiplied back up, the same shape as Marketstack's AAPL. It also agrees with the Marketstack catalog to a basis point everywhere, so the two likely share an upstream. Breadth needs a paid key, about USD 30 a month (ADR-0015); one month covers the probe. |

### Waiting on the VM (5)

Opened 2026-09-11 by audit batch D. The paper VM stands up on 2026-09-15
([`DRAFT_PAPER_VM.md`](DRAFT_PAPER_VM.md), [`../ops/README.md`](../ops/README.md)), and each of
these can be settled only on it: two were prep rows that need the host, and three are the
questions the stand-up plan defers to its stages, each now a row of its own.

| Item                                                                            | Stage | Settle at                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| ------------------------------------------------------------------------------- | ----- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Run IB Gateway headless on the VM**                                           | 08    | Never yet run against Gateway; every session so far went through TWS on the Windows host. ADR-0006 names the IBC-based `gnzsnz/ib-gateway` image. Known traps to carry in: the adapter's container settings include `EXISTING_SESSION_DETECTED_ACTION=primary`, so the VM's login displaces TWS on the dev box; paper is served on the container's 4004, mapped to the host's 4002; its `read_only_api` defaults to true, which blocks the execution client with IB 321; the paper port is 4002 (`GATEWAY_PAPER_PORT`), and `session.py`'s two-check paper guard has to agree with it; the daily restart is the normal case, so the first week is a restart drill; and the `IBAPI_TIMEZONE_ALIASES` workaround exists for a JST-configured TWS and has to be re-measured on an Eastern-configured Gateway. **One session per login**: once the VM streams, the dev box reaches the broker through the VM's Gateway - distinct client ids over an SSH tunnel - rather than a second login. |
| **Get the VM's records back into the repository**                               | 09    | Session records, verdicts and comparisons are written into the working tree and committed, and on an unattended host nobody commits them - a VM lost is a campaign's evidence lost. To settle at stand-up. Recommendation: a deploy key scoped to this repository and a daily push of records to a branch merged by pull request, so evidence survives the host and nothing reaches `develop` unreviewed.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| **Where the heartbeat is watched**                                              | 09    | Stand-up stage three. Recommendation: the owner's cluster monitoring, as an observer of the VM. Required for unattended running: while an automatic halt latch holds, the missed beat is the only alarm that does not depend on Pushover ([ADR-0028](decisions/0028-an-undelivered-critical-halts-the-host.md)). Record the watcher and its alert route here.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| **Whether `IBAPI_TIMEZONE_ALIASES` is still needed against an Eastern Gateway** | 08    | Stand-up stage four. `day` refuses without it; the alias exists for a TWS configured in Japan and the Gateway container runs in `America/New_York`. Connect once without it and once with it; relax the check only on that evidence, and record both results here.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| **Whether IBC's paper login prompts for two-factor**                            | 08    | Stand-up stage four. If it prompts, the Gateway's nightly restart needs the owner present and unattended running is blocked on it; if not, record that it did not and when the check was made.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |

### Ready to build (3)

**Closed 2026-09-11: the random-entry null control is built.** `copilot.validation.null_control`
draws entry sessions at random from the development window, runs the premise's own machinery on
each draw, and reports where the premise's mean sits in that distribution: a one-sided p at or
below 0.10 reads as beating chance, fewer than a hundred replicates is refused, and more than one
replicate in twenty scoring nothing withholds the reading. The playbook's baselines section now
names it. Running it against a premise is the row below.

**Corrected 2026-09-11: this header read one above its rows from #91 to #96.** #91 took two
rows out of this group and the header down by one; the total above stayed right, so the sum of
the groups caught it. The same checksum rule as 2026-09-09, and the same lesson.

Twenty-two rows closed on 2026-09-03, 2026-09-04 and 2026-09-05 moved to [`CHANGELOG.md`](CHANGELOG.md);
this table holds open work only, and its count is the checksum. **Thirty-three rows were
filed by the 2026-09-11 audit** of the work since 2026-09-06, and
[`AUDIT_2026-09-11.md`](AUDIT_2026-09-11.md) holds the evidence for each and the plan: batch A
(four rows) before the VM's supervised day, B before unattended running, C before the next
premise's holdout, D hygiene. **Batch A closed 2026-09-11** (the changelog has it, and it found
that the sweep's cancels could never reach another client's order, now fixed with IB's global
cancel, [ADR-0029](decisions/0029-the-sweep-cancels-with-the-global-cancel.md)). The rows below
hold what the audit did not reach. **Batch D closed 2026-09-11**: the account id out of docstrings,
calibration retention, the dated detail sections, and the stand-up questions as rows under *Waiting
on the VM*. **Batch C closed 2026-09-11** too, all fifteen rows
([ADR-0031](decisions/0031-evidence-and-attribution-after-the-audit.md)). **Batch B closed 2026-09-11** the same way: eleven rows built, and the
next-close carry moved to *Waiting on a decision*, because building it means choosing how an
entry is placed ([ADR-0030](decisions/0030-the-host-runs-one-broker-session-at-a-time.md)).

**Closed 2026-09-12: sizing carries the stressed gap allowance.** `g` is measured per symbol by
`calibration/gap_history.py` over **development bars only**, pinned in `risk/gap_stress.py`, and
declared by every activation; `size_from_levels` divides by `abs(P - S) + g` and records the
stressed per-share loss as the trade's risk. The measured allowances run from 1.07 ATR on XLF to
1.89 on EEM, and **all but two exceed the 1.5-ATR stop the registry uses** - a position stopped
1.5 ATR away can lose roughly twice that overnight, so the old sizing understated planned risk by
more than half. Two consequences are deliberate: an ordinary stop-out now costs a **fraction** of
one R, because one R is the stressed loss and a clean stop-out is not that case; and a strategy
whose activation declares no allowance **refuses to trade** rather than silently reverting to the
stop distance. Every filed verdict is now in the wrong unit and must be recomputed - the row for
that sits below with the Tiered switch, because both change every R and one re-file should carry
both.

The fourteen rows carried from before the audit:

| Item                                                  | Stage | Notes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| ----------------------------------------------------- | ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Decide what the turn of the month gets next**       | 02    | Built, scored and controlled on 2026-09-11: [EXP-2026-001](../strategies/experiments/EXP-2026-001-turn-of-month.md). Development window only. **The walk-forward passes its majority** (9 of 12 folds, +0.100541 R per trade, 144 trades) and **the null control passes** (+0.137807 R over 203 trades at the seeded parameters against a null mean of +0.037457 over 500 draws; percentile 96.0, one-sided p 0.0419), so the entry dates are doing something. **The net evidence interval does not clear zero** - [-0.003868, +0.194246] on 111.7 effective trades - which is the gate ADR-0024 exists for and the one the card predicted would bind at this sample size. So the premise is **neither rejected nor advanced**, and the playbook's four options are the whole menu: extend the history, forward test for longer, simplify the claim, or reject. Not the holdout: that is a separate decision, below. Concentration, doubled costs and the account sweep are the gates still unmeasured. |
| **Iterate the operator-day draft**                    | 08    | [`DRAFT_OPERATOR_DAY.md`](DRAFT_OPERATOR_DAY.md) walks the JST clock from the close through the execution window to the next morning. Five passes, four of them **run** against the real catalog and a live TWS. The fifth, 2026-09-11, ran the morning (2m40s) and Monday's evening (2m20s, 6 of 6) on the VM's code: the comparison found the broker's daily bar reaching every strategy before its warm-up, a second decision path opened by the 2026-09-09 subscriptions, fixed the same evening and then 9 of 9 agree. Kept open as a living draft; its open questions are listed at the end of it.                                                                                                                                                                                                                                                                                                                                                                                                |
| **Switch the account to Tiered in the Client Portal** | 02    | Owner decision 2026-09-10: switch. The model is built and the revalidation is done - ADR-0025, `calibration/out/commission_revalidation_20260910.json`, 0 of 12 verdicts change their majority under Tiered. What remains is the owner's Client Portal action and confirming its effective date. Once the account is actually on Tiered, `SCHEDULE` moves to `TIERED` in a commit of its own and the verdicts are recomputed. Until then the pin stays on Fixed, because a verdict priced on a plan the broker is not running describes a different account.                                                                                                                                                                                                                                                                                                                                                                                                                                            |

### Deferred by decision (9)

**Build the next-close entry placement.** *Decided 2026-09-11 as
[ADR-0032](decisions/0032-a-next-close-trigger-is-placed-as-a-marketable-day-limit.md); the
building waits.* The placement is chosen - a DAY marketable limit at the decision close plus a
declared concession, cancelled by 10:30 Eastern, the bracket from the fill and the frozen ATR -
and writing it is stage-seven work that needs a frozen candidate to be worth having. The
recommended next premise enters at a scheduled close and does not use this path. Revisit when a
candidate is frozen, or sooner if a `next_close` premise reaches its holdout.

**An EODHD key, pinned as is.** *Owner decision 2026-09-11.* The probe is built and ran on the
public demo token; Marketstack stays the daily source and nothing is cancelled. Breadth over the
other eight symbols needs a paid key, about USD 30 for the one month the probe needs. Revisit if
Marketstack fails another check, or when the universe correction makes a second daily source
worth its own budget line.

**Rank signals within the session cap.** *Recommended 2026-09-11; the owner's to overrule.* The
ledger grants in the order bars are handed out, and the row waited on the refusals each session
record carries. There are none to rank: fourteen basket records to 2026-09-11, **no refusal in
any**, and triggers in one session only, two deferrals against a cap of two entries. Ranking
signals from no evidence would be an arbitrary order with a rationale attached. Revisit at the
first refusal in a session record, or when a candidate with more than two correlated
activations reaches paper stage seven.

**Leave GLDM's thirteen 2018-2019 holes open.** *Recommended 2026-09-11; the owner's to
overrule.* The row assumed the Databento store would fill them once it covered them, and it
already did: ARCX's venue bar is in the store for all thirteen, with 13,466 to 1,018,342
shares traded, and **no closing-auction statistic on any of them**. GLDM was months old and
thin, and in 2018 its listing venue ran a closing auction on about three sessions in four. No
official close exists for those days, so no pull fills them under ADR-0018's rule, and
`patch` now reports them as *no auction* rather than blaming the store's start date. Filling
them means a new close rule - the venue's last print, which ran 3.5 to 11.4 bps from the
official close at the median and 459 bps at worst - on an instrument whose premise is
rejected. EEM, HYG and SCHX's 2017-03-20 hole predates the store and is unchanged. Revisit if
GLDM, or any thin ETF's early history, carries a candidate.

**Re-measure the access fee cap on 2027-11-01.** ADR-0025's revisit trigger, and the date the
row itself said would move. Reg NMS Rule 610(c)'s cap falls from 0.003 to 0.001 per share on
its compliance date, which the SEC deferred on 2026-06-11, by exemptive order, from
2026-11-02 to the first business day of November 2027. If it takes effect, Tiered's all-in
rate drops to about 0.0047 against Fixed's flat 0.005 and **the crossover disappears**:
Tiered becomes cheaper at every size. Until then `TIERED` charges 0.003 and ADR-0025 stands.
Revisit on 2027-11-01, or sooner if the order is withdrawn.

**Build the pooled holdout spend.** Owner decision 2026-09-10: AAPL is excluded from any
pooled holdout, and the spend is not built for the gap-fade family, whose pool has no alpha
([ADR-0026](decisions/0026-attribution-is-measured-per-trade.md)). The pooled window is the
largest single-use evidence left. Revisit when a premise's pooled walk-forward shows alpha
under both exit-session treatments.

**The assisted-decision layer's second tier.** Owner decision 2026-09-10: deferred. Both
conditions in [`DRAFT_ASSISTED_DECISIONS.md`](DRAFT_ASSISTED_DECISIONS.md) still fail - the
holdout cannot test a model trained across it, and a USD 30 per month layer costs 0.101 R
per trade at this account. Revisit when a premise clears its interval, which is also when a
forward test could evaluate a model.

**Groom CI for ownership.** Actions is disabled as of 2026-09-02 pending a deliberate
pass over the inherited workflows. The reason CI has never run is diagnosed and recorded
in [`MAINTENANCE.md`](MAINTENANCE.md) under "CI, and why it has never run", so the pass
starts warm: the harden-runner egress allowlist lives in repository variables that did
not travel with the copy, and the workflows still carry upstream's wheel-publication and
release machinery, none of which applies here.

**Tabletop: subscriptions, operations, strategy.** Under way. Operations and strategy
governance are settled and recorded in [`CHARTER.md`](CHARTER.md), the
[playbook](playbook/README.md) and the ADRs. **Subscriptions closed 2026-09-09**: the three
consolidated US equity feeds are bought (see *Waiting on the account*), and the earlier
decision to skip a separate consolidated-data purchase stands - Databento remains the
intraday source ([ADR-0015](decisions/0015-databento-is-the-intraday-source-only.md)). The spread coefficient was called on 2026-09-02
([ADR-0011](decisions/0011-spread-is-charged-at-p95-from-a-pinned-snapshot.md)).

### Carrying cost, tracked (39 files)

Not work items - the standing bill. Reported by `python -m copilot.tools.upstream_delta`,
with the reasoning for each in `docs/UPSTREAM_DELTA.md`, which is the register and the only
list kept by hand. A table here went stale at nine files while the register grew to
thirty-six, so the numbers now come from the tool: after the audit's batches on 2026-09-11 it
reported **39 files changed outside `copilot/`** against the 2026-08-31 merge base - the three
added that day are the IB adapter's global-cancel option, its Python binding and the generated
stub (ADR-0029) - and **a forecast of 8 conflicting files**: the README, `AGENTS.md`,
`CONTRIBUTING.md`, an issue template, the IB adapter's data and execution cores, the live node's
Python binding and the risk engine's tests. Twenty of the thirty-nine are the retired
contribution scaffolding (templates, policies, the code of conduct); the code deltas are the IB
adapter fixes and its global cancel, the execution and risk engine fixes, and the
`set_trading_state` binding.

**Nothing is due.** Syncing is on demand only - the fork is deliberately held still while
development is active, so every conflict above is a forecast for a sync that has not been
scheduled. Upstreaming the IB fixes and the `RiskEngine` binding would retire entries rather
than carry them, and they are additive capability or straight bug fixes upstream would
plausibly accept - but that opens a review front on someone else's schedule, so it is
deferred on the same reasoning.

## Shortest route to a paper run

**Correction to the prerequisites listed further down this document.** They named a
multi-symbol spread calibration and a ported validation gate. Both have landed, so on
paper the paper run is unblocked. That list was incomplete: it never named a strategy,
and without one there is nothing to validate or deploy.

1. ~~**Pick a setup** and port it~~ - done, the gap fade.
2. ~~**Set the cost coefficient** and wire it per instrument~~ - done 2026-09-02,
   [ADR-0011](decisions/0011-spread-is-charged-at-p95-from-a-pinned-snapshot.md): the gate
   scores net, and AAPL's majority flipped to fail the day the placeholder died.
3. ~~**Carve the locked holdout**, then run the gate for real~~ - done 2026-09-02,
   [ADR-0012](decisions/0012-the-holdout-is-carved-at-2022-01-01.md): pinned at
   2022-01-01, and all three activations re-validated over the development window alone.
   All three majority-pass net.
4. ~~**Move entry to the next session, then re-validate**~~ - done 2026-09-02,
   [ADR-0013](decisions/0013-entry-timing-is-evaluated-as-a-bracket.md): next-open entry
   is not expressible on the daily-bar replay, so the premise runs at both expressible
   bounds. All six activations majority-pass net; the bracket table is under stage 02.
5. ~~**Spend the holdout**~~ - spent 2026-09-04 on `aapl-gap-fade-long-next-close`, not
   SPY, and read as `insufficient_evidence` under ADR-0024; the owner revised rather than
   froze. The SCHX spend was voided (ADR-0021). Then attribution found no alpha anywhere
   and **the family was rejected on 2026-09-10**, so this route ends here until the next
   premise (the *Pick the next premise* row) reaches its own holdout.
6. **Two to four weeks on IB paper** with the guard enabled, for a candidate that
   survives step 5. This is the first time the breakers can fire; they cannot fire in a
   backtest by design. Waiting on a candidate; the VM runs the system clock meanwhile.
7. **Compare realised fills** against the modelled cost and close the loop.

Nothing here goes near live capital.

---

**Detail follows.** Everything below is the working record behind the tables above: what was measured,
what was tried, and what cost time. Anchored to kill-chain stages rather than to a
numbered backlog, so the references stay true as the backlog moves.

## Stage 00 - the Marketstack to catalog bridge

`copilot/data/` fetches Marketstack EOD, gates it, and writes Nautilus bars into a
`ParquetDataCatalog`. Run over AAPL, MSFT and SPY for 2005-2025: 15,851 rows fetched,
**15,849 bars written**, 2 rejected.

### The vendor's adjusted prices are not usable

Marketstack returns two OHLC sets. Measured over 15,851 rows:

| Property                  | `open/high/low/close` | `adj_*`     |
| ------------------------- | --------------------- | ----------- |
| Rows with incoherent OHLC | 12 (0.011%)           | 3,553 (22%) |
| Rows with null fields     | 0                     | 1,751       |
| Back-adjusted for splits  | yes                   | yes         |
| Adjusted for dividends    | no                    | yes         |

AAPL 2022-11-03 reports `adj_close` 138.65 under an `adj_low` of 138.75 - a close
outside its own bar. A backtest fed that fills at a price the bar says never traded.

**Correction.** An earlier version of this section said the raw set had *zero*
incoherent rows. That held over the 15,851 rows first measured; over 105,414 it has
twelve, every one an open a few cents outside the day's range (GOOGL 2025-12-29 opens
at 314.52 against a high of 314.02). 0.011% against 22% leaves the choice unchanged,
and the gate rejects them either way - but the claim was stronger than the evidence
supported once the universe widened.

So the overlay stores the **raw** set, which is the reverse of the obvious choice. It is
safe because the vendor has *already back-adjusted it for splits*: AAPL's close on
2020-08-28 is reported as 124.8075, the pre-split 499.23 divided by the 4:1 split that
settled on the 31st. Read back off disk, the split day moves **+3.39%**, not -75%. What
the raw set lacks is dividend adjustment, which understates total return by the yield
without manufacturing a discontinuity a strategy could mistake for a signal. `dividend`
and `split_factor` are carried per row so a dividend-adjusted series can be derived
later from a coherent base.

### The vendor emits bars on days the market was shut

SPY comes back with a complete-looking bar for **Thanksgiving 2023-11-23** and **Good
Friday 2024-03-29** - plausible OHLC, nine-figure volume, nothing marking them as
phantom. A phantom bar lets a strategy enter, exit and be *scored* on a day no order
could have been placed.

`copilot/data/calendar.py` is a rule-based US equity calendar, written rather than
imported because the overlay adds no dependency. It is validated against the data, not
by assertion: over 2005-2025 it reproduces AAPL's and MSFT's session sets exactly - 5,283
sessions, zero extra, zero missing - and flags only SPY's two phantom rows.

That test earned its keep immediately. The first version closed 31 December when
1 January fell on a Saturday, following the federal observance rule; the exchanges stay
open, and all three symbols traded on 2010-12-31 and 2021-12-31.

### The vendor's 2026 rows are holed, and the gate refused them

Measured 2026-09-04, attempting to bring the catalog current for the live warm-up. Of 507
rows fetched for AAPL, MSFT and SPY over 2026-01-01 to 2026-09-04, **11 are unusable**:

| Defect           | Rows | Detail                                                     |
| ---------------- | ---- | ---------------------------------------------------------- |
| `close` is null  | 8    | 2026-06-09 and 06-10 for all three; 06-15 for MSFT and SPY |
| `close` is `0.0` | 2    | SPY 2026-04-07 and 04-08, with open, high and low all sane |
| Incoherent OHLC  | 1    | MSFT 2026-01-15, high 464.12 below open 466.345            |

That is a 2.17% rejection ratio against `backfill`'s 2% threshold, so **nothing was
written** - which is the gate working. A half-ingested history is worse than none,
because later runs treat whatever landed as complete.

**It is not transient.** A narrow re-fetch of the exact dates returns the identical
nulls, so this is the vendor's stored data rather than a bulk-query artefact. AAPL has a
close on 2026-06-15 while MSFT and SPY do not, so it is per-symbol-per-day rather than a
market-wide outage. The zero closes are the more dangerous shape: a null is refused by
any schema check, while `0.0` is a number, and a gate that only tested for presence would
have written it.

**Resolved 2026-09-04.** All eleven are substituted whole from Databento - consolidated
daily bar, close from the listing venue's official auction print - recorded in
`copilot/data/substitutions.py` and verifiable with `python -m copilot.data.substitutions`,
which re-fetches and compares. 11/11 reproduce. The pull cost **$0.0019**, and priced the
whole 2026 year rather than the six bad dates so every session could be checked, not just
the broken ones: **no other 2026 close disagrees by more than 10 bps**.

MSFT 2026-01-15 was the interesting one. Its close was fine; the row is *field-shifted* -
the open repeats 2026-01-14's and the true open (464.12) sits in the high slot, which is
why it read as an incoherent bar. That shape would pass a null check and a zero check
both.

The catalog now runs to 2026-09-03 at 5,452 bars per symbol, the carve is unchanged
(4,280 development, 1,003 holdout, 169 clipped, 18.99%), and all six verdicts re-run
bit-identical. See [ADR-0018](decisions/0018-an-unusable-bar-is-substituted-whole.md).

### Other findings worth keeping

- **`price_currency` is unreliable.** MSFT returns 18 rows tagged `EUR`, 59 tagged
  lowercase `usd`, and 5,023 untagged - all the same continuous USD series (513.71 tagged
  USD, then 512.50 tagged EUR the next session). Collected for reporting, never used to
  decide what is stored.
- **Precision 4 is exact, and measured.** Across 63,404 price values the vendor never
  returns more than four decimal places. The conversion compares every value against its
  source and raises rather than storing a rounded price.
- **Never verify a `Quantity` through `as_double()`.** AAPL's 1,020,062,400-share session
  on 2005-02-02 round-trips as 1020062399.9999999. An exactness guard written against the
  float rejected a volume the catalog stores perfectly well; the check goes through `str`.
- **v2 reports `total` truthfully**, which v1 capped at `limit`. Paging to exhaustion is
  kept regardless - it is correct either way - and `total` is now a cross-check, so a run
  that ends early with rows outstanding fails instead of writing a partial history that
  the next run reads as complete.

The catalog lives at `~/.nautilus_copilot/catalog`, outside the repository: a parquet
store inside the tree would need a `.gitignore` entry, and `.gitignore` is an upstream
file this fork does not touch.

## Stage 04 - the `Replay` seam

The trade-copilot gate takes its replay as an argument:

```python
Replay = Callable[[Sequence[DailyBar], StrategyParameters], BacktestRunResult]
walk_forward(bars, grid, replay=...)
```

That injection point is why the fusion is tractable: the methodology is
engine-agnostic. `copilot/validation/nautilus_replay.py` supplies a replay backed by a
Nautilus `BacktestEngine`, so the same gate can be scored against Nautilus's
volume-, size- and probability-sensitive fill models instead of a flat proxy.

**The strategy must report what it risked.** Nautilus records the fill, not the stop
that sized it, so R cannot be derived from a position alone. Strategies register
per-position risk through `RiskAmountRegistry`; a missing record raises rather than
silently scoring `r_multiple == 0`, which would make the gate report "no edge"
everywhere.

### The replay scored one trade per run - fixed

Found by running the gate on real AAPL history for the first time: all five folds came
back `in_sample_selected_nothing`, with no error anywhere.

`ReplayVenue` defaulted to `OmsType.NETTING`. Under netting, Nautilus reuses **one
position id per instrument and strategy**, so `cache.positions_closed()` holds a single
position object that is reopened and closed over and over, and only the final round trip
survives to be scored. `RiskAmountRegistry` is keyed by position id and aliased the same
way.

Measured on 60 bars of real AAPL with a strategy that alternates in and out every bar:

| OMS       | scoreable trades |
| --------- | ---------------- |
| `NETTING` | 1                |
| `HEDGING` | 30               |

The consequence was not a reporting detail. `expectancy_r` over a single trade is noise,
the `min_trades` floor then rejects every candidate, and the gate returns "selected
nothing" on every fold while looking like it ran correctly. Any verdict it produced
would have been meaningless.

The default is now `HEDGING`, and `ReplayVenue.name` defaults to the instrument's own
venue rather than `"SIM"` - a mismatched venue is rejected by the engine, so guessing a
name could only ever be wrong.

**Why it survived the test suite:** the existing test asserted `result.trades` was
non-empty. One trade satisfies that. The replacement asserts an exact count (four round
trips over eight bars) and a second test pins the netting behaviour explicitly, so the
cost is visible to anyone who sets it deliberately.

## Stage 06 - risk breakers, and what they actually enforce

*Historical, written 2026-09-01.* `set_trading_state` has been exposed to Python since, the guard halts the engine, and it runs in the basket with its ledger (2026-09-11); see the kill chain.

Ported from trade-copilot ADR-0025. Both breakers are pure functions over closed
trades, so they are tested against hand-built losing streaks rather than a live
account.

- Consecutive stop-outs (default 4), streak must be current
- Peak-to-trough realised drawdown (default 6% in 14 days)
- 3-day cooldown, longest-running breach wins

**Enforcement is reactive, not preventive.** Nautilus has exactly the right primitive

- `TradingState` with `HALTED`/`REDUCING`, enforced natively in the Rust risk engine -
but `set_trading_state` has no pyo3 binding and no production caller, so Python cannot
reach it. The guard therefore cancels working orders account-wide, flattens configured
instruments, and publishes a signal. A strategy that keeps submitting will keep
getting orders accepted between evaluations.

Closing that gap is the `set_trading_state` decision above, and is the single change
that would touch upstream files.

## Stage 10 - spread calibration

trade-copilot's `PaperFillConfig.spread_bps` is **5 bps per side**, described in its
own source as "a deliberately conservative ceiling ~10x the quoted spread, pending a
live-quote snapshot". Its `SYSTEM.md` §14 names this the top blocker, ahead of premise
supply, because the modelled cost is what decides every verdict.

Measurement, AAPL over 654s of IB quotes (111 usable, 18 rejected as crossed/locked):

|                                 | full spread (bps) | per side (bps) |
| ------------------------------- | ----------------- | -------------- |
| median                          | 0.6381            | **0.3190**     |
| p75                             | 1.2748            | 0.6374         |
| p95                             | 2.8685            | 1.4343         |
| max                             | 5.7374            | 2.8687         |
| incumbent                       | -                 | 5.0            |
| **overstatement at the median** | -                 | **~15.7x**     |

**Sample size moved this number by 2x, which is itself the finding.** A first 148s run
over 24 quotes gave 1.2753 bps full / 7.8x; the 654s run over 111 quotes gives 0.6381
bps / 15.7x. Do not set a coefficient from a short run.

Two caveats remain:

- **Delayed data.** The account has no realtime US equity subscription, so these are
  delayed quotes - a genuine bid/ask, but updating slowly and possibly wider than the
  realtime NBBO. This is an *upper bound*, the conservative direction.
- **The distribution has a tail.** Median 0.64 bps but p95 2.87 and max 5.74. A cost
  model set at the median will understate the bad days. Choosing the coefficient is a
  policy decision - the median is the honest central estimate, p75 or p95 the
  defensible conservative ones - and should be made explicitly rather than by
  defaulting to whichever number is at hand.

### The first reproducible snapshot, 2026-09-02

With `add_actor` exposed, the calibrator ran from committed code for the first time: 25
minutes, delayed quotes, all three names in one session
(`calibration/out/spread_snapshot_20260901T154744Z.json`).

| Full spread, bps of mid | samples | median     | p75    | p95    |
| ----------------------- | ------- | ---------- | ------ | ------ |
| AAPL                    | 251     | 1.2241     | 1.5312 | 3.9805 |
| MSFT                    | 301     | 1.9964     | 2.3970 | 3.5932 |
| SPY                     | 248     | **0.2618** | 0.3928 | 1.0476 |

Two things this adds to the 2026-08-31 numbers above. **The cross-name spread is the
stable fact**: MSFT quotes ~8x wider than SPY in every run, which is the case for a
per-instrument coefficient regardless of which percentile is chosen. **The day-to-day
movement is not noise to average away**: AAPL's median doubled against 08-31 (1.22 vs
0.64) while SPY's did not move (0.26 both days), so a coefficient set from any single
session inherits that session. The 5 bps per-side incumbent remains 4x-38x conservative
depending on the name.

### Entitlement change, 2026-09-01

IB market data **release forms** were completed. Retested immediately; the effect is
partial and worth recording precisely, because it changes what is calibratable but not
what is backtestable.

|                           | before forms          | after forms       |
| ------------------------- | --------------------- | ----------------- |
| AAPL delayed quotes       | works                 | works             |
| MSFT / SPY delayed quotes | **no data, no error** | **works**         |
| Any realtime quotes       | no data               | **still no data** |
| US equity historical bars | IB 2188               | **still IB 2188** |
| Index (`^SPX`), futures   | IB 2188               | still IB 2188     |
| Forex (IDEALPRO)          | full                  | full              |

So the forms unlocked **delayed quotes across the US equity universe** - the earlier
MSFT/SPY silence was an entitlement gap, not the adapter bug it resembled. Realtime
streaming and historical bars still need a paid subscription, which the forms alone do
not grant. IB subscriptions also typically activate at a trading-day boundary, so
recheck after the next session before concluding a purchase has not landed.

Practical effect: multi-symbol spread calibration is now possible, which was a stated
prerequisite for the paper run. The backtest evidence base is unchanged - there is
still no route to US equity history through IB.

**Rechecked 2026-09-01 after the session close: unchanged.** AAPL, MSFT, SPY and ^SPX
all still return IB 2188 for historical bars under both REALTIME and DELAYED; forex
still returns bars normally. Release forms do not grant historical data, and a paid
subscription is required. `copilot/calibration/entitlements.py` runs this check.

**Realtime quote entitlement, settled 2026-09-02 inside a live session: still absent.**
`spread_snapshot` recorded **zero** usable quotes across AAPL, MSFT and SPY over 107s under
`REALTIME`, and **55** across the same three over 106s under `DELAYED` two minutes later.
The delayed run is what makes the realtime run mean anything - on its own, zero quotes is
indistinguishable from a broken subscription, which is why the earlier closed-session
attempt proved nothing and this one does.

`entitlements.py` probes historical bars only, which is not what this question was about.
Its docstring now says so and points at the two-run procedure above.

### Entitlement change, 2026-09-09: the consolidated feeds

The live username is now subscribed to NYSE (Network A/CTA), NYSE American, BATS, ARCA,
IEX and Regional Exchanges (Network B) and NASDAQ (Network C/UTP), USD 1.50 each a month
for a non-professional - the three tapes the section below said were the only route.
Measured within the hour, from the Florida machine, after the 16:00 ET close:

|                                  | 2026-09-02       | 2026-09-09                                  |
| -------------------------------- | ---------------- | ------------------------------------------- |
| Delayed quotes, nine instruments | works            | works, 15/15                                |
| Realtime quotes, AAPL/MSFT/SPY   | **zero** in 107s | **47 / 61 / 139** in 130s                   |
| Delayed control, same session    | 55 in 106s       | 35 / 64 / 120 in 130s                       |
| US equity historical bars, SMART | IB 2188          | **bars returned**, `REALTIME` and `DELAYED` |
| IEX- and ISLAND-directed history | IB 2188          | bars returned                               |
| `^SPX` index history             | IB 2188          | still 2188 - a CBOE index feed, not bought  |
| Forex (IDEALPRO)                 | full             | full                                        |

Evidence: `live/out/preflight_20260909T204052Z.json`,
`calibration/out/spread_snapshot_20260909T204319Z.json` (realtime) and
`spread_snapshot_20260909T204529Z.json` (the delayed control). The spreads in those two
snapshots are **after-hours** numbers - MSFT quoted 8-10 bps wide - and are filed as the
entitlement evidence, not as a coefficient. ADR-0019 charges spread from measured
history, and the first realtime calibration inside the session is a separate run.

**What it does not settle.** Whether the feed quotes *before* the open, which is the hour
the evening command runs; the row under *Ready to build* stays. And subscriptions activate
at a trading-day boundary, so the 2026-09-10 session is the first full one on the new
entitlement.

**The first connection from this machine failed on IB 10197 before any of this**,
"No market data during competing live session", on all nine quotes with stages 1-2
passing (`live/out/preflight_20260909T202508Z.json`). The paper username borrows the live
username's data and IB refuses it while the live username is logged in elsewhere - Client
Portal, mobile, or a live TWS on another machine. It is the streaming sibling of the 162
rule below: log the live session out, then rerun. Not an entitlement reading.

### Why 2188 happens, and what would fix it

The account's complimentary feed is **"US Real-Time Non Consolidated Streaming
Quotes"** - IB's free IEX-sourced feed. IB's own API documentation states that
historical data carries *the same subscription requirement as streaming top-of-book*,
and that **a SMART-routed historical request requires subscriptions to every exchange
the instrument trades on**. A non-consolidated (single-venue) entitlement cannot
satisfy that for a name like AAPL, which is exactly what 2188 reports.

So the fix is **consolidated** US equity data - the Network A (NYSE/CTA), Network B
(NYSE American) and Network C (NASDAQ/UTP) tapes, which IB normally sells as a value
bundle plus a streaming add-on. Prices were not verifiable from here (the IB pricing
page returns HTTP 403 to automated fetches) and must be confirmed in Client Portal.

**Answered 2026-09-02: a directed-exchange request is not satisfied either.**
`AAPL=STK.IEX` and `AAPL=STK.ISLAND` both return 2188 under `REALTIME` and `DELAYED`, the
same as the SMART-routed request. So the hoped-for free route to some history does not
exist: the non-consolidated entitlement does not cover historical bars even aimed at the
single venue it does cover. Consolidated data remains the only route, and it is a purchase.

### Blocked 2026-09-01: IB error 162

All historical requests - **including forex, which had worked 25 minutes earlier** -
now fail with:

```text
[162] Historical Market Data Service error message:
Trading TWS session is connected from a different IP address
```

This is a session fault, not an entitlement one: the connection succeeds and contract
resolution still works, only the historical service refuses. It appeared after the IB
web portal was accessed while TWS was running. **Do not read any 2188 result taken
while 162 is active** - the two are unrelated failures and conflating them will produce
a wrong conclusion about entitlements.

**Confirmed and cleared 2026-09-01 by logging out of the IB web session.** Forex
historical went straight back to returning bars and AAPL returned to the honest 2188,
with no TWS restart needed.

**Operating rule: IB allows one active session per login.** Opening Client Portal,
Account Management, or the mobile app while TWS is running can displace the API's
historical data service and produce 162, even though the socket stays connected and
contract resolution keeps working. Do not browse the IB website during a data run.
When 162 appears, log out of the web session first - that alone is usually enough.

Re-run `entitlements.py` after clearing it, before trusting any data verdict.

Three-symbol measurement (delayed, 846s, 139-150 usable quotes each;
`calibration/out/spread_snapshot_20260831T173204Z.json`):

| symbol | n   | median full (bps) | per side | p95 full | vs 5 bps/side        |
| ------ | --- | ----------------- | -------- | -------- | -------------------- |
| SPY    | 149 | 0.3917            | 0.1958   | 1.1752   | **25.5x** overstated |
| AAPL   | 139 | 0.6375            | 0.3188   | 2.2284   | 15.7x                |
| MSFT   | 150 | 1.5662            | 0.7831   | 3.5203   | 6.4x                 |

**AAPL reproduces to four decimal places** across two independent runs (0.6381 over
654s, 0.6375 over 846s), which is the first evidence that the measurement is stable
rather than a sampling artefact.

The spread differs by **4x between SPY and MSFT**, so a single global `spread_bps` is
the wrong shape for the model - it should be per-instrument. That conclusion is
structural and does not depend on sample size.

### Calibrator shutdown behaviour

An interrupted run *does* write its report, but only after the node finishes unwinding,
which took minutes on an 846s run. A check made immediately after signalling therefore
looks like data loss when it is not. Worth a signal handler that snapshots accumulated
state promptly, so an operator can interrupt and see results without waiting on node
teardown - but no samples are actually lost today.

## The `set_trading_state` decision, in detail

*Resolved 2026-09-01*: built as the `RiskEngine` binding registered in `UPSTREAM_DELTA.md`. Kept for the reasoning.

Now that the toolchain exists this is no longer blocked on tooling, but it is not a small
binding either, and the shape matters more than the code.

What the investigation found:

- The kernel holds `risk_engine: Rc<RefCell<RiskEngine>>` with a public accessor, so a
  same-thread caller can reach it.
- `LiveNodeHandle` is the thread-safe control surface, but it **cannot** hold the risk
  engine: `Rc` is not `Send`, and the message bus is thread-local too.
- `PyLiveNode` can reach the kernel, but its borrow is unavailable while a hosted run owns
  the node - which is exactly when a halt would be wanted.
- `TradingCommand` has no variant for trading state, and adding one touches the enum and
  every match arm across the execution path.
- A Python `Strategy` has no message bus access at all, so the guard cannot send a command
  even if one existed.

The least invasive option that would actually work is an additive message bus endpoint -
`MessagingSwitchboard` entries are just named strings with a `OnceCell`, so adding
`risk_engine_set_trading_state` and registering a handler is contained. It still needs a
way for a Python component to send to an endpoint.

That is three upstream files including `crates/common`, which is a larger commitment than
the two adapter fixes.

**Cleared 2026-09-01.** Upstream changes are permitted, subject to registration in
`docs/UPSTREAM_DELTA.md`. This would become the fork's largest single delta and the only
one in `crates/common` - a crate far more central than the IB adapter, so more exposed to
upstream churn. Build it as one minimal commit, register every file it touches, and keep
the surface additive: a new switchboard endpoint and handler alongside the existing ones,
never a change to an existing signature. Until it lands, the guard stays reactive and
says so.

## Bugs found upstream - fixed

Both are fixed and proposed on their own branch. **One of them corrects a claim made
earlier in this document.**

### 1. `request_ticks` ignored its `timeout`

The timeout wrapped only the request that opens the subscription, not the loop that drains
it, so a request IB accepts but never answers hung forever. Reachable by asking for
`TRADES` ticks on a forex pair, which IDEALPRO does not have.

Measured before and after against paper TWS: `timeout=20` was still running when killed at
**97s**; it now returns empty at **22.0s**, while `BID_ASK` over the same window still
returns its **1022 ticks in 0.4s**.

### 2. Subscriptions were keyed by instrument alone - *not* what was claimed here before

**Correction.** This document previously stated that a failed subscription silently tears
down sibling subscriptions on the same instrument. Reading the code does not support that
mechanism: every subscription gets its own `child_token()` from the client's cancellation
token, and a per-task error is logged rather than propagated, so one stream failing cannot
cancel another. The claim was inferred from three correlated observations and should not
have been stated as a mechanism.

What the code *does* contain is a different, provable defect. The subscriptions map was
keyed by `InstrumentId` while its value carried a `subscription_type`, so it could hold
only one subscription per instrument. Subscribing to trades on an instrument that already
had quotes silently evicted the quote entry - leaving that task running untracked, and
sending a later `unsubscribe_quotes` to cancel the *trades* stream instead. The key is now
`(InstrumentId, SubscriptionType)`.

**The original observation no longer reproduces.** Tested 2026-09-02 inside a live session
with `live/probes/subscription_interference.py`, which treats one instrument with the second
subscription and leaves a control instrument alone, then counts quotes for both across the
same two windows. Quotes stopping on both would be a session-wide event and evidence about
nothing; only the treated instrument stopping is the reported behaviour.

| Treatment           | Treated AAPL, before / after | Control MSFT, before / after | Verdict        |
| ------------------- | ---------------------------- | ---------------------------- | -------------- |
| Tick-by-tick trades | 39 / 36                      | 38 / 36                      | not reproduced |
| L2 book (`L2_MBP`)  | 39 / 37                      | 38 / 38                      | not reproduced |

**Read this as "the trigger no longer occurs", not as "the mechanism is disproven."** The
original stall came with IB refusals - 10189 for tick-by-tick and a depth-entitlement
refusal - and the hypothesis was that a refusal disturbs the contract's data line. Neither
run drew any refusal: both requests were accepted and simply delivered nothing. The
account's entitlements have widened since (release forms unlocked delayed quotes across the
US equity universe), so the most likely reading is that IB no longer refuses these requests
on this account and therefore cannot trigger the stall. The mechanism is untested, and the
practical risk is gone.

**One method note worth keeping.** The first depth run used `subscribe_book_depth10`, which
the IB adapter does not implement; the call raised on a missing argument, quotes carried on
undisturbed, and the run read *not reproduced* from an experiment that never ran. A
treatment that raised now forces an inconclusive verdict. **A negative result is only worth
as much as the proof that the treatment was applied.**

## Rust toolchain prerequisites

*Historical, written 2026-09-01*: installed on both machines since; `MAINTENANCE.md` has the current build procedure.

Three items are blocked on a source build: the `set_trading_state` pyo3 binding, the two
IB adapter bugs, and `make pre-commit` / `make format`. This environment currently has
`curl`, `git`, `uv` and `python3` and nothing else from the build chain.

**Status: installed and working.** `rustc 1.98.0`, Cap'n Proto 1.5.0 under `~/.local`,
uv 0.12.6, and `make build-debug` produces an editable install. `target/` is ~26 GB.
`make install-tools` has **not** been run, so `make pre-commit` is still unavailable.

**Needs root - the only step an agent cannot do:**

```bash
sudo apt-get update
sudo apt-get install -y build-essential clang lld curl git make pkg-config
```

**Everything after that is user-level**, following `docs/developer_guide/environment_setup.md`:

```bash
curl https://sh.rustup.rs -sSf | sh          # rust-toolchain.toml pins 1.98.0
source "$HOME/.cargo/env"
cargo install cargo-binstall --locked
cargo install cargo-nextest --locked          # the test runner; bare `cargo test` is off-book
CAPNP_PREFIX="$HOME/.local" ./scripts/install-capnp.sh   # 1.5.0; prefix avoids sudo
make install-tools                            # includes prek, needed by make pre-commit
make sync
```

Host has 936 GB free and 33 GB RAM available, so neither disk nor memory is a constraint;
expect the first full workspace build to be long regardless.

`make install-tools` installs the complete dev toolset (cargo-fuzz, llvm-cov, flamegraph,
lychee and more). Only a subset is needed to *build*, but the full set is what
`make pre-commit` expects, so installing it once is what makes that checklist item
satisfiable in future pull requests.

## Environment facts that cost time

*Historical, written 2026-09-01.* The TWS host address rotates on reboot and IB history is entitled since 2026-09-09; the paper campaign log is current.

- **TWS reports JST.** The `ibapi` crate has no alias for it and it is not IANA, so
  connections fail with a generic "Failed to connect to IB Gateway/TWS" that hides the
  cause. `IBAPI_TIMEZONE_ALIASES="JST=Asia/Tokyo"` is required in every process.
- **WSL2 NAT.** TWS is at `172.17.112.1:7497`; `127.0.0.1` does not work. The trusted
  IP is the WSL `eth0` address and **changes on reboot**. Mirrored networking
  (`networkingMode=mirrored` in `.wslconfig`) makes the source `127.0.0.1`, which TWS
  trusts implicitly, and removes a measured ~4.65s handshake stall on every connect.
- **Account data.** Paper `DUT067974` reads forex fully (realtime quotes, bars, ticks)
  and US equities only as delayed quotes. Historical US equity bars fail with IB 2188
  across all 16 request shapes tried - end date from 1 to 400 days back, every market
  data type, every bar spec, RTH on and off. No client-side workaround exists.

## The paper run

*Historical, written 2026-09-01.* Superseded by *Shortest route to a paper run* above and by the paper campaign.

Not started. **Both prerequisites originally listed here have landed** - the multi-symbol
spread calibration and the ported walk-forward gate - but that list was incomplete: it
never named a strategy, and without one there is nothing to validate or deploy. The
corrected route is under "Shortest route to a paper run" above.

**Then, in order**

1. Re-run every existing trade-copilot verdict at the measured spread. Its own analysis
   says this "spends nothing, risks nothing, and is worth more than the next premise" -
   and it flips verdicts, so no current verdict can be read as a statement about the
   market until it is done.
2. Validate a candidate through IS -> WFA -> OOS on the Nautilus replay. The holdout is
   single-use and has never been spent.
3. Run 2-4 weeks on IB paper with the risk guard enabled. Forex is the only asset class
   with complete realtime data on this account, which argues for starting there; against
   that, all three portable setups are daily-bar equity patterns and the daily catalog
   now feeds them, so equities are no longer blocked on data. trade-copilot's own review
   notes that every premise tested so far was a daily-bar pattern on three mega-cap US
   names, "the most heavily arbitraged corner of the market" - which is an argument for
   widening the universe (see the open items), not for switching asset class.
4. Compare realised fills against the modelled cost and close the loop.

**Not gated on any of this:** nothing goes near live capital. The paper run is the first
place the breakers will ever fire, since they cannot fire in a backtest by design.
