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

from copilot.live.alerting import Severity
from copilot.live.day import EVENING
from copilot.live.day import MORNING
from copilot.live.day import REQUIRED_TIMEZONE_ALIAS
from copilot.live.day import SWEEP
from copilot.live.day import TIMEZONE_ALIASES_ENV
from copilot.live.day import Connection
from copilot.live.day import Step
from copilot.live.day import StepResult
from copilot.live.day import _clock_line
from copilot.live.day import alerts_for
from copilot.live.day import closed_session
from copilot.live.day import completed_record
from copilot.live.day import evening_steps
from copilot.live.day import main
from copilot.live.day import monitoring_session
from copilot.live.day import morning_steps
from copilot.live.day import operator_zone
from copilot.live.day import registered_symbols
from copilot.live.day import required_environment
from copilot.live.day import run_steps
from copilot.live.day import scheduled_session
from copilot.live.day import session_clock
from copilot.live.day import summarise
from copilot.live.day import sweep_steps
from copilot.paths import HEARTBEAT_URL_ENV
from copilot.paths import MARKETSTACK_API_KEY_ENV
from copilot.paths import OPERATOR_TZ_ENV
from copilot.paths import PUSHOVER_TOKEN_ENV
from copilot.paths import PUSHOVER_USER_KEY_ENV


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


def test_the_evening_appends_and_scans_before_it_proves_the_broker_and_warms() -> None:
    """
    Changed 2026-09-10: the evening appends first, because the vendor publishes a session's
    bar hours after the morning's append has already run, and the warm-up needs that bar.
    """
    steps = evening_steps(
        "~/cat",
        session=date(2026, 9, 8),
        connection=CONNECTION,
        allocation=None,
        risk_fraction=None,
    )
    assert [s.name for s in steps] == [
        "append",
        "corporate actions",
        "preflight",
        "warmup",
        "basket",
        "sweep",
    ]


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
    assert not by_name["append"].stops_on_failure, "the warm-up is the gate, not the append"
    assert by_name["corporate actions"].stops_on_failure
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


def test_the_evening_needs_the_alias_an_account_and_now_the_vendor_key() -> None:
    missing = required_environment(EVENING, environ={}, account="")
    assert len(missing) == 3
    assert any(TIMEZONE_ALIASES_ENV in m for m in missing)
    assert any(MARKETSTACK_API_KEY_ENV in m for m in missing)
    complete = {TIMEZONE_ALIASES_ENV: REQUIRED_TIMEZONE_ALIAS, MARKETSTACK_API_KEY_ENV: "k"}
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


def test_monitoring_end_needs_the_broker_but_not_the_vendor() -> None:
    missing = required_environment(SWEEP, environ={}, account="")
    assert len(missing) == 2
    assert not any(MARKETSTACK_API_KEY_ENV in m for m in missing)


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


# ---------------------------------------------------------------- fired by a timer

ET = ZoneInfo("America/New_York")


def eastern(*args: int) -> datetime:
    return datetime(*args, tzinfo=ET)


@pytest.mark.parametrize(
    ("phase", "when", "session"),
    [
        (MORNING, eastern(2026, 9, 11, 17, 0), date(2026, 9, 11)),  # Friday after the close
        (EVENING, eastern(2026, 9, 11, 8, 30), date(2026, 9, 11)),  # Friday before the open
        (SWEEP, eastern(2026, 9, 11, 10, 30), date(2026, 9, 11)),  # Friday, monitoring end
        (EVENING, eastern(2026, 11, 27, 8, 30), date(2026, 11, 27)),  # an early close still opens
    ],
)
def test_a_scheduled_phase_acts_on_todays_session(
    phase: str,
    when: datetime,
    session: date,
) -> None:
    assert scheduled_session(phase, when) == (session, "")


@pytest.mark.parametrize(
    ("phase", "when", "because"),
    [
        (EVENING, eastern(2026, 9, 12, 8, 30), "not a trading session"),  # Saturday
        (MORNING, eastern(2026, 9, 13, 17, 0), "not a trading session"),  # Sunday
        (EVENING, eastern(2026, 9, 7, 8, 30), "not a trading session"),  # Labor Day
        (MORNING, eastern(2026, 11, 26, 17, 0), "not a trading session"),  # Thanksgiving
        (MORNING, eastern(2026, 9, 11, 15, 0), "has not closed yet"),
        (EVENING, eastern(2026, 9, 11, 9, 45), "already opened"),
        (SWEEP, eastern(2026, 9, 11, 8, 0), "has not opened yet"),
    ],
)
def test_a_scheduled_phase_with_no_session_today_has_nothing_to_do(
    phase: str,
    when: datetime,
    because: str,
) -> None:
    session, reason = scheduled_session(phase, when)
    assert session is None
    assert because in reason


