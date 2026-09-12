"""
The pinned stressed gap allowance: how far past its stop one share can be carried.

The playbook sizes with ``q = floor(R / (|P - S| + g))``, and this module is where ``g``
comes from. It is pinned the way the spread snapshot is pinned ([ADR-0011]): a number in a
committed file, measured once, that cannot move without a diff. A per-run measurement
would let the sizing basis drift with the catalog, and every filed R would then be in a
slightly different unit.

Why sizing needs it at all
--------------------------
A stop is not a loss guarantee. Measured on the replay, a stop breached inside a session
fills at its trigger and costs exactly the stop distance; a session that **gaps through**
the stop fills at the open and costs the gap. Both strategies here exit on completed daily
bars, which is the case `RISK.md` names explicitly: when exits are next-session, ``g`` must
come from a tested next-session stress loss rather than from an assumption that the stop
bounds the loss.

Until 2026-09-12 `size_from_levels` divided by the stop distance alone, so every verdict
filed before then sized as though a gap could not happen and understated planned risk by
exactly the risk this table describes.

How these numbers were produced
-------------------------------
:mod:`copilot.calibration.gap_history`, over **development bars only** - each symbol carved
at its activations' earliest holdout boundary, because an allowance fitted to holdout bars
is a sizing basis that read the single-use test ([ADR-0033]). The measured quantity is the
adverse opening gap in units of the ATR standing at the previous close, and the pinned
figure is its nearest-rank 99th percentile rounded up to a hundredth.

They are expressed in **ATR units**, not dollars, because the stop is: a stop at
``stop_atr`` times the ATR and an allowance at ``gap_atr`` times the same ATR compose
without either depending on the price level.

The registry declares the value, this table is the source
---------------------------------------------------------
Each activation carries ``gap_atr`` in its ``[parameters]``, because activation is data
([ADR-0005]) and a sizing input belongs in the reviewable file beside the risk budget. This
table is what those declarations must equal, and ``test_gap_stress.py`` fails if one drifts.
That split is deliberate: the declaration is what runs, so it is visible in the diff that
changes it, and the measurement is what is *true*, so it cannot be edited quietly to suit a
result.

[ADR-0005]: ../docs/decisions/0005-setup-is-code-activation-is-data.md
[ADR-0011]: ../docs/decisions/0011-spread-is-charged-at-p95-from-a-pinned-snapshot.md
[ADR-0033]: ../docs/decisions/0033-the-turn-of-month-single-use-test-is-forward.md

"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Mapping


MEASURED_FROM = "gap_stress_20260912T014515Z.json"
"""
The filed measurement these figures come from, under ``calibration/out/``.

Named rather than recomputed, for the same reason the spread snapshot is named: a number
that cannot be tied to the evidence that produced it cannot be audited.

"""

PERCENTILE = "p99"
"""
The percentile pinned, stated here so a reader need not open the evidence file.
"""

GAP_ATR: Mapping[str, Decimal] = {
    "AAPL": Decimal("1.34"),
    "EEM": Decimal("1.89"),
    "GLDM": Decimal("1.63"),
    "HYG": Decimal("1.68"),
    "MSFT": Decimal("1.11"),
    "SCHX": Decimal("1.50"),
    "SPY": Decimal("1.23"),
    "TLT": Decimal("1.49"),
    "XLF": Decimal("1.07"),
}
"""
Stressed adverse gap per symbol, as a multiple of the ATR at the previous close.

The spread is wide and it is informative: XLF gaps least at 1.07 ATR and EEM most at 1.89,
which is the difference between a domestic sector fund and an emerging-market fund that
prices a night of foreign trading at its open. A single global allowance would have been
too small for one and too large for the other, which is why this is per symbol.

Every one of these is **larger than a typical day's whole range is wide**, and all but two
exceed the 1.5-ATR stop the registry uses. That is the finding, not a modelling artefact: a
position whose stop sits 1.5 ATR away can lose roughly twice that overnight, so charging
only the stop distance understated planned risk by more than half.

"""


class UnmeasuredSymbolError(KeyError):
    """
    No gap allowance has been measured for a requested symbol.
    """

    def __init__(self, symbol: str) -> None:
        """
        Name the symbol and where a measurement would come from.
        """
        super().__init__(
            f"no stressed gap allowance measured for {symbol!r}; measured: "
            f"{sorted(GAP_ATR)}. Run `python -m copilot.calibration.gap_history --write` "
            f"over a catalog holding it, then pin the figure here. Sizing without an "
            f"allowance would charge only the stop distance, which understates the risk "
            f"of a gap through the stop.",
        )


def allowance_atr(symbol: str) -> Decimal:
    """
    Return the pinned allowance for one symbol, in ATR units, refusing to guess.
    """
    try:
        return GAP_ATR[symbol]
    except KeyError:
        raise UnmeasuredSymbolError(symbol) from None


def allowance_for(symbol: str, atr: Decimal) -> Decimal:
    """
    Return the per-share allowance in price terms: the pinned multiple times ``atr``.

    Zero when the ATR is not positive, which is the unwarmed case a caller already has to
    refuse; an allowance cannot rescue a signal that has no volatility estimate.

    """
    if atr <= 0:
        return Decimal(0)
    return atr * allowance_atr(symbol)


__all__ = [
    "GAP_ATR",
    "MEASURED_FROM",
    "PERCENTILE",
    "UnmeasuredSymbolError",
    "allowance_atr",
    "allowance_for",
]
