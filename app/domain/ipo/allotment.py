"""Retail allotment-probability estimation (Parts 7/8) — this project's
single most safety-sensitive IPO calculation, because a user could act on
it with real money.

=== THE REAL MECHANISM THIS APPROXIMATES ===
Under SEBI's ICDR regulations, when the retail category is oversubscribed,
allotment is NOT strictly proportionate per rupee bid — SEBI requires every
successful retail applicant receive AT LEAST one minimum lot, decided by a
lottery/draw when the number of valid applications exceeds the number of
lots available at the minimum-lot level. The number of lots actually
available depends on exact bid-size distribution (how many applicants bid
for more than one lot), which this system does not have. `estimate_retail_
allotment_probability()` therefore computes a TRANSPARENT, LABELED
approximation of the single-lot-lottery draw:

    lots_available / valid_applications          (when applications are known)
    1 / times_subscribed                          (when only the subscription
                                                     multiple is known, under the
                                                     explicit assumption that most
                                                     applicants bid for exactly one
                                                     lot)

Both are the same "everyone applies for one lot; lots are drawn by lottery"
approximation, just derived from whichever real input the caller has. This
is the same heuristic GMP-tracker sites publish as "approximate chance" —
this module never claims it is the exact regulatory outcome, always shows
its formula and assumptions, and refuses to compute anything when neither
real input is supplied (`NOT_RELIABLY_ESTIMABLE`) rather than guessing an
application count.
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.ipo.models import (
    AllotmentChanceBand,
    AllotmentEstimability,
    AllotmentProbabilityEstimate,
)

_METHODOLOGY_APPLICATION_COUNT = (
    "SEBI's retail allotment mechanism guarantees at least one minimum lot per successful "
    "applicant when oversubscribed, decided by lottery among valid applications. Approximated "
    "here as (retail lots available) / (valid retail applications) -- the standard single-lot-"
    "lottery approximation, not an exact regulatory computation (exact bid-size distribution is "
    "not known)."
)
_METHODOLOGY_SUBSCRIPTION_ONLY = (
    "No valid-application count was supplied -- approximated instead as 1 / (retail subscription "
    "multiple), under the explicit ASSUMPTION that most retail applicants bid for exactly one "
    "minimum lot (the common case given the minimum-application-amount cutoff). This is a rougher "
    "approximation than using a real application count and can materially overstate or understate "
    "the true chance if that assumption does not hold for this issue."
)


def _chance_band(
    probability: Decimal | None,
    *,
    high_min: Decimal,
    moderate_min: Decimal,
    low_min: Decimal,
) -> AllotmentChanceBand:
    """THRESHOLD-classified: required, undefaulted cutoffs -- there is no
    single objectively-correct boundary between HIGH/MODERATE/LOW/VERY_LOW."""
    if probability is None:
        return AllotmentChanceBand.NOT_APPLICABLE
    if probability >= high_min:
        return AllotmentChanceBand.HIGH
    if probability >= moderate_min:
        return AllotmentChanceBand.MODERATE
    if probability >= low_min:
        return AllotmentChanceBand.LOW
    return AllotmentChanceBand.VERY_LOW


def estimate_retail_allotment_probability(
    *,
    retail_shares_offered: int | None,
    lot_size: int | None,
    valid_retail_applications: int | None,
    retail_subscription_times: Decimal | None,
    high_chance_min: Decimal,
    moderate_chance_min: Decimal,
    low_chance_min: Decimal,
) -> AllotmentProbabilityEstimate:
    """Part 7/8. Every threshold (`*_chance_min`) is required and
    undefaulted -- see module docstring's THRESHOLD discipline. Returns
    `NOT_RELIABLY_ESTIMABLE` (never a guessed number) whenever the
    minimum real inputs are missing.
    """
    assumptions: list[str] = []

    if retail_shares_offered is None or lot_size is None or lot_size <= 0:
        return AllotmentProbabilityEstimate(
            estimability=AllotmentEstimability.NOT_RELIABLY_ESTIMABLE,
            methodology="Retail shares offered and lot size are both required to estimate lots available.",
            assumptions=[], retail_shares_offered=retail_shares_offered, lot_size=lot_size,
            retail_lots_available=None, valid_retail_applications=valid_retail_applications,
            retail_subscription_times=retail_subscription_times, estimated_probability=None,
            chance_band=AllotmentChanceBand.NOT_APPLICABLE,
            detail="ALLOTMENT PROBABILITY: NOT RELIABLY ESTIMABLE FROM AVAILABLE DATA -- retail shares offered and/or lot size not supplied.",
        )

    lots_available = retail_shares_offered // lot_size

    # Path 1: a real valid-application count was supplied -- the most
    # reliable input this module can use.
    if valid_retail_applications is not None and valid_retail_applications > 0:
        if valid_retail_applications <= lots_available:
            return AllotmentProbabilityEstimate(
                estimability=AllotmentEstimability.UNDERSUBSCRIBED_EXPECTED,
                methodology="Valid retail applications did not exceed retail lots available -- no lottery is required.",
                assumptions=["Assumes each valid application requests at least the minimum lot and no applications are rejected for other reasons (e.g. failed UPI mandate)."],
                retail_shares_offered=retail_shares_offered, lot_size=lot_size, retail_lots_available=lots_available,
                valid_retail_applications=valid_retail_applications, retail_subscription_times=retail_subscription_times,
                estimated_probability=Decimal(1),
                chance_band=AllotmentChanceBand.HIGH,
                detail="Retail category was not oversubscribed at the supplied application count -- allotment is expected for a valid application, not merely probable.",
            )
        probability = min(Decimal(1), Decimal(lots_available) / Decimal(valid_retail_applications))
        assumptions.append("Single-lot-lottery approximation -- treats every valid applicant as competing equally for one lot each; does not model multi-lot bids explicitly.")
        return AllotmentProbabilityEstimate(
            estimability=AllotmentEstimability.ESTIMATED,
            methodology=_METHODOLOGY_APPLICATION_COUNT, assumptions=assumptions,
            retail_shares_offered=retail_shares_offered, lot_size=lot_size, retail_lots_available=lots_available,
            valid_retail_applications=valid_retail_applications, retail_subscription_times=retail_subscription_times,
            estimated_probability=probability,
            chance_band=_chance_band(probability, high_min=high_chance_min, moderate_min=moderate_chance_min, low_min=low_chance_min),
            detail=f"ESTIMATED RETAIL ALLOTMENT PROBABILITY ≈ {probability * 100:.1f}% ({lots_available} lots available / {valid_retail_applications} valid applications).",
        )

    # Path 2: only the subscription multiple is known -- a rougher,
    # explicitly-labeled fallback.
    if retail_subscription_times is not None and retail_subscription_times > 0:
        if retail_subscription_times <= 1:
            return AllotmentProbabilityEstimate(
                estimability=AllotmentEstimability.UNDERSUBSCRIBED_EXPECTED,
                methodology="Retail subscription multiple did not exceed 1x -- no lottery is required.",
                assumptions=["Assumes each valid application requests at least the minimum lot and no applications are rejected for other reasons (e.g. failed UPI mandate)."],
                retail_shares_offered=retail_shares_offered, lot_size=lot_size, retail_lots_available=lots_available,
                valid_retail_applications=None, retail_subscription_times=retail_subscription_times,
                estimated_probability=Decimal(1),
                chance_band=AllotmentChanceBand.HIGH,
                detail="Retail category was not oversubscribed -- allotment is expected for a valid application, not merely probable.",
            )
        probability = min(Decimal(1), Decimal(1) / retail_subscription_times)
        assumptions.append("ASSUMES most retail applicants bid for exactly one minimum lot -- this fails if a meaningful share of applicants bid for multiple lots, which would make the true chance lower than this estimate.")
        return AllotmentProbabilityEstimate(
            estimability=AllotmentEstimability.ESTIMATE_INCOMPLETE_INPUT,
            methodology=_METHODOLOGY_SUBSCRIPTION_ONLY, assumptions=assumptions,
            retail_shares_offered=retail_shares_offered, lot_size=lot_size, retail_lots_available=lots_available,
            valid_retail_applications=None, retail_subscription_times=retail_subscription_times,
            estimated_probability=probability,
            chance_band=_chance_band(probability, high_min=high_chance_min, moderate_min=moderate_chance_min, low_min=low_chance_min),
            detail=f"ESTIMATE -- INPUT DATA INCOMPLETE (no application count; approximated from {retail_subscription_times:.1f}x subscription): ≈ {probability * 100:.1f}%.",
        )

    return AllotmentProbabilityEstimate(
        estimability=AllotmentEstimability.NOT_RELIABLY_ESTIMABLE,
        methodology="Neither a valid-application count nor a subscription multiple was supplied.",
        assumptions=[], retail_shares_offered=retail_shares_offered, lot_size=lot_size,
        retail_lots_available=lots_available, valid_retail_applications=None, retail_subscription_times=None,
        estimated_probability=None, chance_band=AllotmentChanceBand.NOT_APPLICABLE,
        detail="ALLOTMENT PROBABILITY: NOT RELIABLY ESTIMABLE FROM AVAILABLE DATA.",
    )
