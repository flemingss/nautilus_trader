"""
Measure real quoted spreads from Interactive Brokers.

Closes the circular dependency recorded in trade-copilot ``docs/SYSTEM.md`` §14: the
validation gate's verdicts depend on a cost model, calibrating the cost model needs
observed prices, and observed prices were expected to need a paper run that the gate
itself gates. A live quote snapshot breaks the circle without trading anything.

``PaperFillConfig.spread_bps`` is a **per-side** proxy currently defaulted to 5 bps,
described in trade-copilot as "a deliberately conservative ceiling ~10x the quoted
spread, pending a live-quote snapshot". This is that snapshot.

Read-only: this subscribes to quotes and never constructs an execution client, so it
cannot place an order.

Delayed data caveat
-------------------
US equities on an account without a realtime subscription return ``DELAYED`` quotes.
Delayed feeds still carry a genuine bid/ask, but they update on a slower cadence and
can be wider than the realtime NBBO. Results are therefore an **upper bound** on the
realtime spread, which is the conservative direction for a cost model. The recorded
``market_data_type`` in the output says which was used; treat DELAYED numbers as
indicative until a realtime subscription is in place.

"""

from __future__ import annotations

import json
import os
import signal
import statistics
import subprocess
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from decimal import Decimal
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
from nautilus_trader.model import InstrumentId
from nautilus_trader.model import TraderId


# The trade-copilot calibration record (ADR-0011) covers these three names, so
# measuring the same set makes the new number directly comparable to the old one.
DEFAULT_SYMBOLS = ("AAPL=STK.SMART", "MSFT=STK.SMART", "SPY=STK.SMART")

# The value being replaced, for reporting only.
INCUMBENT_SPREAD_BPS_PER_SIDE = Decimal(5)

OUT_DIR = Path(__file__).parent / "out"


@dataclass
class SymbolSamples:
    """
    Accumulated spread observations for one instrument.
    """

    instrument_id: str
    spreads_bps: list[float] = field(default_factory=list)
    """Full spread (ask - bid) as basis points of the mid price."""
    rejected: int = 0
    """
    Quotes discarded as unusable (crossed, locked, or non-positive mid).
    """

    def add(self, bid: float, ask: float) -> None:
        """
        Record one quote, discarding it if the book is crossed or locked.
        """
        mid = (bid + ask) / 2.0
        # A crossed or locked book, or a non-positive mid, says nothing about cost.
        if mid <= 0.0 or ask <= bid:
            self.rejected += 1
            return
        self.spreads_bps.append((ask - bid) / mid * 10_000.0)

    def summary(self) -> dict[str, object]:
        """
        Report the distribution, or say plainly that nothing arrived.
        """
        n = len(self.spreads_bps)
        if n == 0:
            return {
                "instrument_id": self.instrument_id,
                "samples": 0,
                "rejected": self.rejected,
                "note": "no usable quotes received",
            }
        ordered = sorted(self.spreads_bps)

        def pct(p: float) -> float:
            # Nearest-rank; n is small enough that interpolation adds nothing.
            idx = min(n - 1, max(0, round(p * (n - 1))))
            return round(ordered[idx], 4)

        median_full = statistics.median(ordered)
        return {
            "instrument_id": self.instrument_id,
            "samples": n,
            "rejected": self.rejected,
            "full_spread_bps": {
                "median": round(median_full, 4),
                "mean": round(statistics.fmean(ordered), 4),
                "p25": pct(0.25),
                "p75": pct(0.75),
                "p95": pct(0.95),
                "min": round(ordered[0], 4),
                "max": round(ordered[-1], 4),
            },
            # PaperFillConfig.spread_bps is per-side: crossing one side costs half
            # the quoted spread.
            "per_side_bps": {
                "median": round(median_full / 2.0, 4),
                "p95": round(pct(0.95) / 2.0, 4),
            },
            "incumbent_per_side_bps": float(INCUMBENT_SPREAD_BPS_PER_SIDE),
            "incumbent_overstatement_x": (
                round(float(INCUMBENT_SPREAD_BPS_PER_SIDE) / (median_full / 2.0), 2)
                if median_full > 0
                else None
            ),
        }


