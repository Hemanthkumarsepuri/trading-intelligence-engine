"""News/event domain models (Sprint 4, Part B) — pure, provider-neutral.

The real, legitimate source for this data is `UpstoxProvider.get_news()`
(the SAME already-authorized account this whole project already uses,
confirmed live 2026-08-29 — see docs/data-sources/PROVIDER_DECISION.md's
"News/event provider re-audit" section). This module never imports from
`app.data.*` — nothing here knows Upstox exists, per this codebase's
dependency-direction rule (`domain/` never imports `data/`).

=== DIRECTION IS NEVER INFERRED FROM ARTICLE TEXT ===
"A news article must NOT directly override technical/options evidence"
(Sprint 4, Part C) is enforced structurally here, not just by convention:
`build_news_item()` ALWAYS sets `direction=NewsDirection.UNKNOWN` — this
codebase has no legitimate sentiment-analysis capability, and guessing one
from a headline string would be exactly the kind of fabrication this
project forbids everywhere else. A human reading the real headline/summary
text (both preserved verbatim) can form their own judgement; this system
never does it for them.

`relevance` is the one honest exception: because every item comes from a
query already scoped to one specific `instrument_key` (Upstox is doing the
filtering, not this codebase inferring it), `relevance=HIGH` is a
structural fact about the query, not an inference.

=== NO-LOOK-AHEAD ===
`filter_news_no_lookahead()` is the enforcement point, mirroring
`app.domain.technical.series.bounded_series()`'s exact discipline for
candles: an item whose `published_at` is after `as_of` is dropped, never
shown as if it were already known at that instant. This matters more here
than almost anywhere else in this codebase — a live-only news endpoint
cannot answer "what did the market know as of a past `as_of`", so this
filter is the only thing standing between a replay/backtest run and a
subtle look-ahead leak.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum


class NewsRelevance(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


class NewsDirection(str, Enum):
    """Always `UNKNOWN` in this system today — see module docstring. The
    full vocabulary exists so a future, genuinely evidence-based
    classifier (never a keyword heuristic, never an LLM guess) has
    somewhere to report into without a schema change.
    """

    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    MIXED = "MIXED"
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"


class NewsEvidenceQuality(str, Enum):
    """How much weight this specific item deserves as evidence -- a
    freshness-based HEURISTIC classification (see `classify_news_evidence_
    quality`), not a claim about the article's journalistic quality.
    """

    HIGH = "HIGH"
    MODERATE = "MODERATE"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class NewsItem:
    source: str
    published_at: datetime
    retrieved_at: datetime
    title: str
    summary: str | None
    url: str | None
    symbol: str
    category: str  # "COMPANY" -- the only category this real endpoint supports today
    relevance: NewsRelevance
    direction: NewsDirection
    evidence_quality: NewsEvidenceQuality


def classify_news_evidence_quality(*, published_at: datetime, as_of: datetime, fresh_within: timedelta) -> NewsEvidenceQuality:
    """`fresh_within` (THRESHOLD, required, undefaulted): the age below
    which an item counts as `HIGH` evidence quality rather than `MODERATE`
    -- no single correct "how old is too old" value exists independent of
    what the analysis is being used for. An item that is somehow timestamped
    after `as_of` (should never happen once `filter_news_no_lookahead()`
    has run first, but defensively guarded here too) is `UNKNOWN`, never
    silently treated as fresh.
    """
    age = as_of - published_at
    if age < timedelta(0):
        return NewsEvidenceQuality.UNKNOWN
    if age <= fresh_within:
        return NewsEvidenceQuality.HIGH
    return NewsEvidenceQuality.MODERATE


def build_news_item(
    *, source: str, heading: str, summary: str | None, url: str | None, published_at: datetime,
    retrieved_at: datetime, symbol: str, as_of: datetime, fresh_within: timedelta,
) -> NewsItem:
    """The only place a `NewsItem` is constructed — guarantees `direction`
    is always `UNKNOWN` and `relevance` is always `HIGH` (see module
    docstring), never left to a caller to accidentally set otherwise.
    """
    return NewsItem(
        source=source, published_at=published_at, retrieved_at=retrieved_at, title=heading, summary=summary, url=url,
        symbol=symbol, category="COMPANY", relevance=NewsRelevance.HIGH, direction=NewsDirection.UNKNOWN,
        evidence_quality=classify_news_evidence_quality(published_at=published_at, as_of=as_of, fresh_within=fresh_within),
    )


def filter_news_no_lookahead(items: list[NewsItem], *, as_of: datetime) -> list[NewsItem]:
    """The no-look-ahead boundary for news — mirrors `bounded_series()`'s
    exact role for candles. `/v2/news` is a live-only endpoint (it cannot
    answer "what did the market know as of a past `as_of`"), so every
    caller MUST run real-time-fetched items through this before treating
    them as evidence for an `as_of` that could legitimately be in the past
    (replay/backtest mode).
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    return sorted((i for i in items if i.published_at <= as_of), key=lambda i: i.published_at)


def deduplicate_news_items(items: list[NewsItem]) -> list[NewsItem]:
    """Stable identity is headline + published_at. First occurrence wins."""
    seen: set[tuple[str, datetime]] = set()
    unique: list[NewsItem] = []
    for item in items:
        key = (item.title.strip().casefold(), item.published_at)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique
