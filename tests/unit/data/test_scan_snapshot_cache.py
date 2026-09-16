from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

import app.data.providers.scan_snapshot_cache as cache_module
from app.data.providers.base import RawCandle, RawQuote
from app.data.providers.exceptions import ProviderTimeout
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


# ---------------------------------------------------------------------------
# Release gate (Section 7) -- minute-bounded sharing of market status and
# identical quote batches.
# ---------------------------------------------------------------------------




class _CountingInner:
    name = "upstox"

    def __init__(self) -> None:
        self.status_calls = 0
        self.quote_batches: list[list[str]] = []
        self.fail_next = False

    async def get_market_status(self, *, exchange: str = "NSE") -> str:
        self.status_calls += 1
        await asyncio.sleep(0.01)
        if self.fail_next:
            self.fail_next = False
            raise ProviderTimeout("boom")
        return f"OPEN-{self.status_calls}"

    async def get_quotes(self, security_ids: list[str]) -> dict[str, Any]:
        self.quote_batches.append(list(security_ids))
        await asyncio.sleep(0.01)
        return {key: object() for key in security_ids}


def _freeze_minute(monkeypatch: pytest.MonkeyPatch, minute: list[int]) -> None:
    monkeypatch.setattr(cache_module, "utc_now", lambda: datetime.fromtimestamp(minute[0] * 60 + 5, tz=UTC))


def test_concurrent_market_status_calls_share_one_request_within_a_minute(monkeypatch: pytest.MonkeyPatch) -> None:
    minute = [29_000_000]
    _freeze_minute(monkeypatch, minute)
    inner = _CountingInner()
    cache = ScanSnapshotCache(inner)

    async def run() -> list[Any]:
        return list(await asyncio.gather(*(cache.get_market_status(exchange="NSE") for _ in range(6))))

    assert asyncio.run(run()) == ["OPEN-1"] * 6
    assert inner.status_calls == 1


def test_a_new_minute_always_refetches(monkeypatch: pytest.MonkeyPatch) -> None:
    minute = [29_000_000]
    _freeze_minute(monkeypatch, minute)
    inner = _CountingInner()
    cache = ScanSnapshotCache(inner)

    async def run() -> tuple[Any, Any]:
        first = await cache.get_market_status()
        minute[0] += 1
        return first, await cache.get_market_status()

    assert asyncio.run(run()) == ("OPEN-1", "OPEN-2")
    assert inner.status_calls == 2


def test_failures_are_never_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    minute = [29_000_000]
    _freeze_minute(monkeypatch, minute)
    inner = _CountingInner()
    inner.fail_next = True
    cache = ScanSnapshotCache(inner)

    async def run() -> Any:
        with pytest.raises(ProviderTimeout):
            await cache.get_market_status()
        return await cache.get_market_status()

    assert asyncio.run(run()) == "OPEN-2"


def test_only_byte_identical_quote_batches_are_shared(monkeypatch: pytest.MonkeyPatch) -> None:
    minute = [29_000_000]
    _freeze_minute(monkeypatch, minute)
    inner = _CountingInner()
    cache = ScanSnapshotCache(inner)

    async def run() -> None:
        a = await cache.get_quotes(["NSE_INDEX|Nifty 50", "NSE_INDEX|India VIX"])
        a["mutated"] = object()
        b = await cache.get_quotes(["NSE_INDEX|Nifty 50", "NSE_INDEX|India VIX"])
        assert "mutated" not in b
        await cache.get_quotes(["NSE_FO|RELIANCE-FUT"])
        await cache.get_quotes(["NSE_FO|SBIN-FUT"])

    asyncio.run(run())
    assert inner.quote_batches == [
        ["NSE_INDEX|Nifty 50", "NSE_INDEX|India VIX"], ["NSE_FO|RELIANCE-FUT"], ["NSE_FO|SBIN-FUT"],
    ]
