"""
Corporate actions, and the back-adjustment the catalog needs to be consistent.

Read-only against the market. It transforms stored bars; it constructs no execution
client and cannot place an order.

Why this exists
---------------
Measured on 2026-09-03 by auditing the catalog against official closing auction prints:
**the vendor's raw series is as-traded for every symbol except AAPL**, which is
back-adjusted. That inconsistency is not cosmetic. A 20:1 split left unadjusted is a
-95% single-day move in the bars, and a gap strategy reads it as the largest gap in its
history.

Five of the nine actions were settled definitively - the catalog's close on the day
before the action equals the venue's official as-traded print, to the cent - and AAPL's
was settled the other way, its 2020-08-28 close of 124.8075 being exactly the official
499.23 over four. The remainder are inferred from the same convention.

Splits and distributions are not the same thing
-----------------------------------------------
The vendor reports both as ``split_factor`` and they need opposite treatment downstream:

- A **split** multiplies the share count. AAPL 4:1, AMZN 20:1, WMT 3:1. Back-adjusting
  the price means a recorded quantity is no longer the real one, so per-share commission
  must be charged on ``recorded / factor``.
- A **distribution** - a spinoff, mostly - does not. MRK 1.05 (Organon), T 1.32 (Warner
  Bros Discovery), VZ 1.04. The price drops by the value handed to shareholders, and the
  share count is untouched. Back-adjusting the price is still right for return
  continuity, but dividing the share count would **overstate** the position and
  understate commission.

Conflating them would fix a price defect and introduce a cost one, so
:func:`classify` separates them and only :data:`SPLIT` actions belong in the cost
model's factor table.

What the classifier can and cannot do
-------------------------------------
A split's factor is a ratio of small integers because shares are indivisible. A
distribution's is whatever the spun-off entity was worth, so it lands on values like
1.005 or 1.32. That separates the two cleanly in this catalog and is the rule used
here. It is a heuristic about how the vendor encodes actions, not a law, so
:func:`classify` is deliberately conservative: anything it cannot place is a
distribution, which is the reading that leaves the share count alone.

"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Iterable
    from collections.abc import Sequence


class ActionKind(Enum):
    """
    What an action does to the share count.
    """

    SPLIT = "split"
    DISTRIBUTION = "distribution"


SPLIT = ActionKind.SPLIT
DISTRIBUTION = ActionKind.DISTRIBUTION

# A split's factor is a ratio of small integers. Denominators past this are not share
# splits any issuer has run; they are the arithmetic of a spinoff.
MAX_SPLIT_DENOMINATOR = 10


class CorporateActionError(ValueError):
    """
    An action could not be applied to a series.
    """


@dataclass(frozen=True)
class Action:
    """
    One corporate action, effective at the open of ``effective``.
    """

    symbol: str
    effective: datetime
    factor: Decimal

    def __post_init__(self) -> None:
        """
        Refuse a factor that cannot describe an action.
        """
        if self.factor <= 0:
            raise CorporateActionError(f"{self.symbol}: factor must be positive")

    @property
    def kind(self) -> ActionKind:
        """
        Return whether this changes the share count.
        """
        return classify(self.factor)


def classify(factor: Decimal) -> ActionKind:
    """
    Return whether a factor describes a share split or a distribution.

    Shares are indivisible, so a split's factor is a ratio of small integers - 2, 3, 4,
    7, 20, and occasionally 3/2. A spinoff's is the price ratio it happened to produce.

    """
    ratio = factor.as_integer_ratio()
    if ratio[1] <= MAX_SPLIT_DENOMINATOR:
        return SPLIT
    return DISTRIBUTION


def cumulative_factor(actions: Iterable[Action], when: datetime) -> Decimal:
    """
    Return the product of every factor effective strictly after ``when``.

    This is what a price recorded at ``when`` must be divided by to be expressed in
    today's terms, and what a back-adjusted quantity must be divided by to recover the
    real share count.

    """
    factor = Decimal(1)
    for action in actions:
        if when < action.effective:
            factor *= action.factor
    return factor


def back_adjust(
    closes: Sequence[tuple[datetime, Decimal]],
    actions: Sequence[Action],
) -> list[tuple[datetime, Decimal]]:
    """
    Express every price in today's terms, removing the action's discontinuity.

    Prices are divided rather than multiplied so that the most recent price is left
    untouched: the series is anchored to what a share costs now, which is the number
    every downstream cost and sizing decision is expressed in.

    """
    return [(when, (price / cumulative_factor(actions, when))) for when, price in closes]


def unadjusted_actions(
    closes: Sequence[tuple[datetime, Decimal]],
    actions: Sequence[Action],
) -> list[Action]:
    """
    Return the actions still sitting in a series as a real price discontinuity.

    Each action is tested against two hypotheses - the series is already adjusted, so
    the ratio across the action is about 1; or it is as-traded, so the ratio is about
    the factor - and assigned to whichever is nearer.

    **This is weak for a small factor.** A 1.005 distribution moves the price by less
    than an ordinary day does, so the two hypotheses sit half a percent apart and the
    answer is a guess. Settle those against an as-traded reference rather than trusting
    this; it exists to catch the large, obvious cases and to describe the rest.

    """
    ordered = sorted(closes)
    found = []
    for action in actions:
        before = [price for when, price in ordered if when < action.effective]
        after = [price for when, price in ordered if when >= action.effective]
        if not before or not after:
            continue
        ratio = before[-1] / after[0]
        if abs(ratio - action.factor) < abs(ratio - 1):
            found.append(action)
    return found


# ---------------------------------------------------------------------------
# What the catalog still needs, measured rather than assumed
# ---------------------------------------------------------------------------

PRE_ADJUSTED = frozenset({"AAPL"})
"""
Symbols the vendor already back-adjusted, so nothing is pending for them.

