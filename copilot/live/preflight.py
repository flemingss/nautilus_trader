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

The quote check, added 2026-09-05
---------------------------------
The playbook's Before list asks for *a fresh, correctly timestamped, non-crossed bid and
ask for every tradable instrument*, and the operator-day draft claimed this command
provided it until the walk ran it and found that none of the six checks looked at a price.
A stale or crossed quote is how a bracket gets placed around the wrong level, and this
account is on delayed data. So the node now carries a quote watcher, and one check per
instrument asks for at least :data:`MIN_QUOTES` quotes, the last of them within
:data:`MAX_QUOTE_AGE_SECS`, with a positive bid strictly below the ask.

Delayed data suffices for the check and shapes it. IB's delayed feed carries no exchange
timestamp for a quote, so age is measured from arrival rather than from the print, and
the note on each check says under which feed and market state it was read. Outside a
session - and, if the walk shows so, before the open - there are no quotes to read, and
the check fails as it should: the operator learns the feed is silent before an order
depends on it.

Every check is recorded pass or fail, and the process exit code is non-zero if any failed,
so this can gate the next stage rather than merely inform it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections.abc import Mapping
from dataclasses import asdict
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from copilot.data.calendar import EASTERN
from copilot.data.calendar import is_trading_day
from copilot.data.calendar import session_close
from copilot.data.calendar import session_open
from copilot.live.account import EXEC_CLIENT_VENUE
from copilot.live.account import find_account
from copilot.live.node import build_paper_node
from copilot.live.session import PaperSession
from copilot.live.session import add_broker_arguments
from copilot.live.symbology import registered_instruments
from nautilus_trader.adapters.interactive_brokers import MarketDataType
from nautilus_trader.common import DataActor
from nautilus_trader.common import DataActorConfig
from nautilus_trader.model import InstrumentId
from nautilus_trader.model import TradingState
from nautilus_trader.model import Venue


OUT_DIR = Path(__file__).parent / "out"


DEFAULT_SETTLE_SECS = 30

MAX_QUOTE_AGE_SECS = 300
"""
How old the newest quote may be, measured from arrival, before the feed counts as stale.

Five minutes rather than five seconds because the check runs on a delayed feed an hour
before the open, when a quiet instrument may legitimately not tick for a while; what it
guards against is a feed that stopped, not one that is slow.

"""

MIN_QUOTES = 2
"""
Quotes an instrument must have delivered; *one isolated quote is not sufficient*.
"""

PREMARKET_LEAD = timedelta(hours=5, minutes=30)
"""
How long before the open US pre-market quoting begins (04:00 Eastern).

Used only to describe the market's state on the check's note, so a failed quote check at
03:00 Eastern reads as "no quotes expected" rather than as a silent feed.

"""

NANOS_PER_SECOND = 1_000_000_000


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


@dataclass(frozen=True)
class QuoteSample:
    """
    The newest quote seen for one instrument, and how many preceded it.
    """

    instrument_id: str
    bid: Decimal
    ask: Decimal
    ts_event: int
    ts_init: int
    count: int


class QuoteWatch(DataActor):
    """
    Subscribes to quotes for the session's instruments and keeps the newest of each.

    ``DataActor`` is a pyo3 class whose ``__new__`` accepts only the config, so the
    instruments are attached after construction by :meth:`configure`.

    """

    def configure(self, instrument_ids: tuple[str, ...]) -> None:
        """
        Attach the instruments to watch, after pyo3 construction.
        """
        self._instrument_ids = tuple(InstrumentId.from_str(i) for i in instrument_ids)
        self.samples: dict[str, QuoteSample | None] = dict.fromkeys(instrument_ids)

    def on_start(self) -> None:
        """
        Subscribe to quotes for every configured instrument.
        """
        for instrument_id in self._instrument_ids:
            self.subscribe_quotes(instrument_id)

    def on_quote(self, quote) -> None:  # noqa: ANN001 - QuoteTick from the engine
        """
        Keep the newest quote and count the ones before it.
        """
        key = str(quote.instrument_id)
        if key not in self.samples:
            return
        previous = self.samples[key]
        self.samples[key] = QuoteSample(
            instrument_id=key,
            bid=Decimal(str(quote.bid_price)),
            ask=Decimal(str(quote.ask_price)),
            ts_event=int(quote.ts_event),
            ts_init=int(quote.ts_init),
            count=(previous.count if previous else 0) + 1,
        )


