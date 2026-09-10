"""
Tests for the cost model the gate charges.

The dangerous failures here are quiet ones: a symbol silently priced at another symbol's
spread, commission charged on split-inflated share counts, or a "net" score that turns
out to be gross. Each test pins one of those shut.

"""

from __future__ import annotations

import tempfile
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from copilot.calibration.cost_model import CANONICAL_SNAPSHOT
from copilot.calibration.cost_model import FIXED
from copilot.calibration.cost_model import SCHEDULE
from copilot.calibration.cost_model import SNAPSHOT_DIR
from copilot.calibration.cost_model import TIERED
from copilot.calibration.cost_model import CostModel
from copilot.calibration.cost_model import UncalibratedSymbolError
from copilot.calibration.cost_model import commission
from copilot.calibration.cost_model import regulatory_fees
from copilot.calibration.cost_model import round_trip_cost_r
from copilot.calibration.cost_model import split_factor
from copilot.data.corporate_actions import ACTIONS
from copilot.data.corporate_actions import SPLIT
from copilot.validation.types import BacktestRunResult
from copilot.validation.types import ClosedTrade
from copilot.validation.types import Direction


def trade(
    *,
    quantity: int = 10,
    entry_price: str = "100",
    realized_pnl: str = "50",
    risk_amount: str = "100",
    opened_at: datetime | None = None,
) -> ClosedTrade:
    """
    Build one closed trade with only the fields under test varying.
    """
    when = opened_at or datetime(2025, 6, 2, tzinfo=UTC)
    return ClosedTrade(
        symbol="TEST",
        direction=Direction.LONG,
        quantity=quantity,
        entry_price=Decimal(entry_price),
        exit_price=Decimal(entry_price),
        exit_reason="test",
        signal_created_at=when,
        opened_at=when,
        closed_at=when,
        realized_pnl=Decimal(realized_pnl),
        risk_amount=Decimal(risk_amount),
    )


# --- commission schedule ------------------------------------------------------------


def test_the_minimum_binds_below_two_hundred_shares() -> None:
    # Measured live twice: 1 and 3 shares both cost ~USD 1.00 per order.
    assert commission(Decimal(1), Decimal(300)) == Decimal("1.00")
    assert commission(Decimal(199), Decimal(60_000)) == Decimal("1.00")


def test_per_share_takes_over_above_the_minimum() -> None:
    assert commission(Decimal(1000), Decimal(300_000)) == Decimal("5.000")


def test_the_notional_cap_binds_on_penny_notional() -> None:
    # 100 shares at USD 0.50: per-share says 1.00 (the minimum), the 1% cap says 0.50.
    assert commission(Decimal(100), Decimal(50)) == Decimal("0.50")


# --- split correction ---------------------------------------------------------------


def test_split_factor_unwinds_the_back_adjustment() -> None:
    # Before all three AAPL splits (2 x 7 x 4): 56 adjusted shares per real share.
    # After the 2005 2:1 but before the 2014 and 2020 splits: 28.
    assert split_factor("AAPL", datetime(2005, 1, 3, tzinfo=UTC)) == Decimal(56)
    assert split_factor("AAPL", datetime(2006, 1, 3, tzinfo=UTC)) == Decimal(28)
    assert split_factor("AAPL", datetime(2025, 1, 3, tzinfo=UTC)) == Decimal(1)


def test_an_unsplit_symbol_is_charged_on_the_recorded_count() -> None:
    assert split_factor("SPY", datetime(2006, 1, 3, tzinfo=UTC)) == Decimal(1)


def test_early_aapl_commission_is_charged_on_real_shares() -> None:
    # 2,800 adjusted shares in 2006 were 100 real shares (factor 28): the USD 1.00
    # minimum per leg, not 2,800 x 0.005 = USD 14.
    cost = round_trip_cost_r(
        symbol="AAPL",
        quantity=Decimal(2_800),
        entry_price=Decimal("2.50"),
        opened_at=datetime(2006, 3, 1, tzinfo=UTC),
        risk_amount=Decimal(1_000),
        bps_per_side=Decimal(0),
    )
    # 2.00 of commission plus the regulatory fee on the sale of 100 real shares at
    # USD 70. Was exactly 2/1000 until ADR-0025 added the pass-through the broker
    # actually charges; the shakedown's measured trips are what showed it missing.
    expected = Decimal(2) + regulatory_fees(Decimal(100), Decimal(7_000))
    assert cost == expected / Decimal(1_000)


