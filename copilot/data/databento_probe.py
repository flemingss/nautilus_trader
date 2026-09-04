"""
Measure whether a Databento one-minute series is a real one, before buying it in bulk.

Split from ``databento.py`` on 2026-09-04. The client and its wire format stay there
because every caller needs them; this is the question asked once per vendor and dataset
- the intraday-fidelity probe that caught a repeated-day payload in its first 200 rows.
See ``docs/decisions/0015`` for why it exists.

"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from itertools import pairwise

from copilot.data.databento import DatabentoClient
from copilot.data.databento import DatabentoError
from copilot.data.databento import Minute


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


def _probe(client: DatabentoClient, symbol: str, start: str, end: str) -> int:
    """
    Measure whether a feed's one-minute rows are bars at all.
    """
    minutes = client.fetch_minutes(symbol, start, end)
    finding = measure(symbol, client.dataset, minutes)
    print()
    print(finding.report())
    if not finding.looks_like_real_bars:
        return 1

    return 0


__all__ = [
    "ProbeFinding",
    "measure",
]
