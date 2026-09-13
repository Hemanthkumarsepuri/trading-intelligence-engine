from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.data.providers.base import RawCandle, RawQuote
from app.data.providers.scan_snapshot_cache import ScanSnapshotCache
from app.domain.market.models import ExchangeSegment, Timeframe


class _Inner:
    name = "upstox"

    def __init__(self) -> None:
        self.ohlcv_calls = 0
        self.quote_calls = 0

    async def get_ohlcv(self, **kwargs: Any) -> list[RawCandle]:
        self.ohlcv_calls += 1
        return [
            RawCandle(
                timestamp=datetime(2026, 9, 8, 10, 0, tzinfo=UTC),
                open=1, high=1, low=1, close=1, volume=1,
            )
        ]

    async def get_quote(self, **kwargs: Any) -> RawQuote:
        self.quote_calls += 1
        return RawQuote(security_id="RELIANCE", last_price=100.0)


@pytest.mark.asyncio
async def test_cache_reuses_same_window_and_records_provenance() -> None:
    inner = _Inner()
    cache = ScanSnapshotCache(inner)
    kwargs = {
        "security_id": "RELIANCE",
        "exchange_segment": ExchangeSegment.NSE_EQ,
        "timeframe": Timeframe.M15,
        "start": datetime(2026, 9, 1, tzinfo=UTC),
        "end": datetime(2026, 9, 8, tzinfo=UTC),
        "as_of": datetime(2026, 9, 8, 12, tzinfo=UTC),
    }
    first = await cache.get_ohlcv(**kwargs)
    second = await cache.get_ohlcv(**kwargs)
    assert first == second
    assert inner.ohlcv_calls == 1
    assert cache.hits == 1
    assert cache.misses == 1
    assert cache.last_snapshot is not None
    assert cache.last_snapshot.provider == "upstox"
    assert cache.last_snapshot.data_type == "ohlcv"
    assert cache.last_snapshot.instrument == "RELIANCE"
