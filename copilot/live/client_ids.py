"""
Every IB client id this overlay connects with, in one table, so no two share one.

IB refuses a second connection on a client id already in use (error 326), and partitions
order-id space by client id, so two commands on one id either cannot both connect or
collide on order ids. Until 2026-09-11 each command chose its own defaults, and two pairs
were shared: ``run_activation`` and ``probes/failure_injection`` both on 871/872, and
``probes/order_types`` with ``probes/strand_recovery``'s recovery session on 841/842. Only the
timers' spacing kept them apart, and an overrun would have made IB refuse the second
connection with no message naming why (``docs/AUDIT_2026-09-11.md``, F12).

Each command reads its defaults from here and a test holds the table unique. A pair is
(data client, execution client); a data-only command has one id.

"""

from __future__ import annotations

from typing import NamedTuple


class ClientIds(NamedTuple):
    """
    One command's data and execution client ids.
    """

    data: int
    execution: int


BROKER_PAIRS: dict[str, ClientIds] = {
    "preflight": ClientIds(801, 802),
    "controlled_order": ClientIds(811, 812),
    "cancel_working": ClientIds(821, 822),
    "census_1": ClientIds(823, 824),
    "census_2": ClientIds(825, 826),
    "census_3": ClientIds(827, 828),
    "census_4": ClientIds(829, 830),
    "strand_recovery_strand": ClientIds(831, 832),
    "strand_recovery_recover": ClientIds(841, 842),
    "order_types": ClientIds(851, 852),
    "supervised_session": ClientIds(861, 862),
    "run_activation": ClientIds(871, 872),
    "failure_injection": ClientIds(881, 882),
}
"""
Commands that open an execution client, by name.
"""

DATA_ONLY: dict[str, int] = {
    "spread_snapshot": 701,
    "subscription_interference": 721,
}
"""
Commands that open a data client only.
"""

ENTITLEMENTS_BASE = 1200
ENTITLEMENTS_SPAN = 100
"""
``calibration/entitlements`` counts up from its base, one id per probe, within this
span.
"""


def census_pairs() -> tuple[ClientIds, ...]:
    """
    Return the census pairs in the order a sweep uses them.
    """
    return tuple(BROKER_PAIRS[f"census_{n}"] for n in range(1, 5))


def every_id() -> list[int]:
    """
    Return every id the table assigns, entitlements' span included.
    """
    ids = [i for pair in BROKER_PAIRS.values() for i in pair]
    ids += list(DATA_ONLY.values())
    ids += list(range(ENTITLEMENTS_BASE, ENTITLEMENTS_BASE + ENTITLEMENTS_SPAN))
    return ids


__all__ = [
    "BROKER_PAIRS",
    "DATA_ONLY",
    "ENTITLEMENTS_BASE",
    "ENTITLEMENTS_SPAN",
    "ClientIds",
    "census_pairs",
    "every_id",
]
