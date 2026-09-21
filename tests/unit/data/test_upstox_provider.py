"""UpstoxProvider tests — exercised entirely against `httpx.MockTransport`
with JSON payloads matching Upstox's documented v3 response shapes
(confirmed 2026-08-28 against upstox.com/developer/api-documentation/v3/).
No network call is ever made; see docs/data-sources/PROVIDER_DECISION.md.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, date, datetime

import httpx
import pytest

from app.data.providers.base import RawCandle, RawOptionChain, RawQuote
from app.data.providers.exceptions import (
    ProviderMalformedResponse,
    ProviderRateLimited,
    ProviderUnavailable,
)
from app.data.providers.upstox_provider import UpstoxProvider
from app.domain.market.models import ExchangeSegment, OptionRight, Timeframe

INSTRUMENT_KEY = "NSE_EQ|INE002A01018"


def _provider(handler: Callable[[httpx.Request], httpx.Response]) -> UpstoxProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return UpstoxProvider(client=client, access_token="tok")


def test_get_ohlcv_parses_row_shaped_candles_and_uses_bearer_auth() -> None:
    body = {
        "status": "success",
        "data": {
            "candles": [
                ["2026-08-27T09:15:00+05:30", 100.0, 102.0, 99.0, 101.0, 1000, 0],
                ["2026-08-27T09:30:00+05:30", 101.0, 103.0, 100.0, 102.0, 1200, 0],
            ]
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer tok"
        assert "/v3/historical-candle/" in request.url.path
        assert "minutes/15" in request.url.path
        return httpx.Response(200, json=body)

    provider = _provider(handler)

    async def run() -> list[RawCandle]:
        return await provider.get_ohlcv(
            security_id=INSTRUMENT_KEY,
            exchange_segment=ExchangeSegment.NSE_EQ,
            timeframe=Timeframe.M15,
            start=datetime(2026, 8, 27, 0, 0, tzinfo=UTC),
            end=datetime(2026, 8, 27, 23, 59, tzinfo=UTC),
            as_of=datetime(2026, 8, 27, 23, 59, tzinfo=UTC),
        )

    candles = asyncio.run(run())
    assert len(candles) == 2
    assert candles[0].open == 100.0
    assert candles[0].volume == 1000
    assert candles[1].close == 102.0


def test_get_ohlcv_never_returns_candle_beyond_as_of() -> None:
    body = {
        "status": "success",
        "data": {
            "candles": [
                ["2026-08-27T09:15:00+05:30", 100.0, 102.0, 99.0, 101.0, 1000, 0],
                ["2026-08-27T09:30:00+05:30", 101.0, 103.0, 100.0, 102.0, 1200, 0],
            ]
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    provider = _provider(handler)
    cutoff = datetime.fromisoformat("2026-08-27T09:15:00+05:30")

    async def run() -> list[RawCandle]:
        return await provider.get_ohlcv(
            security_id=INSTRUMENT_KEY,
            exchange_segment=ExchangeSegment.NSE_EQ,
            timeframe=Timeframe.M15,
            start=datetime(2026, 8, 27, 0, 0, tzinfo=UTC),
            end=datetime(2026, 8, 27, 23, 59, tzinfo=UTC),
            as_of=cutoff,
        )

    candles = asyncio.run(run())
    assert len(candles) == 1


def test_get_ohlcv_merges_intraday_current_day_bars() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/intraday/" in request.url.path:
            return httpx.Response(200, json={
                "status": "success",
                "data": {"candles": [["2026-09-21T14:45:00+05:30", 1245.0, 1246.0, 1244.0, 1245.5, 9000, 0]]},
            })
        return httpx.Response(200, json={
            "status": "success",
            "data": {"candles": [["2026-09-18T15:15:00+05:30", 1226.0, 1227.0, 1225.0, 1226.4, 1000, 0]]},
        })

    provider = _provider(handler)

    async def run() -> list[RawCandle]:
        return await provider.get_ohlcv(
            security_id=INSTRUMENT_KEY,
            exchange_segment=ExchangeSegment.NSE_EQ,
            timeframe=Timeframe.M15,
            start=datetime(2026, 9, 1, tzinfo=UTC),
            end=datetime(2026, 9, 21, 9, 30, tzinfo=UTC),
            as_of=datetime(2026, 9, 21, 9, 30, tzinfo=UTC),
        )

    candles = asyncio.run(run())
    assert [c.close for c in candles] == [1226.4, 1245.5]


def test_get_ohlcv_keeps_historical_when_intraday_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/intraday/" in request.url.path:
            return httpx.Response(503, text="intraday unavailable")
        return httpx.Response(200, json={
            "status": "success",
            "data": {"candles": [["2026-09-18T15:15:00+05:30", 1226.0, 1227.0, 1225.0, 1226.4, 1000, 0]]},
        })

    provider = _provider(handler)
    candles = asyncio.run(provider.get_ohlcv(
        security_id=INSTRUMENT_KEY, exchange_segment=ExchangeSegment.NSE_EQ, timeframe=Timeframe.M15,
        start=datetime(2026, 9, 1, tzinfo=UTC), end=datetime(2026, 9, 21, 9, 30, tzinfo=UTC),
        as_of=datetime(2026, 9, 21, 9, 30, tzinfo=UTC),
    ))
    assert len(candles) == 1
    assert candles[0].close == 1226.4


def test_get_ohlcv_sorts_ascending_regardless_of_response_order() -> None:
    body = {
        "status": "success",
        "data": {
            "candles": [
                ["2026-08-27T09:30:00+05:30", 101.0, 103.0, 100.0, 102.0, 1200, 0],
                ["2026-08-27T09:15:00+05:30", 100.0, 102.0, 99.0, 101.0, 1000, 0],
            ]
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    provider = _provider(handler)

    async def run() -> list[RawCandle]:
        return await provider.get_ohlcv(
            security_id=INSTRUMENT_KEY,
            exchange_segment=ExchangeSegment.NSE_EQ,
            timeframe=Timeframe.M15,
            start=datetime(2026, 8, 27, 0, 0, tzinfo=UTC),
            end=datetime(2026, 8, 27, 23, 59, tzinfo=UTC),
            as_of=datetime(2026, 8, 27, 23, 59, tzinfo=UTC),
        )

    candles = asyncio.run(run())
    assert candles[0].timestamp < candles[1].timestamp


def test_get_ohlcv_rejects_unsupported_timeframe() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not call the network for an unsupported timeframe")

    provider = _provider(handler)

    async def run() -> None:
        await provider.get_ohlcv(
            security_id=INSTRUMENT_KEY,
            exchange_segment=ExchangeSegment.NSE_EQ,
            timeframe=Timeframe.M30,
            start=datetime(2026, 8, 27, 0, 0, tzinfo=UTC),
            end=datetime(2026, 8, 27, 23, 59, tzinfo=UTC),
            as_of=datetime(2026, 8, 27, 23, 59, tzinfo=UTC),
        )

    with pytest.raises(ProviderMalformedResponse):
        asyncio.run(run())


def test_get_ohlcv_instrument_key_with_pipe_is_url_encoded() -> None:
    seen_raw_paths: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_raw_paths.append(bytes(request.url.raw_path))
        return httpx.Response(200, json={"status": "success", "data": {"candles": []}})

    provider = _provider(handler)

    async def run() -> None:
        await provider.get_ohlcv(
            security_id=INSTRUMENT_KEY,
            exchange_segment=ExchangeSegment.NSE_EQ,
            timeframe=Timeframe.M15,
            start=datetime(2026, 8, 27, 0, 0, tzinfo=UTC),
            end=datetime(2026, 8, 27, 23, 59, tzinfo=UTC),
            as_of=datetime(2026, 8, 27, 23, 59, tzinfo=UTC),
        )

    asyncio.run(run())
    assert b"%7C" in seen_raw_paths[0] or b"%7c" in seen_raw_paths[0]  # pipe is percent-encoded on the wire
    assert b"|" not in seen_raw_paths[0]  # raw, unencoded pipe must never be sent
    assert b"NSE_EQ" in seen_raw_paths[0]


def test_get_ohlcv_non_success_status_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "error", "data": {}})

    provider = _provider(handler)

    async def run() -> None:
        await provider.get_ohlcv(
            security_id=INSTRUMENT_KEY,
            exchange_segment=ExchangeSegment.NSE_EQ,
            timeframe=Timeframe.M15,
            start=datetime(2026, 8, 27, 0, 0, tzinfo=UTC),
            end=datetime(2026, 8, 27, 23, 59, tzinfo=UTC),
            as_of=datetime(2026, 8, 27, 23, 59, tzinfo=UTC),
        )

    with pytest.raises(ProviderMalformedResponse):
        asyncio.run(run())


def test_get_ohlcv_rate_limited_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "too many requests"})

    provider = _provider(handler)

    async def run() -> None:
        await provider.get_ohlcv(
            security_id=INSTRUMENT_KEY,
            exchange_segment=ExchangeSegment.NSE_EQ,
            timeframe=Timeframe.M15,
            start=datetime(2026, 8, 27, 0, 0, tzinfo=UTC),
            end=datetime(2026, 8, 27, 23, 59, tzinfo=UTC),
            as_of=datetime(2026, 8, 27, 23, 59, tzinfo=UTC),
        )

    with pytest.raises(ProviderRateLimited):
        asyncio.run(run())


def test_get_ohlcv_server_error_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    provider = _provider(handler)

    async def run() -> None:
        await provider.get_ohlcv(
            security_id=INSTRUMENT_KEY,
            exchange_segment=ExchangeSegment.NSE_EQ,
            timeframe=Timeframe.M15,
            start=datetime(2026, 8, 27, 0, 0, tzinfo=UTC),
            end=datetime(2026, 8, 27, 23, 59, tzinfo=UTC),
            as_of=datetime(2026, 8, 27, 23, 59, tzinfo=UTC),
        )

    with pytest.raises(ProviderUnavailable):
        asyncio.run(run())


def _real_quote_entry_body(*, symbol: str = "RELIANCE", instrument_key: str = INSTRUMENT_KEY) -> dict[str, object]:
    # Shape matches the REAL, live `/v2/market-quote/quotes` response,
    # verified 2026-08-28 (RELIANCE) -- not a guessed schema.
    return {
        "status": "success",
        "data": {
            f"NSE_EQ:{symbol}": {
                "ohlc": {"open": 1284.9, "high": 1291.8, "low": 1280.0, "close": 1287.0},
                "timestamp": "2026-08-28T23:04:06.957+05:30",
                "instrument_token": instrument_key,
                "symbol": symbol,
                "last_price": 1287.0,
                "volume": 6830228,
                "average_price": 1284.51,
                "oi": 0.0,
                "net_change": 4.8,
                "last_trade_time": "1787912999563",
            }
        },
    }


def test_get_quote_parses_real_response_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/market-quote/quotes"
        assert request.url.params["instrument_key"] == INSTRUMENT_KEY
        return httpx.Response(200, json=_real_quote_entry_body())

    provider = _provider(handler)

    async def run() -> RawQuote:
        return await provider.get_quote(
            security_id=INSTRUMENT_KEY, exchange_segment=ExchangeSegment.NSE_EQ, as_of=datetime.now(UTC)
        )

    raw_quote = asyncio.run(run())
    assert raw_quote.security_id == INSTRUMENT_KEY
    assert raw_quote.last_price == 1287.0
    assert raw_quote.previous_close == pytest.approx(1282.2)  # last_price - net_change
    assert raw_quote.volume == 6830228
    assert raw_quote.exchange_timestamp is not None
    assert raw_quote.exchange_timestamp.isoformat() == "2026-08-28T10:29:59.563000+00:00"


def test_get_quotes_batch_resolves_multiple_instruments_by_instrument_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["instrument_key"] == "A,B"
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "NSE_EQ:X": {"instrument_token": "A", "last_price": 100.0, "net_change": 1.0},
                    "NSE_EQ:Y": {"instrument_token": "B", "last_price": 200.0, "net_change": -2.0},
                },
            },
        )

    provider = _provider(handler)

    async def run() -> dict[str, RawQuote]:
        return await provider.get_quotes(["A", "B"])

    quotes = asyncio.run(run())
    assert set(quotes) == {"A", "B"}
    assert quotes["A"].last_price == 100.0
    assert quotes["B"].previous_close == pytest.approx(202.0)


def test_get_quotes_parses_real_ohlc_and_buy_sell_quantity_fields() -> None:
    """Daily Researcher early-stage discovery -- real fields confirmed
    live 2026-08-31 (RELIANCE) on the SAME batched quote response this
    codebase already fetches, previously unparsed."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "NSE_EQ:RELIANCE": {
                        "instrument_token": "A", "last_price": 1278.3, "net_change": -8.7,
                        "volume": 3147068, "average_price": 1275.21,
                        "ohlc": {"open": 1278.7, "high": 1279.7, "low": 1271.0, "close": 1278.3},
                        "total_buy_quantity": 1044030.0, "total_sell_quantity": 733902.0,
                    },
                },
            },
        )

    provider = _provider(handler)
    quotes = asyncio.run(provider.get_quotes(["A"]))
    quote = quotes["A"]
    assert quote.ohlc is not None
    assert quote.ohlc.high == 1279.7
    assert quote.ohlc.low == 1271.0
    assert quote.total_buy_quantity == 1044030
    assert quote.total_sell_quantity == 733902


