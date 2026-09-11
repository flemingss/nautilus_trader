"""
Find the account a session is trading, and read what it holds.

One lookup. The preflight and the activation runner each searched the cache for the
account in their own way, and the search has a trap that was found once and must not be
found twice: the account does not live on the instrument's venue. Instruments resolve on
``SMART`` while the execution client registers the account under its own client name, so
the id reads ``IB-<account>`` and a search of the instrument venues alone finds nothing -
which is how the first preflight reported a missing account that was in the cache the
whole time.

"""

from __future__ import annotations

from decimal import Decimal
from decimal import InvalidOperation

from nautilus_trader.model import AccountType
from nautilus_trader.model import Currency
from nautilus_trader.model import Venue


EXEC_CLIENT_VENUE = "IB"
"""
The venue the execution client registers the account under; not a listing venue.
"""

EQUITY_CURRENCY = "USD"

SETTLED_CASH_TAG = "SettledCash"
"""
The IB account summary tag the adapter carries into the account state's ``info``.
"""

SETTLED_CASH_REPORTED = "reported by the broker"
SETTLED_CASH_NOT_APPLICABLE = "not applicable: a margin account has no settled-cash cap"


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
    settled-cash term is the one that would differ, and :func:`reported_settled_cash`
    reads it.

    """
    venue, account_id = _require_account(cache, venues)
    balance = cache.account_for_venue(venue).balances().get(Currency.from_str(EQUITY_CURRENCY))  # type: ignore[attr-defined]
    if balance is None:
        raise NoAccountError(f"account {account_id} reports no {EQUITY_CURRENCY} balance")
    return balance.total.as_decimal(), account_id


def reported_settled_cash(
    cache: object,
    venues: tuple[Venue, ...],
) -> tuple[Decimal | None, str]:
    """
    Return ``(settled cash, basis)``, or refuse to size a cash account without it.

    IB can send ``SettledCash`` in its account summary, and the adapter carries every summary
    tag into the account state's ``info`` rather than into a typed balance. The newest state
    that carries the tag is read: a later update that brought no summary does not mean the
    cash stopped being reported.

    **A margin account returns no figure, and says so.** The charter's settled-cash rule is
    a cash-account rule, and IB does not send the tag for a margin account: on the paper
    account, 2026-09-11, nine of the ten tags the adapter requests came back and
    ``SettledCash`` was the one that did not. Refusing there would stop every paper session
    over a term that cannot apply to it.

    **Every other account type is refused rather than defaulted when it is missing.** A cash
    account sized without its settled figure is the charter's named failure, and equity is
    not a safe stand-in - it includes the unsettled proceeds a cash account cannot spend. A
    base currency other than USD is refused for the same reason: the tag carries no currency
    of its own. Whether IB sends the tag for a cash account is only answerable on one.

    """
    venue, account_id = _require_account(cache, venues)
    account = cache.account_for_venue(venue)  # type: ignore[attr-defined]
    if account.account_type == AccountType.MARGIN:
        return None, SETTLED_CASH_NOT_APPLICABLE
    base = account.base_currency
    if base is not None and str(base) != EQUITY_CURRENCY:
        raise NoAccountError(
            f"account {account_id} reports in {base}; {SETTLED_CASH_TAG} carries no currency "
            f"of its own, so it cannot be read as {EQUITY_CURRENCY}",
        )
    for state in reversed(account.events):
        value = (state.info or {}).get(SETTLED_CASH_TAG)
        if value is None:
            continue
        try:
            return Decimal(str(value)), SETTLED_CASH_REPORTED
        except InvalidOperation as e:
            raise NoAccountError(
                f"account {account_id} reports {SETTLED_CASH_TAG} as {value!r}, not a number",
            ) from e
    raise NoAccountError(
        f"account {account_id} reports no {SETTLED_CASH_TAG}. A session does not buy "
        f"without knowing what it may spend; check the account summary the execution "
        f"client received before reading anything else into this.",
    )


def _require_account(cache: object, venues: tuple[Venue, ...]) -> tuple[Venue, str]:
    found = find_account(cache, venues)
    if found is None:
        raise NoAccountError(
            f"no account under any of {[str(v) for v in venues]}. A session cannot size "
            f"against equity it cannot read; check the account with "
            f"python -m copilot.live.preflight before reading anything else into this.",
        )
    return found


__all__ = [
    "EQUITY_CURRENCY",
    "EXEC_CLIENT_VENUE",
    "SETTLED_CASH_NOT_APPLICABLE",
    "SETTLED_CASH_REPORTED",
    "SETTLED_CASH_TAG",
    "NoAccountError",
    "find_account",
    "reported_equity",
    "reported_settled_cash",
]
