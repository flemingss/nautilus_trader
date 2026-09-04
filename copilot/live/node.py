"""
Builds the paper node: data client, execution client, and the halt that disables orders.

This is the first code in the overlay that puts an execution client on a
:class:`~nautilus_trader.live.LiveNode`. Everything before it was research plumbing with
no broker on the other end, and ``calibration/spread_snapshot.py`` - the template for the
data half here - deliberately builds a data-only node.

How "orders disabled" is implemented, and why
---------------------------------------------
Paper stage one is *"connect with strategy orders disabled"*. The obvious reading is to
leave the strategies out, but that tests the wrong thing: the point of stage one is to
exercise the real path far enough to find what breaks, and a node with no strategy
exercises none of it.

So strategies are added and run normally, and the **risk engine is halted before the run
starts**. ``TradingState::Halted`` is enforced natively in the Rust risk engine, which
denies every new order before it reaches an execution client. A strategy that decides to
trade therefore does everything except place the order, and the denial is recorded.

That reuses the ``RiskEngine`` handle this fork adds to ``LiveNode`` - the same binding
:mod:`copilot.risk.guard` depends on, and the reason it exists.

**The handle is taken before the run starts.** A hosted run takes ownership of the node,
so ``node.risk_engine`` stops resolving once one is under way, while a handle taken
beforehand keeps working. Getting that wrong degrades the guard silently, which is why
:func:`build_paper_node` returns the handle rather than leaving the caller to find it.

What is verified, and what is not
---------------------------------
Verified against a built node: the handle resolves, ``set_trading_state`` takes effect,
and the state is still ``HALTED`` when read back through a *fresh* ``node.risk_engine``,
so the binding shares one engine rather than handing out copies.

**Not** verified: that the halt survives node **startup**, which needs a broker on the
other end. Nothing found in the risk engine sets ``ACTIVE`` on start, and the guard is
written on the same understanding, but neither has met a running node. Confirming it is
the first thing stage one exists to do, and until then no run is left unattended on the
strength of it.

Actors
------
``LiveNode.add_actor`` **is** exposed to Python on this build - it was added to the fork
so ``spread_snapshot`` could record quotes from committed code, and this docstring said
otherwise until 2026-09-04. It matters now rather than academically: the strategy cannot
get a daily bar from the broker (IB 2188, re-probed 2026-09-04), so the only route to a
paper session that decides anything is an actor publishing the catalog's own bars.
"""

from __future__ import annotations

from typing import Protocol

from copilot.live.session import PaperSession
from copilot.live.symbology import ROUTING_BY_VENUE
from nautilus_trader.adapters.interactive_brokers import InteractiveBrokersDataClientConfig
from nautilus_trader.adapters.interactive_brokers import InteractiveBrokersDataClientFactory
from nautilus_trader.adapters.interactive_brokers import InteractiveBrokersExecutionClientConfig
from nautilus_trader.adapters.interactive_brokers import InteractiveBrokersExecutionClientFactory
from nautilus_trader.adapters.interactive_brokers import InteractiveBrokersInstrumentProviderConfig
from nautilus_trader.adapters.interactive_brokers import MarketDataType
from nautilus_trader.adapters.interactive_brokers import SymbologyMethod
from nautilus_trader.common import Environment
from nautilus_trader.live import LiveNode
from nautilus_trader.live import LiveRiskEngineConfig
from nautilus_trader.live import RoutingConfig
from nautilus_trader.model import InstrumentId
from nautilus_trader.model import TraderId
from nautilus_trader.model import TradingState


class SupportsTradingState(Protocol):
    """
    The one thing this module needs from a ``RiskEngine``.

    Narrower than the engine itself on purpose: the order switch should be testable
    against a recorder, and stating the requirement as one method is what makes that
    honest rather than a cast.

    """

    def set_trading_state(self, state: TradingState) -> None:
        """
        Set the engine-wide trading state.
        """


NODE_NAME = "COPILOT-PAPER"
TRADER_ID = "PAPER-001"

CONNECTION_TIMEOUT_SECS = 60