def test_get_quotes_tolerates_missing_or_malformed_ohlc_without_fabricating() -> None:
    """A malformed/absent `ohlc` must never take down the whole quote
    (the REQUIRED fields still parse) and must never be guessed."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "NSE_EQ:A": {"instrument_token": "A", "last_price": 100.0, "net_change": 1.0},  # no ohlc at all
                    "NSE_EQ:B": {"instrument_token": "B", "last_price": 200.0, "net_change": 1.0, "ohlc": "not-an-object"},
                },
            },
        )

    provider = _provider(handler)
    quotes = asyncio.run(provider.get_quotes(["A", "B"]))
    assert quotes["A"].ohlc is None
    assert quotes["A"].last_price == 100.0  # required fields unaffected
    assert quotes["B"].ohlc is None
    assert quotes["B"].last_price == 200.0


def test_get_quotes_empty_list_returns_empty_dict_without_a_call() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not call the network for an empty instrument list")

    provider = _provider(handler)

    quotes = asyncio.run(provider.get_quotes([]))
    assert quotes == {}


def test_get_quote_missing_instrument_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "success", "data": {}})

    provider = _provider(handler)

    async def run() -> None:
        await provider.get_quote(
            security_id=INSTRUMENT_KEY, exchange_segment=ExchangeSegment.NSE_EQ, as_of=datetime.now(UTC)
        )

    with pytest.raises(ProviderMalformedResponse):
        asyncio.run(run())


def test_get_market_status_parses_real_response() -> None:
    from app.data.providers.upstox_provider import ExchangeStatus

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/market/status/NSE"
        return httpx.Response(
            200,
            json={"status": "success", "data": {"exchange": "NSE", "status": "CLOSING_END", "last_updated": 1787913000013}},
        )

    provider = _provider(handler)

    status = asyncio.run(provider.get_market_status(exchange="NSE"))
    assert status == ExchangeStatus.CLOSING_END


def test_get_market_status_rejects_unrecognized_value() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "success", "data": {"status": "SOMETHING_NEW"}})

    provider = _provider(handler)

    with pytest.raises(ProviderMalformedResponse):
        asyncio.run(provider.get_market_status(exchange="NSE"))


def test_non_json_body_raises_malformed_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    provider = _provider(handler)

    async def run() -> None:
        await provider.get_ohlcv(
            security_id=INSTRUMENT_KEY,
            exchange_segment=ExchangeSegment.NSE_EQ,
            timeframe=Timeframe.M15,
            start=datetime(2026, 8, 27, 0, 0, tzinfo=UTC),
            end=datetime(2026, 8, 27, 23, 59, tzinfo=UTC),
            as_of=datetime(2026, 8, 27, 23, 59, tzinfo=UTC),
        )

    with pytest.raises(ProviderMalformedResponse):
        asyncio.run(run())


# -- option chain ------------------------------------------------------

NIFTY_KEY = "NSE_INDEX|Nifty 50"
EXPIRY = date(2026, 9, 24)


def _real_chain_row(*, strike: float, spot: float = 24800.0) -> dict[str, object]:
    # Shape matches the REAL, live `/v2/option/chain` response, verified
    # 2026-08-28 (NIFTY) -- not a guessed schema.
    return {
        "expiry": EXPIRY.isoformat(),
        "pcr": 1.12,
        "strike_price": strike,
        "underlying_key": NIFTY_KEY,
        "underlying_spot_price": spot,
        "call_options": {
            "instrument_key": f"NSE_FO|CE{int(strike)}",
            "market_data": {
                "ltp": 120.5,
                "volume": 45000,
                "oi": 1250000,
                "close_price": 118.0,
                "bid_price": 120.0,
                "bid_qty": 300,
                "ask_price": 121.0,
                "ask_qty": 300,
                "prev_oi": 1200000,
            },
            "option_greeks": {"vega": 8.2, "theta": -6.1, "gamma": 0.001, "delta": 0.52, "iv": 13.4, "pop": 48.0},
        },
        "put_options": {
            "instrument_key": f"NSE_FO|PE{int(strike)}",
            "market_data": {
                "ltp": 95.0,
                "volume": 38000,
                "oi": 980000,
                "close_price": 97.0,
                "bid_price": 94.5,
                "bid_qty": 300,
                "ask_price": 95.5,
                "ask_qty": 300,
                "prev_oi": 1010000,
            },
            "option_greeks": {"vega": 8.0, "theta": -5.8, "gamma": 0.001, "delta": -0.48, "iv": 13.1, "pop": 52.0},
        },
    }


def test_get_chain_parses_real_response_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/option/chain"
        assert request.url.params["instrument_key"] == NIFTY_KEY
        assert request.url.params["expiry_date"] == EXPIRY.isoformat()
        return httpx.Response(
            200,
            json={"status": "success", "data": [_real_chain_row(strike=24700.0), _real_chain_row(strike=24800.0)]},
        )

    provider = _provider(handler)

    async def run() -> RawOptionChain:
        return await provider.get_chain(underlying=NIFTY_KEY, expiry=EXPIRY, as_of=datetime.now(UTC))

    chain = asyncio.run(run())
    assert chain.underlying == NIFTY_KEY
    assert chain.expiry == EXPIRY
    assert chain.underlying_last_price == 24800.0
    assert set(chain.strikes) == {24700.0, 24800.0}

    legs = chain.strikes[24700.0]
    assert {leg.right for leg in legs} == {OptionRight.CE, OptionRight.PE}
    ce = next(leg for leg in legs if leg.right == OptionRight.CE)
    assert ce.last_price == 120.5
    assert ce.open_interest == 1250000
    assert ce.previous_open_interest == 1200000
    assert ce.implied_volatility == 13.4
    assert ce.delta == 0.52
    pe = next(leg for leg in legs if leg.right == OptionRight.PE)
    assert pe.delta == -0.48


def test_get_chain_non_success_status_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "error", "data": []})

    provider = _provider(handler)

    async def run() -> None:
        await provider.get_chain(underlying=NIFTY_KEY, expiry=EXPIRY, as_of=datetime.now(UTC))

    with pytest.raises(ProviderMalformedResponse):
        asyncio.run(run())


def test_get_chain_no_strikes_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "success", "data": []})

    provider = _provider(handler)

    async def run() -> None:
        await provider.get_chain(underlying=NIFTY_KEY, expiry=EXPIRY, as_of=datetime.now(UTC))

    with pytest.raises(ProviderMalformedResponse):
        asyncio.run(run())


def test_get_chain_missing_strike_price_raises_malformed_not_a_crash() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        row = _real_chain_row(strike=24700.0)
        del row["strike_price"]
        return httpx.Response(200, json={"status": "success", "data": [row]})

    provider = _provider(handler)

    async def run() -> None:
        await provider.get_chain(underlying=NIFTY_KEY, expiry=EXPIRY, as_of=datetime.now(UTC))

    with pytest.raises(ProviderMalformedResponse):
        asyncio.run(run())


def test_get_chain_leg_with_missing_market_data_still_parses() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        row = _real_chain_row(strike=24700.0)
        call = row["call_options"]
        assert isinstance(call, dict)
        del call["market_data"]
        del call["option_greeks"]
        return httpx.Response(200, json={"status": "success", "data": [row]})

    provider = _provider(handler)

    async def run() -> RawOptionChain:
        return await provider.get_chain(underlying=NIFTY_KEY, expiry=EXPIRY, as_of=datetime.now(UTC))

    chain = asyncio.run(run())
    ce = next(leg for leg in chain.strikes[24700.0] if leg.right == OptionRight.CE)
    assert ce.last_price is None
    assert ce.delta is None


def test_get_expiries_parses_real_contract_list_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/option/contract"
        assert request.url.params["instrument_key"] == NIFTY_KEY
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": [
                    {"instrument_key": "NSE_FO|1", "expiry": "2026-09-03", "instrument_type": "CE", "strike_price": 24700},
                    {"instrument_key": "NSE_FO|2", "expiry": "2026-09-03", "instrument_type": "PE", "strike_price": 24700},
                    {"instrument_key": "NSE_FO|3", "expiry": "2026-09-24", "instrument_type": "CE", "strike_price": 24800},
                ],
            },
        )

    provider = _provider(handler)

    expiries = asyncio.run(provider.get_expiries(underlying=NIFTY_KEY, as_of=datetime.now(UTC)))
    assert expiries == [date(2026, 9, 3), date(2026, 9, 24)]


def test_get_expiries_non_success_status_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "error", "data": []})

    provider = _provider(handler)

    with pytest.raises(ProviderMalformedResponse):
        asyncio.run(provider.get_expiries(underlying=NIFTY_KEY, as_of=datetime.now(UTC)))


def test_get_news_parses_real_response_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/news"
        assert request.url.params["category"] == "instrument_keys"
        assert request.url.params["instrument_keys"] == INSTRUMENT_KEY
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    INSTRUMENT_KEY: [
                        {
                            "heading": "SENSEX falls 183 points",
                            "summary": "Reliance Industries was a top drag.",
                            "thumbnail": "https://assets.upstox.com/x.webp",
                            "article_link": "https://upstox.com/news/x/",
                            "published_time": 1787740313933,
                        }
                    ]
                },
                "metadata": {"page": {"page_number": 1, "page_size": 100, "total_records": 1, "total_pages": 1}},
            },
        )

    provider = _provider(handler)
    items = asyncio.run(provider.get_news(instrument_key=INSTRUMENT_KEY))
    assert len(items) == 1
    assert items[0].heading == "SENSEX falls 183 points"
    assert items[0].summary == "Reliance Industries was a top drag."
    assert items[0].published_time == datetime(2026, 8, 26, 10, 31, 53, 933000, tzinfo=UTC)


def test_get_news_real_empty_result_is_not_an_error() -> None:
    """Confirmed live 2026-08-29: an unrecognized/no-news instrument key
    returns a real, honest empty result -- never an error."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "success", "data": {}, "metadata": {"page": {"total_records": 0}}})

    provider = _provider(handler)
    items = asyncio.run(provider.get_news(instrument_key=INSTRUMENT_KEY))
    assert items == []