def test_the_saturday_evening_that_would_have_prepared_monday_does_not() -> None:
    """
    The defect the guard exists for: unguarded, Saturday prepares Monday and Monday again.
    """
    assert scheduled_session(EVENING, eastern(2026, 9, 12, 8, 30))[0] is None
    assert scheduled_session(EVENING, eastern(2026, 9, 14, 8, 30))[0] == date(2026, 9, 14)


def test_a_passing_record_completes_its_session_and_a_failing_one_does_not(tmp_path) -> None:
    def file(name: str, **record: object) -> None:
        (tmp_path / name).write_text(__import__("json").dumps(record))

    file("day_evening_20260911T123000Z.json", session="2026-09-11", exit_code=1, dry_run=False)
    assert completed_record(EVENING, date(2026, 9, 11), tmp_path) is None

    file("day_evening_20260911T124500Z.json", session="2026-09-11", exit_code=0, dry_run=True)
    assert completed_record(EVENING, date(2026, 9, 11), tmp_path) is None, "a dry run did nothing"

    file("day_evening_20260911T130000Z.json", session="2026-09-11", exit_code=0, dry_run=False)
    found = completed_record(EVENING, date(2026, 9, 11), tmp_path)
    assert found is not None
    assert found.name == "day_evening_20260911T130000Z.json"
    assert completed_record(MORNING, date(2026, 9, 11), tmp_path) is None, "phases are separate"
    assert completed_record(EVENING, date(2026, 9, 14), tmp_path) is None


def test_a_scheduled_run_on_a_weekend_exits_zero_without_needing_anything(
    monkeypatch,
    capsys,
) -> None:
    """
    No environment, no broker: a timer on a Saturday must not fail for want of either.
    """
    saturday = eastern(2026, 9, 12, 8, 30)

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN206 - matches datetime.now
            return saturday.astimezone(tz)

    monkeypatch.setattr("copilot.live.day.datetime", Frozen)
    for name in (TIMEZONE_ALIASES_ENV, MARKETSTACK_API_KEY_ENV):
        monkeypatch.delenv(name, raising=False)

    assert main(["evening", "--scheduled"]) == 0
    assert "nothing to do" in capsys.readouterr().out


def test_scheduled_and_an_explicit_session_are_refused_together() -> None:
    with pytest.raises(SystemExit):
        main(["evening", "--scheduled", "--session", "2026-09-14"])


def test_a_sweep_after_the_open_names_todays_session_not_tomorrows() -> None:
    """
    Found 2026-09-10: the sweep used the evening's rule and filed tomorrow's session.
    """
    assert monitoring_session(eastern(2026, 9, 11, 10, 30)) == date(2026, 9, 11)
    assert monitoring_session(eastern(2026, 9, 11, 8, 0)) == date(2026, 9, 11)
    assert monitoring_session(eastern(2026, 9, 12, 10, 30)) == date(2026, 9, 14)


# --------------------------------------------------------------------------- who is told


def _result(name: str, code: int | None, *, skipped: bool = False) -> StepResult:
    return StepResult(name, ("python", "-m", name), code, 1.0, skipped=skipped)


def test_a_clean_morning_sends_only_its_summary() -> None:
    steps = morning_steps("~/cat", today=date(2026, 9, 11))
    results = tuple(_result(s.name, 0) for s in steps)

    alerts = alerts_for(MORNING, "2026-09-11", steps, results)

    assert [a.severity for a in alerts] == [Severity.INFO]
    assert "4 of 4 steps passed" in alerts[0].body


def test_a_stopping_failure_warns_and_names_what_it_protects() -> None:
    steps = evening_steps(
        "~/cat",
        session=date(2026, 9, 11),
        connection=CONNECTION,
        allocation=None,
        risk_fraction=None,
    )
    codes = {"append": 0, "corporate actions": 0, "preflight": 0, "warmup": 1}
    results = tuple(_result(s.name, codes.get(s.name), skipped=s.name not in codes) for s in steps)

    alerts = alerts_for(EVENING, "2026-09-11", steps, results)

    assert len(alerts) == 1, "skipped steps are not failures, and an evening sends no summary"
    assert alerts[0].severity == Severity.WARNING
    assert alerts[0].title == "day evening: warmup failed"
    assert "stopped the day" in alerts[0].body
    assert "cannot warm every activation" in alerts[0].body


def test_a_failed_sweep_is_not_repeated_by_the_day_it_ran_in() -> None:
    """
    The sweep raises its own CRITICAL; a second WARNING for the same order is noise.
    """
    steps = sweep_steps(CONNECTION)
    assert steps[0].alerts_itself

    assert alerts_for(SWEEP, "2026-09-11", steps, (_result("sweep", 1),)) == []


def test_a_reporting_failure_still_warns() -> None:
    steps = morning_steps("~/cat", today=date(2026, 9, 11))
    results = tuple(_result(s.name, 3 if s.name == "append" else 0) for s in steps)

    alerts = alerts_for(MORNING, "2026-09-11", steps, results)

    assert [a.severity for a in alerts] == [Severity.WARNING, Severity.INFO]
    assert "stopped the day" not in alerts[0].body


