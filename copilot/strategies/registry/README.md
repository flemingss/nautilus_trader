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

## Lifecycle

`RESEARCH` never trades. `PAPER` trades a paper account. `LIVE` trades real capital and
nothing reaches it without a spent holdout. Promotion is a diff someone reviewed.
