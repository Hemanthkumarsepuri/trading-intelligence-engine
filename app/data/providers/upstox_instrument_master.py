"""Upstox instrument master — resolves a human-facing symbol (RELIANCE,
TCS, NIFTY50, ...) to the exact `instrument_key` Upstox's APIs require,
using Upstox's own published, public instrument reference file(s). Never
guesses a key from a symbol name; a symbol that doesn't match anything in
the real master resolves to `None`, not an invented key.

Source confirmed live 2026-08-28 (NSE) and 2026-08-29 (MCX, BSE — same
URL pattern, confirmed reachable):
`https://assets.upstox.com/market-quote/instruments/exchange/{EXCHANGE}.json.gz`
— gzip-compressed JSON array per exchange. The NSE file (~75,800
instruments: equities, indices, F&O, currency derivatives, commodities on
NSE) is the default and the only one most of this codebase needs; the MCX
file (confirmed live: 15,552 `MCX_FO` entries including real `CRUDE OIL`
and `GOLD` contracts) is fetched separately, only by the global-market-
context module that needs it — see
`app.domain.options.global_context`/`_MCX_MASTER_URL` at that call site.
Documented fields on every file: `trading_symbol`, `instrument_key`,
`name`, `exchange`, `segment`, `instrument_type`.
"""

from __future__ import annotations

import gzip
import json
import time
from collections.abc import Sequence
from pathlib import Path

import httpx
from pydantic import BaseModel

from app.data.providers.exceptions import ProviderMalformedResponse, ProviderUnavailable
from app.data.providers.instrument_master_index import index_for

NSE_MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"

# A small, explicitly-listed table for the rare case where a natural
# request name and Upstox's own `trading_symbol` genuinely differ —
# confirmed against the real instrument master, not guessed. NIFTY50's
# entry uses trading_symbol "NIFTY" (name "Nifty 50"); every other symbol
# this project currently requests matches its own trading_symbol exactly.
_SYMBOL_ALIASES: dict[str, str] = {
    "NIFTY50": "NIFTY",
    "NIFTY 50": "NIFTY",
}


class InstrumentRef(BaseModel):
    requested_symbol: str
    trading_symbol: str
    instrument_key: str
    name: str
    exchange: str
    segment: str
    instrument_type: str


async def fetch_instrument_master(
    client: httpx.AsyncClient,
    *,
    cache_path: Path | None = None,
    max_cache_age_seconds: float = 86400.0,
    master_url: str = NSE_MASTER_URL,
) -> list[dict[str, object]]:
    """Downloads the real instrument master, or reuses a local cache younger
    than `max_cache_age_seconds` — this reference file changes rarely, so
    re-downloading ~2MB on every call would be wasteful, not "the smallest
    correct mechanism." A cache older than the threshold is always
    refreshed, never silently served as if current.

    `master_url` defaults to the NSE file (this codebase's overwhelming
    common case); pass a different exchange's real URL (same
    `.../exchange/{EXCHANGE}.json.gz` pattern, confirmed live for at least
    NSE/MCX/BSE) to fetch that exchange's master instead — always pair
    with a distinct `cache_path` per exchange, since this function has no
    way to tell two different exchanges' caches apart otherwise.
    """
    if cache_path is not None and cache_path.exists():
        age_seconds = time.time() - cache_path.stat().st_mtime
        if age_seconds <= max_cache_age_seconds:
            return _load_cached(cache_path)

    try:
        response = await client.get(master_url, timeout=30.0)
    except httpx.HTTPError as exc:
        if cache_path is not None and cache_path.exists():
            return _load_cached(cache_path)  # a stale-but-present cache beats a hard failure
        raise ProviderUnavailable(f"could not fetch Upstox instrument master: {exc}") from exc

    if response.status_code >= 400:
        raise ProviderMalformedResponse(f"Upstox instrument master returned HTTP {response.status_code}")

    try:
        raw_bytes = gzip.decompress(response.content)
        data = json.loads(raw_bytes)
    except (OSError, ValueError) as exc:
        raise ProviderMalformedResponse(f"Upstox instrument master was not valid gzip+JSON: {exc}") from exc
    if not isinstance(data, list):
        raise ProviderMalformedResponse("Upstox instrument master root was not a JSON array")

    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(raw_bytes)

    return data


