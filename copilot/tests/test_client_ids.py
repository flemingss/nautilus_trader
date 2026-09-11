"""
No two commands may share an IB client id, and no command may choose one outside the
table.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from copilot.live.cancel_working import CENSUS_CLIENT_IDS
from copilot.live.client_ids import BROKER_PAIRS
from copilot.live.client_ids import census_pairs
from copilot.live.client_ids import every_id
from copilot.live.session import CLIENT_ID_PARTITION


OVERLAY = Path(__file__).resolve().parents[1]


def test_every_client_id_is_assigned_once() -> None:
    """
    Audit F12: the basket and the failure-injection probe shared 871/872.
    """
    duplicates = [i for i, n in Counter(every_id()).items() if n > 1]

    assert duplicates == []


def test_no_client_id_is_one_ib_order_partitioning_refuses() -> None:
    assert all(i > 0 and i % CLIENT_ID_PARTITION for i in every_id())


def test_the_sweep_takes_its_census_pairs_from_the_table() -> None:
    assert tuple(tuple(pair) for pair in census_pairs()) == CENSUS_CLIENT_IDS
    assert len(census_pairs()) == 4


LITERAL_ID = re.compile(
    r"(client_id\w*\s*=\s*\d{3,4}\b)|(--(data-|exec-)?client-id(-base)?\"[^)]*default=\d{3,4})",
)


def test_no_command_chooses_a_client_id_outside_the_table() -> None:
    """
    A literal id in a command is how the two collisions happened: each was chosen alone.
    """
    offenders = []
    for path in sorted(OVERLAY.rglob("*.py")):
        relative = path.relative_to(OVERLAY)
        if relative.parts[0] == "tests" or relative.name == "client_ids.py":
            continue
        source = path.read_text()
        offenders.extend(f"{relative}: {match.group(0)}" for match in LITERAL_ID.finditer(source))

    assert offenders == []


def test_every_order_capable_command_is_in_the_table() -> None:
    expected = {
        "preflight",
        "controlled_order",
        "cancel_working",
        "strand_recovery_strand",
        "strand_recovery_recover",
        "order_types",
        "supervised_session",
        "run_activation",
        "failure_injection",
    }

    assert expected <= set(BROKER_PAIRS)
