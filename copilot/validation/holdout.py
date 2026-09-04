"""
The locked holdout carve: one boundary, pinned by date.

The charter reserves the most recent 15-20% of history as a single-use out-of-sample
test, and this module is where that reservation physically happens. Everything the
walk-forward gate sees passes through ``carve``, and the holdout side of the boundary
never reaches a search, a selection, or a fold score.

Pinned by **date** rather than by percentage for the same reason the cost model pins its
snapshot by name ([ADR-0011]): a percentage is computed over whatever bars happen to be
present, so a re-backfill that extends the catalog would silently move the boundary and
leak formerly held-out bars into development. A date cannot move without a diff to this
file. [ADR-0012] records the choice of boundary.

The band guard is equally deliberate. New bars land on the holdout side of a date pin,
so a growing catalog raises the holdout's share; the moment the share leaves the
charter's band, ``carve`` refuses and the boundary has to be re-decided in a commit
rather than drifting quietly past what was agreed.

Two ends, not one
-----------------
That guard did its job and then became the obstacle. Research needs a frozen universe
and execution needs a catalog fresh to yesterday, and while the carve's universe was
"whatever bars exist", one file could not serve both: appending a session raised the
holdout share until ``carve`` refused, so the catalog had to stay frozen at 2025-12-31
and the live path had no recent history to warm from.

So the evaluation universe is pinned at **both** ends. ``EVALUATION_END`` closes it the
same way ``HOLDOUT_START`` divides it - by a date in this file, which cannot move without
a diff - and bars beyond it are clipped before the split rather than counted into it.
A catalog that grows past the window therefore changes no share, no fold and no verdict,
and the same file can be read fresh by the live path and frozen by the gate.

Clipping is not the same as discarding, and ``carve`` says what it set aside:
:attr:`CarvedHistory.unevaluated` carries those bars, and the verdict record names the
window's end alongside its start. A run scored over 2005-2021 while the catalog held
2026 must say so, or a later reader will take it for a run over everything available.

[ADR-0017] records this second pin. It amends [ADR-0012] rather than superseding it: the
boundary that ADR chose has not moved, and neither has any verdict computed against it.

[ADR-0011]: ../docs/decisions/0011-spread-is-charged-at-p95-from-a-pinned-snapshot.md
[ADR-0012]: ../docs/decisions/0012-the-holdout-is-carved-at-2022-01-01.md
[ADR-0017]: ../docs/decisions/0017-the-evaluation-window-is-pinned-at-both-ends.md
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Sequence

    from copilot.validation.types import DailyBar


HOLDOUT_START = datetime(2022, 1, 1, tzinfo=UTC)
"""
The boundary: bars closing at or after this instant are holdout, never development.

2022-01-01 reserves 18.99% of the evaluation window (1,003 of 5,283 bars per symbol),
inside the charter's 15-20% band, and the held-out span carries the 2022 drawdown, the
2023-2024 recovery and 2025 - regimes the development window's tail does not contain,
which is what an out-of-sample test is for. Moving this date is a deliberate act that
belongs in a commit touching this line, with [ADR-0012] superseded.

The share is stated against the window rather than the catalog because those are no
longer the same thing ([ADR-0017]). A catalog fresh to yesterday holds bars this figure
does not count, and that is the intent: growth past ``EVALUATION_END`` moves neither the
share nor the boundary.

"""

EVALUATION_END = datetime(2026, 1, 1, tzinfo=UTC)
"""
The far end: bars closing at or after this instant are outside the evaluation universe.

2026-01-01 is where the catalog stood when the pin was added - 5,283 bars per symbol,
last close 2025-12-31 - so closing the window here preserves every filed verdict exactly
rather than re-cutting history to a new figure. That is the point: the pin was added to
let the catalog grow without moving research, and a pin that changed the answer on the
day it landed would have failed at the one thing it is for.

Moving it forward is a deliberate re-decision, and an expensive one: it enlarges the
holdout, which is single-use ([ADR-0014]). Growth alone must never do it.

[ADR-0014]: ../docs/decisions/0014-the-holdout-is-spent-as-one-more-fold.md