def market_status(now: datetime) -> str:
    """
    Describe where the US session stands at ``now``, for the note on a quote check.
    """
    today = now.astimezone(EASTERN).date()
    if not is_trading_day(today):
        return "market closed: not a trading day"
    opens, closes = session_open(today), session_close(today)
    if now < opens - PREMARKET_LEAD:
        return "market closed: before pre-market"
    if now < opens:
        return "pre-market"
    if now < closes:
        return "session open"
    return "market closed: after the close"


def quote_checks(
    samples: Mapping[str, QuoteSample | None],
    *,
    now_ns: int,
    max_age_secs: int = MAX_QUOTE_AGE_SECS,
    min_quotes: int = MIN_QUOTES,
    note: str = "",
) -> list[Check]:
    """
    One check per instrument: enough quotes, the newest fresh, bid positive and below ask.

    Pure, so the rules can be tested without a feed. Age is measured from ``ts_init``,
    the instant the quote reached us, because a delayed feed carries no exchange time.

    """
    expected = f">={min_quotes} quotes, <={max_age_secs}s old, 0<bid<ask"
    checks: list[Check] = []
    for instrument_id, sample in samples.items():
        symbol = instrument_id.split("=")[0].split(".")[0]
        if sample is None:
            checks.append(
                Check(
                    name=f"quote_{symbol}",
                    passed=False,
                    observed="none",
                    expected=expected,
                    note=note,
                ),
            )
            continue
        age = Decimal(now_ns - sample.ts_init) / NANOS_PER_SECOND
        fresh = age <= max_age_secs
        enough = sample.count >= min_quotes
        uncrossed = 0 < sample.bid < sample.ask
        checks.append(
            Check(
                name=f"quote_{symbol}",
                passed=fresh and enough and uncrossed,
                observed=f"{sample.count} quotes, last {age:.1f}s ago, {sample.bid}/{sample.ask}",
                expected=expected,
                note=note,
            ),
        )
    return checks


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
    watch = QuoteWatch(DataActorConfig())
    watch.configure(session.instrument_ids)
    node, risk_engine = build_paper_node(
        session,
        market_data_type=market_data_type,
        actors=(watch,),
    )

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
        checks.extend(
            quote_checks(
                watch.samples,
                now_ns=time.time_ns(),
                note=(
                    f"{market_status(datetime.now(UTC))}; "
                    f"{str(market_data_type).rsplit('.', 1)[-1]} feed, age measured from arrival"
                ),
            ),
        )
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

    # The account does not live on the instrument's venue. Instruments resolve on
    # `SMART`, while the execution client registers the account under its own client
    # name, so the account id reads `IB-DUT067974`. Searching only the instrument venues
    # finds nothing, which is how the first run reported a missing account that was
    # sitting in the cache the whole time.
    venues = tuple(sorted({i.id.venue for i in instruments} | {Venue(EXEC_CLIENT_VENUE)}, key=str))
    found = find_account(cache, venues)

    # An account id we supplied and printed back proves nothing. This is the only check
    # that can tell us whether the IB paper *login* is the same string as the account id.
    account_id = found[1] if found else ""
    reported = account_id.split("-", 1)[-1] if account_id else ""
    account = cache.account_for_venue(found[0]) if found else None
    balances = account.balances() if account is not None else {}

    checks.append(
        check(
            "account_reported_by_broker",
            observed=reported,
            expected=session.account_id,
            note=(
                f"venues searched: {[str(v) for v in venues]}; found: {account_id or None}"
                + ("" if found else NO_ACCOUNT_HINT)
            ),
        ),
    )
    checks.append(
        Check(
            name="account_has_balances",
            passed=bool(balances),
            observed=str(len(balances)),
            expected=">0",
            note=(
                f"account_type={getattr(account, 'account_type', None)}. "
                "A paper account may not carry the live account's type - see "
                "docs/PAPER_CAMPAIGN.md on what paper cannot reproduce."
            ),
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
    add_broker_arguments(parser, data_client_id=801, exec_client_id=802)
    parser.add_argument("--settle-secs", type=int, default=DEFAULT_SETTLE_SECS)
    parser.add_argument(
        "--instruments",
        default=",".join(registered_instruments()),
        help="Broker ids to resolve (default: every registered activation's)",
    )
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

    print(f"\n{'check':<32}{'result':<8}{'observed':<40}expected")
    for c in checks:
        print(f"{c.name:<32}{'PASS' if c.passed else 'FAIL':<8}{c.observed[:39]:<40}{c.expected}")
    quotes = [c for c in checks if c.name.startswith("quote_")]
    if quotes:
        print(f"\nquotes: {quotes[0].note}")

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
