"""
Backfill a Nautilus catalog from Marketstack, and report what was rejected.

Read-only against the market: it fetches history and writes files. It constructs no
execution client and cannot place an order.

    export MARKETSTACK_API_KEY=...
    python -m copilot.data.backfill --symbols AAPL,MSFT,SPY --from 2015-01-01

The rejection report is the part worth reading. A run that quietly discarded rows
would still print a success line, so every gate failure is counted by reason and the
run fails outright when the rejection rate crosses ``--max-rejection-ratio``. That
threshold exists because a provider outage tends to look like partially-valid data
rather than an error, and a half-ingested history is worse than none: later runs treat
whatever landed as complete.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from datetime import UTC, date, datetime
from decimal import Decimal

from copilot.data.catalog import (
    DEFAULT_CURRENCY,
    open_catalog,
    venues_from_rows,
    write_ingestion,
)
from copilot.data.marketstack import MarketstackClient, normalize

API_KEY_ENV = "MARKETSTACK_API_KEY"
CATALOG_PATH_ENV = "COPILOT_CATALOG_PATH"
DEFAULT_CATALOG = "~/.nautilus_copilot/catalog"
"""Outside the repository on purpose. A parquet store inside the tree would need a
`.gitignore` entry, and `.gitignore` is an upstream file this fork does not touch —
so the data would sit one `git add -A` away from being committed."""

DEFAULT_MAX_REJECTION_RATIO = "0.02"
"""Two percent. The two phantom holiday rows in 21 years of AAPL, MSFT and SPY are
0.013%, so a real run sits two orders of magnitude below this and anything near the
threshold means something changed at the provider."""


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m copilot.data.backfill",
        description="Fetch Marketstack EOD history into a Nautilus ParquetDataCatalog.",
    )
    parser.add_argument("--symbols", required=True, help="Comma-separated, e.g. AAPL,MSFT,SPY")
    parser.add_argument("--from", dest="start", required=True, help="Start date, YYYY-MM-DD")
    parser.add_argument(
        "--to",
        dest="end",
        default=date.today().isoformat(),  # noqa: DTZ011 - a local calendar day is the intent
        help="End date, YYYY-MM-DD (default: today)",
    )
    parser.add_argument(
        "--catalog",
        default=os.environ.get(CATALOG_PATH_ENV, DEFAULT_CATALOG),
        help=f"Catalog directory (default: ${CATALOG_PATH_ENV} or {DEFAULT_CATALOG})",
    )
    parser.add_argument(
        "--currency",
        default=DEFAULT_CURRENCY,
        help="Quote currency for the instruments written (the vendor's tag is unreliable)",
    )
    parser.add_argument(
        "--max-rejection-ratio",
        type=Decimal,
        default=Decimal(DEFAULT_MAX_REJECTION_RATIO),
        help="Fail the run if more than this share of fetched rows fail a gate",
    )
    parser.add_argument(
        "--allow-non-trading-days",
        action="store_true",
        help="Keep bars dated on days the US market was closed (non-US venues only)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and gate, report, but write nothing",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run one backfill. Returns a process exit code."""
    args = _parse_args(argv)

    access_key = os.environ.get(API_KEY_ENV)
    if not access_key:
        print(f"error: {API_KEY_ENV} is not set", file=sys.stderr)
        return 2

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if end < start:
        print(f"error: --to {end} precedes --from {start}", file=sys.stderr)
        return 2

    client = MarketstackClient(access_key)
    rows = client.fetch_eod(symbols, start, end)
    result = normalize(
        rows,
        received_at=datetime.now(tz=UTC),
        require_trading_day=not args.allow_non_trading_days,
    )

    _report(result, symbols, start, end)

    if result.rejection_ratio > args.max_rejection_ratio:
        print(
            f"\nFAILED: rejection ratio {result.rejection_ratio:.4f} exceeds "
            f"{args.max_rejection_ratio}. Nothing written.",
            file=sys.stderr,
        )
        return 1

    if args.dry_run:
        print("\nDry run: nothing written.")
        return 0

    if not result.bars:
        print("\nNothing to write.")
        return 0

    catalog = open_catalog(args.catalog)
    reports = write_ingestion(
        catalog,
        result,
        venues=venues_from_rows([dict(row) for row in rows]),
        currency=args.currency,
    )
    print(f"\nWrote to {args.catalog}:")
    for report in reports:
        span = f"{report.first:%Y-%m-%d} .. {report.last:%Y-%m-%d}" if report.first else "-"
        print(f"  {report.bar_type:<40} {report.bars_written:>6} bars  {span}")
    return 0


def _report(result, symbols: list[str], start: date, end: date) -> None:  # noqa: ANN001
    print(f"Marketstack EOD  {','.join(symbols)}  {start} .. {end}")
    print(f"  fetched:  {result.fetched}")
    print(f"  accepted: {len(result.bars)}")
    print(f"  rejected: {len(result.rejected)}  ({result.rejection_ratio:.4%})")

    for reason, count in Counter(r.reason.split(":")[0] for r in result.rejected).most_common():
        print(f"      {reason:<28} {count}")
        for example in [r for r in result.rejected if r.reason.startswith(reason)][:3]:
            when = example.closed_at.date() if example.closed_at else "?"
            print(f"        e.g. {example.symbol} {when}")

    # Splits are listed individually and dividends only counted. A split re-bases the
    # vendor's whole history for that symbol, so an already-written catalog range
    # disagrees with a fresh fetch from that point on and someone has to act on it. A
    # dividend changes nothing in the series being stored, so the count is enough.
    splits = [a for a in result.corporate_actions if a.split_factor != 1]
    dividends = [a for a in result.corporate_actions if a.dividend_amount != 0]
    if splits:
        print(f"  splits: {len(splits)} - stored history for these symbols is re-based")
        for action in splits:
            print(f"      {action.symbol} {action.closed_at:%Y-%m-%d}  {action.split_factor}:1")
    if dividends:
        by_symbol = Counter(a.symbol for a in dividends)
        print(f"  dividends: {len(dividends)} ({dict(by_symbol)})")
        print("        not adjusted for; see the price-set note in marketstack.py")

    odd = {s: t for s, t in (result.currency_tags or {}).items() if len(t) > 1}
    if odd:
        print(f"  note: vendor reported inconsistent price_currency tags: {odd}")
        print("        tags are advisory only; --currency decides what is stored")


if __name__ == "__main__":
    raise SystemExit(main())
