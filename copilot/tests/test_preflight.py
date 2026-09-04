"""
Tests for the pre-open quote check.

The feed half is exercised by running the preflight. What is testable here is the rule
that turns what the watcher saw into a pass or a fail, and the market-state note that
keeps a fail outside a session from reading as a silent feed.

"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from decimal import Decimal

from copilot.live.preflight import MAX_QUOTE_AGE_SECS
from copilot.live.preflight import MIN_QUOTES
from copilot.live.preflight import NANOS_PER_SECOND
from copilot.live.preflight import QuoteSample
from copilot.live.preflight import market_status
from copilot.live.preflight import quote_checks


NOW = 1_800_000_000 * NANOS_PER_SECOND


def sample(
    *,
    bid: str = "100.10",
    ask: str = "100.12",
    age_secs: int = 5,
    count: int = 3,
) -> QuoteSample:
    arrived = NOW - age_secs * NANOS_PER_SECOND
    return QuoteSample(
        instrument_id="AAPL=STK.SMART",
        bid=Decimal(bid),
        ask=Decimal(ask),
        ts_event=arrived,
        ts_init=arrived,
        count=count,
    )


def only(checks):
    assert len(checks) == 1
    return checks[0]


def test_a_fresh_uncrossed_run_of_quotes_passes() -> None:
    c = only(quote_checks({"AAPL=STK.SMART": sample()}, now_ns=NOW))
    assert c.passed
    assert c.name == "quote_AAPL"
    assert "3 quotes" in c.observed


def test_no_quote_at_all_fails_and_says_none() -> None:
    c = only(quote_checks({"AAPL=STK.SMART": None}, now_ns=NOW, note="market closed"))
    assert not c.passed
    assert c.observed == "none"
    assert c.note == "market closed"


def test_one_isolated_quote_is_not_sufficient() -> None:
    assert MIN_QUOTES > 1
    assert not only(quote_checks({"AAPL=STK.SMART": sample(count=1)}, now_ns=NOW)).passed


def test_a_stale_quote_fails() -> None:
    stale = sample(age_secs=MAX_QUOTE_AGE_SECS + 1)
    assert not only(quote_checks({"AAPL=STK.SMART": stale}, now_ns=NOW)).passed
    edge = sample(age_secs=MAX_QUOTE_AGE_SECS)
    assert only(quote_checks({"AAPL=STK.SMART": edge}, now_ns=NOW)).passed


def test_a_crossed_or_locked_quote_fails() -> None:
    assert not only(
        quote_checks({"A=STK.SMART": sample(bid="100.12", ask="100.10")}, now_ns=NOW),
    ).passed
    assert not only(
        quote_checks({"A=STK.SMART": sample(bid="100.10", ask="100.10")}, now_ns=NOW),
    ).passed


def test_a_zero_bid_fails() -> None:
    # The adapter builds a quote with a zero side when it has only seen the other one.
    assert not only(quote_checks({"A=STK.SMART": sample(bid="0", ask="100.10")}, now_ns=NOW)).passed


def test_every_instrument_gets_its_own_check() -> None:
    checks = quote_checks({"AAPL=STK.SMART": sample(), "SCHX=STK.SMART": None}, now_ns=NOW)
    assert [c.name for c in checks] == ["quote_AAPL", "quote_SCHX"]
    assert [c.passed for c in checks] == [True, False]


def moment(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone(UTC)


def test_the_market_state_names_where_the_session_stands() -> None:
    assert market_status(moment("2026-09-05T12:00:00-04:00")).startswith("market closed: not")
    assert market_status(moment("2026-09-07T12:00:00-04:00")).startswith("market closed: not")
    assert market_status(moment("2026-09-08T03:00:00-04:00")) == "market closed: before pre-market"
    assert market_status(moment("2026-09-08T08:30:00-04:00")) == "pre-market"
    assert market_status(moment("2026-09-08T10:00:00-04:00")) == "session open"
    assert market_status(moment("2026-09-08T16:30:00-04:00")) == "market closed: after the close"


def test_an_early_close_ends_the_session_at_one() -> None:
    assert market_status(moment("2026-11-27T13:30:00-05:00")) == "market closed: after the close"
