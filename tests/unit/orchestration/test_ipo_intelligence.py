"""IPO Intelligence orchestration -- assembles a result purely from
caller-supplied data; asserts the honest NOT_AVAILABLE path when nothing
is supplied, and correct wiring when it is."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.domain.ipo.models import (
    CategorySubscription,
    GMPObservation,
    IPOIdentity,
    IssueType,
    SubscriptionCategory,
    SubscriptionSnapshot,
)
from app.domain.ipo.quality_and_valuation import IPOFundamentals
from app.domain.ipo.query_parser import IPOIntentKind
from app.orchestration.ipo_intelligence import analyze_ipo_query

_NOW = datetime(2026, 8, 30, 10, 0, tzinfo=UTC)


def test_no_supplied_data_is_honestly_not_available() -> None:
    result = analyze_ipo_query("JIO IPO", as_of=_NOW)
    assert result.data_available is False
    assert "NOT AVAILABLE" in result.detail
    assert result.gmp_summary is None
    assert result.identity is None
    assert result.intent.intent == IPOIntentKind.COMPANY_LOOKUP
    assert result.intent.company_hint == "JIO"


def test_identity_only_is_available() -> None:
    identity = IPOIdentity(company_name="Example Corp", issue_type=IssueType.MAINBOARD, lot_size=50)
    result = analyze_ipo_query("Example Corp IPO", as_of=_NOW, identity=identity)
    assert result.data_available is True
    assert result.identity is not None and result.identity.company_name == "Example Corp"


def test_issue_quality_is_always_computed_when_identity_present() -> None:
    """Sprint 7B, Objective 9 -- pure derivation from `identity`, needs no
    extra input from the caller."""
    identity = IPOIdentity(company_name="Example Corp", issue_size_crore=Decimal("1000"), fresh_issue_crore=Decimal("700"))
    result = analyze_ipo_query("Example Corp IPO", as_of=_NOW, identity=identity)
    assert result.issue_quality is not None
    assert result.issue_quality.fresh_issue_pct == Decimal("70")


def test_valuation_data_not_available_without_fundamentals() -> None:
    """Sprint 7B, Objective 10 -- honest DATA_NOT_AVAILABLE when the
    caller supplies no fundamentals (the common, default case -- no
    authorized fundamentals provider exists)."""
    identity = IPOIdentity(company_name="Example Corp")
    result = analyze_ipo_query("Example Corp IPO", as_of=_NOW, identity=identity)
    assert result.valuation is not None
    assert result.valuation.availability == "DATA_NOT_AVAILABLE"


def test_valuation_wired_when_caller_supplies_fundamentals() -> None:
    identity = IPOIdentity(company_name="Example Corp", price_band_high=Decimal("100"))
    fundamentals = IPOFundamentals(eps=Decimal("5"), source="caller")
    result = analyze_ipo_query("Example Corp IPO", as_of=_NOW, identity=identity, fundamentals=fundamentals)
    assert result.valuation is not None
    assert result.valuation.pe_ratio == Decimal("20")


def test_issue_quality_and_valuation_none_when_no_identity() -> None:
    result = analyze_ipo_query("Some IPO", as_of=_NOW)
    assert result.issue_quality is None
    assert result.valuation is None


def test_demand_quality_always_computed_and_never_infers_retail_from_aggregate() -> None:
    """Sprint 7B, Objective 8 -- wired end-to-end; retail quality stays
    UNKNOWN even though a real aggregate figure is present, because no
    category-wise subscription was supplied."""
    identity = IPOIdentity(company_name="Example Corp", aggregate_subscription_times=Decimal("15"))
    result = analyze_ipo_query("Example Corp IPO", as_of=_NOW, identity=identity)
    assert result.demand_quality is not None
    assert result.demand_quality.total_demand.times_subscribed == Decimal("15")
    assert result.demand_quality.retail_quality.times_subscribed is None


def test_gmp_observations_produce_a_real_summary_and_listing_range() -> None:
    identity = IPOIdentity(company_name="Example Corp", price_band_high=Decimal("200"), lot_size=50)
    obs = [
        GMPObservation(source="TrackerA", value=Decimal("70"), observed_at=_NOW, retrieved_at=_NOW),
        GMPObservation(source="TrackerB", value=Decimal("100"), observed_at=_NOW, retrieved_at=_NOW),
    ]
    result = analyze_ipo_query("Example Corp IPO GMP", as_of=_NOW, identity=identity, gmp_observations=obs)
    assert result.gmp_summary is not None
    assert result.gmp_summary.source_count == 2
    assert result.listing_range is not None
    assert result.listing_range.indicative_low == Decimal("270")
    assert result.listing_range.indicative_high == Decimal("300")


def test_subscription_and_application_count_produce_allotment_estimate() -> None:
    identity = IPOIdentity(company_name="Example Corp", lot_size=100)
    subscription = SubscriptionSnapshot(
        captured_at=_NOW, source="NSE bidding page (manually entered)",
        categories=[CategorySubscription(category=SubscriptionCategory.RETAIL, shares_offered=10000, shares_bid_for=None, times_subscribed=Decimal("25"), as_of_day=3, is_final=True)],
    )
    result = analyze_ipo_query(
        "Example Corp IPO allotment chance", as_of=_NOW, identity=identity, subscription=subscription,
        retail_valid_applications=1000,
    )
    assert result.allotment_estimate is not None
    assert result.allotment_estimate.estimated_probability == Decimal("0.1")  # 100 lots / 1000 applications


def test_shareholder_quota_defaults_to_not_confirmed_and_unknown_eligibility() -> None:
    result = analyze_ipo_query("Do Reliance shareholders get Jio IPO quota?", as_of=_NOW)
    assert result.shareholder_quota is not None
    from app.domain.ipo.models import ShareholderQuotaStatus, UserEligibility

    assert result.shareholder_quota.status == ShareholderQuotaStatus.NOT_CONFIRMED
    assert result.user_eligibility == UserEligibility.UNKNOWN


def test_non_ipo_query_still_returns_an_intent_but_is_never_used_for_routing_here() -> None:
    """This function itself doesn't gate on `is_ipo_query()` -- that's
    the caller's job (see module docstring); confirms it still degrades
    honestly rather than crashing on an options-shaped query."""
    result = analyze_ipo_query("KAYNES 4000 CE", as_of=_NOW)
    assert result.data_available is False
    # The bare parser still extracts "KAYNES" as a mention in isolation --
    # it has no way to know this was an options query; that distinction is
    # the CALLER's job via `is_ipo_query()`, which the options route always
    # checks first (see `app/api/main.py`).
    assert result.intent.intent == IPOIntentKind.COMPANY_LOOKUP


def test_analyzed_at_matches_as_of_never_wall_clock() -> None:
    fixed = datetime(2020, 1, 1, tzinfo=UTC)
    result = analyze_ipo_query("KAYNES IPO", as_of=fixed)
    assert result.analyzed_at == fixed