def build_paper_node(
    session: PaperSession,
    *,
    market_data_type: MarketDataType = MarketDataType.DELAYED,
    symbology: SymbologyMethod = SymbologyMethod.RAW,
    strategies: tuple[object, ...] = (),
    actors: tuple[object, ...] = (),
    risk_engine_config: LiveRiskEngineConfig | None = None,
    logging_config: object | None = None,
) -> tuple[LiveNode, SupportsTradingState]:
    """
    Build the paper node and return it with its risk engine handle.

    The session proved itself paper at construction, so nothing here re-argues it.
    ``market_data_type`` defaults to ``DELAYED`` because that is the only US equity feed
    this account currently has; see ``playbook/PREFLIGHT.md``.

    """
    instrument_ids = {InstrumentId.from_str(s) for s in session.instrument_ids}
    provider = InteractiveBrokersInstrumentProviderConfig(
        symbology_method=symbology,
        load_ids=instrument_ids,
    )

    builder = LiveNode.builder(NODE_NAME, TraderId.from_str(TRADER_ID), Environment.LIVE)
    if logging_config is not None:
        # Needed to see the risk engine's account-resolution failures, which are DEBUG
        # level and are how a silently inert notional cap was found.
        builder = builder.with_logging(logging_config)
    if risk_engine_config is not None:
        # Order limits belong outside the strategy, per playbook/OPERATIONS.md. A
        # strategy cannot relax its own cap if the cap is engine configuration.
        builder = builder.with_risk_engine_config(risk_engine_config)

    node = (
        builder.add_data_client(
            None,
            InteractiveBrokersDataClientFactory(),
            InteractiveBrokersDataClientConfig(
                host=session.host,
                port=session.port,
                client_id=session.data_client_id,
                market_data_type=market_data_type,
                connection_timeout=CONNECTION_TIMEOUT_SECS,
                instrument_provider=provider,
            ),
        )
        .add_exec_client(
            None,
            InteractiveBrokersExecutionClientFactory(),
            InteractiveBrokersExecutionClientConfig(
                host=session.host,
                port=session.port,
                client_id=session.exec_client_id,
                account_id=session.account_id,
                connection_timeout=CONNECTION_TIMEOUT_SECS,
                # `reqAllOpenOrders` rather than `reqOpenOrders`, which returns only orders
                # bound to the calling client id. Every run here uses a fresh client id, so
                # the default left each one blind to every order any previous run had placed
                # - and the sweep tool reported "nothing working" while orders were live at
                # the broker. An operations tool that cannot see the account's orders is
                # worse than none.
                fetch_all_open_orders=True,
                instrument_provider=provider,
            ),
            # Orders route by the instrument's venue, and the execution client does not
            # register under one - the account reads `IB-DUT067974` while instruments
            # resolve on `SMART`. Without this the engine finds no client for `SMART` and
            # denies every order with NO_EXECUTION_CLIENT.
            #
            # The routing destinations are listed rather than `default=True` on purpose.
            # A default would route *any* venue here, including a research-form id like
            # `AAPL.XNAS` that nothing should be able to trade; listing them keeps the
            # deny as a backstop against exactly that mistake.
            RoutingConfig(venues=sorted(set(ROUTING_BY_VENUE.values()))),
        )
        .build()
    )

    for strategy in strategies:
        node.add_strategy(strategy)
    for actor in actors:
        node.add_actor(actor)

    risk_engine = node.risk_engine
    apply_order_switch(risk_engine, orders_enabled=session.orders_enabled)
    return node, risk_engine


def apply_order_switch(risk_engine: SupportsTradingState, *, orders_enabled: bool) -> None:
    """
    Halt the risk engine unless orders are explicitly enabled.

    Only ever sets ``HALTED``. It never sets ``ACTIVE``, for the same reason the guard
    does not: an engine found halted may have been halted by a breaker that knows
    something this function does not, and clearing it here would be a silent override.
    Enabling orders is therefore the *absence* of a halt at build time, not an
    instruction to resume.

    """
    if not orders_enabled:
        risk_engine.set_trading_state(TradingState.HALTED)


__all__ = [
    "CONNECTION_TIMEOUT_SECS",
    "NODE_NAME",
    "TRADER_ID",
    "SupportsTradingState",
    "apply_order_switch",
    "build_paper_node",
]
