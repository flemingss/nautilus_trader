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

| #   | Stage                | Covered by                    | State as of 2026-09-10                                                                |
| --- | -------------------- | ----------------------------- | ------------------------------------------------------------------------------------- |
| 00  | Historical data      | `copilot/data`                | **Ready, with known holes.** 9 registered symbols, 29,722 daily bars to 2026-09-09    |
| 01  | Screening / universe | -                             | **Pinned**, out of repo by decision                                                   |
| 02  | Research / strategy  | `copilot/strategies`          | **NOT READY. No established edge.** The blocking stage; everything below waits on it  |
| 03  | Backtest engine      | Nautilus `BacktestEngine`     | Ready. Fill, fee and latency models                                                   |
| 04  | Validation gate      | `copilot/validation`          | **Ready, and stricter.** Three-way verdict with an interval (ADR-0024)                |
| 05  | Position sizing      | `copilot/risk/sizing`         | Ready for margin. **Settled-cash sizing absent**, and the live account is cash        |
| 06  | Risk limits          | `copilot/risk/protections`    | **Halt proven live.** Balance and margin checks do not run on the SMART venue path    |
| 07  | Orders / exits       | Nautilus execution            | **Ready and exercised.** Order-type matrix passed in regular hours 2026-09-10         |
| 08  | Live deployment      | Nautilus `LiveNode`           | **Supervised: ready.** Unattended: three named gaps, all open                         |
| 09  | Monitoring           | Nautilus analysis + tearsheet | **Alerting built, nothing calls it.** No kill command, so no consumer for its trigger |
| 10  | Cost calibration     | `copilot/calibration`         | **Strongest stage.** Spread and commission both corroborated against the broker       |

**Read the table by where it breaks, not by how much is green.** Ten of eleven stages are
built and seven are proven against a live broker. The one that is not is stage 02, and it is
the one that decides whether any of the rest is worth running: **no premise has an
established edge.** The AAPL holdout returns `insufficient_evidence` under
[ADR-0024](decisions/0024-a-holdout-pass-needs-an-interval.md) - +0.035 R with a 90% interval
of [-0.126, +0.209]. The pooled nine-symbol walk-forward passes its majority gate and clears
zero, and its fold detail puts the edge in the era before the pool was a pool: 12 of 22
three-symbol folds at +0.081 R against 5 of 11 wider folds at +0.017 R. Nothing is frozen,
so stage 08's supervised readiness has nothing to deploy.

Three groups of open work, and they gate different things:

- **A candidate worth deploying** - stage 02, eleven rows. The pooled premise's remaining
  questions, attribution, and the evidence interval on ordinary verdicts.
- **Unattended running** - stages 08 and 09. Alerting is written and unwired, the operator
  kill command does not exist, and the sweep's *clear* verdict cannot yet be confirmed
  against the broker. Supervised paper needs none of these; leaving the system alone needs
  all three.
- **Real money in a cash account** - stages 05 and 06, three rows. Settled-cash sizing, the
  T+1 rules confirmed with the carrying entity, and the margin-or-cash question. The paper
  account is MARGIN with USD 1M and **cannot surface any of them**, which is why they stay
  open however well paper goes.

### Stage 02 - the gap fade, and the first real verdict

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

287 tests, all passing: `PYTHONPATH=. pytest copilot/tests/ -q`.

### Stage 08 - what a paper run actually needs

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

Twenty-one items. Grouped by blocking condition rather than by component, because that is
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

### Waiting on the account (2)

Recorded 2026-09-01. **The operator's to close, not the repository's.** Two items in the
groups below inherit their block, which is why they sit first.

**Closed 2026-09-09: the market-data equity minimum.** The owner subscribed the live
username to NYSE (Network A/CTA), Network B and NASDAQ (Network C/UTP), USD 1.50 each a
month, and the paper account borrows them. Measured the same hour from the second
machine: `preflight` 15/15 after the close, historical bars for AAPL and SPY under both
`REALTIME` and `DELAYED` where every request had returned 2188, and `spread_snapshot`
recording 47/61/139 quotes under `REALTIME` against a 35/64/120 delayed control. Detail
under stage 10 below; the campaign log has the rows.

