# Activation registry

One file per activation: which strategy trades what, with which fixed parameters, at
which lifecycle stage. See [ADR-0005](../../docs/decisions/0005-setup-is-code-activation-is-data.md).

Reproduce a verdict from any of these:

```bash
python -m copilot.strategies.validate <name>
```

## What goes here, and what does not

| Bucket          | Meaning                                                       | Lives in                              |
| --------------- | ------------------------------------------------------------- | ------------------------------------- |
| **Searchable**  | The gate may move it. Its range is a property of the premise. | `SEARCH_SPACE` in the strategy module |
| **Identity**    | Fixed for this activation. Seeds its own validation.          | `[parameters]` here                   |
| **Environment** | Connection, paths, credentials. Never affects a result.       | Environment variables                 |

Numbers are written **as strings**. TOML floats are binary floats, and these values get
multiplied by an ATR to place a stop.

`[parameters]` may hold a value the search also touches - the searched axis wins, so an
activation can never quietly narrow its own declared search.

## The `[validation]` block

| Key               | Meaning                                                                                |
| ----------------- | -------------------------------------------------------------------------------------- |
| `train_bars`      | Bars each walk-forward fold selects on                                                 |
| `test_bars`       | Bars each fold is scored on                                                            |
| `purge_bars`      | Gap between a fold's training and test windows                                         |
| `min_trades`      | In-sample eligibility floor for a parameter set                                        |
| `fold_min_trades` | Trades a test window needs before its score counts; the holdout is scored under it too |
| `holdout_start`   | This activation's holdout boundary, `YYYY-MM-DD`; omit for the shared pin. See below.  |

**`holdout_start` is a date, chosen once, and then a diff.** [ADR-0012](../../docs/decisions/0012-the-holdout-is-carved-at-2022-01-01.md)
pinned one boundary for a universe of twenty-year single names; a series that starts
in 2017 puts that date at 44% of its history and `carve` refuses it.
[ADR-0020](../../docs/decisions/0020-the-holdout-boundary-is-per-activation.md) moves the
pin here. Choose the calendar quarter start whose share sits nearest the middle of the
charter's 15-20% band - `python -m copilot.data.onboard --symbols SYM.VENUE` names it -
and expect the spend to refuse a window too short to score.

## Lifecycle

`RESEARCH` never trades. `PAPER` trades a paper account. `LIVE` trades real capital and
nothing reaches it without a spent holdout. Promotion is a diff someone reviewed.

**And the live path checks the diff was made.** `copilot/strategies/promotion.py` refuses
a `PAPER` or `LIVE` activation unless its holdout record carries `owner_decision:
"freeze"` and the activation's `[parameters]` equal that record's `frozen_parameters`, key
for key - a searched axis left unfixed runs at the strategy's default, which no fold
selected. A `RESEARCH` activation always runs, and its session record says `seeded` and
names every axis it left to the default.

## Why every activation here is long-only

Not a coincidence, and as of 2026-09-01 not only a preference. The broker account is
**cash**, and a cash account cannot sell short - so a `long = false` activation could not be
promoted past `RESEARCH` even if it validated. The premise's own asymmetry points the same
way (gap-downs revert materially more often than gap-ups), which is why the legs are
separate strategies rather than one rule taking the absolute gap. The short leg becomes
available only if the account moves to margin; see
[`../../docs/playbook/PREFLIGHT.md`](../../docs/playbook/PREFLIGHT.md).
