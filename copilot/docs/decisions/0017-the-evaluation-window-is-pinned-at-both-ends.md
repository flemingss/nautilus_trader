# 17. The evaluation window is pinned at both ends

- **Status:** Accepted
- **Date:** 2026-09-04
- **Deciders:** Project owner, choosing between three options put in-session
- **Amends:** [ADR-0012](0012-the-holdout-is-carved-at-2022-01-01.md), which stands

## Context

The live path has no source of recent history. `GapReversal.on_start` subscribes to bars
and nothing else, so a node started today warms its ATR from the subscription alone and
cannot fire for `WARMUP_BARS` sessions - sixteen trading days that would look like "no
setups triggered" rather than like a defect.

The catalog is the obvious source and could not be used, for a reason that was a feature
before it was an obstacle. [ADR-0012] pinned the holdout boundary by date precisely so a
re-backfill could not move it, and made catalog growth the forcing function: new bars
land on the holdout side, raising its share until `carve` refuses. That worked. It also
meant the catalog had to stay frozen at 2025-12-31, because appending a single session
started the clock on a refusal, and every `validate` run would eventually raise
`HoldoutCarveError`.

So one file could not serve both halves. Research needs a universe that never moves;
execution needs one fresh to yesterday.

Three options were considered:

- **Warm from the broker.** `request_bars` against the IB data client, perhaps fifteen
  lines. Measured dead: IB returns **2188** for US equity historical bars on this
  account, directed-exchange requests included. It needs the consolidated-data purchase,
  which is undecided.
- **A second, rolling store.** Cheap - `backfill.py` already takes `--catalog` - but it
  creates two series that can disagree, and the live-versus-replay comparison the
  playbook requires would become a reconciliation of two files rather than a comparison
  against one.
- **One catalog, the window closed at both ends.** Below.

## Decision

**The evaluation universe is bounded by two dates pinned in
`copilot/validation/holdout.py`: `HOLDOUT_START` divides it, and `EVALUATION_END` closes
it. Bars closing at or after `EVALUATION_END` are clipped by `carve` before the split and
scored by nothing.**

- **`EVALUATION_END` is 2026-01-01**, which is where the catalog stood when the pin was
  added: 5,283 bars per symbol, last close 2025-12-31. The window therefore contains
  exactly the history every filed verdict was computed over, and adding the pin changed
  no result. A pin that moved the answer on the day it landed would have failed at the
  one thing it is for.
- **Growth past the window is uneventful, by construction.** `holdout_share` is taken
  over the window rather than the catalog, so appending sessions moves neither the share,
  the split, nor a single bar the gate sees. This is what the ADR buys: the catalog can
  be kept current for execution while research reads a frozen universe out of the same
  file.
- **The clip is reported, not silent.** `CarvedHistory.unevaluated` carries the bars set
  aside, and each verdict records `evaluation_window.end` beside its holdout block. A run
  scored over 2005-2021 against a catalog holding 2026 has to say so, or a later reader
  takes it for a run over everything available.
- **Refusal is narrowed to the split, and its message says so.** `carve` still raises on
  a history that does not straddle the pin or a share outside 15-20%; the message now
  states that catalog growth cannot cause it, so the diagnosis starts in the right place.
- **`HOLDOUT_START` does not move.** ADR-0012's boundary, its 18.99% reservation and
  every verdict computed against it are untouched. This ADR amends the *universe* the
  boundary is applied to, not the boundary.

## Consequences

- **`EVALUATION_END` inherits ADR-0012's discipline and one extra cost.** Moving it
  forward is a deliberate re-decision in a commit, and an expensive one: it enlarges the
  holdout, which is single-use ([ADR-0014]). Growth alone must never move it, which is
  exactly what pinning it prevents.
- **The catalog is now expected to be fresh, and nothing yet keeps it so.** The daily
  append is an operational limb this repository does not have; until it exists, the live
  warmup gate refuses stale history rather than warming from it.
- **Research and execution now share one file, one corporate-actions table
  ([ADR-0016](0016-corporate-actions-are-applied-on-read.md)) and one adjustment path.**
  That is the point: the live-versus-replay comparison the playbook's After checklist
  requires becomes a comparison, because both sides read the same series.
- **The window is a second thing that can be wrong.** A catalog whose bars stop well
  short of `EVALUATION_END` still carves cleanly, because the clip is a filter rather than
  an assertion of fullness. The gate's protection against a half-ingested history remains
  `backfill.py`'s rejection ratio, not this pin.

[ADR-0012]: 0012-the-holdout-is-carved-at-2022-01-01.md
[ADR-0014]: 0014-the-holdout-is-spent-as-one-more-fold.md