def test_a_scheduled_run_refuses_without_alerting_credentials() -> None:
    complete = {
        TIMEZONE_ALIASES_ENV: REQUIRED_TIMEZONE_ALIAS,
        MARKETSTACK_API_KEY_ENV: "k",
    }
    assert required_environment(EVENING, environ=complete, account="DU1") == ()

    missing = required_environment(EVENING, environ=complete, account="DU1", scheduled=True)

    assert len(missing) == 1
    assert PUSHOVER_TOKEN_ENV in missing[0]
    armed = {**complete, PUSHOVER_TOKEN_ENV: "t", PUSHOVER_USER_KEY_ENV: "u"}
    assert required_environment(EVENING, environ=armed, account="DU1", scheduled=True) == ()


def test_a_scheduled_morning_with_nothing_to_do_still_beats(monkeypatch, capsys) -> None:
    """
    A quiet weekend has to read as alive to the watcher, or it pages every Saturday.
    """
    saturday = eastern(2026, 9, 12, 17, 0)
    pinged: list[str] = []

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN206 - matches datetime.now
            return saturday.astimezone(tz)

    monkeypatch.setattr("copilot.live.day.datetime", Frozen)
    monkeypatch.setattr(
        "copilot.live.day.ping",
        lambda url: (pinged.append(url), (True, "pinged"))[1],
    )
    monkeypatch.setattr("copilot.live.day.acknowledgement_check", list)
    monkeypatch.setattr("copilot.live.day.read_latch", lambda: None)
    monkeypatch.setenv(HEARTBEAT_URL_ENV, "https://watcher.example/push/abc")

    assert main(["morning", "--scheduled"]) == 0
    assert pinged == ["https://watcher.example/push/abc"]


@pytest.mark.parametrize(
    ("trigger", "beats"),
    [("operator", True), ("unacknowledged_critical", False), ("undelivered_critical", False)],
)
def test_the_heartbeat_is_withheld_while_the_host_halted_itself_unheard(
    monkeypatch,
    capsys,
    trigger: str,
    beats: bool,
) -> None:
    """
    Audit F3: when Pushover has failed to reach anyone, the missed beat is what the watcher,
    which does not depend on it, alerts on. A halt the operator engaged is one they know of.
    """
    from copilot.live.day import beat
    from copilot.live.halt import Latch

    pinged: list[str] = []
    monkeypatch.setattr(
        "copilot.live.day.ping",
        lambda url: (pinged.append(url), (True, "pinged"))[1],
    )
    monkeypatch.setenv(HEARTBEAT_URL_ENV, "https://watcher.example/push/abc")

    beat(Latch("cb59138d", "t", trigger, "r", "vm"))

    assert bool(pinged) is beats
    assert ("heartbeat withheld" in capsys.readouterr().out) is not beats


def test_an_acknowledgement_check_that_raises_does_not_stop_the_phase(monkeypatch, capsys) -> None:
    """
    Audit F2: it ran first, outside any ``try``, so a Pushover outage stopped the sweep.
    """
    saturday = eastern(2026, 9, 12, 10, 30)

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN206 - matches datetime.now
            return saturday.astimezone(tz)

    def outage() -> list[str]:
        raise ConnectionError("api.pushover.net unreachable")

    monkeypatch.setattr("copilot.live.day.datetime", Frozen)
    monkeypatch.setattr("copilot.live.day.acknowledgement_check", outage)
    monkeypatch.setattr("copilot.live.day.read_latch", lambda: None)

    assert main(["sweep", "--scheduled"]) == 0
    out = capsys.readouterr().out
    assert "acknowledgement check failed (ConnectionError" in out
    assert "nothing to do" in out, "the phase went on to decide for itself"


def test_every_phase_checks_the_halt_first_and_says_so(monkeypatch, capsys) -> None:
    """
    Even a scheduled weekend run: an unanswered Friday alert must halt before Monday.
    """
    from copilot.live.halt import OPERATOR
    from copilot.live.halt import Latch

    saturday = eastern(2026, 9, 12, 8, 30)
    checked: list[bool] = []

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN206 - matches datetime.now
            return saturday.astimezone(tz)

    monkeypatch.setattr("copilot.live.day.datetime", Frozen)
    monkeypatch.setattr(
        "copilot.live.day.acknowledgement_check",
        lambda: (checked.append(True), ["UNACKNOWLEDGED: sweep STILL WORKING"])[1],
    )
    monkeypatch.setattr(
        "copilot.live.day.read_latch",
        lambda: Latch("3f9a1c2e", "2026-09-11T02:00:00+00:00", OPERATOR, "drill", "vm"),
    )

    assert main(["evening", "--scheduled"]) == 0
    out = capsys.readouterr().out
    assert checked == [True]
    assert "HALT LATCH ENGAGED" in out
    assert out.index("HALT LATCH ENGAGED") < out.index("nothing to do")
