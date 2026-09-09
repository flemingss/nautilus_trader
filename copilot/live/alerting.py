"""
Telling the operator something happened, when the operator is asleep.

The playbook makes alerting a gate rather than a nicety: unattended paper opens only
*after alerts and recovery drills pass*, the monitoring-end policy requires an alert on
*any order whose status cannot be confirmed*, safe mode's second limb is *preserve state
and alert*, and the kill switch itself triggers on *a critical alert unacknowledged by
deadline*. Until this module existed no code sent an alert anywhere.
``failure_injection`` proved the system notices; nothing proved anyone was told.

The shape follows from the operator, not from the transport. The US session runs while
the operator sleeps, so three questions decide everything:

- **Does this need to wake someone?** Three severities, and they are not decoration.
  ``INFO`` records, ``WARNING`` shows immediately, ``CRITICAL`` wakes and keeps waking.
- **Did the message arrive, and did anyone see it?** A sent alert is not an acknowledged
  alert, and the playbook's kill switch keys on the difference. ``CRITICAL`` goes out at
  Pushover's emergency priority, which retries until acknowledged and issues a receipt;
  :func:`receipt_status` reads that receipt so "unacknowledged by deadline" is a fact the
  code can check rather than a hope.
- **What happens when the alert itself fails?** It never raises into the caller. A
  session must not die because a notification service did, and equally must not believe
  it warned someone when it did not, so every send returns a :class:`Delivery` saying
  which it was.

**An unconfigured system is loud, not silent.** With no credentials exported the fallback
still writes every alert to stderr and reports ``delivered=False``. The failure mode this
avoids is the expensive one: a notifier that swallows alerts looks exactly like a system
with nothing to report.

**Flooding is a delivery failure too.** An unattended loop can send thousands of
identical alerts, burn the monthly quota and train the operator to ignore the app.
:class:`FloodGuard` collapses repeats of the same severity and title inside a window and
reports the suppressed count on the next one that gets through, so nothing is lost
quietly.

Secrets arrive from the environment at the CLI boundary and are passed down as arguments,
the same rule the data clients follow: no module below the boundary can acquire a way to
write one to disk.

Verify the path before relying on it, which is the whole point of a gate::

    python -m copilot.live.alerting --send-test
    python -m copilot.live.alerting --send-test --severity critical
    python -m copilot.live.alerting --receipt <receipt-id>

"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from typing import Protocol
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request
from urllib.request import urlopen

from copilot.paths import PUSHOVER_TOKEN_ENV
from copilot.paths import PUSHOVER_USER_KEY_ENV


if TYPE_CHECKING:
    from collections.abc import Mapping
    from collections.abc import MutableMapping
    from typing import TextIO


API_BASE = "https://api.pushover.net/1"
USER_AGENT = "nautilus-copilot/alerting"
TIMEOUT_SECONDS = 10.0

RETRY_SECONDS_MIN = 30
"""
Pushover refuses an emergency retry interval below this.
"""

EXPIRE_SECONDS_MAX = 10800
"""
Pushover's ceiling on how long an emergency alert keeps retrying, three hours.
"""

RETRY_CAP = 50
"""
Pushover stops after this many retries whatever ``expire`` says, so a retry interval
that implies more is a configuration that will not do what it claims.
"""

DEFAULT_RETRY_SECONDS = 120
DEFAULT_EXPIRE_SECONDS = 3600
"""
The declared acknowledgement deadline: one hour, retrying every two minutes, thirty
retries. The playbook requires the deadline to be declared rather than assumed, and this
is that declaration. `EXPIRE_SECONDS_ENV` overrides it for an operator who wants a
different one.
"""

DEFAULT_FLOOD_WINDOW_SECONDS = 300.0

RETRY_SECONDS_ENV = "COPILOT_ALERT_RETRY_SECONDS"
EXPIRE_SECONDS_ENV = "COPILOT_ALERT_EXPIRE_SECONDS"

TITLE_PREFIX = "copilot"


class Severity(StrEnum):
    """
    How hard this alert should try to reach a sleeping operator.
    """

    INFO = "INFO"
    """
    Worth recording, not worth waking anyone.

    Delivered without sound.

    """

    WARNING = "WARNING"
    """
    Show it now, bypassing quiet hours, but do not demand a response.
    """

    CRITICAL = "CRITICAL"
    """
    Wake the operator and keep retrying until acknowledged.

    The playbook's kill switch triggers on one of these going unacknowledged past the
    deadline, so a severity chosen carelessly here has teeth.

    """


PUSHOVER_PRIORITY = {
    Severity.INFO: -1,
    Severity.WARNING: 1,
    Severity.CRITICAL: 2,
}
"""
Severity to Pushover priority.