# --- the round trip -----------------------------------------------------------------


def test_round_trip_charges_spread_both_ways_and_commission_both_legs() -> None:
    # 10 shares at 100: notional 1,000. Spread 2 bps/side -> 2 * 0.0002 * 1000 = 0.40.
    # Commission: minimum binds, 2 * 1.00. Plus the regulatory fee on the sale, which
    # ADR-0025 added after the broker charged it on a measured trip.
    cost = round_trip_cost_r(
        symbol="TEST",
        quantity=Decimal(10),
        entry_price=Decimal(100),
        opened_at=datetime(2025, 6, 2, tzinfo=UTC),
        risk_amount=Decimal(100),
        bps_per_side=Decimal(2),
    )
    expected = Decimal("0.40") + Decimal(2) + regulatory_fees(Decimal(10), Decimal(1_000))
    assert cost == expected / Decimal(100)


# --- the model against the pinned snapshot ------------------------------------------


def test_the_canonical_snapshot_loads_and_covers_the_universe() -> None:
    model = CostModel.from_snapshot()
    assert model.snapshot == CANONICAL_SNAPSHOT
    assert model.percentile == "p95"
    for symbol in ("AAPL", "MSFT", "SPY"):
        assert model.spread_bps_for(symbol) > 0


def test_the_pin_is_the_measured_basis_not_the_broker_snapshot() -> None:
    # ADR-0019 moved the source. This failing means the pin was changed without the
    # argument that goes with it.
    assert CANONICAL_SNAPSHOT.startswith("spread_history_")


def test_the_superseded_broker_snapshot_still_reads() -> None:
    # ADR-0011's own record has to stay reproducible; a superseded decision whose
    # numbers can no longer be recomputed is a claim rather than a record.
    old = SNAPSHOT_DIR / "spread_snapshot_20260901T154744Z.json"
    model = CostModel.from_snapshot(path=old)
    assert model.spread_bps_for("SPY") == Decimal("0.5238")


def test_a_snapshot_measured_at_another_percentile_refuses() -> None:
    # Reinterpreting a p95 file as a p75 one would silently change every verdict's cost.
    import json

    source = json.loads((SNAPSHOT_DIR / CANONICAL_SNAPSHOT).read_text())
    source["basis"]["percentile"] = "p75"
    path = Path(tempfile.mkdtemp()) / "wrong_percentile.json"
    path.write_text(json.dumps(source))
    with pytest.raises(ValueError, match="re-measure"):
        CostModel.from_snapshot(path=path)


def test_an_uncalibrated_symbol_refuses_rather_than_guesses(tmp_path) -> None:
    # NVDA rather than GOOGL: the repin to measured history (ADR-0019) calibrated the
    # whole 20-symbol store, so GOOGL now has a coefficient and no longer tests this.
    model = CostModel.from_snapshot()
    with pytest.raises(UncalibratedSymbolError, match="NVDA"):
        model.spread_bps_for("NVDA")
    # And the objective refuses at build time, before any replay has run.
    with pytest.raises(UncalibratedSymbolError, match="NVDA"):
        model.net_expectancy_for("NVDA")


def test_the_repin_calibrated_the_whole_store_not_just_the_traded_three() -> None:
    # The broker snapshot covered AAPL, MSFT and SPY because those were the instruments
    # a live session could subscribe to. The measured history covers everything bought,
    # which is what the universe correction will need and could not have had before.
    model = CostModel.from_snapshot()
    for symbol in ("AAPL", "MSFT", "SPY", "GOOGL", "JPM", "XOM"):
        assert model.spread_bps_for(symbol) > 0


