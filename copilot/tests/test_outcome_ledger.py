"""
The breaker's evidence has to outlive the process that saw it.

Every ``day`` step is a fresh process with a fresh cache, so without a ledger a cooldown
ended at the next restart and a stop streak restarted at zero every session. These pin that
the evidence survives, that a close reported twice counts once, and that a damaged ledger
stops the breaker rather than quietly under-counting.

"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

import pytest

from copilot.paths import risk_ledger_path
from copilot.risk.outcome_ledger import OutcomeLedger
from copilot.risk.outcome_ledger import UnreadableLedgerError
from copilot.risk.outcome_ledger import outcome_key
from copilot.risk.protections import ProtectionPolicy
from copilot.risk.protections import ProtectionTrigger
from copilot.risk.protections import TradeOutcome
from copilot.risk.protections import evaluate_protections


NOW = datetime(2026, 9, 18, 21, 0, tzinfo=UTC)


def stop(days_ago: int) -> TradeOutcome:
    return TradeOutcome(
        closed_at=NOW - timedelta(days=days_ago),
        realized_pnl=Decimal("-20.00"),
        stopped_out=True,
    )


def record_session(path, position: str, days_ago: int) -> None:
    """
    One process: open the ledger, record a close, and end.
    """
    ledger = OutcomeLedger(path)
    known = ledger.read()
    outcome = stop(days_ago)
    ledger.record(outcome_key(position, outcome.closed_at), outcome, known=known)


def test_a_stop_streak_counts_across_four_separate_processes(tmp_path) -> None:
    """
    The breaker could never fire for a daily strategy before: each session a new process.
    """
    path = tmp_path / "outcomes.jsonl"
    for session, position in enumerate(("P-1", "P-2", "P-3", "P-4")):
        record_session(path, position, days_ago=4 - session)

    breach = evaluate_protections(
        list(OutcomeLedger(path).read().values()),
        ProtectionPolicy(),
        now=NOW,
        account_value=Decimal(10_000),
    )

    assert breach is not None
    assert breach.trigger == ProtectionTrigger.CONSECUTIVE_STOPS


def test_a_cooldown_in_force_before_a_restart_is_in_force_after_it(tmp_path) -> None:
    path = tmp_path / "outcomes.jsonl"
    for session in range(4):
        record_session(path, f"P-{session}", days_ago=1)

    after_restart = OutcomeLedger(path).read()
    breach = evaluate_protections(
        list(after_restart.values()),
        ProtectionPolicy(),
        now=NOW,
        account_value=Decimal(10_000),
    )

    assert breach is not None
    assert breach.is_active_at(NOW)


def test_the_same_close_reported_twice_is_recorded_once(tmp_path) -> None:
    """
    Reconciliation after a restart reports closes the ledger already holds.
    """
    ledger = OutcomeLedger(tmp_path / "outcomes.jsonl")
    outcome = stop(1)
    key = outcome_key("P-1", outcome.closed_at)
    known = ledger.read()

    assert ledger.record(key, outcome, known=known) is True
    assert ledger.record(key, outcome, known=ledger.read()) is False
    assert len((tmp_path / "outcomes.jsonl").read_text().splitlines()) == 1


def test_a_reused_position_id_with_a_later_close_is_a_second_trade() -> None:
    """
    Under a netting OMS Nautilus reuses one position id for successive round trips.
    """
    assert outcome_key("SPY-STRAT", stop(2).closed_at) != outcome_key(
        "SPY-STRAT",
        stop(1).closed_at,
    )


def test_values_come_back_exactly(tmp_path) -> None:
    ledger = OutcomeLedger(tmp_path / "outcomes.jsonl")
    outcome = TradeOutcome(closed_at=NOW, realized_pnl=Decimal("-19.37"), stopped_out=False)
    ledger.record("k", outcome, known={})

    assert OutcomeLedger(tmp_path / "outcomes.jsonl").read() == {"k": outcome}


def test_a_torn_line_stops_the_breaker_rather_than_losing_a_loss(tmp_path) -> None:
    path = tmp_path / "outcomes.jsonl"
    record_session(path, "P-1", days_ago=1)
    with path.open("a") as handle:
        handle.write('{"key": "P-2@2026-09-18T21:00:00+00:00", "closed_at": "2026-09-1')

    with pytest.raises(UnreadableLedgerError, match="line 2"):
        OutcomeLedger(path).read()


def test_a_missing_ledger_is_empty_not_an_error(tmp_path) -> None:
    assert OutcomeLedger(tmp_path / "never-written.jsonl").read() == {}


def test_paper_and_live_accounts_never_share_a_ledger(tmp_path) -> None:
    paper = risk_ledger_path("DUT067974", str(tmp_path))
    live = risk_ledger_path("U1234567", str(tmp_path))

    assert paper != live
    assert paper.parent == live.parent
    assert risk_ledger_path("IB-DU/../x", str(tmp_path)).parent == tmp_path
