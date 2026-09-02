# Research

How an experiment is run, and what it has to survive. The gate this feeds is the first of
the three in [`../CHARTER.md`](../CHARTER.md).

## The central rule

**Optimisation does not create an edge. It selects among the things you tried.** The more
ideas and configurations tested, the more likely one looks excellent by chance.

That is not a caution, it is arithmetic: the best score obtainable from pure noise grows
with the number of trials. Everything below exists to keep the count honest and to reduce
the freedom to choose after seeing results.

The Probability of Backtest Overfitting framework was built to estimate exactly this. Not
every advanced statistic is needed on day one, but **a complete trial ledger is**.

## The experiment card

Written before any backtest runs.

```yaml
experiment_id: EXP-YYYY-NNN
question:          What repeatable behaviour is being tested?
rationale:         Why might it persist after costs and competition?
universe:          Exact symbols, or the point-in-time membership rule
data_cutoff:       Timestamp after which no development data may be used
signal:            Exact formula and timestamp availability
entry:             Exact order timing and type
exit:              Invalidation, profit, time and emergency rules
holding_horizon:   Expected and maximum duration
position_sizing:   Formula and caps
cost_model:        Commission, spread, slippage, impact - and the account size assumed
account_size:      Equity and planned risk fraction the cost model assumes  # ADR-0009
parameter_space:   Complete list or range, declared before testing
benchmarks:        Cash, buy-and-hold, and the simpler nested rule
failure_tests:     Regimes and perturbations expected to break it
advance_gates:     Numeric criteria, declared before results
owner_decision:    Reject, revise as a new experiment, or freeze
```

If the rationale is "this indicator combination made money", reject it before writing code.
A useful rationale names a behavioural bias, a risk transfer, an institutional constraint,
or a market mechanism.

`account_size` is not in the original template. It is here because a cost model without one
is not a cost model - see [ADR-0009](../decisions/0009-cost-is-modelled-at-the-target-account-size.md).

## Data

Three layers, all versioned:

1. **Raw** - immutable vendor files exactly as received.
2. **Clean** - normalised timestamps, symbols, sessions, adjustment fields.
3. **Research** - features and labels built only from information available at each
   decision time.

Record vendor, request parameters, download time, timezone, adjustment convention,
checksum, code commit and known defects.

Handle explicitly: splits, dividends, mergers, symbol changes, delistings; point-in-time
universe membership; holidays, early closes and DST transitions; halts, missing and
duplicate observations; **adjusted data for signals versus tradable unadjusted prices for
orders**; and the timestamp at which each value became available.

**Do not use today's surviving stocks to represent a historical universe.** The current
20-symbol catalog does exactly this - it is today's large caps backfilled to 2005 - and is
survivor-biased as a result. Known, tracked, not yet corrected.

What this fork has already found in vendor data, as a calibration of how much of the above
is real rather than ceremonial:

- Bars returned on days the US market was closed, with plausible prices and nine-figure
  volume, and nothing marking them as phantom.
- An adjusted OHLC set where 22% of rows carry a price outside their own high and low.
- Currency tags on US large caps reading ARS, MXN, CLP, THB, GBP and CHF.
- Split-adjusted prices that silently break per-share commission modelling.

## Partitions

**Never shuffle a financial time series.**

| Partition     | Purpose                                    | Permitted use             |
| ------------- | ------------------------------------------ | ------------------------- |
| Discovery     | Build and debug the hypothesis             | Inspect repeatedly        |
| Validation    | Compare declared configurations            | Limited, logged selection |
| Final holdout | Estimate selected-candidate behaviour      | **Open once**             |
| Paper forward | Observe real-time decisions and operations | No performance tuning     |
| Live canary   | Measure actual execution at tiny risk      | No scaling until review   |

Reserve the most recent **15% to 20%** of history as a locked final holdout. Within the
earlier data use expanding or rolling walk-forward folds; where labels or holding periods
overlap, purge the overlap and embargo at least as long as the maximum overlap.

**Once viewed, the holdout is development data for every future decision.** There is no
partial reopening.

> **True of the code as of 2026-09-02**
> ([ADR-0012](../decisions/0012-the-holdout-is-carved-at-2022-01-01.md)): bars from
> 2022-01-01 (18.99% of the catalog) are withheld before the gate sees them, every
> verdict names what was withheld, and `holdout_spent: false` is backed by a real
> reservation. No tool for spending it exists yet, deliberately. The entry-timing gate
> on the spend is resolved
> ([ADR-0013](../decisions/0013-entry-timing-is-evaluated-as-a-bracket.md)): a holdout
> is spent only on a `next_close` activation, so the one-time test cannot be burned on
> fill semantics the charter rejects.

## Configuration search

Declare a small, coarse grid before running anything, and log every point rather than the
winner.

- Prefer a **broad plateau** of acceptable neighbours over a single best point.
- Prefer the simpler nested rule when complexity adds only a little.
- Select on a balanced scorecard, never on highest return, Sharpe or win rate alone.
- **Never narrow the grid around a peak after seeing the holdout.**
- Count every manual idea variation as a trial.

Size the space against the data, not only against the deflation statistic. A search whose
every setting produces no evaluable folds returns the *absence* of a verdict rather than a
verdict, and that reads as a bug rather than a result. In this repository the searched axes
live in `SEARCH_SPACE` beside each strategy, with a test pinning the ceiling so an axis
cannot be widened without re-counting events
([ADR-0005](../decisions/0005-setup-is-code-activation-is-data.md)).

## Baselines and falsification controls

