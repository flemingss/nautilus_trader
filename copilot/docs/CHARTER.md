# Charter

**Start here.** What this system is for, what counts as success, and which gate a candidate
is standing at. Everything else hangs off this page.

The objective is not a configuration that happened to win in a backtest. It is a repeatable
process that finds plausible edges, tries hard to disprove them, translates a frozen
strategy into reliable code, and risks money only when the statistical, operational and
execution evidence pass **separate** gates.

Research and engineering notes, not financial advice. Automated trading loses money, stops
fail or fill through, and past behaviour need not recur. Broker, tax, market-data and
account rules change and must be confirmed with the broker and with qualified
professionals.

## Where to go next

| You are                               | Read                                               |
| ------------------------------------- | -------------------------------------------------- |
| Orienting, or picking what to work on | This page, then [`ROADMAP.md`](ROADMAP.md)         |
| Asking why something is the way it is | [`decisions/`](decisions/README.md)                |
| Setting up an account or broker       | [`playbook/PREFLIGHT.md`](playbook/PREFLIGHT.md)   |
| Running an experiment                 | [`playbook/RESEARCH.md`](playbook/RESEARCH.md)     |
| Sizing a position, setting risk       | [`playbook/RISK.md`](playbook/RISK.md)             |
| Running a session, or something broke | [`playbook/OPERATIONS.md`](playbook/OPERATIONS.md) |
| Writing code in this repository       | [`../AGENTS.md`](../AGENTS.md)                     |
| Taking upstream changes               | [`MAINTENANCE.md`](MAINTENANCE.md)                 |

## Operating model

The binding constraint is not compute. It is a beginner-stage operator with a small
account, based in Japan, able to watch one to two hours of a session that runs overnight
locally.

| Dimension        | Default                                                                                                                     |
| ---------------- | --------------------------------------------------------------------------------------------------------------------------- |
| Instruments      | Liquid US-listed ETFs first; large caps only once the pipeline handles point-in-time universes and corporate actions        |
| Direction        | Long only                                                                                                                   |
| Leverage         | None                                                                                                                        |
| Signal frequency | Daily                                                                                                                       |
| Decision time    | After the US close, which is morning in Japan                                                                               |
| Execution        | A predeclared window in the first one to two hours of the **next** session. Never assume the opening print                  |
| Holding period   | Days to weeks                                                                                                               |
| Orders           | Time-bounded DAY limit or marketable-limit, with a declared maximum concession, partial-fill rule and cancellation deadline |
| Not yet          | Shorts, options, penny stocks, leveraged or inverse ETFs, extended hours, earnings events, intraday, sub-minute             |

The NYSE core session is 09:30 to 16:00 New York time, which is 22:30 to 05:00 JST on US
daylight time and 23:30 to 06:00 JST on standard time. **Use an exchange calendar and
timezone-aware code**, never hard-coded JST hours - `copilot/data/calendar.py` is the
calendar, and it exists because the data vendor emits bars on days the market was shut.

### Modes are different products

| Mode                    | Allowed now                | Rule                                                             |
| ----------------------- | -------------------------- | ---------------------------------------------------------------- |
| Research and backtest   | Yes                        | No broker connection, no orders                                  |
| Paper, supervised       | After research gates       | You are present and can acknowledge alerts                       |
| Paper, unattended       | After supervised stability | Safe mode, alerting, restart and reconciliation drills must pass |
| Live, supervised canary | After all gates            | Tiny risk, no scaling during the evidence period                 |
| Live, unattended        | No                         | Requires repeated failure drills and a written response policy   |

See [ADR-0006](decisions/0006-ops-progression.md) for the deployment progression that
carries these, and [`playbook/OPERATIONS.md`](playbook/OPERATIONS.md) for the
monitoring-end policy, which is not optional.

## Three kinds of success, gated separately

1. **Research success.** A simple hypothesis survives unseen data, conservative costs,
   neighbouring parameters, alternative regimes and selection-bias controls.
2. **System success.** The code makes the intended decisions, routes correct orders,
   enforces risk limits, recovers safely and reconciles with the broker.
