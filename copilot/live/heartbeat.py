"""
A sign of life for a watcher off the host, because an alert cannot send silence.

The alerting path tells the operator what went wrong. It cannot tell them the host died at
02:00, the timer never fired, or the process hung before it reached the alerter - a dead
system and a quiet night look identical from the phone. The fix is the dead man's switch:
the morning pings a URL every day, whether or not a session ran, and something outside the
VM alerts when a ping does not arrive.

The watcher belongs off the VM - the owner's cluster monitoring, as an observer of the host
rather than its host - which is why this is a URL and not a service. Any push monitor that
treats a GET as "alive" works: Uptime Kuma's push monitors, healthchecks.io, and the like.

Like the alerter, a ping never raises into its caller. A monitoring outage must not stop the
trading day, and a failed ping is reported rather than hidden.

"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request
from urllib.request import urlopen

from copilot.live.alerting import USER_AGENT


TIMEOUT_SECONDS = 10.0


def ping(
    url: str,
    *,
    opener: Callable[..., Any] = urlopen,
    timeout: float = TIMEOUT_SECONDS,
) -> tuple[bool, str]:
    """
    GET ``url`` and return whether it answered 2xx, with a line saying what happened.
    """
    if not url:
        return False, "no heartbeat URL configured; nothing outside this host will notice it stop"
    if urlparse(url).scheme not in {"http", "https"}:
        return False, "heartbeat URL is not http(s); not pinged"
    request = Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310 - scheme checked above
    try:
        with opener(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
    except (URLError, OSError, ValueError) as e:
        return False, f"heartbeat ping failed: {e}"
    if not 200 <= status < 300:  # noqa: PLR2004 - the 2xx range
        return False, f"heartbeat ping answered HTTP {status}"
    return True, "heartbeat pinged"


__all__ = ["TIMEOUT_SECONDS", "ping"]