def test_get_news_non_success_status_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "error", "data": {}})

    provider = _provider(handler)
    with pytest.raises(ProviderMalformedResponse):
        asyncio.run(provider.get_news(instrument_key=INSTRUMENT_KEY))


def test_get_news_malformed_published_time_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": "success", "data": {INSTRUMENT_KEY: [{"heading": "x", "published_time": "not-a-number"}]}},
        )

    provider = _provider(handler)
    with pytest.raises(ProviderMalformedResponse):
        asyncio.run(provider.get_news(instrument_key=INSTRUMENT_KEY))


def test_health_reports_up_after_successful_call() -> None:
    from app.data.providers.health import HealthStatus

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "success", "data": {"candles": []}})

    provider = _provider(handler)

    async def run() -> None:
        await provider.get_ohlcv(
            security_id=INSTRUMENT_KEY,
            exchange_segment=ExchangeSegment.NSE_EQ,
            timeframe=Timeframe.M15,
            start=datetime(2026, 8, 27, 0, 0, tzinfo=UTC),
            end=datetime(2026, 8, 27, 23, 59, tzinfo=UTC),
            as_of=datetime(2026, 8, 27, 23, 59, tzinfo=UTC),
        )

    asyncio.run(run())
    health = asyncio.run(provider.health())
    assert health.status == HealthStatus.UP


