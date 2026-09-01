"""
The bridge between the id research scores and the id the broker trades.

Two names for one instrument, and until this module existed nothing connected them:

- **Research** names it by MIC venue - ``AAPL.XNAS``, ``SPY.ARCX`` - built by
  :func:`copilot.data.catalog.equity_for` from an activation's ``symbol`` and ``venue``.
  Every walk-forward verdict in this repository is scored against that form.
- **The broker** resolves ``AAPL=STK.SMART`` under ``SymbologyMethod.RAW`` and reports the
  venue as ``SMART``. ``AAPL.XNAS`` fails resolution outright.

So an instrument id that a walk-forward scored is **not** an id that can be traded. The two
halves of the overlay had been speaking different dialects since the first commit - the
calibrator used the broker form, research used the MIC form - and it surfaced only when a
node was first asked to load both.

Why the MIC venue stays
-----------------------
The obvious shortcut is to name everything ``SMART`` and delete the problem. That would be
wrong: ``SMART`` is an IB **order routing destination**, not a listing venue. It says where
an order is sent, not where the instrument is listed, and it is meaningless in a stored bar.
The catalog's MIC venue is what makes a stored series self-describing and vendor-neutral, so
research keeps it and the mapping happens at the edge, here.

Why the routing is a table rather than a default
------------------------------------------------
``SMART`` is right for US equities and is not right everywhere - it is not universally
available, and a venue we have never traded should not silently acquire a routing
destination because a default was convenient. :data:`ROUTING_BY_VENUE` therefore lists the
venues this fork has actually reasoned about, and an unlisted one raises.

"""

from __future__ import annotations

from nautilus_trader.model import InstrumentId
from nautilus_trader.model import Symbol
from nautilus_trader.model import Venue


IB_SEC_TYPE_EQUITY = "STK"

SMART = "SMART"

ROUTING_BY_VENUE: dict[str, str] = {
    "XNAS": SMART,
    "ARCX": SMART,
    "XNYS": SMART,
    "BATS": SMART,
}
"""
Listing venue (MIC) to IB routing destination.

Only venues this fork has reasoned about. An activation on anything else fails loudly
rather than acquiring `SMART` by default - see the module docstring.

"""


class UnmappedVenueError(Exception):
    """
    Raised when a research venue has no recorded broker routing.

    Loud rather than defaulted: quietly routing an unknown venue to ``SMART`` would make
    an untested assumption at the exact moment an order is about to be placed.

    """


def broker_instrument_id(
    symbol: str,
    venue: str,
    *,
    sec_type: str = IB_SEC_TYPE_EQUITY,
) -> InstrumentId:
    """
    Return the id the broker resolves for a research ``symbol`` and ``venue``.

    ``AAPL``, ``XNAS`` becomes ``AAPL=STK.SMART``.

    """
    routing = ROUTING_BY_VENUE.get(venue.upper())
    if routing is None:
        raise UnmappedVenueError(
            f"venue {venue!r} has no recorded IB routing; "
            f"known: {sorted(ROUTING_BY_VENUE)}. Add it deliberately rather than "
            "defaulting, so the choice is reviewed",
        )
    return InstrumentId(Symbol(f"{symbol.upper()}={sec_type}"), Venue(routing))


def research_instrument_id(symbol: str, venue: str) -> InstrumentId:
    """
    Return the id research scores for the same instrument: the catalog's form.

    Here so both names are produced from one place and cannot drift apart.

    """
    return InstrumentId(Symbol(symbol.upper()), Venue(venue.upper()))


def symbol_of(instrument_id: InstrumentId) -> str:
    """
    Return the bare ticker, whichever form the id is in.

    ``AAPL=STK.SMART`` and ``AAPL.XNAS`` both give ``AAPL``, which is the only part the
    two forms agree on and therefore the only safe way to check that a mapping did not
    change which instrument is meant.

    """
    return str(instrument_id.symbol).split("=")[0]


def same_instrument(left: InstrumentId, right: InstrumentId) -> bool:
    """
    Whether two ids in either form name the same underlying ticker.
    """
    return symbol_of(left) == symbol_of(right)


__all__ = [
    "IB_SEC_TYPE_EQUITY",
    "ROUTING_BY_VENUE",
    "SMART",
    "UnmappedVenueError",
    "broker_instrument_id",
    "research_instrument_id",
    "same_instrument",
    "symbol_of",
]
