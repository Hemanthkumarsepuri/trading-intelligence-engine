"""IPO demand quality (Sprint 7B, Objective 8) -- TOTAL DEMAND / QIB
QUALITY / NII QUALITY / RETAIL QUALITY / PROGRESSION, each computed
independently and never inferred from one another.

Same discipline as every other IPO module: pure, no I/O. Category-wise
quality bands come ONLY from a real, caller-supplied `SubscriptionSnapshot`
(`app.domain.ipo.models.CategorySubscription.times_subscribed`) -- never
approximated from the single aggregate multiple Upstox's real feed
reports (`IPOIdentity.aggregate_subscription_times`), which is explicitly
NOT a category breakdown (see `models.py`'s own docstring on that field).
`TOTAL DEMAND` is the one band allowed to use the aggregate figure,
because that is genuinely what it measures.

PROGRESSION describes real day-over-day change across whatever
`SubscriptionSnapshot`s the caller supplies (append-only, one per day, per
`SubscriptionSnapshot`'s own docstring) -- never a projection of what a
future day's subscription will be.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from app.domain.ipo.models import SubscriptionCategory, SubscriptionSnapshot


class DemandQualityBand(str, Enum):
    STRONG = "STRONG"
    MODERATE = "MODERATE"
    WEAK = "WEAK"
    UNSUBSCRIBED = "UNSUBSCRIBED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class CategoryDemandQuality:
    category: SubscriptionCategory
    times_subscribed: Decimal | None
    band: DemandQualityBand
    detail: str


@dataclass(frozen=True)
class DemandQualitySummary:
    total_demand: CategoryDemandQuality  # category is a placeholder (OTHER); this row represents the aggregate, not one real category
    qib_quality: CategoryDemandQuality
    nii_quality: CategoryDemandQuality
    retail_quality: CategoryDemandQuality
    progression: str


def _band(times_subscribed: Decimal | None, *, strong_min: Decimal, moderate_min: Decimal, weak_min: Decimal) -> DemandQualityBand:
    if times_subscribed is None:
        return DemandQualityBand.UNKNOWN
    if times_subscribed < weak_min:
        return DemandQualityBand.UNSUBSCRIBED
    if times_subscribed < moderate_min:
        return DemandQualityBand.WEAK
    if times_subscribed < strong_min:
        return DemandQualityBand.MODERATE
    return DemandQualityBand.STRONG


def _category_quality(
    category: SubscriptionCategory, snapshot: SubscriptionSnapshot | None, *, strong_min: Decimal, moderate_min: Decimal, weak_min: Decimal,
) -> CategoryDemandQuality:
    row = next((c for c in snapshot.categories if c.category == category), None) if snapshot is not None else None
    if row is None:
        return CategoryDemandQuality(category, None, DemandQualityBand.UNKNOWN, f"no {category.value} figure in the supplied subscription snapshot")
    band = _band(row.times_subscribed, strong_min=strong_min, moderate_min=moderate_min, weak_min=weak_min)
    detail = f"{row.times_subscribed}x subscribed" if row.times_subscribed is not None else f"{category.value} row present but carries no times_subscribed value"
    return CategoryDemandQuality(category, row.times_subscribed, band, detail)


def build_demand_quality_summary(
    *,
    snapshot: SubscriptionSnapshot | None,
    aggregate_subscription_times: Decimal | None,
    strong_min: Decimal,
    moderate_min: Decimal,
    weak_min: Decimal,
    snapshots_for_progression: list[SubscriptionSnapshot] | None = None,
) -> DemandQualitySummary:
    total = CategoryDemandQuality(
        SubscriptionCategory.OTHER, aggregate_subscription_times,
        _band(aggregate_subscription_times, strong_min=strong_min, moderate_min=moderate_min, weak_min=weak_min),
        f"{aggregate_subscription_times}x aggregate across all categories" if aggregate_subscription_times is not None else "no aggregate subscription figure supplied",
    )
    qib = _category_quality(SubscriptionCategory.QIB, snapshot, strong_min=strong_min, moderate_min=moderate_min, weak_min=weak_min)
    nii = _category_quality(SubscriptionCategory.NII_HNI, snapshot, strong_min=strong_min, moderate_min=moderate_min, weak_min=weak_min)
    retail = _category_quality(SubscriptionCategory.RETAIL, snapshot, strong_min=strong_min, moderate_min=moderate_min, weak_min=weak_min)
    progression = build_progression_note(snapshots_for_progression or [])
    return DemandQualitySummary(total_demand=total, qib_quality=qib, nii_quality=nii, retail_quality=retail, progression=progression)


def build_progression_note(snapshots: list[SubscriptionSnapshot]) -> str:
    """Real day-over-day change, described from whatever snapshots the
    caller actually supplied -- never a projection. Snapshots without a
    known `as_of_day` are reported but cannot be ordered against the
    others, so they are listed separately rather than guessed into a
    sequence."""
    if not snapshots:
        return "PROGRESSION: no subscription snapshots supplied."
    dated = sorted((s for s in snapshots if any(c.as_of_day is not None for c in s.categories)), key=lambda s: min(c.as_of_day for c in s.categories if c.as_of_day is not None))
    dated_ids = {id(s) for s in dated}
    undated = [s for s in snapshots if id(s) not in dated_ids]
    if len(dated) < 2:
        parts = [f"PROGRESSION: only {len(dated)} dated snapshot(s) supplied -- day-over-day change not computable."]
    else:
        entries = []
        prev_agg: Decimal | None = None
        for s in dated:
            day = next((c.as_of_day for c in s.categories if c.as_of_day is not None), None)
            agg = next((c.times_subscribed for c in s.categories if c.category == SubscriptionCategory.OTHER), None)
            if agg is None and s.categories:
                # No aggregate row -- report the highest real category figure as a stand-in label, not a computed total.
                agg = max((c.times_subscribed for c in s.categories if c.times_subscribed is not None), default=None)
            if agg is not None and prev_agg is not None:
                entries.append(f"Day {day}: {agg}x ({'+' if agg >= prev_agg else ''}{agg - prev_agg:+.2f})")
            elif agg is not None:
                entries.append(f"Day {day}: {agg}x")
            else:
                entries.append(f"Day {day}: no usable figure")
            prev_agg = agg if agg is not None else prev_agg
        parts = ["PROGRESSION: " + " -> ".join(entries)]
    if undated:
        parts.append(f"({len(undated)} additional snapshot(s) with no as_of_day, not orderable)")
    return " ".join(parts)