# -- IPO Intelligence (get_ipos / get_ipo_detail) ----------------------------
# Response shapes below are taken from REAL, live-verified `/v2/ipos` and
# `/v2/ipos/{id}` responses captured 2026-08-30 -- see
# docs/data-sources/IPO_DATA_SOURCE_DECISION.md.


def test_get_ipos_parses_a_real_list_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/ipos"
        assert request.url.params["status"] == "open"
        assert request.url.params["page_number"] == "1"
        return httpx.Response(200, json={
            "status": "success",
            "data": [{
                "id": "ashutosh-fibre-limited-ipo", "symbol": "ASHUTOSH", "name": "Ashutosh Fibre IPO",
                "status": "open", "isin": "INE19FR01012", "issue_type": "sme", "issue_size": 56.0,
                "industry": "Textile", "minimum_price": 87.0, "maximum_price": 92.0,
                "bidding_start_date": "2026-08-31", "bidding_end_date": "2026-09-02", "total_subscription": "0.0",
            }],
            "meta_data": {"page": {"page_number": 1, "total_pages": 5, "records": 20, "total_records": 95}},
        })

    provider = _provider(handler)
    listings, page = asyncio.run(provider.get_ipos(status="open"))
    assert len(listings) == 1
    assert listings[0].name == "Ashutosh Fibre IPO"
    assert listings[0].issue_type == "sme"
    assert listings[0].minimum_price == 87.0
    assert page.total_records == 95
    assert page.total_pages == 5


