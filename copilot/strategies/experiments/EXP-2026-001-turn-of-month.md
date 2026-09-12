# EXP-2026-001: the turn of the month, SPY

**Written 2026-09-11, before any backtest of this premise ran.** That order is the playbook's
([`RESEARCH.md`](../../docs/playbook/RESEARCH.md)), and it is the point: a card written after
results is a description, not a prediction. The owner picked this premise on 2026-09-11 from the
shortlist in [`DRAFT_NEXT_PREMISE.md`](../../docs/DRAFT_NEXT_PREMISE.md).

**This file starts the experiment registry** the research playbook has asked for since it was
adopted. One card per experiment, numbered, never edited once results exist; a revision is a new
id, which is what "revise" means at the decision gate.

## The card

```yaml
experiment_id: EXP-2026-001
question: >
  Does holding SPY across the turn of the month - from the close of a month's last session
  through the close of the third session of the next - earn more than holding it at times
  chosen by chance?
rationale: >
  An institutional flow effect, not a chart pattern. Salaries, pension and retirement
  contributions, coupon and dividend reinvestment and index fund rebalancing cluster at month
  end and month start, which is a predictable, calendar-driven demand for equities from buyers
  who are not trading on information. Documented since Ariel (1987) and Lakonishok and Smidt
  (1988); McConnell and Xu (2008) find the effect persists out of sample and is not explained by
  size, risk or the January effect. The mechanism is why it might survive competition: the flows
  are mandated rather than opportunistic, so arbitraging them away requires taking the other
  side of money that arrives regardless.
universe: SPY.ARCX only. One instrument, because SCHX and XLF are close to the same exposure
  and ADR-0031 already counts them as one body of evidence rather than three.
data_cutoff: 2026-01-01, the evaluation window's end (ADR-0017). The holdout is SPY's own
  boundary, 2022-01-01 (ADR-0012, ADR-0020), withheld before the gate sees a bar.
signal: >
  A calendar rule, known in advance and independent of price: today is a trading session and no
  trading session remains in this calendar month. No indicator enters the trigger.
entry: >
  Market-on-close on that session. The decision does not depend on the price it fills at, so a
  close fill is honest rather than the optimistic bound ADR-0013 warns about for the gap fade.
  This premise therefore does not use ADR-0032's marketable-limit path.
exit: >
  Whichever comes first: the close of the hold_sessions-th session after entry, or the protective
  stop. There is no profit target: the premise is a window, and a target would truncate the very
  distribution being measured.
holding_horizon: three sessions expected, three the maximum by rule.
position_sizing: >
  The playbook's R = A * r from an executable stop at stop_atr times the ATR at entry, floored to
  whole shares, capped by the notional cap and, on a cash account, by settled cash.
cost_model: >
  The pinned snapshot, spread at p95 per side plus the IBKR Pro Fixed schedule with its per-order
  minimum and the regulatory pass-through (ADR-0011, ADR-0019, ADR-0025).
account_size: >
  Scored at the research R-unit of USD 1,000 per trade, then swept across account sizes, because
  ADR-0009 makes cost at the target account the deciding constraint and two orders a month is the
  lowest turnover on the shortlist.
parameter_space:
  hold_sessions: [2, 3, 4]
  stop_atr: ["1.5", "2.5"]
  # Six points, declared here in full. Every point is logged, winners and failures alike.
fold_geometry:
  train_bars: 1008    # four years, about 48 holds, so a parameter set can be eligible at all
  test_bars: 252      # one year, about 12 holds
  purge_bars: 5
  min_trades: 20      # in-sample eligibility, per parameter set
  fold_min_trades: 6  # a test window's score does not count below this
  # Declared because it is a choice, not a default: the gap fade's 252-bar training window holds
  # about twelve of these trades, and a 20-trade floor over it would make every parameter set
  # ineligible - a search that returns the absence of a verdict rather than a verdict.
benchmarks:
  - cash, or no trade
  - buy and hold SPY over the same window
  - the random-entry null control: the same instrument, the same number of entries, the same
    holding rule and stop, with entry dates drawn at random from the development window
  - a delayed signal: the same rule entering one session late
failure_tests:
  - costs at 1x, 2x and severe
  - the 2008-2009 and 2020 regimes taken separately
  - removal of the best month and the best single trade
  - neighbouring parameters: the whole declared grid's sign
advance_gates:
  - the walk-forward's majority of folds profitable after base costs
  - the net evidence interval above the declared bar, not only its point estimate (ADR-0024)
  - the null control reading beats_chance at the declared one-sided 0.10
  - no single trade contributing more than 20% of net profit
  - still positive at doubled costs
  - the account sweep crossing zero at or below the account that would trade it
minimum_effect_r: "0"
  # Zero, and declared before the first walk-forward is filed (ADR-0031). Costs are already in
  # the fill, so better than not trading is the honest bar. The interval requirement is what
  # makes zero a real test rather than a formality.
owner_decision: pending
```

