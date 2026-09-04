"""
Risk-based position sizing, ported from trade-copilot ``libs/risk/sizing.py``.

Size from the stop, not from a fixed quantity. A fixed share count makes every
reported number price-level dependent: the same percentage move on an instrument at
450 and one at 180 produces different currency swings, so the instrument's price ends
up inside its score, an absolute threshold means a different strictness per instrument,
and no two instruments' expectancies are commensurable.

Sizing from the stop instead makes a stop-out cost the same everywhere, which is what
makes R a scale-free unit and what lets one gate judge candidates across instruments.

Generalised in the port: the original read an entry zone and invalidation level off its
own ``Signal`` contract. Here the levels are passed directly, so any Nautilus strategy
can use it. The arithmetic is unchanged and stays in ``Decimal`` throughout - these are
prices and quantities, and a float round trip would quietly move them.

Pairs with :class:`copilot.validation.nautilus_replay.RiskAmountRegistry`: what
:func:`risk_amount` returns is exactly what a strategy should record when it opens a
position.

"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_FLOOR
from decimal import Decimal

from copilot.validation.types import Direction


@dataclass(frozen=True)
class Sizing:
    """
    The two numbers a position is sized with, kept together so they cannot come apart.

    A strategy holds exactly one of these. Research seeds it from config; the live path
    replaces it with numbers derived from the account. Either way ``on_bar`` reads one
    object, so the research budget cannot survive into a live decision by default.

    """

    risk_budget: Decimal
    max_notional: Decimal | None = None


def stop_distance(
    *,
    direction: Direction,
    entry_price: Decimal,
    stop_price: Decimal,
) -> Decimal:
    """
    Distance from entry to stop, or zero when the stop cannot be honoured.

    Returns zero - rather than a negative number or an exception - when the stop sits on
    the wrong side of the entry, because that is a signal that cannot be sized at all.
    Callers treat zero as "refuse this trade", which is what the production risk engine
    does when it reports an invalid stop distance.

    """
    if entry_price <= 0 or stop_price <= 0:
        return Decimal(0)
    if direction is Direction.LONG:
        # A long's stop must sit below entry, or a stop-out is a profit.
        if stop_price >= entry_price:
            return Decimal(0)
        return entry_price - stop_price
    if stop_price <= entry_price:
        return Decimal(0)
    return stop_price - entry_price


def position_size(
    *,
    risk_budget: Decimal,
    distance: Decimal,
    lot_size: Decimal = Decimal(1),
    max_notional: Decimal | None = None,
    entry_price: Decimal | None = None,
) -> Decimal:
    """
    Largest whole-lot size whose stop-out costs at most ``risk_budget``.

    Floored, never rounded: rounding up would put more at risk than the budget allows,
    which is the one direction a risk control must not err in.

    Returns zero when the trade cannot be sized, which is precisely when a real risk
    engine vetoes for an invalid stop distance or a zero quantity. A caller should skip
    the trade rather than invent one production would never take.

    ``max_notional`` is the playbook's second cap, ``floor(A * c / P)``: the most the
    position may be worth at entry, whatever the stop says. A risk budget alone does not
    bound notional - a tight stop on a cheap instrument sizes to many shares - and on a
    small account that is the difference between a position and the whole account. It
    needs ``entry_price`` to mean anything, and both are given or neither is.

    """
    if distance <= 0 or risk_budget <= 0 or lot_size <= 0:
        return Decimal(0)
    if (max_notional is None) != (entry_price is None):
        raise ValueError("max_notional and entry_price are given together or not at all")
    raw_lots = (risk_budget / distance) / lot_size
    if max_notional is not None and entry_price is not None:
        if max_notional <= 0 or entry_price <= 0:
            return Decimal(0)
        raw_lots = min(raw_lots, (max_notional / entry_price) / lot_size)
    lots = raw_lots.to_integral_value(rounding=ROUND_FLOOR)
    if lots <= 0:
        return Decimal(0)
    return lots * lot_size


def risk_amount(*, quantity: Decimal, distance: Decimal) -> Decimal:
    """
    Currency actually at risk once the size has been floored.

    Recorded per trade rather than assumed equal to the budget: quantity is floored to
    whole lots, so realised risk sits at or just under the budget and differs slightly
    per trade. Using the budget as the R denominator instead would overstate R by that
    rounding.

    """
    if quantity <= 0 or distance <= 0:
        return Decimal(0)
    return quantity * distance


def size_from_levels(
    *,
    direction: Direction,
    entry_price: Decimal,
    stop_price: Decimal,
    risk_budget: Decimal,
    lot_size: Decimal = Decimal(1),
    max_notional: Decimal | None = None,
) -> tuple[Decimal, Decimal]:
    """
    Return ``(quantity, risk_amount)`` for one signal.

    Both zero when the trade cannot be sized, so a caller can refuse on a single check.
    When ``max_notional`` binds, the realised risk is below the budget and says so - the
    same way flooring already leaves it at or under, and for the same reason it is
    recorded per trade rather than assumed.

    """
    distance = stop_distance(
        direction=direction,
        entry_price=entry_price,
        stop_price=stop_price,
    )
    quantity = position_size(
        risk_budget=risk_budget,
        distance=distance,
        lot_size=lot_size,
        max_notional=max_notional,
        entry_price=entry_price if max_notional is not None else None,
    )
    return quantity, risk_amount(quantity=quantity, distance=distance)


__all__ = [
    "Sizing",
    "position_size",
    "risk_amount",
    "size_from_levels",
    "stop_distance",
]
