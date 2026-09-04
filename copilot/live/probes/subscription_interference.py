"""
Test whether adding a second subscription stops the quotes already flowing.

The observation this exists to settle was recorded in ``docs/ROADMAP.md`` and reproduced
across three runs: quotes for an instrument stopped arriving once a trades or depth
subscription was added for the *same* instrument. Two candidate explanations have already
been eliminated. The teardown claim was withdrawn - every subscription takes its own
child cancellation token, so one stream failing cannot cancel another. The subscriptions
map really was keyed by instrument alone and really did evict siblings, but the evicted
task kept running, so that defect does not stop a quote stream either.

What is left is an untested guess: that IB itself disturbs the market data line for a
contract when it refuses a request against it. This run answers it.

The design turns on the control. A treated instrument gets the second subscription and a
control instrument does not, and both are watched across the same two windows. Quotes
stopping on both is a session-wide event and says nothing about the second subscription;
quotes stopping only on the treated instrument is the reported behaviour, and it is
contract-specific, which is what an IB-side explanation predicts.

A phase that recorded no quotes to begin with cannot show quotes stopping, so a baseline
without quotes is reported as inconclusive rather than as a result in either direction.

Read-only. Constructs no execution client, so it cannot place an order.

"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path

from nautilus_trader.adapters.interactive_brokers import InteractiveBrokersDataClientConfig
from nautilus_trader.adapters.interactive_brokers import InteractiveBrokersDataClientFactory
from nautilus_trader.adapters.interactive_brokers import InteractiveBrokersInstrumentProviderConfig
from nautilus_trader.adapters.interactive_brokers import MarketDataType
from nautilus_trader.adapters.interactive_brokers import SymbologyMethod
from nautilus_trader.common import DataActor
from nautilus_trader.common import DataActorConfig
from nautilus_trader.common import Environment
from nautilus_trader.live import LiveNode
from nautilus_trader.model import BookType
from nautilus_trader.model import InstrumentId
from nautilus_trader.model import TraderId


NODE_NAME = "COPILOT-INTERFERENCE"
TRADER_ID = "INTERFERE-001"

BASELINE = "baseline"
"""
Phase name for the window before the second subscription is added.
"""

TREATED = "treated"
"""
Phase name for the window after it is added.
"""

TREATMENT_ALERT = "add-second-subscription"
"""
Clock alert that ends the baseline and applies the treatment.
"""

TREATMENTS = ("trades", "depth")
"""
The two second subscriptions the original observation named.

``trades`` issues IB's tick-by-tick trades request and ``depth`` issues an L2 book
subscription - the same two requests that drew 10189 and a depth-entitlement refusal when
the stall was first seen. Both have to be requests the adapter actually forwards to IB:
``subscribe_book_depth10`` is not implemented for Interactive Brokers, so a run using it
reports a clean negative while testing nothing.

