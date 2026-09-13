from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.market.freshness import DataFreshness
from app.domain.market.models import Candle, Timeframe
from app.persistence.in_memory import InMemoryCandleRepository


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


def test_query_excludes_records_beyond_as_of() -> None:
    repo = InMemoryCandleRepository()
    t0 = datetime(2026, 8, 27, 9, 0, tzinfo=UTC)
    early = _candle(t0)
    late = _candle(t0 + timedelta(minutes=30))

    async def run() -> list[Candle]:
        await repo.save(early)
        await repo.save(late)
        return await repo.query(
            instrument_id="INST1",
            timeframe=Timeframe.M5,
            start=t0 - timedelta(minutes=5),
            end=t0 + timedelta(hours=1),
            as_of=t0 + timedelta(minutes=10),
        )

    results = asyncio.run(run())
    assert results == [early]
    assert late not in results


def test_query_orders_results_by_timestamp() -> None:
    repo = InMemoryCandleRepository()
    t0 = datetime(2026, 8, 27, 9, 0, tzinfo=UTC)
    c2 = _candle(t0 + timedelta(minutes=5))
    c1 = _candle(t0)

    async def run() -> list[Candle]:
        await repo.save(c2)
        await repo.save(c1)
        return await repo.query(
            instrument_id="INST1", timeframe=Timeframe.M5, start=t0, end=t0 + timedelta(hours=1), as_of=t0 + timedelta(hours=1)
        )

    results = asyncio.run(run())
    assert results == [c1, c2]


def test_query_filters_by_instrument_and_timeframe() -> None:
    repo = InMemoryCandleRepository()
    t0 = datetime(2026, 8, 27, 9, 0, tzinfo=UTC)
    target = _candle(t0)
    other_instrument = Candle(
        provider="mock",
        freshness=DataFreshness(data_timestamp=t0, received_timestamp=t0),
        instrument_id="INST2",
        timeframe=Timeframe.M5,
        open=Decimal("50"),
        high=Decimal("51"),
        low=Decimal("49"),
        close=Decimal("50"),
        volume=10,
    )

    async def run() -> list[Candle]:
        await repo.save(target)
        await repo.save(other_instrument)
        return await repo.query(
            instrument_id="INST1", timeframe=Timeframe.M5, start=t0, end=t0 + timedelta(hours=1), as_of=t0 + timedelta(hours=1)
        )

    results = asyncio.run(run())
    assert results == [target]
