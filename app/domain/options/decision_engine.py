"""The final decision framework — deterministic, documented, and never
driven by a single signal. Separates six independent quality dimensions
(market bias, setup quality, option quality, liquidity quality, data
quality, risk quality) and only derives `TRADEABLE` when several of them
agree; any ambiguity anywhere defaults toward `WATCH` or `NO_TRADE`, never
toward a forced trade. `DATA_INSUFFICIENT` always takes precedence over
everything else — a system that can't see clearly must say so before it
says anything else.

The specific "how many STRONG/WEAK dimensions" rule below is this
project's own decision policy, not a mathematically or statistically
derived optimum — documented explicitly (see `decide()`) so it is visible
and auditable, exactly like every other threshold in this package.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from app.domain.market.price_consistency import DataConsistency
from app.domain.options.candidate_engine import OptionCandidate
from app.domain.options.evidence_matrix import (
    EvidenceDirection,
    EvidenceGroup,
    EvidenceMatrix,
    GroupVerdict,
    OverallConvergence,
)
from app.domain.options.iv_context import IvRankResult, IvRankStatus
from app.domain.options.liquidity import LiquidityGrade
from app.domain.options.market_regime import MarketRegime
from app.domain.options.models import ChainQualityIssue, ChainQualityIssueKind


class QualityLevel(str, Enum):
    STRONG = "STRONG"
    MODERATE = "MODERATE"
    WEAK = "WEAK"
    INSUFFICIENT = "INSUFFICIENT"


class FinalDecision(str, Enum):
    TRADEABLE = "TRADEABLE"
    WATCH = "WATCH"
    NO_TRADE = "NO_TRADE"
    DATA_INSUFFICIENT = "DATA_INSUFFICIENT"


@dataclass(frozen=True)
class QualityAssessment:
    market_bias: EvidenceDirection
    convergence: OverallConvergence
    setup_quality: QualityLevel
    option_quality: QualityLevel
    liquidity_quality: QualityLevel
    data_quality: QualityLevel
    risk_quality: QualityLevel
    supporting_evidence_count: int
    conflicting_evidence_count: int


@dataclass(frozen=True)
class DecisionResult:
    decision: FinalDecision
    assessment: QualityAssessment
    reasoning: str


def assess_setup_quality(*, regime: MarketRegime, underlying_group_verdict: GroupVerdict) -> QualityLevel:
    if regime == MarketRegime.DATA_INSUFFICIENT:
        return QualityLevel.INSUFFICIENT
    trending = regime in (MarketRegime.TRENDING_BULLISH, MarketRegime.TRENDING_BEARISH)
    directional = underlying_group_verdict in (GroupVerdict.BULLISH, GroupVerdict.BEARISH)
    if trending and directional:
        return QualityLevel.STRONG
    if directional:
        return QualityLevel.MODERATE
    return QualityLevel.WEAK


def assess_option_quality(candidates: list[OptionCandidate]) -> QualityLevel:
    if not candidates:
        return QualityLevel.INSUFFICIENT
    top = candidates[0]
    if top.liquidity.grade == LiquidityGrade.EXCELLENT and abs(top.strikes_from_atm) <= 1:
        return QualityLevel.STRONG
    if top.liquidity.grade in (LiquidityGrade.EXCELLENT, LiquidityGrade.GOOD):
        return QualityLevel.MODERATE
    return QualityLevel.WEAK


def assess_liquidity_quality(candidates: list[OptionCandidate]) -> QualityLevel:
    if not candidates:
        return QualityLevel.INSUFFICIENT
    mapping = {
        LiquidityGrade.EXCELLENT: QualityLevel.STRONG,
        LiquidityGrade.GOOD: QualityLevel.MODERATE,
        LiquidityGrade.POOR: QualityLevel.WEAK,
        LiquidityGrade.UNTRADEABLE: QualityLevel.INSUFFICIENT,
    }
    return mapping[candidates[0].liquidity.grade]


_BLOCKING_CHAIN_ISSUES = {
    ChainQualityIssueKind.DEAD_CHAIN,
    ChainQualityIssueKind.NO_UNDERLYING_PRICE,
    ChainQualityIssueKind.STALE_SNAPSHOT,
    # Sprint 6 -- a crossed market (bid > ask) is objectively impossible
    # on a real order book, not merely "wide" -- treated at least as
    # seriously as the other blocking issues above.
    ChainQualityIssueKind.CROSSED_MARKET,
}


def assess_data_quality(
    *, chain_issues: list[ChainQualityIssue], underlying_data_state_is_ok: bool,
    price_consistency: DataConsistency | None = None,
) -> QualityLevel:
    """Sprint 6 -- `price_consistency` is the real, already-computed
    verdict from `app.domain.market.price_consistency.classify_price_
    consistency()` (the traced KAYNES root cause: this pipeline
    legitimately fetches THREE separately-timestamped real underlying
    prices -- quote LTP, M15 candle close, option-chain reference -- and
    previously never cross-checked them). `None` means "not assessed this
    call" (e.g. an older/unrelated call site) and never adds a penalty --
    missing evidence must never fabricate a defect. A materially
    INCONSISTENT read is treated exactly as seriously as
    `underlying_data_state_is_ok=False` (forces `QualityLevel.INSUFFICIENT`,
    which `decide()` already turns into `DATA_INSUFFICIENT` before any
    other dimension is even considered) -- a ~7%+ real discrepancy between
    two "spot" sources makes any derived breakeven/support/resistance
    distance genuinely unreliable, not just noisy. `PARTIALLY_ALIGNED`
    downgrades to `WEAK` (a real but more moderate gap) rather than
    blocking outright.
    """
    if not underlying_data_state_is_ok:
        return QualityLevel.INSUFFICIENT
    if price_consistency == DataConsistency.INCONSISTENT:
        return QualityLevel.INSUFFICIENT
    if any(issue.kind in _BLOCKING_CHAIN_ISSUES for issue in chain_issues):
        return QualityLevel.WEAK
    if price_consistency == DataConsistency.PARTIALLY_ALIGNED:
        return QualityLevel.WEAK
    if chain_issues:
        return QualityLevel.MODERATE
    return QualityLevel.STRONG


def assess_risk_quality(*, liquidity_quality: QualityLevel, iv_rank: IvRankResult, data_quality: QualityLevel) -> QualityLevel:
    """"Risk quality" measures how well-contained/well-understood the risk
    is — STRONG means the risk picture is clear and manageable, not that
    there is no risk. A very high IV rank (options priced expensively
    relative to their own real recent history) is treated as an elevated
    risk of IV contraction working against a buyer, independent of
    direction being right.
    """
    if data_quality == QualityLevel.INSUFFICIENT or liquidity_quality == QualityLevel.INSUFFICIENT:
        return QualityLevel.INSUFFICIENT
    if iv_rank.status == IvRankStatus.OK and iv_rank.value is not None and iv_rank.value >= 80:
        return QualityLevel.WEAK
    if liquidity_quality == QualityLevel.WEAK or data_quality == QualityLevel.WEAK:
        return QualityLevel.WEAK
    if liquidity_quality == QualityLevel.STRONG and data_quality == QualityLevel.STRONG:
        return QualityLevel.STRONG
    return QualityLevel.MODERATE


_MARKET_BIAS_FROM_CONVERGENCE = {
    OverallConvergence.CONVERGENCE_BULLISH: EvidenceDirection.BULLISH,
    OverallConvergence.CONVERGENCE_BEARISH: EvidenceDirection.BEARISH,
    OverallConvergence.CONFLICT: EvidenceDirection.NEUTRAL,
    OverallConvergence.INSUFFICIENT_EVIDENCE: EvidenceDirection.UNKNOWN,
}


def market_bias_from_convergence(convergence: OverallConvergence) -> EvidenceDirection:
    """The market bias a caller should use to generate candidates BEFORE
    the full `QualityAssessment` (which itself needs the generated
    candidates) can be built — breaks that ordering dependency without
    duplicating the mapping table.
    """
    return _MARKET_BIAS_FROM_CONVERGENCE[convergence]


def build_quality_assessment(
    *,
    matrix: EvidenceMatrix,
    regime: MarketRegime,
    candidates: list[OptionCandidate],
    chain_issues: list[ChainQualityIssue],
    underlying_data_state_is_ok: bool,
    iv_rank: IvRankResult,
    price_consistency: DataConsistency | None = None,
) -> QualityAssessment:
    convergence = matrix.overall_convergence()
    market_bias = market_bias_from_convergence(convergence)

    underlying_group_verdict = matrix.group_verdict(EvidenceGroup.UNDERLYING_PRICE_STRUCTURE)

    setup_quality = assess_setup_quality(regime=regime, underlying_group_verdict=underlying_group_verdict)
    option_quality = assess_option_quality(candidates)
    liquidity_quality = assess_liquidity_quality(candidates)
    data_quality = assess_data_quality(
        chain_issues=chain_issues, underlying_data_state_is_ok=underlying_data_state_is_ok, price_consistency=price_consistency,
    )
    risk_quality = assess_risk_quality(liquidity_quality=liquidity_quality, iv_rank=iv_rank, data_quality=data_quality)

    # Analytical Consistency Audit -- real, demonstrated defect fixed here:
    # `conflicting_evidence_count` previously had NO guard for a
    # non-decisive `market_bias` (NEUTRAL from CONFLICT, or UNKNOWN from
    # INSUFFICIENT_EVIDENCE) -- its ternary only ever contemplated
    # BULLISH/BEARISH, so for any other bias it silently fell through to
    # counting BULLISH rows regardless of what was actually happening
    # (e.g. real GAIL case: bias=NEUTRAL from a genuine CONFLICT ->
    # supporting_evidence_count correctly forced to 0, but
    # conflicting_evidence_count arbitrarily reported the BULLISH row
    # count as if it meant something, producing a misleading "0
    # supporting / 3 conflicting" readout that hid the real 2 bearish
    # rows entirely and mislabeled the 3 bullish rows as "conflicting").
    # "Supporting"/"conflicting" are only meaningful RELATIVE TO A DECIDED
    # BIAS; with no decided bias, both are honestly 0 -- never an
    # arbitrarily-chosen side. This does not change `decide()` or
    # `evidence_quality` (which reads only `supporting_evidence_count`,
    # already correctly guarded) -- purely a reporting-field correction.
    has_decisive_bias = market_bias in (EvidenceDirection.BULLISH, EvidenceDirection.BEARISH)
    # Final Hardening Pass, Phase 20 -- `supporting_evidence_count`/
    # `conflicting_evidence_count` now count independent GROUPS
    # (`supporting_group_count()`), never raw rows: see that method's own
    # docstring for the real, demonstrated defect this closes (multiple
    # correlated rows from one EvidenceGroup previously could inflate
    # `evidence_quality` to STRONG on their own). This is the same "one
    # verdict per group" discipline `overall_convergence()` already
    # applies to the bullish/bearish DECISION -- now applied consistently
    # to the evidence-QUALITY magnitude shown to the user too.
    return QualityAssessment(
        market_bias=market_bias,
        convergence=convergence,
        setup_quality=setup_quality,
        option_quality=option_quality,
        liquidity_quality=liquidity_quality,
        data_quality=data_quality,
        risk_quality=risk_quality,
        supporting_evidence_count=matrix.supporting_group_count(market_bias) if has_decisive_bias else 0,
        conflicting_evidence_count=(
            matrix.supporting_group_count(EvidenceDirection.BEARISH if market_bias == EvidenceDirection.BULLISH else EvidenceDirection.BULLISH)
            if has_decisive_bias else 0
        ),
    )


def decide(assessment: QualityAssessment, *, derivatives_evidence_available: bool = True) -> DecisionResult:
    """Decision policy (this project's own documented rule, not a
    statistically derived optimum):

    1. `DATA_INSUFFICIENT` first, always — if data quality itself is
       insufficient, nothing downstream can be trusted enough to reason
       about, regardless of how the other five dimensions look.
    2. `NO_TRADE` if the evidence matrix itself has no convergence (either
       `CONFLICT` or `INSUFFICIENT_EVIDENCE`), or if there is no bias, or
       if no viable (sufficiently liquid) candidate exists at all --
       UNLESS `derivatives_evidence_available=False` (Phase 3 gap-closure,
       see below), in which case step 3 still runs.
    3. Otherwise, count how many of the four remaining quality dimensions
       (setup, option, liquidity, risk — market bias/convergence is
       already handled by step 2) are `STRONG` vs `WEAK`:
       - 3+ `STRONG` and 0 `WEAK` -> `TRADEABLE`
       - 2+ `WEAK` -> `NO_TRADE` (too much weakness to act on)
       - anything else -> `WATCH` (real evidence, but not decisive enough
         to call tradeable outright — this is the deliberately common,
         honest middle outcome, not a failure state).

    `derivatives_evidence_available` (Phase 3 gap-closure, default `True`
    -- every call site before this phase, and every LIVE call site since,
    passes nothing and gets IDENTICAL behavior to before this parameter
    existed). `False` means the PROVIDER itself structurally has no
    option-chain history for this instant (`HistoricalReplayProvider`
    replaying a genuinely historical date) -- `option_quality`/
    `liquidity_quality` are INSUFFICIENT not because a real chain was
    checked and found illiquid, but because no chain could be checked at
    all. Skipping step 2's contract-based `NO_TRADE` in that case lets
    step 3's dimension counting run on `setup_quality`/`risk_quality`
    alone; `option_quality`/`liquidity_quality`/`risk_quality` remain
    forced `INSUFFICIENT` (never `STRONG`), which makes `TRADEABLE`
    (requiring 3+ `STRONG`) mathematically impossible without a real
    contract -- the worst this can ever produce is `WATCH`, never a
    recommendation to trade an option this system never actually
    evaluated. This never changes what a LIVE, chain-capable provider's
    genuine `NO_TRADE`-for-illiquidity means.
    """
    if assessment.data_quality == QualityLevel.INSUFFICIENT:
        return DecisionResult(
            decision=FinalDecision.DATA_INSUFFICIENT, assessment=assessment,
            reasoning="data quality is insufficient to reason about this instrument right now",
        )

    if assessment.convergence in (OverallConvergence.CONFLICT, OverallConvergence.INSUFFICIENT_EVIDENCE):
        return DecisionResult(
            decision=FinalDecision.NO_TRADE, assessment=assessment,
            reasoning=f"evidence matrix convergence is {assessment.convergence.value} -- no coherent bias to act on",
        )

    if assessment.market_bias not in (EvidenceDirection.BULLISH, EvidenceDirection.BEARISH):
        return DecisionResult(
            decision=FinalDecision.NO_TRADE, assessment=assessment, reasoning="no directional market bias established"
        )

    if derivatives_evidence_available and (
        assessment.option_quality == QualityLevel.INSUFFICIENT or assessment.liquidity_quality == QualityLevel.INSUFFICIENT
    ):
        return DecisionResult(
            decision=FinalDecision.NO_TRADE, assessment=assessment,
            reasoning="no sufficiently liquid option candidate exists for this bias",
        )

    dimensions = [assessment.setup_quality, assessment.option_quality, assessment.liquidity_quality, assessment.risk_quality]
    strong_count = sum(1 for q in dimensions if q == QualityLevel.STRONG)
    weak_count = sum(1 for q in dimensions if q == QualityLevel.WEAK)

    if strong_count >= 3 and weak_count == 0:
        return DecisionResult(
            decision=FinalDecision.TRADEABLE, assessment=assessment,
            reasoning=f"{strong_count}/4 quality dimensions STRONG, none WEAK, evidence bias is {assessment.market_bias.value}",
        )
    if weak_count >= 2:
        return DecisionResult(
            decision=FinalDecision.NO_TRADE, assessment=assessment,
            reasoning=f"{weak_count}/4 quality dimensions WEAK -- too much weakness to act on despite a {assessment.market_bias.value} bias",
        )
    return DecisionResult(
        decision=FinalDecision.WATCH, assessment=assessment,
        reasoning=f"real {assessment.market_bias.value} evidence, but quality dimensions are not decisive enough for TRADEABLE",
    )


@dataclass(frozen=True)
class QualityTiers:
    """Phase 12's explicit 3-tier summary — a pure relabeling of fields
    `build_quality_assessment()`/`decide()` already computed, never a new
    calculation. Kept separate from `QualityAssessment`'s 6 finer-grained
    dimensions because a compact report needs exactly 3 lines, not 6 --
    this loses no information `render_text()`'s full detail doesn't
    already carry.
    """

    data_quality: QualityLevel
    evidence_quality: QualityLevel
    decision_quality: QualityLevel


def summarize_quality_tiers(result: DecisionResult) -> QualityTiers:
    assessment = result.assessment

    if assessment.convergence in (OverallConvergence.CONVERGENCE_BULLISH, OverallConvergence.CONVERGENCE_BEARISH):
        evidence_quality = QualityLevel.STRONG if assessment.supporting_evidence_count >= 3 else QualityLevel.MODERATE
    elif assessment.convergence == OverallConvergence.CONFLICT:
        evidence_quality = QualityLevel.WEAK
    else:
        evidence_quality = QualityLevel.INSUFFICIENT

    decision_quality = {
        FinalDecision.TRADEABLE: QualityLevel.STRONG,
        FinalDecision.WATCH: QualityLevel.MODERATE,
        FinalDecision.NO_TRADE: QualityLevel.WEAK,
        FinalDecision.DATA_INSUFFICIENT: QualityLevel.INSUFFICIENT,
    }[result.decision]

    return QualityTiers(data_quality=assessment.data_quality, evidence_quality=evidence_quality, decision_quality=decision_quality)


class ResearchState(str, Enum):
    """Research maturity of the situation -- separate from `FinalDecision`
    (kept for API compatibility, including the historical TRADEABLE label).

    This is not a buy/sell instruction. `CONFIRMED_SETUP` means independent
    current evidence groups converged and technical structure was current
    enough to evaluate; it does not mean "buy the candidate."
    """

    UNKNOWN = "UNKNOWN"
    WATCH = "WATCH"
    EARLY_SETUP = "EARLY_SETUP"
    CONFIRMATION_PENDING = "CONFIRMATION_PENDING"
    CONFIRMED_SETUP = "CONFIRMED_SETUP"
    CONFLICT = "CONFLICT"
    EXTENDED = "EXTENDED"
    NO_TRADE = "NO_TRADE"
    DATA_INSUFFICIENT = "DATA_INSUFFICIENT"


# Matches `daily_research.ScreeningConfig.extended_min_day_change_pct` so
# the main path and Stage-1 screen share one documented bar for "already
# a large day move". Not imported from daily_research (orchestration must
# not leak into domain). Exported (not `_`-prefixed) because
# `research_blocker.py`'s extension-distance computation and the
# Quick-View "distance to EXTENDED" indicator both need the exact same
# threshold `derive_research_state()` uses -- a second, silently-drifting
# copy of "6.0" would be a real defect waiting to happen.
EXTENDED_MIN_DAY_CHANGE_PCT = Decimal("6.0")
_EXTENDED_MIN_DAY_CHANGE_PCT = EXTENDED_MIN_DAY_CHANGE_PCT


def derive_research_state(
    *,
    decision: DecisionResult,
    candles_are_current: bool,
    day_change_pct: Decimal | None,
    chain_is_current: bool = True,
    quote_is_current: bool = True,
    development_pattern: str | None = None,
) -> ResearchState:
    """Maps already-computed decision + freshness facts + named development
    pattern into research maturity. Never uses a supporting-evidence count
    as a score. Cash context must not be passed in here.
    """
    if decision.decision == FinalDecision.DATA_INSUFFICIENT:
        return ResearchState.DATA_INSUFFICIENT
    if decision.assessment.convergence == OverallConvergence.CONFLICT:
        return ResearchState.CONFLICT
    if not candles_are_current or not chain_is_current or not quote_is_current:
        return ResearchState.CONFIRMATION_PENDING
    if decision.decision == FinalDecision.NO_TRADE:
        return ResearchState.NO_TRADE

    large_day_move = (
        day_change_pct is not None
        and abs(day_change_pct) >= _EXTENDED_MIN_DAY_CHANGE_PCT
    )
    if large_day_move and decision.decision in (FinalDecision.TRADEABLE, FinalDecision.WATCH):
        return ResearchState.EXTENDED
    if decision.decision == FinalDecision.TRADEABLE:
        return ResearchState.CONFIRMED_SETUP
    if decision.decision == FinalDecision.WATCH:
        if development_pattern and development_pattern != "NONE":
            return ResearchState.EARLY_SETUP
        return ResearchState.WATCH
    return ResearchState.UNKNOWN
