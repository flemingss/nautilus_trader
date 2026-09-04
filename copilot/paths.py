"""
Where the overlay's machine state lives, and the environment that points at it.

One definition. Before this module the catalog path was written out in twelve modules,
its environment override in five and the quote store in three - all identical, all
copied, and each one a place a future change would have to remember. A constant that
has to be right in twelve files is right in eleven the day someone edits it.

Everything here is a *default* the operator may override and a *name* the operator
must export. Nothing here reads a secret: the API keys are named so a module can ask
the environment for them consistently, and never held.

"""

from __future__ import annotations

import argparse
import os
from pathlib import Path


CATALOG_PATH_ENV = "COPILOT_CATALOG_PATH"
DEFAULT_CATALOG = "~/.nautilus_copilot/catalog"
"""
The daily-bar catalog: a Nautilus ``ParquetDataCatalog``, outside the repository because
it is vendor-licensed data and the repository is public.
"""

DEFAULT_STORE = "~/.nautilus_copilot/databento"
"""
Bulk Databento pulls, beside the catalog.

Machine state with the same backup obligation.

"""

MARKETSTACK_API_KEY_ENV = "MARKETSTACK_API_KEY"
DATABENTO_API_KEY_ENV = "DATABENTO_API_KEY"


def catalog_path() -> str:
    """
    Return the catalog directory the environment names, or the default.
    """
    return os.environ.get(CATALOG_PATH_ENV, DEFAULT_CATALOG)


def store_path() -> Path:
    """
    Return the Databento store as an expanded path.
    """
    return Path(DEFAULT_STORE).expanduser()


def add_catalog_argument(parser: argparse.ArgumentParser) -> None:
    """
    Add the ``--catalog`` flag every catalog-reading command takes, worded once.
    """
    parser.add_argument(
        "--catalog",
        default=catalog_path(),
        help=f"Catalog directory (default: ${CATALOG_PATH_ENV} or {DEFAULT_CATALOG})",
    )


__all__ = [
    "CATALOG_PATH_ENV",
    "DATABENTO_API_KEY_ENV",
    "DEFAULT_CATALOG",
    "DEFAULT_STORE",
    "MARKETSTACK_API_KEY_ENV",
    "add_catalog_argument",
    "catalog_path",
    "store_path",
]