| Item                                                              | Stage  | The action                                                                                                                                                                                 |
| ----------------------------------------------------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Resolve margin, or confirm cash is permanent**                  | 05, 06 | The account is **cash**. Cash cannot sell short, so the gap fade's short leg is unavailable at any price, and sizing must come from **settled USD** rather than headline equity.           |
| **Confirm settlement and buying-power rules on the real account** | 06     | T+1 is the general US rule, but PREFLIGHT requires it verified with the carrying entity rather than assumed. Decides whether a settled-cash check has to sit in front of order submission. |

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

### Waiting on a decision (1)

| Item                                                           | Stage  | The decision                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| -------------------------------------------------------------- | ------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Whether to build the assisted-decision layer's second tier** | 02, 08 | [`DRAFT_ASSISTED_DECISIONS.md`](DRAFT_ASSISTED_DECISIONS.md) names two conditions and neither holds today. **The holdout cannot test a model**: it runs 2022-01-01 to 2025-12-31 and every pinnable model trained across it, so the only clean evidence is forward. **And the cost is decisive at this account**: charged against a pooled nine-symbol premise, a USD 30 per month layer costs 0.101 R per trade, which is the commission burden that made the gap fade negative, and a USD 10 layer consumes the whole 0.035 R the AAPL holdout returned. The draft's first tier makes no market claim and is ordinary work; this row is the second tier only. |

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

**Closed 2026-09-09: US equity history through IB.** The three Network subscriptions
under *Waiting on the account* were the spend; `entitlements.py` returned bars for every
US equity shape that had returned 2188. IB history is not adopted as a source - Databento
holds intraday ([ADR-0015](decisions/0015-databento-is-the-intraday-source-only.md)) and
Marketstack the daily series - but the wall is gone, and the calibrator can cross-check a
Databento-derived coefficient against the broker's own tape.

| Item                           | Stage | Notes                                                                                                                                                                                                                                                                                |
| ------------------------------ | ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Point-in-time index membership | 00    | Norgate Platinum, USD 630/year, the only verified source of true daily membership for the S&P 500 and Russell 3000 including delisted securities. Deferred until the universe correction starts, not rejected ([ADR-0015](decisions/0015-databento-is-the-intraday-source-only.md)). |

### Ready to build (16)

Twenty-two rows closed on 2026-09-03, 2026-09-04 and 2026-09-05 moved to [`CHANGELOG.md`](CHANGELOG.md);
this table holds open work only, and its count is the checksum.

