from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime

from app.data.providers.base import RawCandle, RawOptionChain
from app.data.providers.mock_provider import MockMarketDataProvider, MockOptionChainProvider
from app.domain.market.models import ExchangeSegment, Timeframe


def test_mock_market_data_provider_is_deterministic() -> None:
    provider = MockMarketDataProvider()
    start = datetime(2026, 8, 27, 9, 15, tzinfo=UTC)
    end = datetime(2026, 8, 27, 10, 15, tzinfo=UTC)

    async def run() -> tuple[list[RawCandle], list[RawCandle]]:
        first = await provider.get_ohlcv(
            security_id="1333",
            exchange_segment=ExchangeSegment.NSE_EQ,
            timeframe=Timeframe.M5,
            start=start,
            end=end,
            as_of=end,
        )
        second = await provider.get_ohlcv(
            security_id="1333",
            exchange_segment=ExchangeSegment.NSE_EQ,
            timeframe=Timeframe.M5,
            start=start,
            end=end,
            as_of=end,
        )
        return first, second

    first, second = asyncio.run(run())
    assert first == second
    assert len(first) == 12  # 60 minutes / 5-minute step, cursor < end


def test_mock_market_data_respects_as_of() -> None:
    provider = MockMarketDataProvider()
    start = datetime(2026, 8, 27, 9, 15, tzinfo=UTC)
    end = datetime(2026, 8, 27, 10, 15, tzinfo=UTC)
    cutoff = datetime(2026, 8, 27, 9, 45, tzinfo=UTC)

    async def run() -> list[RawCandle]:
        return await provider.get_ohlcv(
            security_id="1333",
            exchange_segment=ExchangeSegment.NSE_EQ,
            timeframe=Timeframe.M5,
            start=start,
            end=end,
            as_of=cutoff,
        )

    candles = asyncio.run(run())
    assert candles  # sanity: as_of within range still yields data
    assert all(c.timestamp <= cutoff for c in candles)
    assert candles[-1].timestamp == cutoff


def test_mock_option_chain_every_strike_has_ce_and_pe_and_positive_strikes() -> None:
    provider = MockOptionChainProvider()
    as_of = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)

    async def run() -> RawOptionChain:
        return await provider.get_chain(underlying="NIFTY", expiry=date(2026, 9, 25), as_of=as_of)

    chain = asyncio.run(run())
    assert chain.strikes
    assert all(len(legs) == 2 for legs in chain.strikes.values())
    assert all(strike > 0 for strike in chain.strikes)


def test_mock_option_chain_deterministic_across_calls() -> None:
    provider = MockOptionChainProvider()
    as_of = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)

    async def run() -> tuple[RawOptionChain, RawOptionChain]:
        first = await provider.get_chain(underlying="NIFTY", expiry=date(2026, 9, 25), as_of=as_of)
        second = await provider.get_chain(underlying="NIFTY", expiry=date(2026, 9, 25), as_of=as_of)
        return first, second

    first, second = asyncio.run(run())
    assert first.strikes.keys() == second.strikes.keys()


def test_mock_expiries_are_thursdays_and_ordered() -> None:
    provider = MockOptionChainProvider()
    as_of = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)

    async def run() -> list[date]:
        return await provider.get_expiries(underlying="NIFTY", as_of=as_of)

    expiries = asyncio.run(run())
    assert len(expiries) == 4
    assert all(d.weekday() == 3 for d in expiries)
    assert expiries == sorted(expiries)
