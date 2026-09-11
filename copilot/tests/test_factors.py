"""
The factor returns attribution is measured against: pinned, parsed by shape, in fractions.
"""

from __future__ import annotations

import io
import shutil
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from copilot.validation.factors import FACTOR_DIR
from copilot.validation.factors import MOMENTUM_FILE
from copilot.validation.factors import RESEARCH_FILE
from copilot.validation.factors import UnpinnedFactorFileError
from copilot.validation.factors import load_factors
from copilot.validation.factors import parse_daily


def zipped(text: str, name: str = "sample.csv") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, text)
    return buffer.getvalue()


SAMPLE = """This file was created by using the 202607 CRSP database.
The Tbill return is the simple daily rate that, over the number of trading days
compounds to 1-month TBill rate.

,Mkt-RF,SMB,HML,RF
20250102,   -0.27,   0.53,   0.14,    0.017
20250103,    1.21,  -0.37,  -0.66,    0.017

Copyright 2026 Eugene F. Fama and Kenneth R. French
"""


def test_rows_are_read_by_shape_and_prose_is_skipped() -> None:
    rows = parse_daily(zipped(SAMPLE))

    assert sorted(rows) == [date(2025, 1, 2), date(2025, 1, 3)]


def test_percent_becomes_a_fraction_exactly() -> None:
    rows = parse_daily(zipped(SAMPLE))

    assert rows[date(2025, 1, 3)] == (
        Decimal("0.0121"),
        Decimal("-0.0037"),
        Decimal("-0.0066"),
        Decimal("0.00017"),
    )


def test_the_missing_value_sentinel_is_refused_not_read_as_a_loss() -> None:
    with pytest.raises(ValueError, match="missing"):
        parse_daily(zipped(SAMPLE.replace("-0.37", "-99.99")))


def test_the_committed_files_are_the_pinned_ones_and_cover_the_evaluation_window() -> None:
    factors = load_factors()

    assert min(factors) <= date(2005, 1, 3)
    assert max(factors) >= date(2025, 12, 31)


def test_a_replaced_file_is_refused_until_the_pin_moves(tmp_path: Path) -> None:
    shutil.copy(FACTOR_DIR / MOMENTUM_FILE, tmp_path / MOMENTUM_FILE)
    (tmp_path / RESEARCH_FILE).write_bytes(zipped(SAMPLE))

    with pytest.raises(UnpinnedFactorFileError, match="update PINNED in the same commit"):
        load_factors(tmp_path)