| Item                                                                                   | Stage | Notes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| -------------------------------------------------------------------------------------- | ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Confirm the sweep against the broker, not against the event stream**                 | 08    | `cancel_working` reports `CACHE CLEAR` when it sees no cancel-rejection events, which is what it also sees when nothing happened. Measured 2026-09-10: an adopted order is cancelled at the broker and **no acknowledgement arrives at all**, from the originating client id or a foreign one, so no deadline fixes the verdict. What did work, by hand, was asking again on a fresh connection - reconciliation reported the order gone. The sweep should end with that second connection so its verdict is the broker's answer rather than its own silence. Until then the printed caveat stands and the operator eyeballs TWS.                                                                                                                                                                                                                                                                            |
| **Revise the AAPL next-close premise as a new experiment**                             | 02    | Owner decision 2026-09-05: `revise`. The holdout is spent, so a revision is a new activation with a new premise and no holdout of its own on this series (ADR-0014). What to change is the research question: the edge was a 54.1% win rate with the average loss larger than the average win, underwater by more than it made, crossing zero at USD 25,000. **Sharpened 2026-09-10 by ADR-0024**: recomputed under the interval gate the same numbers return `insufficient_evidence`, not `pass` - +0.035130 R with a 90% interval of [-0.126, +0.209] R and 102 effective trades. The premise was never disproven, so the revision to reach for is the one that raises the effective sample rather than the one that changes the rule: pooling across the nine registered ETFs is the row below, and it is the same work.                                                                                  |
| **Decide what a pooled holdout may contain**                                           | 02    | `copilot.strategies.pool` refuses to spend one and says why. **One member is spent: AAPL** (ADR-0014), so a pooled holdout containing it is not out of sample for that symbol. SCHX's spend was voided under ADR-0021 and its window is unviewed; the other seven have never been spent. The options are to exclude AAPL - costing one symbol of nine and keeping MSFT and SPY, the other two histories from 2005 - or to include it and label the contamination. Owner decision; the tool will not pick a default. An earlier draft of this row said three members were spent, which was wrong.                                                                                                                                                                                                                                                                                                             |
| **Pool over a constant membership, or accept the pool is two experiments**             | 02    | Measured 2026-09-10: the first pooled run was a three-symbol pool for 22 of 38 folds and a nine-symbol pool for three of them, and the edge lives in the narrow era - 12/22 folds and +0.081 R before 2017 against 5/11 folds and +0.017 R after it. `--from-year` exists for a constant window, but this universe cannot supply a wide one: the three symbols with history from 2005 are exactly the three whose holdout begins in 2022, while the late starters run to 2024. The widest constant slice is seven symbols over 2017-2021, about eight folds. Either find longer histories for the ETFs or state that the pooled verdict covers two regimes.                                                                                                                                                                                                                                                  |
| **Spend the SCHX holdout again, on the corrected series**                              | 02    | Owner decision 2026-09-05: the 2026-09-04 spend is void under ADR-0021, its record under `holdouts/voided/`. `python -m copilot.strategies.spend_holdout schx-gap-fade-long-next-close --confirm schx-gap-fade-long-next-close` is available once more and is the owner's command; the projection refuses a window that cannot score.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| **Probe EODHD against the checks that caught Marketstack**                             | 00    | Owner decision 2026-09-05: probe, cancel nothing. Needs an EODHD key. The checks are the intraday coherence probe, the phantom-session scan against the calendar, the sub-penny close gate, and the as-traded split scan; Marketstack failed all four this week. Adopt only on a pass.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| **Rank signals within the session cap**                                                | 02    | The ledger grants in the order bars are handed out, which is name order, so EEM is asked before SCHX every morning. Arbitrary, stated, and recorded - the refusals in each session record are the input this needs. Ranking by evidence is a research question: which signal deserves the budget when four correlated wrappers fire at once.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| **Let onboarding take the steps it reports**                                           | 00    | `onboard` names the next command; it does not run it. The free steps (backfill, patch, registry file) could be driven from the same status it already computes, leaving only the metered pull and the repin as deliberate acts. Worth doing once the sequence has been run against a few more symbols and stopped changing.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| **Order the evening command after the vendor publishes, or make its warm-up advisory** | 08    | Measured 2026-09-10: `day evening` refuses all twelve activations because warm-up for the next session needs the current day's bar, and `append` shows that bar **pending** at the vendor - the known EQUS.SUMMARY lag of about a day. The refusal itself is right and must stay. What is wrong is the sequence: the command named for the evening cannot pass on that evening, so its later steps - basket and sweep - are skipped every time. Either the evening checklist runs against the previous session's warm-up, or the warm-up step reports without stopping and the morning append is the gate.                                                                                                                                                                                                                                                                                                   |
| **Iterate the operator-day draft**                                                     | 08    | [`DRAFT_OPERATOR_DAY.md`](DRAFT_OPERATOR_DAY.md) walks the JST clock from the close through the execution window to the next morning. Four passes so far, three of them **run** rather than read against the real catalog and a live TWS. The fourth, 2026-09-05, ran the day as `python -m copilot.live.day morning` and `day evening`: two commands, 3m00s and 2m11s, and the first replay comparison found the live ATR one bar behind the engine's in eight sessions of nine. Its own open questions are listed at the end of it.                                                                                                                                                                                                                                                                                                                                                                        |
| **Carry a triggered `next_close` decision into the session it belongs to**             | 08    | The rule decides on bar *t* and enters on bar *t+1*, which live is the session opening an hour after the evening command runs. `run_activation` hands the strategy bar *t*, the trigger sets a deferral, and the deferral dies with the process: no order is placed tonight, and tomorrow's warm-up never calls `on_bar` on bar *t*, so the entry is never made. Recorded as `deferred_atr` in the session record since 2026-09-05 so a trigger is at least visible. The design question is real - the replay fills at *t+1*'s close, the charter's window is its first hours, and the bracket's levels need a price the fill has not yet given - and it is stage-seven work that needs a frozen candidate to be worth deciding.                                                                                                                                                                             |
| **Wire alerting into the paths that need it**                                          | 08    | `live/alerting.py` exists and nothing calls it. The playbook names the callers: an alert on *any order whose status cannot be confirmed* at monitoring end, safe mode's *preserve state and alert*, and the day command's stopping failures. Each wiring is small; the judgement is which conditions earn `CRITICAL`, because that severity wakes a human and feeds the kill switch ([ADR-0023](decisions/0023-a-critical-alert-demands-acknowledgement.md)).                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| **Build the operator kill command**                                                    | 08    | Named as a GAP in the operator-day draft's evening table since 2026-09-04 and never given a row until 2026-09-09. The playbook's kill switch is *independent of the strategy* and its safe mode has five limbs, of which only *alert* now has code. It is also the last unmet line of the evening checklist, *verify the kill switch and remote broker access*. `set_trading_state(HALTED)` is the mechanism and is already proven; what is missing is the operator-facing command, the documented recovery checklist, and the unacknowledged-critical trigger ADR-0023 leaves without a consumer. **Pairs with the admission point**: one entry every decision source passes through, so adding a source is not the same as adding a bypass. Note `max_notional_per_order` is inert on IB and only `TradingState::HALTED` denies, so a gate built on per-order caps passes its tests and does nothing live. |
| **Report the evidence interval beside every walk-forward verdict**                     | 02    | ADR-0024 put an interval on the holdout, and `pool` reports one for the pooled trades, but an ordinary single-symbol verdict still reports only a fold count and a mean. The trades exist in `FoldResult.test_trade_details` at run time and are dropped before filing, which is also why attribution needs a fresh run rather than a query. Persisting dated trades in the verdict unblocks both.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| **Measure attribution against market and style factors**                               | 02    | No model involved and nothing new to collect but factor returns, for which Ken French's library is free. Regress each strategy's return series against the market and a small set of style factors and find out whether anything survives. It is a free read on every premise already measured and it answers a live question: the AAPL holdout returned 0.035 R at a t-statistic of 0.35 with exposure at 0.66, and nobody has asked how much of that residue is beta. Surfaced by [`DRAFT_ASSISTED_DECISIONS.md`](DRAFT_ASSISTED_DECISIONS.md), useful independently of it.                                                                                                                                                                                                                                                                                                                                |
| **Fill GLDM's thirteen 2018-2019 holes**                                               | 00    | A hole census on 2026-09-09 found GLDM missing thirteen sessions between 2018-06-28 and 2019-07-31, all of them after Databento's 2018-05-01 start and therefore fillable by `patch` once the store covers them. EEM, HYG and SCHX each miss 2017-03-20, which is before that start and is **not** fillable from this source. None of them is new; the census is.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| **Switch the account to Tiered in the Client Portal**                                  | 02    | Owner decision 2026-09-10: switch. The model is built and the revalidation is done - ADR-0025, `calibration/out/commission_revalidation_20260910.json`, 0 of 12 verdicts change their majority under Tiered. What remains is the owner's Client Portal action and confirming its effective date. Once the account is actually on Tiered, `SCHEDULE` moves to `TIERED` in a commit of its own and the verdicts are recomputed. Until then the pin stays on Fixed, because a verdict priced on a plan the broker is not running describes a different account.                                                                                                                                                                                                                                                                                                                                                 |
| **Run the account sweep under both commission plans**                                  | 02    | ADR-0025's revalidation is at the activations' research sizing of USD 1,000 per trade, which is 200 shares and the one point on the curve where Fixed is cheaper - by 0.0006 R. At the charter's USD 20 the same comparison favours Tiered by 0.064 R, and commission in R is **thirty times** larger at USD 20 than at USD 1,000. `calibration/account_sweep.py` is where ADR-0009's at-size question is asked properly and it has not been run under Tiered.                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| **Re-measure the access fee cap on 2026-11-02**                                        | 02    | ADR-0025's revisit trigger. Reg NMS Rule 610(c)'s cap falls from 0.003 to 0.001 per share on the first business day of November 2026 - already delayed once from November 2025, with SIFMA asking for a further extension in May 2026. If it takes effect, Tiered's all-in rate drops to about 0.0047 against Fixed's flat 0.005 and **the crossover disappears**: Tiered becomes cheaper at every size. If it is delayed again the ADR stays correct and this row's date moves.                                                                                                                                                                                                                                                                                                                                                                                                                             |
| **Size from settled cash, not headline equity**                                        | 06    | The charter requires it for a cash account and no code reads a settled figure. The paper account is MARGIN with USD 1M, so it **cannot** surface the bug ([paper fidelity limits](PAPER_CAMPAIGN.md)). Pairs with the settlement-rules item under the account group.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |

