"""
Tests for the session manifest: commit, verdict, and whether the inputs moved.
"""

from __future__ import annotations

import json
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from copilot.calibration.cost_model import CostModel
from copilot.live.manifest import Manifest
from copilot.live.manifest import current_commit
from copilot.live.manifest import manifest_for
from copilot.strategies.activations import find_activation
from copilot.strategies.fingerprint import fingerprint_for
from copilot.validation.types import DailyBar


ACTIVATION = find_activation("aapl-gap-fade-long-next-close")
COST_MODEL = CostModel.from_snapshot()
COMMIT = ("0123456789abcdef0123456789abcdef01234567", True)


def bars(n: int) -> tuple[DailyBar, ...]:
    return tuple(
        DailyBar(
            symbol="AAPL",
            closed_at=datetime(2021, 1, 1 + i, tzinfo=UTC),
            open=Decimal(100 + i),
            high=Decimal(101 + i),
            low=Decimal(99 + i),
            close=Decimal(100 + i),
            volume=1_000,
        )
        for i in range(n)
    )


def verdict(directory: Path, series: tuple[DailyBar, ...]) -> Path:
    path = directory / f"{ACTIVATION.name}_20260904T000000Z.json"
    path.write_text(
        json.dumps({"inputs": fingerprint_for(ACTIVATION, series, COST_MODEL).as_record()}),
    )
    return path


def test_no_verdict_is_said_not_guessed(tmp_path: Path) -> None:
    m = manifest_for(ACTIVATION, bars(20), COST_MODEL, commit=COMMIT, verdicts_dir=tmp_path)
    assert m.verdict is None
    assert m.moved == ()
    assert "no verdict filed" in m.label


def test_unchanged_inputs_tie_the_session_to_its_verdict(tmp_path: Path) -> None:
    series = bars(20)
    filed = verdict(tmp_path, series)
    m = manifest_for(ACTIVATION, series, COST_MODEL, commit=COMMIT, verdicts_dir=tmp_path)
    assert m.verdict == filed.name
    assert m.moved == ()
    assert m.label == f"commit 0123456789; inputs unchanged since {filed.name}"


def test_a_moved_input_is_named(tmp_path: Path) -> None:
    # The verdict scored one series; the session is about to run on another. That is
    # the case the playbook's "verify data hash" exists to catch.
    verdict(tmp_path, bars(20))
    m = manifest_for(ACTIVATION, bars(21), COST_MODEL, commit=COMMIT, verdicts_dir=tmp_path)
    assert m.moved == ("data",)
    assert "data moved since" in m.label


def test_a_dirty_tree_is_marked() -> None:
    m = Manifest(code_commit="abc", tree_clean=False, verdict=None)
    assert "(dirty)" in m.label


def test_the_record_round_trips() -> None:
    m = Manifest(
        code_commit="abc",
        tree_clean=True,
        verdict="v.json",
        inputs={"data": "d"},
        moved=("code",),
    )
    record = m.as_record()
    assert record["moved"] == ["code"]
    assert record["inputs"] == {"data": "d"}


def test_the_commit_is_read_from_the_repository() -> None:
    commit, clean = current_commit()
    assert len(commit) == 40
    assert isinstance(clean, bool)
