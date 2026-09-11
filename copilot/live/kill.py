"""
The operator's kill switch: halt this host, cancel, and say how to recover.

    python -m copilot.live.kill --reason "fills look wrong" --account "$COPILOT_PAPER_ACCOUNT"
    python -m copilot.live.kill --status
    python -m copilot.live.kill --release 3f9a1c2e
    python -m copilot.live.kill --check-acknowledgements

The playbook's kill switch is *independent of the strategy* and its safe mode has five
limbs: block new entries, preserve state and alert, cancel only what policy says is safe,
avoid automatic flattening when truth is uncertain, and require the recovery checklist
before re-enabling. This command is those limbs, in that order:

1. **Block.** Engage the halt latch (:mod:`copilot.live.halt`). Every order-capable node
   this host builds from now on starts ``HALTED``, including the next ``day`` step - which
   is the point, because each step is its own process and a halt inside one node would not
   survive to the next.
2. **Alert.** A ``WARNING`` saying who halted and why. The operator who ran it knows; the
   alert is for the record and for anyone else watching.
3. **Cancel.** The sweep, with its broker census, as its own process. It is exempt from the
   latch because cancelling is safe mode.
4. **Do not flatten.** Nothing here closes a position. Positions are the recovery
   checklist's first question, answered against the broker's own records.
5. **Recover.** The checklist is printed; release takes the latch's id, retyped.

It is also the consumer
[ADR-0023](../docs/decisions/0023-a-critical-alert-demands-acknowledgement.md) left
without one: ``--check-acknowledgements`` reads every recorded ``CRITICAL`` receipt, and one
that expired unacknowledged engages the latch. ``copilot.live.day`` runs that check first,
on every phase, so an alert nobody answered on Friday night halts Monday's evening. The
check cannot stop the phase that runs it: an unreachable transport leaves a receipt
outstanding, and an unreadable log is reported, not raised.

"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from copilot.live.alerting import Alert
from copilot.live.alerting import ReceiptLog
from copilot.live.alerting import Severity
from copilot.live.alerting import alerter_from_environment
from copilot.live.alerting import receipt_status
from copilot.live.halt import UNACKNOWLEDGED_CRITICAL
from copilot.live.halt import engage
from copilot.live.halt import read_latch
from copilot.live.halt import release
from copilot.live.session import add_connection_arguments
from copilot.paths import ALERT_RECEIPTS_PATH
from copilot.paths import HALT_LATCH_PATH


if TYPE_CHECKING:
    from collections.abc import Callable

    from copilot.live.alerting import Acknowledgement
    from copilot.live.halt import Latch


OUT_DIR = Path(__file__).parent / "out"

RECOVERY_CHECKLIST = (
    "Positions: compare every open position against the broker's own records, not the cache.",
    "Orders: the sweep's verdict is BROKER CLEAR, or every order it names is accounted for.",
    "Cash and executions: reconcile against the broker's statement for the session.",
    "Cause: the reason above is understood, and fixed or explicitly accepted.",
    "Data: the catalog is current and the preflight passes against this host's broker.",
    "Breaker: the protection ledger's cooldown state is what you expect.",
    "Alerts: every CRITICAL since the halt is acknowledged.",
)
"""
What must be true before the latch is released: ``OPERATIONS.md``'s recovery steps.
"""


def check_acknowledgements(
    receipts: ReceiptLog,
    read_receipt: Callable[[str], Acknowledgement | None],
    *,
    latch_path: str | Path = HALT_LATCH_PATH,
) -> list[str]:
    """
    Settle outstanding ``CRITICAL`` receipts, halting for any that expired unanswered.

    Returns one line per receipt settled. A receipt the transport cannot read back - no
    credentials, a transport without receipts, or one that is unreachable - stays
    outstanding rather than being treated as either answer. A receipt log that cannot be
    read returns one line saying so, and settles nothing: it is not read as empty, and it
    does not stop the phase that asked.

    """
    if not receipts.path.exists():
        return []
    try:
        with receipts.settling():
            return _settle(receipts, read_receipt, latch_path=latch_path)
    except OSError as e:
        return [
            (
                f"WARNING: the receipt log {receipts.path} cannot be read ({e}); an "
                "unanswered CRITICAL cannot engage the halt until it can"
            ),
        ]


def _settle(
    receipts: ReceiptLog,
    read_receipt: Callable[[str], Acknowledgement | None],
    *,
    latch_path: str | Path,
) -> list[str]:
    """
    Settle each outstanding receipt, under the log's lock the caller holds.
    """
    lines: list[str] = []
    for receipt, title in receipts.outstanding().items():
        state = read_receipt(receipt)
        if state is None or state.outstanding:
            continue
        if state.acknowledged:
            receipts.resolve(receipt, "acknowledged")
            lines.append(f"acknowledged: {title}")
            continue
        receipts.resolve(receipt, "expired")
        latch, engaged = engage(
            f"CRITICAL alert {title!r} expired unacknowledged (receipt {receipt})",
            trigger=UNACKNOWLEDGED_CRITICAL,
            path=latch_path,
        )
        verb = "engaged" if engaged else "already engaged"
        lines.append(f"UNACKNOWLEDGED: {title} - halt latch {latch.latch_id} {verb}")
    return lines


def acknowledgement_check() -> list[str]:
    """
    Run the check against this host's receipts and configured transport.
    """
    alerter = alerter_from_environment()
    receipts = ReceiptLog(Path(ALERT_RECEIPTS_PATH).expanduser())
    return check_acknowledgements(receipts, lambda r: receipt_status(alerter.notifier, r))


def describe(latch: Latch | None) -> str:
    """
    One paragraph: halted or not, and if halted, why and how to release.
    """
    if latch is None:
        return "Halt latch: not engaged. Order-capable nodes start as their sessions ask."
    return (
        f"HALT LATCH ENGAGED  id {latch.latch_id}\n"
        f"  since   {latch.engaged_at}\n"
        f"  by      {latch.trigger} on {latch.host}\n"
        f"  reason  {latch.reason}\n"
        f"Every order-capable node on this host starts HALTED. Release, once the recovery "
        f"checklist holds, with: python -m copilot.live.kill --release {latch.latch_id}"
    )


def _engage(args: argparse.Namespace) -> int:
    latch, engaged = engage(args.reason)
    print(describe(latch))
    if not engaged:
        print("\n(already engaged; the original reason stands)")
    delivery = alerter_from_environment().send(
        Alert(Severity.WARNING, "halt engaged", f"{latch.reason} (latch {latch.latch_id})"),
    )
    print(f"\nalert WARNING 'halt engaged': {delivery.outcome}")

    code = 0
    if args.no_sweep:
        print("\nSweep skipped (--no-sweep). Working orders are NOT cancelled.")
        code = 4
    elif not args.account:
        print("\nNo --account: the sweep cannot run. Working orders are NOT cancelled.")
        code = 2
    else:
        sys.stdout.flush()
        code = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [
                sys.executable,
                "-m",
                "copilot.live.cancel_working",
                "--all",
                "--host",
                args.host,
                "--port",
                str(args.port),
                "--account",
                args.account,
            ],
            check=False,
        ).returncode

    print("\nNothing has been flattened. Before release:")
    for number, item in enumerate(RECOVERY_CHECKLIST, start=1):
        print(f"  {number}. {item}")
    return code


def _release(latch_id: str) -> int:
    try:
        released = release(latch_id)
    except ValueError as e:
        print(f"refused: {e}")
        return 2
    now = datetime.now(UTC)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    record = OUT_DIR / f"halt_release_{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    record.write_text(
        json.dumps({**asdict(released), "released_at": now.isoformat()}, indent=2) + "\n",
    )
    delivery = alerter_from_environment().send(
        Alert(Severity.INFO, "halt released", f"latch {released.latch_id}: {released.reason}"),
    )
    print(f"Released latch {released.latch_id}; filed {record}")
    print(f"alert INFO 'halt released': {delivery.outcome}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """
    Engage, inspect, release, or check acknowledgements.
    """
    parser = argparse.ArgumentParser(
        prog="python -m copilot.live.kill",
        description="Halt every order-capable node on this host, cancel, and print recovery.",
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--reason", help="Engage the halt, saying why")
    action.add_argument("--status", action="store_true", help="Show whether the host is halted")
    action.add_argument("--release", metavar="LATCH_ID", help="Release the latch, by its id")
    action.add_argument(
        "--check-acknowledgements",
        action="store_true",
        help="Halt if a CRITICAL alert expired unacknowledged",
    )
    add_connection_arguments(parser)
    parser.add_argument("--no-sweep", action="store_true", help="Engage without cancelling")
    args = parser.parse_args(argv)

    if args.status:
        print(describe(read_latch()))
        return 1 if read_latch() is not None else 0
    if args.release:
        return _release(args.release)
    if args.check_acknowledgements:
        for line in acknowledgement_check() or ["no CRITICAL alert awaiting an answer"]:
            print(line)
        return 1 if read_latch() is not None else 0
    return _engage(args)


__all__ = [
    "RECOVERY_CHECKLIST",
    "acknowledgement_check",
    "check_acknowledgements",
    "describe",
    "main",
]


if __name__ == "__main__":
    sys.exit(main())
