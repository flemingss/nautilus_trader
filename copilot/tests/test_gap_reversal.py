"""
Tests for the gap-fade strategy.

The rule is small; what is easy to get wrong is its *direction discipline*. The original
splits the legs precisely because gap-downs and gap-ups revert at materially different
rates, so a sign error that let the long leg trade gap-ups would pool them and let the
stronger leg carry the weaker one through the gate - while every test about entries,
stops and sizing still passed. Several tests below exist only to pin that.

"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

import pytest

from copilot.data.catalog import bar_type_for
from copilot.data.catalog import equity_for
from copilot.risk.exposure import ExposureLedger
from copilot.strategies.gap_reversal import DEFERRED
from copilot.strategies.gap_reversal import ENTRY_SUBMITTED
from copilot.strategies.gap_reversal import ENTRY_TIMINGS
from copilot.strategies.gap_reversal import MAX_SEARCHABLE_MIN_GAP_ATR
from copilot.strategies.gap_reversal import SEARCH_SPACE
from copilot.strategies.gap_reversal import WARMUP_BARS
from copilot.strategies.gap_reversal import GapReversalConfig
from copilot.strategies.gap_reversal import GapReversalStrategy
from copilot.strategies.gap_reversal import strategy_factory
from copilot.validation.nautilus_replay import RiskAmountRegistry
from copilot.validation.nautilus_replay import run_nautilus_replay
from copilot.validation.nautilus_replay import run_to_decision
from copilot.validation.types import DailyBar


INSTRUMENT = equity_for("AAPL", "XNAS")
BAR_TYPE = bar_type_for(INSTRUMENT.id)
START = datetime(2024, 1, 2, tzinfo=UTC)
WARMUP_BARS_FOR_TEST = WARMUP_BARS


def bars_from(specs: list[tuple[str, str, str, str]]) -> list[DailyBar]:
    """
    Build a bar series from (open, high, low, close) strings, one per day.
    """
    return [
        DailyBar(
            symbol="AAPL",
            closed_at=START + timedelta(days=i),
            open=Decimal(o),
            high=Decimal(h),
            low=Decimal(low),
            close=Decimal(c),
            volume=1_000_000,
        )
        for i, (o, h, low, c) in enumerate(specs)
    ]


def flat_series(n: int, price: str = "100", spread: str = "1") -> list[tuple[str, str, str, str]]:
    """
    A quiet run that charges the ATR without ever gapping.

    Each bar opens exactly where the last one closed, so no bar can trigger the rule and
    anything that fires during the warm-up is a bug rather than a signal.

    """
    p, s = Decimal(price), Decimal(spread)
    return [(str(p), str(p + s), str(p - s), str(p)) for _ in range(n)]


# Quiet bars charge the ATR to roughly 2 on a 1-wide range, which is what the resolving
# tails below are sized against.
RESOLVE_LONG = [("98", "102", "97.5", "101"), *flat_series(3, price="101")]
"""
Reaches a long's 1 ATR target without coming near its 1.5 ATR stop.

Every entering test needs one. A position that never touches either level stays open,
and an open position is not in `positions_closed()` - so the run reports zero trades and
looks exactly like a rule that never fired.

