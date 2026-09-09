"""Sprint 7B, Objective 8 -- pure unit tests for IPO demand quality."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.domain.ipo.demand_quality import (
    DemandQualityBand,
    build_demand_quality_summary,
    build_progression_note,
)
from app.domain.ipo.models import CategorySubscription, SubscriptionCategory, SubscriptionSnapshot

_CAPTURED = datetime(2026, 8, 30, 10, 0, tzinfo=UTC)
_STRONG_MIN = Decimal("10")
_MODERATE_MIN = Decimal("2")
_WEAK_MIN = Decimal("1")


def _snapshot(qib: Decimal | None, nii: Decimal | None, retail: Decimal | None, day: int | None = 1) -> SubscriptionSnapshot:
    return SubscriptionSnapshot(
        captured_at=_CAPTURED,
        categories=[
            CategorySubscription(SubscriptionCategory.QIB, None, None, qib, day),
            CategorySubscription(SubscriptionCategory.NII_HNI, None, None, nii, day),
            CategorySubscription(SubscriptionCategory.RETAIL, None, None, retail, day),
        ],
        source="caller",
    )


def test_total_demand_uses_aggregate_never_category_figures() -> None:
    snapshot = _snapshot(Decimal("50"), Decimal("5"), Decimal("1.5"))
    summary = build_demand_quality_summary(snapshot=snapshot, aggregate_subscription_times=Decimal("20"), strong_min=_STRONG_MIN, moderate_min=_MODERATE_MIN, weak_min=_WEAK_MIN)
    assert summary.total_demand.times_subscribed == Decimal("20")
    assert summary.total_demand.band == DemandQualityBand.STRONG


def test_category_quality_independent_of_aggregate() -> None:
    """Sprint 7B, Objective 8 -- retail quality is never inferred from the
    aggregate figure, even when only the aggregate is known."""
    summary = build_demand_quality_summary(snapshot=None, aggregate_subscription_times=Decimal("50"), strong_min=_STRONG_MIN, moderate_min=_MODERATE_MIN, weak_min=_WEAK_MIN)
    assert summary.retail_quality.band == DemandQualityBand.UNKNOWN
    assert summary.qib_quality.band == DemandQualityBand.UNKNOWN
    assert summary.nii_quality.band == DemandQualityBand.UNKNOWN


def test_category_bands_reflect_real_per_category_figures() -> None:
    snapshot = _snapshot(qib=Decimal("15"), nii=Decimal("3"), retail=Decimal("0.5"))
    summary = build_demand_quality_summary(snapshot=snapshot, aggregate_subscription_times=None, strong_min=_STRONG_MIN, moderate_min=_MODERATE_MIN, weak_min=_WEAK_MIN)
    assert summary.qib_quality.band == DemandQualityBand.STRONG
    assert summary.nii_quality.band == DemandQualityBand.MODERATE
    assert summary.retail_quality.band == DemandQualityBand.UNSUBSCRIBED


def test_progression_none_when_no_snapshots() -> None:
    assert "no subscription snapshots" in build_progression_note([])


def test_progression_insufficient_with_one_dated_snapshot() -> None:
    note = build_progression_note([_snapshot(Decimal("1"), Decimal("1"), Decimal("1"), day=1)])
    assert "only 1 dated snapshot" in note


def test_progression_describes_real_day_over_day_change() -> None:
    day1 = _snapshot(Decimal("1"), Decimal("1"), Decimal("1"), day=1)
    day2 = _snapshot(Decimal("3"), Decimal("2"), Decimal("1.5"), day=2)
    note = build_progression_note([day1, day2])
    assert "Day 1" in note and "Day 2" in note


def test_progression_never_fabricates_a_missing_day() -> None:
    """Only day 1 and day 3 supplied -- day 2 is never invented."""
    day1 = _snapshot(Decimal("1"), Decimal("1"), Decimal("1"), day=1)
    day3 = _snapshot(Decimal("5"), Decimal("4"), Decimal("2"), day=3)
    note = build_progression_note([day1, day3])
    assert "Day 2" not in note
    assert "Day 1" in note and "Day 3" in note
