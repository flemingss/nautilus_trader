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

OPERATOR_TZ_ENV = "COPILOT_OPERATOR_TZ"
DEFAULT_OPERATOR_TZ = "Asia/Tokyo"
"""
The operator's own clock, printed beside Eastern in every time the day command shows.

Display only. Every session decision keys off ``EASTERN``, so this cannot move a window,
a fold or an order - which is why it is a setting rather than a decision.

It is **not** ``IBAPI_TIMEZONE_ALIASES``. That alias is required on every IB connect
whatever the operator's clock says, and conflating the two would make a cosmetic
preference able to break connections opaquely.

Tokyo remains the default because the playbook's schedule is written in JST.

"""

RISK_LEDGER_DIR = "~/.nautilus_copilot/risk"
"""
The protection breaker's closed-trade evidence, one file per account.

Per account because paper and live keep separate everything, and a paper loss must never
count toward a live cooldown or the reverse.

"""


def risk_ledger_path(account_id: str, directory: str = RISK_LEDGER_DIR) -> Path:
    """
    Return the breaker's ledger for one account.
    """
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in account_id)
    return Path(directory).expanduser() / f"outcomes_{safe}.jsonl"


OPS_CONFIG_DIR = "~/.config/copilot"
"""
The paper VM's configuration, outside the tree: ``copilot.env``, ``secrets.env``,
``gateway.env`` and ``tws_password``.

Templates live in ``copilot/ops/env/``.

"""

HALT_LATCH_PATH = "~/.nautilus_copilot/HALT.json"
"""
The operator kill switch's latch. Present means every order-capable node on this host
starts halted; see :mod:`copilot.live.halt`.

Per host, not per account: a kill switch that halted one account while another kept
trading on the same machine would be a switch the operator has to remember the scope of.

"""

ALERT_RECEIPTS_PATH = "~/.nautilus_copilot/alerts/receipts.jsonl"
"""
Every ``CRITICAL`` alert's receipt, so an unacknowledged one can engage the halt latch.
"""

ALERT_FLOOD_PATH = "~/.nautilus_copilot/alerts/flood.json"
"""
When each alert last went out, so repeats collapse across the one-shot processes that
send them.
"""

HEARTBEAT_URL_ENV = "COPILOT_HEARTBEAT_URL"
"""
A push-monitor URL the morning pings, so an external watcher notices when it stops.

Pushover reports what the system sends and cannot report that it stopped sending. The
watcher - Uptime Kuma, healthchecks, or the owner's cluster monitoring - alerts on a
missed ping. Optional; unset, nothing is pinged and the morning says so.

"""

MARKETSTACK_API_KEY_ENV = "MARKETSTACK_API_KEY"
DATABENTO_API_KEY_ENV = "DATABENTO_API_KEY"
PUSHOVER_TOKEN_ENV = "PUSHOVER_TOKEN"  # noqa: S105 - the variable's name, never its value
PUSHOVER_USER_KEY_ENV = "PUSHOVER_USER_KEY"
"""
The alerting transport's credentials.

Named here, read at the CLI boundary in
``live/alerting.py``, and never held by anything below it.

"""


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
    "ALERT_FLOOD_PATH",
    "ALERT_RECEIPTS_PATH",
    "CATALOG_PATH_ENV",
    "DATABENTO_API_KEY_ENV",
    "DEFAULT_CATALOG",
    "DEFAULT_OPERATOR_TZ",
    "DEFAULT_STORE",
    "HALT_LATCH_PATH",
    "HEARTBEAT_URL_ENV",
    "MARKETSTACK_API_KEY_ENV",
    "OPERATOR_TZ_ENV",
    "OPS_CONFIG_DIR",
    "PUSHOVER_TOKEN_ENV",
    "PUSHOVER_USER_KEY_ENV",
    "RISK_LEDGER_DIR",
    "add_catalog_argument",
    "catalog_path",
    "risk_ledger_path",
    "store_path",
]
