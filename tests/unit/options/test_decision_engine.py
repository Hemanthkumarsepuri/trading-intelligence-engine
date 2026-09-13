from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.domain.market.models import OptionRight
from app.domain.market.price_consistency import DataConsistency
from app.domain.options.candidate_engine import OptionCandidate
from app.domain.options.decision_engine import (
    DecisionResult,
    FinalDecision,
    QualityAssessment,
    QualityLevel,
    ResearchState,
    assess_data_quality,
    assess_liquidity_quality,
    assess_option_quality,
    assess_risk_quality,
    assess_setup_quality,
    build_quality_assessment,
    decide,
    derive_research_state,
    summarize_quality_tiers,
)
from app.domain.options.evidence_matrix import (
    EvidenceDirection,
    EvidenceGroup,
    EvidenceMatrix,
    EvidenceRow,
    GroupVerdict,
    OverallConvergence,
)
from app.domain.options.iv_context import IvRankResult, IvRankStatus
from app.domain.options.liquidity import LiquidityAssessment, LiquidityGrade
from app.domain.options.market_regime import MarketRegime
from app.domain.options.models import ChainQualityIssue, ChainQualityIssueKind

AS_OF = datetime(2026, 8, 29, 10, 0, tzinfo=UTC)
_UNAVAILABLE_RANK = IvRankResult(status=IvRankStatus.UNAVAILABLE, value=None, observations_used=0, detail="")


def _candidate(grade: LiquidityGrade, *, strikes_from_atm: int = 0) -> OptionCandidate:
    return OptionCandidate(
        instrument_key="X", underlying="NIFTY", strike=Decimal("24800"), right=OptionRight.CE, ltp=Decimal("100"),
        bid=Decimal("99"), ask=Decimal("101"), spread_pct=Decimal("2"), open_interest=50000, change_in_open_interest=1000,
        volume=5000, implied_volatility=Decimal("14"), delta=Decimal("0.5"), theta=None, gamma=None, vega=None,
        liquidity=LiquidityAssessment(grade=grade, spread_fraction=Decimal("0.02"), volume=5000, open_interest=50000, is_stale=False, reasons=[]),
        is_atm=strikes_from_atm == 0, strikes_from_atm=strikes_from_atm,
    )


# -- individual dimension assessors --------------------------------------


def test_setup_quality_strong_when_trending_and_directional() -> None:
    q = assess_setup_quality(regime=MarketRegime.TRENDING_BULLISH, underlying_group_verdict=GroupVerdict.BULLISH)
    assert q == QualityLevel.STRONG


def test_setup_quality_insufficient_when_regime_data_insufficient() -> None:
    q = assess_setup_quality(regime=MarketRegime.DATA_INSUFFICIENT, underlying_group_verdict=GroupVerdict.NON_DIRECTIONAL)
    assert q == QualityLevel.INSUFFICIENT


def test_setup_quality_weak_when_non_directional() -> None:
    q = assess_setup_quality(regime=MarketRegime.RANGE, underlying_group_verdict=GroupVerdict.NON_DIRECTIONAL)
    assert q == QualityLevel.WEAK


def test_option_quality_insufficient_with_no_candidates() -> None:
    assert assess_option_quality([]) == QualityLevel.INSUFFICIENT


def test_option_quality_strong_for_atm_excellent() -> None:
    assert assess_option_quality([_candidate(LiquidityGrade.EXCELLENT, strikes_from_atm=0)]) == QualityLevel.STRONG


def test_liquidity_quality_maps_grade_directly() -> None:
    assert assess_liquidity_quality([_candidate(LiquidityGrade.GOOD)]) == QualityLevel.MODERATE
    assert assess_liquidity_quality([]) == QualityLevel.INSUFFICIENT


def test_data_quality_strong_with_no_issues() -> None:
    assert assess_data_quality(chain_issues=[], underlying_data_state_is_ok=True) == QualityLevel.STRONG


def test_data_quality_insufficient_when_underlying_state_not_ok() -> None:
    assert assess_data_quality(chain_issues=[], underlying_data_state_is_ok=False) == QualityLevel.INSUFFICIENT


