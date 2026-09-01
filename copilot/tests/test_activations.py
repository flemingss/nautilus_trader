"""
Tests for the activation registry.

Two things here are load-bearing rather than hygiene.

The **seeding rule**: an activation's fixed parameters seed the grid, but a searched axis
always wins. trade-copilot learned the cost of getting that wrong (its V1-30) - without
seeding, validating a short-leg strategy silently rebuilt every candidate as the long leg
and filed the result against the wrong row. Both halves of that rule are pinned below.

The **failure mode of a typo**: a misspelled section in a TOML file would otherwise be
ignored, and an activation would run with defaults nobody chose while looking configured.
Loading is strict for that reason.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from copilot.strategies.activations import REGISTRY_DIR
from copilot.strategies.activations import SETUPS
from copilot.strategies.activations import Lifecycle
from copilot.strategies.activations import find_activation
from copilot.strategies.activations import load_activation
from copilot.strategies.activations import load_activations
from copilot.strategies.activations import parse_activation


MINIMAL = {
    "strategy": "gap_reversal",
    "instrument": {"symbol": "AAPL", "venue": "XNAS"},
}


def an_activation(**overrides: object):
    return parse_activation("test-activation", {**MINIMAL, **overrides})


# ------------------------------------------------------------------- loading


def test_a_minimal_activation_loads():
    activation = an_activation()

    assert activation.name == "test-activation"
    assert activation.symbol == "AAPL"
    assert activation.venue == "XNAS"


def test_lifecycle_defaults_to_research():
    """
    The safe default. An activation that forgot to say must not trade.
    """
    assert an_activation().lifecycle is Lifecycle.RESEARCH
    assert an_activation().trades is False


@pytest.mark.parametrize(
    ("lifecycle", "trades"),
    [("RESEARCH", False), ("PAPER", True), ("LIVE", True)],
)
def test_only_paper_and_live_trade(lifecycle, trades):
    assert an_activation(lifecycle=lifecycle).trades is trades


def test_an_unknown_strategy_fails_loudly():
    """Skipping it with a logged reason would let a typo remove a strategy from a run."""
    with pytest.raises(ValueError, match="unknown strategy"):
        an_activation(strategy="gap_revrsal")


def test_an_unknown_section_fails_loudly():
    """
    A misspelled section would otherwise be ignored.

    The activation would then run on defaults nobody chose, while reading as configured -
    which is worse than failing, because the file says one thing and the run does another.
    """
    with pytest.raises(ValueError, match="unknown section"):
        an_activation(paramaters={"long": True})


@pytest.mark.parametrize("missing", ["symbol", "venue"])
def test_a_missing_instrument_field_fails(missing):
    instrument = dict(MINIMAL["instrument"])
    del instrument[missing]
    with pytest.raises(ValueError, match=f"instrument.{missing}"):
        an_activation(instrument=instrument)


def test_an_unknown_lifecycle_fails():
    with pytest.raises(ValueError, match="LIV"):
        an_activation(lifecycle="LIV")


# ------------------------------------------------------------------ numerics


def test_numbers_arrive_as_decimal_not_float():
    """
    These get multiplied by an ATR to place a stop.

    TOML floats are binary floats. Values are written as strings in the registry for that
    reason, and anything that slips through as a float is converted here rather than
    reaching an order price.
    """
    activation = an_activation(parameters={"stop_atr": "1.5", "entry_buffer_atr": 0.25})

    assert activation.parameters["stop_atr"] == Decimal("1.5")
    assert activation.parameters["entry_buffer_atr"] == Decimal("0.25")
    assert not isinstance(activation.parameters["entry_buffer_atr"], float)


def test_non_numeric_strings_stay_strings():
    assert an_activation(parameters={"mode": "fade"}).parameters["mode"] == "fade"


def test_booleans_are_not_mangled():
    activation = an_activation(parameters={"long": True, "require_unfilled": False})

    assert activation.parameters["long"] is True
    assert activation.parameters["require_unfilled"] is False


# -------------------------------------------------------------- the seeding rule


def test_fixed_parameters_seed_the_grid():
    """
    Half the V1-30 rule: a candidate starts from *this* activation's identity.

    Without it the grid rebuilds every candidate from contract defaults, so validating a
    short-leg strategy silently validates the long leg.
    """
    grid = an_activation(parameters={"long": False, "stop_atr": "2.0"}).grid()

    assert grid.base["long"] is False
    assert grid.base["stop_atr"] == Decimal("2.0")


def test_a_searched_axis_still_wins_over_the_seed():
    """
    The other half: an activation can never quietly narrow its own declared search.

    Seeding `min_gap_atr` must not remove it from the search - every expanded candidate
    still varies it, and the seeded value does not survive into any of them unless the
    axis happens to contain it.
    """
    activation = an_activation(parameters={"min_gap_atr": "0.99"})
    grid = activation.grid()

    assert "min_gap_atr" in grid.axes()
    searched = {str(params["min_gap_atr"]) for params, _, _ in grid.expand()}
    assert searched == {"0.15", "0.25", "0.40"}
    assert "0.99" not in searched


def test_the_grid_expands_to_the_declared_space():
    """Three thresholds by two targets. A larger space costs headroom against deflation."""
    assert len(an_activation().grid().expand()) == 6


# --------------------------------------------------------- the real registry


def test_every_registry_file_loads():
    """
    Run against the committed files, not fixtures.

    A registry that stopped loading would fail every validation run, and the failure would
    surface as "no verdict" rather than as a broken file.
    """
    activations = load_activations()

    assert activations, "the registry is empty"
    for activation in activations:
        assert activation.strategy in SETUPS
        assert activation.grid().expand()


def test_registry_names_match_their_filenames():
    """The name is the handle `validate` is invoked with, so it has to be the filename."""
    for path in sorted(REGISTRY_DIR.glob("*.toml")):
        assert load_activation(path).name == path.stem


def test_nothing_in_the_registry_trades_yet():
    """
    Every activation is RESEARCH until a holdout has been spent.

    A test rather than a convention: promotion should be a deliberate diff that fails this
    assertion and makes someone justify changing it.
    """
    trading = [a.name for a in load_activations() if a.trades]
    assert trading == [], f"activation(s) past RESEARCH without a recorded holdout: {trading}"


def test_find_activation_lists_what_exists_when_missing():
    with pytest.raises(KeyError, match="available"):
        find_activation("no-such-activation")


def test_warmup_comes_from_the_strategy_not_the_caller():
    """
    Guessing it short makes the gate read missing history as failing folds.

    That looks identical to a dead premise, which is why the number is taken from the
    strategy that knows what it needs.
    """
    activation = an_activation()
    assert activation.setup.warmup_bars == 16  # ATR period 14, plus the two-bar trigger


def test_a_registry_directory_with_no_files_is_empty_not_an_error(tmp_path: Path):
    assert load_activations(tmp_path) == ()
