"""IPO Intelligence -- GMP aggregation/freshness/listing-range tests.
Every observation used here is clearly synthetic test data, never a real
company's real GMP (no live GMP source exists -- see
docs/data-sources/IPO_DATA_SOURCE_DECISION.md)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.ipo.gmp_analysis import (
    build_gmp_summary,
    build_listing_range_estimate,
    classify_gmp_freshness,
)
from app.domain.ipo.models import GMPFreshness, GMPObservation, GMPSummary

_NOW = datetime(2026, 8, 30, 10, 0, tzinfo=UTC)
_FRESHNESS_KW = {"very_fresh_max_age": timedelta(hours=2), "recent_max_age": timedelta(hours=12), "aging_max_age": timedelta(days=2)}


def _summary(observations: list[GMPObservation], *, as_of: datetime, previous_summary: GMPSummary | None = None) -> GMPSummary:
    return build_gmp_summary(
        observations, as_of=as_of, very_fresh_max_age=timedelta(hours=2),
        recent_max_age=timedelta(hours=12), aging_max_age=timedelta(days=2), previous_summary=previous_summary,
    )


def test_freshness_very_fresh() -> None:
    assert classify_gmp_freshness(timedelta(minutes=30), **_FRESHNESS_KW) == GMPFreshness.VERY_FRESH


def test_freshness_recent() -> None:
    assert classify_gmp_freshness(timedelta(hours=6), **_FRESHNESS_KW) == GMPFreshness.RECENT


def test_freshness_aging() -> None:
    assert classify_gmp_freshness(timedelta(days=1), **_FRESHNESS_KW) == GMPFreshness.AGING


def test_freshness_stale() -> None:
    assert classify_gmp_freshness(timedelta(days=5), **_FRESHNESS_KW) == GMPFreshness.STALE


def test_freshness_unknown_when_age_is_none() -> None:
    assert classify_gmp_freshness(None, **_FRESHNESS_KW) == GMPFreshness.UNKNOWN


def test_gmp_summary_empty_observations() -> None:
    summary = _summary([], as_of=_NOW)
    assert summary.source_count == 0
    assert summary.minimum is None and summary.maximum is None and summary.median is None
    assert summary.freshness == GMPFreshness.UNKNOWN
    assert summary.sources_disagree is False


def test_gmp_summary_single_source() -> None:
    obs = [GMPObservation(source="TrackerA", value=Decimal("90"), observed_at=_NOW - timedelta(minutes=10), retrieved_at=_NOW)]
    summary = _summary(obs, as_of=_NOW)
    assert summary.minimum == summary.maximum == summary.median == Decimal("90")
    assert summary.source_count == 1
    assert summary.sources_disagree is False
    assert summary.freshness == GMPFreshness.VERY_FRESH


def test_gmp_summary_multi_source_never_picks_the_highest() -> None:
    obs = [
        GMPObservation(source="TrackerA", value=Decimal("70"), observed_at=_NOW - timedelta(hours=1), retrieved_at=_NOW),
        GMPObservation(source="TrackerB", value=Decimal("100"), observed_at=_NOW - timedelta(hours=1), retrieved_at=_NOW),
        GMPObservation(source="TrackerC", value=Decimal("85"), observed_at=_NOW - timedelta(hours=1), retrieved_at=_NOW),
    ]
    summary = _summary(obs, as_of=_NOW)
    assert summary.minimum == Decimal("70")
    assert summary.maximum == Decimal("100")
    assert summary.median == Decimal("85")
    assert summary.source_count == 3
    assert summary.sources_disagree is True  # honest flag, never resolved away


def test_gmp_summary_even_count_median() -> None:
    obs = [
        GMPObservation(source="A", value=Decimal("60"), observed_at=_NOW, retrieved_at=_NOW),
        GMPObservation(source="B", value=Decimal("80"), observed_at=_NOW, retrieved_at=_NOW),
    ]
    summary = _summary(obs, as_of=_NOW)
    assert summary.median == Decimal("70")


def test_gmp_summary_tracks_change_from_previous() -> None:
    previous = _summary(
        [GMPObservation(source="A", value=Decimal("60"), observed_at=_NOW - timedelta(hours=6), retrieved_at=_NOW - timedelta(hours=6))],
        as_of=_NOW - timedelta(hours=6),
    )
    latest = _summary(
        [GMPObservation(source="A", value=Decimal("75"), observed_at=_NOW, retrieved_at=_NOW)],
        as_of=_NOW, previous_summary=previous,
    )
    assert latest.previous_value_for_change == Decimal("60")
    assert latest.change == Decimal("15")
    assert latest.change_pct == Decimal("25")


def test_gmp_summary_no_timestamped_observations_is_unknown_freshness() -> None:
    obs = [GMPObservation(source="A", value=Decimal("50"), observed_at=None, retrieved_at=_NOW)]
    summary = _summary(obs, as_of=_NOW)
    assert summary.freshness == GMPFreshness.UNKNOWN
    assert summary.latest_observed_at is None


def test_listing_range_estimate_is_none_without_gmp() -> None:
    empty_summary = _summary([], as_of=_NOW)
    assert build_listing_range_estimate(Decimal("200"), empty_summary) is None


def test_listing_range_estimate_computes_low_mid_high() -> None:
    obs = [
        GMPObservation(source="A", value=Decimal("70"), observed_at=_NOW, retrieved_at=_NOW),
        GMPObservation(source="B", value=Decimal("100"), observed_at=_NOW, retrieved_at=_NOW),
    ]
    summary = _summary(obs, as_of=_NOW)
    estimate = build_listing_range_estimate(Decimal("200"), summary)
    assert estimate is not None
    assert estimate.indicative_low == Decimal("270")
    assert estimate.indicative_high == Decimal("300")
    assert summary.median is not None
    assert estimate.indicative_mid == Decimal("200") + summary.median
    assert "NOT A FORECAST" in estimate.label
    assert "may differ materially" in estimate.caveat


def test_listing_range_estimate_never_silently_drops_the_disclaimer() -> None:
    """The label/caveat are dataclass fields with defaults baked in --
    a caller cannot construct a valid estimate and forget them."""
    obs = [GMPObservation(source="A", value=Decimal("50"), observed_at=_NOW, retrieved_at=_NOW)]
    summary = _summary(obs, as_of=_NOW)
    estimate = build_listing_range_estimate(Decimal("100"), summary)
    assert estimate is not None
    assert estimate.label != ""
    assert estimate.caveat != ""


@pytest.mark.parametrize("issue_price", [Decimal("0")])
def test_listing_range_estimate_handles_zero_issue_price_without_crashing(issue_price: Decimal) -> None:
    obs = [GMPObservation(source="A", value=Decimal("10"), observed_at=_NOW, retrieved_at=_NOW)]
    summary = _summary(obs, as_of=_NOW)
    estimate = build_listing_range_estimate(issue_price, summary)
    assert estimate is not None
    assert estimate.indicative_premium_pct_low is None  # division by zero avoided, not fabricated