### Deferred by decision (2)

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

### Carrying cost, tracked (9 files)

Not work items - the standing bill. Reported by `python -m copilot.tools.upstream_delta`,
with the reasoning for each in `docs/UPSTREAM_DELTA.md`.

| Path                                                           | Ours      | Upstream since base | Risk                    |
| -------------------------------------------------------------- | --------- | ------------------- | ----------------------- |
| `crates/risk/src/python/engine.rs`                             | new file  | n/a                 | **cannot conflict**     |
| `crates/risk/src/python/mod.rs`                                | +2        | untouched           | quiet                   |
| `crates/adapters/interactive_brokers/src/historical/client.rs` | +36 -2    | untouched           | quiet                   |
| `crates/live/Cargo.toml`                                       | +1        | +3                  | touched                 |
| `python/pyproject.toml`                                        | +54       | untouched           | quiet                   |
| `.typos.toml`                                                  | +1        | untouched           | quiet                   |
| `crates/live/src/python/node.rs`                               | +14       | +166 -88            | **churning**            |
| `crates/adapters/interactive_brokers/src/data/core.rs`         | +16 -12   | +160 -64            | **churning, conflicts** |
| `python/nautilus_trader/{live,risk}/__init__.pyi`              | generated | -                   | regenerate, never edit  |

