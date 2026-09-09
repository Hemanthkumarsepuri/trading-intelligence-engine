from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx

from app.data.providers.upstox_provider import ExchangeStatus, UpstoxProvider
from app.domain.market.data_state import MarketDataState
from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy
from app.orchestration.watchlist_snapshot import build_watchlist_snapshot

RELIANCE_KEY = "NSE_EQ|INE002A01018"
AS_OF = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)

_MASTER: list[dict[str, object]] = [
    {
        "segment": "NSE_EQ",
        "name": "RELIANCE INDUSTRIES LTD",
        "exchange": "NSE",
        "instrument_type": "EQ",
        "instrument_key": RELIANCE_KEY,
        "trading_symbol": "RELIANCE",
    }
]


def _candle_rows(n: int, *, end: datetime) -> list[list[object]]:
    rows = []
    for i in range(n):
        ts = end - timedelta(minutes=15 * (n - 1 - i))
        price = 1280.0 + i * 0.1
        rows.append([ts.isoformat(), price, price + 1, price - 1, price, 1000, 0])
    return rows


def _quote_body(*, symbol: str = "RELIANCE", key: str = RELIANCE_KEY) -> dict[str, object]:
    return {
        "status": "success",
        "data": {
            f"NSE_EQ:{symbol}": {
                "instrument_token": key,
                "last_price": 1287.0,
                "net_change": 4.8,
                "volume": 6830228,
                "average_price": 1284.51,
                "last_trade_time": str(int(AS_OF.timestamp() * 1000) - 5000),
            }
        },
    }


def _status_body(status: str = "NORMAL_OPEN") -> dict[str, object]:
    return {"status": "success", "data": {"exchange": "NSE", "status": status, "last_updated": 1}}


def _router(
    *, status: str = "NORMAL_OPEN", candle_count: int = 60, fail_quotes: bool = False, fail_status: bool = False
) -> object:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v2/market/status/NSE":
            if fail_status:
                return httpx.Response(500, text="down")
            return httpx.Response(200, json=_status_body(status))
        if path == "/v2/market-quote/quotes":
            if fail_quotes:
                return httpx.Response(500, text="down")
            return httpx.Response(200, json=_quote_body())
        if path.startswith("/v3/historical-candle/"):
            return httpx.Response(
                200, json={"status": "success", "data": {"candles": _candle_rows(candle_count, end=AS_OF)}}
            )
        raise AssertionError(f"unexpected path {path}")

    return handler


def _provider(handler: object) -> UpstoxProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
    return UpstoxProvider(client=client, access_token="tok")


def test_successful_instrument_with_sufficient_history_produces_analysis() -> None:
    provider = _provider(_router(status="NORMAL_OPEN", candle_count=60))
    strategy = EMAVWAPAlignmentStrategy()

    results = asyncio.run(
        build_watchlist_snapshot(
            provider=provider, instrument_master=_MASTER, symbols=["RELIANCE"], strategy=strategy, as_of=AS_OF
        )
    )

    assert len(results) == 1
    r = results[0]
    assert r.instrument is not None and r.instrument.instrument_key == RELIANCE_KEY
    assert r.error is None
    assert r.quote is not None
    assert r.previous_close == Decimal("1282.2")
    assert r.day_change == Decimal("4.8")
    assert r.analysis is not None
    assert r.state in (MarketDataState.LIVE_SNAPSHOT, MarketDataState.STALE_DATA)


def test_unresolvable_symbol_produces_error_state_without_crashing_others() -> None:
    provider = _provider(_router())
    strategy = EMAVWAPAlignmentStrategy()

    results = asyncio.run(
        build_watchlist_snapshot(
            provider=provider,
            instrument_master=_MASTER,
            symbols=["NOT_A_REAL_SYMBOL", "RELIANCE"],
            strategy=strategy,
            as_of=AS_OF,
        )
    )

    by_symbol = {r.requested_symbol: r for r in results}
    assert by_symbol["NOT_A_REAL_SYMBOL"].state == MarketDataState.ERROR
    assert by_symbol["NOT_A_REAL_SYMBOL"].error is not None
    assert by_symbol["RELIANCE"].error is None  # unaffected by the other symbol's failure


