"""
Probe what market data this IB account can actually read.

Entitlements change - a release form, a subscription purchase, or a trading-day boundary
can each move them - and the failure modes are quiet: an unentitled subscription returns
no data and logs no error, which reads exactly like a code fault. This answers the
question directly so a session can start from fact rather than from last week's
assumption.

Read-only. Constructs no execution client, so it cannot place an order.

Run it after any change to the IB account, and at the start of any session that depends
on data access.

"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import os
from dataclasses import dataclass

from nautilus_trader.adapters import interactive_brokers as ib
from nautilus_trader.model import InstrumentId


HOST = os.getenv("IB_V2_HOST", "172.17.112.1")
PORT = int(os.getenv("IB_V2_PORT", "7497"))

# A past weekday, so historical requests are not asking for "up to now" - which is a
# separate failure mode from lacking the entitlement itself.
DEFAULT_END = dt.datetime(2026, 8, 28, 16, 0, tzinfo=dt.UTC)


@dataclass(frozen=True)
class Probe:
    """
    One instrument to ask about, and how to ask.
    """

    label: str
    instrument_id: str
    bar_spec: str
    symbology: str = "SIMPLIFIED"


# Chosen to map onto the account's actual subscription list, so a run says which
# *entitlements* work rather than which symbols happen to be popular. Complimentary
# feeds on this account cover FX (IDEALPRO), US non-consolidated equities, bonds,
# Korea equities, alternative European equities and ZEROHASH crypto.
PROBES = (
    # Consolidated US equities - the gap. SMART-routed historical needs every
    # exchange the name trades on, which non-consolidated (IEX-only) does not cover.
    Probe("US equity SMART", "AAPL.NASDAQ", "1-HOUR-LAST"),
    Probe("US ETF SMART", "SPY.ARCA", "1-HOUR-LAST"),
    Probe("US index", "^SPX.CBOE", "1-HOUR-LAST"),
    # Directed-exchange variants: worth knowing whether a non-consolidated
    # entitlement satisfies a request aimed at the one venue it covers.
    Probe("US equity IEX-directed", "AAPL=STK.IEX", "1-HOUR-LAST", "RAW"),
    Probe("US equity ISLAND-directed", "AAPL=STK.ISLAND", "1-HOUR-LAST", "RAW"),
    # Complimentary entitlements, to confirm what they actually deliver via API.
    Probe("Forex IDEALPRO", "EUR/USD.IDEALPRO", "1-HOUR-MID"),
    Probe("Forex IDEALPRO 2", "USD/JPY.IDEALPRO", "1-HOUR-MID"),
)


async def probe_historical(
    probe: Probe,
    market_data_type: str,
    client_id: int,
    end: dt.datetime,
) -> str:
    """
    Return a one-line verdict for one instrument under one market data type.
    """
    try:
        iid = InstrumentId.from_str(probe.instrument_id)
    except Exception as e:  # noqa: BLE001 - reported, not raised
        return f"bad instrument id: {e}"

    provider_config = ib.InteractiveBrokersInstrumentProviderConfig(
        symbology_method=getattr(ib.SymbologyMethod, probe.symbology),
        load_ids={iid},
    )
    config = ib.InteractiveBrokersDataClientConfig(
        host=HOST,
        port=PORT,
        client_id=client_id,
        connection_timeout=30,
        request_timeout=45,
        market_data_type=getattr(ib.MarketDataType, market_data_type),
        instrument_provider=provider_config,
    )
    client = ib.HistoricalInteractiveBrokersClient(
        ib.InteractiveBrokersInstrumentProvider(provider_config),
        config,
    )
    try:
        instruments = await client.request_instruments(instrument_ids=[iid])
        if not instruments:
            return "instrument NOT resolved"
        bars = await client.request_bars(
            bar_specifications=[probe.bar_spec],
            end_date_time=end,
            duration="1 D",
            instrument_ids=[iid],
            use_rth=True,
            timeout=45,
        )
    except Exception as e:  # noqa: BLE001 - the error text is the finding
        return f"FAIL: {str(e).splitlines()[0][:76]}"
    else:
        return f"OK - {len(bars)} bars" if bars else "instrument OK, but zero bars"


async def main() -> None:
    """
    Probe every instrument under each requested market data type.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--market-data-types",
        default="REALTIME,DELAYED",
        help="Comma-separated MarketDataType names to test.",
    )
    parser.add_argument("--client-id-base", type=int, default=1200)
    parser.add_argument(
        "--end",
        default=DEFAULT_END.isoformat(),
        help="Historical end datetime (ISO 8601, UTC).",
    )
    args = parser.parse_args()
    end = dt.datetime.fromisoformat(args.end).astimezone(dt.UTC)

    print(f"IB entitlement probe against {HOST}:{PORT}")
    print(f"historical end = {end.isoformat()}\n")

    client_id = args.client_id_base
    for raw_type in args.market_data_types.split(","):
        market_data_type = raw_type.strip().upper()
        print(f"== historical bars, market_data_type={market_data_type} ==")
        for probe in PROBES:
            client_id += 1
            verdict = await probe_historical(probe, market_data_type, client_id, end)
            print(f"  {probe.label:20s} {probe.instrument_id:18s} {verdict}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
