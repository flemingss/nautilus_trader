"""
Run one registered activation against the paper broker, on the catalog's own bars.

    export IBAPI_TIMEZONE_ALIASES="JST=Asia/Tokyo"
    python -m copilot.live.run_activation aapl-gap-fade-long-next-close --account DUT067974

The first thing in this repository that puts a **researched strategy** on a broker
connection. Everything before it was plumbing exercised by purpose-built probes; this runs
the rule that a walk-forward scored, over the series that scored it, and records what it
decided.

Why the bars come from a file
-----------------------------
Because the broker will not supply them. ``GapReversalStrategy.on_start`` subscribes to
``1-DAY-LAST-EXTERNAL``, the adapter routes any spec other than five seconds to
``reqHistoricalData``, and IB refuses US equity historical bars on this account with
**2188** - re-probed 2026-09-04 across five request shapes, on REALTIME and DELAYED, SMART
and directed. The subscription is left in place and its refusal is recorded, because a
session that quietly did without it would hide the constraint that shapes this whole module.

So the catalog is the data client for daily bars. That is not a workaround so much as the
arrangement [ADR-0017] made possible: research and execution read one series, kept current
by ``copilot.data.append``, so a live decision and its replay are comparable rather than
merely similar.

**What this skips, stated plainly.** There is no Python route into the engine's data path
on this build - no message bus, no data engine, and ``publish_data`` reaches a custom-data
topic rather than the bars topic. So the bar is handed to the strategy directly, through
``decide``, which updates the indicator before the rule the way the engine does; handing it
to ``on_bar`` alone left the ATR one bar behind the replay's, found 2026-09-05 by the
comparison in :mod:`copilot.live.compare`. The engine's cache still does not see the bar,
and anything that marks from the last bar - unrealised PnL, in particular - will not. With
orders denied and no position open that is inert. **It stops being inert the moment orders
are enabled**, and closing it properly means exposing a bar publish on the node, which is a
Rust change.

Orders are denied, deliberately
-------------------------------
The risk engine is ``HALTED`` before the run starts, so a strategy that decides to trade
does everything except place the order and the denial is recorded. That is the whole point
at this stage: the charter's *System* gate asks whether the code does what the research
says, and that question is answerable without a frozen candidate. Enabling orders is paper
stage seven, which needs one.

Two prices, one instrument
--------------------------
Research names the instrument ``AAPL.XNAS`` and the broker resolves ``AAPL=STK.SMART``
(:mod:`copilot.live.symbology`). The strategy is configured with the **broker** id, because
its orders have to be routable, and its bars are built against the **broker's** instrument
so their precision is the one the venue actually quotes. The catalog stores four decimals
because the vendor delivers four; the broker prices in its own increment, and a bar handed
to a live strategy has to be expressed in the broker's terms. That conversion rounds, which
:func:`to_broker_bars` reports rather than refuses - the opposite of
``catalog.to_nautilus_bars``, which refuses, because a stored history that rounds is corrupt
while a live bar that does not is unusable.

[ADR-0017]: ../docs/decisions/0017-the-evaluation-window-is-pinned-at-both-ends.md

"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import date
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from copilot.calibration.cost_model import CostModel
from copilot.data.catalog import BAR_SPEC
from copilot.data.catalog import equity_for
from copilot.data.catalog import read_series
from copilot.live.account import EXEC_CLIENT_VENUE
from copilot.live.account import reported_equity
from copilot.live.manifest import Manifest
from copilot.live.manifest import current_commit
from copilot.live.manifest import manifest_for
from copilot.live.node import build_paper_node
from copilot.live.session import PaperSession
from copilot.live.session import add_broker_arguments
from copilot.live.symbology import broker_instrument_id
from copilot.live.warmup import load as load_warmup
from copilot.live.warmup import session_to_prepare
from copilot.paths import add_catalog_argument
from copilot.risk.budget import DEFAULT_RISK_FRACTION
from copilot.risk.budget import RiskPolicy
from copilot.risk.budget import budget_for
from copilot.risk.exposure import ExposureLedger
from copilot.strategies.activations import Activation
from copilot.strategies.activations import find_activation
from copilot.strategies.activations import load_activations
from copilot.strategies.promotion import Provenance
from copilot.strategies.promotion import UnscoredParametersError
from copilot.strategies.promotion import provenance
from copilot.validation.types import DailyBar
from nautilus_trader.adapters.interactive_brokers import MarketDataType
from nautilus_trader.model import Bar
from nautilus_trader.model import BarType
from nautilus_trader.model import Equity
from nautilus_trader.model import InstrumentId
from nautilus_trader.model import Venue
from nautilus_trader.trading import Strategy


OUT_DIR = Path(__file__).parent / "out"

SETTLE_SECS = 30
"""
Seconds between node start and handing over the decision bar.

