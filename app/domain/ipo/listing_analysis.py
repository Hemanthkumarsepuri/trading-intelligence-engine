"""Listing-day intelligence (Phase 7) — two distinct, never-confused
questions:

1. `reconcile_listing_outcome()` — for an ALREADY-LISTED IPO, compares the
   real `listing_price` (ground truth, from `IPOIdentity.listing_price`,
   populated only once Upstox's real feed reports `status == "listed"`)
   against issue price and any GMP-implied range that was computed
   BEFORE listing. This is a REAL, retrospective comparison -- never a
   prediction, because the outcome already happened.

2. `assess_listing_indication()` — for a NOT-yet-listed IPO, a CONDITIONAL,
   clearly-labeled interpretation of currently available evidence. Never
   a forecast, never a probability of profit, never "will list higher."

Both reuse `hidden_opportunity.classify_hype_vs_evidence()` (not a second
GMP-interpretation implementation) for the GMP-percentage/trend reading
that both need.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from app.domain.ipo.hidden_opportunity import GMPTrend, HypeEvidenceClassification
from app.domain.ipo.models import IPOIdentity, ListingRangeEstimate


@dataclass(frozen=True)
class ListingOutcomeReconciliation:
    """Part 7 -- real, retrospective. `gmp_accuracy_note` is a
    DESCRIPTIVE comparison ("the real listing price fell within/outside
    the recorded GMP-implied range") -- never an accuracy percentage or
    a claim about GMP's general reliability from one observation.
    """

    issue_price: Decimal | None
    listing_price: Decimal
    listing_gain_pct: Decimal | None
    gmp_implied_range: ListingRangeEstimate | None
    gmp_accuracy_note: str | None


def reconcile_listing_outcome(identity: IPOIdentity, *, gmp_implied_range: ListingRangeEstimate | None) -> ListingOutcomeReconciliation | None:
    """Returns `None` when `identity.listing_price` is not yet known --
    never fabricates a pre-listing outcome. `gmp_implied_range` should be
    the range that was actually computed BEFORE this IPO listed (e.g.
    from a persisted audit snapshot -- see Phase 8); passing a range
    computed from post-listing GMP observations would be look-ahead
    contamination and is the CALLER's responsibility to avoid, not
    something this pure function can detect on its own.
    """
    if identity.listing_price is None:
        return None

    issue_price = identity.price_band_high
    gain_pct = None
    if issue_price is not None and issue_price != 0:
        gain_pct = (identity.listing_price - issue_price) / issue_price * Decimal(100)

    note = None
    if gmp_implied_range is not None and gmp_implied_range.indicative_low is not None and gmp_implied_range.indicative_high is not None:
        if gmp_implied_range.indicative_low <= identity.listing_price <= gmp_implied_range.indicative_high:
            note = "The real listing price fell WITHIN the GMP-implied range recorded before listing."
        elif identity.listing_price > gmp_implied_range.indicative_high:
            note = "The real listing price exceeded the GMP-implied range recorded before listing."
        else:
            note = "The real listing price fell BELOW the GMP-implied range recorded before listing."

    return ListingOutcomeReconciliation(
        issue_price=issue_price, listing_price=identity.listing_price, listing_gain_pct=gain_pct,
        gmp_implied_range=gmp_implied_range, gmp_accuracy_note=note,
    )


class ListingIndication(str, Enum):
    VERY_STRONG_INDICATION = "VERY_STRONG_INDICATION"
    POSITIVE_INDICATION = "POSITIVE_INDICATION"
    MIXED = "MIXED"
    WEAK_INDICATION = "WEAK_INDICATION"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True)
class ListingIndicationAssessment:
    indication: ListingIndication
    reasons: list[str] = field(default_factory=list)
    caveat: str = "This is a conditional interpretation of currently available evidence, not a prediction of the actual listing outcome."


def assess_listing_indication(
    hype_evidence: HypeEvidenceClassification | None,
    *,
    strong_gmp_pct_threshold: Decimal,
    positive_gmp_pct_threshold: Decimal,
) -> ListingIndicationAssessment:
    """Part 7, upcoming/open IPOs only. Every threshold is required and
    undefaulted. Combines GMP LEVEL and GMP TREND (never a single
    reading) -- a strong-but-falling GMP is explicitly noted as weakening
    the indication, never silently ignored.
    """
    if hype_evidence is None or hype_evidence.gmp_pct_of_issue_price is None:
        return ListingIndicationAssessment(indication=ListingIndication.INSUFFICIENT_DATA, reasons=["No GMP-derived evidence available (requires both a price band and a GMP observation)."])

    gmp_pct = hype_evidence.gmp_pct_of_issue_price
    reasons: list[str] = []

    if gmp_pct >= strong_gmp_pct_threshold:
        indication = ListingIndication.VERY_STRONG_INDICATION if hype_evidence.gmp_trend == GMPTrend.RISING else ListingIndication.POSITIVE_INDICATION
        reasons.append(f"GMP at {gmp_pct:.1f}% of issue price" + (" and trending upward" if hype_evidence.gmp_trend == GMPTrend.RISING else ""))
    elif gmp_pct >= positive_gmp_pct_threshold:
        indication = ListingIndication.MIXED
        reasons.append(f"GMP at {gmp_pct:.1f}% of issue price -- modestly positive but not strongly so")
    else:
        indication = ListingIndication.WEAK_INDICATION
        reasons.append(f"GMP at {gmp_pct:.1f}% of issue price" if gmp_pct > 0 else "GMP is at or below zero relative to issue price")

    if hype_evidence.gmp_trend == GMPTrend.FALLING:
        reasons.append("GMP is trending downward, which weakens this indication")
        if indication == ListingIndication.VERY_STRONG_INDICATION:
            indication = ListingIndication.POSITIVE_INDICATION
        elif indication == ListingIndication.POSITIVE_INDICATION:
            indication = ListingIndication.MIXED

    return ListingIndicationAssessment(indication=indication, reasons=reasons)
