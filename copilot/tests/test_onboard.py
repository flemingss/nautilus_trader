"""
Tests for the onboarding sequence.

The module is a status report before it is anything else, so most of what matters is
whether it tells the truth about a symbol it cannot help with. Three real vendor shapes
drive the tests, all measured on 2026-09-04:

- **SCHX**, clean from 2017, which is the case the sequence is for.
- **SPLG**, covered from 2020 and then simply stopping on 2026-07-17. A narrow probe
  reads that as "no such symbol" and a long one as "1515 rows, fine"; both are wrong,
  and getting it wrong is what put SCHX in the basket in the first place.
- **TLT**, clean for nine years and then unpriceable through 2026. A trailing break and
  a bad start need opposite answers, and the first version of the recommendation
  conflated them - it reported that no window was clean when every window before the
  break was.

"""

from __future__ import annotations

from datetime import UTC
from datetime import date
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from copilot.data.onboard import MAX_ACTIONS
from copilot.data.onboard import TARGET_REJECTION_RATIO
from copilot.data.onboard import Coverage
from copilot.data.onboard import Step
from copilot.data.onboard import _readable
from copilot.data.onboard import _recommended_start
from copilot.data.onboard import _unusable_tail
from copilot.data.onboard import apply_steps
from copilot.data.onboard import fillable_holes
from copilot.data.onboard import main
from copilot.data.onboard import preferred_boundary
from copilot.data.onboard import register
from copilot.data.onboard import registration_name
from copilot.data.onboard import report
from copilot.strategies.activations import Lifecycle
from copilot.strategies.activations import load_activation
from copilot.validation.holdout import MAX_HOLDOUT_SHARE
from copilot.validation.holdout import MIN_HOLDOUT_SHARE


def coverage(**kwargs: object) -> Coverage:
    """
    Build a coverage report, defaulting to a clean series ending yesterday.
    """
    base = {
        "symbol": "SCHX",
        "venue": "ARCX",
        "rows": 2432,
        "first": date(2017, 1, 3),
        "last": date(2026, 9, 3),
        "recommended_start": date(2017, 1, 1),
        "unusable_by_year": {},
    }
    return Coverage(**{**base, **kwargs})  # type: ignore[arg-type]


class TestReadableClose:
    """
    Predicting the ingestion gate, before anything is fetched in bulk.
    """

    @pytest.mark.parametrize("value", ["30.49", "0.01", 87, "1234.56"])
    def test_a_whole_cent_price_is_readable(self, value: object) -> None:
        """
        What an auction prints.
        """
        assert _readable(value)

    @pytest.mark.parametrize("value", [None, "", "n/a", "0", "-1.00"])
    def test_absent_or_impossible_prices_are_not(self, value: object) -> None:
        """
        Nulls and non-positives, both seen in the vendor's 2026 rows.
        """
        assert not _readable(value)

    @pytest.mark.parametrize("value", ["85.539", "32.869", "95.945", "42.405"])
    def test_a_sub_penny_close_is_not_readable(self, value: str) -> None:
        """
        Real values from the vendor's ETF series, and the reason the gate refuses them.

        None of these is something an auction printed; they are consolidated or derived
        prices, and the strategy enters at the close, so this is the one field that
        cannot be approximately right.

        """
        assert not _readable(value)


class TestRecommendedStart:
    """
    Where a usable history begins, and what a trailing break does to that question.
    """

    def test_a_clean_series_starts_at_its_first_year(self) -> None:
        """
        History is scarce, so the answer wanted is the longest window that passes.
        """
        by_year = {2017: (0, 252), 2018: (0, 251), 2019: (0, 252)}
        assert _recommended_start(by_year) == date(2017, 1, 1)

    def test_bad_early_years_move_the_start_later(self) -> None:
        """
        The vendor's pre-2017 ETF closes, which are mostly not auction prints.
        """
        by_year = {2014: (200, 252), 2015: (180, 252), 2016: (0, 252), 2017: (0, 251)}
        assert _recommended_start(by_year) == date(2016, 1, 1)

    def test_a_trailing_break_does_not_move_the_start(self) -> None:
        """
        TLT's shape: nine clean years, then 122 of 169 sessions unpriceable in 2026.

        No start date fixes a break at the end, and searching over it reports that
        nothing is clean when everything before the break is. The tail is the patch's
        business.

        """
        by_year = {2017: (0, 252), 2018: (0, 251), 2026: (122, 169)}
        assert _recommended_start(by_year) == date(2017, 1, 1)

    def test_a_series_bad_throughout_has_no_start(self) -> None:
        """
        Refusing to name one is the useful answer; naming the least bad year is not.
        """
        assert _recommended_start({2017: (100, 252), 2018: (90, 251)}) is None