``-1`` shows with no sound, ``1`` bypasses quiet hours,
``2`` is emergency and is the only one that retries and issues a receipt.

"""


@dataclass(frozen=True)
class Alert:
    """
    One thing the operator needs to know.
    """

    severity: Severity
    title: str
    body: str
    context: Mapping[str, str] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def key(self) -> tuple[str, str]:
        """
        What makes two alerts "the same one again" for flood control.
        """
        return (str(self.severity), self.title)

    def rendered(self) -> str:
        """
        Render the message body as it reaches the phone, context included.

        The context lines are the difference between an alert that names a problem and
        one that can be acted on from a phone at three in the morning.

        """
        if not self.context:
            return self.body
        lines = [f"{name}: {value}" for name, value in sorted(self.context.items())]
        return self.body + "\n\n" + "\n".join(lines)


@dataclass(frozen=True)
class Delivery:
    """
    What happened when an alert was sent.

    ``delivered`` is the only field a caller must check. ``receipt`` is present only for
    an emergency-priority send and is what :func:`receipt_status` needs to answer whether
    anyone acknowledged it.

    """

    alert: Alert
    delivered: bool
    notifier: str
    detail: str = ""
    receipt: str | None = None
    suppressed: int = 0
    """
    How many identical alerts the flood guard collapsed into this one.
    """


@dataclass(frozen=True)
class Acknowledgement:
    """
    The state of an emergency alert, read back from its receipt.
    """

    receipt: str
    acknowledged: bool
    expired: bool
    acknowledged_at: datetime | None = None

    @property
    def outstanding(self) -> bool:
        """
        True while the alert is still retrying and nobody has acknowledged it.

        An alert that expired unacknowledged is **not** outstanding by this reading: it
        stopped retrying, and the deadline the operator declared has passed. That is the
        kill switch's condition, not a reason to keep waiting.

        """
        return not self.acknowledged and not self.expired


class Notifier(Protocol):
    """
    Anything that can carry an alert to a human.
    """

    @property
    def name(self) -> str:
        """
        What to record as the delivery route.
        """
        ...  # pragma: no cover - protocol declaration

    def send(self, alert: Alert) -> Delivery:
        """
        Deliver one alert, returning what happened rather than raising.
        """
        ...  # pragma: no cover - protocol declaration


class StreamNotifier:
    """
    The fallback when no transport is configured: write it where someone might see it.

    Deliberately **not** a null notifier. Reporting ``delivered=True`` for an alert that
    reached nobody would let the unattended gate pass on a system with no alerting at
    all, which is the exact failure the gate exists to catch.

    """

    def __init__(self, stream: TextIO | None = None) -> None:
        """
        Write alerts to ``stream``, defaulting to stderr.
        """
        self._stream = stream if stream is not None else sys.stderr

    @property
    def name(self) -> str:
        """
        The delivery route recorded on every :class:`Delivery`.
        """
        return "stream"

    def send(self, alert: Alert) -> Delivery:
        """
        Write the alert to the stream and report it as undelivered.
        """
        stamp = alert.occurred_at.isoformat()
        print(
            f"[{stamp}] ALERT {alert.severity} {alert.title}\n{alert.rendered()}",
            file=self._stream,
        )
        return Delivery(
            alert=alert,
            delivered=False,
            notifier=self.name,
            detail=(
                f"no notifier configured: export {PUSHOVER_TOKEN_ENV} and "
                f"{PUSHOVER_USER_KEY_ENV} to deliver alerts"
            ),
        )


class PushoverNotifier:
    """
    Pushover as the transport, with emergency priority reserved for ``CRITICAL``.

    Chosen because the operator already runs it, and because its emergency priority is a
    close match for the playbook's requirement: it retries until acknowledged and hands
    back a receipt, which turns *acknowledge critical alerts within the deadline* from a
    procedure into something the code can verify.

    Credentials are passed in, never read from the environment here. Use
    :func:`notifier_from_environment` at a CLI boundary.

    """

    def __init__(
        self,
        token: str,
        user_key: str,
        *,
        retry_seconds: int = DEFAULT_RETRY_SECONDS,
        expire_seconds: int = DEFAULT_EXPIRE_SECONDS,
        timeout_seconds: float = TIMEOUT_SECONDS,
        base_url: str = API_BASE,
    ) -> None:
        """
        Hold the credentials and a deadline Pushover will honour.
        """
        if retry_seconds < RETRY_SECONDS_MIN:
            raise ValueError(
                f"retry_seconds must be at least {RETRY_SECONDS_MIN}, got {retry_seconds}",
            )
        if expire_seconds > EXPIRE_SECONDS_MAX:
            raise ValueError(
                f"expire_seconds must be at most {EXPIRE_SECONDS_MAX}, got {expire_seconds}",
            )
        if expire_seconds // retry_seconds > RETRY_CAP:
            raise ValueError(
                f"retry_seconds={retry_seconds} over expire_seconds={expire_seconds} implies "
                f"more than {RETRY_CAP} retries, which Pushover caps; widen the interval",
            )
        self._token = token
        self._user_key = user_key
        self._retry_seconds = retry_seconds
        self._expire_seconds = expire_seconds
        self._timeout_seconds = timeout_seconds
        self._base_url = base_url

    @property
    def name(self) -> str:
        """
        The delivery route recorded on every :class:`Delivery`.
        """
        return "pushover"

    def send(self, alert: Alert) -> Delivery:
        """
        Post one alert, returning a :class:`Delivery` whatever goes wrong.
        """
        priority = PUSHOVER_PRIORITY[alert.severity]
        params: dict[str, object] = {
            "token": self._token,
            "user": self._user_key,
            "title": f"{TITLE_PREFIX} {alert.severity}: {alert.title}",
            "message": alert.rendered(),
            "priority": priority,
            "timestamp": int(alert.occurred_at.timestamp()),
        }
        if priority == PUSHOVER_PRIORITY[Severity.CRITICAL]:
            params["retry"] = self._retry_seconds
            params["expire"] = self._expire_seconds

        try:
            payload = self._post("messages.json", params)
        except (HTTPError, URLError, TimeoutError, ValueError, TypeError) as e:
            return Delivery(
                alert=alert,
                delivered=False,
                notifier=self.name,
                detail=f"{type(e).__name__}: {e}",
            )

        if payload.get("status") != 1:
            errors = payload.get("errors")
            return Delivery(
                alert=alert,
                delivered=False,
                notifier=self.name,
                detail=f"pushover rejected the message: {errors}",
            )

        receipt = payload.get("receipt")
        return Delivery(
            alert=alert,
            delivered=True,
            notifier=self.name,
            detail=str(payload.get("request", "")),
            receipt=str(receipt) if isinstance(receipt, str) else None,
        )

    def receipt_status(self, receipt: str) -> Acknowledgement:
        """
        Ask whether an emergency alert has been acknowledged yet.

        This is what makes the kill switch's *critical alert unacknowledged by deadline*
        checkable. Pushover asks for no more than one poll every five seconds.

        """
        query = urlencode({"token": self._token})
        request = Request(  # noqa: S310 - fixed https scheme from API_BASE
            f"{self._base_url}/receipts/{receipt}.json?{query}",
            headers={"User-Agent": USER_AGENT},
        )
        with urlopen(request, timeout=self._timeout_seconds) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))

        acknowledged_at = payload.get("acknowledged_at") or 0
        return Acknowledgement(
            receipt=receipt,
            acknowledged=bool(payload.get("acknowledged")),
            expired=bool(payload.get("expired")),
            acknowledged_at=(
                datetime.fromtimestamp(int(acknowledged_at), tz=UTC) if acknowledged_at else None
            ),
        )

    def _post(self, endpoint: str, params: Mapping[str, object]) -> Mapping[str, object]:
        request = Request(  # noqa: S310 - fixed https scheme from API_BASE
            f"{self._base_url}/{endpoint}",
            data=urlencode(params).encode("utf-8"),
            headers={"User-Agent": USER_AGENT},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:  # noqa: S310
                body = response.read().decode("utf-8")
        except HTTPError as e:
            # Pushover reports a rejected message as 4xx with the reasons in the body,
            # which is more useful to the operator than the status line alone.
            body = e.read().decode("utf-8", errors="replace")
            if not body.strip().startswith("{"):
                raise
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise TypeError("Pushover response must be an object")
        return payload


class FloodGuard:
    """
    Collapse repeats of the same alert inside a window, and count what was collapsed.

    An unattended failure loop is the case this exists for. Without it a stuck condition
    sends an alert per iteration, exhausts the monthly quota and teaches the operator
    that the app is noise, which costs more than the alerts were worth.

    Nothing is dropped silently: the next alert that gets through carries the suppressed
    count, so the record says how long the condition was repeating.

    """

    def __init__(self, window_seconds: float = DEFAULT_FLOOD_WINDOW_SECONDS) -> None:
        """
        Collapse repeats seen within ``window_seconds`` of the last one through.
        """
        self._window_seconds = window_seconds
        self._last_sent: MutableMapping[tuple[str, str], float] = {}
        self._suppressed: MutableMapping[tuple[str, str], int] = {}

    def admit(self, alert: Alert, *, now: float | None = None) -> int | None:
        """
        Return the suppressed count to report, or ``None`` if this alert is a repeat.

        A ``CRITICAL`` alert is never suppressed. Its whole purpose is to keep asking
        until a human answers, and a guard that muted it would defeat the acknowledgement
        the kill switch depends on.

        """
        moment = time.monotonic() if now is None else now
        if alert.severity is Severity.CRITICAL:
            return self._suppressed.pop(alert.key, 0)

        previous = self._last_sent.get(alert.key)
        if previous is not None and moment - previous < self._window_seconds:
            self._suppressed[alert.key] = self._suppressed.get(alert.key, 0) + 1
            return None

        self._last_sent[alert.key] = moment
        return self._suppressed.pop(alert.key, 0)


class Alerter:
    """
    The thing callers hold: a notifier, a flood guard, and a promise not to raise.

    Nothing below this class is allowed to take a trading session down. Every failure
    becomes a :class:`Delivery` with ``delivered=False`` and a reason, and the caller
    decides what that is worth.

    """

    def __init__(self, notifier: Notifier, guard: FloodGuard | None = None) -> None:
        """
        Wrap a notifier with flood control and the promise not to raise.
        """
        self._notifier = notifier
        self._guard = guard if guard is not None else FloodGuard()

    @property
    def notifier_name(self) -> str:
        """
        Which transport is configured, for a session record to name.
        """
        return self._notifier.name

    def send(self, alert: Alert) -> Delivery:
        """
        Deliver an alert, or say precisely why it did not go.
        """
        suppressed = self._guard.admit(alert)
        if suppressed is None:
            return Delivery(
                alert=alert,
                delivered=False,
                notifier=self._notifier.name,
                detail="suppressed as a repeat inside the flood window",
            )
        try:
            delivery = self._notifier.send(alert)
        except Exception as e:  # noqa: BLE001 - an alert must never take a session down
            return Delivery(
                alert=alert,
                delivered=False,
                notifier=self._notifier.name,
                detail=f"notifier raised {type(e).__name__}: {e}",
                suppressed=suppressed,
            )
        if suppressed:
            return Delivery(
                alert=delivery.alert,
                delivered=delivery.delivered,
                notifier=delivery.notifier,
                detail=delivery.detail,
                receipt=delivery.receipt,
                suppressed=suppressed,
            )
        return delivery

    def info(self, title: str, body: str, context: Mapping[str, str] | None = None) -> Delivery:
        """
        Record something without waking anyone.
        """
        return self.send(Alert(Severity.INFO, title, body, context or {}))

    def warning(self, title: str, body: str, context: Mapping[str, str] | None = None) -> Delivery:
        """
        Show something immediately without demanding a response.
        """
        return self.send(Alert(Severity.WARNING, title, body, context or {}))

    def critical(self, title: str, body: str, context: Mapping[str, str] | None = None) -> Delivery:
        """
        Wake the operator and keep asking until acknowledged.
        """
        return self.send(Alert(Severity.CRITICAL, title, body, context or {}))


def notifier_from_environment(environ: Mapping[str, str] | None = None) -> Notifier:
    """
    Build the configured notifier, or the loud fallback when nothing is configured.

    This is the secret boundary: the credentials are read here and handed to the notifier
    as arguments, so nothing below can reach the environment for them.

    """
    source = os.environ if environ is None else environ
    token = source.get(PUSHOVER_TOKEN_ENV, "").strip()
    user_key = source.get(PUSHOVER_USER_KEY_ENV, "").strip()
    if not token or not user_key:
        return StreamNotifier()
    return PushoverNotifier(
        token,
        user_key,
        retry_seconds=int(source.get(RETRY_SECONDS_ENV, DEFAULT_RETRY_SECONDS)),
        expire_seconds=int(source.get(EXPIRE_SECONDS_ENV, DEFAULT_EXPIRE_SECONDS)),
    )


def alerter_from_environment(environ: Mapping[str, str] | None = None) -> Alerter:
    """
    Build the alerter a session needs, reading credentials from the environment.
    """
    return Alerter(notifier_from_environment(environ))


def _send_test(args: argparse.Namespace) -> int:
    severity = Severity(args.severity.upper())
    notifier = notifier_from_environment()
    alerter = Alerter(notifier)
    delivery = alerter.send(
        Alert(
            severity=severity,
            title="alerting self-check",
            body=(
                "If this reached you, the alerting path works. Sent deliberately by "
                "python -m copilot.live.alerting --send-test."
            ),
            context={"host": os.uname().nodename, "notifier": notifier.name},
        ),
    )
    print(f"notifier   {delivery.notifier}")
    print(f"delivered  {delivery.delivered}")
    if delivery.detail:
        print(f"detail     {delivery.detail}")
    if delivery.receipt:
        print(f"receipt    {delivery.receipt}")
        print(f"           poll it: python -m copilot.live.alerting --receipt {delivery.receipt}")
    if not delivery.delivered:
        print(
            "\nAlerting is NOT working. The unattended-paper gate requires it, and a "
            "system that cannot tell you it broke is not ready to run unattended.",
        )
    return 0 if delivery.delivered else 1


def _check_receipt(args: argparse.Namespace) -> int:
    notifier = notifier_from_environment()
    if not isinstance(notifier, PushoverNotifier):
        print(
            f"no transport configured: export {PUSHOVER_TOKEN_ENV} and {PUSHOVER_USER_KEY_ENV}",
        )
        return 1
    state = notifier.receipt_status(args.receipt)
    print(f"receipt        {state.receipt}")
    print(f"acknowledged   {state.acknowledged}")
    print(f"expired        {state.expired}")
    print(f"outstanding    {state.outstanding}")
    if state.acknowledged_at is not None:
        print(f"acknowledged at {state.acknowledged_at.isoformat()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """
    Operator entry point: prove the path works, or read an acknowledgement back.
    """
    parser = argparse.ArgumentParser(
        description="Verify the alerting path, or poll an emergency alert's receipt.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--send-test",
        action="store_true",
        help="send one alert to the configured transport",
    )
    group.add_argument(
        "--receipt",
        help="poll an emergency alert's receipt for acknowledgement",
    )
    parser.add_argument(
        "--severity",
        default=Severity.WARNING.value,
        choices=[s.value.lower() for s in Severity] + [s.value for s in Severity],
        help="severity for --send-test (default: warning)",
    )
    args = parser.parse_args(argv)
    if args.receipt:
        return _check_receipt(args)
    return _send_test(args)


def receipt_status(notifier: Notifier, receipt: str) -> Acknowledgement | None:
    """
    Read an emergency alert's acknowledgement, when the transport supports one.

    Returns ``None`` for a transport with no receipts, which is the honest answer: the
    alert's fate is unknown rather than acknowledged.

    """
    if not isinstance(notifier, PushoverNotifier):
        return None
    return notifier.receipt_status(receipt)


__all__ = [
    "Acknowledgement",
    "Alert",
    "Alerter",
    "Delivery",
    "FloodGuard",
    "Notifier",
    "PushoverNotifier",
    "Severity",
    "StreamNotifier",
    "alerter_from_environment",
    "main",
    "notifier_from_environment",
    "receipt_status",
]


if __name__ == "__main__":
    raise SystemExit(main())
