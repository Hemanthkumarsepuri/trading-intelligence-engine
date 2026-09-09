from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.domain.market.freshness import DataFreshness, FreshnessClass


def test_data_age_computed_correctly() -> None:
    freshness = DataFreshness(
        data_timestamp=datetime(2026, 8, 27, 10, 0, 0, tzinfo=UTC),
        received_timestamp=datetime(2026, 8, 27, 10, 0, 10, tzinfo=UTC),
    )
    assert freshness.data_age == timedelta(seconds=10)


def test_is_stale_true_beyond_max_age_at_receipt() -> None:
    freshness = DataFreshness(
        data_timestamp=datetime(2026, 8, 27, 10, 0, 0, tzinfo=UTC),
        received_timestamp=datetime(2026, 8, 27, 10, 1, 0, tzinfo=UTC),
    )
    assert freshness.is_stale(timedelta(seconds=15)) is True
    assert freshness.is_stale(timedelta(seconds=120)) is False


def test_is_stale_true_when_consumed_late_even_if_fresh_at_receipt() -> None:
    freshness = DataFreshness(
        data_timestamp=datetime(2026, 8, 27, 10, 0, 0, tzinfo=UTC),
        received_timestamp=datetime(2026, 8, 27, 10, 0, 1, tzinfo=UTC),
    )
    later = datetime(2026, 8, 27, 10, 10, 0, tzinfo=UTC)
    assert freshness.is_stale(timedelta(seconds=15), as_of=later) is True
    assert freshness.is_stale(timedelta(seconds=15)) is False  # fresh at receipt


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValidationError):
        DataFreshness(
            data_timestamp=datetime(2026, 8, 27, 10, 0, 0),  # noqa: DTZ001 - deliberately naive, that's what's under test
            received_timestamp=datetime(2026, 8, 27, 10, 0, 1, tzinfo=UTC),
        )


def test_received_before_data_timestamp_rejected() -> None:
    with pytest.raises(ValidationError):
        DataFreshness(
            data_timestamp=datetime(2026, 8, 27, 10, 0, 10, tzinfo=UTC),
            received_timestamp=datetime(2026, 8, 27, 10, 0, 0, tzinfo=UTC),
        )


def test_freshness_class_values_match_architecture_doc() -> None:
    assert {c.value for c in FreshnessClass} == {
        "REAL_TIME",
        "SHORT_INTERVAL",
        "ANALYSIS_INTERVAL",
        "SLOW_REFRESH",
        "EVENT_DRIVEN",
    }
