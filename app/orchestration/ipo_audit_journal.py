"""IPO audit journal orchestration (Phase 8) — pure transforms from an
already-computed `IPOAnalysisResult` (+ optional comparison/hidden-
opportunity assessments) into the immutable, persistable snapshot shape.
No I/O of its own; the caller persists via `JsonlIPOAuditJournalRepository`.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.domain.ipo.audit_models import (
    AllotmentEstimateSnapshot,
    GMPObservationSnapshot,
    GMPSummarySnapshot,
    HiddenOpportunitySnapshot,
    IPOAnalysisSnapshot,
    IPOIdentitySnapshot,
    IPOListingOutcomeRecord,
    ListingRangeSnapshot,
    ShareholderQuotaSnapshot,
    SubscriptionCategorySnapshot,
    new_ipo_audit_id,
)
from app.domain.ipo.hidden_opportunity import HiddenOpportunityAssessment
from app.domain.ipo.listing_analysis import ListingOutcomeReconciliation
from app.domain.ipo.models import ListingRangeEstimate
from app.orchestration.ipo_intelligence import IPOAnalysisResult


def build_ipo_analysis_snapshot(
    result: IPOAnalysisResult,
    *,
    hidden_opportunity: HiddenOpportunityAssessment | None = None,
    audit_id: str | None = None,
    corrects_audit_id: str | None = None,
) -> IPOAnalysisSnapshot:
    """Never raises: `result.identity is None` (nothing found) is a
    real, honest, valid snapshot of "what this system knew at this
    moment" -- not an error to reject (see `IPOAnalysisSnapshot.
    data_available`, unlike the options side's `build_analysis_snapshot()`
    which DOES raise for a failed report, because that IS a caller-
    ordering bug there)."""
    identity_snapshot = None
    if result.identity is not None:
        i = result.identity
        identity_snapshot = IPOIdentitySnapshot(
            company_name=i.company_name, issue_type=i.issue_type.value, ipo_id=i.ipo_id, symbol=i.symbol,
            status=i.status, price_band_low=i.price_band_low, price_band_high=i.price_band_high,
            issue_size_crore=i.issue_size_crore, lot_size=i.lot_size, listing_price=i.listing_price,
            listing_exchange=i.listing_exchange, aggregate_subscription_times=i.aggregate_subscription_times,
            investor_categories=list(i.investor_categories), source=i.source, retrieved_at=i.retrieved_at,
        )

    gmp_snapshot = None
    if result.gmp_summary is not None:
        g = result.gmp_summary
        gmp_snapshot = GMPSummarySnapshot(
            observations=[GMPObservationSnapshot(source=o.source, value=o.value, observed_at=o.observed_at, retrieved_at=o.retrieved_at) for o in g.observations],
            minimum=g.minimum, maximum=g.maximum, median=g.median, source_count=g.source_count,
            freshness=g.freshness.value, sources_disagree=g.sources_disagree,
        )

    listing_range_snapshot = None
    if result.listing_range is not None:
        lr = result.listing_range
        listing_range_snapshot = ListingRangeSnapshot(indicative_low=lr.indicative_low, indicative_high=lr.indicative_high, gmp_median=lr.gmp_median)

    subscription_snapshots = []
    if result.subscription is not None:
        subscription_snapshots = [
            SubscriptionCategorySnapshot(category=c.category.value, shares_offered=c.shares_offered, times_subscribed=c.times_subscribed, as_of_day=c.as_of_day)
            for c in result.subscription.categories
        ]

    allotment_snapshot = None
    if result.allotment_estimate is not None:
        a = result.allotment_estimate
        allotment_snapshot = AllotmentEstimateSnapshot(estimability=a.estimability.value, estimated_probability=a.estimated_probability, chance_band=a.chance_band.value, methodology=a.methodology)

    quota_snapshot = None
    if result.shareholder_quota is not None:
        q = result.shareholder_quota
        quota_snapshot = ShareholderQuotaSnapshot(status=q.status.value, eligibility_company=q.eligibility_company, record_date=q.record_date, source=q.source)

    hidden_opportunity_snapshot = None
    if hidden_opportunity is not None:
        hidden_opportunity_snapshot = HiddenOpportunitySnapshot(
            label=hidden_opportunity.label.value, reasons=list(hidden_opportunity.reasons),
            evidence_considered=list(hidden_opportunity.evidence_considered), missing_evidence=list(hidden_opportunity.missing_evidence),
        )

    return IPOAnalysisSnapshot(
        audit_id=audit_id or new_ipo_audit_id(), analyzed_at=result.analyzed_at, query=result.query,
        data_available=result.data_available, identity=identity_snapshot, gmp=gmp_snapshot,
        listing_range=listing_range_snapshot, subscription_categories=subscription_snapshots,
        allotment=allotment_snapshot, shareholder_quota=quota_snapshot, hidden_opportunity=hidden_opportunity_snapshot,
        corrects_audit_id=corrects_audit_id,
    )


def build_listing_outcome_record(
    snapshot: IPOAnalysisSnapshot, *, reconciliation: ListingOutcomeReconciliation, recorded_at: datetime
) -> IPOListingOutcomeRecord:
    """Wraps `listing_analysis.reconcile_listing_outcome()`'s already-
    computed result into the persisted record shape -- computes nothing
    new itself."""
    return IPOListingOutcomeRecord(
        audit_id=snapshot.audit_id, recorded_at=recorded_at, listing_price=reconciliation.listing_price,
        listing_gain_pct=reconciliation.listing_gain_pct, gmp_accuracy_note=reconciliation.gmp_accuracy_note,
    )


def listing_range_from_snapshot(snapshot: IPOAnalysisSnapshot) -> ListingRangeEstimate | None:
    """Reconstructs enough of the original `ListingRangeEstimate` from a
    persisted snapshot to feed `reconcile_listing_outcome()` later --
    that function only ever reads `indicative_low`/`indicative_high` from
    this object, so `issue_price` below is a structurally-required but
    functionally-unused placeholder (`Decimal(0)`), not a real value.
    This is NOT a full round-trip of the original object (the snapshot
    never stored the rest), and callers must not treat it as one."""
    if snapshot.listing_range is None:
        return None
    lr = snapshot.listing_range
    return ListingRangeEstimate(
        issue_price=Decimal(0), gmp_low=None, gmp_high=None, gmp_median=lr.gmp_median,
        indicative_low=lr.indicative_low, indicative_mid=None, indicative_high=lr.indicative_high,
        indicative_premium_pct_low=None, indicative_premium_pct_high=None,
    )
