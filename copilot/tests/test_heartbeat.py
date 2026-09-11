"""
The heartbeat has one job - be noticed when it stops - and must never stop the day itself.
"""

from __future__ import annotations

from typing import Self
from urllib.error import URLError

from copilot.live.heartbeat import ping


class _Response:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def test_an_unconfigured_heartbeat_says_nothing_outside_will_notice() -> None:
    called: list[object] = []

    ok, line = ping("", opener=lambda *a, **k: called.append(a))

    assert ok is False
    assert "nothing outside this host will notice" in line
    assert called == []


def test_a_2xx_answer_is_a_beat() -> None:
    assert ping("https://watcher.example/push/x", opener=lambda *a, **k: _Response(200)) == (
        True,
        "heartbeat pinged",
    )


def test_a_watcher_that_is_down_does_not_raise_into_the_day() -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise URLError("connection refused")

    ok, line = ping("https://watcher.example/push/x", opener=refuse)

    assert ok is False
    assert "connection refused" in line


def test_a_non_2xx_answer_is_reported_not_counted() -> None:
    ok, line = ping("https://watcher.example/push/x", opener=lambda *a, **k: _Response(404))

    assert ok is False
    assert "404" in line


def test_a_non_http_url_is_not_opened() -> None:
    called: list[object] = []

    ok, _ = ping("file:///etc/passwd", opener=lambda *a, **k: called.append(a))

    assert ok is False
    assert called == []
