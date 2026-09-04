"""
Find the account a session is trading, and read what it holds.

One lookup. The preflight and the activation runner each searched the cache for the
account in their own way, and the search has a trap that was found once and must not be
found twice: the account does not live on the instrument's venue. Instruments resolve on
``SMART`` while the execution client registers the account under its own client name, so
the id reads ``IB-DUT067974`` and a search of the instrument venues alone finds nothing -
which is how the first preflight reported a missing account that was in the cache the
whole time.

"""

from __future__ import annotations

from decimal import Decimal

from nautilus_trader.model import Currency
from nautilus_trader.model import Venue


EXEC_CLIENT_VENUE = "IB"
"""
The venue the execution client registers the account under; not a listing venue.
"""

EQUITY_CURRENCY = "USD"


class NoAccountError(RuntimeError):
    """
    No account was found under any venue searched.
    """


def find_account(cache: object, venues: tuple[Venue, ...]) -> tuple[Venue, str] | None:
    """
    Return the venue the account is registered under and its id, or None.

    ``venues`` should include :data:`EXEC_CLIENT_VENUE`; it is searched in the order
    given, and the first hit wins.

    """
    for venue in venues:
        account_id = cache.account_id(venue)  # type: ignore[attr-defined]
        if account_id is not None:
            return venue, str(account_id)
    return None


def reported_equity(cache: object, venues: tuple[Venue, ...]) -> tuple[Decimal, str]:
    """
    Read the account's total balance in the sizing currency, and say which account.

    ``total`` rather than ``free``. Free excludes what working orders have reserved,
    which on a session that starts with none is the same number; the playbook's
    settled-cash term is the one that would differ, and it is not modelled here.

    """
    found = find_account(cache, venues)
    if found is None:
        raise NoAccountError(
            f"no account under any of {[str(v) for v in venues]}. A session cannot size "
            f"against equity it cannot read; check the account with "
            f"python -m copilot.live.preflight before reading anything else into this.",
        )
    venue, account_id = found
    balance = cache.account_for_venue(venue).balances().get(Currency.from_str(EQUITY_CURRENCY))  # type: ignore[attr-defined]
    if balance is None:
        raise NoAccountError(f"account {account_id} reports no {EQUITY_CURRENCY} balance")
    return balance.total.as_decimal(), account_id


__all__ = [
    "EQUITY_CURRENCY",
    "EXEC_CLIENT_VENUE",
    "NoAccountError",
    "find_account",
    "reported_equity",
]
