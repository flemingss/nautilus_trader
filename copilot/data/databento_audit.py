"""
Check the catalog's daily closes against Databento's, official print by official print.

Split from ``databento.py`` on 2026-09-04. The audit is the instrument that found nine
unadjusted corporate actions and one fabricated price for $0.09 of metered data
([ADR-0015]); it reads the catalog, which the client module deliberately does not.

[ADR-0015]: ../docs/decisions/0015-databento-is-the-intraday-source-only.md

"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from copilot.data.databento import DAILY_DATASET
from copilot.data.databento import LISTING_DATASETS
from copilot.data.databento import DatabentoClient
from copilot.data.databento import catalog_closes
from copilot.data.databento import catalog_series


@dataclass(frozen=True)
class SymbolAudit:
    """
    One symbol's catalog checked against an independent daily series.
    """

    symbol: str
    days_compared: int
    exact_closes: int
    max_deviation_bps: Decimal
    worst_day: str
    median_volume_ratio: Decimal

    @property
    def clean(self) -> bool:
        """
        Return whether every overlapping close matched to the cent.
        """
        return self.days_compared > 0 and self.exact_closes == self.days_compared

    def row(self) -> str:
        """
        Return one fixed-width line for the audit table.
        """
        share = f"{self.exact_closes}/{self.days_compared}"
        return (
            f"  {self.symbol:<7}{share:>10}{self.max_deviation_bps:>12}"
            f"{self.worst_day:>13}{self.median_volume_ratio:>10}  "
            f"{'ok' if self.clean else 'MISMATCH'}"
        )


def audit_symbol(
    symbol: str,
    vendor: dict[date, tuple[Decimal, int]],
    catalog: dict[date, tuple[Decimal, int]],
) -> SymbolAudit:
    """
    Compare one symbol's closes and volumes over the days both sources carry.

    Only overlapping days are compared, so a shorter vendor window narrows the audit
    rather than reporting the missing days as failures. The catalog holds vendor prices
    that are already split-adjusted while Databento reports them as traded, so a split
    inside the window would surface here as a large deviation - which is a finding, not
    a bug in this function.

    """
    shared = sorted(set(vendor) & set(catalog))
    exact = 0
    worst = Decimal(0)
    worst_day = "-"
    ratios = []
    for day in shared:
        vendor_close, vendor_volume = vendor[day]
        catalog_close, catalog_volume = catalog[day]
        if vendor_close == catalog_close:
            exact += 1
        deviation = abs(catalog_close - vendor_close) / catalog_close * 10000
        if deviation > worst:
            worst, worst_day = deviation, day.isoformat()
        if catalog_volume:
            ratios.append(Decimal(vendor_volume) / Decimal(catalog_volume))

    ratios.sort()
    median = ratios[len(ratios) // 2] if ratios else Decimal(0)
    return SymbolAudit(
        symbol=symbol,
        days_compared=len(shared),
        exact_closes=exact,
        max_deviation_bps=worst.quantize(Decimal("0.01")),
        worst_day=worst_day,
        median_volume_ratio=median.quantize(Decimal("0.001")),
    )


def _audit_deep(client: DatabentoClient, catalog_path: str, start: str, end: str) -> int:
    """
    Check the catalog against each listing venue's official closing auction print.

    Routed per venue because the auction that sets a security's official close is run by
    the venue it is listed on, and those datasets reach 2018-05-01 rather than the
    consolidated summary's 2024-07-01.

    """
    held = catalog_series(catalog_path)
    by_venue: dict[str, list[str]] = {}
    for symbol, (venue, _) in held.items():
        by_venue.setdefault(venue, []).append(symbol)

    audits = []
    for venue, symbols in sorted(by_venue.items()):
        dataset = LISTING_DATASETS.get(venue)
        if dataset is None:
            print(f"  no listing dataset for venue {venue}; skipped {sorted(symbols)}")
            continue
        official = client.fetch_official_closes(sorted(symbols), dataset, start, end)
        for symbol in sorted(symbols):
            series = {day: (price, 0) for day, price in official.get(symbol, {}).items()}
            audits.append(audit_symbol(symbol, series, held[symbol][1]))

    print(f"\nCatalog audited against official closing auction prints, {start} to {end}")
    print(f"  {'symbol':<7}{'exact':>12}{'worst bps':>12}{'worst day':>13}")
    for entry in sorted(audits, key=lambda a: a.symbol):
        share = f"{entry.exact_closes}/{entry.days_compared}"
        flag = "ok" if entry.clean else "MISMATCH"
        print(
            f"  {entry.symbol:<7}{share:>12}{entry.max_deviation_bps:>12}"
            f"{entry.worst_day:>13}  {flag}",
        )
    clean = sum(1 for a in audits if a.clean)
    compared = sum(a.days_compared for a in audits)
    disagreed = sum(a.days_compared - a.exact_closes for a in audits)
    print(f"\n  {clean}/{len(audits)} symbols exact across {compared:,} days")
    print(f"  {disagreed} disagreements ({disagreed / compared * 100:.3f}%)")
    return 0


def _audit(client: DatabentoClient, catalog_path: str, start: str, end: str) -> int:
    """
    Check the catalog's closes against an independent daily series.
    """
    held = catalog_closes(catalog_path)
    vendor = client.fetch_daily(sorted(held), start, end, DAILY_DATASET)
    audits = [audit_symbol(s, vendor.get(s, {}), held[s]) for s in sorted(held)]
    print(f"\nCatalog audited against {DAILY_DATASET}, {start} to {end}")
    print(
        f"  {'symbol':<7}{'exact':>10}{'worst bps':>12}{'worst day':>13}{'vol ratio':>10}",
    )
    for entry in audits:
        print(entry.row())
    clean = sum(1 for a in audits if a.clean)
    compared = sum(a.days_compared for a in audits)
    print(f"\n  {clean}/{len(audits)} symbols matched exactly across {compared} days")
    if clean != len(audits):
        return 1

    return 0


__all__ = [
    "SymbolAudit",
    "audit_symbol",
]
