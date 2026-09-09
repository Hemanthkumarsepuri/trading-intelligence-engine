"""IPO comparison engine tests -- the central invariant: missing data
must NEVER become zero/false; it must become NOT_AVAILABLE/
INSUFFICIENT_DATA, explicitly."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.ipo.comparison import (
    DIMENSION_NAMES,
    EvidenceAvailability,
    IPOComparisonInput,
    build_comparison,
    build_comparison_entry,
)
from app.domain.ipo.gmp_analysis import build_gmp_summary
from app.domain.ipo.models import (
    AllotmentChanceBand,
    AllotmentEstimability,
    AllotmentProbabilityEstimate,
    CategorySubscription,
    GMPObservation,
    IPOIdentity,
    IssueType,
    ShareholderQuotaInfo,
    ShareholderQuotaStatus,
    SubscriptionCategory,
    SubscriptionSnapshot,
)

_NOW = datetime(2026, 8, 30, 10, 0, tzinfo=UTC)


def _bare_identity(**overrides: object) -> IPOIdentity:
    base: dict[str, object] = {"company_name": "Example Corp"}
    base.update(overrides)
    return IPOIdentity(**base)  # type: ignore[arg-type]


def test_bare_identity_produces_mostly_not_available_never_zero() -> None:
    entry = build_comparison_entry(IPOComparisonInput(identity=_bare_identity()))
    assert len(entry.dimensions) == len(DIMENSION_NAMES)
    non_identity = [d for d in entry.dimensions if d.name != "Identity/status"]
    assert all(d.availability == EvidenceAvailability.NOT_AVAILABLE for d in non_identity)
    # Never a fabricated "0" or "false" value_summary standing in for missing data.
    assert all(d.value_summary == "n/a" for d in non_identity)


def test_data_completeness_reflects_only_available_dimensions() -> None:
    bare = build_comparison_entry(IPOComparisonInput(identity=_bare_identity()))
    assert bare.data_completeness == Decimal(1) / Decimal(len(DIMENSION_NAMES))  # only "Identity/status"

    richer_identity = _bare_identity(price_band_high=Decimal("300"), price_band_low=Decimal("285"), issue_size_crore=Decimal("650"))
    richer = build_comparison_entry(IPOComparisonInput(identity=richer_identity))
    assert richer.data_completeness > bare.data_completeness


def test_gmp_dimension_marks_source_as_caller_supplied_never_provider() -> None:
    identity = _bare_identity(price_band_high=Decimal("300"))
    gmp_summary = build_gmp_summary(
        [GMPObservation(source="TrackerA", value=Decimal("70"), observed_at=_NOW, retrieved_at=_NOW)],
        as_of=_NOW, very_fresh_max_age=timedelta(hours=2), recent_max_age=timedelta(hours=12), aging_max_age=timedelta(days=2),
    )
    entry = build_comparison_entry(IPOComparisonInput(identity=identity, gmp_summary=gmp_summary))
    gmp_dim = next(d for d in entry.dimensions if d.name == "GMP")
    assert gmp_dim.availability == EvidenceAvailability.PARTIALLY_SUPPORTED  # single source
    assert gmp_dim.is_provider_data is False


def test_aggregate_subscription_is_partially_supported_never_full_retail_answer() -> None:
    """The real Upstox feed only gives ONE aggregate number -- this must
    never be silently treated as equivalent to category-wise retail data."""
    identity = _bare_identity(aggregate_subscription_times=Decimal("25"), source="upstox")
    entry = build_comparison_entry(IPOComparisonInput(identity=identity))
    dim = next(d for d in entry.dimensions if d.name == "Aggregate subscription")
    assert dim.availability == EvidenceAvailability.PARTIALLY_SUPPORTED
    assert dim.is_provider_data is True

    retail_dim = next(d for d in entry.dimensions if d.name == "Retail subscription")
    assert retail_dim.availability == EvidenceAvailability.NOT_AVAILABLE  # nothing category-wise supplied


def test_retail_subscription_supported_when_caller_supplies_category_data() -> None:
    identity = _bare_identity()
    subscription = SubscriptionSnapshot(
        captured_at=_NOW, source="NSE bidding page (manual)",
        categories=[CategorySubscription(category=SubscriptionCategory.RETAIL, shares_offered=10000, shares_bid_for=None, times_subscribed=Decimal("3"), as_of_day=3)],
    )
    entry = build_comparison_entry(IPOComparisonInput(identity=identity, subscription=subscription))
    dim = next(d for d in entry.dimensions if d.name == "Retail subscription")
    assert dim.availability == EvidenceAvailability.SUPPORTED
    assert dim.is_provider_data is False


def test_shareholder_quota_not_confirmed_is_insufficient_not_not_available() -> None:
    identity = _bare_identity()
    quota = ShareholderQuotaInfo(
        status=ShareholderQuotaStatus.NOT_CONFIRMED, eligibility_company=None, parent_company=None, record_date=None,
        minimum_holding_required=None, reserved_shares=None, maximum_application_shares=None,
        both_retail_and_shareholder_allowed=None, source=None, detail="not confirmed",
    )
    entry = build_comparison_entry(IPOComparisonInput(identity=identity, shareholder_quota=quota))
    dim = next(d for d in entry.dimensions if d.name == "Shareholder quota")
    assert dim.availability == EvidenceAvailability.INSUFFICIENT_DATA


def test_allotment_dimension_undersubscribed_is_supported() -> None:
    identity = _bare_identity()
    estimate = AllotmentProbabilityEstimate(
        estimability=AllotmentEstimability.UNDERSUBSCRIBED_EXPECTED, methodology="m", assumptions=[],
        retail_shares_offered=1000, lot_size=100, retail_lots_available=10, valid_retail_applications=5,
        retail_subscription_times=None, estimated_probability=Decimal(1), chance_band=AllotmentChanceBand.HIGH, detail="d",
    )
    entry = build_comparison_entry(IPOComparisonInput(identity=identity, allotment_estimate=estimate))
    dim = next(d for d in entry.dimensions if d.name == "Allotment difficulty")
    assert dim.availability == EvidenceAvailability.SUPPORTED
    assert dim.value_summary == "HIGH"


def test_build_comparison_preserves_input_order_never_reorders() -> None:
    entries_input = [
        IPOComparisonInput(identity=_bare_identity(company_name="Z Corp")),
        IPOComparisonInput(identity=_bare_identity(company_name="A Corp")),
    ]
    results = build_comparison(entries_input)
    assert [r.identity.company_name for r in results] == ["Z Corp", "A Corp"]  # NOT alphabetized/ranked


def test_mainboard_vs_sme_identity_dimension_shows_real_issue_type() -> None:
    entry = build_comparison_entry(IPOComparisonInput(identity=_bare_identity(issue_type=IssueType.SME, status="open")))
    dim = next(d for d in entry.dimensions if d.name == "Identity/status")
    assert "SME" in dim.value_summary
    assert "open" in dim.value_summary
