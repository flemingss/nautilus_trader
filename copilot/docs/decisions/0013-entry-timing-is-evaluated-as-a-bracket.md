# 13. Entry timing is evaluated as a bracket

- **Status:** Accepted
- **Date:** 2026-09-02
- **Deciders:** Project owner; the engine measurements proposed in-session

## Context

The charter requires trading **no earlier than the next eligible session**, executed as
"a predeclared window in the first one to two hours of the next session - never assume
the opening print". The gap fade fills at the signal bar's close, a market-on-close
assumption the charter has already rejected, and this conflict gates the holdout spend:
walk-forward re-runs are free, the holdout is not, and spending it on fill semantics the
charter rejects would burn the one-time test.

The first plan was to move entry to the next session's **open**, plus a modelled
concession standing in for the charter's window. Measuring the engine killed it. On the
daily-bar replay, all five candidate mechanisms were probed against synthetic bars with
deliberately separated prices, so each fill names the tick it matched:

| Mechanism                              | Fills at          |
| -------------------------------------- | ----------------- |
| Market order from `on_bar(t)`          | close(t)          |
| Marketable limit from `on_bar(t)`      | close(t)          |
| Market order deferred to `on_bar(t+1)` | close(t+1)        |
| Passive limit resting overnight        | its own price     |
| `TimeInForce.AT_THE_OPEN`              | rejected outright |

The open of session t+1 is consumed as a matching tick **before** any order submitted
from `on_bar` can arrive, and the only order that can match it is one already resting -
which then fills at its own limit price, with no opening price improvement. A buy limit
above the signal close is immediately marketable and fills at close(t), so "willing to
pay up to X next session" has no representation either. The backtest matching engine
rejects `AT_THE_OPEN` with "not currently supported"
(`crates/execution/src/matching_engine/mod.rs`). **Next-open entry is not expressible on
this data**, and neither is the charter's concession-bounded window.

The two mechanisms that are expressible sit on either side of the charter's rule:
close(t) assumes the reversion is captured from the signal print itself; close(t+1)
gives away a full further session of it. A passive resting limit was rejected as the
single mechanism because it fills only when the market comes to it, which changes which
trades exist - a selection effect with no reason to be conservative.

## Decision

**The premise is evaluated at both expressible bounds, and the charter's rule is read as
lying between them.**

- `entry_timing = "signal_close"` - today's behaviour, the **optimistic bound**.
- `entry_timing = "next_close"` - signal frozen at bar t (direction, ATR, levels
  geometry), entry submitted on bar t+1 and filled at its close, the **pessimistic
  bound**. Deliberately more pessimistic than the charter's first-hours window.

Entry timing is **identity, not a searchable axis** (ADR-0005): it lives in the
activation's `[parameters]`, never in `SEARCH_SPACE`, and each bound is its own
activation with its own verdicts. Verdicts are never comparable across timing modes.

**The holdout is spent only on a `next_close` activation.** A candidate that passes only
at the optimistic bound has not met the charter's execution rule, and nothing at
`signal_close` may be promoted past `RESEARCH`.

The labels name the *assumptions* - transactability of the decision print on one side, a
forgone session of the hypothesized reversion on the other - not a promised ordering of
results. Whether the bounds order that way is an empirical output of running both: a
premise whose net edge changes materially between them depends on fill timing, and that
is the finding whichever direction it moves.

## Consequences

- The entry-timing charter conflict is closed: the charter-compliant bound exists, is
  the only spendable one, and the optimistic bound is reclassified as diagnostic.
- Three new activations (`*-gap-fade-long-next-close`) join the registry as new
  experiments; the existing three keep their history and their names.
- In `next_close` mode the fill price is known when the order is submitted, so the
  earlier open question of sizing against an unknown fill dissolves: sizing anchors to
  the actual entry with the ATR frozen at signal time, and realised risk is still
  recorded per trade rather than assumed.
- **Revisit trigger:** intraday history. With it the charter's actual window becomes
  modellable and this bracket is superseded by measuring the rule itself - the same
  trigger already attached to the Databento decision.
