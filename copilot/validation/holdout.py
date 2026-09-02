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

[ADR-0011]: ../docs/decisions/0011-spread-is-charged-at-p95-from-a-pinned-snapshot.md
[ADR-0012]: ../docs/decisions/0012-the-holdout-is-carved-at-2022-01-01.md
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

2022-01-01 reserves 18.99% of the current catalog (1,003 of 5,283 bars per symbol),
inside the charter's 15-20% band, and the held-out span carries the 2022 drawdown, the
2023-2024 recovery and 2025 - regimes the development window's tail does not contain,
which is what an out-of-sample test is for. Moving this date is a deliberate act that
belongs in a commit touching this line, with [ADR-0012] superseded.

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
    One history split at the pin: what the gate may see, and what it never sees.
    """

    development: tuple[DailyBar, ...]
    holdout: tuple[DailyBar, ...]

    @property
    def holdout_share(self) -> Decimal:
        """
        Return the held-out fraction of the whole history.
        """
        total = len(self.development) + len(self.holdout)
        return Decimal(len(self.holdout)) / Decimal(total)


def carve(bars: Sequence[DailyBar]) -> CarvedHistory:
    """
    Split ``bars`` at the pinned boundary, refusing any carve outside the charter.

    Refusal rather than a warning, in both directions: a history that does not straddle
    the pin means the pin is wrong for this catalog, and a share outside the 15-20% band
    means the catalog has grown or shrunk past what the boundary was decided against.
    Either way the answer is a re-decision in a commit, not a quietly different carve.

    """
    ordered = sorted(bars, key=lambda bar: bar.closed_at)
    development = tuple(bar for bar in ordered if bar.closed_at < HOLDOUT_START)
    holdout = tuple(bar for bar in ordered if bar.closed_at >= HOLDOUT_START)

    boundary = HOLDOUT_START.date().isoformat()
    if not development or not holdout:
        raise HoldoutCarveError(
            f"history does not straddle the holdout boundary {boundary}: "
            f"{len(development)} development bars, {len(holdout)} holdout bars. "
            f"The pin does not fit this catalog; re-decide it (ADR-0012) rather than "
            f"validating without a holdout.",
        )

    carved = CarvedHistory(development=development, holdout=holdout)
    share = carved.holdout_share
    if not MIN_HOLDOUT_SHARE <= share <= MAX_HOLDOUT_SHARE:
        raise HoldoutCarveError(
            f"the {boundary} boundary now reserves {share:.2%} of {len(ordered)} bars, "
            f"outside the charter's {MIN_HOLDOUT_SHARE:.0%}-{MAX_HOLDOUT_SHARE:.0%}. "
            f"The catalog has changed since the boundary was decided; re-decide the pin "
            f"in a commit (ADR-0012) rather than letting the carve drift.",
        )
    return carved


__all__ = [
    "HOLDOUT_START",
    "MAX_HOLDOUT_SHARE",
    "MIN_HOLDOUT_SHARE",
    "CarvedHistory",
    "HoldoutCarveError",
    "carve",
]
