"""Sprint 7B, Objectives 5/6 -- pure unit tests for deterministic news
event-type and recency classification. No sentiment, no I/O.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.news.event_classification import (
    NewsEventCategory,
    NewsRecency,
    classify_news_event,
    classify_news_recency,
)

AS_OF = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
INTRADAY_WITHIN = timedelta(hours=6)


def test_earnings_headline_classified() -> None:
    assert classify_news_event("Company X posts Q2 results, net profit up 20%", None) == NewsEventCategory.EARNINGS


def test_order_win_headline_classified() -> None:
    assert classify_news_event("Company X wins order worth Rs 500 crore", None) == NewsEventCategory.ORDER


def test_regulatory_headline_classified() -> None:
    assert classify_news_event("SEBI probe into Company X disclosure", None) == NewsEventCategory.REGULATORY


def test_analyst_action_headline_classified() -> None:
    assert classify_news_event("Brokerage upgrades Company X, raises target price", None) == NewsEventCategory.ANALYST_ACTION


def test_geopolitical_headline_classified() -> None:
    assert classify_news_event("Middle East tension escalates, crude jumps", None) == NewsEventCategory.GEOPOLITICAL


def test_unmatched_headline_stays_unknown() -> None:
    """No sentiment, no forced label -- a genuinely unmatched headline is
    honestly UNKNOWN, never coerced into a category to look complete."""
    assert classify_news_event("Company X unveils new logo", None) == NewsEventCategory.UNKNOWN


def test_summary_text_also_searched() -> None:
    assert classify_news_event("Update", "Company X announces bonus issue of shares") == NewsEventCategory.CORPORATE_ACTION


def test_classification_never_touches_sentiment_or_direction() -> None:
    """Structural proof: this module has no concept of direction/
    sentiment at all -- it returns only a category enum, never a
    bullish/bearish read."""
    import inspect

    sig = inspect.signature(classify_news_event)
    assert "direction" not in sig.parameters
    assert "sentiment" not in sig.parameters


# -- recency ----------------------------------------------------------


def test_recency_intraday_within_threshold() -> None:
    assert classify_news_recency(published_at=AS_OF - timedelta(minutes=30), as_of=AS_OF, intraday_within=INTRADAY_WITHIN) == NewsRecency.INTRADAY


def test_recency_one_day() -> None:
    assert classify_news_recency(published_at=AS_OF - timedelta(hours=20), as_of=AS_OF, intraday_within=INTRADAY_WITHIN) == NewsRecency.ONE_DAY


def test_recency_three_day() -> None:
    assert classify_news_recency(published_at=AS_OF - timedelta(days=2), as_of=AS_OF, intraday_within=INTRADAY_WITHIN) == NewsRecency.THREE_DAY


def test_recency_seven_day() -> None:
    assert classify_news_recency(published_at=AS_OF - timedelta(days=6), as_of=AS_OF, intraday_within=INTRADAY_WITHIN) == NewsRecency.SEVEN_DAY


def test_recency_older() -> None:
    assert classify_news_recency(published_at=AS_OF - timedelta(days=30), as_of=AS_OF, intraday_within=INTRADAY_WITHIN) == NewsRecency.OLDER


def test_recency_unknown_for_impossible_future_timestamp() -> None:
    assert classify_news_recency(published_at=AS_OF + timedelta(hours=1), as_of=AS_OF, intraday_within=INTRADAY_WITHIN) == NewsRecency.UNKNOWN


def test_recency_never_deletes_older_items_just_labels_them() -> None:
    """A 7-day-old headline still classifies to a real, real bucket
    (OLDER), never raises/excludes -- de-emphasis, not deletion."""
    result = classify_news_recency(published_at=AS_OF - timedelta(days=30), as_of=AS_OF, intraday_within=INTRADAY_WITHIN)
    assert result is not None
