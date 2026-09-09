"""IPO Intelligence orchestration — routes a query to IPO analysis and
assembles the result from real, official Upstox IPO data plus whatever
GMP/subscription/shareholder-quota data the CALLER additionally supplies.

`analyze_ipo_query()` itself performs NO I/O and is unchanged since this
module's first version -- a pure assembler over caller-supplied domain
objects, still returning an honest `NOT_AVAILABLE` result when nothing is
supplied. `discover_ipos()`/`fetch_ipo_detail()`/`analyze_ipo_query_live()`
are ADDITIVE (see docs/data-sources/IPO_DATA_SOURCE_DECISION.md's
CORRECTED FINDING): Upstox's real, authorized `/v2/ipos`/`/v2/ipos/{id}`
is now the primary IPO calendar/identity/timeline/registrar/listing-price
source. GMP remains unofficial and still comes only from the caller --
that part of the architecture is unchanged.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from app.data.normalization.ipo_normalizer import normalize_ipo_detail, normalize_ipo_listing
from app.data.providers.base import RawIPOPage
from app.data.providers.upstox_provider import UpstoxProvider
from app.domain.ipo.allotment import estimate_retail_allotment_probability
from app.domain.ipo.demand_quality import DemandQualitySummary, build_demand_quality_summary
from app.domain.ipo.gmp_analysis import build_gmp_summary, build_listing_range_estimate
from app.domain.ipo.hidden_opportunity import HiddenOpportunityAssessment
from app.domain.ipo.models import (
    AllotmentProbabilityEstimate,
    GMPObservation,
    GMPSummary,
    IPOIdentity,
    ListingRangeEstimate,
    ShareholderQuotaInfo,
    SubscriptionCategory,
    SubscriptionSnapshot,
    UserEligibility,
)
from app.domain.ipo.quality_and_valuation import (
    IPOFundamentals,
    IPOValuationAssessment,
    IssueQualityAssessment,
    build_issue_quality_assessment,
    build_valuation_assessment,
)
from app.domain.ipo.query_parser import IPOQueryIntent, is_ipo_query, parse_ipo_query
from app.domain.ipo.shareholder_quota import (
    build_shareholder_quota_info,
    determine_user_eligibility,
    has_real_shareholder_reservation,
)
from app.utils.time import utc_now


@dataclass(frozen=True)
class IPOAnalysisConfig:
    """Every threshold with no single objectively-correct value, gathered
    in one place with documented defaults -- same pattern as
    `options_intelligence_pipeline.PipelineConfig`."""

    gmp_very_fresh_max_age: timedelta = timedelta(hours=2)
    gmp_recent_max_age: timedelta = timedelta(hours=12)
    gmp_aging_max_age: timedelta = timedelta(days=2)
    allotment_high_chance_min: Decimal = Decimal("0.5")
    allotment_moderate_chance_min: Decimal = Decimal("0.2")
    allotment_low_chance_min: Decimal = Decimal("0.05")
    # Phase 3/4/7 -- hype-vs-evidence / hidden-opportunity / listing-
    # indication thresholds. GMP % is relative to the issue price's upper
    # band; subscription is the aggregate multiple Upstox's real feed
    # reports.
    high_gmp_pct_threshold: Decimal = Decimal("20")
    low_gmp_pct_threshold: Decimal = Decimal("5")
    high_subscription_threshold: Decimal = Decimal("10")
    low_subscription_threshold: Decimal = Decimal("2")
    strong_gmp_pct_threshold: Decimal = Decimal("20")
    positive_gmp_pct_threshold: Decimal = Decimal("5")
    # Sprint 7B, Objective 8 -- demand-quality bands (times-subscribed
    # thresholds). No single objectively-correct value, same discipline
    # as every other threshold here.
    demand_quality_strong_min: Decimal = Decimal("10")
    demand_quality_moderate_min: Decimal = Decimal("2")
    demand_quality_weak_min: Decimal = Decimal("1")


@dataclass(frozen=True)
class IPOAnalysisResult:
    query: str
    intent: IPOQueryIntent
    identity: IPOIdentity | None
    gmp_summary: GMPSummary | None
    listing_range: ListingRangeEstimate | None
    subscription: SubscriptionSnapshot | None
    allotment_estimate: AllotmentProbabilityEstimate | None
    shareholder_quota: ShareholderQuotaInfo | None
    user_eligibility: UserEligibility
    data_available: bool
    detail: str
    analyzed_at: datetime
    # Product Effectiveness Patch, P1 #2 -- carries the SAME hidden-
    # opportunity classification `/api/ipo/compare` already computes via
    # `assess_hidden_opportunity()`, so a single-IPO analyze response can
    # expose it too. `analyze_ipo_query()`/`analyze_ipo_query_live()`
    # themselves still compute nothing new here -- this field defaults to
    # `None` and is only ever populated by a caller (the API handler)
    # that has already computed it, via `dataclasses.replace()`.
    hidden_opportunity: HiddenOpportunityAssessment | None = None
    # Sprint 7B, Objectives 9/10 -- issue quality is always computed
    # (pure derivation from `identity`, no new input required); valuation
    # stays `None` unless the caller supplies `IPOFundamentals` (no
    # authorized fundamentals provider exists -- see
    # quality_and_valuation.py's own docstring).
    issue_quality: IssueQualityAssessment | None = None
    valuation: IPOValuationAssessment | None = None
    # Sprint 7B, Objective 8 -- always computed (needs no new required
    # input; every field inside honestly defaults to UNKNOWN when its own
    # real data is absent).
    demand_quality: DemandQualitySummary | None = None


_DEFAULT_CONFIG = IPOAnalysisConfig()


def analyze_ipo_query(
    query: str,
    *,
    as_of: datetime,
    config: IPOAnalysisConfig = _DEFAULT_CONFIG,
    identity: IPOIdentity | None = None,
    gmp_observations: Sequence[GMPObservation] = (),
    previous_gmp_summary: GMPSummary | None = None,
    subscription: SubscriptionSnapshot | None = None,
    retail_valid_applications: int | None = None,
    shareholder_quota: ShareholderQuotaInfo | None = None,
    user_holds_parent_shares: bool | None = None,
    fundamentals: IPOFundamentals | None = None,
    subscription_history: Sequence[SubscriptionSnapshot] = (),
) -> IPOAnalysisResult:
    """The single entry point. Every optional parameter is data the
    CALLER supplied -- this function fetches nothing. If `query` is not
    even IPO-shaped, callers should route elsewhere (`is_ipo_query()`)
    before reaching here; this function does not re-check that itself so
    it stays usable for programmatic (non-text-query) callers too.
    """
    intent = parse_ipo_query(query)

    gmp_summary: GMPSummary | None = None
    listing_range: ListingRangeEstimate | None = None
    if gmp_observations:
        gmp_summary = build_gmp_summary(
            list(gmp_observations), as_of=as_of, very_fresh_max_age=config.gmp_very_fresh_max_age,
            recent_max_age=config.gmp_recent_max_age, aging_max_age=config.gmp_aging_max_age,
            previous_summary=previous_gmp_summary,
        )
        if identity is not None and identity.price_band_high is not None:
            listing_range = build_listing_range_estimate(identity.price_band_high, gmp_summary)

    allotment_estimate: AllotmentProbabilityEstimate | None = None
    retail_category = None
    if subscription is not None:
        retail_category = next((c for c in subscription.categories if c.category == SubscriptionCategory.RETAIL), None)
    if identity is not None and identity.lot_size is not None:
        retail_shares_offered = retail_category.shares_offered if retail_category is not None else None
        retail_subscription_times = retail_category.times_subscribed if retail_category is not None else None
        if retail_shares_offered is not None or retail_valid_applications is not None or retail_subscription_times is not None:
            allotment_estimate = estimate_retail_allotment_probability(
                retail_shares_offered=retail_shares_offered, lot_size=identity.lot_size,
                valid_retail_applications=retail_valid_applications, retail_subscription_times=retail_subscription_times,
                high_chance_min=config.allotment_high_chance_min, moderate_chance_min=config.allotment_moderate_chance_min,
                low_chance_min=config.allotment_low_chance_min,
            )

    resolved_quota = shareholder_quota if shareholder_quota is not None else build_shareholder_quota_info(confirmed_by_offer_document=False)
    user_eligibility = determine_user_eligibility(resolved_quota, user_holds_shares_as_of_record_date=user_holds_parent_shares)

    # Sprint 7B, Objectives 9/10 -- issue quality is a pure derivation of
    # `identity` (computed whenever an identity exists, no new input
    # needed); valuation requires caller-supplied fundamentals (no
    # authorized provider exists) and is `None` when `identity` itself is
    # `None` too, matching every other identity-derived field here.
    issue_quality = build_issue_quality_assessment(identity) if identity is not None else None
    valuation = build_valuation_assessment(identity, fundamentals) if identity is not None else None

    # Sprint 7B, Objective 8 -- TOTAL DEMAND / QIB / NII / RETAIL quality
    # + real day-over-day progression. `demand_quality.py`'s own
    # docstring: category bands never inferred from the aggregate figure.
    demand_quality = build_demand_quality_summary(
        snapshot=subscription, aggregate_subscription_times=identity.aggregate_subscription_times if identity is not None else None,
        strong_min=config.demand_quality_strong_min, moderate_min=config.demand_quality_moderate_min, weak_min=config.demand_quality_weak_min,
        snapshots_for_progression=list(subscription_history),
    )

    data_available = any([identity is not None, gmp_summary is not None, subscription is not None, allotment_estimate is not None])
    detail = (
        "Analysis assembled from supplied data."
        if data_available
        else "NOT AVAILABLE -- no IPO data was supplied for this query and this function performs no lookup "
             "itself. Real IPO identity/timeline/registrar data IS available from Upstox's authorized "
             "/v2/ipos -- see analyze_ipo_query_live() for the version that looks it up automatically "
             "(GMP/category-wise subscription/shareholder-quota-terms still require caller-supplied data; "
             "see docs/data-sources/IPO_DATA_SOURCE_DECISION.md)."
    )

    return IPOAnalysisResult(
        query=query, intent=intent, identity=identity, gmp_summary=gmp_summary, listing_range=listing_range,
        subscription=subscription, allotment_estimate=allotment_estimate, shareholder_quota=resolved_quota,
        user_eligibility=user_eligibility, data_available=data_available, detail=detail, analyzed_at=as_of,
        issue_quality=issue_quality, valuation=valuation, demand_quality=demand_quality,
    )


_ALL_STATUSES = ("open", "upcoming", "closed", "listed")


async def discover_ipos(*, provider: UpstoxProvider, status: str, page_number: int = 1) -> tuple[list[IPOIdentity], RawIPOPage]:
    """Part 3/5 -- real IPO discovery via Upstox's authorized `/v2/ipos`.
    Returns exactly one page; the real `RawIPOPage` is handed back
    unmodified so a caller can page through `total_pages` itself rather
    than this function silently deciding how much to fetch.
    """
    listings, page = await provider.get_ipos(status=status, page_number=page_number)
    identities = [normalize_ipo_listing(r, source="upstox", retrieved_at=utc_now()) for r in listings]
    return identities, page


async def fetch_ipo_detail(*, provider: UpstoxProvider, ipo_id: str) -> IPOIdentity:
    """Real per-IPO detail (timeline, registrar, investor categories, real
    listing price once listed) via Upstox's authorized `/v2/ipos/{id}`."""
    raw = await provider.get_ipo_detail(ipo_id=ipo_id)
    return normalize_ipo_detail(raw, source="upstox", retrieved_at=utc_now())


