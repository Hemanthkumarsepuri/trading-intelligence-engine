"""Sprint 7A, Objective 13 -- `build_no_trade_explanation()` is a pure
synthesis over an already-built `EvidenceMatrix`/`QualityAssessment` --
no new computation, informational only.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.domain.market.data_state import MarketDataState
from app.domain.options.decision_engine import (
    DecisionResult,
    FinalDecision,
    QualityAssessment,
    QualityLevel,
)
from app.domain.options.evidence_matrix import (
    EvidenceDirection,
    EvidenceGroup,
    EvidenceMatrix,
    EvidenceRow,
    OverallConvergence,
)
from app.domain.options.market_regime_context import (
    MacroRegime,
    MacroRegimeInput,
    MacroRegimeResult,
)
from app.orchestration.options_intelligence_report import (
    OptionsIntelligenceReport,
    _evidence_tier,
    build_final_decision_explanation,
    build_no_trade_explanation,
)

GENERATED_AT = datetime(2026, 8, 30, 4, 0, tzinfo=UTC)


def _matrix() -> EvidenceMatrix:
    return EvidenceMatrix(rows=[
        EvidenceRow("M15 trend", EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH, "ascending"),
        EvidenceRow("VWAP", EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BEARISH, "below VWAP"),
        EvidenceRow("IV", EvidenceGroup.OPTIONS_IV, EvidenceDirection.UNKNOWN, "unavailable"),
    ])


def _assessment(*, convergence: OverallConvergence, data_quality: QualityLevel = QualityLevel.STRONG) -> QualityAssessment:
    return QualityAssessment(
        market_bias=EvidenceDirection.NEUTRAL, convergence=convergence, setup_quality=QualityLevel.MODERATE,
        option_quality=QualityLevel.INSUFFICIENT, liquidity_quality=QualityLevel.INSUFFICIENT, data_quality=data_quality,
        risk_quality=QualityLevel.MODERATE, supporting_evidence_count=0, conflicting_evidence_count=0,
    )


def _report(*, convergence: OverallConvergence, data_quality: QualityLevel = QualityLevel.STRONG) -> OptionsIntelligenceReport:
    decision = DecisionResult(decision=FinalDecision.NO_TRADE, assessment=_assessment(convergence=convergence, data_quality=data_quality), reasoning="test reasoning")
    return OptionsIntelligenceReport(
        symbol="UNOMINDA", underlying_instrument_key="NSE_EQ|X", generated_at=GENERATED_AT,
        data_state=MarketDataState.LIVE_SNAPSHOT, data_age_seconds=1.0, spot=Decimal("1276"),
        decision=decision, candidates=[], matrix=_matrix(),
    )


def test_none_when_no_real_matrix_or_decision() -> None:
    report = OptionsIntelligenceReport(
        symbol="X", underlying_instrument_key=None, generated_at=GENERATED_AT, data_state=MarketDataState.LIVE_SNAPSHOT, data_age_seconds=1.0,
    )
    assert build_no_trade_explanation(report) is None


def test_separates_bullish_bearish_and_unreliable_evidence() -> None:
    explanation = build_no_trade_explanation(_report(convergence=OverallConvergence.CONFLICT))
    assert explanation is not None
    assert any("M15 trend" in x for x in explanation.what_supports_bullish)
    assert any("VWAP" in x for x in explanation.what_supports_bearish)
    assert any("IV" in x for x in explanation.what_is_unreliable)
    assert not any("IV" in x for x in explanation.what_supports_bullish + explanation.what_supports_bearish)


def test_decisive_missing_confirmation_names_conflict() -> None:
    explanation = build_no_trade_explanation(_report(convergence=OverallConvergence.CONFLICT))
    assert explanation is not None
    assert any("disagree" in x for x in explanation.decisive_missing_confirmation)
    assert explanation.what_would_change_the_state  # never empty when a real reason exists


def test_decisive_missing_confirmation_names_insufficient_data_quality_first() -> None:
    """Data-quality insufficiency is checked BEFORE convergence -- matches
    `decide()`'s own real precedence (data quality first, always)."""
    explanation = build_no_trade_explanation(_report(convergence=OverallConvergence.CONFLICT, data_quality=QualityLevel.INSUFFICIENT))
    assert explanation is not None
    assert any("data quality itself is insufficient" in x for x in explanation.decisive_missing_confirmation)


def test_rendered_text_includes_all_seven_sections() -> None:
    text = _report(convergence=OverallConvergence.CONFLICT).render_text()
    for heading in (
        "WHAT WE KNOW", "WHAT SUPPORTS BULLISH", "WHAT SUPPORTS BEARISH", "WHAT IS UNRELIABLE",
        "DECISIVE MISSING CONFIRMATION", "WHAT WOULD CHANGE THE STATE",
        "WHY THE OPTION ITSELF IS NOT YET ATTRACTIVE/CONFIRMED",
    ):
        assert heading in text


# -- Sprint 7A, Objective 12: evidence hierarchy --------------------------


def test_evidence_tier_price_structure_and_relative_strength_are_primary() -> None:
    assert _evidence_tier(EvidenceGroup.UNDERLYING_PRICE_STRUCTURE) == "PRIMARY"
    assert _evidence_tier(EvidenceGroup.RELATIVE_STRENGTH) == "PRIMARY"


def test_evidence_tier_futures_and_oi_and_iv_are_secondary() -> None:
    assert _evidence_tier(EvidenceGroup.FUTURES) == "SECONDARY"
    assert _evidence_tier(EvidenceGroup.OPTIONS_OI) == "SECONDARY"
    assert _evidence_tier(EvidenceGroup.OPTIONS_IV) == "SECONDARY"