def test_data_quality_weak_with_blocking_issue() -> None:
    issues = [ChainQualityIssue(kind=ChainQualityIssueKind.DEAD_CHAIN, detail="x")]
    assert assess_data_quality(chain_issues=issues, underlying_data_state_is_ok=True) == QualityLevel.WEAK


def test_data_quality_weak_when_crossed_market_detected() -> None:
    """Sprint 6 -- a crossed market (bid > ask) is treated at least as
    seriously as DEAD_CHAIN/NO_UNDERLYING_PRICE/STALE_SNAPSHOT."""
    issues = [ChainQualityIssue(kind=ChainQualityIssueKind.CROSSED_MARKET, detail="x")]
    assert assess_data_quality(chain_issues=issues, underlying_data_state_is_ok=True) == QualityLevel.WEAK


def test_data_quality_moderate_with_non_blocking_issue() -> None:
    issues = [ChainQualityIssue(kind=ChainQualityIssueKind.WIDE_SPREAD, detail="x")]
    assert assess_data_quality(chain_issues=issues, underlying_data_state_is_ok=True) == QualityLevel.MODERATE


def test_data_quality_insufficient_when_price_consistency_is_inconsistent() -> None:
    """Sprint 6 -- the real KAYNES fix: a materially INCONSISTENT
    underlying-price read is treated as seriously as underlying_data_
    state_is_ok=False -- forces INSUFFICIENT even with no chain issues at
    all, so downstream breakeven/support/resistance calculations are
    withheld via the SAME existing DATA_INSUFFICIENT gate `decide()`
    already applies."""
    assert assess_data_quality(
        chain_issues=[], underlying_data_state_is_ok=True, price_consistency=DataConsistency.INCONSISTENT,
    ) == QualityLevel.INSUFFICIENT


def test_data_quality_weak_when_price_consistency_is_partially_aligned() -> None:
    assert assess_data_quality(
        chain_issues=[], underlying_data_state_is_ok=True, price_consistency=DataConsistency.PARTIALLY_ALIGNED,
    ) == QualityLevel.WEAK


def test_data_quality_unaffected_when_price_consistency_is_consistent_or_unknown() -> None:
    assert assess_data_quality(
        chain_issues=[], underlying_data_state_is_ok=True, price_consistency=DataConsistency.CONSISTENT,
    ) == QualityLevel.STRONG
    assert assess_data_quality(
        chain_issues=[], underlying_data_state_is_ok=True, price_consistency=DataConsistency.UNKNOWN,
    ) == QualityLevel.STRONG


def test_data_quality_unaffected_when_price_consistency_not_supplied() -> None:
    """Backward-compatible default -- an older/unrelated call site that
    never assessed price consistency gets no new penalty."""
    assert assess_data_quality(chain_issues=[], underlying_data_state_is_ok=True) == QualityLevel.STRONG


def test_risk_quality_weak_with_extreme_iv_rank() -> None:
    high_rank = IvRankResult(status=IvRankStatus.OK, value=Decimal(90), observations_used=10, detail="")
    q = assess_risk_quality(liquidity_quality=QualityLevel.STRONG, iv_rank=high_rank, data_quality=QualityLevel.STRONG)
    assert q == QualityLevel.WEAK


def test_risk_quality_strong_when_everything_clean() -> None:
    q = assess_risk_quality(liquidity_quality=QualityLevel.STRONG, iv_rank=_UNAVAILABLE_RANK, data_quality=QualityLevel.STRONG)
    assert q == QualityLevel.STRONG


# -- decide() end-to-end ----------------------------------------------------


def _matrix(directions: list[tuple[EvidenceGroup, EvidenceDirection]]) -> EvidenceMatrix:
    return EvidenceMatrix(rows=[EvidenceRow(f"row{i}", g, d, "") for i, (g, d) in enumerate(directions)])