3. **Trading success.** Small live results stay inside the range research predicted, after
   real fees, slippage and operational effects.

**A candidate advances only when every applicable gate passes.** Paper profit cannot repair
weak research. A good backtest cannot excuse broken order handling. Early live profit
cannot prove a durable edge.

## The two tracks

```text
Track A: prove the candidate
  question -> falsifiable hypothesis -> frozen data and design
    -> discovery -> validation and robustness -> locked holdout -> freeze

Track B: productionise the frozen candidate
  unit and scenario tests -> deterministic replay -> broker connection tests
    -> supervised paper -> unattended drills -> tiny live canary -> scale or retire
```

**Do not mix the tracks.** Strategy logic is never changed to improve paper or live P&L. A
material logic or parameter change is a new experiment and resets everything downstream of
it.

## Gates at a glance

| Gate                 | Where                                              | One-line test                                                  |
| -------------------- | -------------------------------------------------- | -------------------------------------------------------------- |
| Research             | [`playbook/RESEARCH.md`](playbook/RESEARCH.md)     | Would this survive if someone tried to break it?               |
| Evidence sufficiency | [`playbook/RESEARCH.md`](playbook/RESEARCH.md)     | Is the effective sample big enough to say anything?            |
| Cost at target size  | [`playbook/RISK.md`](playbook/RISK.md)             | Does it survive at the account that would trade it?            |
| System               | [`playbook/OPERATIONS.md`](playbook/OPERATIONS.md) | Does the code do what the research says?                       |
| Paper                | [`playbook/OPERATIONS.md`](playbook/OPERATIONS.md) | Correct behaviour, and forward results inside predicted ranges |
| Live canary          | [`playbook/OPERATIONS.md`](playbook/OPERATIONS.md) | Tiny risk, no scaling, written pause and retirement rules      |

Only three decisions are ever allowed at a gate: **reject**, **revise** (which creates a
new experiment ID), or **freeze**.

## Known state of this charter against the code

Written down because the charter was adopted after the code existed, and four conflicts
came with it. Each is tracked in [`ROADMAP.md`](ROADMAP.md).

| Charter says                                          | Code does                                                          | Status                                                                                                                                            |
| ----------------------------------------------------- | ------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| Reserve the most recent 15-20% as a locked holdout    | The carve withholds bars from 2022-01-01 before the gate sees them | **Resolved as [ADR-0012](decisions/0012-the-holdout-is-carved-at-2022-01-01.md).** 18.99% reserved, pinned by date, unspent                       |
| Trade no earlier than the next eligible session       | The gap fade fills at the signal bar's close                       | **Resolved as [ADR-0013](decisions/0013-entry-timing-is-evaluated-as-a-bracket.md).** Both expressible bounds run; only `next_close` is spendable |
| Do not use today's survivors as a historical universe | The 20-symbol universe is today's large caps                       | **Survivor-biased.** Known, not yet corrected                                                                                                     |
| Cost must hold at the traded account size             | Research default risks USD 1,000 per trade                         | **Resolved as [ADR-0009](decisions/0009-cost-is-modelled-at-the-target-account-size.md).** The premise is negative at the charter's account size  |

## Principles

- Search for robustness, not the highest score.
- A configuration is evidence only when every attempt is counted.
- The locked holdout is a one-time test, not a tuning surface.
- Paper trading validates behaviour far more than it validates fill quality.
- Under USD 10,000, skipped trades and small whole-share positions are features.
- Strategy risk controls and system safety controls are **separate**, and the kill switch
  does not live inside the strategy.
- Every order traces to a frozen decision, code version, data version and configuration.
- Broker resets, bad data, rejects, disconnects and restarts are normal test cases.
- Retirement is a normal outcome. Archive the evidence rather than quietly editing a
  strategy until it looks profitable again.
- **Going live is optional.** Staying on paper until the evidence is credible is a
  successful outcome.

The question that matters is not "which configuration won". It is: after counting every
attempt, using only information available at the time, charging realistic costs at the size
we would actually trade, testing unseen periods and adverse conditions, and reproducing the
decision through the live system - is there still enough evidence to risk the next very
small unit of capital?
