"""RHP/DRHP document fetch — Sprint 8, Objective P0.1.

Downloads a real, legitimately-public IPO prospectus PDF from its own
real `rhp_url`/`drhp_url` (Upstox's real `/v2/ipos/{id}` — see
`app.domain.ipo.models.IPOIdentity` — or SEBI's own public filings
repository, `https://www.sebi.gov.in/filings/public-issues.html`,
confirmed reachable 2026-09-03). This is a single plain HTTP GET of a
document the regulator/issuer already makes public for exactly this kind
of use — not scraping an interactive page, not evading any bot defense,
never a third-party aggregator.

The RHP is static once filed — a document hash lets a caller detect if a
supposedly-unchanged URL ever serves different bytes (it shouldn't, but
this system never assumes that without checking), and the cache is keyed
by that same URL so a given IPO's document is downloaded at most once per
process lifetime, aggressively cached on disk afterward (mirrors
`upstox_instrument_master.fetch_instrument_master()`'s own disk-cache
pattern, but keyed by URL hash instead of a fixed path, since there are
many possible RHP URLs, not one fixed reference file).
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.data.providers.exceptions import (
    ProviderMalformedResponse,
    ProviderTimeout,
    ProviderUnavailable,
)

_PDF_MAGIC = b"%PDF-"
_DEFAULT_MAX_BYTES = 60 * 1024 * 1024  # 60MB -- a real RHP is typically 5-40MB; well beyond that is not a legitimate response
_DEFAULT_TIMEOUT_SECONDS = 45.0
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; trading-intelligence-engine/1.0)"}
_ALLOWED_RHP_HOSTS = frozenset({
    "www.sebi.gov.in",
    "sebi.gov.in",
    "www.upstox.com",
    "upstox.com",
    "assets.upstox.com",
})


def _assert_safe_rhp_url(source_url: str) -> None:
    parsed = urlparse(source_url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in _ALLOWED_RHP_HOSTS:
        raise ProviderMalformedResponse(
            f"RHP URL refused: only https hosts in the published allowlist may be fetched (got {source_url!r})"
        )


@dataclass(frozen=True)
class RHPDocument:
    """Real, already-validated document bytes plus provenance -- never
    handed to a caller without `document_hash`/`retrieved_at`/
    `source_url` attached, so nothing downstream can present extracted
    facts without a traceable origin."""

    source_url: str
    content: bytes
    document_hash: str  # sha256 hex digest -- lets a caller detect a real byte-level change on a re-fetch
    retrieved_at_epoch: float
    from_cache: bool


def _cache_path_for(cache_dir: Path, source_url: str) -> Path:
    key = hashlib.sha256(source_url.encode("utf-8")).hexdigest()
    return cache_dir / f"{key}.pdf"


async def fetch_rhp_document(
    client: httpx.AsyncClient,
    *,
    source_url: str,
    cache_dir: Path | None = None,
    attempts: int = 3,
    backoff_seconds: float = 1.0,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> RHPDocument:
    """The RHP is static after filing -- a cache hit (any cached copy,
    regardless of age) is ALWAYS preferred over a re-fetch; there is no
    TTL here (unlike the instrument master/sector index, which genuinely
    change periodically). Content-type is validated by real PDF magic
    bytes (`%PDF-`), not by a trusted `Content-Type` header alone (a
    misconfigured server can send the wrong header for a real PDF, or the
    right header for something that isn't one) -- a response failing
    that check is `ProviderMalformedResponse`, never silently accepted.
    """
    _assert_safe_rhp_url(source_url)
    if cache_dir is not None:
        cache_path = _cache_path_for(cache_dir, source_url)
        if cache_path.exists():
            content = cache_path.read_bytes()
            return RHPDocument(
                source_url=source_url, content=content, document_hash=hashlib.sha256(content).hexdigest(),
                retrieved_at_epoch=cache_path.stat().st_mtime, from_cache=True,
            )

    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            content = await _download(client, source_url, timeout_seconds=timeout_seconds, max_bytes=max_bytes)
            break
        except (ProviderTimeout, ProviderUnavailable) as exc:
            last_exc = exc
            if attempt < attempts - 1:
                continue
            raise
    else:
        assert last_exc is not None
        raise last_exc

    if cache_dir is not None:
        cache_path = _cache_path_for(cache_dir, source_url)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(content)

    return RHPDocument(
        source_url=source_url, content=content, document_hash=hashlib.sha256(content).hexdigest(),
        retrieved_at_epoch=time.time(), from_cache=False,
    )


async def _download(client: httpx.AsyncClient, source_url: str, *, timeout_seconds: float, max_bytes: int) -> bytes:
    try:
        async with client.stream("GET", source_url, headers=_HEADERS, timeout=timeout_seconds, follow_redirects=False) as response:
            if response.status_code >= 400:
                raise ProviderMalformedResponse(f"RHP document fetch returned HTTP {response.status_code} for {source_url}")
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise ProviderMalformedResponse(f"RHP document at {source_url} exceeded the {max_bytes}-byte size limit -- refused")
                chunks.append(chunk)
            content = b"".join(chunks)
    except httpx.TimeoutException as exc:
        raise ProviderTimeout(f"RHP document fetch timed out for {source_url}: {exc}") from exc
    except httpx.HTTPError as exc:
        raise ProviderUnavailable(f"RHP document fetch failed for {source_url}: {exc}") from exc

    if not content.startswith(_PDF_MAGIC):
        raise ProviderMalformedResponse(f"RHP document at {source_url} does not start with the real PDF magic bytes -- not a genuine PDF")
    return content
