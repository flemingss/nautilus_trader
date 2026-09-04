"""
Tests for what a live session may run.

The failure this guards is quiet: a strategy on a broker connection deciding with a
parameter set the gate never scored, while the record reads like the researched rule.

"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from copilot.strategies.activations import Activation
from copilot.strategies.activations import Lifecycle
from copilot.strategies.activations import find_activation
from copilot.strategies.gap_reversal import AXIS_DEFAULTS
from copilot.strategies.gap_reversal import SEARCH_SPACE
from copilot.strategies.promotion import FROZEN
from copilot.strategies.promotion import SEEDED
from copilot.strategies.promotion import UnscoredParametersError
from copilot.strategies.promotion import differences
from copilot.strategies.promotion import provenance
from copilot.strategies.promotion import unfixed_axes


FROZEN_SET = {
    "long": "True",
    "entry_timing": "next_close",
    "atr_period": "14",
    "min_gap_atr": "0.40",
    "target_1_atr": "1.5",
}


def activation(lifecycle: Lifecycle, **parameters: object) -> Activation:
    return Activation(
        name="test-promotion",
        strategy="gap_reversal",
        lifecycle=lifecycle,
        symbol="SPY",
        venue="ARCX",
        parameters={"long": True, "entry_timing": "next_close", "atr_period": 14, **parameters},
    )


def holdout(tmp_path: Path, decision: str | None, frozen: dict | None = FROZEN_SET) -> Path:
    path = tmp_path / "test-promotion.json"
    path.write_text(json.dumps({"owner_decision": decision, "frozen_parameters": frozen}))
    return tmp_path


def test_every_searched_axis_has_a_default_to_name() -> None:
    # The label says what an unfixed axis ran at. An axis without a default would be
    # reported as fixed when it was not.
    assert set(AXIS_DEFAULTS) == set(SEARCH_SPACE)


def test_research_runs_seeded_and_names_what_it_left_unfixed() -> None:
    p = provenance(activation(Lifecycle.RESEARCH))
    assert p.source == SEEDED
    assert p.unfixed == {"min_gap_atr": "0.25", "target_1_atr": "1.0"}
    assert "not the gate's selection" in p.label


def test_a_research_activation_fixing_every_axis_says_so() -> None:
    p = provenance(activation(Lifecycle.RESEARCH, min_gap_atr="0.40", target_1_atr="1.5"))
    assert p.unfixed == {}
    assert "every searched axis fixed" in p.label


def test_the_registry_activations_are_all_seeded_today() -> None:
    # Pins the state of the world this module was written into: nothing is frozen, so
    # every live session is a RESEARCH session and must be labelled as one.
    a = find_activation("aapl-gap-fade-long-next-close")
    assert provenance(a).source == SEEDED
    assert set(unfixed_axes(a)) == set(SEARCH_SPACE)


def test_paper_without_a_spent_holdout_is_refused(tmp_path: Path) -> None:
    with pytest.raises(UnscoredParametersError, match="no spent holdout"):
        provenance(activation(Lifecycle.PAPER), tmp_path)


def test_paper_on_an_undecided_holdout_is_refused(tmp_path: Path) -> None:
    with pytest.raises(UnscoredParametersError, match="not 'freeze'"):
        provenance(activation(Lifecycle.PAPER), holdout(tmp_path, None))


def test_paper_on_a_rejected_holdout_is_refused(tmp_path: Path) -> None:
    with pytest.raises(UnscoredParametersError, match="'reject'"):
        provenance(activation(Lifecycle.PAPER), holdout(tmp_path, "reject"))


def test_a_frozen_record_that_selected_nothing_is_refused(tmp_path: Path) -> None:
    with pytest.raises(UnscoredParametersError, match="no frozen_parameters"):
        provenance(activation(Lifecycle.PAPER), holdout(tmp_path, "freeze", None))


def test_paper_with_the_frozen_set_in_the_registry_runs_frozen(tmp_path: Path) -> None:
    a = activation(Lifecycle.PAPER, min_gap_atr="0.40", target_1_atr="1.5")
    p = provenance(a, holdout(tmp_path, "freeze"))
    assert p.source == FROZEN
    assert p.record == "test-promotion.json"
    assert p.unfixed == {}


def test_live_is_held_to_the_same_rule(tmp_path: Path) -> None:
    a = activation(Lifecycle.LIVE, min_gap_atr="0.40", target_1_atr="1.5")
    assert provenance(a, holdout(tmp_path, "freeze")).source == FROZEN


def test_an_unfixed_axis_on_a_paper_activation_is_refused(tmp_path: Path) -> None:
    # The exact case of 2026-09-04: the registry leaves min_gap_atr to the default while
    # the frozen set says 0.40. That ran silently; it must not run at all.
    with pytest.raises(UnscoredParametersError, match="min_gap_atr: not fixed"):
        provenance(activation(Lifecycle.PAPER, target_1_atr="1.5"), holdout(tmp_path, "freeze"))


def test_a_drifted_value_is_refused_and_named(tmp_path: Path) -> None:
    a = activation(Lifecycle.PAPER, min_gap_atr="0.25", target_1_atr="1.5")
    with pytest.raises(UnscoredParametersError, match=r"min_gap_atr: \[parameters\] has 0.25"):
        provenance(a, holdout(tmp_path, "freeze"))


def test_an_extra_key_the_holdout_never_scored_is_refused(tmp_path: Path) -> None:
    a = activation(Lifecycle.PAPER, min_gap_atr="0.40", target_1_atr="1.5", stop_atr="2.0")
    with pytest.raises(UnscoredParametersError, match=r"stop_atr=2\.0: fixed"):
        provenance(a, holdout(tmp_path, "freeze"))


def test_values_are_compared_as_values() -> None:
    # 0.4 and 0.40 are the same number; True and "true" are the same flag. A string
    # comparison would refuse a correct promotion over formatting.
    assert differences({"a": Decimal("0.4"), "b": True}, {"a": "0.40", "b": "true"}) == ()
    assert differences({"a": "0.41"}, {"a": "0.40"}) != ()


def test_the_refusal_says_how_to_fix_it(tmp_path: Path) -> None:
    with pytest.raises(UnscoredParametersError, match="Promote by diff"):
        provenance(activation(Lifecycle.PAPER), holdout(tmp_path, "freeze"))
