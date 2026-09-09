"""Hype-vs-evidence interpretation and hidden-opportunity detection
(Phases 3/4) — pure domain logic over already-computed objects.

=== HARD RULE: NEVER A SINGLE-METRIC LABEL ===
Every candidate reason this module can produce already combines TWO
independent data points (e.g. GMP level AND subscription level, or GMP
level AND allotment difficulty) -- a single isolated reading on ONE
dimension alone (e.g. "GMP is high" with nothing else known) can never by
itself produce a reason, let alone a label. `UNDERFOLLOWED_EVIDENCE`
additionally requires at least TWO such already-combined reasons to
agree with no conflicting hype signal; `HYPE_HEAVY` accepts one (the
"high GMP + high subscription" / "high GMP + very-low-allotment" cases
are themselves unambiguous two-signal combinations in practice). This is
enforced by counting reasons in code, not by a docstring promise.

=== VOCABULARY DISCIPLINE ===
This module never says BUY/SELL, never estimates profit probability,
and never claims a listing outcome as fact. `ATTENTION_WORTHY`/
`UNDERFOLLOWED_EVIDENCE` are attention signals ("this deserves a closer
look"), not investment signals ("this will perform well").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from app.domain.ipo.models import (
    AllotmentChanceBand,
    AllotmentProbabilityEstimate,
    GMPSummary,
    IPOIdentity,
)

# ============================================================
# Phase 4 -- hype vs. evidence classification
# ============================================================


class GMPLevel(str, Enum):
    HIGH = "HIGH"
    MODERATE = "MODERATE"
    LOW = "LOW"
    UNAVAILABLE = "UNAVAILABLE"


class GMPTrend(str, Enum):
    RISING = "RISING"
    FALLING = "FALLING"
    STABLE = "STABLE"
    UNAVAILABLE = "UNAVAILABLE"


class SubscriptionLevel(str, Enum):
    HIGH = "HIGH"
    MODERATE = "MODERATE"
    LOW = "LOW"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class HypeEvidenceClassification:
    """Part 4. `combined_label` is one of the exact vocabulary strings
    the product spec names (e.g. `"HIGH_GMP_STRONG_SUBSCRIPTION"`) when
    both GMP level and subscription level are known; `None` when either
    is `UNAVAILABLE` -- this module never guesses the missing half.
    """

    gmp_pct_of_issue_price: Decimal | None
    gmp_level: GMPLevel
    gmp_trend: GMPTrend
    subscription_level: SubscriptionLevel
    combined_label: str | None
    interpretation: str


def _gmp_level(gmp_pct: Decimal | None, *, high_gmp_pct_threshold: Decimal, low_gmp_pct_threshold: Decimal) -> GMPLevel:
    if gmp_pct is None:
        return GMPLevel.UNAVAILABLE
    if gmp_pct >= high_gmp_pct_threshold:
        return GMPLevel.HIGH
    if gmp_pct <= low_gmp_pct_threshold:
        return GMPLevel.LOW
    return GMPLevel.MODERATE


def _gmp_trend(gmp_summary: GMPSummary | None) -> GMPTrend:
    if gmp_summary is None or gmp_summary.change is None:
        return GMPTrend.UNAVAILABLE
    if gmp_summary.change > 0:
        return GMPTrend.RISING
    if gmp_summary.change < 0:
        return GMPTrend.FALLING
    return GMPTrend.STABLE


def _subscription_level(
    times_subscribed: Decimal | None, *, high_subscription_threshold: Decimal, low_subscription_threshold: Decimal
) -> SubscriptionLevel:
    if times_subscribed is None:
        return SubscriptionLevel.UNAVAILABLE
    if times_subscribed >= high_subscription_threshold:
        return SubscriptionLevel.HIGH
    if times_subscribed <= low_subscription_threshold:
        return SubscriptionLevel.LOW
    return SubscriptionLevel.MODERATE


def classify_hype_vs_evidence(
    identity: IPOIdentity,
    gmp_summary: GMPSummary | None,
    *,
    high_gmp_pct_threshold: Decimal,
    low_gmp_pct_threshold: Decimal,
    high_subscription_threshold: Decimal,
    low_subscription_threshold: Decimal,
) -> HypeEvidenceClassification:
    """Every threshold is required and undefaulted -- there is no single
    objectively-correct "what counts as high GMP" or "what counts as
    heavy subscription" (see `IPOAnalysisConfig` for the documented
    defaults every caller in this codebase actually uses)."""
    gmp_pct: Decimal | None = None
    if gmp_summary is not None and gmp_summary.median is not None and identity.price_band_high:
        gmp_pct = gmp_summary.median / identity.price_band_high * Decimal(100)

    gmp_level = _gmp_level(gmp_pct, high_gmp_pct_threshold=high_gmp_pct_threshold, low_gmp_pct_threshold=low_gmp_pct_threshold)
    gmp_trend = _gmp_trend(gmp_summary)
    subscription_level = _subscription_level(
        identity.aggregate_subscription_times, high_subscription_threshold=high_subscription_threshold, low_subscription_threshold=low_subscription_threshold
    )

    combined_label = None
    interpretation = "Available evidence is incomplete -- GMP and/or subscription data is unavailable."
    if gmp_level != GMPLevel.UNAVAILABLE and subscription_level != SubscriptionLevel.UNAVAILABLE:
        combined_label = f"{gmp_level.value}_GMP_{subscription_level.value}_SUBSCRIPTION"
        if gmp_level == GMPLevel.HIGH and subscription_level == SubscriptionLevel.HIGH:
            interpretation = "Available evidence currently supports strong headline enthusiasm AND strong actual demand -- a crowded, high-attention setup."
        elif gmp_level == GMPLevel.HIGH and subscription_level == SubscriptionLevel.LOW:
            interpretation = "Evidence is mixed: reported GMP is high but actual subscription demand has not (yet) followed -- worth watching for confirmation."
        elif gmp_level == GMPLevel.LOW and subscription_level == SubscriptionLevel.HIGH:
            interpretation = "Evidence is mixed: subscription demand is strong despite a modest reported GMP -- the market may be pricing this differently from GMP trackers."
        elif gmp_level == GMPLevel.LOW and subscription_level == SubscriptionLevel.LOW:
            interpretation = "Available evidence currently supports low headline enthusiasm and low actual demand."
        else:
            interpretation = f"Available evidence shows {gmp_level.value.lower()} GMP and {subscription_level.value.lower()} subscription -- no strong divergence either way."

    return HypeEvidenceClassification(
        gmp_pct_of_issue_price=gmp_pct, gmp_level=gmp_level, gmp_trend=gmp_trend, subscription_level=subscription_level,
        combined_label=combined_label, interpretation=interpretation,
    )


# ============================================================
# Phase 3 -- hidden-opportunity detection
# ============================================================


class HiddenOpportunityLabel(str, Enum):
    ATTENTION_WORTHY = "ATTENTION_WORTHY"
    UNDERFOLLOWED_EVIDENCE = "UNDERFOLLOWED_EVIDENCE"
    HYPE_HEAVY = "HYPE_HEAVY"
    DATA_INSUFFICIENT = "DATA_INSUFFICIENT"
    NO_CLEAR_EDGE = "NO_CLEAR_EDGE"
    CONFLICTED = "CONFLICTED"


@dataclass(frozen=True)
class HiddenOpportunityAssessment:
    label: HiddenOpportunityLabel
    reasons: list[str] = field(default_factory=list)
    evidence_considered: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    hype_evidence: HypeEvidenceClassification | None = None


def assess_hidden_opportunity(
    identity: IPOIdentity,
    *,
    gmp_summary: GMPSummary | None,
    allotment_estimate: AllotmentProbabilityEstimate | None,
    high_gmp_pct_threshold: Decimal,
    low_gmp_pct_threshold: Decimal,
    high_subscription_threshold: Decimal,
    low_subscription_threshold: Decimal,
) -> HiddenOpportunityAssessment:
    """Part 3. Requires >= 2 independent supporting signals for either
    `UNDERFOLLOWED_EVIDENCE` or `HYPE_HEAVY` (see module docstring's HARD
    RULE) -- a caller cannot get either label from one strong reading.
    """
    hype = classify_hype_vs_evidence(
        identity, gmp_summary, high_gmp_pct_threshold=high_gmp_pct_threshold, low_gmp_pct_threshold=low_gmp_pct_threshold,
        high_subscription_threshold=high_subscription_threshold, low_subscription_threshold=low_subscription_threshold,
    )

    evidence_considered: list[str] = []
    missing: list[str] = []
    if hype.gmp_level != GMPLevel.UNAVAILABLE:
        evidence_considered.append("GMP relative to issue price")
    else:
        missing.append("GMP")
    if hype.gmp_trend != GMPTrend.UNAVAILABLE:
        evidence_considered.append("GMP trend")
    else:
        missing.append("GMP trend")
    if hype.subscription_level != SubscriptionLevel.UNAVAILABLE:
        evidence_considered.append("aggregate subscription")
    else:
        missing.append("subscription")
    allotment_known = allotment_estimate is not None and allotment_estimate.estimated_probability is not None
    if allotment_known:
        evidence_considered.append("allotment difficulty")
    else:
        missing.append("allotment estimate")

    if not evidence_considered:
        return HiddenOpportunityAssessment(label=HiddenOpportunityLabel.DATA_INSUFFICIENT, evidence_considered=[], missing_evidence=missing, hype_evidence=hype)

    hidden_reasons: list[str] = []
    hype_reasons: list[str] = []

    if hype.gmp_level in (GMPLevel.HIGH, GMPLevel.MODERATE) and hype.subscription_level == SubscriptionLevel.LOW:
        hidden_reasons.append(f"positive GMP ({hype.gmp_pct_of_issue_price:.1f}% of issue price) combined with LOW aggregate subscription -- real interest without crowding" if hype.gmp_pct_of_issue_price is not None else "positive GMP combined with LOW aggregate subscription")
    if hype.gmp_trend == GMPTrend.RISING and hype.subscription_level in (SubscriptionLevel.LOW, SubscriptionLevel.MODERATE):
        hidden_reasons.append("GMP trending upward while subscription has not yet caught up")
    if allotment_known and allotment_estimate is not None and allotment_estimate.chance_band in (AllotmentChanceBand.HIGH, AllotmentChanceBand.MODERATE) and hype.gmp_level in (GMPLevel.HIGH, GMPLevel.MODERATE):
        hidden_reasons.append(f"positive GMP with a comparatively {allotment_estimate.chance_band.value.lower()} estimated allotment chance")

    if hype.gmp_level == GMPLevel.HIGH and hype.subscription_level == SubscriptionLevel.HIGH:
        hype_reasons.append("high GMP combined with high subscription -- the obvious, crowded, headline case")
    if allotment_known and allotment_estimate is not None and allotment_estimate.chance_band == AllotmentChanceBand.VERY_LOW and hype.gmp_level == GMPLevel.HIGH:
        hype_reasons.append("high GMP but a very low estimated allotment chance -- heavy demand competing for scarce lots")

    if len(hidden_reasons) >= 2 and not hype_reasons:
        label = HiddenOpportunityLabel.UNDERFOLLOWED_EVIDENCE
        reasons = hidden_reasons
    elif hidden_reasons and hype_reasons:
        label = HiddenOpportunityLabel.CONFLICTED
        reasons = hidden_reasons + hype_reasons
    elif len(hype_reasons) >= 1 and not hidden_reasons:
        label = HiddenOpportunityLabel.HYPE_HEAVY
        reasons = hype_reasons
    elif len(hidden_reasons) == 1:
        label = HiddenOpportunityLabel.ATTENTION_WORTHY
        reasons = hidden_reasons
    else:
        label = HiddenOpportunityLabel.NO_CLEAR_EDGE
        reasons = ["Available evidence does not show a clear divergence between headline enthusiasm and actual demand/allotment conditions."]

    return HiddenOpportunityAssessment(label=label, reasons=reasons, evidence_considered=evidence_considered, missing_evidence=missing, hype_evidence=hype)