def test_evidence_tier_global_and_news_are_context() -> None:
    assert _evidence_tier(EvidenceGroup.GLOBAL) == "CONTEXT"
    assert _evidence_tier(EvidenceGroup.NEWS_EVENT) == "CONTEXT"


def test_evidence_tier_never_directional_groups_have_no_tier() -> None:
    assert _evidence_tier(EvidenceGroup.LIQUIDITY) == "N/A"
    assert _evidence_tier(EvidenceGroup.DATA_QUALITY) == "N/A"


def test_evidence_hierarchy_never_changes_convergence() -> None:
    """The hierarchy is a label only -- `overall_convergence()` reads
    unchanged real rows regardless of tier."""
    matrix = _matrix()
    before = matrix.overall_convergence()
    for r in matrix.rows:
        _evidence_tier(r.group)  # calling the labeler must not mutate anything
    assert matrix.overall_convergence() == before


# -- Sprint 7B, Objectives 13/14/15: final decision explanation ----------


def test_final_decision_explanation_none_when_no_real_matrix_or_decision() -> None:
    report = OptionsIntelligenceReport(
        symbol="X", underlying_instrument_key=None, generated_at=GENERATED_AT, data_state=MarketDataState.LIVE_SNAPSHOT, data_age_seconds=1.0,
    )
    assert build_final_decision_explanation(report) is None


def test_final_decision_explanation_answers_all_5_questions() -> None:
    report = _report(convergence=OverallConvergence.CONFLICT)
    explanation = build_final_decision_explanation(report)
    assert explanation is not None
    assert explanation.what_is_the_market_doing
    assert explanation.what_is_the_stock_doing_relative_to_market
    assert explanation.what_is_the_options_market_showing  # IV row is OPTIONS_IV
    assert explanation.what_can_invalidate_this
    assert explanation.what_data_is_missing  # IV row is UNKNOWN


def test_final_decision_explanation_reuses_real_market_regime_context_verbatim() -> None:
    """Never recomputes -- reads `report.market_regime_context` as-is."""
    report = _report(convergence=OverallConvergence.CONFLICT)
    regime = MacroRegimeResult(regime=MacroRegime.RISK_OFF, detail="test detail", inputs=[MacroRegimeInput("NIFTY 50", Decimal("-1.0"), True, "down")])
    report = OptionsIntelligenceReport(**{**report.__dict__, "market_regime_context": regime})
    explanation = build_final_decision_explanation(report)
    assert explanation is not None
    assert any("RISK_OFF" in x for x in explanation.what_is_the_market_doing)


def test_final_decision_explanation_market_regime_unknown_when_not_computed() -> None:
    report = _report(convergence=OverallConvergence.CONFLICT)
    explanation = build_final_decision_explanation(report)
    assert explanation is not None
    assert any("UNKNOWN" in x for x in explanation.what_is_the_market_doing)


def test_final_decision_explanation_no_candidate_leaves_contract_chain_none() -> None:
    explanation = build_final_decision_explanation(_report(convergence=OverallConvergence.CONFLICT))
    assert explanation is not None
    assert explanation.direction is None
    assert explanation.option_side is None
    assert explanation.invalidation is None


def test_final_decision_explanation_never_feeds_back_into_the_matrix() -> None:
    """Structural proof of Objective 15/17's discipline: calling the
    builder does not mutate the report's own matrix/convergence."""
    report = _report(convergence=OverallConvergence.CONFLICT)
    assert report.matrix is not None
    before = report.matrix.overall_convergence()
    build_final_decision_explanation(report)
    assert report.matrix.overall_convergence() == before


def test_rendered_text_includes_final_decision_explanation_for_every_decision() -> None:
    """Unlike WHY NO TRADE (only NO_TRADE/DATA_INSUFFICIENT), this section
    renders for every decision outcome, per Sprint 7B's own requirement."""
    text = _report(convergence=OverallConvergence.CONFLICT).render_text()
    for heading in (
        "FINAL DECISION EXPLANATION -- 5 STANDARD QUESTIONS", "1. WHAT IS THE MARKET DOING",
        "2. WHAT IS THIS STOCK DOING RELATIVE TO MARKET/SECTOR", "3. WHAT IS THE OPTIONS MARKET SHOWING",
        "4. WHAT CAN INVALIDATE THIS", "5. WHAT DATA IS MISSING",
    ):
        assert heading in text


def test_rendered_text_includes_reason_recheck_bullets_for_no_trade() -> None:
    """Sprint 7B, Objective 14 -- the compact Reason:/Recheck: bullet
    format, alongside (not instead of) the Sprint 7A section format."""
    text = _report(convergence=OverallConvergence.CONFLICT).render_text()
    assert "NO-TRADE SUMMARY (Reason / Recheck)" in text
    assert "Reason:" in text
    assert "Recheck:" in text


def test_no_trade_explanation_never_appears_for_a_report_with_no_matrix() -> None:
    """The existing (Sprint-pre-7A) hand-built fixtures in
    test_report_candidates_text.py never set `matrix` -- confirms the new
    section stays silent rather than crashing on that real, common shape."""
    decision = DecisionResult(decision=FinalDecision.NO_TRADE, assessment=_assessment(convergence=OverallConvergence.CONFLICT), reasoning="test")
    report = OptionsIntelligenceReport(
        symbol="X", underlying_instrument_key=None, generated_at=GENERATED_AT, data_state=MarketDataState.LIVE_SNAPSHOT,
        data_age_seconds=1.0, decision=decision, candidates=[],
    )
    text = report.render_text()
    assert "WHY NO TRADE -- STRUCTURED EXPLANATION" not in text
