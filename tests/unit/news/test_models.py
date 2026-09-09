from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.news.models import (
    NewsDirection,
    NewsEvidenceQuality,
    NewsItem,
    NewsRelevance,
    build_news_item,
    classify_news_evidence_quality,
    filter_news_no_lookahead,
)

AS_OF = datetime(2026, 8, 29, 10, 0, tzinfo=UTC)


def _item(
    published_at: datetime, *, retrieved_at: datetime = AS_OF, as_of: datetime = AS_OF, fresh_within: timedelta = timedelta(days=2)
) -> NewsItem:
    return build_news_item(
        source="upstox", heading="headline", summary="summary", url="https://example.com", published_at=published_at,
        retrieved_at=retrieved_at, symbol="RELIANCE", as_of=as_of, fresh_within=fresh_within,
    )


def test_build_news_item_never_infers_a_direction() -> None:
    item = _item(AS_OF - timedelta(hours=1))
    assert item.direction == NewsDirection.UNKNOWN


def test_build_news_item_relevance_is_high_by_construction() -> None:
    # Every item comes from a query already scoped to one instrument_key --
    # relevance=HIGH is a structural fact, not an inference.
    item = _item(AS_OF - timedelta(hours=1))
    assert item.relevance == NewsRelevance.HIGH


def test_build_news_item_category_is_company() -> None:
    item = _item(AS_OF - timedelta(hours=1))
    assert item.category == "COMPANY"


def test_build_news_item_preserves_title_and_summary_verbatim() -> None:
    item = build_news_item(
        source="upstox", heading="SENSEX falls 183 points", summary="Reliance was a top drag.", url="https://x",
        published_at=AS_OF - timedelta(hours=1), retrieved_at=AS_OF, symbol="RELIANCE", as_of=AS_OF, fresh_within=timedelta(days=2),
    )
    assert item.title == "SENSEX falls 183 points"
    assert item.summary == "Reliance was a top drag."


# -- classify_news_evidence_quality ----------------------------------------


def test_evidence_quality_high_when_within_fresh_window() -> None:
    q = classify_news_evidence_quality(published_at=AS_OF - timedelta(hours=1), as_of=AS_OF, fresh_within=timedelta(days=2))
    assert q == NewsEvidenceQuality.HIGH


def test_evidence_quality_moderate_when_beyond_fresh_window() -> None:
    q = classify_news_evidence_quality(published_at=AS_OF - timedelta(days=10), as_of=AS_OF, fresh_within=timedelta(days=2))
    assert q == NewsEvidenceQuality.MODERATE


def test_evidence_quality_unknown_when_published_after_as_of() -> None:
    # Defensive: should never happen once filter_news_no_lookahead() has
    # run, but never silently treated as fresh if it somehow does.
    q = classify_news_evidence_quality(published_at=AS_OF + timedelta(hours=1), as_of=AS_OF, fresh_within=timedelta(days=2))
    assert q == NewsEvidenceQuality.UNKNOWN


def test_evidence_quality_at_exact_boundary_is_high() -> None:
    q = classify_news_evidence_quality(published_at=AS_OF - timedelta(days=2), as_of=AS_OF, fresh_within=timedelta(days=2))
    assert q == NewsEvidenceQuality.HIGH


# -- filter_news_no_lookahead (Part Q item 20 / Part S) ---------------------


def test_filter_drops_items_published_after_as_of() -> None:
    future_item = _item(AS_OF + timedelta(hours=1))
    past_item = _item(AS_OF - timedelta(hours=1))
    result = filter_news_no_lookahead([future_item, past_item], as_of=AS_OF)
    assert result == [past_item]


def test_filter_keeps_item_published_exactly_at_as_of() -> None:
    exact_item = _item(AS_OF)
    result = filter_news_no_lookahead([exact_item], as_of=AS_OF)
    assert result == [exact_item]


def test_filter_orders_results_chronologically() -> None:
    later = _item(AS_OF - timedelta(hours=1))
    earlier = _item(AS_OF - timedelta(hours=3))
    result = filter_news_no_lookahead([later, earlier], as_of=AS_OF)
    assert result == [earlier, later]


def test_filter_empty_input_returns_empty() -> None:
    assert filter_news_no_lookahead([], as_of=AS_OF) == []


def test_filter_rejects_naive_as_of() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        filter_news_no_lookahead([], as_of=datetime(2026, 8, 29, 10, 0))  # noqa: DTZ001


def test_filter_never_mutates_input_list() -> None:
    items = [_item(AS_OF - timedelta(hours=1)), _item(AS_OF + timedelta(hours=1))]
    original = list(items)
    filter_news_no_lookahead(items, as_of=AS_OF)
    assert items == original