class TestUnusableTail:
    """
    Telling a break at the end from a bad patch in the middle.
    """

    def test_a_clean_series_has_no_tail(self) -> None:
        assert _unusable_tail({2017: (0, 252), 2026: (2, 169)}) == ()

    def test_the_most_recent_bad_years_are_the_tail(self) -> None:
        assert _unusable_tail({2024: (0, 252), 2025: (60, 250), 2026: (122, 169)}) == (2025, 2026)

    def test_a_bad_middle_year_is_not_a_tail(self) -> None:
        """
        The run has to reach the end, or a 2020 hole would be read as a live outage.
        """
        assert _unusable_tail({2020: (200, 253), 2021: (0, 252), 2026: (0, 169)}) == ()


class TestStaleness:
    """
    A series that stopped, which neither a narrow nor a wide probe reports usefully.
    """

    def test_a_current_series_is_not_stale(self) -> None:
        """
        Yesterday's row on this morning's run is current, not one session behind.
        """
        as_of = datetime(2026, 9, 4, 6, tzinfo=UTC)
        assert coverage(last=date(2026, 9, 3)).stale_sessions(as_of) == 0

    def test_an_unpublished_session_is_not_counted(self) -> None:
        """
        Counting it would make every symbol look stale every morning, which teaches the
        operator to ignore the line.
        """
        as_of = datetime(2026, 9, 4, 21, tzinfo=UTC)
        assert coverage(last=date(2026, 9, 3)).stale_sessions(as_of) == 0

    def test_a_stopped_series_is_stale_by_its_missing_sessions(self) -> None:
        """
        SPLG's real shape on 2026-09-04.
        """
        as_of = datetime(2026, 9, 4, 12, tzinfo=UTC)
        assert coverage(last=date(2026, 7, 17)).stale_sessions(as_of) > 30

    def test_an_uncovered_symbol_reports_no_staleness(self) -> None:
        """
        Nothing to be behind.

        The coverage step already refused it.

        """
        assert coverage(rows=0, last=None).stale_sessions(datetime.now(tz=UTC)) == 0


class TestPreferredBoundary:
    """
    Choosing the holdout pin by rule rather than by judgement.
    """

    def test_the_middle_of_the_band_wins(self) -> None:
        """
        So two people onboarding the same symbol pin the same date.
        """
        candidates = ((date(2024, 4, 1), Decimal("0.1950")), (date(2024, 7, 1), Decimal("0.1672")))
        chosen = preferred_boundary(candidates)
        assert chosen is not None
        assert chosen[0] == date(2024, 7, 1)

    def test_the_chosen_share_is_inside_the_band(self) -> None:
        """
        The rule may not produce something carve would refuse.
        """
        candidates = ((date(2025, 1, 1), Decimal("0.1781")),)
        chosen = preferred_boundary(candidates)
        assert chosen is not None
        assert MIN_HOLDOUT_SHARE <= chosen[1] <= MAX_HOLDOUT_SHARE

    def test_no_candidate_means_no_boundary(self) -> None:
        """
        A history too short to carry a holdout gets None, not the least bad option.
        """
        assert preferred_boundary(()) is None


class TestReport:
    """
    The exit code, which is what a scheduled run is judged by.
    """

    def test_a_finished_symbol_exits_zero(self) -> None:
        steps = [Step("catalog history", done=True, detail="2430 bars")]
        assert report([("SCHX.ARCX", steps)]) == 0

    def test_an_unfinished_symbol_exits_nonzero(self) -> None:
        steps = [Step("catalog history", done=False, detail="nothing stored", command="backfill")]
        assert report([("IJH.ARCX", steps)]) == 1

    def test_the_target_stays_under_the_backfill_gate(self) -> None:
        """
        A start chosen to land exactly on the gate leaves no room for unpublished
        sessions, and a survey that promises a backfill will pass must be right.
        """
        assert Decimal("0.02") > TARGET_REJECTION_RATIO


BACKFILL = ("copilot.data.backfill", "--symbols", "IJH")
VALIDATE = ("copilot.strategies.validate", "--changed", "--write")


