"""
What the alerting path has to get right, and what it must never do.

No test here touches the network. The transport is exercised against a stub ``urlopen``
so the assertions are about our behaviour rather than Pushover's availability.

Three properties carry the weight, and each has a failure this pins:

- **An unconfigured system must not look healthy.** The unattended-paper gate reads
  ``delivered``; a fallback that reported success would let a system with no alerting at
  all pass the gate that exists to catch exactly that.
- **Alerting must never take a session down.** Anything the transport raises becomes a
  failed delivery with a reason, because a notification service outage is not a reason to
  stop trading safely.
- **A CRITICAL alert is never suppressed.** The flood guard is there to protect the
  operator's attention, and the one class of alert whose whole purpose is to keep asking
  until a human answers has to be exempt, or the kill switch's acknowledgement condition
  can never fire.

"""

from __future__ import annotations

import json
from datetime import UTC
from datetime import datetime
from io import BytesIO
from typing import Self
from urllib.error import URLError
from urllib.parse import parse_qs

import pytest

from copilot.live import alerting
from copilot.live.alerting import Alert
from copilot.live.alerting import Alerter
from copilot.live.alerting import Delivery
from copilot.live.alerting import FloodGuard
from copilot.live.alerting import PushoverNotifier
from copilot.live.alerting import Severity
from copilot.live.alerting import StreamNotifier
from copilot.live.alerting import alerter_from_environment
from copilot.live.alerting import notifier_from_environment
from copilot.live.alerting import receipt_status
from copilot.paths import PUSHOVER_TOKEN_ENV
from copilot.paths import PUSHOVER_USER_KEY_ENV


NOW = datetime(2026, 9, 10, 13, 30, tzinfo=UTC)

CONFIGURED = {PUSHOVER_TOKEN_ENV: "app-token", PUSHOVER_USER_KEY_ENV: "user-key"}


class _Response(BytesIO):
    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __init__(self, body: str) -> None:
        super().__init__(body.encode())


class _Recorder:
    """
    A stub ``urlopen`` that remembers what was posted and replays a canned body.
    """

    def __init__(self, payload: dict[str, object] | None = None) -> None:
        self.payload = payload if payload is not None else {"status": 1, "request": "req-1"}
        self.calls: list[tuple[str, dict[str, list[str]]]] = []

    def __call__(self, request, timeout=None) -> _Response:
        body = request.data.decode() if request.data else ""
        self.calls.append((request.full_url, parse_qs(body)))
        return _Response(json.dumps(self.payload))

    @property
    def last_params(self) -> dict[str, list[str]]:
        return self.calls[-1][1]


class _Clock:
    """
    A monotonic clock the test drives, standing in for the ``time`` module.
    """

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now


def _alert(severity: Severity = Severity.WARNING, title: str = "a title") -> Alert:
    return Alert(severity=severity, title=title, body="a body", occurred_at=NOW)


def _notifier(monkeypatch, stub, **kwargs: object) -> PushoverNotifier:
    monkeypatch.setattr(alerting, "urlopen", stub)
    return PushoverNotifier("app-token", "user-key", **kwargs)


# ----------------------------------------------------------------------------- the alert


def test_context_is_rendered_into_the_message():
    """
    An alert that names a problem without naming the instrument cannot be acted on.
    """
    alert = Alert(Severity.WARNING, "t", "body", {"symbol": "AAPL", "order": "O-1"})

    assert alert.rendered() == "body\n\norder: O-1\nsymbol: AAPL"


def test_an_alert_without_context_renders_as_its_body():
    assert _alert().rendered() == "a body"


def test_alerts_of_the_same_severity_and_title_share_a_key():
    """
    What the flood guard treats as "this again".
    """
    assert _alert(title="x").key == _alert(title="x").key
    assert _alert(title="x").key != _alert(title="y").key
    assert _alert(Severity.INFO, "x").key != _alert(Severity.WARNING, "x").key


# ------------------------------------------------------------------------- the fallback


def test_an_unconfigured_notifier_reports_failure_not_success(capsys):
    """
    The gate reads ``delivered``.

    A silent fallback would let it pass on nothing.

    """
    delivery = StreamNotifier().send(_alert())

    assert delivery.delivered is False
    assert PUSHOVER_TOKEN_ENV in delivery.detail
    assert "a body" in capsys.readouterr().err


