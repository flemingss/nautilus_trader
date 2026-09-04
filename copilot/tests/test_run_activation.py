"""
Tests for running a registered activation on the paper broker.

Everything that needs a broker is exercised by running it against one; what is tested here
is the seam either side of that - how a catalog bar becomes a bar the broker's instrument
can carry, and how an activation becomes a strategy configured for the right instrument.

Both have a failure mode that is silent rather than loud. A bar built at the wrong
precision is still a bar and still trades. A strategy configured with the research
instrument id resolves nothing at the broker and looks like a connection problem.

"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from decimal import Decimal

from copilot.data.catalog import BAR_SPEC
from copilot.data.catalog import equity_for
from copilot.live.run_activation import OrderRecord
from copilot.live.run_activation import SessionRecord
from copilot.live.run_activation import broker_bar_type
from copilot.live.run_activation import build_strategy
from copilot.live.run_activation import to_broker_bars
from copilot.live.symbology import broker_instrument_id
from copilot.strategies.activations import find_activation
from copilot.validation.types import DailyBar


ACTIVATION = find_activation("aapl-gap-fade-long-next-close")
BROKER_ID = broker_instrument_id(ACTIVATION.symbol, ACTIVATION.venue)
CATALOG_EQUITY = equity_for(ACTIVATION.symbol, ACTIVATION.venue)


def daily(day: int, close: str) -> DailyBar:
    """
    Build one catalog bar at the catalog's four-decimal precision.
    """
    value = Decimal(close)
    return DailyBar(
        symbol=ACTIVATION.symbol,
        closed_at=datetime(2026, 9, day, tzinfo=UTC),
        open=value,
        high=value + Decimal(2),
        low=value - Decimal(2),
        close=value,
        volume=1_000_000,
    )


def record(**kwargs: object) -> SessionRecord:
    """
    Build a session record with the fields the assertions care about.
    """
    base = {
        "activation": ACTIVATION.name,
        "broker_instrument": str(BROKER_ID),
        "research_instrument": str(CATALOG_EQUITY.id),
        "decision_bar": "2026-09-03",
        "warmup_bars": 16,
        "warmup_from": "2026-08-12",
        "warmup_to": "2026-09-02",
        "parameters": {},
    }
    return SessionRecord(**{**base, **kwargs})


def test_the_bar_type_is_the_catalogs_spec_on_the_brokers_instrument() -> None:
    # Same series, different namespace. A different spec would mean the live path and the
    # replay were not scoring the same thing at all.
    bar_type = broker_bar_type(BROKER_ID)
    assert str(bar_type) == f"{BROKER_ID}-{BAR_SPEC}"
    assert str(bar_type.instrument_id) == "AAPL=STK.SMART"


def test_the_bar_type_is_not_the_research_one() -> None:
    # AAPL.XNAS fails resolution at the broker outright, and the failure reads as a
    # connection problem rather than as a naming one.
    assert str(broker_bar_type(BROKER_ID)) != str(
        broker_bar_type(CATALOG_EQUITY.id),
    )


def test_conversion_rounds_to_the_brokers_precision() -> None:
    # The catalog stores four decimals because the vendor delivers four; the broker
    # quotes its own increment, and a bar handed to a live strategy is expressed in the
    # market it trades.
    bars, largest = to_broker_bars(
        (daily(3, "328.2149"),),
        CATALOG_EQUITY,
        broker_bar_type(BROKER_ID),
    )
    assert len(bars) == 1
    assert Decimal(str(bars[0].close)) == Decimal("328.2149")
    assert largest == Decimal(0)


def test_conversion_reports_the_largest_rounding_rather_than_refusing() -> None:
    # catalog.to_nautilus_bars refuses here, and is right to: a stored history that
    # rounds is read as ground truth by every later run. A live bar that refuses to
    # round cannot be handed to a strategy at all, so this reports instead.
    from nautilus_trader.model import Equity
    from nautilus_trader.model import InstrumentId
    from nautilus_trader.model import Price
    from nautilus_trader.model import Quantity
    from nautilus_trader.model import Symbol
    from nautilus_trader.model import Venue

    coarse = Equity(
        instrument_id=InstrumentId(Symbol("AAPL"), Venue("SMART")),
        raw_symbol=Symbol("AAPL"),
        currency=CATALOG_EQUITY.quote_currency,
        price_precision=2,
        price_increment=Price.from_str("0.01"),
        lot_size=Quantity.from_int(1),
        ts_event=0,
        ts_init=0,
    )
    _, largest = to_broker_bars((daily(3, "328.2149"),), coarse, broker_bar_type(BROKER_ID))
    assert largest == Decimal("0.0049")


def test_conversion_orders_bars_by_close() -> None:
    unordered = (daily(4, "330"), daily(2, "328"), daily(3, "329"))
    bars, _ = to_broker_bars(unordered, CATALOG_EQUITY, broker_bar_type(BROKER_ID))
    assert [b.ts_event for b in bars] == sorted(b.ts_event for b in bars)


def test_the_strategy_is_configured_for_the_broker_instrument() -> None:
    # Orders have to be routable. Configuring the research id produces a strategy that
    # decides correctly and cannot place anything.
    strategy = build_strategy(ACTIVATION, BROKER_ID)
    assert str(strategy.config.instrument_id) == "AAPL=STK.SMART"
    assert str(strategy.config.bar_type) == f"{BROKER_ID}-{BAR_SPEC}"


def test_the_strategy_carries_the_activations_own_parameters() -> None:
    # The activation's seeded identity, not a holdout's frozen set: reading frozen
    # parameters into a live run is a promotion, and a promotion is a reviewed diff.
    strategy = build_strategy(ACTIVATION, BROKER_ID)
    assert strategy.config.entry_timing == ACTIVATION.parameters["entry_timing"]
    assert str(strategy.config.risk_budget) == str(ACTIVATION.parameters["risk_budget"])


def test_a_session_that_placed_nothing_did_not_decide_to_trade() -> None:
    assert not record(skips={"setup_not_triggered": 1}).decided_to_trade


def test_a_denied_order_still_counts_as_a_decision() -> None:
    # The risk engine halt means an order is denied rather than absent. Reading a denial
    # as "no signal" would make orders-disabled sessions useless as evidence.
    denied = OrderRecord(
        client_order_id="O-1",
        side="BUY",
        quantity="3",
        order_type="MARKET",
        status="DENIED",
    )
    assert record(orders=[denied]).decided_to_trade
