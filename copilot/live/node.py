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

import asyncio
import sys
from collections.abc import Callable
from typing import Protocol

from copilot.live.halt import orders_allowed
from copilot.live.halt import read_latch
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
    latch = read_latch()
    allowed = orders_allowed(
        requested=session.orders_enabled,
        cancels_only=session.cancels_only,
        latch=latch,
    )
    if session.orders_enabled and not allowed and latch is not None:
        print(
            f"HALT LATCH {latch.latch_id} engaged {latch.engaged_at} ({latch.trigger}): "
            f"{latch.reason}. This node starts HALTED; release with "
            "python -m copilot.live.kill --release.",
            file=sys.stderr,
        )
    apply_order_switch(risk_engine, orders_enabled=allowed)
    return node, risk_engine


CANCEL_DEADLINE_SECS = 120
"""
How long to wait for the broker's cancellation acknowledgements before giving up.

Measured, not guessed. On 2026-09-10 a stranded order was swept three times: the
acknowledgement had not arrived 30s after the cancel, nor 90s after a second one, and the
order was gone by the next connection. The old fixed 30s window therefore reported *the
cancel was refused* for an order the broker did cancel, which is the worst kind of wrong -
it sends the operator to the TWS order list to fix something that is already fixed, and it
teaches them that the probe's FAIL means nothing.

Waiting longer is not the whole of the fix. A fixed sleep cannot tell slow from refused at
any length, which is why :func:`wait_for_settlement` polls and returns whether it settled.

"""


async def wait_for_settlement(
    settled: Callable[[], bool],
    *,
    deadline_secs: int,
    poll_secs: float = 1.0,
) -> bool:
    """
    Wait until ``settled`` is true, and report whether it became true in time.

    Returns as soon as it does, so the common case costs one poll rather than the whole
    deadline. A ``False`` return means the deadline genuinely passed with the condition
    unmet - which is the distinction a fixed sleep cannot draw.

    """
    deadline = asyncio.get_running_loop().time() + deadline_secs
    while True:
        if settled():
            return True
        if asyncio.get_running_loop().time() >= deadline:
            return False
        await asyncio.sleep(poll_secs)


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
    "CANCEL_DEADLINE_SECS",
    "CONNECTION_TIMEOUT_SECS",
    "NODE_NAME",
    "TRADER_ID",
    "SupportsTradingState",
    "apply_order_switch",
    "build_paper_node",
    "wait_for_settlement",
]
