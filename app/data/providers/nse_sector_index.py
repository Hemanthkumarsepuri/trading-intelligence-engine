"""NSE official sector/industry classification — Sprint 8, Objective P1.

Source: NSE Indices Limited's own real, public, official constituent file
for the Nifty 500 index — confirmed live reachable 2026-09-03:
`https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv`
(`nsearchives.nseindia.com` is NSE's own static-content archive
subdomain, not a third party). The file carries a real `Industry` column
per constituent (confirmed live: 500 rows, 20 distinct real NSE-defined
industry values, e.g. "Financial Services", "Information Technology",
"Oil Gas & Consumable Fuels") — this IS the official sector
classification, sourced from the exchange's own index-methodology
process, never inferred from a company's name.

This is a single plain HTTP GET of a public CSV file NSE itself publishes
for exactly this purpose (index-constituent transparency) — the same
category of request `upstox_instrument_master.fetch_instrument_master()`
already makes against a different exchange-adjacent static file, not
scraping an interactive page or evading any bot defense. No
authentication, no cost, no rate limit encountered in live testing.

Coverage: the Nifty 500 covers roughly the top 500 companies by market
cap — in practice, effectively all NSE F&O-eligible underlyings this
system already analyzes are inside it, but a real, valid instrument
outside the list is honestly UNKNOWN here, never guessed.

Same cache/refresh discipline as `fetch_instrument_master()`: this file
changes only on NSE's own periodic index rebalancing (quarterly/semi-
annual), so a local cache younger than `max_cache_age_seconds` is reused
rather than re-fetched on every call.
"""

from __future__ import annotations

import csv
import io
import time
from pathlib import Path

import httpx

from app.data.providers.exceptions import ProviderMalformedResponse, ProviderUnavailable

NIFTY500_CONSTITUENT_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
NIFTY50_CONSTITUENT_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv"

# NSE serves this without a browser-shaped User-Agent header on at least
# some request paths -- confirmed live 2026-09-03 this default header is
# NOT required for a 200, but a real browser-like UA is supplied anyway as
# defensive practice against a future WAF change, exactly the same
# reasoning as any other real HTTP client identifying itself honestly.
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; trading-intelligence-engine/1.0)"}


async def fetch_sector_index(
    client: httpx.AsyncClient,
    *,
    cache_path: Path | None = None,
    max_cache_age_seconds: float = 86400.0 * 7,  # index rebalances rarely; a week-old cache is still correct
    source_url: str = NIFTY500_CONSTITUENT_URL,
) -> dict[str, str]:
    """Returns a real `{trading_symbol: industry}` mapping, built ONLY
    from NSE's own published constituent file -- never a guessed or
    name-inferred sector. A symbol absent from the returned dict is
    honestly UNKNOWN to the caller, never defaulted to anything.
    """
    if cache_path is not None and cache_path.exists():
        age_seconds = time.time() - cache_path.stat().st_mtime
        if age_seconds <= max_cache_age_seconds:
            return _parse_csv(cache_path.read_text(encoding="utf-8"))

    try:
        response = await client.get(source_url, headers=_HEADERS, timeout=30.0)
    except httpx.HTTPError as exc:
        if cache_path is not None and cache_path.exists():
            return _parse_csv(cache_path.read_text(encoding="utf-8"))  # stale-but-present cache beats a hard failure
        raise ProviderUnavailable(f"could not fetch NSE sector index constituent file: {exc}") from exc

    if response.status_code >= 400:
        raise ProviderMalformedResponse(f"NSE sector index constituent file returned HTTP {response.status_code}")

    text = response.text
    mapping = _parse_csv(text)  # validate before caching -- never cache an unparseable response

    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(text, encoding="utf-8")

    return mapping


def _parse_csv(text: str) -> dict[str, str]:
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or "Symbol" not in reader.fieldnames or "Industry" not in reader.fieldnames:
        raise ProviderMalformedResponse(
            f"NSE sector index constituent file missing expected columns (got {reader.fieldnames!r})"
        )
    mapping: dict[str, str] = {}
    for row in reader:
        symbol = (row.get("Symbol") or "").strip()
        industry = (row.get("Industry") or "").strip()
        if symbol and industry:
            mapping[symbol] = industry
    if not mapping:
        raise ProviderMalformedResponse("NSE sector index constituent file parsed to zero usable rows")
    return mapping
