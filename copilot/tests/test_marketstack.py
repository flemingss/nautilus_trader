"""
Tests for Marketstack ingestion.

No test here touches the network. The client is exercised against a stub urlopen so
that pagination and retry — the two behaviours whose failure modes are silent — are
tested deterministically rather than against whatever the provider does today.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from io import BytesIO
from typing import Self
from urllib.error import HTTPError, URLError

import pytest
from copilot.data import marketstack
from copilot.data.marketstack import MarketstackClient, normalize

NOW = datetime(2026, 1, 2, tzinfo=UTC)


def row(
    symbol="AAPL",
    day="2025-06-06",
    *,
    open_=203.0,
    high=205.7,
    low=202.05,
    close=203.92,
    volume=46539200.0,
    split=1.0,
    dividend=0.0,
    **extra: object,
) -> dict[str, object]:
    """A provider row in the shape the live v2 API returns."""
    return {
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "split_factor": split,
        "dividend": dividend,
        "symbol": symbol,
        "exchange": "XNAS",
        "date": f"{day}T00:00:00+0000",
        **extra,
    }


class _StubHttp:
    """Serves canned pages and records the offsets it was asked for."""

    def __init__(self, pages, errors=()) -> None:
        self._pages = list(pages)
        self._errors = list(errors)
        self.offsets: list[int] = []

    def __call__(self, request, timeout=None) -> _Response:
        if self._errors:
            raise self._errors.pop(0)
        offset = int(request.full_url.split("offset=")[1].split("&")[0])
        self.offsets.append(offset)
        page = self._pages.pop(0) if self._pages else []
        total = sum(len(p) for p in [*self._pages, page]) + len(self.offsets[:-1])
        body = json.dumps({"pagination": {"total": total, "limit": 2}, "data": page})
        return _Response(body)


class _Response(BytesIO):
    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __init__(self, body: str) -> None:
        super().__init__(body.encode())


def _client(monkeypatch, stub, **kwargs: object) -> MarketstackClient:
    monkeypatch.setattr(marketstack, "urlopen", stub)
    return MarketstackClient("key", page_limit=2, sleep=lambda _: None, **kwargs)


# --------------------------------------------------------------------------- client


def test_paging_continues_until_a_short_page(monkeypatch):
    """
    A full page cannot be distinguished from the end of the data.

    Stopping at the first page would silently truncate a multi-year backfill to its
    most recent slice while still reporting success.
    """
    stub = _StubHttp([[row(day="2025-06-06"), row(day="2025-06-05")], [row(day="2025-06-04")]])
    rows = _client(monkeypatch, stub).fetch_eod(["AAPL"], date(2025, 6, 1), date(2025, 6, 6))

    assert len(rows) == 3
    assert stub.offsets == [0, 2]


def test_a_short_first_page_ends_the_fetch(monkeypatch):
    stub = _StubHttp([[row()]])
    rows = _client(monkeypatch, stub).fetch_eod(["AAPL"], date(2025, 6, 1), date(2025, 6, 6))

    assert len(rows) == 1
    assert stub.offsets == [0]


def test_a_shortfall_against_the_reported_total_raises(monkeypatch):
    """
    Ending early with the provider still claiming more rows must not look successful.

    A partial history written to the catalog is worse than none: the next run reads
    whatever landed as complete.
    """

    def stub(request, timeout=None):
        body = json.dumps({"pagination": {"total": 900, "limit": 2}, "data": [row()]})
        return _Response(body)

    monkeypatch.setattr(marketstack, "urlopen", stub)
    client = MarketstackClient("key", page_limit=2, sleep=lambda _: None)
    with pytest.raises(ValueError, match="silently incomplete"):
        client.fetch_eod(["AAPL"], date(2025, 6, 1), date(2025, 6, 6))


def test_page_count_is_bounded(monkeypatch):
    stub = _StubHttp([[row(), row()]] * 10)
    client = _client(monkeypatch, stub, max_pages=3)
    with pytest.raises(ValueError, match="exceeded 3 pages"):
        client.fetch_eod(["AAPL"], date(2025, 6, 1), date(2025, 6, 6))


def test_server_errors_are_retried(monkeypatch):
    stub = _StubHttp(
        [[row()]],
        errors=[HTTPError("u", 503, "busy", {}, None), URLError("reset")],
    )
    rows = _client(monkeypatch, stub).fetch_eod(["AAPL"], date(2025, 6, 1), date(2025, 6, 6))
    assert len(rows) == 1


def test_client_errors_are_not_retried(monkeypatch):
    """A 401 means the request is wrong; retrying only re-sends the same mistake."""
    stub = _StubHttp([[row()]], errors=[HTTPError("u", 401, "nope", {}, None)])
    with pytest.raises(HTTPError):
        _client(monkeypatch, stub).fetch_eod(["AAPL"], date(2025, 6, 1), date(2025, 6, 6))


def test_rate_limiting_is_retried(monkeypatch):
    stub = _StubHttp([[row()]], errors=[HTTPError("u", 429, "slow down", {}, None)])
    assert (
        len(_client(monkeypatch, stub).fetch_eod(["AAPL"], date(2025, 6, 1), date(2025, 6, 6))) == 1
    )


def test_retries_are_bounded(monkeypatch):
    stub = _StubHttp([[row()]], errors=[URLError("x")] * 5)
    with pytest.raises(URLError):
        _client(monkeypatch, stub).fetch_eod(["AAPL"], date(2025, 6, 1), date(2025, 6, 6))


def test_no_symbols_makes_no_request(monkeypatch):
    stub = _StubHttp([[row()]])
    assert _client(monkeypatch, stub).fetch_eod([], date(2025, 6, 1), date(2025, 6, 6)) == ()
    assert stub.offsets == []


def test_an_access_key_is_required():
    with pytest.raises(ValueError, match="access key"):
        MarketstackClient("")


# ----------------------------------------------------------------------- normalizer


def test_a_clean_row_becomes_a_bar():
    result = normalize([row()], received_at=NOW)

    assert result.fetched == 1
    assert not result.rejected
    (bar,) = result.bars
    assert bar.symbol == "AAPL"
    assert bar.close == Decimal("203.92")
    assert bar.volume == 46539200
    assert bar.closed_at == datetime(2025, 6, 6, tzinfo=UTC)


def test_prices_keep_their_exact_decimal_value():
    """Parsed through str, not float: 126.5225 must not become 126.52249999999999."""
    result = normalize(
        [row(open_=126.0, high=127.0, low=125.0, close=126.5225)],
        received_at=NOW,
    )
    assert result.bars[0].close == Decimal("126.5225")


def test_a_phantom_holiday_row_is_rejected():
    """
    The defect this gate exists for.

    SPY really does come back with a full bar for Thanksgiving 2023, volume and all.
    """
    result = normalize([row(symbol="SPY", day="2023-11-23")], received_at=NOW)

    assert not result.bars
    assert [r.reason for r in result.rejected] == ["non_trading_day"]


def test_the_trading_day_gate_can_be_turned_off():
    result = normalize([row(day="2023-11-23")], received_at=NOW, require_trading_day=False)
    assert len(result.bars) == 1


def test_a_bar_whose_close_is_outside_its_range_is_rejected():
    """
    The exact defect that makes the vendor's adjusted OHLC set unusable.

    AAPL 2022-11-03 reports adj_close 138.65 under an adj_low of 138.75. A backtest
    fed that bar fills at a price the bar itself says never traded.
    """
    result = normalize(
        [row(day="2022-11-03", open_=142.06, high=142.8, low=138.75, close=138.65)],
        received_at=NOW,
    )

    assert not result.bars
    assert result.rejected[0].reason.startswith("schema_or_value_error")
    assert "incoherent OHLC" in result.rejected[0].reason


@pytest.mark.parametrize(
    ("bad", "why"),
    [
        ({"close": None}, "null price"),
        ({"close": "not-a-number"}, "unparseable price"),
        ({"close": float("nan")}, "non-finite price"),
        ({"open_": -1.0}, "negative price"),
        ({"close": 0.0}, "zero price"),
        ({"volume": 1.5}, "fractional volume"),
        ({"volume": -5}, "negative volume"),
        ({"symbol": ""}, "missing symbol"),
    ],
)
def test_malformed_rows_are_rejected_not_raised(bad, why):
    """One bad row must not take the batch down with it."""
    result = normalize([row(**bad), row(symbol="MSFT")], received_at=NOW)

    assert len(result.bars) == 1, why
    assert len(result.rejected) == 1, why


def test_a_missing_split_factor_is_rejected_rather_than_assumed():
    """Defaulting an absent split to 1 would pass a re-based series off as continuous."""
    incomplete = row()
    del incomplete["split_factor"]
    result = normalize([incomplete], received_at=NOW)

    assert not result.bars
    assert "split_factor" in result.rejected[0].reason


def test_a_future_bar_is_rejected():
    result = normalize([row(day="2027-01-04")], received_at=NOW)
    assert [r.reason for r in result.rejected] == ["future_bar"]


def test_duplicate_bars_are_rejected_keeping_the_first():
    first = row(close=203.92)
    second = row(close=202.50)  # coherent, so only the duplicate gate can reject it
    result = normalize([first, second], received_at=NOW)

    assert len(result.bars) == 1
    assert result.bars[0].close == Decimal("203.92")
    assert [r.reason for r in result.rejected] == ["duplicate_bar"]


def test_rows_are_sorted_regardless_of_provider_order():
    """Marketstack returns newest-first; everything downstream assumes chronological."""
    result = normalize(
        [row(day="2025-06-06"), row(day="2025-06-04"), row(day="2025-06-05")],
        received_at=NOW,
    )
    closes = [b.closed_at.date() for b in result.bars]
    assert closes == sorted(closes)


def test_symbols_are_kept_in_separate_series():
    """Interleaved symbols must not read as one non-monotonic series."""
    result = normalize(
        [row(symbol="AAPL", day="2025-06-06"), row(symbol="MSFT", day="2025-06-05")],
        received_at=NOW,
    )
    assert len(result.bars) == 2
    assert not result.rejected


def test_splits_and_dividends_are_reported():
    result = normalize(
        [
            row(day="2020-08-31", split=4.0),
            row(symbol="MSFT", day="2025-05-12", dividend=0.26),
        ],
        received_at=NOW,
    )

    actions = {(a.symbol, a.split_factor, a.dividend_amount) for a in result.corporate_actions}
    assert actions == {
        ("AAPL", Decimal(4), Decimal(0)),
        ("MSFT", Decimal(1), Decimal("0.26")),
    }


def test_an_action_on_a_rejected_row_is_not_reported():
    """Reporting a split whose bar never landed sends someone hunting a phantom rebase."""
    result = normalize([row(day="2023-11-23", split=4.0)], received_at=NOW)

    assert not result.bars
    assert result.corporate_actions == ()


def test_currency_tags_are_collected_but_not_trusted():
    """
    The vendor tags the same continuous USD series USD, usd, EUR and nothing at all.

    Collected for reporting so a genuinely non-USD symbol has somewhere to surface,
    never used to decide what gets stored.
    """
    result = normalize(
        [
            row(day="2025-07-25", price_currency="USD"),
            row(day="2025-07-28", price_currency="EUR"),
            row(day="2025-07-29", price_currency="usd"),
            row(day="2025-07-30"),
        ],
        received_at=NOW,
    )

    assert len(result.bars) == 4
    assert result.currency_tags == {"AAPL": ("EUR", "USD")}


def test_rejection_ratio_counts_against_what_was_fetched():
    result = normalize([row(), row(day="2023-11-23"), row(day="2024-03-29")], received_at=NOW)

    assert result.fetched == 3
    assert result.rejection_ratio == Decimal(2) / Decimal(3)


def test_an_empty_fetch_has_no_rejection_ratio():
    """An empty window is a holiday, not a mass rejection; it must not divide by zero."""
    assert normalize([], received_at=NOW).rejection_ratio == Decimal(0)