def test_decide_data_insufficient_takes_precedence_over_everything() -> None:
    matrix = _matrix([(EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH)])
    assessment = build_quality_assessment(
        matrix=matrix, regime=MarketRegime.TRENDING_BULLISH, candidates=[_candidate(LiquidityGrade.EXCELLENT)],
        chain_issues=[], underlying_data_state_is_ok=False, iv_rank=_UNAVAILABLE_RANK,
    )
    result = decide(assessment)
    assert result.decision == FinalDecision.DATA_INSUFFICIENT


def test_decide_no_trade_when_evidence_conflicts() -> None:
    matrix = _matrix(
        [
            (EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH),
            (EvidenceGroup.OPTIONS_OI, EvidenceDirection.BEARISH),
        ]
    )
    assessment = build_quality_assessment(
        matrix=matrix, regime=MarketRegime.RANGE, candidates=[_candidate(LiquidityGrade.GOOD)], chain_issues=[],
        underlying_data_state_is_ok=True, iv_rank=_UNAVAILABLE_RANK,
    )
    result = decide(assessment)
    assert result.decision == FinalDecision.NO_TRADE


def test_decide_no_trade_when_no_candidates_exist() -> None:
    matrix = _matrix([(EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH)])
    assessment = build_quality_assessment(
        matrix=matrix, regime=MarketRegime.TRENDING_BULLISH, candidates=[], chain_issues=[],
        underlying_data_state_is_ok=True, iv_rank=_UNAVAILABLE_RANK,
    )
    result = decide(assessment)
    assert result.decision == FinalDecision.NO_TRADE


# -- Phase 3 gap-closure: derivatives_evidence_available -----------------


def test_decide_no_candidates_defaults_to_the_unchanged_live_behavior() -> None:
    """The new parameter's default (`True`) must reproduce EXACTLY the
    pre-Phase-3 behavior for every existing call site -- this is the same
    fixture as `test_decide_no_trade_when_no_candidates_exist()`, called
    without the new kwarg at all."""
    matrix = _matrix([(EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH)])
    assessment = build_quality_assessment(
        matrix=matrix, regime=MarketRegime.TRENDING_BULLISH, candidates=[], chain_issues=[],
        underlying_data_state_is_ok=True, iv_rank=_UNAVAILABLE_RANK,
    )
    result = decide(assessment)
    assert result.decision == FinalDecision.NO_TRADE


def test_decide_no_candidates_with_derivatives_available_true_is_still_no_trade() -> None:
    """A LIVE provider (derivatives_evidence_available=True, explicit)
    with zero real candidates is still NO_TRADE -- this parameter never
    changes a genuine live "no viable contract" outcome."""
    matrix = _matrix([(EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH)])
    assessment = build_quality_assessment(
        matrix=matrix, regime=MarketRegime.TRENDING_BULLISH, candidates=[], chain_issues=[],
        underlying_data_state_is_ok=True, iv_rank=_UNAVAILABLE_RANK,
    )
    result = decide(assessment, derivatives_evidence_available=True)
    assert result.decision == FinalDecision.NO_TRADE


def test_decide_watch_when_derivatives_unavailable_but_setup_is_strong() -> None:
    """A replay provider (derivatives_evidence_available=False) with zero
    candidates but a real, strong price-only setup reaches WATCH -- never
    silently NO_TRADE'd purely because no chain could be checked."""
    matrix = _matrix(
        [
            (EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH),
            (EvidenceGroup.GLOBAL, EvidenceDirection.BULLISH),
        ]
    )
    assessment = build_quality_assessment(
        matrix=matrix, regime=MarketRegime.TRENDING_BULLISH, candidates=[], chain_issues=[],
        underlying_data_state_is_ok=True, iv_rank=_UNAVAILABLE_RANK,
    )
    result = decide(assessment, derivatives_evidence_available=False)
    assert result.decision == FinalDecision.WATCH