"""

MIN_HOLDOUT_SHARE = Decimal("0.15")
MAX_HOLDOUT_SHARE = Decimal("0.20")
"""
The charter's band: reserve the most recent 15-20% of history.
"""


class HoldoutCarveError(ValueError):
    """
    The carve cannot honour the charter with the bars it was given.
    """


@dataclass(frozen=True)
class CarvedHistory:
    """
    One history split at the pin, clipped to the evaluation window.

    Three parts, and only two of them are the experiment: what the gate may see, what it
    never sees, and what the window does not reach at all.

    """

    development: tuple[DailyBar, ...]
    holdout: tuple[DailyBar, ...]
    unevaluated: tuple[DailyBar, ...] = ()
    """
    Bars past ``EVALUATION_END``, clipped before the split.

    Not a third fold and not a second holdout: these bars are outside the experiment
    entirely, and nothing scores against them. They are carried so the clip is visible to
    a caller that wants to report it, because a history silently shortened is the failure
    this whole module exists to prevent.

    """

    @property
    def holdout_share(self) -> Decimal:
        """
        Return the held-out fraction of the evaluated history.

        The denominator is the window, not the catalog: bars past ``EVALUATION_END`` are
        excluded here for the same reason they are excluded from the split, and counting
        them would let catalog growth move the share the pin exists to hold still.

        """
        total = len(self.development) + len(self.holdout)
        return Decimal(len(self.holdout)) / Decimal(total)


def carve(bars: Sequence[DailyBar]) -> CarvedHistory:
    """
    Clip to the evaluation window, split at the pin, refuse a carve outside the charter.

    Clipping happens first and is not a refusal: a catalog fresh to yesterday is the
    normal state once the live path warms from the same file, so bars past
    ``EVALUATION_END`` are set aside rather than treated as an error. Everything after the
    clip sees exactly the history the window names, whatever else the catalog holds.

    Refusal is reserved for the split itself, in both directions: a history that does not
    straddle the pin means the pin is wrong for this catalog, and a share outside the
    15-20% band means the *window* has grown or shrunk past what the boundary was decided
    against. Either way the answer is a re-decision in a commit, not a quietly different
    carve.

    """
    ordered = sorted(bars, key=lambda bar: bar.closed_at)
    within = tuple(bar for bar in ordered if bar.closed_at < EVALUATION_END)
    unevaluated = tuple(bar for bar in ordered if bar.closed_at >= EVALUATION_END)
    development = tuple(bar for bar in within if bar.closed_at < HOLDOUT_START)
    holdout = tuple(bar for bar in within if bar.closed_at >= HOLDOUT_START)

    boundary = HOLDOUT_START.date().isoformat()
    window_end = EVALUATION_END.date().isoformat()
    if not development or not holdout:
        raise HoldoutCarveError(
            f"history does not straddle the holdout boundary {boundary}: "
            f"{len(development)} development bars, {len(holdout)} holdout bars within "
            f"the evaluation window ending {window_end}, {len(unevaluated)} bars beyond "
            f"it. The pin does not fit this catalog; re-decide it (ADR-0012) rather than "
            f"validating without a holdout.",
        )

    carved = CarvedHistory(
        development=development,
        holdout=holdout,
        unevaluated=unevaluated,
    )
    share = carved.holdout_share
    if not MIN_HOLDOUT_SHARE <= share <= MAX_HOLDOUT_SHARE:
        raise HoldoutCarveError(
            f"the {boundary} boundary now reserves {share:.2%} of the {len(within)} bars "
            f"inside the window ending {window_end}, outside the charter's "
            f"{MIN_HOLDOUT_SHARE:.0%}-{MAX_HOLDOUT_SHARE:.0%}. Catalog growth past the "
            f"window cannot cause this (ADR-0017), so the history itself has changed; "
            f"re-decide the pin in a commit (ADR-0012) rather than letting the carve "
            f"drift.",
        )
    return carved


__all__ = [
    "EVALUATION_END",
    "HOLDOUT_START",
    "MAX_HOLDOUT_SHARE",
    "MIN_HOLDOUT_SHARE",
    "CarvedHistory",
    "HoldoutCarveError",
    "carve",
]
