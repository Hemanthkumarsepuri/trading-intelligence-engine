"""95% sprint, Sprint 2b -- unit tests for `CachedCandleRepository`:
correctness (identical results to the wrapped repository, no-lookahead
preserved) and the actual caching behavior (the wrapped repository's
`query()` is called at most once per (instrument_id, timeframe) key,
regardless of how many times the cache itself is queried)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from app.domain.market.freshness import DataFreshness
from app.domain.market.models import Candle, Timeframe
from app.persistence.caching import CachedCandleRepository
from app.persistence.jsonl_file import JsonlCandleRepository

_T0 = datetime(2026, 8, 27, 3, 45, tzinfo=UTC)


class _CountingRepository:
    """Wraps a real `JsonlCandleRepository` and counts real `query()`
    calls that reach it -- the fact this test actually cares about."""

    def __init__(self, inner: JsonlCandleRepository) -> None:
        self._inner = inner
        self.query_calls = 0

    async def save(self, candle: Candle) -> None:
        await self._inner.save(candle)

    async def query(self, *, instrument_id: str, timeframe: Timeframe, start: datetime, end: datetime, as_of: datetime) -> list[Candle]:
        self.query_calls += 1
        return await self._inner.query(instrument_id=instrument_id, timeframe=timeframe, start=start, end=end, as_of=as_of)


def _candle(*, timestamp: datetime, close: str) -> Candle:
    c = Decimal(close)
    return Candle(
        provider="test", freshness=DataFreshness(data_timestamp=timestamp, received_timestamp=timestamp),
        instrument_id="NSE_EQ|TEST", timeframe=Timeframe.M15, open=c, high=c, low=c, close=c, volume=1000,
    )


def test_repeated_queries_hit_the_backing_repository_exactly_once(tmp_path: Path) -> None:
    inner = JsonlCandleRepository(tmp_path / "candles.jsonl")
    candles = [_candle(timestamp=_T0 + timedelta(minutes=15 * i), close=str(1000 + i)) for i in range(10)]

    async def run() -> int:
        for c in candles:
            await inner.save(c)
        counting = _CountingRepository(inner)
        cache = CachedCandleRepository(counting)  # type: ignore[arg-type]
        for i in range(20):  # many real calls with DIFFERENT as_of, same key
            await cache.query(
                instrument_id="NSE_EQ|TEST", timeframe=Timeframe.M15,
                start=_T0, end=_T0 + timedelta(hours=3), as_of=_T0 + timedelta(minutes=15 * i),
            )
        return counting.query_calls

    assert asyncio.run(run()) == 1


def test_cached_results_match_the_backing_repository_exactly(tmp_path: Path) -> None:
    inner = JsonlCandleRepository(tmp_path / "candles.jsonl")
    candles = [_candle(timestamp=_T0 + timedelta(minutes=15 * i), close=str(1000 + i)) for i in range(10)]

    async def run() -> tuple[list[Candle], list[Candle]]:
        for c in candles:
            await inner.save(c)
        cache = CachedCandleRepository(inner)
        as_of = _T0 + timedelta(minutes=90)
        direct = await inner.query(instrument_id="NSE_EQ|TEST", timeframe=Timeframe.M15, start=_T0, end=_T0 + timedelta(hours=3), as_of=as_of)
        cached = await cache.query(instrument_id="NSE_EQ|TEST", timeframe=Timeframe.M15, start=_T0, end=_T0 + timedelta(hours=3), as_of=as_of)
        return direct, cached

    direct, cached = asyncio.run(run())
    assert direct == cached
    assert len(direct) == 7  # candles at offsets 0..6 (<=90 min)


def test_no_lookahead_is_preserved_through_the_cache(tmp_path: Path) -> None:
    """The cache is populated with a WIDE internal bound, but every real
    call still only ever sees candles at or before ITS OWN `as_of` --
    the defining no-lookahead property must survive caching."""
    inner = JsonlCandleRepository(tmp_path / "candles.jsonl")
    candles = [_candle(timestamp=_T0 + timedelta(minutes=15 * i), close=str(1000 + i)) for i in range(10)]

    async def run() -> list[Candle]:
        for c in candles:
            await inner.save(c)
        cache = CachedCandleRepository(inner)
        as_of = _T0 + timedelta(minutes=30)  # only the first 3 candles
        return await cache.query(instrument_id="NSE_EQ|TEST", timeframe=Timeframe.M15, start=_T0, end=_T0 + timedelta(hours=3), as_of=as_of)

    result = asyncio.run(run())
    assert all(c.freshness.data_timestamp <= _T0 + timedelta(minutes=30) for c in result)
    assert len(result) == 3


def test_save_invalidates_that_keys_cache(tmp_path: Path) -> None:
    inner = JsonlCandleRepository(tmp_path / "candles.jsonl")

    async def run() -> list[Candle]:
        cache = CachedCandleRepository(inner)
        first = await cache.query(instrument_id="NSE_EQ|TEST", timeframe=Timeframe.M15, start=_T0, end=_T0 + timedelta(hours=1), as_of=_T0 + timedelta(hours=1))
        assert first == []
        await cache.save(_candle(timestamp=_T0, close="1000"))
        return await cache.query(instrument_id="NSE_EQ|TEST", timeframe=Timeframe.M15, start=_T0, end=_T0 + timedelta(hours=1), as_of=_T0 + timedelta(hours=1))

    result = asyncio.run(run())
    assert len(result) == 1