## The stop caps the loss only when price trades through it

Measured on the replay while building the rule, 2026-09-11, and recorded here because it changes
what an R means in this experiment.

| The session                                          | The fill               | The loss    |
| ---------------------------------------------------- | ---------------------- | ----------- |
| Opens 99.50, dips to 96 through a 97 stop, closes 99 | 97.00, the trigger     | exactly 1 R |
| Opens 85 and never trades near 97                    | 85.00, the bar's close | 5 R         |

On daily bars the engine has no intrabar path, so a gap through the stop fills where the market
actually was. That is honest rather than pessimistic, and it is the reason
[`RISK.md`](../../docs/playbook/RISK.md) sizes with a **stressed per-share allowance for gaps,
slippage and fees** on top of the stop distance. `size_from_levels` does not carry that allowance
today - for this premise or for the gap fade - so **planned risk is the stop distance alone, and a
gapped stop loses more than the one R the denominator implies**. The roadmap carries that as its
own row; this card's results are read knowing it, and the failure tests below include the regimes
where gaps cluster.

**About 200 trades.** One hold a month over 2005-2021 is roughly 204, and SPY's filed
walk-forwards put the standard error of a per-trade mean near 0.048 R at 418 trades, so near
0.07 R here: a 90% interval roughly 0.11 R either side. **An edge below about 0.1 R per trade
cannot clear zero on this history.** If the premise is real but small, the honest outcome is
`insufficient_evidence`, not a pass - and that is a result, not a failure of the run.

**One discovery look has been taken, and it is recorded.** Over 2005-2021, the 203 turn-of-month
windows returned 24.1 bps on average against 15.4 bps for an average four-session SPY window: a
difference of 8.7 bps with a standard error of 14.3, t = 0.61. That is the right sign and
indistinguishable from chance, which is exactly what the arithmetic above predicts for an effect
this size. It is a look at discovery data, it counts as one trial, and it is not evidence.

**Attribution will not credit this premise.** ADR-0026 regresses each trade on the factor returns
of its own window, so a long SPY position returns a market loading near one and an alpha near
zero however well its entries were chosen. That is why the null control is an advance gate here
and alpha is not.

## The holdout was looked at by accident, 2026-09-11

**Recorded because it happened, not because it is defensible.** The first run of the
randomised-signal control drew its window from the activation's own holdout boundary, and this
activation declares none - it inherits the shared 2022-01-01 pin
([ADR-0012](../../docs/decisions/0012-the-holdout-is-carved-at-2022-01-01.md)). The code read
"no boundary of its own" as "nothing withheld" and ran over **2005-01-03 to 2025-12-31**, 5,283
sessions, holdout included.

What was seen, and it cannot be unseen: at the seeded parameters over that whole span, the
premise showed **251 trades at +0.1080 R per trade net**, against a null mean of +0.0381 R over
500 random draws, percentile 91.8, one-sided p 0.0838. The record was deleted and the reading is
**void**: it is not evidence for this premise, and quoting it as though it were would be the
failure the whole holdout mechanism exists to prevent.

The fix is in `null_run.development_bars`, which now carves through the gate's own
`carve(bars, holdout_start=...)`, and in `test_null_run.py`, which pins the window for an
activation with no boundary of its own.