def _load_cached(cache_path: Path) -> list[dict[str, object]]:
    data = json.loads(cache_path.read_bytes())
    if not isinstance(data, list):
        raise ProviderMalformedResponse(f"cached instrument master at {cache_path} was not a JSON array")
    return data


def resolve_symbol(
    master: Sequence[dict[str, object]], symbol: str, *, segment: str | None = None
) -> InstrumentRef | None:
    """Exact `trading_symbol` match (after the small alias table above) —
    never a fuzzy/partial match, which could silently resolve to the wrong
    instrument. Returns `None`, never a guess, if nothing matches.

    Sprint 5 -- MOTHERSON root-cause fix: a small number of real NSE_EQ
    `trading_symbol`s are genuinely ambiguous in the real Upstox
    instrument master -- e.g. MOTHERSON carries BOTH a `D1`-series entry
    (a distinct, separately-listed, effectively inactive security --
    confirmed live 2026-08-31 by its own genuinely ancient exchange
    timestamp) AND the real, actively-traded `EQ` entry, under the exact
    same `trading_symbol`/`segment`. Live audit of the cached master
    confirmed 4 such collisions (MOTHERSON, CHOLAFIN, ELECTCAST, IMC1).
    Blindly taking the first match left the result at the mercy of the
    master's own row order -- correct for CHOLAFIN (EQ happens to sort
    first) but wrong for MOTHERSON (D1 sorts first), which is exactly
    what previously surfaced as a repeated, real `STALE_DATA` rejection
    for MOTHERSON (this was never a freshness-tolerance defect -- the
    freshness check was correctly rejecting genuinely stale data from the
    WRONG instrument). When more than one real candidate matches, the
    regular `instrument_type == "EQ"` entry is always the intended
    equity; this is a pure tie-break, never a filter -- a symbol with
    only one match (e.g. an index like NIFTY, whose `instrument_type` is
    `"INDEX"`) resolves exactly as before.
    """
    canonical = _SYMBOL_ALIASES.get(symbol.strip().upper(), symbol.strip().upper())
    # Final release gate (Section 7) -- the equality on `trading_symbol`
    # is now served from a memoized index instead of re-scanning all
    # ~29,713 master rows on every call (measured 0.307 s/call; see
    # `instrument_master_index`). The remaining predicates below are
    # UNCHANGED and still applied per candidate, and the index preserves
    # the master's own row order, so the `EQ` tie-break underneath
    # resolves identically to the old linear scan.
    matches = [
        entry
        for entry in index_for(master).by_trading_symbol().get(canonical, ())
        if (segment is None or entry.get("segment") == segment)
        and isinstance(entry.get("instrument_key"), str)
    ]
    if not matches:
        return None
    entry = next((e for e in matches if e.get("instrument_type") == "EQ"), matches[0])
    instrument_key = entry.get("instrument_key")
    if not isinstance(instrument_key, str):
        return None  # unreachable given the filter above; keeps mypy honest without an assert
    return InstrumentRef(
        requested_symbol=symbol,
        trading_symbol=str(entry.get("trading_symbol", "")),
        instrument_key=instrument_key,
        name=str(entry.get("name", "")),
        exchange=str(entry.get("exchange", "")),
        segment=str(entry.get("segment", "")),
        instrument_type=str(entry.get("instrument_type", "")),
    )


def resolve_symbols(
    master: Sequence[dict[str, object]], symbols: Sequence[str], *, segment: str | None = None
) -> dict[str, InstrumentRef | None]:
    return {symbol: resolve_symbol(master, symbol, segment=segment) for symbol in symbols}