"""

DEPTH_BOOK_TYPE = BookType.L2_MBP
"""
IB publishes L1 and L2 and refuses L3_MBO outright, so L2 is the deepest real request.
"""

OUT_DIR = Path(__file__).parent / "out"


@dataclass
class PhaseCounts:
    """
    Quotes recorded for one instrument, split by phase.
    """

    instrument_id: str
    role: str
    """
    ``treated`` if this instrument receives the second subscription, else ``control``.
    """
    counts: dict[str, int] = field(default_factory=lambda: {BASELINE: 0, TREATED: 0})
    trades: int = 0
    """
    Trade ticks received, which says whether the second subscription delivered anything.
    """

    def summary(self) -> dict[str, object]:
        """
        Render the counts for the report.
        """
        return {
            "instrument_id": self.instrument_id,
            "role": self.role,
            "quotes_baseline": self.counts[BASELINE],
            "quotes_after_treatment": self.counts[TREATED],
            "trades_received": self.trades,
        }


class InterferenceProbe(DataActor):
    """
    Subscribes two instruments to quotes, then adds a second subscription to one.

    ``DataActor`` is a pyo3 class whose ``__new__`` accepts only the config, so the
    instruments are attached after construction rather than passed through ``__init__``.

    """

    def configure(
        self,
        *,
        treated: InstrumentId,
        control: InstrumentId,
        treatment: str,
        phase_secs: int,
    ) -> None:
        """
        Attach the instruments and the treatment, after pyo3 construction.
        """
        self._treated = treated
        self._control = control
        self._treatment = treatment
        self._phase_secs = phase_secs
        self._phase = BASELINE
        self.treatment_error: str | None = None
        """
        The exception from applying the treatment, if it raised rather than being
        refused quietly.
        """
        self.treatment_applied_at: str | None = None
        self.records: dict[str, PhaseCounts] = {
            str(treated): PhaseCounts(str(treated), "treated"),
            str(control): PhaseCounts(str(control), "control"),
        }

    def on_start(self) -> None:
        """
        Subscribe both instruments to quotes and arm the treatment.
        """
        for instrument_id in (self._treated, self._control):
            self.subscribe_quotes(instrument_id)
            self.log.info(f"Subscribed quotes for {instrument_id}")

        self.clock.set_time_alert(
            TREATMENT_ALERT,
            self.clock.utc_now() + timedelta(seconds=self._phase_secs),
        )

    def on_time_event(self, event) -> None:  # noqa: ANN001 - TimeEvent from the engine
        """
        End the baseline and add the second subscription to the treated instrument only.
        """
        if event.name != TREATMENT_ALERT:
            return

        # The phase flips before the subscription is attempted, so a quote arriving
        # between the two is counted against the treated window rather than the
        # baseline. That direction is the conservative one: it can only weaken a
        # claim that quotes stopped, never manufacture it.
        self._phase = TREATED
        self.treatment_applied_at = datetime.now(UTC).isoformat()

        try:
            if self._treatment == "trades":
                self.subscribe_trades(self._treated)
            else:
                self.subscribe_book_deltas(self._treated, DEPTH_BOOK_TYPE)
        except Exception as e:  # noqa: BLE001 - the error text is part of the finding
            self.treatment_error = f"{type(e).__name__}: {e}"
            self.log.warning(f"Treatment raised: {self.treatment_error}")
        else:
            self.log.info(f"Applied {self._treatment} subscription to {self._treated}")

    def on_quote(self, quote) -> None:  # noqa: ANN001 - QuoteTick from the engine
        """
        Count one quote against the current phase.
        """
        record = self.records.get(str(quote.instrument_id))
        if record is not None:
            record.counts[self._phase] += 1

    def on_trade(self, trade) -> None:  # noqa: ANN001 - TradeTick from the engine
        """
        Count one trade, so a treatment that was actually permissioned is visible.
        """
        record = self.records.get(str(trade.instrument_id))
        if record is not None:
            record.trades += 1


def build_node(
    probe_args: dict[str, object],
    *,
    host: str,
    port: int,
    client_id: int,
    market_data_type: MarketDataType,
    symbology: SymbologyMethod = SymbologyMethod.RAW,
) -> tuple[LiveNode, InterferenceProbe]:
    """
    Build a data-only node with the probe attached.
    """
    treated = probe_args["treated"]
    control = probe_args["control"]
    provider_config = InteractiveBrokersInstrumentProviderConfig(
        symbology_method=symbology,
        load_ids={treated, control},
    )
    node = (
        LiveNode.builder(
            NODE_NAME,
            TraderId.from_str(TRADER_ID),
            Environment.LIVE,
        )
        .add_data_client(
            None,
            InteractiveBrokersDataClientFactory(),
            InteractiveBrokersDataClientConfig(
                host=host,
                port=port,
                client_id=client_id,
                market_data_type=market_data_type,
                connection_timeout=60,
                instrument_provider=provider_config,
            ),
        )
        .build()
    )
    probe = InterferenceProbe(DataActorConfig())
    probe.configure(**probe_args)  # type: ignore[arg-type]
    node.add_actor(probe)
    return node, probe


def verdict(probe: InterferenceProbe) -> tuple[str, str]:
    """
    Read the counts and say what they support.

    Returns the verdict and the sentence behind it, so a report carries its own
    reasoning rather than leaving a reader to reconstruct it from four integers.

    """
    treated = next(r for r in probe.records.values() if r.role == "treated")
    control = next(r for r in probe.records.values() if r.role == "control")

    # A treatment that raised was never applied, so the quotes that kept flowing are not
    # evidence that the second subscription left them alone. Measured 2026-09-02: a
    # `subscribe_book_depth10` call missing an argument raised, and the run still read
    # NOT REPRODUCED - a clean negative from an experiment that never ran.
    if probe.treatment_error is not None:
        return (
            "INCONCLUSIVE",
            (
                "The second subscription was never applied - it raised "
                f"{probe.treatment_error}. The counts describe a run with no treatment "
                "in it."
            ),
        )

    if treated.counts[BASELINE] == 0:
        return (
            "INCONCLUSIVE",
            (
                f"{treated.instrument_id} recorded no quotes before the treatment, so "
                "no stall could be observed. Re-run inside a session with quotes flowing."
            ),
        )

    if treated.counts[TREATED] > 0:
        return (
            "NOT REPRODUCED",
            (
                f"{treated.instrument_id} kept receiving quotes after the second "
                f"subscription ({treated.counts[TREATED]} against a baseline of "
                f"{treated.counts[BASELINE]})."
            ),
        )

    if control.counts[TREATED] == 0:
        return (
            "INCONCLUSIVE",
            (
                "Quotes stopped on both instruments, so this is a session-wide event "
                "and says nothing about the second subscription. The control is what "
                "makes that distinction, and it did not survive either."
            ),
        )

    return (
        "REPRODUCED",
        (
            f"{treated.instrument_id} stopped receiving quotes after the second "
            f"subscription while the control {control.instrument_id} kept receiving "
            f"them ({control.counts[TREATED]}). The stall is specific to the treated "
            "contract."
        ),
    )


def build_report(
    probe: InterferenceProbe,
    *,
    started: datetime,
    finished: datetime,
    host: str,
    port: int,
    market_data_type: str,
    treatment: str,
    phase_secs: int,
) -> dict[str, object]:
    """
    Assemble the report from what was recorded.
    """
    result, reasoning = verdict(probe)
    return {
        "measured_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_seconds": round((finished - started).total_seconds(), 1),
        "source": f"interactive_brokers {host}:{port}",
        "market_data_type": market_data_type,
        "treatment": treatment,
        "phase_seconds": phase_secs,
        "treatment_applied_at": probe.treatment_applied_at,
        "treatment_error": probe.treatment_error,
        "verdict": result,
        "reasoning": reasoning,
        "instruments": [r.summary() for r in probe.records.values()],
    }


def main() -> None:
    """
    Run the two phases, then report what the counts support.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.getenv("IB_V2_HOST", "172.17.112.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("IB_V2_PORT", "7497")))
    parser.add_argument("--client-id", type=int, default=721)
    parser.add_argument("--treated", default="AAPL=STK.SMART")
    parser.add_argument("--control", default="MSFT=STK.SMART")
    parser.add_argument("--treatment", choices=TREATMENTS, default="trades")
    parser.add_argument("--phase-secs", type=int, default=180)
    parser.add_argument("--market-data-type", default="DELAYED")
    args = parser.parse_args()

    treated = InstrumentId.from_str(args.treated)
    control = InstrumentId.from_str(args.control)
    if treated == control:
        print("error: the treated and control instruments must differ")
        raise SystemExit(2)

    node, probe = build_node(
        {
            "treated": treated,
            "control": control,
            "treatment": args.treatment,
            "phase_secs": args.phase_secs,
        },
        host=args.host,
        port=args.port,
        client_id=args.client_id,
        market_data_type=getattr(MarketDataType, args.market_data_type.upper()),
    )

    started = datetime.now(UTC)
    print(
        f"Interference probe: {treated} treated with a {args.treatment} subscription, "
        f"{control} as control, {args.phase_secs}s per phase "
        f"[{args.market_data_type.upper()}]",
        flush=True,
    )

    # Bound the run from inside, as `spread_snapshot` does, so the tool is
    # self-contained rather than relying on the caller wrapping it in `timeout`.
    total = args.phase_secs * 2
    subprocess.Popen(  # noqa: S603
        ["/bin/sh", "-c", f"sleep {total}; kill -{signal.SIGINT.value} {os.getpid()}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        node.run()
    finally:
        report = build_report(
            probe,
            started=started,
            finished=datetime.now(UTC),
            host=args.host,
            port=args.port,
            market_data_type=args.market_data_type.upper(),
            treatment=args.treatment,
            phase_secs=args.phase_secs,
        )
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        path = OUT_DIR / f"interference_{started.strftime('%Y%m%dT%H%M%SZ')}.json"
        path.write_text(json.dumps(report, indent=2) + "\n")

        print()
        print(f"{'instrument':22s} {'role':8s} {'baseline':>9s} {'treated':>9s} {'trades':>7s}")
        for row in report["instruments"]:  # type: ignore[union-attr]
            print(
                f"{row['instrument_id']:22s} {row['role']:8s} "
                f"{row['quotes_baseline']:9d} {row['quotes_after_treatment']:9d} "
                f"{row['trades_received']:7d}",
            )
        if probe.treatment_error:
            print(f"\ntreatment error: {probe.treatment_error}")
        print(f"\nVERDICT: {report['verdict']}")
        print(report["reasoning"])
        print(f"\nWrote {path}")


if __name__ == "__main__":
    main()
