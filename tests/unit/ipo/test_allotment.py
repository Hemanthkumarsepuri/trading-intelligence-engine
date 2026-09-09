"""IPO Intelligence -- retail allotment probability estimation. The
single most safety-sensitive calculation in this feature -- every test
here is really testing "does this refuse to guess" as much as "is the
math right"."""

from __future__ import annotations

from decimal import Decimal

from app.domain.ipo.allotment import estimate_retail_allotment_probability
from app.domain.ipo.models import AllotmentChanceBand, AllotmentEstimability

_BANDS = {"high_chance_min": Decimal("0.5"), "moderate_chance_min": Decimal("0.2"), "low_chance_min": Decimal("0.05")}


def test_missing_lot_size_is_not_reliably_estimable() -> None:
    result = estimate_retail_allotment_probability(
        retail_shares_offered=10000, lot_size=None, valid_retail_applications=50000,
        retail_subscription_times=None, **_BANDS,
    )
    assert result.estimability == AllotmentEstimability.NOT_RELIABLY_ESTIMABLE
    assert result.estimated_probability is None
    assert "NOT RELIABLY ESTIMABLE" in result.detail


def test_missing_shares_offered_is_not_reliably_estimable() -> None:
    result = estimate_retail_allotment_probability(
        retail_shares_offered=None, lot_size=100, valid_retail_applications=50000,
        retail_subscription_times=None, **_BANDS,
    )
    assert result.estimability == AllotmentEstimability.NOT_RELIABLY_ESTIMABLE


def test_no_application_count_or_subscription_times_is_not_reliably_estimable() -> None:
    result = estimate_retail_allotment_probability(
        retail_shares_offered=10000, lot_size=100, valid_retail_applications=None,
        retail_subscription_times=None, **_BANDS,
    )
    assert result.estimability == AllotmentEstimability.NOT_RELIABLY_ESTIMABLE
    assert result.estimated_probability is None
    assert result.retail_lots_available == 100  # still computable, just nothing to divide it by


def test_undersubscribed_via_application_count_expects_allotment() -> None:
    result = estimate_retail_allotment_probability(
        retail_shares_offered=10000, lot_size=100, valid_retail_applications=50,  # 100 lots available, only 50 applicants
        retail_subscription_times=None, **_BANDS,
    )
    assert result.estimability == AllotmentEstimability.UNDERSUBSCRIBED_EXPECTED
    assert result.estimated_probability == Decimal(1)
    assert result.chance_band == AllotmentChanceBand.HIGH


def test_oversubscribed_via_real_application_count_computes_real_estimate() -> None:
    """100 lots available, 1000 valid applications -> 10% chance."""
    result = estimate_retail_allotment_probability(
        retail_shares_offered=10000, lot_size=100, valid_retail_applications=1000,
        retail_subscription_times=None, **_BANDS,
    )
    assert result.estimability == AllotmentEstimability.ESTIMATED
    assert result.estimated_probability == Decimal("0.1")
    assert result.chance_band == AllotmentChanceBand.LOW
    assert "SEBI" in result.methodology
    assert any("lottery" in a.lower() for a in result.assumptions)


def test_oversubscribed_via_application_count_never_exceeds_100_percent() -> None:
    result = estimate_retail_allotment_probability(
        retail_shares_offered=10000, lot_size=100, valid_retail_applications=1,  # more lots than applicants but still went through the ">lots" branch check
        retail_subscription_times=None, **_BANDS,
    )
    assert result.estimated_probability is not None
    assert result.estimated_probability <= Decimal(1)


def test_subscription_only_fallback_is_labeled_incomplete_input() -> None:
    """No application count -- only the subscription multiple (25x)."""
    result = estimate_retail_allotment_probability(
        retail_shares_offered=10000, lot_size=100, valid_retail_applications=None,
        retail_subscription_times=Decimal("25"), **_BANDS,
    )
    assert result.estimability == AllotmentEstimability.ESTIMATE_INCOMPLETE_INPUT
    assert result.estimated_probability == Decimal("0.04")  # 1/25
    assert result.chance_band == AllotmentChanceBand.VERY_LOW
    assert any("ASSUMES" in a for a in result.assumptions)


def test_subscription_times_under_1_is_undersubscribed() -> None:
    result = estimate_retail_allotment_probability(
        retail_shares_offered=10000, lot_size=100, valid_retail_applications=None,
        retail_subscription_times=Decimal("0.8"), **_BANDS,
    )
    assert result.estimability == AllotmentEstimability.UNDERSUBSCRIBED_EXPECTED
    assert result.estimated_probability == Decimal(1)


def test_application_count_is_preferred_over_subscription_times_when_both_given() -> None:
    """Real application count must win over the rougher subscription-multiple fallback."""
    result = estimate_retail_allotment_probability(
        retail_shares_offered=10000, lot_size=100, valid_retail_applications=1000,
        retail_subscription_times=Decimal("999"),  # would give a wildly different number if used
        **_BANDS,
    )
    assert result.estimability == AllotmentEstimability.ESTIMATED
    assert result.estimated_probability == Decimal("0.1")  # from the 1000-application path, not 1/999


def test_never_fabricates_an_application_count() -> None:
    """The function signature itself has no way to invent a count -- this
    test exists to make the invariant explicit and regression-proof: with
    every count-shaped input as None, the result must carry no probability."""
    result = estimate_retail_allotment_probability(
        retail_shares_offered=10000, lot_size=100, valid_retail_applications=None,
        retail_subscription_times=None, **_BANDS,
    )
    assert result.estimated_probability is None
    assert result.valid_retail_applications is None


def test_chance_bands_are_threshold_driven_not_hardcoded() -> None:
    """Passing different threshold values changes the band for the same
    probability -- proves the bands are genuinely parameterized, not a
    hidden constant."""
    lenient = estimate_retail_allotment_probability(
        retail_shares_offered=10000, lot_size=100, valid_retail_applications=1000,
        retail_subscription_times=None,
        high_chance_min=Decimal("0.05"), moderate_chance_min=Decimal("0.01"), low_chance_min=Decimal("0.001"),
    )
    strict = estimate_retail_allotment_probability(
        retail_shares_offered=10000, lot_size=100, valid_retail_applications=1000,
        retail_subscription_times=None,
        high_chance_min=Decimal("0.9"), moderate_chance_min=Decimal("0.8"), low_chance_min=Decimal("0.5"),
    )
    assert lenient.estimated_probability == strict.estimated_probability == Decimal("0.1")
    assert lenient.chance_band == AllotmentChanceBand.HIGH
    assert strict.chance_band == AllotmentChanceBand.VERY_LOW
