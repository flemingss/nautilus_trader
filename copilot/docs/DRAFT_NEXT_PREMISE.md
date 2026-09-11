# Draft: choosing the next premise

**Status: working draft, first pass 2026-09-11. Not governance.** The
[charter](CHARTER.md), the [research playbook](playbook/RESEARCH.md) and the ADRs decide; this
file sets out the choice for the owner, who picks. It serves the roadmap row *Pick the next
premise to research*.

## What the choice is constrained by

Three things narrow it before any idea is scored.

**The charter.** Long only, daily, liquid US ETFs first, and large caps only once the pipeline
handles point-in-time universes - which it does not, and which Norgate prices at USD 630 a year
([ADR-0015](decisions/0015-databento-is-the-intraday-source-only.md)). So the next premise
trades the ETFs already onboarded, or a handful more.

**The attribution.** [ADR-0026](decisions/0026-attribution-is-measured-per-trade.md) regresses
each trade's excess R on its own leverage times the factor returns **over its own holding
window**. For a long position in a broad index fund, the trade's R is close to that leverage
times the market's return over the same window. The fit then returns a market loading near one
and **alpha near zero by construction, however well the entries were timed**: timing skill shows
up as larger market returns inside the windows, and the regression books that to the loading.
The gap fade on SPY could not have shown alpha for this reason alone; on AAPL it could, and did
not. So every long-only premise on the onboarded ETFs is a *timing* premise, and the current
attribution cannot credit timing.

**The missing control.** The playbook already names the test that can:
*"a randomised or permuted signal with similar trading frequency"* among the baselines every
experiment is compared against, and *"Benchmarks and null controls run"* on its checklist. No
code builds it. A random-entry null - the same instrument, the same number of trades, the same
holding lengths, entry dates drawn at random from the development window - answers the timing
question directly: did the premise's windows earn more than windows chosen by chance? That is a
stage 04 build, and it is a prerequisite for judging any premise on this universe.

## How much evidence a premise can produce

The filed AAPL next-close walk-forward had a standard error of 0.048 R per trade over 418
trades. Scaled by the square root of the count, a premise with 200 trades over the same
2005-2021 window has one of about 0.07 R, so a 90% interval about 0.11 R either side of its
mean. **A premise whose true edge is below roughly 0.1 R per trade cannot clear zero on this
history**, and the holdout adds perhaps a quarter as many trades again. Pooling SPY with SCHX or
XLF does not help: they are close to the same exposure, and ADR-0031 already counts them as one
body of evidence.

## One look at the development data, recorded as a look

Descriptive, from the SPY catalog over 2005-2021, computed 2026-09-11 while writing this. It is
a discovery-partition observation, not a result; the playbook counts it as a look, and it is
written here so the trial ledger does.

| Measure                                             | Value                       |
| --------------------------------------------------- | --------------------------- |
| Turn-of-month holds, last session through the third | 203, mean 24.1 bps          |
| Every four-session SPY window                       | mean 15.4 bps               |
| Difference, and its standard error                  | 8.7 bps, 14.3 bps; t = 0.61 |
| Overnight return, close to next open                | mean 2.75 bps a night       |

The turn-of-month gap is the right sign and **indistinguishable from noise on this history**,
which is what the evidence arithmetic above predicts for an effect this size. The overnight mean
is under half of what two Tiered minimums cost on a USD 1,000 position.

## The shortlist

| Premise                     | Mechanism                                                                                                                                             | Trades, 2005-2021                                               | Cost at size                                                                                                   | Attribution fit                                                                                     | Reading                                                                                                                                 |
| --------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| **Turn of the month, SPY**  | Month-end and month-start cash flows: payroll, pension contributions, fund rebalancing (Ariel 1987; Lakonishok and Smidt 1988; McConnell and Xu 2008) | About 200: one hold a month, the last session through the third | Two orders a month; the lowest turnover here                                                                   | Timing: needs the null control                                                                      | **Recommended first**                                                                                                                   |
| Overnight holding, SPY      | The equity premium accrues mostly between close and open (Cliff, Cooper and Gulen 2008; Lou, Polk and Skouras 2019)                                   | About 4,300: every session                                      | Two orders a session. At a USD 1,000 position, Tiered's two minimums alone are 7 bps a night, above the effect | Timing                                                                                              | **Reject on cost arithmetic**, before code                                                                                              |
| Trend across the ETFs       | Time-series momentum: hold each fund above its ten-month average (Faber 2007; Moskowitz, Ooi and Pedersen 2012)                                       | A few switches a year per fund; correlated across funds         | Low turnover                                                                                                   | Timing, and TLT, GLDM and HYG are not spanned by equity factors, so alpha would be measured wrongly | Second, after a multi-asset factor set                                                                                                  |
| Two-day mean reversion, SPY | Liquidity provision after short selling pressure (Connors-style RSI(2))                                                                               | Several hundred                                                 | Moderate                                                                                                       | Timing                                                                                              | **Deprioritise**: short-horizon reversal is the rejected gap fade's family, and V1-31 found filtered RSI(2) produced no evaluable folds |

### Why the turn of the month

- **The mechanism is institutional, not behavioural noise.** It names who trades, when and why,
  which is the rationale the playbook asks for before any code.
- **Its entry does not depend on the price it fills at.** The window is a calendar date known in
  advance, so a market-on-close entry on the session before it is honest. That sidesteps the
  open decision on how a triggered next-close entry is placed, which the gap fade needed.
- **Its turnover is the lowest**, which matters at the account size where commission decided
  the gap fade.
- **It is not the rejected family.** The gap fade was short-horizon reversal; this is a flow
  effect.

### What it has to survive, stated now

- **The null control, built first.** Random four-session SPY windows over the same years, with
  the same stop and sizing, as the distribution the premise's mean is read against.
- **A protective stop, because the playbook sizes from one.** A four-session hold needs an
  executable stop to size against; a stop truncates the payoff, which ADR-0026 notes flatters
  alpha, so the null control must carry the same stop.
- **Decay.** The effect is documented as weaker in recent decades. The walk-forward's recent
  folds and the holdout are where that shows.
- **The evidence arithmetic above.** About 200 trades cannot show an edge below about 0.1 R.
  The card should say what the premise is expected to earn, and why that is above the bar.

## What the owner decides

1. **Build the random-entry null control first?** Recommended: yes. Without it no premise on this
   universe can be judged, and the playbook already requires it.
2. **Which premise gets the first experiment card?** Recommended: the turn of the month on SPY.
3. **Its declared effect size.** Zero is the honest default and a valid declaration
   ([ADR-0031](decisions/0031-evidence-and-attribution-after-the-audit.md)); raising it says an
   edge below the bar is not worth trading at this account.
4. **Whether overnight holding is logged as a rejected idea** on cost arithmetic alone.
   Recommended: yes, so the trial ledger counts it.

## To argue with next time

- Should the null control replace the four-factor alpha test for timing premises, or sit beside
  it? Beside it, probably: alpha stays the test for anything single-name.
- Is a multi-asset factor set worth building for the trend premise, or is the null control
  enough on its own?
