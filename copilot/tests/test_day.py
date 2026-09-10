"""
Tests for the operator's day as a sequence.

The steps themselves are the commands they run and are tested where they live. What is
tested here is the thing a draft could not enforce: the order, what stops the day, what
runs regardless, and what the day refuses to start without.

"""

from __future__ import annotations

from datetime import UTC
from datetime import date
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from copilot.live.day import EVENING
from copilot.live.day import MORNING
from copilot.live.day import REQUIRED_TIMEZONE_ALIAS
from copilot.live.day import SWEEP
from copilot.live.day import TIMEZONE_ALIASES_ENV
from copilot.live.day import Connection
from copilot.live.day import Step
from copilot.live.day import _clock_line
from copilot.live.day import closed_session
from copilot.live.day import evening_steps
from copilot.live.day import morning_steps
from copilot.live.day import operator_zone
from copilot.live.day import registered_symbols
from copilot.live.day import required_environment
from copilot.live.day import run_steps
from copilot.live.day import session_clock
from copilot.live.day import summarise
from copilot.live.day import sweep_steps
from copilot.paths import MARKETSTACK_API_KEY_ENV
from copilot.paths import OPERATOR_TZ_ENV


CONNECTION = Connection(host="172.17.112.1", port=7497, account="DUT067974")


def test_the_morning_is_append_then_actions_then_verdicts_then_the_comparison() -> None:
    steps = morning_steps("~/cat", today=date(2026, 9, 5))
    assert [s.name for s in steps] == ["append", "corporate actions", "validate", "compare"]


def test_the_scan_covers_the_registry_up_to_today() -> None:
    # The scan's default window ended 2025-12-31; a 2026 split would have been missed
    # every morning by the command written to catch it.
    scan = morning_steps("~/cat", today=date(2026, 9, 5))[1]
    assert scan.argv[0] == ",".join(registered_symbols())
    assert "SCHX" in scan.argv[0]
    assert scan.argv[scan.argv.index("--to") + 1] == "2026-09-05"


def test_the_scan_stops_the_morning_and_the_append_does_not() -> None:
    steps = {s.name: s for s in morning_steps("~/cat", today=date(2026, 9, 5))}
    assert steps["corporate actions"].stops_on_failure
    assert not steps["append"].stops_on_failure
    assert steps["validate"].stops_on_failure


def test_the_verdicts_recompute_only_what_moved() -> None:
    validate = morning_steps("~/cat", today=date(2026, 9, 5))[2]
    assert "--changed" in validate.argv
    assert "--write" in validate.argv


def test_the_evening_is_preflight_then_warmup_then_basket_then_sweep() -> None:
    steps = evening_steps(
        "~/cat",
        session=date(2026, 9, 8),
        connection=CONNECTION,
        allocation=None,
        risk_fraction=None,
    )
    assert [s.name for s in steps] == ["preflight", "warmup", "basket", "sweep"]


def test_every_evening_step_names_the_same_session_and_connection() -> None:
    steps = evening_steps(
        "~/cat",
        session=date(2026, 9, 8),
        connection=CONNECTION,
        allocation=Decimal(1000),
        risk_fraction=None,
    )
    by_name = {s.name: s for s in steps}
    assert "2026-09-08" in by_name["warmup"].argv
    assert "2026-09-08" in by_name["basket"].argv
    for name in ("preflight", "basket", "sweep"):
        assert "--account" in by_name[name].argv
        assert "DUT067974" in by_name[name].argv
    assert "--all" in by_name["basket"].argv
    assert "--all" in by_name["sweep"].argv
    assert by_name["basket"].argv[by_name["basket"].argv.index("--allocation") + 1] == "1000"