class SpreadRecorder(DataActor):
    """
    Subscribes to quotes and records the spread of each tick.

    ``DataActor`` is a pyo3 class whose ``__new__`` accepts only the config, so the
    instruments are attached after construction by :func:`build_node` rather than
    passed through ``__init__``.

    """

    def configure(self, instrument_ids: list[InstrumentId]) -> None:
        """
        Attach the instruments to record, after pyo3 construction.
        """
        self._instrument_ids = instrument_ids
        self.samples: dict[str, SymbolSamples] = {
            str(i): SymbolSamples(instrument_id=str(i)) for i in instrument_ids
        }

    def on_start(self) -> None:
        """
        Subscribe to quotes for every configured instrument.
        """
        for instrument_id in self._instrument_ids:
            self.subscribe_quotes(instrument_id)
            self.log.info(f"Subscribed quotes for {instrument_id}")

    def on_quote(self, quote) -> None:  # noqa: ANN001 - QuoteTick from the engine
        """
        Accumulate the spread of one quote.
        """
        bucket = self.samples.get(str(quote.instrument_id))
        if bucket is not None:
            bucket.add(float(quote.bid_price), float(quote.ask_price))


def build_node(  # noqa: PLR0913 - each argument is an independent connection knob
    instrument_ids: list[InstrumentId],
    *,
    host: str,
    port: int,
    client_id: int,
    market_data_type: MarketDataType,
    symbology: SymbologyMethod = SymbologyMethod.RAW,
) -> tuple[LiveNode, SpreadRecorder]:
    """
    Build a data-only node with a recorder attached.
    """
    provider_config = InteractiveBrokersInstrumentProviderConfig(
        symbology_method=symbology,
        load_ids=set(instrument_ids),
    )
    node = (
        LiveNode.builder(
            "COPILOT-SPREAD-CAL",
            TraderId.from_str("CALIBRATE-001"),
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
    recorder = SpreadRecorder(DataActorConfig())
    recorder.configure(instrument_ids)
    node.add_actor(recorder)
    return node, recorder


def main() -> None:
    """
    Record spreads for the configured window, then write the report.
    """
    host = os.getenv("IB_V2_HOST", "172.17.112.1")
    port = int(os.getenv("IB_V2_PORT", "7497"))
    client_id = int(os.getenv("COPILOT_CAL_CLIENT_ID", "701"))
    seconds = int(os.getenv("COPILOT_CAL_SECONDS", "120"))
    symbols = os.getenv("COPILOT_CAL_SYMBOLS", ",".join(DEFAULT_SYMBOLS)).split(",")
    mdt_name = os.getenv("COPILOT_CAL_MARKET_DATA_TYPE", "DELAYED").upper()
    market_data_type = getattr(MarketDataType, mdt_name)
    symbology = getattr(SymbologyMethod, os.getenv("COPILOT_CAL_SYMBOLOGY", "RAW").upper())

    instrument_ids = [InstrumentId.from_str(s.strip()) for s in symbols if s.strip()]
    node, recorder = build_node(
        instrument_ids,
        host=host,
        port=port,
        client_id=client_id,
        market_data_type=market_data_type,
        symbology=symbology,
    )

    started = datetime.now(UTC)
    print(
        f"Recording spreads for {len(instrument_ids)} instrument(s) "
        f"over {seconds}s via {host}:{port} [{mdt_name}]",
        flush=True,
    )

    # Bound the run from inside so the tool is self-contained rather than relying
    # on the caller wrapping it in `timeout`. Mirrors `schedule_node_stop` in the
    # upstream IB examples.
    if seconds > 0:
        subprocess.Popen(  # noqa: S603
            ["/bin/sh", "-c", f"sleep {seconds}; kill -{signal.SIGINT.value} {os.getpid()}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    try:
        node.run()
    except KeyboardInterrupt:
        pass
    finally:
        finished = datetime.now(UTC)
        report = {
            "measured_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "duration_seconds": round((finished - started).total_seconds(), 1),
            "source": f"interactive_brokers {host}:{port}",
            "market_data_type": mdt_name,
            "delayed_caveat": mdt_name.startswith("DELAYED"),
            "symbols": [s.summary() for s in recorder.samples.values()],
        }
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = started.strftime("%Y%m%dT%H%M%SZ")
        out_path = OUT_DIR / f"spread_snapshot_{stamp}.json"
        out_path.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2), flush=True)
        print(f"\nWrote {out_path}", flush=True)


if __name__ == "__main__":
    main()
