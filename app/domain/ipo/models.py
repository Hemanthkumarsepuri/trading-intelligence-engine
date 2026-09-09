"""IPO Intelligence — core domain models.

=== READ THIS FIRST: NO LIVE IPO DATA SOURCE EXISTS ===
See `docs/data-sources/IPO_DATA_SOURCE_DECISION.md`. This project holds no
authorized, legitimate, non-scraping data source for GMP, subscription,
allotment results, or IPO calendars -- the already-integrated Upstox
Analytics Token was live-probed for an IPO endpoint and has none. Every
model in this module is therefore an INPUT-DRIVEN calculator: every
GMP/subscription/eligibility value is something the CALLER supplies
(a human reading a GMP tracker/NSE bidding page, or a future authorized
provider), never something this codebase fetches or invents itself.

=== TWO SEPARATE QUESTIONS, NEVER ONE SCORE ===
Per the product's own explicit requirement: LISTING OPPORTUNITY (will this
look attractive on listing day) and ALLOTMENT OPPORTUNITY (will I actually
receive shares) are structurally different questions with different
mechanics. Nothing in this module combines them into a single number.

=== THRESHOLD DISCIPLINE (same taxonomy as the options domain) ===
    FACT      — a real, sourced value the caller supplies (GMP figure,
                issue price, lot size, subscription count) with its own
                `source`/timestamp. Never defaulted, never guessed.
    METRIC    — a deterministic derivation with no free parameter (e.g.
                a list's min/max/median, or a percentage change).
    HEURISTIC — the retail-lottery allotment approximation (industry-
                standard, but an approximation of a real regulatory
                mechanism, not the mechanism itself) -- always labeled as
                such, with its formula and assumptions shown.
    THRESHOLD — every cutoff with no single objectively-correct value
                (freshness age bands, probability qualitative buckets) is
                a required, undefaulted parameter at the point of use,
                exactly like every other threshold in this codebase.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum


class IssueType(str, Enum):
    MAINBOARD = "MAINBOARD"
    SME = "SME"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class IPOIdentity:
    """FACT section -- every field is either directly supplied by the
    caller or `None` (never guessed). `company_name` is the only required
    field; everything else defaults to `None` (honestly absent) rather
    than a fabricated placeholder."""

    company_name: str
    issue_type: IssueType = IssueType.UNKNOWN
    issue_size_crore: Decimal | None = None
    fresh_issue_crore: Decimal | None = None
    offer_for_sale_crore: Decimal | None = None
    price_band_low: Decimal | None = None
    price_band_high: Decimal | None = None
    lot_size: int | None = None
    minimum_application_amount: Decimal | None = None
    face_value: Decimal | None = None
    opening_date: date | None = None
    closing_date: date | None = None
    allotment_date: date | None = None
    listing_date: date | None = None
    registrar: str | None = None
    lead_managers: list[str] = field(default_factory=list)
    promoter_names: list[str] = field(default_factory=list)
    objects_of_issue: list[str] = field(default_factory=list)
    offer_document_reference: str | None = None  # e.g. a name/citation the caller supplies, not a fetched URL
    source: str | None = None
    retrieved_at: datetime | None = None

    # Real, official-source fields (populated by
    # `app.orchestration.ipo_provider_adapter.normalize_ipo_identity()`
    # from Upstox's real, authorized `/v2/ipos`/`/v2/ipos/{id}` -- see
    # docs/data-sources/IPO_DATA_SOURCE_DECISION.md). All default to
    # `None`/empty for identities the caller builds by hand instead.
    ipo_id: str | None = None  # Upstox's own real id, e.g. "tempsens-instruments-india-limited-ipo"
    symbol: str | None = None
    isin: str | None = None
    status: str | None = None  # real values: "upcoming" | "open" | "closed" | "listed"
    listing_price: Decimal | None = None  # real, once status == "listed"; never fabricated pre-listing
    listing_exchange: str | None = None
    rhp_url: str | None = None
    drhp_url: str | None = None
    # Real investor-reservation CATEGORY CODES this IPO has (e.g. "IND",
    # "EMP", "HNI", "SHA") -- confirms a category's EXISTENCE, never its
    # reserved-share count, record date, or minimum holding (those remain
    # only in the actual offer document). See `app.domain.ipo.
    # shareholder_quota` for how "SHA" feeds shareholder-quota detection.
    investor_categories: list[str] = field(default_factory=list)
    # A SINGLE aggregate subscription multiple across ALL categories, as
    # Upstox's real feed reports it -- explicitly NOT the same as
    # category-wise `SubscriptionSnapshot`/`CategorySubscription` (which
    # requires per-category numbers this feed does not provide). Naming
    # kept distinct so the two are never confused.
    aggregate_subscription_times: Decimal | None = None


# ============================================================
# GMP intelligence (Part 4) — unofficial, unregulated, multi-source
# ============================================================


@dataclass(frozen=True)
class GMPObservation:
    """One GMP figure as reported by ONE named source at ONE point in
    time. GMP is inherently unofficial -- `source` is required, free-text
    (whatever the caller names it), and this model never implies
    officialdom. `observed_at` is when the SOURCE says the figure was
    current; `retrieved_at` is when THIS SYSTEM recorded it -- kept
    separate so staleness is never hidden by conflating the two."""

    source: str
    value: Decimal
    observed_at: datetime | None
    retrieved_at: datetime


class GMPFreshness(str, Enum):
    VERY_FRESH = "VERY_FRESH"
    RECENT = "RECENT"
    AGING = "AGING"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"  # no observation carries a usable timestamp


@dataclass(frozen=True)
class GMPSummary:
    """METRIC section -- deterministic aggregation over whatever
    `GMPObservation`s were supplied, computed by `build_gmp_summary()`.
    Never silently picks the highest figure; `sources_disagree` is an
    honest flag, not resolved away."""

    observations: list[GMPObservation]
    minimum: Decimal | None
    maximum: Decimal | None
    median: Decimal | None
    source_count: int
    latest_observed_at: datetime | None
    freshness: GMPFreshness
    sources_disagree: bool
    previous_value_for_change: Decimal | None = None
    change: Decimal | None = None
    change_pct: Decimal | None = None


@dataclass(frozen=True)
class ListingRangeEstimate:
    """Part 5 -- explicitly NOT a forecast. `label` is baked into the
    type so a caller cannot accidentally drop the disclaimer when
    rendering this."""

    issue_price: Decimal
    gmp_low: Decimal | None
    gmp_high: Decimal | None
    gmp_median: Decimal | None
    indicative_low: Decimal | None
    indicative_mid: Decimal | None
    indicative_high: Decimal | None
    indicative_premium_pct_low: Decimal | None
    indicative_premium_pct_high: Decimal | None
    label: str = "GMP-IMPLIED INDICATION -- NOT A FORECAST OR GUARANTEE"
    caveat: str = "Actual listing may differ materially. GMP is unofficial and unregulated."


# ============================================================
# Subscription intelligence (Part 6)
# ============================================================


class SubscriptionCategory(str, Enum):
    QIB = "QIB"
    NII_HNI = "NII_HNI"
    RETAIL = "RETAIL"
    EMPLOYEE = "EMPLOYEE"
    SHAREHOLDER = "SHAREHOLDER"
    OTHER = "OTHER"


@dataclass(frozen=True)
class CategorySubscription:
    category: SubscriptionCategory
    shares_offered: int | None
    shares_bid_for: int | None
    times_subscribed: Decimal | None  # FACT if caller supplies it directly, else computed as a METRIC (see helpers)
    as_of_day: int | None  # 1, 2, 3, ... or None if unknown
    is_final: bool = False


@dataclass(frozen=True)
class SubscriptionSnapshot:
    """One point-in-time record across all categories -- append a NEW
    `SubscriptionSnapshot` for each day rather than mutating a prior one,
    mirroring the audit journal's own append-only discipline (Part 22)."""

    captured_at: datetime
    categories: list[CategorySubscription]
    source: str | None
    is_final: bool = False


