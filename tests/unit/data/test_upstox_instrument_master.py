from __future__ import annotations

import asyncio
import gzip
import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from app.data.providers.exceptions import ProviderMalformedResponse, ProviderUnavailable
from app.data.providers.upstox_instrument_master import (
    NSE_MASTER_URL,
    fetch_instrument_master,
    resolve_symbol,
    resolve_symbols,
)

_SAMPLE_MASTER: list[dict[str, object]] = [
    {
        "segment": "NSE_EQ",
        "name": "RELIANCE INDUSTRIES LTD",
        "exchange": "NSE",
        "instrument_type": "EQ",
        "instrument_key": "NSE_EQ|INE002A01018",
        "trading_symbol": "RELIANCE",
    },
    {
        "segment": "NSE_INDEX",
        "name": "Nifty 50",
        "exchange": "NSE",
        "instrument_type": "INDEX",
        "instrument_key": "NSE_INDEX|Nifty 50",
        "trading_symbol": "NIFTY",
    },
]


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _gzip_body() -> bytes:
    return gzip.compress(json.dumps(_SAMPLE_MASTER).encode("utf-8"))


# -- fetch_instrument_master ------------------------------------------------


def test_fetch_downloads_and_parses_real_gzip_json_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_gzip_body())

    master = asyncio.run(fetch_instrument_master(_client(handler)))

    assert master == _SAMPLE_MASTER


def test_fetch_defaults_to_the_real_nse_url() -> None:
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(200, content=_gzip_body())

    asyncio.run(fetch_instrument_master(_client(handler)))
    assert seen_urls == [NSE_MASTER_URL]


def test_fetch_uses_a_different_exchange_url_when_given_one() -> None:
    mcx_url = "https://assets.upstox.com/market-quote/instruments/exchange/MCX.json.gz"
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(200, content=_gzip_body())

    asyncio.run(fetch_instrument_master(_client(handler), master_url=mcx_url))
    assert seen_urls == [mcx_url]


def test_fetch_caches_to_disk_and_reuses_fresh_cache(tmp_path: Path) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, content=_gzip_body())

    cache_path = tmp_path / "NSE.json"

    first = asyncio.run(fetch_instrument_master(_client(handler), cache_path=cache_path))
    second = asyncio.run(fetch_instrument_master(_client(handler), cache_path=cache_path))

    assert first == second == _SAMPLE_MASTER
    assert calls["n"] == 1  # second call served from the fresh on-disk cache, no second network call


def test_fetch_refreshes_cache_once_it_expires(tmp_path: Path) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, content=_gzip_body())

    cache_path = tmp_path / "NSE.json"

    # A negative max-age unambiguously forces "always expired" regardless of
    # filesystem mtime resolution -- exactly 0.0 can race against a very
    # fast second call landing within the same clock tick, which is a test
    # precision issue, not a production guarantee this module makes.
    asyncio.run(fetch_instrument_master(_client(handler), cache_path=cache_path, max_cache_age_seconds=-1.0))
    asyncio.run(fetch_instrument_master(_client(handler), cache_path=cache_path, max_cache_age_seconds=-1.0))

    assert calls["n"] == 2  # always treated as expired -> re-fetched both times


def test_fetch_falls_back_to_stale_cache_on_network_failure(tmp_path: Path) -> None:
    cache_path = tmp_path / "NSE.json"
    cache_path.write_bytes(json.dumps(_SAMPLE_MASTER).encode("utf-8"))

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")

    master = asyncio.run(
        fetch_instrument_master(_client(handler), cache_path=cache_path, max_cache_age_seconds=0.0)
    )

    assert master == _SAMPLE_MASTER


def test_fetch_raises_provider_unavailable_with_no_cache_and_network_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")

    with pytest.raises(ProviderUnavailable):
        asyncio.run(fetch_instrument_master(_client(handler)))


def test_fetch_rejects_non_array_root() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=gzip.compress(b'{"not": "an array"}'))

    with pytest.raises(ProviderMalformedResponse):
        asyncio.run(fetch_instrument_master(_client(handler)))


def test_fetch_rejects_http_error_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server error")

    with pytest.raises(ProviderMalformedResponse):
        asyncio.run(fetch_instrument_master(_client(handler)))


# -- resolve_symbol / resolve_symbols ---------------------------------------


