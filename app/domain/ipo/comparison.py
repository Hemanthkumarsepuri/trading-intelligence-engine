"""IPO comparison engine (Phase 2) — compares multiple IPOs across
EXPLICIT evidence dimensions, never collapsing them into one opaque
score. Pure domain logic: every input is an already-computed object
(`IPOIdentity`, `GMPSummary`, `AllotmentProbabilityEstimate`,
`ShareholderQuotaInfo`, ...) this module performs no I/O and fetches
nothing.

Deliberately NOT a ranking engine. `data_completeness` is the one
aggregate number this module produces, and it measures ONLY how much
evidence exists for an IPO -- never how "good" it is. Two IPOs with
identical `data_completeness` can have completely different, even
opposite, dimension values; this module never resolves that into a
winner. See `app.domain.ipo.hidden_opportunity` for the (also
non-ranking) pattern-based interpretation layer built on top of this.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from app.domain.ipo.models import (
    AllotmentEstimability,
    AllotmentProbabilityEstimate,
    GMPFreshness,
    GMPSummary,
    IPOIdentity,
    ListingRangeEstimate,
    ShareholderQuotaInfo,
    ShareholderQuotaStatus,
    SubscriptionCategory,
    SubscriptionSnapshot,
)


class EvidenceAvailability(str, Enum):
    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    NOT_AVAILABLE = "NOT_AVAILABLE"


@dataclass(frozen=True)
class ComparisonDimension:
    """One evidence dimension for one IPO. `is_provider_data` is
    meaningless (left `False`) when `availability` is `NOT_AVAILABLE` --
    there is no data to attribute a source to."""

    name: str
    availability: EvidenceAvailability
    value_summary: str
    is_provider_data: bool
    source: str | None
    detail: str


@dataclass(frozen=True)
class IPOComparisonInput:
    """Bundles the already-computed pieces one `IPOAnalysisResult`
    (`app.orchestration.ipo_intelligence`) carries -- this module needs
    no I/O of its own because every input here was already assembled by
    that orchestration layer."""

    identity: IPOIdentity
    gmp_summary: GMPSummary | None = None
    listing_range: ListingRangeEstimate | None = None
    subscription: SubscriptionSnapshot | None = None
    allotment_estimate: AllotmentProbabilityEstimate | None = None
    shareholder_quota: ShareholderQuotaInfo | None = None


@dataclass(frozen=True)
class IPOComparisonEntry:
    identity: IPOIdentity
    dimensions: list[ComparisonDimension]
    data_completeness: Decimal = field(default=Decimal(0))  # METRIC, descriptive only -- see module docstring


_DIM_IDENTITY = "Identity/status"
_DIM_PRICE_BAND = "Price band"
_DIM_ISSUE_SIZE = "Issue size"
_DIM_SUBSCRIPTION = "Aggregate subscription"
_DIM_RETAIL_SUBSCRIPTION = "Retail subscription"
_DIM_GMP = "GMP"
_DIM_GMP_FRESHNESS = "GMP freshness"
_DIM_LISTING_RANGE = "GMP-implied listing range"
_DIM_ALLOTMENT = "Allotment difficulty"
_DIM_SHAREHOLDER_QUOTA = "Shareholder quota"

DIMENSION_NAMES = [
    _DIM_IDENTITY, _DIM_PRICE_BAND, _DIM_ISSUE_SIZE, _DIM_SUBSCRIPTION, _DIM_RETAIL_SUBSCRIPTION,
    _DIM_GMP, _DIM_GMP_FRESHNESS, _DIM_LISTING_RANGE, _DIM_ALLOTMENT, _DIM_SHAREHOLDER_QUOTA,
]


def _identity_dimension(identity: IPOIdentity) -> ComparisonDimension:
    parts = [identity.company_name, identity.issue_type.value]
    if identity.status is not None:
        parts.append(identity.status)
    return ComparisonDimension(
        name=_DIM_IDENTITY, availability=EvidenceAvailability.SUPPORTED, value_summary=" | ".join(parts),
        is_provider_data=identity.source == "upstox", source=identity.source, detail="Real identity/status from the source that provided this IPO.",
    )


def _price_band_dimension(identity: IPOIdentity) -> ComparisonDimension:
    if identity.price_band_low is None and identity.price_band_high is None:
        return ComparisonDimension(_DIM_PRICE_BAND, EvidenceAvailability.NOT_AVAILABLE, "n/a", False, None, "No price band supplied.")
    if identity.price_band_low is not None and identity.price_band_high is not None:
        return ComparisonDimension(
            _DIM_PRICE_BAND, EvidenceAvailability.SUPPORTED, f"₹{identity.price_band_low}-{identity.price_band_high}",
            identity.source == "upstox", identity.source, "Real price band.",
        )
    return ComparisonDimension(
        _DIM_PRICE_BAND, EvidenceAvailability.PARTIALLY_SUPPORTED, "one bound only", identity.source == "upstox",
        identity.source, "Only one side of the price band is known.",
    )


def _issue_size_dimension(identity: IPOIdentity) -> ComparisonDimension:
    if identity.issue_size_crore is None:
        return ComparisonDimension(_DIM_ISSUE_SIZE, EvidenceAvailability.NOT_AVAILABLE, "n/a", False, None, "No issue size supplied.")
    return ComparisonDimension(
        _DIM_ISSUE_SIZE, EvidenceAvailability.SUPPORTED, f"₹{identity.issue_size_crore} crore",
        identity.source == "upstox", identity.source, "Real issue size.",
    )


def _subscription_dimension(identity: IPOIdentity) -> ComparisonDimension:
    if identity.aggregate_subscription_times is None:
        return ComparisonDimension(_DIM_SUBSCRIPTION, EvidenceAvailability.NOT_AVAILABLE, "n/a", False, None, "No aggregate subscription reported yet.")
    return ComparisonDimension(
        _DIM_SUBSCRIPTION, EvidenceAvailability.PARTIALLY_SUPPORTED, f"{identity.aggregate_subscription_times}x overall",
        identity.source == "upstox", identity.source,
        "A single aggregate multiple across ALL categories (Upstox's real feed does not break this out by category) -- PARTIALLY_SUPPORTED because it cannot answer a retail-specific question alone.",
    )


def _retail_subscription_dimension(subscription: SubscriptionSnapshot | None) -> ComparisonDimension:
    if subscription is None:
        return ComparisonDimension(_DIM_RETAIL_SUBSCRIPTION, EvidenceAvailability.NOT_AVAILABLE, "n/a", False, None, "No category-wise subscription supplied.")
    retail = next((c for c in subscription.categories if c.category == SubscriptionCategory.RETAIL), None)
    if retail is None or retail.times_subscribed is None:
        return ComparisonDimension(_DIM_RETAIL_SUBSCRIPTION, EvidenceAvailability.INSUFFICIENT_DATA, "n/a", False, subscription.source, "Category-wise subscription was supplied but has no retail figure.")
    return ComparisonDimension(
        _DIM_RETAIL_SUBSCRIPTION, EvidenceAvailability.SUPPORTED, f"{retail.times_subscribed}x retail",
        False, subscription.source, "Caller-supplied category-wise retail subscription.",
    )


def _gmp_dimension(gmp_summary: GMPSummary | None) -> ComparisonDimension:
    if gmp_summary is None or gmp_summary.source_count == 0:
        return ComparisonDimension(_DIM_GMP, EvidenceAvailability.NOT_AVAILABLE, "n/a", False, None, "No GMP observation supplied -- GMP is unofficial and has no authorized provider (see docs/data-sources/IPO_DATA_SOURCE_DECISION.md).")
    summary = f"₹{gmp_summary.median}" if gmp_summary.minimum == gmp_summary.maximum else f"₹{gmp_summary.minimum}-{gmp_summary.maximum} (median ₹{gmp_summary.median})"
    availability = EvidenceAvailability.SUPPORTED if gmp_summary.source_count >= 2 else EvidenceAvailability.PARTIALLY_SUPPORTED
    return ComparisonDimension(
        _DIM_GMP, availability, summary, False, f"{gmp_summary.source_count} caller-supplied source(s)",
        "GMP is unofficial/unregulated and always caller-supplied in this product." + (" Sources disagree." if gmp_summary.sources_disagree else ""),
    )


def _gmp_freshness_dimension(gmp_summary: GMPSummary | None) -> ComparisonDimension:
    if gmp_summary is None or gmp_summary.source_count == 0:
        return ComparisonDimension(_DIM_GMP_FRESHNESS, EvidenceAvailability.NOT_AVAILABLE, "n/a", False, None, "No GMP observation to assess freshness of.")

    availability = EvidenceAvailability.SUPPORTED if gmp_summary.freshness in (GMPFreshness.VERY_FRESH, GMPFreshness.RECENT) else EvidenceAvailability.PARTIALLY_SUPPORTED
    return ComparisonDimension(_DIM_GMP_FRESHNESS, availability, gmp_summary.freshness.value, False, None, "Freshness of the caller-supplied GMP observation(s).")


def _listing_range_dimension(listing_range: ListingRangeEstimate | None) -> ComparisonDimension:
    if listing_range is None:
        return ComparisonDimension(_DIM_LISTING_RANGE, EvidenceAvailability.NOT_AVAILABLE, "n/a", False, None, "No GMP-implied listing range could be computed (requires both a price band and GMP).")
    return ComparisonDimension(
        _DIM_LISTING_RANGE, EvidenceAvailability.SUPPORTED,
        f"₹{listing_range.indicative_low}-{listing_range.indicative_high}", False, None, listing_range.label,
    )


def _allotment_dimension(allotment_estimate: AllotmentProbabilityEstimate | None) -> ComparisonDimension:

    if allotment_estimate is None or allotment_estimate.estimability == AllotmentEstimability.NOT_RELIABLY_ESTIMABLE:
        return ComparisonDimension(_DIM_ALLOTMENT, EvidenceAvailability.NOT_AVAILABLE, "n/a", False, None, "Not reliably estimable from available data.")
    availability = (
        EvidenceAvailability.SUPPORTED if allotment_estimate.estimability in (AllotmentEstimability.ESTIMATED, AllotmentEstimability.UNDERSUBSCRIBED_EXPECTED)
        else EvidenceAvailability.PARTIALLY_SUPPORTED
    )
    summary = allotment_estimate.chance_band.value if allotment_estimate.estimated_probability is not None else "n/a"
    return ComparisonDimension(_DIM_ALLOTMENT, availability, summary, False, None, allotment_estimate.methodology)


def _shareholder_quota_dimension(quota: ShareholderQuotaInfo | None) -> ComparisonDimension:
    if quota is None:
        return ComparisonDimension(_DIM_SHAREHOLDER_QUOTA, EvidenceAvailability.NOT_AVAILABLE, "n/a", False, None, "No shareholder-quota information supplied.")
    if quota.status == ShareholderQuotaStatus.NOT_CONFIRMED:
        return ComparisonDimension(_DIM_SHAREHOLDER_QUOTA, EvidenceAvailability.INSUFFICIENT_DATA, "NOT_CONFIRMED", False, quota.source, quota.detail)
    is_provider = quota.source is not None and quota.source.startswith("upstox")
    availability = EvidenceAvailability.PARTIALLY_SUPPORTED if quota.record_date is None else EvidenceAvailability.SUPPORTED
    return ComparisonDimension(_DIM_SHAREHOLDER_QUOTA, availability, "CONFIRMED (category exists)" if quota.record_date is None else "CONFIRMED (with terms)", is_provider, quota.source, quota.detail)


def build_comparison_entry(entry_input: IPOComparisonInput) -> IPOComparisonEntry:
    dimensions = [
        _identity_dimension(entry_input.identity),
        _price_band_dimension(entry_input.identity),
        _issue_size_dimension(entry_input.identity),
        _subscription_dimension(entry_input.identity),
        _retail_subscription_dimension(entry_input.subscription),
        _gmp_dimension(entry_input.gmp_summary),
        _gmp_freshness_dimension(entry_input.gmp_summary),
        _listing_range_dimension(entry_input.listing_range),
        _allotment_dimension(entry_input.allotment_estimate),
        _shareholder_quota_dimension(entry_input.shareholder_quota),
    ]
    usable = sum(1 for d in dimensions if d.availability in (EvidenceAvailability.SUPPORTED, EvidenceAvailability.PARTIALLY_SUPPORTED))
    completeness = Decimal(usable) / Decimal(len(dimensions))
    return IPOComparisonEntry(identity=entry_input.identity, dimensions=dimensions, data_completeness=completeness)


def build_comparison(inputs: list[IPOComparisonInput]) -> list[IPOComparisonEntry]:
    """Part 2 -- one entry per input, in the SAME order given (never
    reordered/ranked by this function; a caller wanting a particular
    order applies it to `inputs` before calling this, or sorts the
    result by whatever single dimension it explicitly chooses to)."""
    return [build_comparison_entry(i) for i in inputs]