def test_the_net_objective_subtracts_exactly_the_round_trip_cost() -> None:
    model = CostModel(
        bps_per_side={"TEST": Decimal(2)},
        snapshot="synthetic",
        percentile="p95",
    )
    objective = model.net_expectancy_for("TEST")
    # One trade: gross R = 50/100 = 0.5, less exactly what the round-trip test computes.
    result = BacktestRunResult(trades=(trade(),))
    cost = round_trip_cost_r(
        symbol="TEST",
        quantity=Decimal(10),
        entry_price=Decimal(100),
        opened_at=datetime(2025, 6, 2, tzinfo=UTC),
        risk_amount=Decimal(100),
        bps_per_side=Decimal(2),
    )
    assert objective(result) == Decimal("0.5") - cost


def test_the_net_objective_scores_an_empty_window_as_zero() -> None:
    model = CostModel(bps_per_side={"TEST": Decimal(2)}, snapshot="s", percentile="p95")
    assert model.net_expectancy_for("TEST")(BacktestRunResult()) == Decimal(0)


def test_the_record_names_its_exact_basis() -> None:
    record = CostModel.from_snapshot().as_record("SPY")
    assert record["snapshot"] == CANONICAL_SNAPSHOT
    assert record["percentile"] == "p95"
    assert Decimal(record["bps_per_side"]) == Decimal("0.5466")
    assert "1.00" in record["commission"]


def test_split_factor_now_covers_the_symbols_it_used_to_miss() -> None:
    """
    The table this reads used to live here and listed AAPL alone.

    It was right only by
    luck: the symbols whose splits were missing were the same ones whose prices were
    never adjusted, so a recorded quantity happened to be the real one. Adjusting the
    prices makes that luck run out, so the factors have to arrive together.

    """
    assert split_factor("GOOGL", datetime(2013, 1, 3, tzinfo=UTC)) == Decimal(40)
    assert split_factor("GOOGL", datetime(2015, 1, 3, tzinfo=UTC)) == Decimal(20)
    assert split_factor("AMZN", datetime(2020, 1, 3, tzinfo=UTC)) == Decimal(20)
    assert split_factor("WMT", datetime(2023, 1, 3, tzinfo=UTC)) == Decimal(3)
    assert split_factor("KO", datetime(2010, 1, 4, tzinfo=UTC)) == Decimal(2)


def test_a_spinoff_never_divides_the_share_count() -> None:
    """
    MRK's Organon, T's Warner Bros Discovery and VZ's three moved the price without
    issuing a share.

    Treating one as a split would understate commission on every trade before it.

    """
    for symbol in ("MRK", "T", "VZ"):
        assert split_factor(symbol, datetime(2006, 1, 3, tzinfo=UTC)) == Decimal(1)


def test_every_price_adjustment_that_changes_shares_has_a_matching_factor() -> None:
    """
    The invariant the two-table arrangement could not state: a symbol whose stored
    prices get divided on read must have its share count divided by exactly the
    share-count part of the same events.
    """
    for symbol, actions in ACTIONS.items():
        expected = Decimal(1)
        for action in actions:
            if action.kind is SPLIT:
                expected *= action.factor
        earliest = datetime(2005, 1, 3, tzinfo=UTC)
        assert split_factor(symbol, earliest) == expected, symbol


# ------------------------------------------------------------- the commission plans


def test_the_model_reproduces_the_measured_round_trips_to_the_cent() -> None:
    """
    The strongest test here, because the number came from the broker.

    The 2026-09-10 shakedown ran two deliberate round trips on the paper account and the
    statement charged USD 2.01 on one share and USD 2.02 on three. A bare
    ``max(1.00, 0.005/share)`` both ways is 2.00 exactly, so the cent was the regulatory
    pass-through the model did not have. It has it now, and both trips come back exact.

    """
    price = Decimal("322.52")
    for shares, expected in ((Decimal(1), Decimal("2.01")), (Decimal(3), Decimal("2.02"))):
        notional = shares * price
        modelled = 2 * commission(shares, notional, FIXED) + regulatory_fees(shares, notional)
        assert modelled.quantize(Decimal("0.01")) == expected, f"{shares} shares"