def test_market_status_failure_does_not_block_instrument_results() -> None:
    provider = _provider(_router(fail_status=True))
    strategy = EMAVWAPAlignmentStrategy()

    results = asyncio.run(
        build_watchlist_snapshot(
            provider=provider, instrument_master=_MASTER, symbols=["RELIANCE"], strategy=strategy, as_of=AS_OF
        )
    )

    assert results[0].exchange_status == ExchangeStatus.UNKNOWN
    assert results[0].error is None  # quote/history still succeeded


def test_quotes_failure_marks_provider_unavailable() -> None:
    provider = _provider(_router(fail_quotes=True))
    strategy = EMAVWAPAlignmentStrategy()

    results = asyncio.run(
        build_watchlist_snapshot(
            provider=provider, instrument_master=_MASTER, symbols=["RELIANCE"], strategy=strategy, as_of=AS_OF
        )
    )

    assert results[0].state == MarketDataState.PROVIDER_UNAVAILABLE
    assert results[0].error is not None
    assert results[0].analysis is None


def test_insufficient_history_produces_no_analysis() -> None:
    provider = _provider(_router(candle_count=10))
    strategy = EMAVWAPAlignmentStrategy()

    results = asyncio.run(
        build_watchlist_snapshot(
            provider=provider, instrument_master=_MASTER, symbols=["RELIANCE"], strategy=strategy, as_of=AS_OF
        )
    )

    assert results[0].state == MarketDataState.INSUFFICIENT_HISTORY
    assert results[0].analysis is None


def test_market_closed_state_overrides_snapshot_even_with_fresh_quote() -> None:
    provider = _provider(_router(status="CLOSING_END", candle_count=60))
    strategy = EMAVWAPAlignmentStrategy()

    results = asyncio.run(
        build_watchlist_snapshot(
            provider=provider, instrument_master=_MASTER, symbols=["RELIANCE"], strategy=strategy, as_of=AS_OF
        )
    )

    assert results[0].state == MarketDataState.MARKET_CLOSED_LATEST_DATA
    assert results[0].analysis is not None  # still computed -- state, not availability, communicates trust level


def test_multiple_instruments_batched_in_one_quotes_call() -> None:
    master: list[dict[str, object]] = [
        *_MASTER,
        {
            "segment": "NSE_EQ",
            "name": "TCS",
            "exchange": "NSE",
            "instrument_type": "EQ",
            "instrument_key": "NSE_EQ|TCSKEY",
            "trading_symbol": "TCS",
        },
    ]
    seen_instrument_keys: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v2/market/status/NSE":
            return httpx.Response(200, json=_status_body())
        if path == "/v2/market-quote/quotes":
            seen_instrument_keys.append(request.url.params["instrument_key"])
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": {
                        "NSE_EQ:RELIANCE": {"instrument_token": RELIANCE_KEY, "last_price": 1287.0, "net_change": 4.8},
                        "NSE_EQ:TCS": {"instrument_token": "NSE_EQ|TCSKEY", "last_price": 3500.0, "net_change": -10.0},
                    },
                },
            )
        if path.startswith("/v3/historical-candle/"):
            return httpx.Response(200, json={"status": "success", "data": {"candles": _candle_rows(60, end=AS_OF)}})
        raise AssertionError(path)

    provider = _provider(handler)
    strategy = EMAVWAPAlignmentStrategy()

    results = asyncio.run(
        build_watchlist_snapshot(
            provider=provider, instrument_master=master, symbols=["RELIANCE", "TCS"], strategy=strategy, as_of=AS_OF
        )
    )

    assert len(results) == 2
    assert "," in seen_instrument_keys[0]  # both instrument_keys sent in ONE batched request
    assert {r.requested_symbol for r in results} == {"RELIANCE", "TCS"}