def test_an_unconfigured_environment_falls_back_rather_than_raising():
    assert isinstance(notifier_from_environment({}), StreamNotifier)
    assert isinstance(notifier_from_environment({PUSHOVER_TOKEN_ENV: "t"}), StreamNotifier)
    assert isinstance(notifier_from_environment({PUSHOVER_USER_KEY_ENV: "u"}), StreamNotifier)


def test_blank_credentials_are_not_credentials():
    """
    An exported-but-empty variable is the shape a half-finished .env leaves behind.
    """
    assert isinstance(
        notifier_from_environment({PUSHOVER_TOKEN_ENV: "  ", PUSHOVER_USER_KEY_ENV: "u"}),
        StreamNotifier,
    )


def test_a_configured_environment_builds_the_transport():
    assert isinstance(notifier_from_environment(CONFIGURED), PushoverNotifier)
    assert alerter_from_environment(CONFIGURED).notifier_name == "pushover"


# -------------------------------------------------------------------------- the transport


def test_severity_maps_to_priority(monkeypatch):
    stub = _Recorder()
    notifier = _notifier(monkeypatch, stub)

    for severity, expected in ((Severity.INFO, "-1"), (Severity.WARNING, "1")):
        notifier.send(_alert(severity))
        assert stub.last_params["priority"] == [expected]


def test_only_critical_carries_retry_and_expire(monkeypatch):
    """
    Emergency priority is what makes an alert demand acknowledgement.
    """
    stub = _Recorder({"status": 1, "request": "r", "receipt": "rcpt-1"})
    notifier = _notifier(monkeypatch, stub, retry_seconds=120, expire_seconds=3600)

    notifier.send(_alert(Severity.WARNING))
    assert "retry" not in stub.last_params

    delivery = notifier.send(_alert(Severity.CRITICAL))
    assert stub.last_params["priority"] == ["2"]
    assert stub.last_params["retry"] == ["120"]
    assert stub.last_params["expire"] == ["3600"]
    assert delivery.receipt == "rcpt-1"


def test_a_non_emergency_send_has_no_receipt(monkeypatch):
    delivery = _notifier(monkeypatch, _Recorder()).send(_alert(Severity.WARNING))

    assert delivery.delivered is True
    assert delivery.receipt is None


def test_a_rejected_message_is_a_failed_delivery(monkeypatch):
    """
    Pushover reports a bad token as status 0, not as a transport error.
    """
    stub = _Recorder({"status": 0, "errors": ["application token is invalid"]})

    delivery = _notifier(monkeypatch, stub).send(_alert())

    assert delivery.delivered is False
    assert "invalid" in delivery.detail


def test_a_transport_error_is_a_failed_delivery_not_an_exception(monkeypatch):
    def boom(request, timeout=None):
        raise URLError("no route to host")

    delivery = _notifier(monkeypatch, boom).send(_alert())

    assert delivery.delivered is False
    assert "URLError" in delivery.detail


def test_the_credentials_are_posted_not_put_in_the_url(monkeypatch):
    """
    A token in a query string lands in logs and proxies; the body is the right place.
    """
    stub = _Recorder()
    _notifier(monkeypatch, stub).send(_alert())
    url, params = stub.calls[-1]

    assert "app-token" not in url
    assert params["token"] == ["app-token"]
    assert params["user"] == ["user-key"]


# --------------------------------------------------------------- the deadline is declared


@pytest.mark.parametrize(
    ("retry", "expire", "match"),
    [
        (10, 3600, "at least 30"),
        (120, 20000, "at most 10800"),
        (30, 3600, "caps"),
    ],
)
def test_an_undeliverable_deadline_is_refused(retry, expire, match):
    """
    Pushover silently caps retries at fifty, so a configuration implying more is a
    deadline that will not be honoured.

    Refuse it rather than let it read as declared.

    """
    with pytest.raises(ValueError, match=match):
        PushoverNotifier("t", "u", retry_seconds=retry, expire_seconds=expire)


