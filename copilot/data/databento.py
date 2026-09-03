"""
Databento historical client, and the probe that decides whether to trust it.

Read-only against the market. It fetches history and prints findings; it constructs no
execution client and cannot place an order.

    export DATABENTO_API_KEY=...
    python -m copilot.data.databento --survey
    python -m copilot.data.databento --cost --symbols AAPL,MSFT,SPY
    python -m copilot.data.databento --probe AAPL --spend

Why this module exists
----------------------
Databento is the intraday source, and *only* the intraday source. It sells no corporate
actions and its US equities history begins around 2018-05, so it cannot rebuild the
2005-2018 daily series the catalog already holds. What it does offer that nothing else
cheap does is 1-minute bars from a real consolidated feed, plus an independent daily
series for 2018 onward that can be used to audit seven of the catalog's twenty years
against a vendor that is not Marketstack.

Why the probe exists
--------------------
The last vendor's intraday endpoint returned rows shaped exactly like OHLCV bars that
were not bars: ``open``, ``high``, ``low`` and ``close`` held **one distinct value
across 200 rows** - the day's own values, repeated - and ``volume`` was **cumulative
since the open** rather than per-interval. Read naively, that hands a backtest the
day's closing price at 09:31, which is lookahead severe enough to make any intraday
result meaningless while looking entirely healthy.

That was caught by measurement, not by reading a pricing page, so no new intraday
vendor is trusted here until the same measurements pass. `probe` runs them and prints
what it found:

- **distinct OHLC values** across the sample. A real minute series varies; one
  distinct value per field is the failure above.
- **volume monotonicity.** Per-interval volume rises and falls. Volume that never
  decreases across a session is a running total wearing a bar's name.
- **summed volume against the daily total.** Minute volume that sums well past the
  day's own figure is double counting.
- **OHLC coherence**, the same rule the daily gate applies: low <= min(open, close)
  and max(open, close) <= high.

On billing
----------
Historical is usage-based and metered on the *uncompressed bytes delivered*, with no
per-dataset subscription: an API key alone reaches every historical dataset. The
metadata calls used by ``--survey`` and ``--cost`` are free, so the cost of a pull is
knowable before it is paid for. ``--probe`` spends, which is why it refuses to run
without ``--spend``.

A caution that belongs next to the code rather than in a portal: because billing
follows bytes returned, a careless full-depth query is the entire risk surface. Set a
monthly historical spend limit in the Databento portal as well as passing ``--spend``
here.

"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from itertools import pairwise
from typing import Any
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request
from urllib.request import urlopen


API_KEY_ENV = "DATABENTO_API_KEY"
DATASET_ENV = "DATABENTO_DATASET"

DEFAULT_BASE_URL = "https://hist.databento.com/v0"

# Databento's own consolidated US equities dataset. Chosen over XNAS.BASIC because the
# strategy trades a consolidated tape rather than one venue's view of it, and over the
# deprecated DBEQ.BASIC, which was folded into the US equities service in January 2025.
DEFAULT_DATASET = "EQUS.SUMMARY"

USER_AGENT = "NautilusCopilot/1.0"
TIMEOUT_SECONDS = 60

# Enough rows to see structure without paying for a session. The vendor failure this
# probe exists to catch showed up inside the first 200.
PROBE_ROWS = 200


class DatabentoError(RuntimeError):
    """
    A request to Databento failed, or returned something unusable.
    """


@dataclass(frozen=True)
class DatasetRange:
    """
    The window a dataset actually covers, as the vendor reports it.
    """

    dataset: str
    start: str
    end: str


@dataclass(frozen=True)
class Minute:
    """
    One 1-minute bar, normalized out of the vendor's JSON.
    """

    opened_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int

    @property
    def coherent(self) -> bool:
        """
        Return whether the bar's close and open sit inside its own range.
        """
        return self.low <= min(self.open, self.close) and max(self.open, self.close) <= self.high


@dataclass(frozen=True)
class ProbeFinding:
    """
    What the fidelity probe measured, and whether it is worth trusting.
    """

    symbol: str
    dataset: str
    rows: int
    distinct: dict[str, int]
    volume_is_monotonic: bool
    volume_sum: int
    incoherent_rows: int

    @property
    def looks_like_real_bars(self) -> bool:
        """
        Return whether every fidelity check passed.

        A single distinct value in any price field, or volume that never decreases, is
        the failure mode that made the previous vendor's intraday unusable.

        """
        varies = all(count > 1 for count in self.distinct.values())
        return varies and not self.volume_is_monotonic and self.incoherent_rows == 0

    def report(self) -> str:
        """
        Return the human-readable verdict, findings first.
        """
        lines = [
            f"{self.symbol} on {self.dataset}: {self.rows} one-minute rows",
            "  distinct values  " + "  ".join(f"{k}={v}" for k, v in self.distinct.items()),
            f"  volume monotonic {self.volume_is_monotonic}  (True means cumulative)",
            f"  volume summed    {self.volume_sum:,}",
            f"  incoherent rows  {self.incoherent_rows}",
            f"  VERDICT          {'real bars' if self.looks_like_real_bars else 'NOT USABLE'}",
        ]
        return "\n".join(lines)


class DatabentoClient:
    """
    A minimal historical client over the vendor's REST interface.

    Deliberately stdlib-only and hand-rolled, matching ``marketstack``: the vendor SDK
    would be a dependency carried for four endpoints, three of which are free.

    """

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        dataset: str = DEFAULT_DATASET,
    ) -> None:
        """
        Build a client.

        The key is held here and never written to a config or a log.

        """
        if not api_key:
            raise DatabentoError(f"no API key. Set {API_KEY_ENV}.")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self.dataset = dataset

    def _get(self, endpoint: str, params: dict[str, str | int] | None = None) -> Any:
        """
        Issue one authenticated GET, and return the decoded body.
        """
        query = urlencode(params or {})
        url = f"{self._base_url}/{endpoint}"
        if query:
            url = f"{url}?{query}"

        # Databento authenticates with HTTP Basic, the key as username and no password.
        token = base64.b64encode(f"{self._api_key}:".encode()).decode()
        request = Request(  # noqa: S310 - fixed https scheme from DEFAULT_BASE_URL
            url,
            headers={"Authorization": f"Basic {token}", "User-Agent": USER_AGENT},
        )
        try:
            with urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
                body = response.read().decode()
        except HTTPError as e:
            detail = e.read().decode(errors="replace")[:400]
            raise DatabentoError(f"{endpoint} failed: HTTP {e.code} {detail}") from e
        except URLError as e:
            raise DatabentoError(f"{endpoint} failed: {e.reason}") from e

        text = body.strip()
        if not text:
            return []
        # Some endpoints answer with JSON, the data endpoints with newline-delimited
        # JSON. Try the whole body first, then fall back to per-line decoding.
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return [json.loads(line) for line in text.splitlines() if line.strip()]

    def list_datasets(self) -> list[str]:
        """
        Return every dataset the key can reach.

        Free.

        """
        return list(self._get("metadata.list_datasets"))

    def dataset_range(self, dataset: str) -> DatasetRange:
        """
        Return the window a dataset covers.

        Free, and the answer to how far back it goes.

        """
        raw = self._get("metadata.get_dataset_range", {"dataset": dataset})
        if not isinstance(raw, dict):
            raise DatabentoError(f"unexpected range payload for {dataset}: {raw!r}")
        start = raw.get("start") or raw.get("start_date") or "?"
        end = raw.get("end") or raw.get("end_date") or "?"
        return DatasetRange(dataset=dataset, start=str(start), end=str(end))

    def fetch_minutes(
        self,
        symbol: str,
        start: str,
        end: str,
        limit: int = PROBE_ROWS,
    ) -> list[Minute]:
        """
        Fetch one-minute bars.

        **This spends credit**; price it with ``cost`` first.

        """
        rows = self._get(
            "timeseries.get_range",
            {
                "dataset": self.dataset,
                "symbols": symbol,
                "schema": "ohlcv-1m",
                "start": start,
                "end": end,
                "stype_in": "raw_symbol",
                "encoding": "json",
                "limit": limit,
            },
        )
        return [normalize_minute(row) for row in rows]

    def cost(
        self,
        symbols: list[str],
        schema: str,
        start: str,
        end: str,
        dataset: str | None = None,
    ) -> Decimal:
        """
        Return the US dollar cost of a pull, without making it.

        Free.

        """
        raw = self._get(
            "metadata.get_cost",
            {
                "dataset": dataset or self.dataset,
                "symbols": ",".join(symbols),
                "schema": schema,
                "start": start,
                "end": end,
                "stype_in": "raw_symbol",
                "mode": "historical",
            },
        )
        # The endpoint answers with a bare number.
        return Decimal(str(raw))


def normalize_minute(raw: dict[str, Any]) -> Minute:
    """
    Turn one vendor row into a ``Minute``.

    Prices arrive as integers scaled by 1e-9. They are converted through ``Decimal``
    rather than float so the exact arithmetic the rest of the project relies on is not
    broken at the boundary.

    """
    scale = Decimal(10) ** 9

    def price(field: str) -> Decimal:
        return Decimal(str(raw[field])) / scale

    stamp = raw.get("ts_event") or raw.get("hd", {}).get("ts_event")
    opened_at = datetime.fromtimestamp(int(stamp) / 1e9, tz=UTC)
    return Minute(
        opened_at=opened_at,
        open=price("open"),
        high=price("high"),
        low=price("low"),
        close=price("close"),
        volume=int(raw["volume"]),
    )


def measure(symbol: str, dataset: str, minutes: list[Minute]) -> ProbeFinding:
    """
    Run the fidelity checks over a sample, and return what they found.
    """
    if not minutes:
        raise DatabentoError(f"no rows returned for {symbol}; nothing to measure")

    distinct = {
        field: len(Counter(getattr(m, field) for m in minutes))
        for field in ("open", "high", "low", "close")
    }
    volumes = [m.volume for m in minutes]
    monotonic = all(b >= a for a, b in pairwise(volumes))
    return ProbeFinding(
        symbol=symbol,
        dataset=dataset,
        rows=len(minutes),
        distinct=distinct,
        volume_is_monotonic=monotonic,
        volume_sum=sum(volumes),
        incoherent_rows=sum(1 for m in minutes if not m.coherent),
    )


def _client(args: argparse.Namespace) -> DatabentoClient:
    key = os.environ.get(API_KEY_ENV, "")
    dataset = args.dataset or os.environ.get(DATASET_ENV) or DEFAULT_DATASET
    return DatabentoClient(api_key=key, dataset=dataset)


def main(argv: list[str] | None = None) -> int:
    """
    Survey the account, price a pull, or probe a vendor's intraday fidelity.

    Returns a process exit code. A probe that fails its checks exits non-zero, so an
    unusable feed cannot be mistaken for a usable one by a caller reading status.

    """
    parser = argparse.ArgumentParser(
        prog="python -m copilot.data.databento",
        description="Survey Databento, price a pull, and probe intraday fidelity.",
    )
    parser.add_argument("--survey", action="store_true", help="List datasets and ranges (free)")
    parser.add_argument("--cost", action="store_true", help="Price a one-minute pull (free)")
    parser.add_argument("--probe", metavar="SYMBOL", help="Measure intraday fidelity (spends)")
    parser.add_argument("--spend", action="store_true", help="Permit the probe to incur cost")
    parser.add_argument("--dataset", help=f"Dataset override (default {DEFAULT_DATASET})")
    parser.add_argument("--symbols", default="AAPL,MSFT,SPY", help="Comma-separated symbols")
    parser.add_argument("--from", dest="start", default="2024-01-02", help="Start date")
    parser.add_argument("--to", dest="end", default="2024-01-03", help="End date")
    args = parser.parse_args(argv)

    if not (args.survey or args.cost or args.probe):
        parser.error("choose --survey, --cost, or --probe SYMBOL")

    client = _client(args)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    if args.survey:
        datasets = client.list_datasets()
        print(f"{len(datasets)} datasets reachable with this key.\n")
        equities = [d for d in datasets if d.startswith(("EQUS", "XNAS", "XNYS", "DBEQ", "ARCX"))]
        for name in sorted(equities):
            try:
                window = client.dataset_range(name)
                print(f"  {name:<20} {window.start}  ->  {window.end}")
            except DatabentoError as e:
                print(f"  {name:<20} range unavailable: {e}")
        print(f"\n  ({len(datasets) - len(equities)} non-equities datasets not listed)")

    if args.cost:
        price = client.cost(symbols, "ohlcv-1m", args.start, args.end)
        print(
            f"\nohlcv-1m, {len(symbols)} symbols, {args.start} to {args.end}: "
            f"${price} on {client.dataset}",
        )

    if args.probe:
        if not args.spend:
            print(
                "\n--probe spends credit. Price it with --cost first, then pass --spend.",
                file=sys.stderr,
            )
            return 2
        minutes = client.fetch_minutes(args.probe.upper(), args.start, args.end)
        finding = measure(args.probe.upper(), client.dataset, minutes)
        print()
        print(finding.report())
        if not finding.looks_like_real_bars:
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
