"""Requested-Contract Analysis Engine (Sprint 2) — evaluates ONE specific
strike/right a user asked about (e.g. "KAYNES 4000 CE"), and compares it
against nearby alternative strikes on the SAME right, entirely
INDEPENDENT of whichever directional bias the evidence matrix has
established elsewhere.

This is the direct enforcement point for the master directive's rule:
"If the user specifies KAYNES 4000 CE, the system MUST analyze that exact
contract independently of the overall evidence-matrix directional bias.
It must not say: 'overall bias is bearish, therefore 4000 CE is
ignored.'" `assess_contract()` never reads a bias; it is called once for
the requested strike and once per alternative, always for the SAME
right the user asked about (this module never substitutes CE for PE or
vice versa — a user who asked about a call gets calls compared against
calls, never puts presented as an "alternative").

Never claims a strike is "best" from a fabricated score. `compare_contract_
pair()` is deterministic Pareto-dominance over the two ordinal rankings
this codebase already computes (liquidity grade, decay-viability verdict):
a strike is only ever "preferable" if it is at least as good on EVERY
comparable dimension and strictly better on at least one. Any genuine
trade-off, tie, or missing dimension honestly returns
`NO_DEFENSIBLE_PREFERENCE`/`INSUFFICIENT_DATA` — never a synthesized
overall score.

Threshold classification (per this project's fact/metric/heuristic/
threshold discipline):
    FACT      — the leg's own reported LTP/bid/ask/OI/volume/Greeks.
    METRIC    — moneyness (`chain_analysis.classify_moneyness`), spread
                percent, distance-to-level percent — no free parameter.
    HEURISTIC — the liquidity rubric and decay-viability heuristics this
                module reuses from `liquidity.py`/`decay_viability.py`
                are unchanged here, just composed.
    THRESHOLD — none new: every threshold used here (liquidity grade
                floor, decay-viability ratio cutoffs, holding horizon) is
                the SAME required, undefaulted parameter already used
                elsewhere in this package — never a second, silently
                different cutoff for the same concept.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum

from app.domain.market.models import OptionChainSnapshot, OptionQuote, OptionRight
from app.domain.options.chain_analysis import atm_strike, classify_moneyness, strike_window_around
from app.domain.options.decay_viability import (
    DecayViabilityAssessment,
    DecayViabilityVerdict,
    assess_decay_viability,
)
from app.domain.options.liquidity import LiquidityAssessment, LiquidityGrade, assess_liquidity
from app.domain.options.models import Moneyness
from app.domain.options.support_resistance import Level

_LIQUIDITY_RANK = {
    LiquidityGrade.EXCELLENT: 3,
    LiquidityGrade.GOOD: 2,
    LiquidityGrade.POOR: 1,
    LiquidityGrade.UNTRADEABLE: 0,
}

_DECAY_VERDICT_RANK = {
    DecayViabilityVerdict.DECAY_FAVORABLE: 3,
    DecayViabilityVerdict.DECAY_ACCEPTABLE: 2,
    DecayViabilityVerdict.DECAY_HEADWIND: 1,
    DecayViabilityVerdict.DECAY_UNFAVORABLE: 0,
}


@dataclass(frozen=True)
class ContractAssessment:
    strike: Decimal
    right: OptionRight
    moneyness: Moneyness | None

    ltp: Decimal | None
    bid: Decimal | None
    ask: Decimal | None
    # Sprint 7A, Objective 9 -- real premium decomposition: `intrinsic_value`
    # is a pure METRIC (max(spot-strike,0) for CE / max(strike-spot,0) for
    # PE, zero free parameter); `time_value = ltp - intrinsic_value`. Both
    # `None` whenever spot or LTP is unavailable -- never estimated.
    intrinsic_value: Decimal | None
    time_value: Decimal | None
    spread_pct: Decimal | None
    open_interest: int | None
    change_in_open_interest: int | None
    volume: int | None
    implied_volatility: Decimal | None
    delta: Decimal | None
    theta: Decimal | None
    gamma: Decimal | None
    vega: Decimal | None

    liquidity: LiquidityAssessment
    decay_viability: DecayViabilityAssessment | None

    distance_to_support_pct: Decimal | None
    distance_to_resistance_pct: Decimal | None

    data_quality_notes: list[str] = field(default_factory=list)


def _leg_for(snapshot: OptionChainSnapshot, *, strike: Decimal, right: OptionRight) -> OptionQuote | None:
    for leg in snapshot.legs:
        if leg.strike == strike and leg.right == right:
            return leg
    return None


def _decompose_premium(
    *, spot: Decimal | None, strike: Decimal, right: OptionRight, ltp: Decimal | None,
) -> tuple[Decimal | None, Decimal | None, list[str]]:
    """Sprint 7A, Objective 9 -- real intrinsic/time-value split, and an
    honest flag for an objectively suspicious state: `time_value < 0`
    means the real LTP is below the real intrinsic value, which should
    not happen on a genuinely current, liquid quote (a real, if rare,
    stale/thin-quote artifact -- flagged, never silently hidden or
    corrected).
    """
    if spot is None or ltp is None:
        return None, None, []
    intrinsic = max(spot - strike, Decimal(0)) if right == OptionRight.CE else max(strike - spot, Decimal(0))
    time_value = ltp - intrinsic
    notes: list[str] = []
    if time_value < 0:
        notes.append(
            f"time value is negative ({time_value:.2f}) -- LTP ({ltp}) is below real intrinsic value ({intrinsic}) "
            "-- an objectively suspicious quote state (stale/thin quote), not corrected"
        )
    return intrinsic, time_value, notes


def _nearest_level_distance_pct(levels: list[Level], *, spot: Decimal | None) -> Decimal | None:
    if spot is None or spot == 0:
        return None
    candidates = [lv for lv in levels if lv.distance_from_spot_pct is not None]
    if not candidates:
        return None
    nearest = min(candidates, key=lambda lv: abs(lv.distance_from_spot_pct))  # type: ignore[arg-type]
    return nearest.distance_from_spot_pct


def assess_contract(
    snapshot: OptionChainSnapshot,
    *,
    strike: Decimal,
    right: OptionRight,
    expiry: date,
    as_of: datetime,
    max_quote_age: timedelta,
    support_levels: list[Level],
    resistance_levels: list[Level],
    decay_viability_holding_horizon_hours: Decimal,
    decay_viability_iv_scenario_points: Decimal,
    decay_viability_favorable_min_ratio: Decimal,
    decay_viability_acceptable_min_ratio: Decimal,
    decay_viability_headwind_min_ratio: Decimal,
) -> ContractAssessment | None:
    """Returns `None` if `strike`/`right` does not exist in this real
    chain snapshot at all — never fabricates a contract that isn't
    actually there.
    """
    leg = _leg_for(snapshot, strike=strike, right=right)
    if leg is None:
        return None

    spot = snapshot.underlying_last_price
    notes: list[str] = []

    moneyness = None
    atm = atm_strike(snapshot)
    if atm is not None and spot is not None:
        moneyness = classify_moneyness(strike=strike, right=right, atm=atm, spot=spot)
    else:
        notes.append("moneyness unavailable (no ATM/spot)")

    liquidity = assess_liquidity(leg, as_of=as_of, max_quote_age=max_quote_age)
    spread_pct = liquidity.spread_fraction * Decimal(100) if liquidity.spread_fraction is not None else None

    decay_viability: DecayViabilityAssessment | None = None
    if leg.last_price is not None:
        decay_viability = assess_decay_viability(
            strike=strike, right=right, spot=spot, expiry=expiry, as_of=as_of,
            implied_volatility=leg.implied_volatility, delta=leg.delta, gamma=leg.gamma, vega=leg.vega,
            theta=leg.theta, ltp=leg.last_price, bid=leg.bid_price, ask=leg.ask_price,
            holding_horizon_hours=decay_viability_holding_horizon_hours,
            iv_scenario_points=decay_viability_iv_scenario_points,
            favorable_min_ratio=decay_viability_favorable_min_ratio,
            acceptable_min_ratio=decay_viability_acceptable_min_ratio,
            headwind_min_ratio=decay_viability_headwind_min_ratio,
        )
    else:
        notes.append("decay viability unavailable (no usable LTP)")

    intrinsic_value, time_value, premium_notes = _decompose_premium(spot=spot, strike=strike, right=right, ltp=leg.last_price)
    notes.extend(premium_notes)

    return ContractAssessment(
        strike=strike, right=right, moneyness=moneyness, ltp=leg.last_price, bid=leg.bid_price, ask=leg.ask_price,
        intrinsic_value=intrinsic_value, time_value=time_value,
        spread_pct=spread_pct, open_interest=leg.open_interest, change_in_open_interest=leg.change_in_open_interest,
        volume=leg.volume, implied_volatility=leg.implied_volatility, delta=leg.delta, theta=leg.theta,
        gamma=leg.gamma, vega=leg.vega, liquidity=liquidity, decay_viability=decay_viability,
        distance_to_support_pct=_nearest_level_distance_pct(support_levels, spot=spot),
        distance_to_resistance_pct=_nearest_level_distance_pct(resistance_levels, spot=spot),
        data_quality_notes=notes,
    )


@dataclass(frozen=True)
class ContractComparison:
    requested_strike: Decimal
    requested_right: OptionRight
    requested: ContractAssessment | None
    alternatives: list[ContractAssessment] = field(default_factory=list)
    requested_not_in_chain_detail: str | None = None


def compare_contracts(
    snapshot: OptionChainSnapshot,
    *,
    requested_strike: Decimal,
    requested_right: OptionRight,
    strikes_each_side: int,
    expiry: date,
    as_of: datetime,
    max_quote_age: timedelta,
    support_levels: list[Level],
    resistance_levels: list[Level],
    decay_viability_holding_horizon_hours: Decimal,
    decay_viability_iv_scenario_points: Decimal,
    decay_viability_favorable_min_ratio: Decimal,
    decay_viability_acceptable_min_ratio: Decimal,
    decay_viability_headwind_min_ratio: Decimal,
) -> ContractComparison:
    """Assesses the requested contract and every nearby-strike alternative
    ON THE SAME RIGHT — regardless of any directional bias computed
    elsewhere (see module docstring). `strikes_each_side` reuses
    `PipelineConfig.strikes_each_side`, the same window size the bias-
    gated candidate engine already uses, rather than a second concept.
    """
    def _assess(strike: Decimal) -> ContractAssessment | None:
        return assess_contract(
            snapshot, strike=strike, right=requested_right, expiry=expiry, as_of=as_of, max_quote_age=max_quote_age,
            support_levels=support_levels, resistance_levels=resistance_levels,
            decay_viability_holding_horizon_hours=decay_viability_holding_horizon_hours,
            decay_viability_iv_scenario_points=decay_viability_iv_scenario_points,
            decay_viability_favorable_min_ratio=decay_viability_favorable_min_ratio,
            decay_viability_acceptable_min_ratio=decay_viability_acceptable_min_ratio,
            decay_viability_headwind_min_ratio=decay_viability_headwind_min_ratio,
        )

    requested = _assess(requested_strike)
    not_in_chain_detail = None if requested is not None else f"{requested_right.value} {requested_strike} was not found in the real chain for this expiry"

    window = strike_window_around(snapshot, center=requested_strike, strikes_each_side=strikes_each_side) if requested is not None else []
    alternatives: list[ContractAssessment] = []
    for strike in window:
        if strike == requested_strike:
            continue
        assessment = _assess(strike)
        if assessment is not None:
            alternatives.append(assessment)

    return ContractComparison(
        requested_strike=requested_strike, requested_right=requested_right, requested=requested,
        alternatives=alternatives, requested_not_in_chain_detail=not_in_chain_detail,
    )


class ContractPreference(str, Enum):
    REQUESTED_PREFERABLE = "REQUESTED_PREFERABLE"
    ALTERNATIVE_PREFERABLE = "ALTERNATIVE_PREFERABLE"
    NO_DEFENSIBLE_PREFERENCE = "NO_DEFENSIBLE_PREFERENCE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True)
class ContractPreferenceResult:
    alternative_strike: Decimal
    preference: ContractPreference
    detail: str


def compare_contract_pair(requested: ContractAssessment, alternative: ContractAssessment) -> ContractPreferenceResult:
    """Deterministic Pareto-dominance over whichever of {liquidity grade,
    decay-viability verdict} are available for BOTH sides — see module
    docstring. Never averages, weights, or scores; a genuine trade-off
    (one dimension favors each side) or a tie is honestly
    `NO_DEFENSIBLE_PREFERENCE`, never resolved by a tie-breaker.
    """
    dims: list[tuple[str, int, int]] = [
        ("liquidity", _LIQUIDITY_RANK[requested.liquidity.grade], _LIQUIDITY_RANK[alternative.liquidity.grade])
    ]
    if requested.decay_viability is not None and alternative.decay_viability is not None:
        r_verdict, a_verdict = requested.decay_viability.verdict, alternative.decay_viability.verdict
        if r_verdict in _DECAY_VERDICT_RANK and a_verdict in _DECAY_VERDICT_RANK:
            dims.append(("decay viability", _DECAY_VERDICT_RANK[r_verdict], _DECAY_VERDICT_RANK[a_verdict]))

    dims_desc = ", ".join(f"{name} requested={r} vs {alternative.strike}={a}" for name, r, a in dims)
    requested_ge_all = all(r >= a for _, r, a in dims)
    alt_ge_all = all(a >= r for _, r, a in dims)
    requested_gt_any = any(r > a for _, r, a in dims)
    alt_gt_any = any(a > r for _, r, a in dims)

    if requested_ge_all and requested_gt_any:
        return ContractPreferenceResult(alternative.strike, ContractPreference.REQUESTED_PREFERABLE, f"requested contract dominates strike {alternative.strike} on every comparable dimension ({dims_desc})")
    if alt_ge_all and alt_gt_any:
        return ContractPreferenceResult(alternative.strike, ContractPreference.ALTERNATIVE_PREFERABLE, f"strike {alternative.strike} dominates the requested contract on every comparable dimension ({dims_desc})")
    return ContractPreferenceResult(alternative.strike, ContractPreference.NO_DEFENSIBLE_PREFERENCE, f"dimensions disagree or are tied -- no deterministic dominance ({dims_desc})")


def summarize_contract_preference(comparison: ContractComparison) -> tuple[ContractPreference, str]:
    """The overall requested-vs-alternatives verdict. If ANY alternative
    dominates the requested contract, that alone is enough to say an
    alternative is preferable overall — a strictly better choice exists,
    regardless of how the requested contract compares to the others.
    """
    if comparison.requested is None:
        return ContractPreference.INSUFFICIENT_DATA, comparison.requested_not_in_chain_detail or "requested contract unavailable"
    if not comparison.alternatives:
        return ContractPreference.INSUFFICIENT_DATA, "no alternative strikes were available for comparison"

    pairwise = [compare_contract_pair(comparison.requested, alt) for alt in comparison.alternatives]
    dominating = [r for r in pairwise if r.preference == ContractPreference.ALTERNATIVE_PREFERABLE]
    if dominating:
        names = ", ".join(str(r.alternative_strike) for r in dominating)
        return ContractPreference.ALTERNATIVE_PREFERABLE, f"strike(s) {names} dominate the requested contract on every comparable dimension"
    if all(r.preference == ContractPreference.REQUESTED_PREFERABLE for r in pairwise):
        return ContractPreference.REQUESTED_PREFERABLE, "the requested contract dominates every compared alternative"
    return ContractPreference.NO_DEFENSIBLE_PREFERENCE, "no strike dominates every comparable dimension -- a genuine structural trade-off exists"


class ContractStructuralQuality(str, Enum):
    ACCEPTABLE = "ACCEPTABLE"
    HIGH_RISK_STRUCTURE = "HIGH_RISK_STRUCTURE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


def classify_contract_structural_quality(assessment: ContractAssessment) -> ContractStructuralQuality:
    """A purely STRUCTURAL flag on the contract itself -- illiquid AND
    decay-unfavorable at once -- independent of any directional view.
    Never a probability of loss, never blended into the evidence-matrix
    decision (`decision_engine.py` never reads this, exactly like
    `decay_viability.py`'s own scope note)."""
    if assessment.decay_viability is None or assessment.decay_viability.verdict == DecayViabilityVerdict.INSUFFICIENT_DATA:
        # A real `DecayViabilityAssessment` object with an INSUFFICIENT_DATA
        # verdict (e.g. missing bid/ask) is NOT the same as "checked and
        # fine" -- both cases mean this module cannot honestly classify the
        # contract's structural quality at all.
        return ContractStructuralQuality.INSUFFICIENT_DATA
    illiquid = assessment.liquidity.grade in (LiquidityGrade.POOR, LiquidityGrade.UNTRADEABLE)
    decay_bad = assessment.decay_viability.verdict in (DecayViabilityVerdict.DECAY_HEADWIND, DecayViabilityVerdict.DECAY_UNFAVORABLE)
    if illiquid and decay_bad:
        return ContractStructuralQuality.HIGH_RISK_STRUCTURE
    return ContractStructuralQuality.ACCEPTABLE