def test_a_blank_deadline_setting_is_the_default_and_no_problem():
    settings = alerting.alert_settings(
        {alerting.RETRY_SECONDS_ENV: "", alerting.EXPIRE_SECONDS_ENV: " "},
    )

    assert settings == alerting.AlertSettings()


def test_a_valid_deadline_setting_is_kept():
    settings = alerting.alert_settings(
        {alerting.RETRY_SECONDS_ENV: "60", alerting.EXPIRE_SECONDS_ENV: "1800"},
    )

    assert (settings.retry_seconds, settings.expire_seconds, settings.problems) == (60, 1800, ())


@pytest.mark.parametrize(
    ("environ", "named"),
    [
        ({alerting.RETRY_SECONDS_ENV: "two minutes"}, "not a whole number"),
        ({alerting.EXPIRE_SECONDS_ENV: "10800"}, "more than 50 retries"),
        ({alerting.RETRY_SECONDS_ENV: "10"}, "at least 30"),
    ],
)
def test_a_deadline_setting_that_would_not_work_falls_back_and_says_so(environ, named):
    """
    Audit F2: these raised inside every ``day`` phase, before the phase did anything.
    """
    settings = alerting.alert_settings(environ)

    assert (settings.retry_seconds, settings.expire_seconds) == (
        alerting.DEFAULT_RETRY_SECONDS,
        alerting.DEFAULT_EXPIRE_SECONDS,
    )
    assert any(named in problem for problem in settings.problems)
    assert "default deadline is in force" in settings.problems[-1]


def test_a_bad_deadline_setting_does_not_stop_the_transport_being_built(capsys):
    notifier = notifier_from_environment({**CONFIGURED, alerting.RETRY_SECONDS_ENV: ""})
    assert isinstance(notifier, PushoverNotifier)

    notifier = notifier_from_environment({**CONFIGURED, alerting.EXPIRE_SECONDS_ENV: "10800"})
    assert isinstance(notifier, PushoverNotifier)
    assert "alert deadline setting" in capsys.readouterr().err


def test_the_default_deadline_is_within_pushover_limits():
    PushoverNotifier("t", "u")  # the constructor is the check; it refuses a bad deadline

    assert alerting.DEFAULT_EXPIRE_SECONDS <= alerting.EXPIRE_SECONDS_MAX
    assert alerting.DEFAULT_RETRY_SECONDS >= alerting.RETRY_SECONDS_MIN
    assert alerting.DEFAULT_EXPIRE_SECONDS // alerting.DEFAULT_RETRY_SECONDS <= alerting.RETRY_CAP


# ---------------------------------------------------------------------- acknowledgement


def test_a_receipt_reads_back_as_acknowledged(monkeypatch):
    payload = {
        "status": 1,
        "acknowledged": 1,
        "acknowledged_at": int(NOW.timestamp()),
        "expired": 0,
    }
    monkeypatch.setattr(alerting, "urlopen", lambda *_, **__: _Response(json.dumps(payload)))

    state = PushoverNotifier("t", "u").receipt_status("rcpt-1")

    assert state.acknowledged is True
    assert state.outstanding is False
    assert state.acknowledged_at == NOW


def test_an_unacknowledged_alert_is_outstanding_until_it_expires(monkeypatch):
    """
    The kill switch triggers on a critical alert unacknowledged by deadline, so expiry
    has to stop counting as "still trying".
    """
    payload = {"status": 1, "acknowledged": 0, "acknowledged_at": 0, "expired": 0}
    monkeypatch.setattr(alerting, "urlopen", lambda *_, **__: _Response(json.dumps(payload)))
    assert PushoverNotifier("t", "u").receipt_status("r").outstanding is True

    payload["expired"] = 1
    assert PushoverNotifier("t", "u").receipt_status("r").outstanding is False


@pytest.mark.parametrize(
    "failure",
    [URLError("unreachable"), TimeoutError("slow"), "not json"],
)
def test_a_receipt_that_cannot_be_read_is_unknown_not_an_exception(monkeypatch, capsys, failure):
    """
    Audit F2: ``day`` reads receipts first, and a Pushover outage raised before the sweep.
    """

    def unreadable(*_: object, **__: object) -> _Response:
        if isinstance(failure, str):
            return _Response(failure)
        raise failure

    monkeypatch.setattr(alerting, "urlopen", unreadable)

    assert receipt_status(PushoverNotifier("t", "u"), "rcpt-1") is None
    assert "stays outstanding" in capsys.readouterr().err