The instrument provider has to have resolved the contract before a bar can be built
against it, and the exec client has to be up before a denial can be recorded. Generous
rather than tight: this runs once a day and a short wait that occasionally is not long
enough would produce a session that looks like a strategy decision and is a race.

"""

DRAIN_SECS = 10
"""
Seconds after the decision, to let order events land before the cache is read.
"""


@dataclass(frozen=True)
class Plan:
    """
    One activation and the bars the session will hand it, decided before any connection.
    """

    activation: Activation
    warmup: tuple[DailyBar, ...]
    decision: DailyBar
    provenance: Provenance
    """
    Where the parameters came from, checked before the plan exists.

    A trading activation whose parameters are not its frozen set has no plan; the check
    raises and the session is refused before a connection is opened.

    """
    manifest: Manifest | None = None
    """
    Commit, newest verdict and the four digests, so the record ties to what was scored.
    """


class NoBrokerInstrumentError(RuntimeError):
    """
    The broker did not resolve the instrument the strategy was configured for.
    """


@dataclass(frozen=True)
class OrderRecord:
    """
    One order the strategy submitted, and what became of it.
    """

    client_order_id: str
    side: str
    quantity: str
    order_type: str
    status: str
    price: str | None = None


@dataclass
class SessionRecord:
    """
    What one activation's session decided, and everything needed to audit it.
    """

    activation: str
    broker_instrument: str
    research_instrument: str
    decision_bar: str
    warmup_bars: int
    warmup_from: str
    warmup_to: str
    parameters: dict[str, str]
    parameters_source: dict[str, object] = field(default_factory=dict)
    """
    Whether these parameters are a seeded identity or a frozen set, and what was
    unfixed.
    """
    manifest: dict[str, object] = field(default_factory=dict)
    """
    The playbook's first Before line: commit, verdict, and whether the inputs moved.
    """
    atr_initialized: bool = False
    atr_value: str | None = None
    previous_close: str | None = None
    deferred_atr: str | None = None
    """
    Set when a ``next_close`` rule triggered: the decision is made, the entry is not.

    Recorded because a triggered next-close decision produced neither a skip nor an
    order, and until 2026-09-05 the record could not tell it from a bar the rule never
    looked at.

    """
    outcome: str | None = None
    """
    The one thing the decision bar did, in the strategy's own words.
    """
    skips: dict[str, int] = field(default_factory=dict)
    orders: list[OrderRecord] = field(default_factory=list)
    largest_rounding: str = "0"
    instrument_resolved: bool = False
    budget: dict[str, str] = field(default_factory=dict)
    """
    What the session sized against, derived from the equity the broker reported.
    """
    basket_position: int = 0
    """
    Where this activation stood in the order bars were handed out, from one.
    """
    exposure_after: dict[str, object] = field(default_factory=dict)
    """
    The shared ledger as it stood once this activation had decided.
    """
    note: str = ""

    @property
    def decided_to_trade(self) -> bool:
        """
        Whether the rule decided to enter: an order, denied or not, or a deferred entry.

        A ``next_close`` rule that triggers places nothing on the decision bar, so a
        session that reads only its orders reports a trigger as a quiet bar.

        """
        return bool(self.orders) or self.deferred_atr is not None

    @property
    def decision(self) -> dict[str, object]:
        """
        Return the fields the offline replay recomputes, in the strategy's own shape.
        """
        return {
            "atr_initialized": self.atr_initialized,
            "atr_value": self.atr_value,
            "previous_close": self.previous_close,
            "deferred_atr": self.deferred_atr,
            "outcome": self.outcome,
            "skips": dict(self.skips),
        }


def broker_bar_type(instrument_id: InstrumentId) -> BarType:
    """
    Return the daily bar type in the broker's own instrument namespace.

    The spec matches the catalog's exactly - the series is the same series - and only the
    instrument id differs, which is the one thing that has to.

    """
    return BarType.from_str(f"{instrument_id}-{BAR_SPEC}")


def to_broker_bars(
    bars: tuple[DailyBar, ...],
    instrument: Equity,
    bar_type: BarType,
) -> tuple[list[Bar], Decimal]:
    """
    Convert catalog bars to the broker's precision, reporting the largest rounding.

    Reports rather than refuses, unlike ``catalog.to_nautilus_bars``. The difference is
    the direction of the harm: a *stored* history that silently rounded would be read as
    ground truth by every later run, while a *live* bar that refuses to round cannot be
    handed to a strategy at all. The amount is returned so a session that rounded more
    than a tick has somewhere to say so.

    """
    out: list[Bar] = []
    largest = Decimal(0)
    for daily in sorted(bars, key=lambda b: b.closed_at):
        prices = []
        for source in (daily.open, daily.high, daily.low, daily.close):
            converted = instrument.make_price(source)
            largest = max(largest, abs(Decimal(str(converted)) - source))
            prices.append(converted)
        ts = int(daily.closed_at.timestamp() * 1_000_000_000)
        out.append(
            Bar(
                bar_type=bar_type,
                open=prices[0],
                high=prices[1],
                low=prices[2],
                close=prices[3],
                volume=instrument.make_qty(daily.volume),
                ts_event=ts,
                ts_init=ts,
            ),
        )
    return out, largest


def build_strategy(activation: Activation, instrument_id: InstrumentId) -> Strategy:
    """
    Build the activation's strategy against the broker's instrument id.

    The parameters are the activation's own - its seeded identity plus whatever the
    registry fixes - and **not** read from a holdout's frozen set. Reading frozen
    parameters into a live run is a promotion, and a promotion is a diff someone
    reviewed rather than a default this module reaches for. What the module does
    instead is check the diff was made: :func:`copilot.strategies.promotion.provenance`
    refuses a PAPER or LIVE activation whose parameters are not its frozen set, and
    labels a RESEARCH one as seeded.

    """
    return activation.setup.factory(
        {**activation.parameters, "order_id_tag": _tag_for(activation)},
        instrument_id=instrument_id,
        bar_type=broker_bar_type(instrument_id),
        risk_registry=None,
    )


def _tag_for(activation: Activation) -> str:
    """
    Return the order-id tag that makes this activation's strategy distinct in a node.

    Several strategies run in one node now, and Nautilus keys them by strategy id. Two
    built from the same class with no tag collide silently: the second registration
    replaces the first, and the session reports one decision where it should report two.
    The symbol is the tag because it is what makes the activations different.

    """
    return activation.symbol.lower()


async def run_session(
    session: PaperSession,
    plans: tuple[Plan, ...],
    *,
    policy: RiskPolicy,
    allocation: Decimal | None,
    settle_secs: int = SETTLE_SECS,
    drain_secs: int = DRAIN_SECS,
) -> tuple[SessionRecord, ...]:
    """
    Start one node for the whole basket, hand each strategy its bar, and record it all.

    One node rather than one per activation, because the session-wide cap is a property
    of the session: strategies that cannot see each other's reservations cannot be capped
    together, and the shape that motivated the ledger - four risk-on wrappers deciding on
    the same morning - only exists when they decide in one place.

    Bars are handed out in the order ``plans`` arrives, and the ledger grants in that
    order. The order is recorded per activation so a refusal can be read against who was
    asked first.

    """
    strategies = {
        plan.activation.name: build_strategy(
            plan.activation,
            broker_instrument_id(plan.activation.symbol, plan.activation.venue),
        )
        for plan in plans
    }
    records = {
        plan.activation.name: SessionRecord(
            activation=plan.activation.name,
            broker_instrument=str(
                broker_instrument_id(plan.activation.symbol, plan.activation.venue),
            ),
            research_instrument=str(equity_for(plan.activation.symbol, plan.activation.venue).id),
            decision_bar=plan.decision.closed_at.date().isoformat(),
            warmup_bars=len(plan.warmup),
            warmup_from=plan.warmup[0].closed_at.date().isoformat(),
            warmup_to=plan.warmup[-1].closed_at.date().isoformat(),
            parameters={k: str(v) for k, v in plan.activation.parameters.items()},
            parameters_source=plan.provenance.as_record(),
            manifest=plan.manifest.as_record() if plan.manifest else {},
            basket_position=index + 1,
        )
        for index, plan in enumerate(plans)
    }

    node, _risk_engine = build_paper_node(
        session,
        market_data_type=MarketDataType.DELAYED,
        strategies=tuple(strategies.values()),
    )
    # Captured before the run, for the same reason ``build_paper_node`` returns the risk
    # engine handle: a hosted run takes ownership of the node, and ``node.cache`` raises
    # once one is under way. Reaching for it mid-session is the mistake this line exists
    # to have made once.
    cache = node.cache
    handle = node.handle()
    task = asyncio.create_task(node.run_async())
    try:
        await asyncio.sleep(settle_secs)

        # One equity, one budget, one ledger for the basket. Read here rather than at
        # construction because the equity does not exist until the exec client has
        # connected, and the strategies have to exist before the node.
        first = plans[0].activation
        venues = (broker_instrument_id(first.symbol, first.venue).venue, Venue(EXEC_CLIENT_VENUE))
        equity, _account = reported_equity(cache, venues)
        budget = budget_for(equity, allocation=allocation, policy=policy)
        ledger = ExposureLedger(
            max_total_risk=budget.max_total_risk,
            max_new_entries=policy.max_new_entries,
        )

        for plan in plans:
            name = plan.activation.name
            strategy, record = strategies[name], records[name]
            broker_id = broker_instrument_id(plan.activation.symbol, plan.activation.venue)
            instrument = cache.instrument(broker_id)
            if instrument is None:
                raise NoBrokerInstrumentError(
                    f"the broker did not resolve {broker_id} within {settle_secs}s. A "
                    f"bar cannot be built without its instrument's precision; check the "
                    f"instrument provider and the connection before reading anything "
                    f"else into this session.",
                )
            record.instrument_resolved = True

            strategy.size_against(budget.risk_budget, budget.max_notional, ledger=ledger)
            record.budget = budget.as_record()

            bar_type = broker_bar_type(broker_id)
            warm_bars, warm_rounding = to_broker_bars(plan.warmup, instrument, bar_type)
            decision_bars, decision_rounding = to_broker_bars(
                (plan.decision,),
                instrument,
                bar_type,
            )
            record.largest_rounding = str(max(warm_rounding, decision_rounding))

            strategy.warm_up(warm_bars)
            record.atr_initialized = bool(strategy.decision_record()["atr_initialized"])
            strategy.decide(decision_bars[0])
            record.exposure_after = ledger.as_record()

        await asyncio.sleep(drain_secs)

        for plan in plans:
            name = plan.activation.name
            strategy, record = strategies[name], records[name]
            decided = strategy.decision_record()
            record.atr_value = decided["atr_value"]
            record.previous_close = decided["previous_close"]
            record.deferred_atr = decided["deferred_atr"]
            record.outcome = decided["outcome"]
            record.skips = dict(decided["skips"])
            record.orders = [
                OrderRecord(
                    client_order_id=str(order.client_order_id),
                    side=str(order.side),
                    quantity=str(order.quantity),
                    order_type=str(order.order_type),
                    status=str(order.status),
                    price=str(order.price) if hasattr(order, "price") else None,
                )
                for order in cache.orders()
                if str(order.strategy_id) == str(strategy.strategy_id)
            ]
    finally:
        handle.stop()
        try:
            await asyncio.wait_for(task, timeout=90)
        except (TimeoutError, asyncio.CancelledError):
            for record in records.values():
                record.note = "node did not stop cleanly within 90s"
    return tuple(records[plan.activation.name] for plan in plans)


def _report(record: SessionRecord) -> None:
    """
    Print what the session decided, in the order an operator reads it.
    """
    print(f"\n  activation      {record.activation}")
    print(f"  instrument      {record.broker_instrument}  (research {record.research_instrument})")
    print(f"  warm-up         {record.warmup_bars} bars, {record.warmup_from}..{record.warmup_to}")
    print(f"  decision bar    {record.decision_bar}")
    if record.parameters_source:
        print(f"  parameters      {Provenance.from_record(record.parameters_source).label}")
    if record.manifest:
        print(f"  manifest        {Manifest(**_manifest_fields(record.manifest)).label}")
    print(f"  indicator       initialized={record.atr_initialized}  atr={record.atr_value}")
    print(f"  previous close  {record.previous_close}")
    _report_sizing(record)
    if record.largest_rounding != "0":
        print(f"  rounding        {record.largest_rounding} to the broker's precision")
    _report_outcome(record)
    if record.note:
        print(f"  note            {record.note}")


def _manifest_fields(record: dict[str, object]) -> dict[str, object]:
    """
    Rebuild a manifest's fields from the record, so the report reads what was filed.
    """
    return {
        "code_commit": str(record["code_commit"]),
        "tree_clean": bool(record["tree_clean"]),
        "verdict": record.get("verdict"),
        "inputs": dict(record.get("inputs") or {}),  # type: ignore[call-overload]
        "moved": tuple(record.get("moved") or ()),  # type: ignore[arg-type]
    }


def _report_sizing(record: SessionRecord) -> None:
    """
    Print what the session sized against and where the shared ledger stood after it.
    """
    if record.budget:
        b = record.budget
        capped = (
            "  (request exceeded equity, capped)" if b.get("allocation_capped") == "True" else ""
        )
        print(f"  equity          {b['equity']} reported; sizing against {b['allocation']}{capped}")
        print(
            f"  risk budget     {b['risk_budget']} per position ({b['risk_fraction']} of "
            f"allocation), notional cap {b['max_notional']} ({b['max_position_fraction']})",
        )
    if record.exposure_after:
        x = record.exposure_after
        print(
            f"  session risk    {x['total']} of {x['max_total_risk']} reserved after this "
            f"decision; entries {x['entries']}/{x['max_new_entries']}  "
            f"(basket position {record.basket_position})",
        )


def _report_outcome(record: SessionRecord) -> None:
    """
    Print the one thing the decision bar did: declined, ordered, or deferred.
    """
    for reason, count in sorted(record.skips.items()):
        print(f"  declined        {reason} x{count}")
    if record.orders:
        for order in record.orders:
            print(
                f"  ORDER           {order.side} {order.quantity} {order.order_type} "
                f"-> {order.status}",
            )
    elif record.deferred_atr is not None:
        print(
            f"  DEFERRED        triggered at atr={record.deferred_atr}; a next_close entry "
            f"belongs to the session now opening, and the live path does not yet carry "
            f"it there (ROADMAP)",
        )
    else:
        print("  no order        the rule did not fire on this bar")


def main(argv: list[str] | None = None) -> int:
    """
    Run one activation for one session and file what it decided.
    """
    parser = argparse.ArgumentParser(
        prog="python -m copilot.live.run_activation",
        description="Run a registered activation on the paper broker, orders denied.",
    )
    add_broker_arguments(parser, data_client_id=871, exec_client_id=872)
    parser.add_argument(
        "activations",
        nargs="*",
        help="Activation names, handed bars in this order (default with --all: every "
        "next_close activation, by name)",
    )
    parser.add_argument("--all", action="store_true", help="Run every next_close activation")
    add_catalog_argument(parser)
    parser.add_argument("--session", help="Session to run for, YYYY-MM-DD (default: the next)")
    parser.add_argument("--settle-secs", type=int, default=SETTLE_SECS)
    parser.add_argument(
        "--allocation",
        type=Decimal,
        default=None,
        help="Capital this activation sizes against (default: the reported equity; never more)",
    )
    parser.add_argument(
        "--risk-fraction",
        type=Decimal,
        default=DEFAULT_RISK_FRACTION,
        help=(
            f"Planned risk per position as a fraction of allocation "
            f"(default {DEFAULT_RISK_FRACTION})"
        ),
    )
    args = parser.parse_args(argv)

    if not args.account:
        print("error: no paper account; pass --account or set COPILOT_PAPER_ACCOUNT")
        return 2

    if args.all:
        activations = tuple(
            a
            for a in sorted(load_activations(), key=lambda a: a.name)
            if a.parameters.get("entry_timing") == "next_close"
        )
    else:
        activations = tuple(find_activation(name) for name in args.activations)
    if not activations:
        print("error: name at least one activation, or pass --all")
        return 2
    first_session = (
        datetime.fromisoformat(args.session).date()
        if args.session
        else session_to_prepare(datetime.now(tz=UTC))
    )

    cost_model = CostModel.from_snapshot()
    commit = current_commit()
    plans = []
    for activation in activations:
        # Before any bar is read: a trading activation whose parameters the gate never
        # scored has no session, and a research one is labelled as seeded.
        try:
            source = provenance(activation)
        except UnscoredParametersError as e:
            print(f"refused: {e}")
            return 2
        # The warm-up loader is used as the gate and not as the source. It refuses a
        # stale or holed window - the same refusal the operator's morning check makes -
        # but it returns bars built at the catalog's precision, and what reaches the
        # strategy has to be built at the broker's.
        load_warmup(args.catalog, activation, first_session=first_session)

        # The last bar before the session is the one the rule decides on; the warm-up is
        # the history behind it.
        catalog_bars = _catalog_bars(args.catalog, activation, first_session)
        needed = activation.setup.warmup_bars
        if len(catalog_bars) < needed + 1:
            print(f"error: {activation.name}: {len(catalog_bars)} bars, need {needed + 1}")
            return 2
        plans.append(
            Plan(
                activation=activation,
                warmup=catalog_bars[-(needed + 1) : -1],
                decision=catalog_bars[-1],
                provenance=source,
                # Over the whole stored series, as the verdict's own digest was taken.
                manifest=manifest_for(
                    activation,
                    read_series(args.catalog, activation.symbol, activation.venue),
                    cost_model,
                    commit=commit,
                ),
            ),
        )

    names = ", ".join(a.name for a in activations)
    print(
        f"Running {len(plans)} activation(s) for the session opening {first_session}: "
        f"{names}.\n"
        f"Orders are DENIED in the risk engine: this measures what the rule decides, "
        f"not what it fills. Enabling them is paper stage seven and needs a frozen "
        f"candidate.\n",
    )

    session = PaperSession(
        account_id=args.account,
        host=args.host,
        port=args.port,
        data_client_id=args.data_client_id,
        exec_client_id=args.exec_client_id,
        orders_enabled=False,
        instrument_ids=tuple(
            dict.fromkeys(str(broker_instrument_id(a.symbol, a.venue)) for a in activations),
        ),
    )

    started = datetime.now(tz=UTC)
    records = asyncio.run(
        run_session(
            session,
            tuple(plans),
            policy=RiskPolicy(risk_fraction=args.risk_fraction),
            allocation=args.allocation,
            settle_secs=args.settle_secs,
        ),
    )
    for record in records:
        _report(record)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"run_activation_{started.strftime('%Y%m%dT%H%M%SZ')}.json"
    payload = {
        "run_at": started.isoformat(),
        "session": first_session.isoformat(),
        "orders_enabled": False,
        "basket": [a.name for a in activations],
        "activations": [
            {
                **{k: v for k, v in vars(record).items() if k != "orders"},
                "orders": [vars(o) for o in record.orders],
            }
            for record in records
        ],
    }
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n")
    print(f"\n  filed {path}")
    return 0 if all(record.instrument_resolved for record in records) else 1


def _catalog_bars(catalog_path: str, activation: Activation, before: date) -> tuple[DailyBar, ...]:
    """
    Return the catalog's bars for this instrument, ending before ``before``.
    """
    stored = read_series(catalog_path, activation.symbol, activation.venue)
    return tuple(bar for bar in stored if bar.closed_at.date() < before)


__all__ = [
    "DRAIN_SECS",
    "SETTLE_SECS",
    "NoBrokerInstrumentError",
    "OrderRecord",
    "Plan",
    "SessionRecord",
    "broker_bar_type",
    "build_strategy",
    "run_session",
    "to_broker_bars",
]


if __name__ == "__main__":
    sys.exit(main())