class TestApply:
    """
    Free stages taken in order, and the three places it must stop.
    """

    def test_takes_free_stages_in_order_until_every_stage_is_done(self) -> None:
        states = iter(
            [
                [
                    Step("catalog history", done=False, detail="nothing stored", action=BACKFILL),
                    Step("validated", done=False, detail="unfiled", action=VALIDATE),
                ],
                [
                    Step("catalog history", done=True, detail="2430 bars"),
                    Step("validated", done=False, detail="unfiled", action=VALIDATE),
                ],
                [
                    Step("catalog history", done=True, detail="2430 bars"),
                    Step("validated", done=True, detail="1/1 current"),
                ],
            ],
        )
        ran: list[tuple[str, ...]] = []

        code = apply_steps("IJH.ARCX", lambda: next(states), lambda a: ran.append(a) or 0)

        assert code == 0
        assert ran == [BACKFILL, VALIDATE]

    def test_stops_at_a_deliberate_act_without_running_anything_after_it(self) -> None:
        """
        The metered pull and the repin are the operator's; a stage after them is not
        reached.
        """
        steps = [
            Step("spread calibrated", done=False, detail="absent", command="databento --pull"),
            Step("validated", done=False, detail="unfiled", action=VALIDATE),
        ]
        ran: list[tuple[str, ...]] = []

        code = apply_steps("IJH.ARCX", lambda: steps, lambda a: ran.append(a) or 0)

        assert code == 1
        assert ran == []

    def test_stops_when_a_stage_fails(self) -> None:
        steps = [Step("catalog history", done=False, detail="nothing stored", action=BACKFILL)]
        ran: list[tuple[str, ...]] = []

        code = apply_steps("IJH.ARCX", lambda: steps, lambda a: ran.append(a) or 3)

        assert code == 3
        assert ran == [BACKFILL]

    def test_a_stage_that_ran_and_is_still_not_done_is_not_repeated(self) -> None:
        """
        A patch whose store fills nothing would otherwise run until the action cap.
        """
        steps = [
            Step("holes filled", done=False, detail="13 missing", action=("copilot.data.patch",)),
        ]
        ran: list[tuple[str, ...]] = []

        code = apply_steps("GLDM.ARCX", lambda: steps, lambda a: ran.append(a) or 0)

        assert code == 1
        assert len(ran) == 1
        assert len(ran) < MAX_ACTIONS

    def test_without_a_store_no_hole_is_counted_fillable(self, tmp_path: Path) -> None:
        assert fillable_holes("unused", "GLDM", "ARCX", None) is None
        assert fillable_holes("unused", "GLDM", "ARCX", tmp_path / "absent") is None


TEMPLATE = """\
strategy = "gap_reversal"
lifecycle = "PAPER"
note = "the template"

[instrument]
symbol = "SPY"
venue = "ARCX"

[parameters]
long = true
entry_timing = "next_close"
atr_period = 14
stop_atr = "1.5"

[validation]
train_bars = 252
test_bars = 126
holdout_start = "2022-01-01"
"""


class TestRegister:
    """
    A registry file written from a template, which the registry itself must load.
    """

    @pytest.fixture
    def registry(self, tmp_path: Path) -> Path:
        (tmp_path / "spy-gap-fade-long-next-close.toml").write_text(TEMPLATE)
        return tmp_path

    def test_writes_a_research_activation_like_the_template(self, registry: Path) -> None:
        path = register(
            "IJH.ARCX",
            like="spy-gap-fade-long-next-close",
            holdout_start="2023-04-01",
            minimum_effect_r="0",
            directory=registry,
        )

        written = load_activation(path)
        template = load_activation(registry / "spy-gap-fade-long-next-close.toml")
        assert path.name == "ijh-gap-fade-long-next-close.toml"
        assert (written.symbol, written.venue) == ("IJH", "ARCX")
        assert written.lifecycle is Lifecycle.RESEARCH
        assert written.strategy == template.strategy
        assert written.parameters == template.parameters
        assert written.validation.train_bars == template.validation.train_bars
        assert written.validation.holdout_start == "2023-04-01"
        assert written.validation.minimum_effect_r == "0"

    def test_a_registration_is_written_once(self, registry: Path) -> None:
        kwargs = {
            "like": "spy-gap-fade-long-next-close",
            "holdout_start": "2023-04-01",
            "minimum_effect_r": "0",
            "directory": registry,
        }
        register("IJH.ARCX", **kwargs)  # type: ignore[arg-type]
        with pytest.raises(FileExistsError, match="written once"):
            register("IJH.ARCX", **kwargs)  # type: ignore[arg-type]

    def test_an_empty_effect_size_is_not_a_declaration(self, registry: Path) -> None:
        with pytest.raises(ValueError, match="zero is, empty is not"):
            register(
                "IJH.ARCX",
                like="spy-gap-fade-long-next-close",
                holdout_start="2023-04-01",
                minimum_effect_r="",
                directory=registry,
            )
        assert not (registry / "ijh-gap-fade-long-next-close.toml").exists()

    def test_a_template_not_named_for_its_symbol_is_refused(self) -> None:
        with pytest.raises(ValueError, match="nothing to swap"):
            registration_name("gap-template", "SPY", "IJH")


class TestCommandLine:
    """
    The flag combinations that would otherwise skip a stage or an effect-size
    declaration.
    """

    def test_apply_without_the_survey_is_refused(self) -> None:
        assert main(["--symbols", "IJH.ARCX", "--apply"]) == 2

    def test_like_without_an_effect_size_is_refused(self) -> None:
        assert main(["--symbols", "IJH.ARCX", "--like", "spy-gap-fade-long-next-close"]) == 2

    def test_register_needs_every_input(self) -> None:
        assert main(["--register", "IJH.ARCX", "--like", "spy-gap-fade-long-next-close"]) == 2
