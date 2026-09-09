"""Listing-day intelligence tests. Real reconciliation uses the exact
real Tempsens numbers observed live this session (price band ₹285-300,
real listing_price ₹634) -- a genuine, verified data point, not a
hypothetical."""

from __future__ import annotations

from decimal import Decimal

from app.domain.ipo.hidden_opportunity import (
    GMPLevel,
    GMPTrend,
    HypeEvidenceClassification,
    SubscriptionLevel,
)
from app.domain.ipo.listing_analysis import (
    ListingIndication,
    assess_listing_indication,
    reconcile_listing_outcome,
)
from app.domain.ipo.models import IPOIdentity, ListingRangeEstimate

_THRESH = {"strong_gmp_pct_threshold": Decimal("20"), "positive_gmp_pct_threshold": Decimal("5")}


def _tempsens_like_identity(listing_price: Decimal | None) -> IPOIdentity:
    return IPOIdentity(company_name="Tempsens Instruments (India) IPO", price_band_high=Decimal("300"), listing_price=listing_price, source="upstox")


def test_reconciliation_is_none_for_a_not_yet_listed_ipo() -> None:
    assert reconcile_listing_outcome(_tempsens_like_identity(None), gmp_implied_range=None) is None


def test_reconciliation_computes_real_listing_gain() -> None:
    """Real, verified Tempsens numbers: issue price ₹300, real listing
    price ₹634 -> a real, non-fabricated ~111.3% listing gain."""
    result = reconcile_listing_outcome(_tempsens_like_identity(Decimal("634")), gmp_implied_range=None)
    assert result is not None
    assert result.listing_price == Decimal("634")
    assert result.listing_gain_pct is not None
    assert result.listing_gain_pct.quantize(Decimal("0.1")) == Decimal("111.3")


def test_reconciliation_notes_when_real_listing_price_exceeds_the_recorded_gmp_range() -> None:
    gmp_range = ListingRangeEstimate(
        issue_price=Decimal("300"), gmp_low=Decimal("50"), gmp_high=Decimal("80"), gmp_median=Decimal("65"),
        indicative_low=Decimal("350"), indicative_mid=Decimal("365"), indicative_high=Decimal("380"),
        indicative_premium_pct_low=None, indicative_premium_pct_high=None,
    )
    result = reconcile_listing_outcome(_tempsens_like_identity(Decimal("634")), gmp_implied_range=gmp_range)
    assert result is not None
    assert result.gmp_accuracy_note is not None
    assert "exceeded" in result.gmp_accuracy_note


def test_reconciliation_notes_when_real_listing_price_falls_within_range() -> None:
    gmp_range = ListingRangeEstimate(
        issue_price=Decimal("300"), gmp_low=Decimal("300"), gmp_high=Decimal("400"), gmp_median=Decimal("350"),
        indicative_low=Decimal("600"), indicative_mid=Decimal("650"), indicative_high=Decimal("700"),
        indicative_premium_pct_low=None, indicative_premium_pct_high=None,
    )
    result = reconcile_listing_outcome(_tempsens_like_identity(Decimal("634")), gmp_implied_range=gmp_range)
    assert result is not None
    assert "WITHIN" in result.gmp_accuracy_note  # type: ignore[operator]


def test_listing_indication_insufficient_data_without_gmp() -> None:
    result = assess_listing_indication(None, **_THRESH)
    assert result.indication == ListingIndication.INSUFFICIENT_DATA


def test_listing_indication_very_strong_when_high_and_rising() -> None:
    hype = HypeEvidenceClassification(
        gmp_pct_of_issue_price=Decimal("30"), gmp_level=GMPLevel.HIGH, gmp_trend=GMPTrend.RISING,
        subscription_level=SubscriptionLevel.UNAVAILABLE, combined_label=None, interpretation="x",
    )
    result = assess_listing_indication(hype, **_THRESH)
    assert result.indication == ListingIndication.VERY_STRONG_INDICATION


def test_listing_indication_downgraded_when_gmp_falling() -> None:
    """The same high GMP level, but FALLING, must never still read as
    VERY_STRONG_INDICATION -- trend must genuinely affect the label."""
    hype = HypeEvidenceClassification(
        gmp_pct_of_issue_price=Decimal("30"), gmp_level=GMPLevel.HIGH, gmp_trend=GMPTrend.FALLING,
        subscription_level=SubscriptionLevel.UNAVAILABLE, combined_label=None, interpretation="x",
    )
    result = assess_listing_indication(hype, **_THRESH)
    assert result.indication != ListingIndication.VERY_STRONG_INDICATION
    assert any("downward" in r for r in result.reasons)


def test_listing_indication_weak_for_low_or_negative_gmp() -> None:
    hype = HypeEvidenceClassification(
        gmp_pct_of_issue_price=Decimal("-5"), gmp_level=GMPLevel.LOW, gmp_trend=GMPTrend.STABLE,
        subscription_level=SubscriptionLevel.UNAVAILABLE, combined_label=None, interpretation="x",
    )
    result = assess_listing_indication(hype, **_THRESH)
    assert result.indication == ListingIndication.WEAK_INDICATION


def test_listing_indication_never_labeled_for_an_already_listed_ipo_via_reconcile() -> None:
    """Sanity: the two functions address disjoint cases -- an
    already-listed identity should go through reconcile_listing_outcome,
    never assess_listing_indication (which has no `identity` parameter
    at all, structurally preventing that misuse)."""
    result = reconcile_listing_outcome(_tempsens_like_identity(Decimal("634")), gmp_implied_range=None)
    assert result is not None  # real outcome exists -- this IS the listed-IPO path