def test_resolve_symbol_exact_match() -> None:
    ref = resolve_symbol(_SAMPLE_MASTER, "RELIANCE")
    assert ref is not None
    assert ref.instrument_key == "NSE_EQ|INE002A01018"
    assert ref.requested_symbol == "RELIANCE"


def test_resolve_symbol_is_case_insensitive() -> None:
    ref = resolve_symbol(_SAMPLE_MASTER, "reliance")
    assert ref is not None
    assert ref.instrument_key == "NSE_EQ|INE002A01018"


def test_resolve_symbol_applies_known_alias() -> None:
    ref = resolve_symbol(_SAMPLE_MASTER, "NIFTY50")
    assert ref is not None
    assert ref.instrument_key == "NSE_INDEX|Nifty 50"
    assert ref.trading_symbol == "NIFTY"
    assert ref.requested_symbol == "NIFTY50"  # the original request is preserved, not overwritten


def test_resolve_symbol_returns_none_for_unknown_symbol_never_guesses() -> None:
    assert resolve_symbol(_SAMPLE_MASTER, "NOT_A_REAL_SYMBOL") is None


def test_resolve_symbol_respects_segment_filter() -> None:
    assert resolve_symbol(_SAMPLE_MASTER, "RELIANCE", segment="NSE_INDEX") is None
    assert resolve_symbol(_SAMPLE_MASTER, "RELIANCE", segment="NSE_EQ") is not None


def test_resolve_symbol_prefers_eq_over_an_ambiguous_duplicate_trading_symbol() -> None:
    """Sprint 5 -- the real MOTHERSON root cause: a `D1`-series entry and
    the real `EQ` entry share the exact same `trading_symbol`/`segment`,
    with the (genuinely inactive, ancient-timestamped) D1 entry sorted
    FIRST in the master -- `resolve_symbol()` must never resolve to it
    just because of row order."""
    master: list[dict[str, object]] = [
        {"segment": "NSE_EQ", "name": "SAMVARDHANA MOTHERSON INT", "exchange": "NSE", "instrument_type": "D1", "instrument_key": "NSE_EQ|INE775A08105", "trading_symbol": "MOTHERSON"},
        {"segment": "NSE_EQ", "name": "SAMVRDHNA MTHRSN INTL LTD", "exchange": "NSE", "instrument_type": "EQ", "instrument_key": "NSE_EQ|INE775A01035", "trading_symbol": "MOTHERSON"},
    ]
    ref = resolve_symbol(master, "MOTHERSON", segment="NSE_EQ")
    assert ref is not None
    assert ref.instrument_key == "NSE_EQ|INE775A01035"
    assert ref.instrument_type == "EQ"


def test_resolve_symbol_prefers_eq_regardless_of_which_duplicate_sorts_first() -> None:
    """Same ambiguity, reversed row order (mirrors the real CHOLAFIN case,
    where EQ already happened to sort first) -- the result must be
    identical either way, proving this is a real tie-break, not a
    first-match accident."""
    master: list[dict[str, object]] = [
        {"segment": "NSE_EQ", "name": "REAL EQUITY", "exchange": "NSE", "instrument_type": "EQ", "instrument_key": "NSE_EQ|REAL", "trading_symbol": "DUP"},
        {"segment": "NSE_EQ", "name": "OTHER SERIES", "exchange": "NSE", "instrument_type": "D1", "instrument_key": "NSE_EQ|OTHER", "trading_symbol": "DUP"},
    ]
    ref = resolve_symbol(master, "DUP", segment="NSE_EQ")
    assert ref is not None
    assert ref.instrument_key == "NSE_EQ|REAL"


def test_resolve_symbol_falls_back_to_first_match_when_no_candidate_is_eq() -> None:
    """A symbol with no `EQ` candidate at all (e.g. a real index, whose
    `instrument_type` is `"INDEX"`) resolves exactly as before -- this
    tie-break is additive, never a filter that could return `None` for a
    previously-resolvable symbol."""
    ref = resolve_symbol(_SAMPLE_MASTER, "NIFTY50")
    assert ref is not None
    assert ref.instrument_type == "INDEX"
    assert ref.instrument_key == "NSE_INDEX|Nifty 50"


def test_resolve_symbols_resolves_a_batch_with_mixed_hits_and_misses() -> None:
    result = resolve_symbols(_SAMPLE_MASTER, ["RELIANCE", "NIFTY50", "NOT_REAL"])

    assert result["RELIANCE"] is not None
    assert result["NIFTY50"] is not None
    assert result["NOT_REAL"] is None