def test_decide_never_tradeable_when_derivatives_unavailable() -> None:
    """Mathematically impossible by construction (see `decide()`'s own
    docstring): option_quality/liquidity_quality/risk_quality are all
    forced INSUFFICIENT with zero candidates, so at most 1 of the 4
    dimensions (setup_quality) can ever be STRONG -- TRADEABLE needs 3+.
    Verified here with an otherwise maximally favorable setup."""
    matrix = _matrix(
        [
            (EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH),
            (EvidenceGroup.GLOBAL, EvidenceDirection.BULLISH),
            (EvidenceGroup.NEWS_EVENT, EvidenceDirection.BULLISH),
            (EvidenceGroup.RELATIVE_STRENGTH, EvidenceDirection.BULLISH),
        ]
    )
    assessment = build_quality_assessment(
        matrix=matrix, regime=MarketRegime.TRENDING_BULLISH, candidates=[], chain_issues=[],
        underlying_data_state_is_ok=True, iv_rank=_UNAVAILABLE_RANK,
    )
    result = decide(assessment, derivatives_evidence_available=False)
    assert result.decision != FinalDecision.TRADEABLE


def test_decide_still_no_trade_on_conflict_even_without_derivatives() -> None:
    """`derivatives_evidence_available=False` never rescues a genuine
    CONFLICT -- that gate runs first, unchanged."""
    matrix = _matrix(
        [
            (EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH),
            (EvidenceGroup.GLOBAL, EvidenceDirection.BEARISH),
        ]
    )
    assessment = build_quality_assessment(
        matrix=matrix, regime=MarketRegime.RANGE, candidates=[], chain_issues=[],
        underlying_data_state_is_ok=True, iv_rank=_UNAVAILABLE_RANK,
    )
    result = decide(assessment, derivatives_evidence_available=False)
    assert result.decision == FinalDecision.NO_TRADE


def test_decide_tradeable_when_all_dimensions_strong() -> None:
    matrix = _matrix(
        [
            (EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH),
            (EvidenceGroup.OPTIONS_OI, EvidenceDirection.BULLISH),
        ]
    )
    assessment = build_quality_assessment(
        matrix=matrix, regime=MarketRegime.TRENDING_BULLISH, candidates=[_candidate(LiquidityGrade.EXCELLENT)],
        chain_issues=[], underlying_data_state_is_ok=True, iv_rank=_UNAVAILABLE_RANK,
    )
    result = decide(assessment)
    assert result.decision == FinalDecision.TRADEABLE


def test_decide_watch_when_evidence_bullish_but_quality_mixed() -> None:
    matrix = _matrix([(EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH)])
    assessment = build_quality_assessment(
        matrix=matrix, regime=MarketRegime.RANGE, candidates=[_candidate(LiquidityGrade.GOOD, strikes_from_atm=2)],
        chain_issues=[ChainQualityIssue(kind=ChainQualityIssueKind.WIDE_SPREAD, detail="x")],
        underlying_data_state_is_ok=True, iv_rank=_UNAVAILABLE_RANK,
    )
    result = decide(assessment)
    assert result.decision == FinalDecision.WATCH


def test_decide_never_tradeable_without_a_directional_bias() -> None:
    matrix = _matrix([(EvidenceGroup.LIQUIDITY, EvidenceDirection.NEUTRAL)])
    assessment = build_quality_assessment(
        matrix=matrix, regime=MarketRegime.RANGE, candidates=[_candidate(LiquidityGrade.EXCELLENT)], chain_issues=[],
        underlying_data_state_is_ok=True, iv_rank=_UNAVAILABLE_RANK,
    )
    result = decide(assessment)
    assert result.decision == FinalDecision.NO_TRADE


# -- summarize_quality_tiers() -----------------------------------------------


def test_quality_tiers_data_quality_passes_through_assessment_unchanged() -> None:
    matrix = _matrix([(EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH)])
    assessment = build_quality_assessment(
        matrix=matrix, regime=MarketRegime.TRENDING_BULLISH, candidates=[_candidate(LiquidityGrade.EXCELLENT)],
        chain_issues=[], underlying_data_state_is_ok=False, iv_rank=_UNAVAILABLE_RANK,
    )
    result = decide(assessment)
    tiers = summarize_quality_tiers(result)
    assert tiers.data_quality == assessment.data_quality == QualityLevel.INSUFFICIENT


