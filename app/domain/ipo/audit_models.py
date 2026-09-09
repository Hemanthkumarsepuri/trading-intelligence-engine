"""IPO audit journal — immutable, append-only records (Phase 8).

Mirrors `app.domain.audit.models`'s exact immutability discipline for the
options side: pydantic `BaseModel` with `frozen=True` at every nesting
level, no update/delete method anywhere in the persistence layer, and a
`corrects_audit_id` pointer for fixing a real error via a NEW record
rather than rewriting history. Pydantic here (not the plain
`@dataclass`es the rest of `app.domain.ipo` uses) for the same reason
`AnalysisSnapshot` is pydantic while `OptionsIntelligenceReport` is a
dataclass: JSONL persistence needs `model_dump_json()`/
`model_validate_json()`, and only the PERSISTED shape needs that -- the
working domain dataclasses (`IPOIdentity`, `GMPSummary`, ...) are
converted into these snapshot models at the orchestration boundary
(`app.orchestration.ipo_audit_journal`), never the reverse.

=== NO LOOK-AHEAD ===
`IPOListingOutcomeRecord` is a SEPARATE, later, append-only record --
never written into or merged back into the original `IPOAnalysisSnapshot`.
A snapshot taken before an IPO listed must remain byte-for-byte exactly
as it was, forever, even after the real listing price becomes known.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

from pydantic import BaseModel, Field


def new_ipo_audit_id() -> str:
    return uuid4().hex


class _FrozenModel(BaseModel):
    model_config = {"frozen": True}


class IPOIdentitySnapshot(_FrozenModel):
    company_name: str
    issue_type: str
    ipo_id: str | None = None
    symbol: str | None = None
    status: str | None = None
    price_band_low: Decimal | None = None
    price_band_high: Decimal | None = None
    issue_size_crore: Decimal | None = None
    lot_size: int | None = None
    listing_price: Decimal | None = None
    listing_exchange: str | None = None
    aggregate_subscription_times: Decimal | None = None
    investor_categories: list[str] = Field(default_factory=list)
    source: str | None = None
    retrieved_at: datetime | None = None


class GMPObservationSnapshot(_FrozenModel):
    source: str
    value: Decimal
    observed_at: datetime | None
    retrieved_at: datetime


class GMPSummarySnapshot(_FrozenModel):
    observations: list[GMPObservationSnapshot] = Field(default_factory=list)
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    median: Decimal | None = None
    source_count: int = 0
    freshness: str = "UNKNOWN"
    sources_disagree: bool = False


class SubscriptionCategorySnapshot(_FrozenModel):
    category: str
    shares_offered: int | None = None
    times_subscribed: Decimal | None = None
    as_of_day: int | None = None


class AllotmentEstimateSnapshot(_FrozenModel):
    estimability: str
    estimated_probability: Decimal | None = None
    chance_band: str
    methodology: str


class ShareholderQuotaSnapshot(_FrozenModel):
    status: str
    eligibility_company: str | None = None
    record_date: date | None = None
    source: str | None = None


class HiddenOpportunitySnapshot(_FrozenModel):
    label: str
    reasons: list[str] = Field(default_factory=list)
    evidence_considered: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)


class ListingRangeSnapshot(_FrozenModel):
    indicative_low: Decimal | None = None
    indicative_high: Decimal | None = None
    gmp_median: Decimal | None = None


class IPOAnalysisSnapshot(_FrozenModel):
    """The immutable record for one IPO analysis. `data_available`
    mirrors `IPOAnalysisResult.data_available` -- a snapshot with
    `identity is None` (nothing found) is still a real, honest, valid
    snapshot of "what this system knew at this moment", not an error."""

    audit_id: str
    analyzed_at: datetime
    query: str
    data_available: bool
    identity: IPOIdentitySnapshot | None = None
    gmp: GMPSummarySnapshot | None = None
    listing_range: ListingRangeSnapshot | None = None
    subscription_categories: list[SubscriptionCategorySnapshot] = Field(default_factory=list)
    allotment: AllotmentEstimateSnapshot | None = None
    shareholder_quota: ShareholderQuotaSnapshot | None = None
    hidden_opportunity: HiddenOpportunitySnapshot | None = None
    corrects_audit_id: str | None = None


class IPOListingOutcomeRecord(_FrozenModel):
    """A LATER, separate, append-only record of the real listing outcome
    for one prior `IPOAnalysisSnapshot` -- see module docstring's NO
    LOOK-AHEAD section. `audit_id` here refers to that snapshot's own
    `audit_id`, establishing the same "snapshot -> later observation"
    traceability the options audit journal already uses."""

    audit_id: str
    recorded_at: datetime
    listing_price: Decimal
    listing_gain_pct: Decimal | None = None
    gmp_accuracy_note: str | None = None
