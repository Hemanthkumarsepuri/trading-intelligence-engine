"""DhanProvider tests — exercised entirely against `httpx.MockTransport` with
JSON payloads matching Dhan's documented v2 response shapes (confirmed
2026-08-27 against docs.dhanhq.co/dhanhq.co/docs/v2). No network call is ever
made; see docs/data-sources/PROVIDERS.md for the source docs.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, date, datetime

import httpx
import pytest

from app.data.providers.base import RawCandle, RawOptionChain, RawQuote
from app.data.providers.dhan_provider import DhanProvider
from app.data.providers.exceptions import (
    ProviderMalformedResponse,
    ProviderRateLimited,
    ProviderUnavailable,
)
from app.domain.market.models import ExchangeSegment, OptionRight, Timeframe


def _provider(handler: Callable[[httpx.Request], httpx.Response]) -> DhanProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return DhanProvider(client=client, client_id="cid", access_token="tok")


def test_get_ohlcv_intraday_parses_parallel_arrays() -> None:
    body = {
        "open": [100.0, 101.0],
        "high": [102.0, 103.0],
        "low": [99.0, 100.0],
        "close": [101.0, 102.0],
        "volume": [1000, 1200],
        "timestamp": [1756289700, 1756290000],
        "open_interest": [0, 0],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/charts/intraday"
        assert request.headers["access-token"] == "tok"
        assert request.headers["client-id"] == "cid"
        return httpx.Response(200, json=body)

    provider = _provider(handler)

    async def run() -> list[RawCandle]:
        return await provider.get_ohlcv(
            security_id="1333",
            exchange_segment=ExchangeSegment.NSE_EQ,
            timeframe=Timeframe.M5,
            start=datetime(2026, 8, 27, 9, 0, tzinfo=UTC),
            end=datetime(2026, 8, 27, 10, 0, tzinfo=UTC),
            as_of=datetime(2026, 8, 27, 10, 0, tzinfo=UTC),
        )

    candles = asyncio.run(run())
    assert len(candles) == 2
    assert candles[0].open == 100.0
    assert candles[0].volume == 1000
    assert candles[1].open_interest == 0


def test_get_ohlcv_daily_uses_historical_endpoint() -> None:
    body = {
        "open": [100.0],
        "high": [102.0],
        "low": [99.0],
        "close": [101.0],
        "volume": [1000],
        "timestamp": [1756289700],
        "open_interest": [],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/charts/historical"
        return httpx.Response(200, json=body)

    provider = _provider(handler)

    async def run() -> list[RawCandle]:
        return await provider.get_ohlcv(
            security_id="1333",
            exchange_segment=ExchangeSegment.NSE_EQ,
            timeframe=Timeframe.D1,
            start=datetime(2026, 8, 1, tzinfo=UTC),
            end=datetime(2026, 8, 27, tzinfo=UTC),
            as_of=datetime(2026, 8, 27, tzinfo=UTC),
        )

    candles = asyncio.run(run())
    assert len(candles) == 1
    assert candles[0].open_interest is None  # empty OI array -> not populated


def test_get_ohlcv_rejects_mismatched_array_lengths() -> None:
    body = {"open": [1.0], "high": [1.0, 2.0], "low": [1.0], "close": [1.0], "volume": [1], "timestamp": [1756289700]}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    provider = _provider(handler)

    async def run() -> None:
        await provider.get_ohlcv(
            security_id="1333",
            exchange_segment=ExchangeSegment.NSE_EQ,
            timeframe=Timeframe.M5,
            start=datetime(2026, 8, 27, 9, 0, tzinfo=UTC),
            end=datetime(2026, 8, 27, 10, 0, tzinfo=UTC),
            as_of=datetime(2026, 8, 27, 10, 0, tzinfo=UTC),
        )

    with pytest.raises(ProviderMalformedResponse):
        asyncio.run(run())


def test_get_ohlcv_rejects_unsupported_timeframe() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not call the network for an unsupported timeframe")

    provider = _provider(handler)

    async def run() -> None:
        await provider.get_ohlcv(
            security_id="1333",
            exchange_segment=ExchangeSegment.NSE_EQ,
            timeframe=Timeframe.M30,
            start=datetime(2026, 8, 27, 9, 0, tzinfo=UTC),
            end=datetime(2026, 8, 27, 10, 0, tzinfo=UTC),
            as_of=datetime(2026, 8, 27, 10, 0, tzinfo=UTC),
        )

    with pytest.raises(ProviderMalformedResponse):
        asyncio.run(run())


def test_get_ohlcv_never_returns_candle_beyond_as_of() -> None:
    body = {
        "open": [100.0, 101.0],
        "high": [102.0, 103.0],
        "low": [99.0, 100.0],
        "close": [101.0, 102.0],
        "volume": [1000, 1200],
        "timestamp": [1756289700, 1756290000],
        "open_interest": [],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    provider = _provider(handler)
    cutoff = datetime.fromtimestamp(1756289700, tz=UTC)

    async def run() -> list[RawCandle]:
        return await provider.get_ohlcv(
            security_id="1333",
            exchange_segment=ExchangeSegment.NSE_EQ,
            timeframe=Timeframe.M5,
            start=datetime(2026, 8, 27, 9, 0, tzinfo=UTC),
            end=datetime(2026, 8, 27, 10, 0, tzinfo=UTC),
            as_of=cutoff,
        )

    candles = asyncio.run(run())
    assert len(candles) == 1
    assert candles[0].timestamp <= cutoff


def test_get_quote_parses_documented_envelope() -> None:
    body = {
        "data": {
            "NSE_EQ": {
                "11536": {
                    "last_price": 4525.55,
                    "volume": 12345,
                    "oi": 0,
                    "ohlc": {"open": 4521.45, "close": 4507.85, "high": 4530, "low": 4500},
                }
            }
        },
        "status": "success",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/marketfeed/quote"
        return httpx.Response(200, json=body)

    provider = _provider(handler)

    async def run() -> RawQuote:
        return await provider.get_quote(
            security_id="11536", exchange_segment=ExchangeSegment.NSE_EQ, as_of=datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
        )

    quote = asyncio.run(run())
    assert quote.last_price == 4525.55
    assert quote.previous_close == 4507.85
    assert quote.volume == 12345


def test_get_quote_rate_limited_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "too many requests"})

    provider = _provider(handler)

    async def run() -> None:
        await provider.get_quote(
            security_id="11536", exchange_segment=ExchangeSegment.NSE_EQ, as_of=datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
        )

    with pytest.raises(ProviderRateLimited):
        asyncio.run(run())


def test_get_quote_server_error_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    provider = _provider(handler)

    async def run() -> None:
        await provider.get_quote(
            security_id="11536", exchange_segment=ExchangeSegment.NSE_EQ, as_of=datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
        )

    with pytest.raises(ProviderUnavailable):
        asyncio.run(run())


def test_get_chain_parses_documented_envelope() -> None:
    body = {
        "data": {
            "last_price": 25642.8,
            "oc": {
                "25650.000000": {
                    "ce": {
                        "greeks": {"delta": 0.53871, "theta": -15.1539, "gamma": 0.00132, "vega": 12.18593},
                        "implied_volatility": 9.789193798280868,
                        "last_price": 134,
                        "oi": 3786445,
                        "previous_oi": 402220,
                        "security_id": 42528,
                        "top_ask_price": 134,
                        "top_bid_price": 133.55,
                        "volume": 117567970,
                    },
                    "pe": {
                        "greeks": {"delta": -0.46732, "theta": -10.61131, "gamma": 0.00109, "vega": 12.2025},
                        "implied_volatility": 11.939337251984934,
                        "last_price": 132.8,
                        "oi": 3096145,
                        "previous_oi": 2327260,
                        "security_id": 42529,
                        "top_ask_price": 132.75,
                        "top_bid_price": 132.45,
                        "volume": 157009970,
                    },
                }
            },
        },
        "status": "success",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/optionchain"
        return httpx.Response(200, json=body)

    provider = _provider(handler)

    async def run() -> RawOptionChain:
        return await provider.get_chain(
            underlying="13", expiry=date(2026, 9, 25), as_of=datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
        )

    chain = asyncio.run(run())
    assert chain.underlying_last_price == 25642.8
    legs = chain.strikes[25650.0]
    assert len(legs) == 2
    ce = next(leg for leg in legs if leg.right == OptionRight.CE)
    assert ce.open_interest == 3786445
    assert ce.delta == 0.53871
    assert ce.security_id == "42528"


def test_get_expiries_parses_documented_envelope() -> None:
    body = {"data": ["2026-09-25", "2026-10-02"], "status": "success"}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/optionchain/expirylist"
        return httpx.Response(200, json=body)

    provider = _provider(handler)

    async def run() -> list[date]:
        return await provider.get_expiries(underlying="13", as_of=datetime(2026, 8, 27, 10, 0, tzinfo=UTC))

    expiries = asyncio.run(run())
    assert expiries == [date(2026, 9, 25), date(2026, 10, 2)]


def test_non_json_body_raises_malformed_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    provider = _provider(handler)

    async def run() -> None:
        await provider.get_expiries(underlying="13", as_of=datetime(2026, 8, 27, 10, 0, tzinfo=UTC))

    with pytest.raises(ProviderMalformedResponse):
        asyncio.run(run())