"""

RESOLVE_SHORT = [("102", "102.5", "98", "99"), *flat_series(3, price="99")]
"""
The mirror, for the short leg.
"""


def run(specs, **parameters: object):
    """
    Replay one parameter set over a hand-built series.
    """
    return run_nautilus_replay(
        bars_from(specs),
        parameters,
        instrument=INSTRUMENT,
        bar_type=BAR_TYPE,
        strategy_factory=strategy_factory,
    )


def a_strategy(**overrides: object) -> GapReversalStrategy:
    settings = {"instrument_id": INSTRUMENT.id, "bar_type": BAR_TYPE, **overrides}
    strategy = GapReversalStrategy(GapReversalConfig(**settings))
    strategy.configure(RiskAmountRegistry())
    return strategy


# ------------------------------------------------------------------- direction


def test_the_long_leg_fades_a_gap_down():
    """The premise: a session opening far below the previous close is faded, long."""
    # 20 quiet bars at 100 with a 1-wide range give ATR ~= 2, then open at 96: a 2 ATR
    # gap down, well past the 0.25 default.
    specs = [*flat_series(20), ("96", "99", "95", "98"), *RESOLVE_LONG]
    result = run(specs, min_gap_atr="0.25")

    assert len(result.trades) == 1
    assert result.trades[0].direction.value == "LONG"


def test_the_long_leg_ignores_a_gap_up():
    """
    The sign check, and the reason the legs are separate strategies.

    Taking the magnitude of the gap here would have each leg fire on both kinds, which
    is exactly the pooling the split exists to prevent: gap-downs revert materially more
    often than gap-ups, so a pooled rule lets the stronger leg carry the weaker one.

    """
    specs = [*flat_series(20), ("104", "105", "101", "102"), *flat_series(6, price="102")]
    result = run(specs, min_gap_atr="0.25")

    assert result.trades == ()


def test_the_short_leg_fades_a_gap_up():
    specs = [*flat_series(20), ("104", "105", "101", "102"), *RESOLVE_SHORT]
    result = run(specs, min_gap_atr="0.25", long=False)

    assert len(result.trades) == 1
    assert result.trades[0].direction.value == "SHORT"


def test_the_short_leg_ignores_a_gap_down():
    specs = [*flat_series(20), ("96", "99", "95", "98"), *RESOLVE_LONG]
    assert run(specs, min_gap_atr="0.25", long=False).trades == ()


# -------------------------------------------------------------------- trigger


def test_a_gap_under_the_threshold_does_not_fire():
    """
    The trigger is also the quality measure; below threshold there is no signal.
    """
    # ATR ~= 2, so a 1-point gap is 0.5 ATR - under a 1.0 threshold.
    specs = [*flat_series(20), ("99", "100", "98", "99"), *flat_series(6, price="99")]
    assert run(specs, min_gap_atr="1.0").trades == ()


def test_a_larger_threshold_takes_strictly_fewer_trades():
    """Monotonicity: raising the bar cannot admit a signal a lower bar rejected."""
    specs = [*flat_series(20)]
    for gap in ("97", "98", "99"):
        specs += [(gap, "100", str(Decimal(gap) - 1), "100"), *flat_series(3)]
    specs += flat_series(6)

    loose = len(run(specs, min_gap_atr="0.15").trades)
    tight = len(run(specs, min_gap_atr="1.2").trades)
    assert tight <= loose


# ---------------------------------------------------------------- entry timing


# One series serving both bounds of the ADR-0013 bracket: the gap fires on the bar
# closing at 98, the following session closes at 99, and the tail then reaches a long's
# target from either entry without approaching either stop.
BRACKET_SPECS = [
    *flat_series(20),
    ("96", "99", "95", "98"),  # signal bar: 2 ATR gap down, closes 98
    ("97", "99.5", "96.5", "99"),  # the next session, closing 99
    ("99", "101.5", "98.5", "101"),
    *flat_series(3, price="101"),
]


def test_the_bracket_bounds_fill_one_session_apart():
    """
    ADR-0013's two bounds, measured on the same bars.

    ``signal_close`` fills at the close that produced the signal; ``next_close`` freezes
    the decision and fills at the following session's close. Same premise, same trigger
    bar, entries one session and one price apart - which is the entire point of running
    both.

    """
    optimistic = run(BRACKET_SPECS, min_gap_atr="0.25")
    pessimistic = run(BRACKET_SPECS, min_gap_atr="0.25", entry_timing="next_close")

    assert len(optimistic.trades) == 1
    assert len(pessimistic.trades) == 1
    assert optimistic.trades[0].entry_price == Decimal(98)
    assert pessimistic.trades[0].entry_price == Decimal(99)
    assert pessimistic.trades[0].opened_at - optimistic.trades[0].opened_at == timedelta(days=1)


def test_next_close_freezes_the_atr_at_the_signal_bar():
    """
    The deferral carries the signal-time ATR, not the fill bar's.

    The fill bar here has a range wide enough to multiply a live ATR several times over.
    If the levels were rebuilt from it, the recorded stop distance (risk over quantity)
    would be several times the frozen one - so a small distance is only reachable with
    the ATR frozen at the signal.

    """
    specs = [
        *flat_series(20),
        ("96", "99", "95", "98"),  # signal bar; ATR ~= 2 here
        ("97", "120", "90", "99"),  # its session: a 30-wide bar the live ATR would eat
        ("99", "101.5", "98.5", "101"),
        *flat_series(3, price="101"),
    ]
    result = run(specs, min_gap_atr="0.25", entry_timing="next_close")

    assert len(result.trades) == 1
    trade = result.trades[0]
    stop_distance = trade.risk_amount / trade.quantity
    # Frozen: ~2 ATR x 1.5 = ~3. Live at the fill bar would be at least double that.
    assert stop_distance < Decimal(5)


def test_a_signal_on_the_final_bar_never_fills_under_next_close():
    """
    A deferred decision with no next session is a trade left on the table, silently.

    The charter's own rule has the same property - an order for the next session cannot
    fill when no next session exists - so the run must end clean rather than erroring or
    inventing a same-bar fill.

    """
    specs = [*flat_series(20), ("96", "99", "95", "98")]
    result = run(specs, min_gap_atr="0.25", entry_timing="next_close")

    assert result.trades == ()


def test_an_unknown_entry_timing_is_rejected():
    """
    A typo here would silently measure the optimistic bound while the operator believes
    the charter-compliant one is running - the config refuses instead.
    """
    with pytest.raises(ValueError, match="entry_timing"):
        a_strategy(entry_timing="next_open")
    assert ENTRY_TIMINGS == ("signal_close", "next_close")


def test_require_unfilled_rejects_a_gap_the_session_closed():
    """
    A gap that filled intraday has already paid out the move being traded.

    Entering afterwards is buying the reversion after it happened, which is the whole
    thing the knob guards against.

    """
    # Opens 4 below (a real gap down) but closes back above the previous close.
    specs = [
        *flat_series(20),
        ("96", "101", "95", "100.5"),
        ("100.5", "104", "100", "103"),
        *flat_series(3, price="103"),
    ]

    assert len(run(specs, min_gap_atr="0.25").trades) == 1
    assert run(specs, min_gap_atr="0.25", require_unfilled=True).trades == ()


def test_a_surviving_gap_passes_require_unfilled():
    specs = [*flat_series(20), ("96", "99", "95", "98"), *RESOLVE_LONG]
    assert len(run(specs, min_gap_atr="0.25", require_unfilled=True).trades) == 1


# ------------------------------------------------------------ risk and sizing


def test_every_trade_reports_the_risk_it_took():
    """
    Without this the gate scores every trade at r_multiple == 0 and reports no edge.

    `run_nautilus_replay` raises rather than returning that silently, so a regression
    here fails loudly - but the assertion states the contract explicitly anyway.

    """
    specs = [*flat_series(20), ("96", "99", "95", "98"), *RESOLVE_LONG]
    result = run(specs, min_gap_atr="0.25")

    assert result.diagnostics["risk_records"] >= 1
    for trade in result.trades:
        assert trade.risk_amount > 0
        assert trade.r_multiple == trade.realized_pnl / trade.risk_amount


def test_risk_is_the_floored_quantity_times_the_stop_distance():
    """
    Not the budget.

    Quantity floors to whole shares, so realised risk sits at or just under the budget;
    using the budget as the R denominator would overstate every trade by that rounding.

    """
    specs = [*flat_series(20), ("96", "99", "95", "98"), *RESOLVE_LONG]
    result = run(specs, min_gap_atr="0.25", stop_atr="1.5", risk_budget="1000")

    (trade,) = result.trades
    budget = Decimal(1000)
    per_share = trade.risk_amount / trade.quantity

    # At or under budget, and tight against it: one more share would exceed it. That
    # pair is what "floored, never rounded" means, and it is the direction a risk
    # control must not err in.
    assert trade.risk_amount <= budget
    assert trade.risk_amount + per_share > budget


def test_only_one_position_is_held_at_a_time():
    """
    A run of gap days would otherwise stack correlated entries.

    The original's engine enforces this upstream of the setup; here the rule has to, or
    the risk budget is exceeded by a multiple nobody chose.

    """
    specs = [*flat_series(20)]
    for _ in range(4):
        specs += [("96", "99", "95", "98"), ("94", "97", "93", "96")]
    specs += flat_series(6, price="96")

    result = run(specs, min_gap_atr="0.15")
    opens_and_closes = sorted(
        [(t.opened_at, 1) for t in result.trades] + [(t.closed_at, -1) for t in result.trades],
    )
    concurrent = 0
    for _, delta in opens_and_closes:
        concurrent += delta
        assert concurrent <= 1


# ----------------------------------------------------------------- warm-up


def test_nothing_trades_before_the_atr_is_ready():
    """
    A signal during warm-up is a signal computed from an ATR that does not exist yet.

    `insufficient_history` in the skip counts is also how a fold that produced no trades
    is diagnosed as a warm-up that was set too short, rather than a dead premise.

    """
    # A huge gap on bar 2, long before a 14-period ATR can be initialised.
    specs = [("100", "101", "99", "100"), ("80", "101", "79", "100"), *flat_series(4)]
    result = run(specs, min_gap_atr="0.25")

    assert result.trades == ()


def test_skips_are_counted_by_reason():
    strategy = a_strategy(min_gap_atr="0.25")
    strategy._skip("setup_not_triggered")
    strategy._skip("setup_not_triggered")
    strategy._skip("non_positive_atr")

    assert strategy.skips == {"setup_not_triggered": 2, "non_positive_atr": 1}


# ------------------------------------------------------------------ factory


def test_the_factory_matches_the_gate_contract():
    """
    `make_replay` calls exactly this shape, so a mismatch breaks the gate silently.
    """
    strategy = strategy_factory(
        {"min_gap_atr": Decimal("0.40"), "target_1_atr": Decimal("1.5")},
        instrument_id=INSTRUMENT.id,
        bar_type=BAR_TYPE,
        risk_registry=RiskAmountRegistry(),
    )

    assert isinstance(strategy, GapReversalStrategy)
    assert strategy.config.min_gap_atr == "0.40"
    assert strategy.config.target_1_atr == "1.5"


def test_parameters_survive_as_decimals_not_floats():
    """
    These multiples decide order prices. A float round trip would move them.

    0.15 is the case that shows it: `str(float("0.15"))` is stable, but arithmetic on it
    is not, and the strategy multiplies it by an ATR to place a stop.

    """
    strategy = strategy_factory(
        {"min_gap_atr": Decimal("0.15")},
        instrument_id=INSTRUMENT.id,
        bar_type=BAR_TYPE,
        risk_registry=RiskAmountRegistry(),
    )
    assert Decimal(strategy.config.min_gap_atr) == Decimal("0.15")


@pytest.mark.parametrize("threshold", ["0.15", "0.25", "0.40"])
def test_the_searched_thresholds_all_produce_trades(threshold):
    """
    The property this premise was chosen for.

    trade-copilot's V1-31 found that a filtered RSI trigger fell under the eligibility
    floor at every setting, producing no evaluable folds and so no verdict at all. The
    gap thresholds were picked so every one of them still fires. A change that breaks
    that does not weaken the verdict - it removes it.

    """
    specs = [*flat_series(20)]
    for _ in range(5):
        specs += [("95", "99", "94", "98"), *flat_series(3, price="98"), *flat_series(1)]
    specs += flat_series(6)

    assert run(specs, min_gap_atr=threshold).trades != ()


# --------------------------------------------------------------- search space


def test_the_search_space_ceiling_is_pinned():
    """
    The axis cannot be widened without re-counting events.

    trade-copilot's V1-31 searched quality gates whose every setting produced no evaluable
    folds, so the run returned the *absence* of a verdict rather than a verdict - which
    reads as a bug, not as a weak result. Its values were then chosen by counting events
    first, and 0.40 is the loosest that still clears the eligibility floor.

    Raising this ceiling is allowed. Raising it without counting events on the current
    universe is not, and that is what this test is here to interrupt.

    """
    assert max(SEARCH_SPACE["min_gap_atr"]) == MAX_SEARCHABLE_MIN_GAP_ATR
    assert Decimal("0.40") == MAX_SEARCHABLE_MIN_GAP_ATR


def test_the_search_space_stays_small():
    """
    Size is quantitative, not stylistic.

    The best score obtainable from pure noise grows with the number of trials, so a six-
    point space buys real headroom against the deflation statistic that an 81-point one
    does not.

    """
    points = 1
    for values in SEARCH_SPACE.values():
        points *= len(values)
    assert points <= 12, f"search space grew to {points} points; deflation headroom shrinks with it"


def test_every_searched_threshold_produces_trades_on_real_bars():
    """
    The property this premise was chosen for, checked against the axis as declared.

    `test_the_searched_thresholds_all_produce_trades` above pins it on synthetic bars.
    This one guards the pairing: if a value is ever added to `SEARCH_SPACE` that the
    synthetic fixture happens not to cover, it is still asserted here.

    """
    assert set(SEARCH_SPACE["min_gap_atr"]) == {
        Decimal("0.15"),
        Decimal("0.25"),
        Decimal("0.40"),
    }


# --------------------------------------------------------------------- sizing hook


def test_the_config_budget_seeds_the_sizing():
    """
    Research runs size from the config, unchanged: no verdict moves because a hook
    exists.
    """
    strategy = a_strategy(risk_budget="1000")
    assert strategy._sizing.risk_budget == Decimal(1000)
    assert strategy._sizing.max_notional is None


def test_size_against_replaces_the_research_budget():
    """
    The live path's whole point: USD 1,000 is an R-unit, not an amount to risk.
    """
    strategy = a_strategy(risk_budget="1000")
    strategy.size_against(Decimal("1.00"), Decimal("100.00"))
    assert strategy._sizing.risk_budget == Decimal("1.00")
    assert strategy._sizing.max_notional == Decimal("100.00")


def test_size_against_refuses_a_budget_that_is_not_one():
    strategy = a_strategy()
    with pytest.raises(ValueError, match="risk_budget must be positive"):
        strategy.size_against(Decimal(0), None)
    with pytest.raises(ValueError, match="max_notional must be positive"):
        strategy.size_against(Decimal(1), Decimal(0))


def test_a_config_notional_cap_binds_in_a_replay():
    """
    The cap reaches the order, not only the sizing object.

    The same 2 ATR gap-down that sizes 1000 / (1.5 * ~2) = ~333 shares unbounded sizes to
    floor(300 / 96) = 3 shares under a 300 cap - and the trade's recorded risk is the
    floored quantity times the stop distance, not the budget.

    """
    specs = [*flat_series(20), ("96", "99", "95", "98"), *RESOLVE_LONG]
    unbounded = run(specs, min_gap_atr="0.25", stop_atr="1.5", risk_budget="1000")
    capped = run(specs, min_gap_atr="0.25", stop_atr="1.5", risk_budget="1000", max_notional="300")

    (free,), (bound,) = unbounded.trades, capped.trades
    assert free.quantity > bound.quantity
    assert bound.quantity * bound.entry_price <= Decimal(300)
    assert bound.risk_amount < free.risk_amount


# ------------------------------------------------------------------ session cap


def _with_ledger(ledger: ExposureLedger, **sizing: object):
    """
    Wrap the factory so the replay's strategy sizes against a shared ledger.
    """

    def factory(parameters: object, **kwargs: object):
        strategy = strategy_factory(parameters, **kwargs)
        strategy.size_against(
            sizing.get("risk_budget", Decimal(1000)),
            sizing.get("max_notional"),
            ledger=ledger,
        )
        return strategy

    return factory


def _run_with_ledger(specs: list, ledger: ExposureLedger, **parameters: object):
    return run_nautilus_replay(
        bars_from(specs),
        parameters,
        instrument=INSTRUMENT,
        bar_type=BAR_TYPE,
        strategy_factory=_with_ledger(ledger),
    )


def test_a_refused_reservation_is_a_skip_not_a_trade():
    """
    The session-wide cap reaches the order path, and the skip names it.
    """
    ledger = ExposureLedger(max_total_risk=Decimal("0.01"), max_new_entries=5)
    specs = [*flat_series(20), ("96", "99", "95", "98"), *RESOLVE_LONG]
    result = _run_with_ledger(specs, ledger, min_gap_atr="0.25", stop_atr="1.5")

    assert result.trades == ()
    # The replay result carries trades, not skips; the ledger is the witness that the
    # refusal happened where it was supposed to, before any order was built.
    assert ledger.entries == 0
    assert ledger.refusals
    assert "cap of 0.01" in ledger.refusals[0].reason


def test_a_granted_reservation_is_released_when_the_position_closes():
    """
    The risk goes back to the session once it is no longer open, and the entry stays
    counted - which is what lets the same ledger refuse a fifth entry later in the day.
    """
    ledger = ExposureLedger(max_total_risk=Decimal(10_000), max_new_entries=5)
    specs = [*flat_series(20), ("96", "99", "95", "98"), *RESOLVE_LONG]
    result = _run_with_ledger(specs, ledger, min_gap_atr="0.25", stop_atr="1.5")

    (trade,) = result.trades
    assert ledger.total == Decimal(0)
    assert ledger.entries == 1
    assert ledger.reserved == {}
    assert trade.risk_amount > 0


def test_without_a_ledger_the_strategy_sizes_alone():
    """
    Every research replay: no ledger, no reservation, no change to any verdict.
    """
    specs = [*flat_series(20), ("96", "99", "95", "98"), *RESOLVE_LONG]
    result = run(specs, min_gap_atr="0.25", stop_atr="1.5", risk_budget="1000")
    assert len(result.trades) == 1


class TestDecisionRecord:
    """
    What one bar did, named, so a live session and a replay can be compared.
    """

    def test_a_skip_names_itself(self) -> None:
        strategy = strategy_factory(
            {},
            instrument_id=INSTRUMENT.id,
            bar_type=BAR_TYPE,
            risk_registry=None,
        )
        strategy._skip("setup_not_triggered")
        decided = strategy.decision_record()
        assert decided["outcome"] == "setup_not_triggered"
        assert decided["skips"] == {"setup_not_triggered": 1}
        assert decided["deferred_atr"] is None

    def test_before_any_bar_nothing_is_recorded(self) -> None:
        strategy = strategy_factory(
            {},
            instrument_id=INSTRUMENT.id,
            bar_type=BAR_TYPE,
            risk_registry=None,
        )
        decided = strategy.decision_record()
        assert decided["outcome"] is None
        assert decided["atr_initialized"] is False
        assert decided["atr_value"] is None
        assert decided["previous_close"] is None

    def test_a_next_close_trigger_records_the_deferral_and_the_atr_it_froze(self) -> None:
        # The gap-down on the last bar triggers under next_close: no order, no skip, and
        # until this existed nothing in the record said the rule had fired.
        series = [*flat_series(WARMUP_BARS_FOR_TEST), ("94", "95", "93", "94.5")]
        decided = run_to_decision(
            bars_from(series),
            {"long": True, "entry_timing": "next_close", "min_gap_atr": "0.25"},
            instrument=INSTRUMENT,
            bar_type=BAR_TYPE,
            strategy_factory=strategy_factory,
            warmup=WARMUP_BARS_FOR_TEST,
            inspect=lambda s: s.decision_record(),
        )
        assert decided["outcome"] == DEFERRED
        assert decided["deferred_atr"] is not None
        assert decided["skips"] == {}
        # The strategy's "previous close" after a bar is that bar's close: the level the
        # *next* decision compares against, and what the live record files.
        assert decided["previous_close"] == "94.5000"

    def test_a_quiet_bar_declines_and_says_why(self) -> None:
        series = [*flat_series(WARMUP_BARS_FOR_TEST), ("100", "101", "99", "100")]
        decided = run_to_decision(
            bars_from(series),
            {"long": True, "entry_timing": "next_close"},
            instrument=INSTRUMENT,
            bar_type=BAR_TYPE,
            strategy_factory=strategy_factory,
            warmup=WARMUP_BARS_FOR_TEST,
            inspect=lambda s: s.decision_record(),
        )
        assert decided["outcome"] == "setup_not_triggered"

    def test_a_signal_close_trigger_records_the_submission(self) -> None:
        series = [*flat_series(WARMUP_BARS_FOR_TEST), ("94", "95", "93", "94.5")]
        decided = run_to_decision(
            bars_from(series),
            {"long": True, "entry_timing": "signal_close", "min_gap_atr": "0.25"},
            instrument=INSTRUMENT,
            bar_type=BAR_TYPE,
            strategy_factory=strategy_factory,
            warmup=WARMUP_BARS_FOR_TEST,
            inspect=lambda s: s.decision_record(),
        )
        assert decided["outcome"] == ENTRY_SUBMITTED