AAPL is the only one, and it is confirmed rather than inferred: its 2020-08-28 close of
124.8075 is exactly the venue's official as-traded print of 499.23 divided by four.
Every other symbol tested against an official print matched it to the cent, meaning the
stored price is as-traded and the action is still in the series.

"""

ACTIONS: dict[str, tuple[Action, ...]] = {
    "AAPL": (
        Action("AAPL", datetime(2005, 2, 28, tzinfo=UTC), Decimal(2)),
        Action("AAPL", datetime(2014, 6, 9, tzinfo=UTC), Decimal(7)),
        Action("AAPL", datetime(2020, 8, 31, tzinfo=UTC), Decimal(4)),
    ),
    "AMZN": (Action("AMZN", datetime(2022, 6, 6, tzinfo=UTC), Decimal(20)),),
    "GOOGL": (
        Action("GOOGL", datetime(2014, 4, 3, tzinfo=UTC), Decimal(2)),
        Action("GOOGL", datetime(2022, 7, 18, tzinfo=UTC), Decimal(20)),
    ),
    "KO": (Action("KO", datetime(2012, 8, 13, tzinfo=UTC), Decimal(2)),),
    "MRK": (Action("MRK", datetime(2021, 6, 3, tzinfo=UTC), Decimal("1.05")),),
    "T": (Action("T", datetime(2022, 4, 11, tzinfo=UTC), Decimal("1.32")),),
    "VZ": (
        Action("VZ", datetime(2006, 11, 20, tzinfo=UTC), Decimal("1.04")),
        Action("VZ", datetime(2008, 4, 1, tzinfo=UTC), Decimal("1.005")),
        Action("VZ", datetime(2010, 7, 2, tzinfo=UTC), Decimal("1.07")),
    ),
    "WMT": (Action("WMT", datetime(2024, 2, 26, tzinfo=UTC), Decimal(3)),),
}
"""
Every corporate action inside the catalog window, per symbol.

This is the single source of truth for two different questions, which is the point of
keeping one table. :func:`pending_for` answers "what is still sitting in the stored
prices as a discontinuity" and drives the read-time adjustment. :func:`split_actions`
answers "what does a recorded quantity have to be divided by to be a real share count"
and drives the cost model. AAPL appears here and in neither pending list, because its
prices arrived adjusted while its share counts still need the factor.

Measured 2026-09-03 from the vendor's ``/splits`` endpoint for all twenty symbols,
cross-checked against official closing auction prints. Five were settled definitively
against an as-traded print (AMZN, GOOGL 2022, MRK, T, WMT); the rest predate the
reference and are assigned by the ratio test and the convention those five establish.

**VZ 2008-04-01 is the one entry the evidence does not settle.** Its factor is 1.005,
so the two hypotheses sit half a percent apart and the ratio test has no power there;
the day's real move swamps it. It is listed because every action this catalog *can*
resolve says the vendor stores as-traded, and consistency is a better tiebreak than a
coin flip. The cost of being wrong either way is bounded by that same half a percent.

Symbols absent from this table either never acted (CSCO, CVX, INTC, JNJ, JPM, MSFT,
PEP, PG, QQQ, SPY, XOM), are already adjusted (AAPL), or acted before their first
stored bar (IWM's 2005-06-09 split, against a series starting 2005-07-18).

"""


def pending_for(symbol: str) -> tuple[Action, ...]:
    """
    Return the actions still sitting in a symbol's stored prices as a discontinuity.
    """
    if symbol in PRE_ADJUSTED:
        return ()
    return ACTIONS.get(symbol, ())


def split_actions(symbol: str) -> tuple[Action, ...]:
    """
    Return only the actions that changed the share count.

    This is what a per-share cost model needs, and it is deliberately **not** limited
    to the pending ones: once every series is back-adjusted - whether by the vendor or
    on read - a recorded quantity is the real one multiplied by every split, including
    the ones that arrived already applied. A distribution left the count untouched and
    is excluded.

    """
    return tuple(a for a in ACTIONS.get(symbol, ()) if a.kind is SPLIT)
