from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.news.models import (
    NewsDirection,
    NewsEvidenceQuality,
    NewsItem,
    NewsRelevance,
    deduplicate_news_items,
    filter_news_no_lookahead,
)


def _item(*, title: str, published: datetime, retrieved: datetime) -> NewsItem:
    return NewsItem(
        source="upstox", published_at=published, retrieved_at=retrieved, title=title,
        summary=None, url=None, symbol="ONGC", category="COMPANY",
        relevance=NewsRelevance.HIGH, direction=NewsDirection.UNKNOWN,
        evidence_quality=NewsEvidenceQuality.HIGH,
    )


def test_deduplicate_news_keeps_first_headline_published_pair() -> None:
    t0 = datetime(2026, 9, 8, 10, 0, tzinfo=UTC)
    retrieved = datetime(2026, 9, 9, 10, 0, tzinfo=UTC)
    items = [
        _item(title="ONGC update", published=t0, retrieved=retrieved),
        _item(title="ONGC UPDATE", published=t0, retrieved=retrieved),
        _item(title="Different", published=t0, retrieved=retrieved),
    ]
    unique = deduplicate_news_items(items)
    assert len(unique) == 2
    assert unique[0].title == "ONGC update"
    assert unique[1].title == "Different"


def test_future_news_is_dropped_for_historical_as_of() -> None:
    as_of = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
    retrieved = as_of
    past = _item(title="past", published=as_of - timedelta(hours=1), retrieved=retrieved)
    future = _item(title="future", published=as_of + timedelta(hours=1), retrieved=retrieved)
    kept = filter_news_no_lookahead([past, future], as_of=as_of)
    assert [i.title for i in kept] == ["past"]