def test_a_transport_without_receipts_says_unknown_rather_than_acknowledged():
    """
    ``None`` is the honest answer.

    Reporting an acknowledgement nobody made would let the kill switch's condition be
    satisfied by a transport that cannot observe it.

    """
    assert receipt_status(StreamNotifier(), "rcpt-1") is None


# ------------------------------------------------------------------------- the flood guard


def test_a_repeat_inside_the_window_is_suppressed():
    guard = FloodGuard(window_seconds=300.0)

    assert guard.admit(_alert(), now=0.0) == 0
    assert guard.admit(_alert(), now=10.0) is None
    assert guard.admit(_alert(), now=299.0) is None


def test_the_suppressed_count_rides_the_next_one_through():
    """
    Nothing is dropped quietly: the record has to say how long it was repeating.
    """
    guard = FloodGuard(window_seconds=100.0)
    guard.admit(_alert(), now=0.0)
    guard.admit(_alert(), now=1.0)
    guard.admit(_alert(), now=2.0)

    assert guard.admit(_alert(), now=200.0) == 2
    assert guard.admit(_alert(), now=400.0) == 0


def test_a_different_alert_is_not_a_repeat():
    guard = FloodGuard(window_seconds=300.0)

    assert guard.admit(_alert(title="one"), now=0.0) == 0
    assert guard.admit(_alert(title="two"), now=1.0) == 0


def test_critical_is_never_suppressed():
    """
    The one severity whose job is to keep asking until a human answers.
    """
    guard = FloodGuard(window_seconds=3600.0)

    for moment in (0.0, 1.0, 2.0, 3.0):
        assert guard.admit(_alert(Severity.CRITICAL), now=moment) is not None


def test_the_alerter_reports_a_suppressed_repeat_as_undelivered(monkeypatch):
    stub = _Recorder()
    alerter = Alerter(_notifier(monkeypatch, stub), FloodGuard(window_seconds=3600.0))

    assert alerter.warning("t", "b").delivered is True
    second = alerter.warning("t", "b")

    assert second.delivered is False
    assert "suppressed" in second.detail
    assert len(stub.calls) == 1


def test_the_alerter_carries_the_suppressed_count_onto_the_delivery(monkeypatch):
    """
    A delivery after a quiet spell has to say how many it stands for.
    """
    clock = _Clock()
    monkeypatch.setattr(alerting, "time", clock)
    stub = _Recorder()
    alerter = Alerter(_notifier(monkeypatch, stub), FloodGuard(window_seconds=100.0))

    alerter.warning("t", "b")
    for _ in range(3):
        clock.now += 1.0
        assert alerter.warning("t", "b").delivered is False

    clock.now += 500.0
    through = alerter.warning("t", "b")

    assert through.delivered is True
    assert through.suppressed == 3
    assert len(stub.calls) == 2


# ------------------------------------------------------------- alerting never kills a run


def test_a_notifier_that_raises_becomes_a_failed_delivery():
    """
    A notification service outage must not propagate into the trading path.
    """

    class _Exploding:
        name = "exploding"

        def send(self, alert: Alert) -> Delivery:
            raise RuntimeError("kaboom")

    delivery = Alerter(_Exploding()).critical("t", "b")

    assert delivery.delivered is False
    assert "kaboom" in delivery.detail


# ---------------------------------------------------------- a CRITICAL nobody received


class _Undelivered:
    name = "down"

    def send(self, alert: Alert) -> Delivery:
        return Delivery(alert=alert, delivered=False, notifier="down", detail="URLError: down")


def test_an_undelivered_critical_is_escalated_and_the_delivery_says_how():
    """
    Audit F3: it was printed and forgotten - no receipt, no latch, nothing from the day.
    """
    escalated: list[Delivery] = []
    alerter = Alerter(
        _Undelivered(),
        on_undelivered_critical=lambda d: (escalated.append(d), "halt latch ab12 engaged")[1],
    )

    delivery = alerter.critical("sweep STILL WORKING", "b")

    assert [d.alert.title for d in escalated] == ["sweep STILL WORKING"]
    assert delivery.delivered is False
    assert "URLError: down" in delivery.detail
    assert "halt latch ab12 engaged" in delivery.detail


