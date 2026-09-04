"""
Tests for the monitoring-end sweep's coverage and its verdict.

The broker half is exercised by running it. What is testable here is the part that
decided, for a day, that the sweep covered one instrument in nine: which instruments an
invocation means, and whether "clear" is computed from acknowledgements rather than from
having sent a cancel.

"""

from __future__ import annotations

from copilot.live.cancel_working import CancelWorking
from copilot.live.cancel_working import CancelWorkingConfig
from copilot.live.cancel_working import instruments_to_sweep
from copilot.live.symbology import registered_instruments
from nautilus_trader.model import InstrumentId


def test_all_means_the_registry() -> None:
    # The sweep follows the universe; a list would have stopped at the first three.
    assert instruments_to_sweep(all_=True, symbol=None, venue="XNAS") == registered_instruments()
    assert len(registered_instruments()) > 1


def test_a_symbol_means_that_one_in_the_brokers_form() -> None:
    assert instruments_to_sweep(all_=False, symbol="schx", venue="arcx") == ("SCHX=STK.SMART",)


def test_naming_nothing_sweeps_nothing() -> None:
    # Empty rather than a default: the old AAPL default swept one instrument while the
    # operator read RESULT: CACHE CLEAR as the session being closed.
    assert instruments_to_sweep(all_=False, symbol=None, venue="XNAS") == ()


def test_all_wins_over_a_symbol() -> None:
    assert len(instruments_to_sweep(all_=True, symbol="AAPL", venue="XNAS")) == len(
        registered_instruments(),
    )


def _sweeper(*ids: str) -> CancelWorking:
    return CancelWorking(
        CancelWorkingConfig(instrument_ids=tuple(InstrumentId.from_str(i) for i in ids)),
    )


def test_outstanding_is_what_was_found_and_never_acknowledged() -> None:
    # Sending a cancel is not evidence; the acknowledgement is. An order found working
    # and never acknowledged stays outstanding whatever was sent.
    strategy = _sweeper("AAPL=STK.SMART", "SCHX=STK.SMART")
    strategy.before["AAPL=STK.SMART"] = ["O-1", "O-2"]
    strategy.before["SCHX=STK.SMART"] = ["O-3"]
    strategy.canceled = ["O-1", "O-3"]
    assert strategy.outstanding() == {"AAPL=STK.SMART": ["O-2"]}


def test_nothing_found_is_clear() -> None:
    assert _sweeper("AAPL=STK.SMART").outstanding() == {}


def test_every_configured_instrument_is_reported_even_when_clear() -> None:
    # A sweep that only listed instruments with orders would read the same whether it
    # covered nine instruments or one.
    strategy = _sweeper("AAPL=STK.SMART", "SCHX=STK.SMART")
    assert set(strategy.before) == {"AAPL=STK.SMART", "SCHX=STK.SMART"}
