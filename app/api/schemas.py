"""Presentation-layer request models for the dashboard API. The response
model (`AnalyzeResponse`) deliberately lives in
`app.orchestration.dashboard_service` instead of being re-declared here —
it is the orchestration layer's own contract, not a presentation-layer
concern; importing it directly avoids a second, possibly-drifting copy.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class AnalyzeRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    fii_cash_net: Decimal | None = None
    dii_cash_net: Decimal | None = None
    index_fo_net: Decimal | None = None
    fii_dii_as_of: date | None = None
    fii_dii_source: str | None = None


class WatchlistRequest(BaseModel):
    """Part 4H -- multiple independent queries compared side by side.
    Each is analyzed exactly like a standalone `/api/analyze` call (same
    function, same persistence); this is never a ranking or a
    recommendation across symbols, just parallel independent summaries."""

    queries: list[str] = Field(min_length=1, max_length=20)


class GMPObservationInput(BaseModel):
    """One GMP figure, as the caller (a human reading a GMP tracker page)
    supplies it. `source` is required -- GMP is inherently unofficial,
    never presented as if it came from nowhere."""

    source: str = Field(min_length=1, max_length=100)
    value: Decimal
    observed_at: datetime | None = None


class CategorySubscriptionInput(BaseModel):
    category: str  # matches app.domain.ipo.models.SubscriptionCategory values
    shares_offered: int | None = None
    shares_bid_for: int | None = None
    times_subscribed: Decimal | None = None
    as_of_day: int | None = None
    is_final: bool = False


class IPOAnalyzeRequest(BaseModel):
    """Part 18/19/28 -- every field is data the CALLER supplies (no field
    here is ever fetched by this system -- see
    docs/data-sources/IPO_DATA_SOURCE_DECISION.md). `query` alone (no
    other fields) is valid and returns an honest NOT_AVAILABLE result."""

    query: str = Field(min_length=1, max_length=200)

    company_name: str | None = None
    issue_type: str | None = None  # "MAINBOARD" | "SME"
    price_band_low: Decimal | None = None
    price_band_high: Decimal | None = None
    lot_size: int | None = None
    # Sprint 6 -- PRICE / ISSUE STRUCTURE. `IPOIdentity` already carried
    # these; this manual-entry request just couldn't set them before.
    issue_size_crore: Decimal | None = None
    fresh_issue_crore: Decimal | None = None
    offer_for_sale_crore: Decimal | None = None

    gmp_observations: list[GMPObservationInput] = Field(default_factory=list)

    subscription_categories: list[CategorySubscriptionInput] = Field(default_factory=list)
    subscription_captured_at: datetime | None = None
    subscription_source: str | None = None
    retail_valid_applications: int | None = None

    shareholder_quota_confirmed_by_offer_document: bool = False
    shareholder_eligibility_company: str | None = None
    shareholder_parent_company: str | None = None
    shareholder_reserved_shares: int | None = None
    user_holds_parent_shares: bool | None = None


class FollowupRequest(BaseModel):
    """Section 6 -- a short, deterministic follow-up ('what about PE?',
    'why?') resolved against the server's in-memory last-analysis context
    for that domain. See `app.orchestration.query_context`."""

    text: str = Field(min_length=1, max_length=200)


class IPOCompareRequest(BaseModel):
    """Part 2/3 -- compares multiple IPOs side by side. If `queries` is
    empty, scans the real Upstox `open` bucket instead (requires a
    configured provider) -- this is never a ranking; see
    `app.domain.ipo.comparison`'s own docstring."""

    queries: list[str] = Field(default_factory=list, max_length=20)


class ExplainRequest(BaseModel):
    """Structured TIRE facts only. Never used to decide BUY/SELL."""

    facts: dict[str, object]
    source_evidence_ids: list[str] = Field(default_factory=list)


class PersonalJournalEntryRequest(BaseModel):
    """Operator-authored research note. Never an order. Emotion is never
    inferred -- `user_reasoning` must be supplied by the operator."""

    symbol: str = Field(min_length=1, max_length=40)
    user_reasoning: str = Field(min_length=1, max_length=4000)
    market_regime: str | None = None
    sector: str | None = None
    research_state: str | None = None
    research_bucket: str | None = None
    timing_stage: str | None = None
    direction_hypothesis: str | None = None
    underlying_price: str | None = None
    option_contract: str | None = None
    strike: str | None = None
    expiry: str | None = None
    dte: int | None = None
    iv: str | None = None
    spread: str | None = None
    liquidity: str | None = None
    supporting_evidence: str | None = None
    missing_evidence: str | None = None
    confirmation: str | None = None
    invalidation: str | None = None
    audit_id: str | None = None


class PersonalJournalOutcomeRequest(BaseModel):
    """Later comparison of what the operator thought vs what happened.
    `mistake_class` is operator-assigned; TIRE never infers EMOTIONAL_DECISION."""

    what_i_thought: str = Field(min_length=1, max_length=4000)
    what_actually_happened: str = Field(min_length=1, max_length=4000)
    what_tire_observed: str | None = None
    mistake_class: str = "UNKNOWN"
    notes: str | None = None
