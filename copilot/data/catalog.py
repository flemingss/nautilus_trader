"""
Write Marketstack daily bars into a Nautilus ``ParquetDataCatalog``, and read them back.

The catalog is the seam between ingestion and everything downstream. Once bars are in
it, the same series feeds ``BacktestEngine`` through ``copilot.validation.nautilus_replay``
and the walk-forward gate, without either of them knowing a vendor was involved.

Precision
---------
``PRICE_PRECISION`` is 4 and that number is measured, not chosen. Across 63,404 price
values (AAPL, MSFT and SPY, 2005-2025) the vendor never returns more than four decimal
places: the histogram is 951 values at 0 dp, 5,195 at 1, 41,009 at 2, 3,325 at 3 and
12,924 at 4. Four therefore stores the vendor's numbers exactly.

Exactness is enforced rather than trusted. ``Price`` rounds to the instrument's
precision, so a vendor that started returning a fifth decimal would silently shift
prices in a stored history that later runs treat as ground truth. :func:`to_nautilus_bars`
compares every converted value against its source and raises on any disagreement.

Venue
-----
Taken from the vendor's ``exchange`` MIC (``XNAS`` for AAPL, ``ARCX`` for SPY) so an
instrument id names where the series actually came from. The MIC is required to be
constant across a symbol's history; a change means a relisting, which re-bases prices
the same way a split does and is not something to merge into one series silently.

"""

from __future__ import annotations

from collections.abc import Iterable
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from copilot.data.corporate_actions import SPLIT
from copilot.data.corporate_actions import cumulative_factor
from copilot.data.corporate_actions import pending_for
from copilot.validation.types import DailyBar
from nautilus_trader.model import Bar
from nautilus_trader.model import BarType
from nautilus_trader.model import Currency
from nautilus_trader.model import Equity
from nautilus_trader.model import InstrumentId
from nautilus_trader.model import Price
from nautilus_trader.model import Quantity
from nautilus_trader.model import Symbol
from nautilus_trader.model import Venue
from nautilus_trader.persistence import ParquetDataCatalog


if TYPE_CHECKING:
    from copilot.data.marketstack import IngestionResult

PRICE_PRECISION = 4
"""
Decimal places stored for a price.

Measured against the vendor; see module docstring.

"""

PRICE_INCREMENT = "0.0001"
DEFAULT_CURRENCY = "USD"
BAR_SPEC = "1-DAY-LAST-EXTERNAL"
"""
Daily bars, last-price aggregated, sourced externally rather than aggregated by the
engine - which is what these are: the venue's own official daily summary.
"""


@dataclass(frozen=True)
class WriteReport:
    """
    What one catalog write actually persisted, per symbol.
    """

    instrument_id: str
    bar_type: str
    bars_written: int
    first: datetime | None
    last: datetime | None


def bar_type_for(instrument_id: InstrumentId) -> BarType:
    """
    Return the daily bar type this overlay stores for an instrument.
    """
    return BarType.from_str(f"{instrument_id}-{BAR_SPEC}")


def equity_for(
    symbol: str,
    venue: str,
    *,
    currency: str = DEFAULT_CURRENCY,
    ts_init: int = 0,
) -> Equity:
    """
    Build the instrument a stored series belongs to.

    ``currency`` is a caller decision, not read from the vendor rows: Marketstack's
    ``price_currency`` is unreliable (see ``marketstack.DEFAULT_BASE_URL``).

    """
    instrument_id = InstrumentId(Symbol(symbol), Venue(venue))
    return Equity(
        instrument_id=instrument_id,
        raw_symbol=Symbol(symbol),
        currency=Currency.from_str(currency),
        price_precision=PRICE_PRECISION,
        price_increment=Price.from_str(PRICE_INCREMENT),
        ts_event=ts_init,
        ts_init=ts_init,
    )


def to_nautilus_bars(
    bars: Sequence[DailyBar],
    instrument: Equity,
    bar_type: BarType,
) -> list[Bar]:
    """
    Convert gate bars into Nautilus bars, refusing to round anything away.

    A ``Price`` carries a fixed precision, so constructing one from a value with more
    decimal places quietly rounds it. That is tolerable in a transient replay and not
    tolerable in a stored history, which later runs read as ground truth without ever
    seeing the source. Every field is therefore compared back against its input.

    """
    out: list[Bar] = []
    for daily in sorted(bars, key=lambda b: b.closed_at):
        ts = _to_ns(daily.closed_at)
        prices = {
            "open": (daily.open, instrument.make_price(daily.open)),
            "high": (daily.high, instrument.make_price(daily.high)),
            "low": (daily.low, instrument.make_price(daily.low)),
            "close": (daily.close, instrument.make_price(daily.close)),
        }
        for field, (source, converted) in prices.items():
            if Decimal(str(converted)) != source:
                raise ValueError(
                    f"{daily.symbol} {daily.closed_at.date()} {field}: {source} does not "
                    f"survive {PRICE_PRECISION}-dp conversion (became {converted}). "
                    "Raise PRICE_PRECISION rather than storing a rounded price.",
                )
        quantity = Quantity.from_int(daily.volume)
        # Compared through `str`, never `as_double`. `Quantity` holds the value
        # exactly, but `as_double` is an f64 conversion: AAPL's 1,020,062,400-share
        # session on 2005-02-02 comes back as 1020062399.9999999, and an earlier
        # version of this guard rejected a volume the catalog would have stored
        # perfectly well.
        if Decimal(str(quantity)) != Decimal(daily.volume):
            raise ValueError(
                f"{daily.symbol} {daily.closed_at.date()} volume {daily.volume} "
                f"does not survive conversion (became {quantity})",
            )

        out.append(
            Bar(
                bar_type=bar_type,
                open=prices["open"][1],
                high=prices["high"][1],
                low=prices["low"][1],
                close=prices["close"][1],
                volume=quantity,
                ts_event=ts,
                ts_init=ts,
            ),
        )
    return out


