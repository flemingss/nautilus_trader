"""
Tests for the verdict fingerprint.

The property that matters is the one the morning depends on: a bar appended past the
evaluation window must not change the data digest, or `--changed` recomputes every
verdict every day and is worth nothing. The rest pins that each of the four inputs is
actually in the hash - a digest that ignored the cost basis would report "unchanged"
across a repin, which is the silent failure the record exists to make loud.

"""

from __future__ import annotations

import json
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from copilot.calibration.cost_model import CostModel
from copilot.strategies.activations import find_activation
from copilot.strategies.fingerprint import Fingerprint
from copilot.strategies.fingerprint import code_digest
from copilot.strategies.fingerprint import cost_digest
from copilot.strategies.fingerprint import data_digest
from copilot.strategies.fingerprint import filed_fingerprint
from copilot.strategies.fingerprint import identity_digest
from copilot.strategies.fingerprint import unchanged_since
from copilot.validation.holdout import EVALUATION_END
from copilot.validation.types import DailyBar


ACTIVATION = find_activation("aapl-gap-fade-long-next-close")


def bar(when: datetime, close: str = "100") -> DailyBar:
    value = Decimal(close)
    return DailyBar(
        symbol="AAPL",
        closed_at=when,
        open=value,
        high=value + 1,
        low=value - 1,
        close=value,
        volume=1_000,
    )


def inside(n: int) -> list[DailyBar]:
    """
    Bars inside the evaluation window, oldest first.
    """
    return [bar(EVALUATION_END - timedelta(days=n - i)) for i in range(n)]


def beyond(n: int) -> list[DailyBar]:
    """
    Bars a daily append would land past the window's end.
    """
    return [bar(EVALUATION_END + timedelta(days=i)) for i in range(n)]


class TestDataDigest:
    def test_an_append_past_the_window_changes_nothing(self) -> None:
        """
        The property the morning depends on.
        """
        assert data_digest(inside(50)) == data_digest([*inside(50), *beyond(200)])

    def test_a_bar_inside_the_window_changes_it(self) -> None:
        changed = [*inside(49), bar(EVALUATION_END - timedelta(days=1), close="101")]
        assert data_digest(inside(50)) != data_digest(changed)

    def test_input_order_does_not_matter(self) -> None:
        """
        The catalog is read sorted; a digest that depended on order would flap.
        """
        assert data_digest(inside(50)) == data_digest(inside(50)[::-1])

    def test_a_bar_at_the_window_end_is_outside(self) -> None:
        """
        Same convention as the carve: the instant belongs to the far side.
        """
        assert data_digest(inside(10)) == data_digest([*inside(10), bar(EVALUATION_END)])


class TestIdentityDigest:
    def test_a_parameter_changes_it(self) -> None:
        from dataclasses import replace

        other = replace(ACTIVATION, parameters={**ACTIVATION.parameters, "stop_atr": "2.0"})
        assert identity_digest(ACTIVATION) != identity_digest(other)

    def test_a_holdout_boundary_changes_it(self) -> None:
        """
        ADR-0020: two verdicts carved at different dates are different experiments.
        """
        from dataclasses import replace

        settings = replace(ACTIVATION.validation, holdout_start="2024-07-01")
        other = replace(ACTIVATION, validation=settings)
        assert identity_digest(ACTIVATION) != identity_digest(other)

    def test_the_same_activation_digests_the_same(self) -> None:
        assert identity_digest(ACTIVATION) == identity_digest(find_activation(ACTIVATION.name))


class TestCostDigest:
    def test_the_symbol_is_part_of_the_basis(self) -> None:
        """
        A repin that moved one symbol's spread must show as a change for that symbol.
        """
        model = CostModel.from_snapshot()
        assert cost_digest(model, "AAPL") != cost_digest(model, "SPY")


class TestCodeDigest:
    def test_filed_outputs_are_not_code(self, tmp_path: Path) -> None:
        """
        A verdict being filed must not make the next `--changed` think the code moved.
        """
        (tmp_path / "strategies").mkdir()
        (tmp_path / "strategies" / "rule.py").write_text("x = 1\n")
        before = code_digest(roots=("strategies",), overlay=tmp_path)
        (tmp_path / "strategies" / "verdict.json").write_text("{}")
        (tmp_path / "strategies" / "notes.md").write_text("# n")
        assert code_digest(roots=("strategies",), overlay=tmp_path) == before

    def test_a_source_edit_is_a_change(self, tmp_path: Path) -> None:
        (tmp_path / "strategies").mkdir()
        source = tmp_path / "strategies" / "rule.py"
        source.write_text("x = 1\n")
        before = code_digest(roots=("strategies",), overlay=tmp_path)
        source.write_text("x = 2\n")
        assert code_digest(roots=("strategies",), overlay=tmp_path) != before

    def test_a_single_file_root_is_hashed(self, tmp_path: Path) -> None:
        (tmp_path / "cost.py").write_text("c = 1\n")
        assert code_digest(roots=("cost.py",), overlay=tmp_path) != code_digest(
            roots=(),
            overlay=tmp_path,
        )


class TestUnchangedSince:
    def current(self) -> Fingerprint:
        return Fingerprint(data="d", identity="i", cost="c", code="k")

    def file(self, directory: Path, name: str, stamp: str, inputs: dict | None) -> Path:
        path = directory / f"{name}_{stamp}.json"
        payload = {"activation": name}
        if inputs is not None:
            payload["inputs"] = inputs
        path.write_text(json.dumps(payload))
        return path

    def test_nothing_filed_means_run(self, tmp_path: Path) -> None:
        assert unchanged_since("x", self.current(), tmp_path) is None

    def test_a_record_without_a_fingerprint_means_run(self, tmp_path: Path) -> None:
        """
        Every verdict filed before this existed.

        Once, and then never again.

        """
        self.file(tmp_path, "x", "20260904T000000Z", None)
        assert unchanged_since("x", self.current(), tmp_path) is None

    def test_a_matching_record_is_returned(self, tmp_path: Path) -> None:
        path = self.file(tmp_path, "x", "20260904T000000Z", self.current().as_record())
        assert unchanged_since("x", self.current(), tmp_path) == path

    def test_only_the_newest_record_counts(self, tmp_path: Path) -> None:
        """
        An older match behind a newer mismatch is not "unchanged": the newer run is what
        the operator last saw, and it was computed from something else.
        """
        self.file(tmp_path, "x", "20260901T000000Z", self.current().as_record())
        stale = Fingerprint(data="d2", identity="i", cost="c", code="k")
        self.file(tmp_path, "x", "20260904T000000Z", stale.as_record())
        assert unchanged_since("x", self.current(), tmp_path) is None

    def test_a_moved_digest_is_named(self, tmp_path: Path) -> None:
        path = self.file(
            tmp_path,
            "x",
            "20260904T000000Z",
            Fingerprint(data="d", identity="i", cost="c2", code="k").as_record(),
        )
        filed = filed_fingerprint(path)
        assert filed is not None
        assert self.current().differs_from(filed) == ("cost",)


def test_the_combined_digest_covers_all_four() -> None:
    base = Fingerprint(data="d", identity="i", cost="c", code="k")
    for field in ("data", "identity", "cost", "code"):
        from dataclasses import replace

        assert replace(base, **{field: "other"}).combined != base.combined
