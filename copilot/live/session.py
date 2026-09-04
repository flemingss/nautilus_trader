"""
What separates a paper session from a live one, and the checks that enforce it.

Pure: no broker, no node, no I/O. The whole point is that the guard which stands
between a test and the real account can be tested without either.

Why this is not a formality
---------------------------
On the stage-one deployment shape (WSL with host TWS, [ADR-0006]) **paper and live differ
by a port number**: 7497 against 7496. There is no handshake, no confirmation and no
second chance - a transposed digit in an environment variable is the entire distance
between a controlled test and an order against real capital.

So two independent facts must agree before a session is called paper, and both are
checked:

1. The **port** is a known paper port, and specifically is not a known live one.
2. The **account identifier** is a paper account.

Either check alone is insufficient in a way the other covers. The port can be
reconfigured in TWS, so a paper port is not proof of a paper account. The account
identifier is supplied by us rather than discovered, so it is only as good as the
environment it came from. Requiring both means one mistake is not enough to reach the
live account.

The account prefix
------------------
IB paper accounts are ``DU``-prefixed and live accounts are not. That is a convention
observed on this account rather than a documented guarantee, so it is used as the
*structural* half of the check and never on its own: :func:`verify_paper_session` also
requires the identifier to match the one the operator configured. The prefix catches a
wrong-but-plausible account; the exact match catches a right-shaped wrong one.

"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from dataclasses import field


TWS_PAPER_PORT = 7497
TWS_LIVE_PORT = 7496
GATEWAY_PAPER_PORT = 4002
GATEWAY_LIVE_PORT = 4001

PAPER_PORTS = frozenset({TWS_PAPER_PORT, GATEWAY_PAPER_PORT})
LIVE_PORTS = frozenset({TWS_LIVE_PORT, GATEWAY_LIVE_PORT})

PAPER_ACCOUNT_PREFIX = "DU"

CLIENT_ID_PARTITION = 1_000
"""
An execution client ID divisible by this is refused.

``playbook/OPERATIONS.md`` records the constraint as version-specific and explicitly
unverified against the pinned adapter. Honouring it costs one integer out of every
thousand, so it is enforced rather than investigated: if the claim is wrong we have
given up nothing, and if it is right we have avoided an order-ID collision that would
surface as a confusing broker reject.

"""


class NotAPaperSessionError(Exception):
    """
    Raised when a session claiming to be paper cannot be shown to be one.

    An exception rather than a logged warning: the failure mode this prevents is placing
    a real order, and a warning is something a run continues past.

    """


@dataclass(frozen=True)
class PaperSession:
    """
    One configured paper session.

    ``orders_enabled`` is the stage-one switch. False means the node connects, resolves
    instruments, subscribes to data and runs strategies, and every order those strategies
    submit is denied inside the risk engine - see :mod:`copilot.live.node`.

    """

    account_id: str
    host: str = "127.0.0.1"
    port: int = TWS_PAPER_PORT
    data_client_id: int = 1
    exec_client_id: int = 2
    orders_enabled: bool = False
    instrument_ids: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        """
        Prove the session is paper at construction, so no caller can skip the check.
        """
        verify_paper_session(
            account_id=self.account_id,
            port=self.port,
            expected_account_id=self.account_id,
        )
        for name, value in (
            ("data_client_id", self.data_client_id),
            ("exec_client_id", self.exec_client_id),
        ):
            verify_client_id(value, name=name)
        if self.data_client_id == self.exec_client_id:
            # IB allocates order ID space per client ID. Sharing one between a data and
            # an execution client is the documented way to get two connections fighting
            # over the same sequence.
            raise NotAPaperSessionError(
                f"data_client_id and exec_client_id are both {self.data_client_id}; "
                "IB partitions order IDs by client ID, so they must differ",
            )


def verify_client_id(client_id: int, *, name: str = "client_id") -> None:
    """
    Refuse a client ID that IB's order-ID partitioning may not tolerate.
    """
    if client_id <= 0:
        raise NotAPaperSessionError(f"{name} must be positive, got {client_id}")
    if client_id % CLIENT_ID_PARTITION == 0:
        raise NotAPaperSessionError(
            f"{name} {client_id} is divisible by {CLIENT_ID_PARTITION}; "
            "see CLIENT_ID_PARTITION for why this is refused rather than warned about",
        )


def verify_paper_session(
    *,
    account_id: str,
    port: int,
    expected_account_id: str | None = None,
) -> None:
    """
    Raise unless both independent checks agree this is a paper session.

    Order matters for the message rather than the outcome: the port is reported first
    because a wrong port is the mistake that reaches a live account fastest.

    """
    if port in LIVE_PORTS:
        raise NotAPaperSessionError(
            f"port {port} is a known IB **live** port; paper ports are {sorted(PAPER_PORTS)}",
        )
    if port not in PAPER_PORTS:
        raise NotAPaperSessionError(
            f"port {port} is not a known IB paper port {sorted(PAPER_PORTS)}. "
            "If TWS has been reconfigured, add the port to PAPER_PORTS deliberately "
            "rather than relaxing this check",
        )

    if not account_id:
        raise NotAPaperSessionError("no account identifier supplied; cannot confirm paper")
    if not account_id.startswith(PAPER_ACCOUNT_PREFIX):
        raise NotAPaperSessionError(
            f"account {account_id!r} does not start with {PAPER_ACCOUNT_PREFIX!r}, "
            "which every IB paper account on this login does",
        )
    if expected_account_id is not None and account_id != expected_account_id:
        raise NotAPaperSessionError(
            f"account {account_id!r} is not the configured paper account {expected_account_id!r}",
        )


def add_broker_arguments(
    parser: argparse.ArgumentParser,
    *,
    data_client_id: int,
    exec_client_id: int,
) -> None:
    """
    Add the five flags every broker-connected command takes, worded once.

    The client ids are per command and required here rather than defaulted, because IB
    partitions order-id space by client id and two commands sharing one collide.

    """
    parser.add_argument("--host", default=os.getenv("IB_V2_HOST", "172.17.112.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("IB_V2_PORT", "7497")))
    parser.add_argument("--account", default=os.getenv("COPILOT_PAPER_ACCOUNT", ""))
    parser.add_argument("--data-client-id", type=int, default=data_client_id)
    parser.add_argument("--exec-client-id", type=int, default=exec_client_id)


__all__ = [
    "CLIENT_ID_PARTITION",
    "GATEWAY_LIVE_PORT",
    "GATEWAY_PAPER_PORT",
    "LIVE_PORTS",
    "PAPER_ACCOUNT_PREFIX",
    "PAPER_PORTS",
    "TWS_LIVE_PORT",
    "TWS_PAPER_PORT",
    "NotAPaperSessionError",
    "PaperSession",
    "add_broker_arguments",
    "verify_client_id",
    "verify_paper_session",
]