def test_get_ipos_rejects_an_invalid_status_before_ever_calling_the_network() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not reach the network with an invalid status")

    provider = _provider(handler)
    with pytest.raises(ValueError, match="invalid IPO status"):
        asyncio.run(provider.get_ipos(status="all"))


def test_get_ipos_non_success_status_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "error", "data": []})

    provider = _provider(handler)
    with pytest.raises(ProviderMalformedResponse):
        asyncio.run(provider.get_ipos(status="open"))


def test_get_ipo_detail_parses_a_real_detail_response_with_listing_price() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/ipos/tempsens-instruments-india-limited-ipo"
        return httpx.Response(200, json={
            "status": "success",
            "data": {
                "id": "tempsens-instruments-india-limited-ipo", "symbol": "TEMPSENS",
                "name": "Tempsens Instruments (India) IPO", "status": "listed", "isin": "INE1KZI01025",
                "issue_type": "regular", "issue_size": 650.0, "industry": "Engineering - Industrial Equipments",
                "minimum_price": 285.0, "maximum_price": 300.0, "bidding_start_date": "2026-08-20",
                "bidding_end_date": "2026-08-24", "face_value": 4.0, "tick_size": None, "lot_size": 50,
                "minimum_quantity": 50, "cut_off_price": 300.0, "listing_price": 634.0,
                "listing_exchange": "BSE,NSE",
                "rhp_url": "https://www.icicisecurities.com/x.pdf", "drhp_url": None,
                "timeline": {
                    "pre_apply_start_date": "2026-08-18", "application_start_date": "2026-08-20",
                    "application_end_date": "2026-08-24", "allotment_start_date": "2026-08-25",
                    "allotment_date": "2026-08-27", "refund_initiation_date": "2026-08-27",
                    "listing_date": "2026-08-28", "mandate_end_date": "2026-10-05",
                },
                "registrar_info": {
                    "name": "KFin Technologies Limited", "email": "tempsens.ipo@kfintech.com",
                    "contact_name": "M. Murali Krishna", "contact_number": "+91 40 6716 2222",
                    "website": "https://www.kfintech.com/", "registrar": "K FINTECH",
                },
                "total_subscription": "213.92",
                "investors": [{"category": "IND", "description": None}, {"category": "EMP", "description": None}, {"category": "HNI", "description": None}],
            },
        })

    provider = _provider(handler)
    detail = asyncio.run(provider.get_ipo_detail(ipo_id="tempsens-instruments-india-limited-ipo"))
    assert detail.listing_price == 634.0
    assert detail.lot_size == 50
    assert detail.timeline.listing_date == date(2026, 8, 28)
    assert detail.registrar_info.registrar == "K FINTECH"
    assert {c.category for c in detail.investors} == {"IND", "EMP", "HNI"}
    assert detail.rhp_url is not None and detail.rhp_url.endswith(".pdf")


def test_get_ipo_detail_never_fabricates_a_shareholder_category() -> None:
    """The real "SHA" (shareholder) category is a genuine, sourced signal
    when present -- this test locks in that it is NEVER added when the
    real response doesn't include it."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "status": "success",
            "data": {
                "id": "example-ipo", "symbol": "EX", "name": "Example IPO", "status": "open", "isin": None,
                "issue_type": "regular", "issue_size": None, "industry": None, "minimum_price": None,
                "maximum_price": None, "bidding_start_date": None, "bidding_end_date": None,
                "total_subscription": None, "investors": [{"category": "IND", "description": None}],
            },
        })

    provider = _provider(handler)
    detail = asyncio.run(provider.get_ipo_detail(ipo_id="example-ipo"))
    assert "SHA" not in {c.category for c in detail.investors}


def test_get_ipo_detail_non_success_status_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "error", "data": {}})

    provider = _provider(handler)
    with pytest.raises(ProviderMalformedResponse):
        asyncio.run(provider.get_ipo_detail(ipo_id="anything"))