**Resolved 2026-09-12 as [ADR-0033](../../docs/decisions/0033-the-turn-of-month-single-use-test-is-forward.md):
this premise's single-use test is forward, not carved.** The owner concurred with re-pinning the
boundary earlier, and the arithmetic refused it. The charter's band reserves 15-20% of the
window, which over SPY's 5,283 in-window bars admits boundaries from 2021-11-01 (19.80%) to
2022-11-01 (15.03%) and no others. Every holdout any of them carves lies wholly inside
2005-01-03 to 2025-12-31 - the span the void run read - so moving the pin earlier enlarges the
holdout with bars that were *also* in the aggregate and buys no unread history. A re-pin would
have produced a holdout that reads as pristine in the record and is not.

So the carved holdout is **compromised**: not spent in ADR-0021's sense, because nothing was
decided from it and nothing was selected against it, but never to be quoted as a clean
out-of-sample pass. The boundary stays at the shared pin. The single-use test is the forward
span, which `carve` clips and the void run provably never touched - 174 bars already in the
catalog past 2026-01-01, and the paper clock from 2026-09-15. `spend_holdout` now refuses this
activation **by name**: today ADR-0013 refuses it anyway as a `signal_close` activation, but that
protection is accidental and disappears the day this premise is re-expressed on intraday bars.

## Results, as of 2026-09-11

Development window only, 2005-01-03 to 2021-12-31. The holdout is untouched by these two runs -
see the incident above for the one that was not.

| Gate                                                  | Reading                                                                                                                                                              |
| ----------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Walk-forward majority, after base costs               | **Pass**: 9 of 12 folds, mean +0.100541 R per trade over 144 scored trades                                                                                           |
| Net evidence interval above the declared bar of zero  | **Fail**: 90% interval [-0.003868, +0.194246] straddles zero; 111.7 effective trades, standard error 0.061046                                                        |
| Null control, one-sided p at or below 0.10            | **Pass**: premise +0.137807 R over 203 trades at the seeded parameters, against a null mean of +0.037457 R over 500 draws; percentile 96.0, p 0.0419, `beats_chance` |
| No single trade over 20% of net profit                | Not yet measured                                                                                                                                                     |
| Still positive at doubled costs                       | Not yet measured                                                                                                                                                     |
| Account sweep crossing at or below the traded account | Not yet measured                                                                                                                                                     |

**What that combination means.** The entry dates are doing something: holding SPY across the turn
of the month earned about 0.14 R per trade where the same rule on random sessions earned about
0.04, and a gap that size arose by chance in 21 of 500 draws. But **the premise's own interval
does not clear zero**, which is the gate ADR-0024 exists for and the one the card predicted would
bind: 144 scored trades, 111.7 of them effective after calendar clustering, cannot resolve an edge
near 0.1 R. The two readings are not in conflict. The control asks whether the sessions were
chosen well; the interval asks whether this much evidence can tell. The answers are yes and not
yet.

So the premise is **not rejected and not advanced**. What the playbook offers here is exactly what
it says for a thin sample: extend the history, forward test for longer, simplify the claim, or
reject. It does not offer spending the holdout on it, and under ADR-0033 there is no carved
holdout left to spend: this premise earns its out-of-sample evidence forward.

## Trial ledger

| #   | Date       | What was run                                                                              | Result                                                                                                                                                          |
| --- | ---------- | ----------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | 2026-09-11 | Descriptive look at turn-of-month windows against all four-session windows, SPY 2005-2021 | +8.7 bps, t = 0.61; recorded as a look                                                                                                                          |
| 2   | 2026-09-11 | Walk-forward, development window only, six declared points, 12 folds                      | 9 of 12 folds passed, +0.100541 R per trade, interval [-0.003868, +0.194246], **straddles zero**                                                                |
| 3   | 2026-09-11 | Null control, **run over the holdout by mistake**, 500 replicates                         | +0.1080 R against a null of +0.0381, p 0.0838; **void**, and a look at the holdout                                                                              |
| 4   | 2026-09-11 | Null control on the carved window, 2005-2021, 500 replicates, seed 20260911               | +0.137807 R over 203 trades against a null of +0.037457; percentile 96.0, p 0.0419, `beats_chance` (`out/null_control_spy-turn-of-month_20260912T002452Z.json`) |