def test_quality_tiers_evidence_quality_strong_when_convergent_with_three_plus_supporting() -> None:
    matrix = _matrix(
        [
            (EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH),
            (EvidenceGroup.OPTIONS_OI, EvidenceDirection.BULLISH),
            (EvidenceGroup.OPTIONS_IV, EvidenceDirection.BULLISH),
        ]
    )
    assessment = build_quality_assessment(
        matrix=matrix, regime=MarketRegime.TRENDING_BULLISH, candidates=[_candidate(LiquidityGrade.EXCELLENT)],
        chain_issues=[], underlying_data_state_is_ok=True, iv_rank=_UNAVAILABLE_RANK,
    )
    assert assessment.convergence == OverallConvergence.CONVERGENCE_BULLISH
    assert assessment.supporting_evidence_count >= 3
    result = decide(assessment)
    tiers = summarize_quality_tiers(result)
    assert tiers.evidence_quality == QualityLevel.STRONG


def test_quality_tiers_evidence_quality_moderate_when_convergent_with_fewer_than_three_supporting() -> None:
    matrix = _matrix([(EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH)])
    assessment = build_quality_assessment(
        matrix=matrix, regime=MarketRegime.TRENDING_BULLISH, candidates=[_candidate(LiquidityGrade.EXCELLENT)],
        chain_issues=[], underlying_data_state_is_ok=True, iv_rank=_UNAVAILABLE_RANK,
    )
    assert assessment.convergence == OverallConvergence.CONVERGENCE_BULLISH
    assert assessment.supporting_evidence_count < 3
    result = decide(assessment)
    tiers = summarize_quality_tiers(result)
    assert tiers.evidence_quality == QualityLevel.MODERATE


def test_quality_tiers_evidence_quality_not_inflated_by_correlated_rows_in_one_group() -> None:
    """Final Hardening Pass, Phase 20 (evidence double-counting audit) --
    real, demonstrated defect: 3 BULLISH rows all inside the SAME
    `OPTIONS_OI` group (e.g. PCR change + ATM CE change-in-OI + ATM PE
    change-in-OI, genuinely correlated -- one option-chain OI fetch,
    sliced three ways) must NOT read as "3 supporting" the way 3
    INDEPENDENT groups legitimately would. `overall_convergence()` already
    correctly treats this as exactly ONE bullish group; `evidence_quality`
    must agree, not re-count the same group's own rows as if they were
    separate confirmations."""
    matrix = _matrix(
        [
            (EvidenceGroup.OPTIONS_OI, EvidenceDirection.BULLISH),
            (EvidenceGroup.OPTIONS_OI, EvidenceDirection.BULLISH),
            (EvidenceGroup.OPTIONS_OI, EvidenceDirection.BULLISH),
        ]
    )
    assessment = build_quality_assessment(
        matrix=matrix, regime=MarketRegime.TRENDING_BULLISH, candidates=[_candidate(LiquidityGrade.EXCELLENT)],
        chain_issues=[], underlying_data_state_is_ok=True, iv_rank=_UNAVAILABLE_RANK,
    )
    assert assessment.convergence == OverallConvergence.CONVERGENCE_BULLISH
    # The old, row-counting behavior would have reported 3 here.
    assert assessment.supporting_evidence_count == 1
    result = decide(assessment)
    tiers = summarize_quality_tiers(result)
    assert tiers.evidence_quality == QualityLevel.MODERATE


def test_supporting_group_count_matches_across_multiple_genuinely_independent_groups() -> None:
    """The positive control: 3 rows across 3 DIFFERENT groups genuinely
    are 3 independent confirmations, and must still read as 3."""
    matrix = _matrix(
        [
            (EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH),
            (EvidenceGroup.OPTIONS_OI, EvidenceDirection.BULLISH),
            (EvidenceGroup.OPTIONS_IV, EvidenceDirection.BULLISH),
        ]
    )
    assert matrix.supporting_group_count(EvidenceDirection.BULLISH) == 3


