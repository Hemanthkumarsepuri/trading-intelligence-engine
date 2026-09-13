"""Sprint 8, Objective P1 -- `fetch_sector_index()` against a mocked real
CSV response, mirroring `test_upstox_instrument_master.py`'s pattern."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from app.data.providers.exceptions import ProviderMalformedResponse, ProviderUnavailable
from app.data.providers.nse_sector_index import NIFTY500_CONSTITUENT_URL, fetch_sector_index

_SAMPLE_CSV = (
    "Company Name,Industry,Symbol,Series,ISIN Code\r\n"
    "Tata Consultancy Services Ltd.,Information Technology,TCS,EQ,INE467B01029\r\n"
    "Reliance Industries Ltd.,Oil Gas & Consumable Fuels,RELIANCE,EQ,INE002A01018\r\n"
)


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_fetch_parses_real_csv_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_SAMPLE_CSV)

    mapping = asyncio.run(fetch_sector_index(_client(handler)))
    assert mapping == {"TCS": "Information Technology", "RELIANCE": "Oil Gas & Consumable Fuels"}


def test_fetch_defaults_to_the_real_nse_archive_url() -> None:
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(200, text=_SAMPLE_CSV)

    asyncio.run(fetch_sector_index(_client(handler)))
    assert seen_urls == [NIFTY500_CONSTITUENT_URL]


def test_fetch_raises_on_missing_columns() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="Symbol,Foo\r\nTCS,bar\r\n")

    with pytest.raises(ProviderMalformedResponse):
        asyncio.run(fetch_sector_index(_client(handler)))


def test_fetch_raises_on_empty_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="Company Name,Industry,Symbol,Series,ISIN Code\r\n")

    with pytest.raises(ProviderMalformedResponse):
        asyncio.run(fetch_sector_index(_client(handler)))


def test_fetch_raises_on_http_error_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    with pytest.raises(ProviderMalformedResponse):
        asyncio.run(fetch_sector_index(_client(handler)))


def test_fetch_raises_provider_unavailable_on_network_error_with_no_cache() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(ProviderUnavailable):
        asyncio.run(fetch_sector_index(_client(handler)))


def test_cache_used_when_fresh(tmp_path: Path) -> None:
    cache_path = tmp_path / "sector.csv"
    cache_path.write_text(_SAMPLE_CSV, encoding="utf-8")
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise AssertionError("should not fetch when a fresh cache exists")

    mapping = asyncio.run(fetch_sector_index(_client(handler), cache_path=cache_path, max_cache_age_seconds=999999))
    assert calls["n"] == 0
    assert mapping["TCS"] == "Information Technology"


def test_stale_cache_still_used_when_fetch_fails(tmp_path: Path) -> None:
    """A stale-but-present cache beats a hard failure -- same discipline
    as `fetch_instrument_master()`."""
    cache_path = tmp_path / "sector.csv"
    cache_path.write_text(_SAMPLE_CSV, encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    mapping = asyncio.run(fetch_sector_index(_client(handler), cache_path=cache_path, max_cache_age_seconds=0))
    assert mapping["TCS"] == "Information Technology"


def test_fresh_fetch_writes_the_cache(tmp_path: Path) -> None:
    cache_path = tmp_path / "sector.csv"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_SAMPLE_CSV)

    asyncio.run(fetch_sector_index(_client(handler), cache_path=cache_path))
    assert cache_path.exists()
    assert "TCS" in cache_path.read_text(encoding="utf-8")


def test_malformed_response_is_never_cached(tmp_path: Path) -> None:
    """A response that fails validation must not poison the cache for the
    next call."""
    cache_path = tmp_path / "sector.csv"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not,valid,columns\r\n")

    with pytest.raises(ProviderMalformedResponse):
        asyncio.run(fetch_sector_index(_client(handler), cache_path=cache_path))
    assert not cache_path.exists()
