"""Hype-vs-evidence + hidden-opportunity tests. Core invariant: no label
is ever produced from a single isolated metric."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.ipo.gmp_analysis import build_gmp_summary
from app.domain.ipo.hidden_opportunity import (
    GMPLevel,
    GMPTrend,
    HiddenOpportunityLabel,
    SubscriptionLevel,
    assess_hidden_opportunity,
    classify_hype_vs_evidence,
)
from app.domain.ipo.models import (
    AllotmentChanceBand,
    AllotmentEstimability,
    AllotmentProbabilityEstimate,
    GMPObservation,
    GMPSummary,
    IPOIdentity,
)

_NOW = datetime(2026, 8, 30, 10, 0, tzinfo=UTC)
_THRESH = {"high_gmp_pct_threshold": Decimal("20"), "low_gmp_pct_threshold": Decimal("5"), "high_subscription_threshold": Decimal("10"), "low_subscription_threshold": Decimal("2")}


def _gmp(value: Decimal, *, change: Decimal | None = None) -> GMPSummary:
    obs = [GMPObservation(source="A", value=value, observed_at=_NOW, retrieved_at=_NOW)]
    previous = None
    if change is not None:
        prev_obs = [GMPObservation(source="A", value=value - change, observed_at=_NOW - timedelta(hours=6), retrieved_at=_NOW - timedelta(hours=6))]
        previous = build_gmp_summary(prev_obs, as_of=_NOW - timedelta(hours=6), very_fresh_max_age=timedelta(hours=2), recent_max_age=timedelta(hours=12), aging_max_age=timedelta(days=2))
    return build_gmp_summary(obs, as_of=_NOW, very_fresh_max_age=timedelta(hours=2), recent_max_age=timedelta(hours=12), aging_max_age=timedelta(days=2), previous_summary=previous)


def _identity(price_band_high: Decimal, subscription: Decimal | None) -> IPOIdentity:
    return IPOIdentity(company_name="Example Corp", price_band_high=price_band_high, aggregate_subscription_times=subscription)


def _allotment(chance_band: AllotmentChanceBand, probability: Decimal | None) -> AllotmentProbabilityEstimate:
    return AllotmentProbabilityEstimate(
        estimability=AllotmentEstimability.ESTIMATED, methodology="m", assumptions=[],
        retail_shares_offered=1000, lot_size=100, retail_lots_available=10, valid_retail_applications=100,
        retail_subscription_times=None, estimated_probability=probability, chance_band=chance_band, detail="d",
    )


# -- classify_hype_vs_evidence -----------------------------------------------


def test_hype_evidence_unavailable_when_no_data() -> None:
    result = classify_hype_vs_evidence(_identity(Decimal("200"), None), None, **_THRESH)
    assert result.gmp_level == GMPLevel.UNAVAILABLE
    assert result.subscription_level == SubscriptionLevel.UNAVAILABLE
    assert result.combined_label is None


def test_high_gmp_high_subscription() -> None:
    gmp = _gmp(Decimal("60"))  # 30% of 200
    result = classify_hype_vs_evidence(_identity(Decimal("200"), Decimal("50")), gmp, **_THRESH)
    assert result.gmp_level == GMPLevel.HIGH
    assert result.subscription_level == SubscriptionLevel.HIGH
    assert result.combined_label == "HIGH_GMP_HIGH_SUBSCRIPTION"


def test_high_gmp_low_subscription_is_mixed_evidence() -> None:
    gmp = _gmp(Decimal("60"))
    result = classify_hype_vs_evidence(_identity(Decimal("200"), Decimal("1")), gmp, **_THRESH)
    assert result.gmp_level == GMPLevel.HIGH
    assert result.subscription_level == SubscriptionLevel.LOW
    assert "mixed" in result.interpretation.lower()


def test_gmp_trend_rising() -> None:
    gmp = _gmp(Decimal("60"), change=Decimal("10"))
    result = classify_hype_vs_evidence(_identity(Decimal("200"), None), gmp, **_THRESH)
    assert result.gmp_trend == GMPTrend.RISING


def test_gmp_trend_falling() -> None:
    gmp = _gmp(Decimal("60"), change=Decimal("-10"))
    result = classify_hype_vs_evidence(_identity(Decimal("200"), None), gmp, **_THRESH)
    assert result.gmp_trend == GMPTrend.FALLING


# -- assess_hidden_opportunity ------------------------------------------------


def test_no_evidence_at_all_is_data_insufficient() -> None:
    result = assess_hidden_opportunity(_identity(Decimal("200"), None), gmp_summary=None, allotment_estimate=None, **_THRESH)
    assert result.label == HiddenOpportunityLabel.DATA_INSUFFICIENT
    assert result.evidence_considered == []


def test_single_positive_gmp_alone_never_produces_underfollowed_evidence() -> None:
    """The hard rule under direct test: moderate/high GMP with nothing
    else known must NEVER alone yield UNDERFOLLOWED_EVIDENCE or
    HYPE_HEAVY."""
    gmp = _gmp(Decimal("60"))
    result = assess_hidden_opportunity(_identity(Decimal("200"), None), gmp_summary=gmp, allotment_estimate=None, **_THRESH)
    assert result.label not in (HiddenOpportunityLabel.UNDERFOLLOWED_EVIDENCE, HiddenOpportunityLabel.HYPE_HEAVY)


def test_positive_gmp_plus_low_subscription_plus_good_allotment_is_underfollowed() -> None:
    """Three independent supporting signals -> UNDERFOLLOWED_EVIDENCE,
    with every reason traceable to real evidence."""
    gmp = _gmp(Decimal("60"))  # 30% -> HIGH
    identity = _identity(Decimal("200"), Decimal("1"))  # LOW subscription
    allotment = _allotment(AllotmentChanceBand.HIGH, Decimal("0.6"))
    result = assess_hidden_opportunity(identity, gmp_summary=gmp, allotment_estimate=allotment, **_THRESH)
    assert result.label == HiddenOpportunityLabel.UNDERFOLLOWED_EVIDENCE
    assert len(result.reasons) >= 2
    assert all(isinstance(r, str) and len(r) > 0 for r in result.reasons)


def test_high_gmp_high_subscription_and_very_low_allotment_is_hype_heavy() -> None:
    gmp = _gmp(Decimal("60"))
    identity = _identity(Decimal("200"), Decimal("50"))  # HIGH subscription
    allotment = _allotment(AllotmentChanceBand.VERY_LOW, Decimal("0.02"))
    result = assess_hidden_opportunity(identity, gmp_summary=gmp, allotment_estimate=allotment, **_THRESH)
    assert result.label == HiddenOpportunityLabel.HYPE_HEAVY


def test_conflicting_signals_are_labeled_conflicted_not_forced_to_one_side() -> None:
    """Construct a case with BOTH a hidden-style reason (rising GMP, low
    subscription) AND a hype-style reason (high GMP + high subscription
    would conflict) -- use allotment VERY_LOW with high GMP for hype,
    while subscription is LOW for a hidden signal, forcing genuine
    disagreement between the two reason sets."""
    gmp = _gmp(Decimal("60"), change=Decimal("10"))  # HIGH + RISING
    identity = _identity(Decimal("200"), Decimal("1"))  # LOW subscription -> hidden signal
    allotment = _allotment(AllotmentChanceBand.VERY_LOW, Decimal("0.02"))  # HIGH gmp + very low allotment -> hype signal
    result = assess_hidden_opportunity(identity, gmp_summary=gmp, allotment_estimate=allotment, **_THRESH)
    assert result.label == HiddenOpportunityLabel.CONFLICTED


def test_moderate_everything_is_no_clear_edge() -> None:
    gmp = _gmp(Decimal("20"))  # 10% -> MODERATE (between 5 and 20)
    identity = _identity(Decimal("200"), Decimal("5"))  # MODERATE subscription
    result = assess_hidden_opportunity(identity, gmp_summary=gmp, allotment_estimate=None, **_THRESH)
    assert result.label in (HiddenOpportunityLabel.NO_CLEAR_EDGE, HiddenOpportunityLabel.ATTENTION_WORTHY)


def test_missing_evidence_list_is_populated_honestly() -> None:
    result = assess_hidden_opportunity(_identity(Decimal("200"), None), gmp_summary=None, allotment_estimate=None, **_THRESH)
    assert "GMP" in result.missing_evidence
    assert "subscription" in result.missing_evidence
    assert "allotment estimate" in result.missing_evidence