It grew from 2 files to 9 in one session, all to reach `set_trading_state`. Six of the
nine are additive-only or new files, which is the cheapest shape a delta can take; two
sit in files upstream is actively rewriting.

**Nothing is due.** Syncing is on demand only - the fork is deliberately held still while
development is active, so the conflict in `data/core.rs` is a forecast for a sync that has
not been scheduled. Upstreaming the two IB fixes and the `RiskEngine` binding would retire
three entries rather than carry them, and all three are additive capability or straight bug
fixes upstream would plausibly accept - but that opens a review front on someone else's
schedule, so it is deferred on the same reasoning.

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
5. **Spend the holdout - `spy-gap-fade-long-next-close` first** - the deliberate
   one-time act, now un-gated and built: `spend_holdout` scores the holdout as one more
   walk-forward fold with nothing chosen at spend time
   ([ADR-0014](decisions/0014-the-holdout-is-spent-as-one-more-fold.md), accepted
   2026-09-03). Only a `next_close` activation is spendable (ADR-0013), and **SPY
   first**: the charter trades ETFs before single names, and SPY's is the only verdict
   that leans on neither the survivor-chosen universe nor the hand-maintained splits
   table. **Unspent.** The command is the owner's to run.
6. **Two to four weeks on IB paper** with the guard enabled, for a candidate that
   survives step 5. This is the first time the breakers can fire; they cannot fire in a
   backtest by design.
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

Three-symbol measurement (delayed, 846s, 139-150 usable quotes each):

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