async def find_ipo_by_hint(*, provider: UpstoxProvider, hint: str) -> IPOIdentity | None:
    """Searches EVERY real page of every real status bucket (Part 14 fix
    -- this previously only checked page 1 of each status, a documented
    limitation from the prior milestone) for an IPO whose `symbol` or
    `name` contains `hint` (case-insensitive). Returns the first, real
    match's full detail, or `None` when genuinely nothing matches -- never
    a fabricated identity.

    Reuses `ipo_universe.fetch_ipo_universe()` for the actual pagination
    sequencing rather than duplicating it here (imported locally, not at
    module scope, to avoid a circular import -- `ipo_universe` itself
    imports `discover_ipos` from this module).
    """
    needle = hint.strip().lower()
    if not needle:
        return None

    from app.orchestration.ipo_universe import (
        fetch_ipo_universe,  # local import: breaks the ipo_universe <-> ipo_intelligence cycle
    )

    outcome = await fetch_ipo_universe(provider=provider, statuses=_ALL_STATUSES)
    for entry in outcome.entries:
        haystack = f"{entry.identity.symbol or ''} {entry.identity.company_name}".lower()
        if needle in haystack:
            if entry.identity.ipo_id is not None:
                return await fetch_ipo_detail(provider=provider, ipo_id=entry.identity.ipo_id)
            return entry.identity
    return None