def test_the_sweep_runs_whatever_the_basket_did() -> None:
    steps = evening_steps(
        "~/cat",
        session=date(2026, 9, 8),
        connection=CONNECTION,
        allocation=None,
        risk_fraction=None,
    )
    by_name = {s.name: s for s in steps}
    assert by_name["preflight"].stops_on_failure
    assert by_name["warmup"].stops_on_failure
    assert not by_name["basket"].stops_on_failure
    assert not by_name["sweep"].stops_on_failure


def test_a_stopping_failure_skips_what_follows_and_the_rest_still_records() -> None:
    steps = (
        Step("a", "m.a"),
        Step("b", "m.b", stops_on_failure=True),
        Step("c", "m.c", stops_on_failure=False),
    )
    ran: list[str] = []

    def runner(step: Step) -> int:
        ran.append(step.name)
        return 1 if step.name == "b" else 0

    results = run_steps(steps, runner=runner)
    assert ran == ["a", "b"]
    assert [r.skipped for r in results] == [False, False, True]
    assert summarise(results) == 1


def test_a_reporting_failure_lets_the_next_step_run() -> None:
    steps = (Step("basket", "m.b", stops_on_failure=False), Step("sweep", "m.s"))
    ran: list[str] = []

    def runner(step: Step) -> int:
        ran.append(step.name)
        return 1 if step.name == "basket" else 0

    results = run_steps(steps, runner=runner)
    assert ran == ["basket", "sweep"]
    assert summarise(results) == 1


def test_a_clean_day_exits_zero() -> None:
    results = run_steps((Step("a", "m.a"), Step("b", "m.b")), runner=lambda _: 0)
    assert summarise(results) == 0


def test_the_morning_needs_the_vendor_key() -> None:
    assert required_environment(MORNING, environ={}) != ()
    assert required_environment(MORNING, environ={MARKETSTACK_API_KEY_ENV: "k"}) == ()


def test_the_evening_needs_the_alias_and_an_account() -> None:
    missing = required_environment(EVENING, environ={}, account="")
    assert len(missing) == 2
    assert any(TIMEZONE_ALIASES_ENV in m for m in missing)
    complete = {TIMEZONE_ALIASES_ENV: REQUIRED_TIMEZONE_ALIAS}
    assert required_environment(EVENING, environ=complete, account="DUT067974") == ()


@pytest.fixture(autouse=True)
def _default_operator_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Clear the operator's timezone override so the clock tests read the default.

    Without this they pass or fail on whatever the machine that runs them exports, which
    is the one thing a test of a default must not do.

    """
    monkeypatch.delenv(OPERATOR_TZ_ENV, raising=False)


def test_the_alias_must_be_the_right_one() -> None:
    wrong = {TIMEZONE_ALIASES_ENV: "JST=Asia/Seoul"}
    assert required_environment(EVENING, environ=wrong, account="DUT067974") != ()


def test_the_clock_reads_on_both_sides_of_the_pacific() -> None:
    clock = session_clock(date(2026, 9, 8))
    lines = clock.lines()
    assert lines[0].startswith("open")
    assert "09:30 EDT" in lines[0]
    assert "22:30 JST" in lines[0]
    assert "11:30 EDT" in lines[1]
    assert "00:30 JST (next day)" in lines[1]
    assert "16:00 EDT" in lines[2]
    assert "05:00 JST (next day)" in lines[2]
    assert not clock.early_close


def test_an_early_close_is_named_and_ends_at_one() -> None:
    clock = session_clock(date(2026, 11, 27))
    assert clock.early_close
    assert "13:00 EST" in clock.lines()[2]
    assert "03:00 JST (next day)" in clock.lines()[2]
    assert any("EARLY CLOSE" in line for line in clock.lines())


def test_the_window_never_outlasts_the_session() -> None:
    clock = session_clock(date(2026, 11, 27))
    assert clock.window_ends <= clock.closes


def test_the_morning_knows_which_session_closed() -> None:
    saturday_jst = datetime(2026, 9, 4, 22, 0, tzinfo=UTC)  # 07:00 JST Saturday
    assert closed_session(saturday_jst) == date(2026, 9, 4)
    before_close = datetime(2026, 9, 4, 19, 0, tzinfo=UTC)  # 15:00 ET Friday
    assert closed_session(before_close) == date(2026, 9, 3)


def test_monitoring_end_is_the_sweep_alone_over_the_registry() -> None:
    steps = sweep_steps(CONNECTION)
    assert [s.name for s in steps] == ["sweep"]
    assert "--all" in steps[0].argv
    assert "DUT067974" in steps[0].argv


def test_monitoring_end_needs_what_the_evening_needs() -> None:
    assert required_environment(SWEEP, environ={}, account="") == required_environment(
        EVENING,
        environ={},
        account="",
    )


# -------------------------------------------------------- the operator's own clock


def test_the_operator_clock_defaults_to_the_playbooks_zone() -> None:
    """
    The schedule in the playbook is written in JST, so the default has to match it.
    """
    assert operator_zone() == ZoneInfo("Asia/Tokyo")


def test_the_operator_clock_follows_the_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    The operator moved from Japan to Florida mid-campaign; the printed clock followed.
    """
    monkeypatch.setenv(OPERATOR_TZ_ENV, "America/New_York")
    clock = session_clock(date(2026, 9, 8))

    assert operator_zone() == ZoneInfo("America/New_York")
    assert clock.lines()[0] == "open          09:30 EDT"
    assert "JST" not in "".join(clock.lines())


