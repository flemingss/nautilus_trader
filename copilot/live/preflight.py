"""
Paper stages one and two: connect with orders disabled, and confirm the environment.

    IBAPI_TIMEZONE_ALIASES="JST=Asia/Tokyo" python -m copilot.live.preflight

Runs the node for a bounded window, observes what the **broker** says rather than what
configuration claims, stops cleanly, and writes an evidence record under ``out/``.

What this is actually testing
-----------------------------
Stage one's claim is not "it connected". It is that the risk engine halt **survives node
startup**. That has been verified on a *built* node but never across a start, and if it
does not hold then orders-disabled mode is a comment and every later stage has been running
with the safety off. So the halt is read before the start and again after it, and both
readings are recorded.

Stage two's claim is that the account, instruments and feed are the ones we think they are.
The distinction that matters is **read back from the broker, not echoed from config**: an
account id we supplied and then printed proves nothing. The account check therefore compares
the identifier the broker reports against the configured one and fails on a mismatch, which
is also the only way to learn whether an IB paper *login* name is the same string as its
*account* id.

Every check is recorded pass or fail, and the process exit code is non-zero if any failed,
so this can gate the next stage rather than merely inform it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import asdict
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from pathlib import Path

from copilot.live.node import build_paper_node
from copilot.live.session import PaperSession
from nautilus_trader.adapters.interactive_brokers import MarketDataType
from nautilus_trader.model import TradingState


OUT_DIR = Path(__file__).parent / "out"

DEFAULT_INSTRUMENTS = ("AAPL=STK.SMART", "MSFT=STK.SMART", "SPY=STK.SMART")
"""
Broker-side ids, in the ``SymbologyMethod.RAW`` form the IB adapter resolves.

Deliberately **not** the catalog ids. Research names the same instrument
``AAPL.XNAS`` (MIC venue, via ``data/catalog.equity_for``) and the broker will not
resolve that string. Nothing in the overlay maps between the two, which is a real gap
that has to close before stage three places an order; it is recorded here rather than
papered over by quietly using one form everywhere.

"""
DEFAULT_SETTLE_SECS = 30

NO_ACCOUNT_HINT = (
    " - no account reached the cache. Observed cause on 2026-09-01: TWS had "
    "**Read-Only API** enabled, so the execution client failed to connect with IB 321 "
    "and no account was ever reconciled. Check the TWS API settings before anything else."
)
"""
Named rather than left to be rediscovered.

Two checks fail together and neither says why: the account is missing because the
execution client never connected, and the execution client never connected because of
a checkbox in the TWS GUI. Nothing in the failure text points there.

