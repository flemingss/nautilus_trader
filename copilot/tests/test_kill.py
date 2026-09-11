"""
The kill switch has to hold across processes, never read a broken latch as safe, and be
the thing an unanswered critical alert finally reaches.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from copilot.live import kill
from copilot.live.alerting import Acknowledgement
from copilot.live.alerting import Alert
from copilot.live.alerting import Alerter
from copilot.live.alerting import Delivery
from copilot.live.alerting import ReceiptLog
from copilot.live.alerting import Severity
from copilot.live.halt import OPERATOR
from copilot.live.halt import UNACKNOWLEDGED_CRITICAL
from copilot.live.halt import UNDELIVERED_CRITICAL
from copilot.live.halt import Latch
from copilot.live.halt import engage
from copilot.live.halt import engaged_automatically
from copilot.live.halt import orders_allowed
from copilot.live.halt import read_latch
from copilot.live.halt import release
from copilot.live.kill import check_acknowledgements
from copilot.live.kill import describe
from copilot.live.session import PaperSession


# ------------------------------------------------------------------------- the latch


def test_engaging_writes_a_latch_every_process_can_read(tmp_path: Path) -> None:
    path = tmp_path / "HALT.json"

    latch, engaged = engage("fills look wrong", path=path)

    assert engaged is True
    assert read_latch(path) == latch
    assert latch.trigger == OPERATOR
    assert len(latch.latch_id) == 8


def test_a_second_engagement_keeps_the_first_reason(tmp_path: Path) -> None:
    """
    The recovery checklist answers why it started; a later trigger must not erase that.
    """
    path = tmp_path / "HALT.json"
    first, _ = engage("fills look wrong", path=path)

    second, engaged = engage("CRITICAL expired", trigger=UNACKNOWLEDGED_CRITICAL, path=path)

    assert engaged is False
    assert second == first


def test_a_latch_that_cannot_be_read_is_engaged(tmp_path: Path) -> None:
    """
    A torn write must never read as "not halted" - the one reading that lets an order out.
    """
    path = tmp_path / "HALT.json"
    path.write_text('{"latch_id": "ab')

    latch = read_latch(path)

    assert latch is not None
    assert "cannot be read" in latch.reason


def test_no_file_is_not_halted(tmp_path: Path) -> None:
    assert read_latch(tmp_path / "HALT.json") is None


def test_release_needs_the_latch_id_retyped(tmp_path: Path) -> None:
    path = tmp_path / "HALT.json"
    latch, _ = engage("x", path=path)

    with pytest.raises(ValueError, match="is not the engaged latch's id"):
        release("00000000", path=path)
    assert read_latch(path) is not None

    assert release(latch.latch_id, path=path) == latch
    assert read_latch(path) is None


def test_releasing_nothing_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not engaged"):
        release("anything", path=tmp_path / "HALT.json")


@pytest.mark.parametrize(
    ("requested", "cancels_only", "latched", "allowed"),
    [
        (False, False, False, False),  # orders-denied sessions stay denied
        (True, False, False, True),
        (True, False, True, False),  # the latch halts an order-capable node
        (True, True, True, True),  # the sweep still cancels under a halt
        (False, True, True, False),
    ],
)
def test_the_latch_halts_everything_that_could_submit(
    requested: bool,
    cancels_only: bool,
    latched: bool,
    allowed: bool,
) -> None:
    latch = Latch("id", "t", OPERATOR, "r", "h") if latched else None

    assert orders_allowed(requested=requested, cancels_only=cancels_only, latch=latch) is allowed


def test_a_session_can_declare_it_only_cancels() -> None:
    session = PaperSession(account_id="DU1234567", orders_enabled=True, cancels_only=True)

    assert session.cancels_only is True
    assert PaperSession(account_id="DU1234567").cancels_only is False


# --------------------------------------------------------- unacknowledged critical alerts


def _critical(receipt: str, title: str = "sweep STILL WORKING") -> Delivery:
    return Delivery(
        alert=Alert(Severity.CRITICAL, title, "body"),
        delivered=True,
        notifier="pushover",
        receipt=receipt,
    )


def _state(receipt: str, *, acknowledged: bool = False, expired: bool = False) -> Acknowledgement:
    return Acknowledgement(receipt=receipt, acknowledged=acknowledged, expired=expired)


def test_an_expired_unanswered_critical_engages_the_latch(tmp_path: Path) -> None:
    receipts = ReceiptLog(tmp_path / "receipts.jsonl")
    receipts.remember(_critical("r-1"))
    latch_path = tmp_path / "HALT.json"

    lines = check_acknowledgements(
        receipts,
        lambda r: _state(r, expired=True),
        latch_path=latch_path,
    )

    latch = read_latch(latch_path)
    assert latch is not None
    assert latch.trigger == UNACKNOWLEDGED_CRITICAL
    assert "r-1" in latch.reason
    assert lines[0].startswith("UNACKNOWLEDGED")
    assert receipts.outstanding() == {}, "settled once, not re-checked every phase"


def test_an_acknowledged_critical_is_settled_without_a_halt(tmp_path: Path) -> None:
    receipts = ReceiptLog(tmp_path / "receipts.jsonl")
    receipts.remember(_critical("r-2"))

    check_acknowledgements(
        receipts,
        lambda r: _state(r, acknowledged=True),
        latch_path=tmp_path / "HALT.json",
    )

    assert read_latch(tmp_path / "HALT.json") is None
    assert receipts.outstanding() == {}


@pytest.mark.parametrize("answer", [None, "retrying"])
def test_a_critical_still_retrying_or_unreadable_is_left_outstanding(
    tmp_path: Path,
    answer,
) -> None:
    receipts = ReceiptLog(tmp_path / "receipts.jsonl")
    receipts.remember(_critical("r-3"))

    check_acknowledgements(
        receipts,
        lambda r: None if answer is None else _state(r),
        latch_path=tmp_path / "HALT.json",
    )

    assert read_latch(tmp_path / "HALT.json") is None
    assert receipts.outstanding() == {"r-3": "sweep STILL WORKING"}


def test_an_unreadable_receipt_log_is_said_not_raised_and_not_read_as_empty(tmp_path: Path) -> None:
    """
    Audit F2: the check runs first in every ``day`` phase and must not stop the sweep.
    """
    path = tmp_path / "receipts.jsonl"
    path.mkdir()

    lines = check_acknowledgements(
        ReceiptLog(path),
        lambda r: _state(r, expired=True),
        latch_path=tmp_path / "HALT.json",
    )

    assert len(lines) == 1
    assert "cannot be read" in lines[0]
    assert read_latch(tmp_path / "HALT.json") is None


@pytest.mark.parametrize(
    ("trigger", "automatic"),
    [
        (OPERATOR, False),
        (UNACKNOWLEDGED_CRITICAL, True),
        (UNDELIVERED_CRITICAL, True),
        ("unknown", True),
    ],
)
def test_only_an_operator_latch_is_one_somebody_knows_about(trigger: str, automatic: bool) -> None:
    assert engaged_automatically(Latch("id", "t", trigger, "r", "h")) is automatic
    assert engaged_automatically(None) is False


def test_only_a_delivered_alert_with_a_receipt_is_remembered(tmp_path: Path) -> None:
    receipts = ReceiptLog(tmp_path / "receipts.jsonl")
    receipts.remember(
        Delivery(alert=Alert(Severity.WARNING, "w", "b"), delivered=True, notifier="pushover"),
    )
    receipts.remember(
        Delivery(alert=Alert(Severity.CRITICAL, "c", "b"), delivered=False, notifier="stderr"),
    )

    assert receipts.outstanding() == {}


def test_the_alerter_keeps_every_critical_receipt(tmp_path: Path) -> None:
    class _Emergency:
        name = "pushover"

        def send(self, alert: Alert) -> Delivery:
            return Delivery(alert=alert, delivered=True, notifier="pushover", receipt="r-9")

    receipts = ReceiptLog(tmp_path / "receipts.jsonl")

    Alerter(_Emergency(), receipts=receipts).send(
        Alert(Severity.CRITICAL, "sweep UNCONFIRMED", "b"),
    )

    assert receipts.outstanding() == {"r-9": "sweep UNCONFIRMED"}


# ----------------------------------------------------------------------- the command


def test_the_status_names_the_release_command_with_its_id() -> None:
    latch = Latch("3f9a1c2e", "2026-09-15T14:00:00+00:00", OPERATOR, "fills look wrong", "vm")

    text = describe(latch)

    assert "HALT LATCH ENGAGED" in text
    assert "--release 3f9a1c2e" in text
    assert "not engaged" in describe(None)


def test_status_exits_non_zero_while_halted(monkeypatch, capsys) -> None:
    latch = Latch("3f9a1c2e", "2026-09-15T14:00:00+00:00", OPERATOR, "r", "vm")
    monkeypatch.setattr(kill, "read_latch", lambda: latch)

    assert kill.main(["--status"]) == 1
    assert "3f9a1c2e" in capsys.readouterr().out


def test_engaging_without_a_sweep_says_nothing_was_cancelled_and_never_flattens(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    path = tmp_path / "HALT.json"
    monkeypatch.setattr(kill, "engage", lambda reason: engage(reason, path=path))
    monkeypatch.setattr(
        kill,
        "alerter_from_environment",
        lambda: Alerter(_Silent(), receipts=ReceiptLog(tmp_path / "r.jsonl")),
    )

    code = kill.main(["--reason", "drill", "--no-sweep"])

    out = capsys.readouterr().out
    assert code == 4
    assert read_latch(path) is not None
    assert "NOT cancelled" in out
    assert "Nothing has been flattened" in out
    assert all(item in out for item in kill.RECOVERY_CHECKLIST)


class _Silent:
    name = "test"

    def send(self, alert: Alert) -> Delivery:
        return Delivery(alert=alert, delivered=True, notifier="test")


def test_the_receipt_log_survives_its_writer(tmp_path: Path) -> None:
    path = tmp_path / "receipts.jsonl"
    ReceiptLog(path).remember(_critical("r-5"))

    assert ReceiptLog(path).outstanding() == {"r-5": "sweep STILL WORKING"}
    ReceiptLog(path).resolve("r-5", "acknowledged")
    assert ReceiptLog(path).outstanding() == {}