def test_the_second_column_is_dropped_when_it_repeats_the_first() -> None:
    """
    An operator in Eastern reading ``09:30 EDT   09:30 EDT`` learns nothing.

    The fixed Tokyo column was noise for an operator in Florida; repeating Eastern back
    at them is the same noise wearing different clothes, and worse, it has to be read
    before it can be dismissed.

    """
    eastern = _clock_line(
        "open",
        datetime(2026, 9, 8, 13, 30, tzinfo=UTC),
        ZoneInfo("America/New_York"),
    )
    tokyo = _clock_line("open", datetime(2026, 9, 8, 13, 30, tzinfo=UTC), ZoneInfo("Asia/Tokyo"))

    assert eastern.count("EDT") == 1
    assert tokyo.count("EDT") == 1
    assert "JST" in tokyo


def test_a_misspelt_zone_falls_back_loudly(monkeypatch, capsys) -> None:
    """
    Neither silent nor fatal.

    A display preference must not stop the trading day, and an operator reading Tokyo
    while believing they set Eastern is worse than being told.

    """
    monkeypatch.setenv(OPERATOR_TZ_ENV, "Mars/Olympus_Mons")

    assert operator_zone() == ZoneInfo("Asia/Tokyo")
    assert "not a known timezone" in capsys.readouterr().err


def test_the_ib_alias_is_not_the_operators_clock() -> None:
    """
    The regression for conflating two things that both say Asia/Tokyo.

    ``IBAPI_TIMEZONE_ALIASES`` is required on every IB connect wherever the operator
    is; without it connects fail and name nothing. Tying it to a cosmetic preference
    would let a display setting break the broker connection.

    """
    assert REQUIRED_TIMEZONE_ALIAS == "JST=Asia/Tokyo"
    assert OPERATOR_TZ_ENV != TIMEZONE_ALIASES_ENV


def test_no_session_time_moves_with_the_operators_clock(monkeypatch) -> None:
    """
    Display only.

    A clock setting that could move a window would be a charter change.

    """
    monkeypatch.delenv(OPERATOR_TZ_ENV, raising=False)
    tokyo = session_clock(date(2026, 9, 8))
    monkeypatch.setenv(OPERATOR_TZ_ENV, "America/New_York")
    eastern = session_clock(date(2026, 9, 8))

    assert (tokyo.opens, tokyo.window_ends, tokyo.closes) == (
        eastern.opens,
        eastern.window_ends,
        eastern.closes,
    )
