"""
Tests for the one definition of where machine state lives.

The value of this module is that there is exactly one of each constant. The tests pin
that the modules which used to define their own now carry this one, so the duplication
cannot quietly return through a copy-paste.

"""

from __future__ import annotations

import argparse
import importlib
from pathlib import Path

import pytest

from copilot import paths


OVERLAY = Path(__file__).resolve().parent.parent

MODULES_THAT_STILL_NAME_THE_DEFAULT = (
    "copilot.strategies.validate",
    "copilot.strategies.spend_holdout",
)
"""
Modules that take the catalog as a function default rather than only as a CLI flag.

The CLI-only readers stopped naming the constant at all once the flag was shared, which
is the better outcome and the one the source scan below protects.

"""


@pytest.mark.parametrize("module", MODULES_THAT_STILL_NAME_THE_DEFAULT)
def test_a_module_that_names_the_default_carries_the_one_object(module: str) -> None:
    """
    Same object, not merely the same string: a re-typed copy would compare equal.
    """
    assert importlib.import_module(module).DEFAULT_CATALOG is paths.DEFAULT_CATALOG


def test_no_module_defines_its_own_copy() -> None:
    """
    The invariant this module exists for: one definition, found by reading the source.

    Twelve modules once carried the string; the day one of them is edited alone is the
    day the catalog path means two things. The scan is over source text because that is
    where a copy would reappear, and an import-time check cannot see a literal.

    """
    literals = ("~/.nautilus_copilot/catalog", "~/.nautilus_copilot/databento")
    offenders = []
    for file in OVERLAY.rglob("*.py"):
        if file.name == "paths.py" or "tests" in file.parts:
            continue
        text = file.read_text()
        for literal in literals:
            if f'"{literal}"' in text or f"'{literal}'" in text:
                offenders.append(f"{file.relative_to(OVERLAY)}: {literal}")
    assert offenders == []


def test_the_environment_overrides_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(paths.CATALOG_PATH_ENV, "/elsewhere")
    assert paths.catalog_path() == "/elsewhere"


def test_without_the_environment_the_default_stands(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(paths.CATALOG_PATH_ENV, raising=False)
    assert paths.catalog_path() == paths.DEFAULT_CATALOG


def test_the_store_sits_beside_the_catalog() -> None:
    """
    Same backup obligation, same parent.
    """
    assert paths.store_path().parent == Path(paths.DEFAULT_CATALOG).expanduser().parent


def test_the_catalog_flag_is_worded_once() -> None:
    parser = argparse.ArgumentParser()
    paths.add_catalog_argument(parser)
    args = parser.parse_args([])
    assert args.catalog == paths.catalog_path()
    assert parser.parse_args(["--catalog", "/x"]).catalog == "/x"