"""


@dataclass
class Check:
    """
    One observation, its expectation, and whether they agreed.
    """

    name: str
    passed: bool
    observed: str
    expected: str
    note: str = ""


def check(name: str, *, observed: object, expected: object, note: str = "") -> Check:
    """
    Compare an observation against an expectation, as strings so the record is flat.
    """
    return Check(
        name=name,
        passed=str(observed) == str(expected),
        observed=str(observed),
        expected=str(expected),
        note=note,
    )


async def run_preflight(
    session: PaperSession,
    *,
    market_data_type: MarketDataType,
    settle_secs: int,
) -> list[Check]:
    """
    Build, start, observe, stop.

    Returns every check in the order it was made.

    """
    node, risk_engine = build_paper_node(session, market_data_type=market_data_type)

    # Captured before the run for the same reason the risk engine handle is: a hosted run
    # takes ownership of the node, and `node.cache` then raises rather than returning a
    # stale view. Learned the hard way on the first stage-one attempt.
    cache = node.cache

    checks = [
        check(
            "halt_applied_before_start",
            observed=risk_engine.trading_state,
            expected=TradingState.HALTED,
            note="orders disabled at build time",
        ),
    ]

    handle = node.handle()
    task = asyncio.create_task(node.run_async())
    try:
        # No readiness signal is exposed, so the window is a wait rather than a poll.
        # Long enough to cover the ~4.65s NAT handshake stall plus instrument loading.
        await asyncio.sleep(settle_secs)

        checks.append(
            check(
                "halt_survives_startup",
                observed=risk_engine.trading_state,
                expected=TradingState.HALTED,
                note="the claim stage one exists to settle",
            ),
        )
        checks.extend(observe_environment(cache, session))
    finally:
        handle.stop()
        try:
            await asyncio.wait_for(task, timeout=60)
        except (TimeoutError, asyncio.CancelledError):
            checks.append(
                Check(
                    name="clean_shutdown",
                    passed=False,
                    observed="did not stop within 60s",
                    expected="stopped",
                ),
            )
        else:
            checks.append(
                Check(
                    name="clean_shutdown",
                    passed=True,
                    observed="stopped",
                    expected="stopped",
                ),
            )
    return checks


def observe_environment(cache: object, session: PaperSession) -> list[Check]:
    """
    Stage two: what the broker says about the account and the instruments.

    Takes the cache rather than the node because the node refuses to hand one out while a
    run owns it.
    """
    instruments = cache.instruments()
    resolved = {str(i.id) for i in instruments}
    checks = [
        check(
            "instruments_resolved",
            observed=len(resolved & set(session.instrument_ids)),
            expected=len(session.instrument_ids),
            note=f"resolved: {sorted(resolved)}",
        ),
    ]

    # The venue is discovered from what resolved rather than assumed, because the venue an
    # IB instrument lands on is a property of the adapter's symbology, not of our config.
    venues = sorted({i.id.venue for i in instruments}, key=str)
    account_ids = [str(a) for v in venues if (a := cache.account_id(v)) is not None]

    # An account id we supplied and printed back proves nothing. This is the only check
    # that can tell us whether the IB paper *login* is the same string as the account id.
    reported = account_ids[0].split("-")[-1] if account_ids else ""
    checks.append(
        check(
            "account_reported_by_broker",
            observed=reported,
            expected=session.account_id,
            note=(
                f"venues: {[str(v) for v in venues]}; accounts: {account_ids}"
                + ("" if account_ids else NO_ACCOUNT_HINT)
            ),
        ),
    )

    account = cache.account_for_venue(venues[0]) if venues else None
    balances = account.balances() if account is not None else {}
    checks.append(
        Check(
            name="account_has_balances",
            passed=bool(balances),
            observed=str(len(balances)),
            expected=">0",
            note="a connected account with no balances means reconciliation is incomplete",
        ),
    )
    return checks


def main(argv: list[str] | None = None) -> int:
    """
    Run the preflight and file the evidence.

    Non-zero exit if any check failed.

    """
    parser = argparse.ArgumentParser(
        prog="python -m copilot.live.preflight",
        description="Paper stages one and two: connect with orders disabled, confirm environment.",
    )
    parser.add_argument("--host", default=os.getenv("IB_V2_HOST", "172.17.112.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("IB_V2_PORT", "7497")))
    parser.add_argument("--account", default=os.getenv("COPILOT_PAPER_ACCOUNT", ""))
    parser.add_argument("--data-client-id", type=int, default=801)
    parser.add_argument("--exec-client-id", type=int, default=802)
    parser.add_argument("--settle-secs", type=int, default=DEFAULT_SETTLE_SECS)
    parser.add_argument("--instruments", default=",".join(DEFAULT_INSTRUMENTS))
    parser.add_argument("--market-data-type", default="DELAYED")
    args = parser.parse_args(argv)

    if not args.account:
        print("error: no paper account; pass --account or set COPILOT_PAPER_ACCOUNT")
        return 2

    session = PaperSession(
        account_id=args.account,
        host=args.host,
        port=args.port,
        data_client_id=args.data_client_id,
        exec_client_id=args.exec_client_id,
        orders_enabled=False,
        instrument_ids=tuple(s.strip() for s in args.instruments.split(",") if s.strip()),
    )

    started = datetime.now(UTC)
    checks = asyncio.run(
        run_preflight(
            session,
            market_data_type=getattr(MarketDataType, args.market_data_type.upper()),
            settle_secs=args.settle_secs,
        ),
    )

    print(f"\n{'check':<32}{'result':<8}{'observed':<24}expected")
    for c in checks:
        print(f"{c.name:<32}{'PASS' if c.passed else 'FAIL':<8}{c.observed[:23]:<24}{c.expected}")

    failed = [c.name for c in checks if not c.passed]
    report = {
        "started_at": started.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "stage": "1-2",
        "source": f"interactive_brokers {args.host}:{args.port}",
        "market_data_type": args.market_data_type.upper(),
        "orders_enabled": False,
        "passed": not failed,
        "failed_checks": failed,
        "checks": [asdict(c) for c in checks],
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"preflight_{started.strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(report, indent=2) + "\n")
    print(f"\nWrote {path}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