def test_quality_tiers_evidence_quality_weak_on_conflict() -> None:
    matrix = _matrix(
        [
            (EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH),
            (EvidenceGroup.OPTIONS_OI, EvidenceDirection.BEARISH),
        ]
    )
    assessment = build_quality_assessment(
        matrix=matrix, regime=MarketRegime.RANGE, candidates=[_candidate(LiquidityGrade.GOOD)], chain_issues=[],
        underlying_data_state_is_ok=True, iv_rank=_UNAVAILABLE_RANK,
    )
    assert assessment.convergence == OverallConvergence.CONFLICT
    result = decide(assessment)
    tiers = summarize_quality_tiers(result)
    assert tiers.evidence_quality == QualityLevel.WEAK


def test_quality_tiers_evidence_quality_insufficient_when_no_directional_bias() -> None:
    matrix = _matrix([(EvidenceGroup.LIQUIDITY, EvidenceDirection.NEUTRAL)])
    assessment = build_quality_assessment(
        matrix=matrix, regime=MarketRegime.RANGE, candidates=[_candidate(LiquidityGrade.EXCELLENT)], chain_issues=[],
        underlying_data_state_is_ok=True, iv_rank=_UNAVAILABLE_RANK,
    )
    assert assessment.convergence == OverallConvergence.INSUFFICIENT_EVIDENCE
    result = decide(assessment)
    tiers = summarize_quality_tiers(result)
    assert tiers.evidence_quality == QualityLevel.INSUFFICIENT


def test_quality_tiers_decision_quality_maps_each_final_decision() -> None:
    tradeable_matrix = _matrix(
        [
            (EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH),
            (EvidenceGroup.OPTIONS_OI, EvidenceDirection.BULLISH),
        ]
    )
    tradeable_assessment = build_quality_assessment(
        matrix=tradeable_matrix, regime=MarketRegime.TRENDING_BULLISH, candidates=[_candidate(LiquidityGrade.EXCELLENT)],
        chain_issues=[], underlying_data_state_is_ok=True, iv_rank=_UNAVAILABLE_RANK,
    )
    tradeable_result = decide(tradeable_assessment)
    assert tradeable_result.decision == FinalDecision.TRADEABLE
    assert summarize_quality_tiers(tradeable_result).decision_quality == QualityLevel.STRONG

    watch_matrix = _matrix([(EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH)])
    watch_assessment = build_quality_assessment(
        matrix=watch_matrix, regime=MarketRegime.RANGE, candidates=[_candidate(LiquidityGrade.GOOD, strikes_from_atm=2)],
        chain_issues=[ChainQualityIssue(kind=ChainQualityIssueKind.WIDE_SPREAD, detail="x")],
        underlying_data_state_is_ok=True, iv_rank=_UNAVAILABLE_RANK,
    )
    watch_result = decide(watch_assessment)
    assert watch_result.decision == FinalDecision.WATCH
    assert summarize_quality_tiers(watch_result).decision_quality == QualityLevel.MODERATE

    no_trade_matrix = _matrix(
        [
            (EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH),
            (EvidenceGroup.OPTIONS_OI, EvidenceDirection.BEARISH),
        ]
    )
    no_trade_assessment = build_quality_assessment(
        matrix=no_trade_matrix, regime=MarketRegime.RANGE, candidates=[_candidate(LiquidityGrade.GOOD)], chain_issues=[],
        underlying_data_state_is_ok=True, iv_rank=_UNAVAILABLE_RANK,
    )
    no_trade_result = decide(no_trade_assessment)
    assert no_trade_result.decision == FinalDecision.NO_TRADE
    assert summarize_quality_tiers(no_trade_result).decision_quality == QualityLevel.WEAK

    insufficient_matrix = _matrix([(EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH)])
    insufficient_assessment = build_quality_assessment(
        matrix=insufficient_matrix, regime=MarketRegime.TRENDING_BULLISH, candidates=[_candidate(LiquidityGrade.EXCELLENT)],
        chain_issues=[], underlying_data_state_is_ok=False, iv_rank=_UNAVAILABLE_RANK,
    )
    insufficient_result = decide(insufficient_assessment)
    assert insufficient_result.decision == FinalDecision.DATA_INSUFFICIENT
    assert summarize_quality_tiers(insufficient_result).decision_quality == QualityLevel.INSUFFICIENT


