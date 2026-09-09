"""Dedicated look-ahead-bias tests — ARCHITECTURE.md Addendum A5.

These tests deliberately seed a repository/provider with data both before
and after an evaluation instant T, then assert that requesting `as_of=T`
never returns anything from beyond T. This is its own test category (not
folded into general persistence tests) because preventing look-ahead
leakage is a safety property this project cannot regress on silently, not
just a correctness nicety — see SAFETY_LOCK.txt's broader spirit.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.data.providers.base import RawCandle
from app.data.providers.exceptions import ProviderMalformedResponse
from app.data.providers.historical_provider import HistoricalProvider
from app.domain.market.freshness import DataFreshness
from app.domain.market.models import (
    Candle,
    ExchangeSegment,
    OptionChainSnapshot,
    OptionQuote,
    OptionRight,
    Timeframe,
)
from app.domain.options.models import IvObservation
from app.persistence.in_memory import (
    InMemoryCandleRepository,
    InMemoryIvObservationRepository,
    InMemoryOptionChainRepository,
)
from app.persistence.jsonl_file import JsonlOptionChainRepository


def _candle(ts: datetime) -> Candle:
    return Candle(
        provider="mock",
        freshness=DataFreshness(data_timestamp=ts, received_timestamp=ts),
        instrument_id="INST1",
        timeframe=Timeframe.M5,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=10,
    )


def test_candle_repository_never_returns_future_rows() -> None:
    repo = InMemoryCandleRepository()
    t = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
    past = _candle(t - timedelta(minutes=5))
    present = _candle(t)
    future = _candle(t + timedelta(minutes=5))

    async def run() -> list[Candle]:
        for candle in (past, present, future):
            await repo.save(candle)
        return await repo.query(
            instrument_id="INST1",
            timeframe=Timeframe.M5,
            start=t - timedelta(hours=1),
            end=t + timedelta(hours=1),
            as_of=t,
        )

    results = asyncio.run(run())
    assert future not in results
    assert past in results
    assert present in results
    assert len(results) == 2


def test_historical_provider_get_ohlcv_never_leaks_future_candles() -> None:
    repo = InMemoryCandleRepository()
    t = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)

    async def run() -> list[RawCandle]:
        for offset in range(-3, 4):  # 3 before, present, 3 after
            await repo.save(_candle(t + timedelta(minutes=5 * offset)))
        provider = HistoricalProvider(candles=repo, option_chains=InMemoryOptionChainRepository())
        return await provider.get_ohlcv(
            security_id="INST1",
            exchange_segment=ExchangeSegment.NSE_EQ,
            timeframe=Timeframe.M5,
            start=t - timedelta(hours=1),
            end=t + timedelta(hours=1),
            as_of=t,
        )

    candles = asyncio.run(run())
    assert all(c.timestamp <= t for c in candles)
    assert len(candles) == 4  # the 3 before + the one exactly at t


def _snapshot(ts: datetime, underlying_price: Decimal, expiry: date) -> OptionChainSnapshot:
    fresh = DataFreshness(data_timestamp=ts, received_timestamp=ts)
    ce = OptionQuote(
        provider="mock", freshness=fresh, underlying="NIFTY", expiry=expiry, strike=Decimal("25000"), right=OptionRight.CE
    )
    pe = OptionQuote(
        provider="mock", freshness=fresh, underlying="NIFTY", expiry=expiry, strike=Decimal("25000"), right=OptionRight.PE
    )
    return OptionChainSnapshot(
        provider="mock", freshness=fresh, underlying="NIFTY", expiry=expiry, underlying_last_price=underlying_price,
        legs=[ce, pe],
    )


def test_historical_provider_option_chain_never_leaks_future_snapshot() -> None:
    repo = InMemoryOptionChainRepository()
    t = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
    expiry = date(2026, 9, 25)

    async def run() -> OptionChainSnapshot:
        await repo.save(_snapshot(t - timedelta(minutes=10), Decimal("25000"), expiry))
        # This one must never be visible to a query with as_of=t:
        await repo.save(_snapshot(t + timedelta(minutes=10), Decimal("99999"), expiry))
        provider = HistoricalProvider(candles=InMemoryCandleRepository(), option_chains=repo)
        raw = await provider.get_chain(underlying="NIFTY", expiry=expiry, as_of=t)
        return raw  # type: ignore[return-value]

    raw_chain = asyncio.run(run())
    assert raw_chain.underlying_last_price == 25000.0


def test_historical_provider_raises_when_no_snapshot_exists_at_or_before_as_of() -> None:
    repo = InMemoryOptionChainRepository()
    t = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
    provider = HistoricalProvider(candles=InMemoryCandleRepository(), option_chains=repo)

    async def run() -> None:
        await provider.get_chain(underlying="NIFTY", expiry=date(2026, 9, 25), as_of=t)

    with pytest.raises(ProviderMalformedResponse):
        asyncio.run(run())


def test_jsonl_option_chain_repository_never_returns_future_snapshot(tmp_path: Path) -> None:
    """Same property as `test_historical_provider_option_chain_never_leaks_future_snapshot`
    above, but against the REAL durable file-backed repository used in
    production (`JsonlOptionChainRepository`), not the in-memory test
    double — the Options Intelligence Engine milestone's persistence layer
    must uphold this exact contract too, on disk, across process runs.
    """
    repo = JsonlOptionChainRepository(tmp_path / "chains.jsonl")
    t = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
    expiry = date(2026, 9, 25)

    async def run() -> OptionChainSnapshot | None:
        await repo.save(_snapshot(t - timedelta(minutes=10), Decimal("25000"), expiry))
        await repo.save(_snapshot(t + timedelta(minutes=10), Decimal("99999"), expiry))  # must never leak
        return await repo.latest(underlying="NIFTY", expiry=expiry, as_of=t)

    result = asyncio.run(run())
    assert result is not None
    assert result.underlying_last_price == Decimal("25000")


def test_option_chain_query_range_never_returns_future_snapshots(tmp_path: Path) -> None:
    """The temporal evidence engine's multi-snapshot query
    (`OptionChainRepository.query_range`) must uphold the same no-future-
    leakage contract as every other repository read — a wide `end` bound
    must never become a backdoor around `as_of`.
    """
    repo = JsonlOptionChainRepository(tmp_path / "chains.jsonl")
    t = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
    expiry = date(2026, 9, 25)

    async def run() -> list[OptionChainSnapshot]:
        await repo.save(_snapshot(t - timedelta(minutes=5), Decimal("25000"), expiry))
        await repo.save(_snapshot(t + timedelta(minutes=5), Decimal("99999"), expiry))  # must never leak
        return await repo.query_range(
            underlying="NIFTY", expiry=expiry, start=t - timedelta(hours=1), end=t + timedelta(hours=1), as_of=t
        )

    results = asyncio.run(run())
    assert len(results) == 1
    assert results[0].underlying_last_price == Decimal("25000")


def test_iv_observation_repository_never_returns_future_observations() -> None:
    """The IV-rank prerequisite (`app.domain.options.iv_context.
    compute_iv_rank`) is only honest if its input history itself never
    leaks a future observation — this is that guarantee's enforcement
    point.
    """
    repo = InMemoryIvObservationRepository()
    t = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
    fresh_past = DataFreshness(data_timestamp=t - timedelta(days=1), received_timestamp=t - timedelta(days=1))
    fresh_future = DataFreshness(data_timestamp=t + timedelta(days=1), received_timestamp=t + timedelta(days=1))

    def _obs(freshness: DataFreshness, iv: str) -> IvObservation:
        return IvObservation(
            provider="mock", freshness=freshness, underlying="NIFTY", expiry=date(2026, 9, 25),
            atm_strike=Decimal("25000"), atm_ce_iv=Decimal(iv), atm_pe_iv=Decimal(iv), chain_iv=Decimal(iv),
        )

    async def run() -> list[IvObservation]:
        await repo.save(_obs(fresh_past, "13.0"))
        await repo.save(_obs(fresh_future, "999.0"))  # must never leak into a query as_of=t
        return await repo.query_history(underlying="NIFTY", as_of=t, lookback=timedelta(days=30))

    results = asyncio.run(run())
    assert len(results) == 1
    assert results[0].chain_iv == Decimal("13.0")