Every experiment compares against: cash or no-trade; buy-and-hold of the same instrument;
a risk-matched passive benchmark; **a simpler version of the same rule**; a delayed signal;
a randomised or permuted signal with similar trading frequency; and a sign-inverted rule
where that is economically meaningful.

If a complicated strategy cannot beat its simpler nested version after costs at comparable
risk, use the simpler one or reject both.

## Honest execution

The signal at the close of bar `t` cannot fill at that same close unless an order could
genuinely have been calculated and accepted before it.

```text
observe completed bar t -> calculate signal after close
  -> create order intent -> trade no earlier than the next eligible session
```

> **Resolved as a bracket
> ([ADR-0013](../decisions/0013-entry-timing-is-evaluated-as-a-bracket.md), 2026-09-02).**
> Next-open entry is not expressible on the daily-bar replay, so the gap fade runs at both
> bounds that are: `signal_close` (a market-on-close assumption - not lookahead, but it
> assumes the closing print is transactable at the level just used to decide; diagnostic
> only) and `next_close` (next session's close; charter-compliant and the only mode a
> holdout may be spent on). Verdicts are never comparable across timing modes.

Model commission and regulatory fees; half or full spread depending on order policy;
slippage that worsens with volatility and size; gap risk between decision and execution;
unfilled and partial limit orders; cancellations, rejections, rounding and tick sizes;
dividends, splits and cash availability. Run base, stressed and severe cost cases. **A
strategy that works only at zero cost is not a candidate.**

## Scorecard

| Area           | Metrics                                                                          |
| -------------- | -------------------------------------------------------------------------------- |
| Return         | Total, CAGR, average and median trade, expectancy                                |
| Risk           | Max drawdown, recovery time, volatility, downside deviation, worst day and trade |
| Risk-adjusted  | Sharpe, Sortino, Calmar, each labelled with frequency and assumptions            |
| Trading        | Trade count, win rate, average win and loss, profit factor, holding time         |
| Implementation | Turnover, exposure, concentration, estimated cost, capacity                      |
| Stability      | Walk-forward folds, regime splits, parameter neighbourhood, bootstrap interval   |
| Attribution    | Return by symbol, year, regime, and largest contributors                         |

**Win rate alone is nearly meaningless.** A 70% win rate loses money if the losses are
large enough. Use net expectancy, where `C` is average all-in cost per trade:

```text
E = p_w * avg_win - (1 - p_w) * avg_loss - C
```

## Robustness battery

A candidate must survive: costs at 1x, 2x and severe; execution delayed one extra bar;
neighbouring parameters; bull, bear, high-volatility and low-volatility periods; removal of
the best trade and best month; removal of the best instrument; missing and stale data;
alternative start dates and rebalance days; walk-forward rather than one full-period run;
and a dependence-aware resampling such as a block bootstrap.

Investigate any result where a small change flips a strong profit into a strong loss.

**Report concentration explicitly.** A fold-level pass rate cannot show it, because a fold
passes on its mean too. The gap fade illustrates why: it majority-passes 39 folds while two
years out of twenty produce 54% of all R, eight of twenty are negative, and two of its three
symbols have no edge at all since 2021. `python -m copilot.calibration.cost_impact` reports
this alongside cost.

## Gates

### Research gate

- Economic rationale written before results.
- Every configuration and failed experiment logged.
- Net expectancy positive in validation **and** in the locked holdout.
- A majority of walk-forward folds profitable after base costs.
- Still positive under doubled costs.
- At least 70% of immediate parameter neighbours share the sign of net expectancy.
- No single trade contributes more than 20% of total net profit.
- Removing the best month does not erase the full-period profit.
- Drawdown and recovery fit the predeclared risk budget.
- The selected rule beats its simpler nested alternative by enough to justify complexity.
- For a large search, a multiple-testing correction is applied and documented.

### Evidence sufficiency gate

**Do not use a fixed closed-trade count.** Predeclare a requirement based on scheduled
decision points, holding-period overlap, and **effective** rather than raw sample size.
Require coverage across materially different regimes and report dependence-aware intervals.

For a weekly or monthly strategy, do not raise turnover to reach a trade count. If the
effective sample is too small or the intervals too wide: extend the history, forward test
for longer, simplify the claim, or reject.

### Cost-at-size gate

See [`RISK.md`](RISK.md). A premise only counts if it survives at the account that would
trade it.

### Decision gate

Exactly three outcomes:

1. **Reject** - archive the evidence and the reason.
2. **Revise** - allocate a new experiment ID *before* changing logic, data, universe or
   parameters.
3. **Freeze** - package it and advance without performance-driven changes.

## The frozen package

```text
strategy_id/
  hypothesis.md          experiment_registry.csv    data_manifest.json
  config.yaml            config_hash.txt            code_commit.txt
  dependency_lock.txt    tests/                     discovery_results/
  validation_results/    holdout_results/           decision_record.md
```

The same strategy ID and configuration hash appear in backtest, paper and live logs.

In this repository the activation registry and the verdict records under
`copilot/strategies/` carry part of this already: the search space as declared at the time,
the seeded parameters, the fold geometry and the bar range. The experiment registry, the
data manifest and the decision record do not exist yet.

## Checklist

- [ ] Experiment card written **before** results, including `account_size`
- [ ] Data and code hashes recorded
- [ ] Final holdout still locked
- [ ] Full parameter space recorded, winners and failures alike
- [ ] Costs applied at the target account size, with next-eligible execution
- [ ] Benchmarks and null controls run
- [ ] Concentration reported, not just the mean
- [ ] Decision recorded as reject, revise or freeze
