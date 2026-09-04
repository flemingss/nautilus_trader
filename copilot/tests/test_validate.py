"""
Tests for what a `validate` invocation means, which the CLI had never had.
"""

from __future__ import annotations

from copilot.strategies.activations import load_activations
from copilot.strategies.validate import selected_activations


def test_changed_alone_means_every_activation() -> None:
    """
    The morning command, with no second flag whose absence fails after the step felt
    done.
    """
    assert len(selected_activations(None, all_=False, changed=True)) == len(load_activations())


def test_a_name_with_changed_means_that_one() -> None:
    chosen = selected_activations("aapl-gap-fade-long-next-close", all_=False, changed=True)
    assert [a.name for a in chosen] == ["aapl-gap-fade-long-next-close"]


def test_all_wins_over_a_name() -> None:
    assert len(
        selected_activations("aapl-gap-fade-long-next-close", all_=True, changed=False),
    ) == len(load_activations())


def test_nothing_named_is_nothing() -> None:
    """
    Empty rather than a default: the CLI turns this into an error the operator reads.
    """
    assert selected_activations(None, all_=False, changed=False) == ()
