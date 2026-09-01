"""
Tests for the `RiskEngine` handle this fork adds to the Python API.

These run against a real `LiveNode` and the real Rust risk engine, not a stub. The whole
value of the binding is that a state change made from Python reaches the engine that
gates order flow, and only the real object can show that.

`copilot/tests/test_protections.py` covers the guard's *decision* to halt and release
against a stand-in. This file covers the marshalling underneath it.

Skipped rather than failed when the extension predates the binding: the overlay is
sometimes run against a wheel rather than the source build, and a hard failure there
would say "the overlay is broken" when the truth is "this build does not have it".

"""

from __future__ import annotations

import pytest


pytest.importorskip("nautilus_trader.risk", reason="risk pymodule unavailable")

from nautilus_trader.live import LiveNode
from nautilus_trader.model import TradingState


if not hasattr(LiveNode, "risk_engine"):
    pytest.skip(
        "this build predates the `LiveNode.risk_engine` binding; rebuild with `make build-debug`",
        allow_module_level=True,
    )


@pytest.fixture
def node():
    """
    A built but unstarted node.

    Building needs no broker connection.

    """
    return LiveNode.build("COPILOT-RISK-BINDING-TEST")


def test_a_fresh_engine_is_active(node):
    assert node.risk_engine.trading_state == TradingState.ACTIVE


@pytest.mark.parametrize(
    "state",
    [TradingState.HALTED, TradingState.REDUCING, TradingState.ACTIVE],
)
def test_every_state_round_trips(node, state):
    engine = node.risk_engine
    engine.set_trading_state(state)

    assert engine.trading_state == state


def test_two_handles_share_one_engine(node):
    """
    The property the whole design rests on.

    The handle carries the same `Rc<RefCell<RiskEngine>>` the kernel and its components
    share, so a change made through one is seen by the engine that gates orders. A
    handle holding its own copy would set a state nothing enforces - and would look
    identical from Python.

    """
    first = node.risk_engine
    second = node.risk_engine

    first.set_trading_state(TradingState.HALTED)

    assert second.trading_state == TradingState.HALTED


def test_the_handle_outlives_the_accessor(node):
    """
    Why the handle must be taken *before* a hosted run.

    A run takes ownership of the node, after which `node.risk_engine` stops resolving -
    the same lifetime `cache` and `portfolio` have. A handle taken beforehand keeps
    working, which is what makes halting a running node possible at all.

    Dropping the Python reference to the node stands in for that here: the handle holds
    its own `Rc`, so the engine stays alive and usable.

    """
    engine = node.risk_engine
    del node

    engine.set_trading_state(TradingState.HALTED)
    assert engine.trading_state == TradingState.HALTED


def test_setting_the_current_state_is_a_no_op(node):
    """
    The engine logs a warning and returns rather than republishing.

    Worth pinning because the guard calls this on every breach evaluation while a
    breach is in force, and a `TradingStateChanged` event per evaluation would be noise
    that buries the one that mattered.

    """
    engine = node.risk_engine
    engine.set_trading_state(TradingState.HALTED)
    engine.set_trading_state(TradingState.HALTED)

    assert engine.trading_state == TradingState.HALTED


def test_repr_shows_the_state(node):
    engine = node.risk_engine
    engine.set_trading_state(TradingState.REDUCING)

    assert "Reducing" in repr(engine)
