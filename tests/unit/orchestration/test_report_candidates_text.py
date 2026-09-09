"""Analytical Consistency Audit -- real, traced defect: the CANDIDATES
section previously said "no candidate passed the minimum evidence/
liquidity/data-quality bar" even when NO per-strike check was ever
attempted (candidate_engine.generate_candidates() returns `[]`
immediately whenever bias is not BULLISH/BEARISH, before any liquidity/
spread check runs -- confirmed by reading that function directly). This
file proves the corrected text distinguishes the two real cases.
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
from app.domain.options.evidence_matrix import EvidenceDirection, OverallConvergence
from app.orchestration.options_intelligence_report import OptionsIntelligenceReport

GENERATED_AT = datetime(2026, 8, 30, 4, 0, tzinfo=UTC)


def _assessment(bias: EvidenceDirection) -> QualityAssessment:
    return QualityAssessment(
        market_bias=bias, convergence=OverallConvergence.CONFLICT, setup_quality=QualityLevel.MODERATE,
        option_quality=QualityLevel.INSUFFICIENT, liquidity_quality=QualityLevel.INSUFFICIENT, data_quality=QualityLevel.STRONG,
        risk_quality=QualityLevel.MODERATE, supporting_evidence_count=0, conflicting_evidence_count=0,
    )


def _report(bias: EvidenceDirection) -> OptionsIntelligenceReport:
    decision = DecisionResult(decision=FinalDecision.NO_TRADE, assessment=_assessment(bias), reasoning="test")
    return OptionsIntelligenceReport(
        symbol="UNOMINDA", underlying_instrument_key="NSE_EQ|X", generated_at=GENERATED_AT,
        data_state=MarketDataState.LIVE_SNAPSHOT, data_age_seconds=1.0, spot=Decimal("1276"),
        decision=decision, candidates=[],
    )


def test_no_candidates_with_neutral_bias_explains_none_were_ever_attempted() -> None:
    text = _report(EvidenceDirection.NEUTRAL).render_text()
    assert "no directional bias was established" in text
    assert "REQUESTED CONTRACT section above already assessed" in text
    # Must NOT claim a check ran and failed when none was ever attempted.
    assert "passed the minimum" not in text.split("CANDIDATES", 1)[1].split("FINAL ASSESSMENT", 1)[0]


def test_no_candidates_with_unknown_bias_explains_none_were_ever_attempted() -> None:
    text = _report(EvidenceDirection.UNKNOWN).render_text()
    assert "no directional bias was established" in text


def test_no_candidates_with_decisive_bias_correctly_says_a_check_actually_ran() -> None:
    """When bias IS decisive, an empty candidate list genuinely means a
    real per-strike liquidity/spread/data-quality check ran and excluded
    everything -- the ORIGINAL wording for this specific case remains
    accurate and is preserved."""
    text = _report(EvidenceDirection.BULLISH).render_text()
    assert "no directional bias was established" not in text
    assert "passed the minimum liquidity/spread/data-quality bar" in text