# -- Analytical Consistency Audit: evidence-count honesty on conflict --
# Real GAIL UAT scenario: 3 independent-group BULLISH rows + 2
# independent-group BEARISH rows -> genuine CONFLICT (bias=NEUTRAL). The
# defect: `conflicting_evidence_count` had no guard for a non-decisive
# bias and silently reported the BULLISH row count (3) as if it meant
# "conflicting", while `supporting_evidence_count` correctly showed 0 --
# an asymmetric, misleading "0 supporting / 3 conflicting" readout that
# hid the real bearish rows entirely.


def test_conflicting_evidence_count_is_honestly_zero_not_an_arbitrary_side_on_conflict() -> None:
    matrix = _matrix(
        [
            (EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH),
            (EvidenceGroup.OPTIONS_OI, EvidenceDirection.BULLISH),
            (EvidenceGroup.GLOBAL, EvidenceDirection.BULLISH),
            (EvidenceGroup.OPTIONS_IV, EvidenceDirection.BEARISH),
            (EvidenceGroup.LIQUIDITY, EvidenceDirection.BEARISH),
        ]
    )
    assessment = build_quality_assessment(
        matrix=matrix, regime=MarketRegime.RANGE, candidates=[_candidate(LiquidityGrade.GOOD)], chain_issues=[],
        underlying_data_state_is_ok=True, iv_rank=_UNAVAILABLE_RANK,
    )
    assert assessment.convergence == OverallConvergence.CONFLICT
    assert assessment.market_bias == EvidenceDirection.NEUTRAL
    # Neither count may claim a meaningful reading when there is no decided bias.
    assert assessment.supporting_evidence_count == 0
    assert assessment.conflicting_evidence_count == 0


def test_conflicting_evidence_count_is_honestly_zero_on_insufficient_evidence() -> None:
    matrix = _matrix([(EvidenceGroup.LIQUIDITY, EvidenceDirection.UNKNOWN)])
    assessment = build_quality_assessment(
        matrix=matrix, regime=MarketRegime.RANGE, candidates=[_candidate(LiquidityGrade.GOOD)], chain_issues=[],
        underlying_data_state_is_ok=True, iv_rank=_UNAVAILABLE_RANK,
    )
    assert assessment.convergence == OverallConvergence.INSUFFICIENT_EVIDENCE
    assert assessment.market_bias == EvidenceDirection.UNKNOWN
    assert assessment.supporting_evidence_count == 0
    assert assessment.conflicting_evidence_count == 0


def test_supporting_and_conflicting_counts_on_a_genuinely_decisive_bias() -> None:
    """Preserves the already-correct BULLISH behavior -- must not regress
    the case the fix does not touch. Structural note (not a defect):
    `overall_convergence()` requires ZERO bearish-direction rows anywhere
    in the matrix for a genuine CONVERGENCE_BULLISH read (any bearish row
    would make its own group BEARISH or CONFLICTING, which breaks
    convergence) -- so `conflicting_evidence_count` was, and remains,
    structurally guaranteed 0 whenever bias is decisively BULLISH; this
    was already true before this fix and is unrelated to it."""
    matrix = _matrix(
        [
            (EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH),
            (EvidenceGroup.OPTIONS_OI, EvidenceDirection.BULLISH),
            (EvidenceGroup.OPTIONS_IV, EvidenceDirection.NEUTRAL),
        ]
    )
    assessment = build_quality_assessment(
        matrix=matrix, regime=MarketRegime.TRENDING_BULLISH, candidates=[_candidate(LiquidityGrade.EXCELLENT)],
        chain_issues=[], underlying_data_state_is_ok=True, iv_rank=_UNAVAILABLE_RANK,
    )
    assert assessment.convergence == OverallConvergence.CONVERGENCE_BULLISH
    assert assessment.market_bias == EvidenceDirection.BULLISH
    assert assessment.supporting_evidence_count == 2
    assert assessment.conflicting_evidence_count == 0


