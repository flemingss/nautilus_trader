"""
Tests for the subscription interference verdict.

The counts are cheap to collect and expensive to misread: three of the four outcomes
look like "quotes stopped" if only the treated instrument is examined, and only one of
them is evidence about the subscription. These pin which is which.

"""

from __future__ import annotations

from types import SimpleNamespace

from copilot.live.probes.subscription_interference import BASELINE
from copilot.live.probes.subscription_interference import TREATED
from copilot.live.probes.subscription_interference import PhaseCounts
from copilot.live.probes.subscription_interference import verdict


def probe(
    *,
    treated: tuple[int, int],
    control: tuple[int, int],
    treatment_error: str | None = None,
) -> SimpleNamespace:
    """
    Build a stand-in carrying only what ``verdict`` reads.

    The real probe is a pyo3 ``DataActor`` that cannot be constructed without a node.

    """
    treated_record = PhaseCounts("TREATED.SMART", "treated")
    treated_record.counts = {BASELINE: treated[0], TREATED: treated[1]}
    control_record = PhaseCounts("CONTROL.SMART", "control")
    control_record.counts = {BASELINE: control[0], TREATED: control[1]}
    return SimpleNamespace(
        treatment_error=treatment_error,
        records={
            treated_record.instrument_id: treated_record,
            control_record.instrument_id: control_record,
        },
    )


def test_quotes_stopping_only_on_the_treated_instrument_is_the_reported_behaviour() -> None:
    result, reasoning = verdict(probe(treated=(40, 0), control=(38, 36)))
    assert result == "REPRODUCED"
    assert "specific to the treated contract" in reasoning


def test_quotes_surviving_the_second_subscription_is_a_negative_result() -> None:
    result, reasoning = verdict(probe(treated=(39, 36), control=(38, 36)))
    assert result == "NOT REPRODUCED"
    assert "36" in reasoning


def test_quotes_stopping_on_both_instruments_says_nothing_about_the_subscription() -> None:
    result, reasoning = verdict(probe(treated=(40, 0), control=(38, 0)))
    assert result == "INCONCLUSIVE"
    assert "session-wide" in reasoning


def test_a_baseline_without_quotes_cannot_show_quotes_stopping() -> None:
    result, reasoning = verdict(probe(treated=(0, 0), control=(38, 36)))
    assert result == "INCONCLUSIVE"
    assert "no quotes before the treatment" in reasoning


def test_a_single_surviving_quote_is_enough_to_refuse_the_claim() -> None:
    # The claim under test is that quotes *stop*. One quote after the treatment
    # falsifies it, and the threshold has to be exactly zero rather than "few", or
    # the verdict starts encoding a judgement about how thin a stream may get.
    result, _ = verdict(probe(treated=(40, 1), control=(38, 36)))
    assert result == "NOT REPRODUCED"


def test_a_treatment_that_never_applied_cannot_produce_a_verdict_about_it() -> None:
    # The clean negative this guards against was real: a `subscribe_book_depth10` call
    # missing an argument raised, quotes carried on undisturbed, and the run read
    # NOT REPRODUCED from an experiment that had no treatment in it.
    result, reasoning = verdict(
        probe(treated=(38, 36), control=(38, 36), treatment_error="TypeError: missing book_type"),
    )
    assert result == "INCONCLUSIVE"
    assert "never applied" in reasoning
