"""Option candidate generation — the ONLY module in this package allowed to
suggest a specific CE/PE strike, and only after a directional bias has
already been established elsewhere (the evidence matrix). Never runs on
its own; never outputs a bare "BUY CE"/"BUY PE" — every candidate carries
its full observable evidence, its liquidity assessment, an explicit
invalidation condition, and a list of risks, so a reader can judge it
rather than trust a label.

An option that is too far OTM, too illiquid, or has no usable quote is
excluded before ranking, not merely down-ranked — this is the enforcement
point for "a highly illiquid option must be prevented from becoming a top
candidate merely because its theoretical signal looks attractive."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import pairwise

from app.domain.market.models import OptionChainSnapshot, OptionQuote, OptionRight
from app.domain.options.chain_analysis import atm_strike, strike_window
from app.domain.options.evidence_matrix import EvidenceDirection
from app.domain.options.liquidity import LiquidityAssessment, LiquidityGrade, assess_liquidity

_LIQUIDITY_RANK = {
    LiquidityGrade.EXCELLENT: 3,
    LiquidityGrade.GOOD: 2,
    LiquidityGrade.POOR: 1,
    LiquidityGrade.UNTRADEABLE: 0,
}


@dataclass(frozen=True)
class OptionCandidate:
    instrument_key: str | None
    underlying: str
    strike: Decimal
    right: OptionRight
    ltp: Decimal | None
    bid: Decimal | None
    ask: Decimal | None
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
    is_atm: bool
    strikes_from_atm: int
    supporting_evidence: list[str] = field(default_factory=list)
    contradicting_evidence: list[str] = field(default_factory=list)
    invalidation_condition: str = ""
    risks: list[str] = field(default_factory=list)


def _leg_for(snapshot: OptionChainSnapshot, *, strike: Decimal, right: OptionRight) -> OptionQuote | None:
    for leg in snapshot.legs:
        if leg.strike == strike and leg.right == right:
            return leg
    return None


def generate_candidates(
    snapshot: OptionChainSnapshot,
    *,
    bias: EvidenceDirection,
    supporting_evidence: list[str],
    contradicting_evidence: list[str],
    strikes_each_side: int,
    as_of: datetime,
    max_quote_age: timedelta,
    min_liquidity_grade: LiquidityGrade,
    nearest_support_strike: Decimal | None,
    nearest_resistance_strike: Decimal | None,
) -> list[OptionCandidate]:
    """Only ever generates CE candidates for a `BULLISH` bias or PE
    candidates for a `BEARISH` bias — `NEUTRAL`/`UNKNOWN` bias produces no
    candidates at all (there is no directional thesis to build a
    strike-selection around; that is a `NO_TRADE` outcome, decided
    upstream by the decision engine, not here).

    `min_liquidity_grade` is required, undefaulted — an option below it is
    EXCLUDED, not merely down-ranked, so a theoretically attractive but
    illiquid strike can never surface as a top candidate.
    """
    if bias not in (EvidenceDirection.BULLISH, EvidenceDirection.BEARISH):
        return []

    right = OptionRight.CE if bias == EvidenceDirection.BULLISH else OptionRight.PE
    atm = atm_strike(snapshot)
    window = strike_window(snapshot, strikes_each_side=strikes_each_side)
    if atm is None or not window:
        return []

    min_rank = _LIQUIDITY_RANK[min_liquidity_grade]
    candidates: list[OptionCandidate] = []

    for strike in window:
        leg = _leg_for(snapshot, strike=strike, right=right)
        if leg is None:
            continue

        liquidity = assess_liquidity(leg, as_of=as_of, max_quote_age=max_quote_age)
        if _LIQUIDITY_RANK[liquidity.grade] < min_rank:
            continue

        spread_pct = liquidity.spread_fraction * Decimal(100) if liquidity.spread_fraction is not None else None
        strikes_from_atm = round((strike - atm) / _strike_step(window))
        is_atm = strike == atm

        candidate_supporting = list(supporting_evidence)
        candidate_contradicting = list(contradicting_evidence)
        if liquidity.grade == LiquidityGrade.EXCELLENT:
            candidate_supporting.append("liquidity grade EXCELLENT")
        elif liquidity.grade == LiquidityGrade.POOR:
            candidate_contradicting.append("liquidity grade only POOR (passed the minimum bar, but marginal)")

        invalidation = _invalidation_condition(
            bias=bias, nearest_support_strike=nearest_support_strike, nearest_resistance_strike=nearest_resistance_strike
        )

        risks = [
            "time decay (theta) erodes option value daily regardless of direction being right",
            "a correct directional call can still lose money if IV contracts after entry",
            "this assessment is a point-in-time snapshot, not a forecast of what happens next",
        ]
        if liquidity.grade in (LiquidityGrade.POOR, LiquidityGrade.UNTRADEABLE):
            risks.append("liquidity is marginal — realized entry/exit price may differ meaningfully from the quoted LTP")

        candidates.append(
            OptionCandidate(
                instrument_key=leg.security_id,
                underlying=snapshot.underlying,
                strike=strike,
                right=right,
                ltp=leg.last_price,
                bid=leg.bid_price,
                ask=leg.ask_price,
                spread_pct=spread_pct,
                open_interest=leg.open_interest,
                change_in_open_interest=leg.change_in_open_interest,
                volume=leg.volume,
                implied_volatility=leg.implied_volatility,
                delta=leg.delta,
                theta=leg.theta,
                gamma=leg.gamma,
                vega=leg.vega,
                liquidity=liquidity,
                is_atm=is_atm,
                strikes_from_atm=strikes_from_atm,
                supporting_evidence=candidate_supporting,
                contradicting_evidence=candidate_contradicting,
                invalidation_condition=invalidation,
                risks=risks,
            )
        )

    candidates.sort(key=lambda c: (-_LIQUIDITY_RANK[c.liquidity.grade], abs(c.strikes_from_atm)))
    return candidates


def _strike_step(window: list[Decimal]) -> Decimal:
    if len(window) < 2:
        return Decimal(1)
    diffs = [b - a for a, b in pairwise(window) if b - a > 0]
    return min(diffs) if diffs else Decimal(1)


def _invalidation_condition(
    *, bias: EvidenceDirection, nearest_support_strike: Decimal | None, nearest_resistance_strike: Decimal | None
) -> str:
    if bias == EvidenceDirection.BULLISH:
        if nearest_support_strike is not None:
            return f"thesis invalidated if the underlying closes decisively below the {nearest_support_strike} support candidate"
        return "thesis invalidated if the underlying's M15 trend/VWAP structure turns bearish or mixed"
    if nearest_resistance_strike is not None:
        return f"thesis invalidated if the underlying closes decisively above the {nearest_resistance_strike} resistance candidate"
    return "thesis invalidated if the underlying's M15 trend/VWAP structure turns bullish or mixed"
