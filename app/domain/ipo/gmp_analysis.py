"""GMP aggregation and GMP-implied listing range (Parts 4/5).

Pure functions only -- every input is caller-supplied `GMPObservation`s
(never fetched here; see `docs/data-sources/IPO_DATA_SOURCE_DECISION.md`).
Never picks the highest reported figure; always surfaces the full range
and an honest source-disagreement flag.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from app.domain.ipo.models import GMPFreshness, GMPObservation, GMPSummary, ListingRangeEstimate


def _median(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / Decimal(2)


def classify_gmp_freshness(
    age: timedelta | None,
    *,
    very_fresh_max_age: timedelta,
    recent_max_age: timedelta,
    aging_max_age: timedelta,
) -> GMPFreshness:
    """THRESHOLD-classified: every cutoff is a required, undefaulted
    parameter -- there is no single objectively-correct "how old is too
    old" for GMP, exactly like every other freshness threshold in this
    codebase (see `app.domain.options.freshness_label`)."""
    if age is None:
        return GMPFreshness.UNKNOWN
    if age <= very_fresh_max_age:
        return GMPFreshness.VERY_FRESH
    if age <= recent_max_age:
        return GMPFreshness.RECENT
    if age <= aging_max_age:
        return GMPFreshness.AGING
    return GMPFreshness.STALE


def build_gmp_summary(
    observations: list[GMPObservation],
    *,
    as_of: datetime,
    very_fresh_max_age: timedelta,
    recent_max_age: timedelta,
    aging_max_age: timedelta,
    previous_summary: GMPSummary | None = None,
) -> GMPSummary:
    """METRIC aggregation over whatever observations were supplied.
    `previous_summary`, if given, lets the caller track change over time
    (Part 4's "GMP change") without this function needing to read or
    write any persistence itself -- purely a caller-supplied prior value.
    """
    if not observations:
        return GMPSummary(
            observations=[], minimum=None, maximum=None, median=None, source_count=0,
            latest_observed_at=None, freshness=GMPFreshness.UNKNOWN, sources_disagree=False,
        )

    values = [o.value for o in observations]
    minimum, maximum = min(values), max(values)
    median = _median(values)

    timestamped = [o.observed_at for o in observations if o.observed_at is not None]
    latest_observed_at = max(timestamped) if timestamped else None
    age = (as_of - latest_observed_at) if latest_observed_at is not None else None
    freshness = classify_gmp_freshness(
        age, very_fresh_max_age=very_fresh_max_age, recent_max_age=recent_max_age, aging_max_age=aging_max_age
    )

    previous_value = previous_summary.median if previous_summary is not None else None
    change = (median - previous_value) if previous_value is not None else None
    change_pct = (change / previous_value * Decimal(100)) if change is not None and previous_value is not None and previous_value != 0 else None

    return GMPSummary(
        observations=list(observations), minimum=minimum, maximum=maximum, median=median,
        source_count=len(observations), latest_observed_at=latest_observed_at, freshness=freshness,
        sources_disagree=minimum != maximum, previous_value_for_change=previous_value,
        change=change, change_pct=change_pct,
    )


def build_listing_range_estimate(issue_price: Decimal, gmp_summary: GMPSummary) -> ListingRangeEstimate | None:
    """Part 5. Returns `None` when there is genuinely no GMP to build a
    range from (never a range anchored on a fabricated GMP)."""
    if gmp_summary.source_count == 0 or gmp_summary.minimum is None or gmp_summary.maximum is None:
        return None

    low = issue_price + gmp_summary.minimum
    high = issue_price + gmp_summary.maximum
    mid = issue_price + gmp_summary.median if gmp_summary.median is not None else None

    pct_low = (gmp_summary.minimum / issue_price * Decimal(100)) if issue_price != 0 else None
    pct_high = (gmp_summary.maximum / issue_price * Decimal(100)) if issue_price != 0 else None

    return ListingRangeEstimate(
        issue_price=issue_price, gmp_low=gmp_summary.minimum, gmp_high=gmp_summary.maximum, gmp_median=gmp_summary.median,
        indicative_low=low, indicative_mid=mid, indicative_high=high,
        indicative_premium_pct_low=pct_low, indicative_premium_pct_high=pct_high,
    )