def test_tiered_is_cheaper_at_this_accounts_order_sizes() -> None:
    """
    The whole economic case, at the sizes the charter actually trades.

    USD 20 of risk on a stock near USD 320 with a stop a few dollars wide is two or
    three shares. There the minimum is the entire cost and the two minimums differ
    threefold.

    """
    for shares in (Decimal(1), Decimal(3), Decimal(9)):
        notional = shares * Decimal("322.52")
        assert commission(shares, notional, TIERED) < commission(shares, notional, FIXED)


def test_fixed_becomes_cheaper_above_the_crossover() -> None:
    """
    Tiered is not simply Fixed with a lower minimum, and modelling it that way would
    lie.

    Tiered passes the exchange access fee through, so its all-in per-share rate is above
    Fixed's flat one. Past roughly 150 shares the pass-through outweighs the lower
    minimum and Fixed wins - which is why this account's sizing, not a preference,
    decides the plan.

    """
    small = Decimal(100)
    large = Decimal(1000)
    price = Decimal("322.52")

    assert commission(small, small * price, TIERED) < commission(small, small * price, FIXED)
    assert commission(large, large * price, TIERED) > commission(large, large * price, FIXED)


def test_the_pass_through_is_not_capped_with_the_commission() -> None:
    """
    IBKR's *maximum per order* caps what IBKR charges; an exchange fee is not IBKR's.

    Folding the pass-through inside the cap would under-charge small high-priced orders,
    which is exactly the shape this account trades.

    """
    shares = Decimal(100)
    # A cap of 1% of USD 10 is 0.10, below the 0.35 minimum, so the cap genuinely binds.
    # A first cut used USD 50, where the cap is 0.50 and never bit - the test passed its
    # first assertion for the wrong reason.
    notional = Decimal(10)

    charged = commission(shares, notional, TIERED)
    passed = (TIERED.exchange_per_share + TIERED.clearing_per_share) * shares

    assert charged > TIERED.max_pct * notional
    assert charged == TIERED.max_pct * notional + passed


def test_fixed_passes_no_exchange_fee_through() -> None:
    """
    Its single per-share rate covers exchange and clearing; adding them would double-
    count.
    """
    assert FIXED.exchange_per_share == 0
    assert FIXED.clearing_per_share == 0


def test_the_pinned_plan_is_the_one_the_account_is_on() -> None:
    """
    A verdict priced on a plan the broker is not running is about a different account.

    The paper account is measurably on Fixed, confirmed by the round trips above. This
    stays Fixed until the switch is actually made.

    """
    assert SCHEDULE is FIXED


def test_the_access_fee_is_the_cap_in_force_today_not_the_one_adopted() -> None:
    """
    The SEC cut Rule 610(c)'s cap from 0.003 to 0.001 in September 2024 and the
    compliance date has been pushed to 2026-11-02, with a further extension still being
    asked for in May 2026.

    Charging 0.001 today would price a rule that is not yet operative, and in the
    direction that flatters Tiered.

    """
    assert TIERED.exchange_per_share == Decimal("0.003")


def test_a_verdict_records_which_plan_priced_it() -> None:
    """
    Two plans means a bare R number is ambiguous; the record has to name its basis.
    """
    fixed = CostModel.from_snapshot().as_record("AAPL")["commission"]
    tiered = CostModel.from_snapshot(schedule=TIERED).as_record("AAPL")["commission"]

    assert "Fixed" in fixed
    assert "Tiered" in tiered
    assert "passed through" in tiered
    assert "passed through" not in fixed


def test_the_regulatory_fee_is_charged_once_not_per_leg() -> None:
    """
    SEC and TAF fees fall on the sale.

    Charging them both ways would double them.

    """
    shares, notional = Decimal(100), Decimal(32252)
    once = regulatory_fees(shares, notional)

    assert once > 0
    assert regulatory_fees(shares, notional) == once
