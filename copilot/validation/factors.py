"""
Daily factor returns, read from a pinned copy of the Kenneth French data library.

Attribution asks whether a premise's return is anything but exposure to things that are
free to own: the market, and the size, value and momentum premia. Answering that needs
those returns for every session a trade was open, and the French library publishes them
daily from 1926, free, built from CRSP.

Pinned, not fetched
-------------------
**The files in** ``factors/`` **are committed and checked by digest on every read.** The
library is rebuilt from each new CRSP vintage and past values move when it is: a verdict
that fetched on read would change under a commit that changed nothing, which is the
failure [ADR-0011](../docs/decisions/0011-spread-is-charged-at-p95-from-a-pinned-snapshot.md)
pinned the spread snapshot to prevent. The digests below are the pin; replacing a file
without changing them is refused, and changing them is a reviewable commit like any other
change to a number a verdict depends on.

``python -m copilot.validation.factors --fetch`` downloads the current files into the
directory and prints their digests. It does not touch the pin.

Units
-----
The files are in percent to two places. :func:`load_factors` returns plain fractions as
``Decimal``, so ``0.09`` in the file is ``0.0009`` here. ``-99.99`` is the library's
missing-value sentinel and is refused rather than read as a 99.99% loss.

"""

from __future__ import annotations

import argparse
import hashlib
import io
import sys
import zipfile
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.request import urlopen


FACTOR_DIR = Path(__file__).parent / "factors"

SOURCE_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"

RESEARCH_FILE = "F-F_Research_Data_Factors_daily_CSV.zip"
"""
Market excess return, SMB, HML and the risk-free rate, daily.
"""

MOMENTUM_FILE = "F-F_Momentum_Factor_daily_CSV.zip"

PINNED = {
    RESEARCH_FILE: "1916d331c2c51d2aee3d00215897d2b8e5995cb387f1f4569ba46bff5fb049a8",
    MOMENTUM_FILE: "b039d986db27fc831bcf1f52ba2134916e96fbeba4762fefc942429b0a568a96",
}
"""
SHA-256 of each file as committed: the 202607 CRSP vintage, fetched 2026-09-10, covering
1926-07-01 to 2026-07-31.
"""

MISSING = Decimal("-99.99")

_PERCENT = Decimal(100)

_DATE_DIGITS = 8
"""
A data row starts ``YYYYMMDD``; anything else in the file is prose or a column header.
"""


class UnpinnedFactorFileError(ValueError):
    """
    A factor file does not match its pinned digest.
    """


@dataclass(frozen=True)
class FactorReturns:
    """
    One session's factor returns, as fractions.
    """

    market_excess: Decimal
    """
    The value-weighted US market less the one-month T-bill.
    """
    size: Decimal
    """
    SMB: small minus big.
    """
    value: Decimal
    """
    HML: high book-to-market minus low.
    """
    momentum: Decimal
    """
    Past winners minus past losers.
    """
    risk_free: Decimal


def _verified(path: Path) -> bytes:
    """
    Return a file's bytes, or refuse if they are not the pinned ones.
    """
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    expected = PINNED[path.name]
    if digest != expected:
        raise UnpinnedFactorFileError(
            f"{path.name} has digest {digest}, and the pin is {expected}. A new vintage "
            "moves past factor returns, so replacing the file is a change to every "
            "attribution result: update PINNED in the same commit, deliberately.",
        )
    return content


def parse_daily(content: bytes) -> dict[date, tuple[Decimal, ...]]:
    """
    Read every ``YYYYMMDD, value, ...`` row from a French library zip, as fractions.

    Everything else in the file - the header prose, the column line, the copyright - is
    skipped by shape rather than by line number, because the prose length differs between
    files and between vintages.

    """
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        (name,) = archive.namelist()
        text = archive.read(name).decode("latin-1")

    rows: dict[date, tuple[Decimal, ...]] = {}
    for line in text.splitlines():
        fields = [field.strip() for field in line.split(",")]
        if len(fields) == 1 or len(fields[0]) != _DATE_DIGITS or not fields[0].isdigit():
            continue
        day = date(int(fields[0][:4]), int(fields[0][4:6]), int(fields[0][6:]))
        values = tuple(Decimal(field) for field in fields[1:])
        if MISSING in values:
            raise ValueError(
                f"{name} marks {day} as missing ({MISSING}); it cannot be read as a return",
            )
        rows[day] = tuple(value / _PERCENT for value in values)
    return rows


def load_factors(directory: Path = FACTOR_DIR) -> dict[date, FactorReturns]:
    """
    Return the pinned daily factor returns, keyed by session date.

    Only sessions present in both files are returned. They agree on the calendar today;
    taking the intersection means a future vintage that disagrees drops a day rather
    than silently pairing one file's Tuesday with the other's Wednesday.

    """
    research = parse_daily(_verified(directory / RESEARCH_FILE))
    momentum = parse_daily(_verified(directory / MOMENTUM_FILE))
    return {
        day: FactorReturns(
            market_excess=research[day][0],
            size=research[day][1],
            value=research[day][2],
            momentum=momentum[day][0],
            risk_free=research[day][3],
        )
        for day in sorted(research.keys() & momentum.keys())
    }


def fetch(directory: Path = FACTOR_DIR) -> dict[str, str]:
    """
    Download the current files into ``directory`` and return their digests.

    Does not change :data:`PINNED`. Reading the new files refuses until the pin is moved,
    which is the point: a vintage change is a decision, not a side effect of a download.

    """
    digests: dict[str, str] = {}
    for name in PINNED:
        with urlopen(SOURCE_URL + name, timeout=60) as response:  # noqa: S310 - fixed https URL
            content = response.read()
        (directory / name).write_bytes(content)
        digests[name] = hashlib.sha256(content).hexdigest()
    return digests


def main(argv: list[str] | None = None) -> int:
    """
    Report the pinned coverage, or fetch the current vintage.
    """
    parser = argparse.ArgumentParser(
        prog="python -m copilot.validation.factors",
        description="Show the pinned factor returns' coverage, or download the current files.",
    )
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="Download the current files and print their digests; the pin is not changed",
    )
    args = parser.parse_args(argv)

    if args.fetch:
        for name, digest in fetch().items():
            state = "pinned" if PINNED[name] == digest else "NEW - move PINNED to use it"
            print(f"{name}  {digest}  {state}")
        return 0

    try:
        factors = load_factors()
    except UnpinnedFactorFileError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    days = sorted(factors)
    print(f"{len(days)} sessions, {days[0]} to {days[-1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "FACTOR_DIR",
    "PINNED",
    "FactorReturns",
    "UnpinnedFactorFileError",
    "fetch",
    "load_factors",
    "parse_daily",
]