async def analyze_ipo_query_live(
    query: str,
    *,
    as_of: datetime,
    provider: UpstoxProvider | None,
    config: IPOAnalysisConfig = _DEFAULT_CONFIG,
    identity: IPOIdentity | None = None,
    gmp_observations: Sequence[GMPObservation] = (),
    previous_gmp_summary: GMPSummary | None = None,
    subscription: SubscriptionSnapshot | None = None,
    retail_valid_applications: int | None = None,
    shareholder_quota: ShareholderQuotaInfo | None = None,
    user_holds_parent_shares: bool | None = None,
    fundamentals: IPOFundamentals | None = None,
    subscription_history: Sequence[SubscriptionSnapshot] = (),
) -> IPOAnalysisResult:
    """The live-lookup counterpart to `analyze_ipo_query()`. When
    `identity` was not explicitly supplied and the parsed query carries a
    `company_hint`, looks it up against the REAL Upstox IPO feed via
    `find_ipo_by_hint()` before falling back to `analyze_ipo_query()`'s
    existing (unmodified) assembly logic. A caller-supplied `identity`
    always wins -- this function never overwrites data the caller
    explicitly provided. If `provider` is `None`, or no match is found,
    behaves exactly like `analyze_ipo_query()` (still honest, never a
    fabricated match).

    Shareholder-quota detection: when the resolved identity carries real
    `investor_categories` from Upstox and the caller did not separately
    supply a `shareholder_quota`, this function derives one via
    `has_real_shareholder_reservation()` -- a real, sourced signal for
    the category's EXISTENCE, never its record date/reserved-shares/
    minimum-holding (those still require the caller to read the real
    `rhp_url`/`drhp_url` offer document).
    """
    resolved_identity = identity
    live_lookup_attempted = False
    live_lookup_hint: str | None = None
    if resolved_identity is None and provider is not None:
        intent = parse_ipo_query(query)
        if intent.company_hint is not None:
            live_lookup_attempted = True
            live_lookup_hint = intent.company_hint
            resolved_identity = await find_ipo_by_hint(provider=provider, hint=intent.company_hint)

    resolved_quota = shareholder_quota
    if resolved_quota is None and resolved_identity is not None and resolved_identity.investor_categories:
        resolved_quota = build_shareholder_quota_info(
            confirmed_by_offer_document=has_real_shareholder_reservation(resolved_identity.investor_categories),
            eligibility_company=resolved_identity.company_name, source=f"upstox /v2/ipos/{resolved_identity.ipo_id}",
        )

    result = analyze_ipo_query(
        query, as_of=as_of, config=config, identity=resolved_identity, gmp_observations=gmp_observations,
        previous_gmp_summary=previous_gmp_summary, subscription=subscription,
        retail_valid_applications=retail_valid_applications, shareholder_quota=resolved_quota,
        user_holds_parent_shares=user_holds_parent_shares, fundamentals=fundamentals, subscription_history=subscription_history,
    )

    # Correct `analyze_ipo_query()`'s generic "this function performs no
    # lookup itself" message when THIS function genuinely did attempt a
    # real lookup and found nothing -- the user deserves to know a real
    # search happened and came back empty, not that none was tried.
    if not result.data_available and live_lookup_attempted:
        result = dataclasses.replace(
            result,
            detail=f"NOT AVAILABLE -- searched Upstox's real, authorized IPO feed for {live_lookup_hint!r} "
                    "and found no match in any of upcoming/open/closed/listed. This is a genuine 'not found' "
                    "result, not an unattempted lookup. GMP/category-wise subscription/shareholder-quota-terms "
                    "still require caller-supplied data even for a real match "
                    "(see docs/data-sources/IPO_DATA_SOURCE_DECISION.md).",
        )
    return result


__all__ = [
    "IPOAnalysisConfig", "IPOAnalysisResult", "analyze_ipo_query", "analyze_ipo_query_live",
    "discover_ipos", "fetch_ipo_detail", "find_ipo_by_hint", "is_ipo_query",
]