def write_ingestion(
    catalog: ParquetDataCatalog,
    result: IngestionResult,
    *,
    venues: dict[str, str],
    currency: str = DEFAULT_CURRENCY,
) -> tuple[WriteReport, ...]:
    """
    Persist one ingestion result, one instrument and bar series per symbol.

    The instrument is written alongside its bars. A catalog holding bars for an
    instrument it cannot describe is only half a dataset - ``BacktestNode`` needs both,
    and discovering the instrument is missing at run time is a much worse place to find
    out than here.

    """
    reports: list[WriteReport] = []
    for symbol in sorted({bar.symbol for bar in result.bars}):
        venue = venues.get(symbol)
        if venue is None:
            raise KeyError(f"no venue known for {symbol}; cannot build an instrument id")

        symbol_bars = [bar for bar in result.bars if bar.symbol == symbol]
        instrument = equity_for(symbol, venue, currency=currency)
        bar_type = bar_type_for(instrument.id)

        catalog.write_instruments([instrument])
        catalog.write_bars(to_nautilus_bars(symbol_bars, instrument, bar_type))

        closes = sorted(bar.closed_at for bar in symbol_bars)
        reports.append(
            WriteReport(
                instrument_id=str(instrument.id),
                bar_type=str(bar_type),
                bars_written=len(symbol_bars),
                first=closes[0],
                last=closes[-1],
            ),
        )
    return tuple(reports)


def read_daily_bars(
    catalog: ParquetDataCatalog,
    bar_type: BarType,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    adjust: bool = True,
) -> tuple[DailyBar, ...]:
    """
    Read a stored series back as the gate's ``DailyBar``, corporate actions applied.

    Prices come back through ``str`` into ``Decimal`` rather than through ``float``:
    the whole point of storing at a fixed precision is that the value is exact, and a
    float round trip would give that away on the last step.

    **Adjustment happens here rather than in the stored file.** The vendor's series is
    as-traded for every symbol but AAPL, so a split sits in it as a real discontinuity -
    GOOGL moves -95% on 2022-07-18 - which a gap strategy reads as its largest gap ever.
    Correcting that by rewriting the catalog would have destroyed the only property that
    let it be found: as-traded prices can be checked against a venue's official closing
    auction print, and back-adjusted ones cannot. So the file stays faithful to the
    vendor, the correction is a versioned table in
    :mod:`copilot.data.corporate_actions`, and ``adjust=False`` reads the raw series
    back for that audit.

    Volume is scaled by the share-count factor so the day's traded notional, price times
    volume, is unchanged by the adjustment. A distribution does not change the share
    count and so does not touch volume.

    """
    symbol = bar_type.instrument_id.symbol.value
    stored = catalog.query_bars(
        [str(bar_type)],
        start=_to_ns(start) if start else None,
        end=_to_ns(end) if end else None,
    )
    pending = pending_for(symbol) if adjust else ()
    shares = tuple(a for a in pending if a.kind is SPLIT)

    bars = []
    for bar in stored:
        closed_at = datetime.fromtimestamp(bar.ts_event / 1e9, tz=UTC)
        price_factor = cumulative_factor(pending, closed_at)
        share_factor = cumulative_factor(shares, closed_at)
        bars.append(
            DailyBar(
                symbol=symbol,
                closed_at=closed_at,
                open=Decimal(str(bar.open)) / price_factor,
                high=Decimal(str(bar.high)) / price_factor,
                low=Decimal(str(bar.low)) / price_factor,
                close=Decimal(str(bar.close)) / price_factor,
                volume=int(Decimal(str(bar.volume)) * share_factor),
            ),
        )
    return tuple(bars)


def venues_from_rows(rows: Iterable[dict[str, object]]) -> dict[str, str]:
    """
    Map each symbol to its exchange MIC, requiring one MIC per symbol.

    A symbol reported on two exchanges across one history is a relisting, not a detail
    to average over: the two series are not continuous with each other.

    """
    seen: dict[str, set[str]] = {}
    for row in rows:
        symbol, exchange = row.get("symbol"), row.get("exchange")
        if isinstance(symbol, str) and isinstance(exchange, str) and exchange:
            seen.setdefault(symbol.upper(), set()).add(exchange.upper())

    conflicted = {s: sorted(v) for s, v in seen.items() if len(v) > 1}
    if conflicted:
        raise ValueError(
            f"symbols reported on more than one exchange: {conflicted}. "
            "Ingest each listing separately rather than merging discontinuous series.",
        )
    return {symbol: next(iter(mics)) for symbol, mics in seen.items()}


def open_catalog(path: str | Path) -> ParquetDataCatalog:
    """
    Open (creating if needed) the catalog at ``path``.
    """
    base = Path(path).expanduser().resolve()
    base.mkdir(parents=True, exist_ok=True)
    return ParquetDataCatalog(str(base))


def _to_ns(moment: datetime) -> int:
    return int(moment.timestamp() * 1_000_000_000)


__all__ = [
    "BAR_SPEC",
    "DEFAULT_CURRENCY",
    "PRICE_PRECISION",
    "WriteReport",
    "bar_type_for",
    "equity_for",
    "open_catalog",
    "read_daily_bars",
    "to_nautilus_bars",
    "venues_from_rows",
    "write_ingestion",
]