def test_a_raising_notifier_on_a_critical_is_escalated_too():
    class _Exploding:
        name = "exploding"

        def send(self, alert: Alert) -> Delivery:
            raise RuntimeError("kaboom")

    escalated: list[str] = []
    Alerter(
        _Exploding(),
        on_undelivered_critical=lambda d: (escalated.append("x"), "x")[1],
    ).critical(
        "t",
        "b",
    )

    assert escalated == ["x"]


def test_only_an_undelivered_critical_is_escalated(monkeypatch):
    escalated: list[str] = []
    hook = lambda d: (escalated.append(str(d.alert.severity)), "noted")[1]  # noqa: E731

    Alerter(_Undelivered(), on_undelivered_critical=hook).warning("w", "b")
    Alerter(_notifier(monkeypatch, _Recorder()), on_undelivered_critical=hook).critical("c", "b")

    assert escalated == []


def test_an_escalation_that_raises_does_not_take_the_session_down():
    def hook(_delivery: Delivery) -> str:
        raise OSError("disk full")

    delivery = Alerter(_Undelivered(), on_undelivered_critical=hook).critical("t", "b")

    assert delivery.delivered is False
    assert "escalation raised OSError: disk full" in delivery.detail


def test_an_alerter_from_the_environment_halts_on_an_undelivered_critical(tmp_path, capsys):
    """
    With no transport configured every CRITICAL is undelivered, so it engages the latch.
    """
    from copilot.live.halt import UNDELIVERED_CRITICAL
    from copilot.live.halt import read_latch

    latch_path = tmp_path / "HALT.json"
    alerter = alerter_from_environment(
        {},
        receipts_path=tmp_path / "receipts.jsonl",
        latch_path=latch_path,
    )

    delivery = alerter.critical("sweep UNCONFIRMED", "b")

    latch = read_latch(latch_path)
    assert latch is not None
    assert latch.trigger == UNDELIVERED_CRITICAL
    assert "sweep UNCONFIRMED" in latch.reason
    assert latch.latch_id in delivery.detail
    assert alerter.warning("w", "b").delivered is False
    assert read_latch(latch_path) == latch, "a WARNING nobody received halts nothing"


def test_a_malformed_receipt_line_is_skipped_not_raised(tmp_path, capsys):
    from copilot.live.alerting import ReceiptLog

    path = tmp_path / "receipts.jsonl"
    path.write_text(
        '["not", "an", "object"]\n'
        '{"event": "sent"}\n'
        '{"event": "sent", "receipt": "r-1", "title": "t"}\n',
    )

    assert ReceiptLog(path).outstanding() == {"r-1": "t"}
    assert capsys.readouterr().err.count("unreadable receipt line") == 2


def test_the_severity_helpers_set_the_severity(monkeypatch):
    stub = _Recorder({"status": 1, "request": "r", "receipt": "rc"})
    alerter = Alerter(_notifier(monkeypatch, stub))

    alerter.info("t", "b")
    assert stub.last_params["priority"] == ["-1"]
    alerter.warning("t2", "b")
    assert stub.last_params["priority"] == ["1"]
    alerter.critical("t3", "b")
    assert stub.last_params["priority"] == ["2"]


# ------------------------------------------------------------------------------- the CLI


def test_the_self_check_exits_nonzero_when_nothing_is_configured(monkeypatch, capsys):
    """
    The operator command is the gate's evidence, so an unconfigured box must fail it.
    """
    monkeypatch.setattr(alerting, "notifier_from_environment", lambda *_: StreamNotifier())

    assert alerting.main(["--send-test"]) == 1
    assert "NOT working" in capsys.readouterr().out


def test_the_self_check_exits_zero_once_delivery_works(monkeypatch, capsys):
    monkeypatch.setattr(alerting, "urlopen", _Recorder())
    monkeypatch.setattr(
        alerting,
        "notifier_from_environment",
        lambda *_: PushoverNotifier("t", "u"),
    )

    assert alerting.main(["--send-test"]) == 0
    assert "delivered  True" in capsys.readouterr().out