def _decision(decision: FinalDecision, *, convergence: OverallConvergence = OverallConvergence.INSUFFICIENT_EVIDENCE) -> DecisionResult:
    assessment = QualityAssessment(
        market_bias=EvidenceDirection.UNKNOWN, convergence=convergence,
        setup_quality=QualityLevel.MODERATE, option_quality=QualityLevel.MODERATE,
        liquidity_quality=QualityLevel.MODERATE, data_quality=QualityLevel.MODERATE,
        risk_quality=QualityLevel.MODERATE, supporting_evidence_count=0, conflicting_evidence_count=0,
    )
    return DecisionResult(decision=decision, assessment=assessment, reasoning="test")


def test_research_state_stale_candles_are_confirmation_pending_not_confirmed() -> None:
    state = derive_research_state(
        decision=_decision(FinalDecision.TRADEABLE), candles_are_current=False, day_change_pct=Decimal("1.0"),
    )
    assert state == ResearchState.CONFIRMATION_PENDING
    assert state != ResearchState.CONFIRMED_SETUP


def test_research_state_tradeable_current_is_confirmed_setup() -> None:
    state = derive_research_state(
        decision=_decision(FinalDecision.TRADEABLE), candles_are_current=True, day_change_pct=Decimal("1.0"),
    )
    assert state == ResearchState.CONFIRMED_SETUP


def test_research_state_conflict_is_conflict() -> None:
    state = derive_research_state(
        decision=_decision(FinalDecision.NO_TRADE, convergence=OverallConvergence.CONFLICT),
        candles_are_current=True, day_change_pct=Decimal("1.0"),
    )
    assert state == ResearchState.CONFLICT


def test_research_state_stale_chain_is_confirmation_pending() -> None:
    state = derive_research_state(
        decision=_decision(FinalDecision.TRADEABLE), candles_are_current=True, day_change_pct=Decimal("1.0"),
        chain_is_current=False,
    )
    assert state == ResearchState.CONFIRMATION_PENDING
    assert state != ResearchState.CONFIRMED_SETUP


def test_research_state_watch_without_two_groups_is_watch_not_early() -> None:
    state = derive_research_state(
        decision=_decision(FinalDecision.WATCH), candles_are_current=True, day_change_pct=Decimal("1.0"),
    )
    assert state == ResearchState.WATCH
    assert state != ResearchState.EARLY_SETUP


def test_research_state_watch_with_named_pattern_is_early_setup() -> None:
    state = derive_research_state(
        decision=_decision(FinalDecision.WATCH), candles_are_current=True, day_change_pct=Decimal("1.0"),
        development_pattern="OI_MIGRATION",
    )
    assert state == ResearchState.EARLY_SETUP


def test_research_state_watch_with_two_groups_is_not_early_setup() -> None:
    decision = _decision(FinalDecision.WATCH)
    decision = DecisionResult(
        decision=decision.decision, assessment=QualityAssessment(
            market_bias=EvidenceDirection.UNKNOWN, convergence=OverallConvergence.INSUFFICIENT_EVIDENCE,
            setup_quality=QualityLevel.MODERATE, option_quality=QualityLevel.MODERATE,
            liquidity_quality=QualityLevel.MODERATE, data_quality=QualityLevel.MODERATE,
            risk_quality=QualityLevel.MODERATE, supporting_evidence_count=2, conflicting_evidence_count=0,
        ), reasoning="test",
    )
    state = derive_research_state(decision=decision, candles_are_current=True, day_change_pct=Decimal("1.0"))
    assert state == ResearchState.WATCH
    assert state != ResearchState.EARLY_SETUP


def test_research_state_does_not_read_cash_context() -> None:
    """Cash confirmation must not be an input -- the function has no such parameter."""
    import inspect
    assert "breadth" not in inspect.signature(derive_research_state).parameters
    assert "delivery" not in inspect.signature(derive_research_state).parameters
    assert "fii" not in inspect.signature(derive_research_state).parameters

