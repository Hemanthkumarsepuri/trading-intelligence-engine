"""Sprint 7B, Objective 5/6 -- deterministic news EVENT TYPE and RECENCY
classification, over the SAME real `NewsItem.title`/`summary` text
already fetched via `UpstoxProvider.get_news()` -- zero new fetch, zero
new provider.

=== THIS IS NOT SENTIMENT ===
`classify_news_event()` never touches `NewsItem.direction` (which stays
permanently `NewsDirection.UNKNOWN` -- see `app.domain.news.models`'s
own module docstring) and never infers whether an event is good or bad
for the stock. It answers a genuinely different, deterministic, keyword-
pattern question: "what TOPIC/TYPE is this headline about" (an earnings
release vs an order win vs a regulatory action) -- the same category of
classification as `oi_migration.py`'s quadrant read: a real, reproducible
pattern match over real text, never a guessed sentiment. A headline that
matches no known pattern honestly stays `UNKNOWN`, never forced into a
category to look complete.

Recency buckets exist so a UI can visually de-emphasize a stale headline
without ever deleting it -- see `classify_news_recency()`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum


class NewsEventCategory(str, Enum):
    EARNINGS = "EARNINGS"
    ORDER = "ORDER"
    CAPEX = "CAPEX"
    GOVERNMENT_POLICY = "GOVERNMENT_POLICY"
    REGULATORY = "REGULATORY"
    MANAGEMENT = "MANAGEMENT"
    PROMOTER = "PROMOTER"
    MERGER_ACQUISITION = "M&A"
    CONTRACT = "CONTRACT"
    SECTOR = "SECTOR"
    MACRO = "MACRO"
    GEOPOLITICAL = "GEOPOLITICAL"
    CORPORATE_ACTION = "CORPORATE_ACTION"
    ANALYST_ACTION = "ANALYST_ACTION"
    UNKNOWN = "UNKNOWN"


class NewsRecency(str, Enum):
    INTRADAY = "INTRADAY"
    ONE_DAY = "1D"
    THREE_DAY = "3D"
    SEVEN_DAY = "7D"
    OLDER = "OLDER"
    UNKNOWN = "UNKNOWN"


# Real, observable keyword patterns -- an ENGINEERING HEURISTIC, not an
# exhaustive or validated taxonomy; ordered so the more specific
# categories are checked before broader ones (e.g. M&A before CONTRACT).
_CATEGORY_KEYWORDS: dict[NewsEventCategory, tuple[str, ...]] = {
    NewsEventCategory.EARNINGS: ("q1 results", "q2 results", "q3 results", "q4 results", "quarterly results", "net profit", "posts profit", "posts loss", "earnings", "results announcement"),
    NewsEventCategory.MERGER_ACQUISITION: ("merger", "acquisition", "acquires", "to merge", "amalgamation", "stake buy", "takeover"),
    NewsEventCategory.CORPORATE_ACTION: ("dividend", "bonus issue", "stock split", "buyback", "rights issue", "record date"),
    NewsEventCategory.PROMOTER: ("promoter", "pledge", "promoter holding", "promoter stake"),
    NewsEventCategory.MANAGEMENT: ("ceo", "cfo", "md resigns", "appoints", "steps down", "resignation", "new chairman", "new director"),
    NewsEventCategory.ORDER: ("wins order", "bags order", "secures order", "order win", "receives order", "order book"),
    NewsEventCategory.CONTRACT: ("signs contract", "agreement with", "mou", "partnership", "tie-up", "collaborat"),
    NewsEventCategory.CAPEX: ("capex", "capacity expansion", "new plant", "approves capacity", "investment plan"),
    NewsEventCategory.REGULATORY: ("sebi", "rbi order", "regulator", "compliance", "penalty", "show cause", "probe"),
    NewsEventCategory.GOVERNMENT_POLICY: ("government policy", "budget", "gst", "import duty", "export duty", "tariff", "subsidy", "pli scheme"),
    NewsEventCategory.ANALYST_ACTION: ("upgrades", "downgrades", "target price", "brokerage", "maintains buy", "maintains sell", "initiates coverage"),
    NewsEventCategory.GEOPOLITICAL: ("war", "sanctions", "conflict", "geopolitical", "middle east", "border tension", "military"),
    NewsEventCategory.MACRO: ("inflation", "gdp", "interest rate", "repo rate", "monetary policy", "fiscal deficit"),
    NewsEventCategory.SECTOR: ("sector outlook", "industry body", "sectoral"),
}


def classify_news_event(title: str, summary: str | None) -> NewsEventCategory:
    """Deterministic, case-insensitive substring match over the real
    headline/summary text. The FIRST matching category in `_CATEGORY_
    KEYWORDS`'s own (deliberately ordered, most-specific-first) iteration
    wins -- `UNKNOWN` when nothing matches, never a guess.
    """
    text = f"{title} {summary or ''}".lower()
    for category, keywords in _CATEGORY_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return category
    return NewsEventCategory.UNKNOWN


def classify_news_recency(*, published_at: datetime, as_of: datetime, intraday_within: timedelta) -> NewsRecency:
    """`intraday_within` (THRESHOLD, required, undefaulted) -- how recent
    counts as "INTRADAY" (same real trading-session-scale window) rather
    than just "within 1 day"; no single correct value independent of
    what the analysis is being used for. A negative age (should never
    happen once `filter_news_no_lookahead()` has run) is honestly
    `UNKNOWN`, never treated as fresh.
    """
    age = as_of - published_at
    if age < timedelta(0):
        return NewsRecency.UNKNOWN
    if age <= intraday_within:
        return NewsRecency.INTRADAY
    if age <= timedelta(days=1):
        return NewsRecency.ONE_DAY
    if age <= timedelta(days=3):
        return NewsRecency.THREE_DAY
    if age <= timedelta(days=7):
        return NewsRecency.SEVEN_DAY
    return NewsRecency.OLDER
