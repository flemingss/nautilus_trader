"""
Tests for the live-versus-replay comparison.

The comparison exists to make a silent disagreement loud. What is tested is that it
stays quiet on precision and loud on everything else, and that the replay it runs
reaches the decision bar in the state the live session was in.

"""

from __future__ import annotations

import json
from datetime import UTC
from datetime import date
from datetime import datetime
from decimal import Decimal

from copilot.data.catalog import open_catalog
from copilot.data.catalog import write_ingestion
from copilot.data.marketstack import IngestionResult
from copilot.live.compare import compare_decisions
from copilot.live.compare import compare_entry
from copilot.live.compare import entries_of
from copilot.live.compare import latest_record
from copilot.live.compare import offline_decision
from copilot.live.compare import typed_parameters
from copilot.live.compare import window_bars
from copilot.live.warmup import expected_sessions
from copilot.strategies.gap_reversal import DEFERRED
from copilot.strategies.gap_reversal import WARMUP_BARS
from copilot.validation.types import DailyBar


ACTIVATION = "schx-gap-fade-long-next-close"
DECISION = date(2026, 9, 3)
SESSIONS = (*expected_sessions(DECISION, WARMUP_BARS), DECISION)


def bar(day: date, *, open_: str, close: str) -> DailyBar:
    o, c = Decimal(open_), Decimal(close)
    return DailyBar(
        symbol="SCHX",
        closed_at=datetime(day.year, day.month, day.day, tzinfo=UTC),
        open=o,
        high=max(o, c) + Decimal("0.5"),
        low=min(o, c) - Decimal("0.5"),
        close=c,
        volume=1_000_000,
    )


def series(*, gap_down: bool) -> tuple[DailyBar, ...]:
    """
    Sixteen quiet sessions and a decision bar that either gaps down hard or does not.
    """
    quiet = tuple(bar(day, open_="30", close="30") for day in SESSIONS[:-1])
    last = bar(DECISION, open_="28" if gap_down else "30", close="29" if gap_down else "30")
    return (*quiet, last)


def catalog_with(tmp_path, bars) -> str:
    store = tmp_path / "store"
    write_ingestion(
        open_catalog(store),
        IngestionResult(bars=tuple(bars), fetched=len(bars)),
        venues={"SCHX": "ARCX"},
    )
    return str(store)


def entry(**overrides: object) -> dict:
    base = {
        "activation": ACTIVATION,
        "broker_instrument": "SCHX=STK.SMART",
        "research_instrument": "SCHX.ARCX",
        "decision_bar": DECISION.isoformat(),
        "warmup_bars": WARMUP_BARS,
        "warmup_from": SESSIONS[0].isoformat(),
        "warmup_to": SESSIONS[-2].isoformat(),
        "parameters": {"long": "True", "entry_timing": "next_close", "min_gap_atr": "0.25"},
        "budget": {"risk_budget": "1.00", "max_notional": "100.00"},
        "largest_rounding": "0.0050",
        "skips": {},
        "orders": [],
    }
    return {**base, **overrides}


def test_the_window_is_inclusive_at_both_ends_and_ordered() -> None:
    bars = series(gap_down=False)
    chosen = window_bars(
        tuple(reversed(bars)),
        warmup_from=SESSIONS[0].isoformat(),
        decision_bar=DECISION.isoformat(),
    )
    assert len(chosen) == WARMUP_BARS + 1
    assert chosen[0].closed_at.date() == SESSIONS[0]
    assert chosen[-1].closed_at.date() == DECISION


def test_booleans_are_retyped_from_the_records_strings() -> None:
    # bool("False") is True; a replay of a short activation would run the long leg.
    typed = typed_parameters({"long": "False", "require_unfilled": "True", "stop_atr": "1.5"}, {})
    assert typed["long"] is False
    assert typed["require_unfilled"] is True
    assert typed["stop_atr"] == "1.5"


def test_the_budget_replaces_the_research_r_unit() -> None:
    typed = typed_parameters(
        {"risk_budget": "1000"},
        {"risk_budget": "1.00", "max_notional": "100"},
    )
    assert typed["risk_budget"] == "1.00"
    assert typed["max_notional"] == "100"


def test_precision_is_not_a_disagreement() -> None:
    # The ATR moves by at most twice the recorded rounding; the previous close by it.
    live = {
        "atr_initialized": True,
        "atr_value": "1.0100",
        "previous_close": "30.005",
        "outcome": "x",
        "skips": {"x": 1},
    }
    offline = {
        "atr_initialized": True,
        "atr_value": "1.0000",
        "previous_close": "30.000",
        "outcome": "x",
        "skips": {"x": 1},
    }
    assert compare_decisions(live, offline, rounding=Decimal("0.0050")) == ()


def test_more_than_precision_is() -> None:
    live = {"atr_initialized": True, "atr_value": "1.0101", "outcome": "x", "skips": {"x": 1}}
    offline = {"atr_initialized": True, "atr_value": "1.0000", "outcome": "x", "skips": {"x": 1}}
    found = compare_decisions(live, offline, rounding=Decimal("0.0050"))
    assert [d.field for d in found] == ["atr_value"]


def test_a_different_outcome_is_named() -> None:
    live = {"outcome": "setup_not_triggered", "skips": {"setup_not_triggered": 1}}
    offline = {"outcome": DEFERRED, "deferred_atr": "1.0", "skips": {}}
    fields = {d.field for d in compare_decisions(live, offline, rounding=Decimal(0))}
    assert fields == {"deferred_atr", "outcome", "skips"}


def test_a_ledger_refusal_agrees_with_an_offline_entry() -> None:
    # The cap is a session property no single replay can reach; the rule wanted in on
    # both sides, and that is the agreement being measured.
    live = {"outcome": "portfolio_risk_capped", "skips": {"portfolio_risk_capped": 1}}
    offline = {"outcome": "entry_submitted", "skips": {}}
    assert compare_decisions(live, offline, rounding=Decimal(0)) == ()


def test_a_ledger_refusal_of_an_entry_the_rule_did_not_want_disagrees() -> None:
    live = {"outcome": "portfolio_risk_capped", "skips": {"portfolio_risk_capped": 1}}
    offline = {"outcome": "setup_not_triggered", "skips": {"setup_not_triggered": 1}}
    found = compare_decisions(live, offline, rounding=Decimal(0))
    assert {d.field for d in found} == {"outcome", "skips"}


def test_the_replay_reaches_the_decision_bar_warmed_and_flat(tmp_path) -> None:
    # The warm-up bars never reach on_bar - a gap during them must not leave a position
    # or a deferral behind - and the decision bar does, with the ATR including it.
    bars = series(gap_down=True)
    decided = offline_decision(entry(), bars)
    assert decided["atr_initialized"] is True
    assert decided["outcome"] == DEFERRED
    # After the bar, the strategy's previous close is the decision bar's own close.
    assert decided["previous_close"] == "29.0000"
    assert decided["skips"] == {}


def test_a_session_that_agrees_with_the_catalog_agrees(tmp_path) -> None:
    bars = series(gap_down=True)
    catalog = catalog_with(tmp_path, bars)
    truth = offline_decision(entry(), bars)
    live = entry(
        atr_initialized=True,
        atr_value=truth["atr_value"],
        previous_close="29.00",
        deferred_atr=truth["deferred_atr"],
        outcome=DEFERRED,
    )
    comparison = compare_entry(live, catalog)
    assert comparison.agrees, comparison.disagreements
    assert comparison.offline_outcome == DEFERRED


def test_a_session_whose_atr_lagged_a_bar_disagrees(tmp_path) -> None:
    # The defect the comparison found on its first run: the live ATR stood at the last
    # warm-up bar's value. On a quiet warm-up that is the quiet ATR, and the decision
    # bar's range is what moves it.
    bars = series(gap_down=True)
    catalog = catalog_with(tmp_path, bars)
    lagged = entry(
        atr_initialized=True,
        atr_value="1.0000",
        previous_close="29.00",
        deferred_atr="1.0000",
        outcome=DEFERRED,
    )
    comparison = compare_entry(lagged, catalog)
    assert not comparison.agrees
    assert "atr_value" in {d.field for d in comparison.disagreements}


def test_a_catalog_that_moved_under_the_record_is_reported_not_replayed(tmp_path) -> None:
    catalog = catalog_with(tmp_path, series(gap_down=False)[:-1])
    comparison = compare_entry(entry(outcome="setup_not_triggered"), catalog)
    assert not comparison.agrees
    assert comparison.disagreements[0].field == "bars"
    assert comparison.offline_outcome is None


def test_an_older_record_without_an_outcome_is_still_comparable(tmp_path) -> None:
    # Records filed before `outcome` existed carry skips and orders only.
    bars = series(gap_down=False)
    catalog = catalog_with(tmp_path, bars)
    truth = offline_decision(entry(), bars)
    old = entry(
        atr_initialized=True,
        atr_value=truth["atr_value"],
        previous_close="30.00",
        skips={"setup_not_triggered": 1},
    )
    assert compare_entry(old, catalog).agrees


def test_both_record_shapes_are_read() -> None:
    assert len(entries_of({"activations": [entry(), entry()]})) == 2
    assert len(entries_of(entry())) == 1


def test_the_newest_record_is_the_one_compared(tmp_path) -> None:
    assert latest_record(tmp_path) is None
    (tmp_path / "run_activation_20260904T100207Z.json").write_text(json.dumps(entry()))
    (tmp_path / "run_activation_20260904T132308Z.json").write_text(json.dumps(entry()))
    assert latest_record(tmp_path).name == "run_activation_20260904T132308Z.json"