# ============================================================
# Allotment probability (Part 7/8)
# ============================================================


class AllotmentEstimability(str, Enum):
    ESTIMATED = "ESTIMATED"
    ESTIMATE_INCOMPLETE_INPUT = "ESTIMATE -- INPUT DATA INCOMPLETE"
    NOT_RELIABLY_ESTIMABLE = "NOT_RELIABLY_ESTIMABLE"
    UNDERSUBSCRIBED_EXPECTED = "UNDERSUBSCRIBED -- ALLOTMENT EXPECTED FOR VALID APPLICATIONS"


class AllotmentChanceBand(str, Enum):
    HIGH = "HIGH"
    MODERATE = "MODERATE"
    LOW = "LOW"
    VERY_LOW = "VERY_LOW"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class AllotmentProbabilityEstimate:
    """Part 7 -- HEURISTIC section. `methodology`/`assumptions` are
    always populated so the number is never presented as bare truth.
    `estimated_probability` is `None` whenever `estimability` is not
    `ESTIMATED`/`UNDERSUBSCRIBED_EXPECTED` -- this type makes it
    structurally impossible to show a percentage with no basis."""

    estimability: AllotmentEstimability
    methodology: str
    assumptions: list[str]
    retail_shares_offered: int | None
    lot_size: int | None
    retail_lots_available: int | None
    valid_retail_applications: int | None
    retail_subscription_times: Decimal | None
    estimated_probability: Decimal | None  # 0..1
    chance_band: AllotmentChanceBand
    detail: str


# ============================================================
# Shareholder quota (Part 9)
# ============================================================


class ShareholderQuotaStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    NOT_CONFIRMED = "NOT_CONFIRMED"


@dataclass(frozen=True)
class ShareholderQuotaInfo:
    """Part 9 -- `status` defaults structurally to `NOT_CONFIRMED`
    (see `build_shareholder_quota_info()`); it becomes `CONFIRMED` only
    when the caller explicitly supplies `confirmed_by_offer_document=True`
    -- this module never infers a quota from "the user owns the parent
    company" or any other proxy."""

    status: ShareholderQuotaStatus
    eligibility_company: str | None
    parent_company: str | None
    record_date: date | None
    minimum_holding_required: int | None
    reserved_shares: int | None
    maximum_application_shares: int | None
    both_retail_and_shareholder_allowed: bool | None
    source: str | None
    detail: str


class UserEligibility(str, Enum):
    YES = "YES"
    NO = "NO"
    UNKNOWN = "UNKNOWN"
