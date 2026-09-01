"""
Tests for the research-to-broker instrument id bridge.

The failure this prevents is not a crash. It is an order placed against the wrong
instrument, or an activation that validated on one id and traded another, so the mapping
is pinned in both directions and the ticker is checked to survive the round trip.

"""

from __future__ import annotations

import pytest

from copilot.live.symbology import ROUTING_BY_VENUE
from copilot.live.symbology import UnmappedVenueError
from copilot.live.symbology import broker_instrument_id
from copilot.live.symbology import research_instrument_id
from copilot.live.symbology import same_instrument
from copilot.live.symbology import symbol_of
from copilot.strategies.activations import load_activations


def test_a_us_equity_maps_to_the_raw_ib_form():
    assert str(broker_instrument_id("AAPL", "XNAS")) == "AAPL=STK.SMART"


def test_the_listing_venue_does_not_survive_into_the_broker_id():
    """
    SMART is a routing destination, not a listing venue.

    SPY lists on ARCX and AAPL on XNAS, and both route the same way, so the broker id
    cannot be used to recover where an instrument is listed.

    """
    assert str(broker_instrument_id("SPY", "ARCX")) == "SPY=STK.SMART"
    assert str(broker_instrument_id("AAPL", "XNAS")) == "AAPL=STK.SMART"


def test_an_unknown_venue_raises_rather_than_defaulting_to_smart():
    """
    Defaulting would make an untested routing assumption as an order is placed.

    SMART is not universally available, so a venue nobody has reasoned about must not
    acquire a destination by convenience.

    """
    with pytest.raises(UnmappedVenueError, match="no recorded IB routing"):
        broker_instrument_id("VOD", "XLON")


def test_the_research_id_keeps_the_mic_venue():
    assert str(research_instrument_id("AAPL", "XNAS")) == "AAPL.XNAS"


@pytest.mark.parametrize("venue", sorted(ROUTING_BY_VENUE))
def test_the_ticker_survives_the_mapping_in_every_known_venue(venue):
    """
    The one part both forms agree on, and so the only check that a mapping is faithful.
    """
    research = research_instrument_id("MSFT", venue)
    broker = broker_instrument_id("MSFT", venue)

    assert symbol_of(research) == "MSFT"
    assert symbol_of(broker) == "MSFT"
    assert same_instrument(research, broker)


def test_different_tickers_are_not_the_same_instrument():
    assert not same_instrument(
        research_instrument_id("AAPL", "XNAS"),
        broker_instrument_id("MSFT", "XNAS"),
    )


def test_every_registered_activation_can_reach_a_broker_id():
    """
    The check that matters: no activation is stranded on an unmappable venue.

    An activation that validates but cannot be traded is worse than one that fails to
    load, because nothing surfaces until an order is due.
    """
    for activation in load_activations():
        broker = broker_instrument_id(activation.symbol, activation.venue)
        research = research_instrument_id(activation.symbol, activation.venue)

        assert same_instrument(broker, research)
        assert str(broker).endswith("=STK.SMART")
