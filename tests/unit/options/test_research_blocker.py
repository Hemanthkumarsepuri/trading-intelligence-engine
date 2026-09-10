"""Unit tests for `app.domain.options.research_blocker` -- the generic
fix for the real KAYNES UAT defect (`docs/TIRE_OPERATOR_UAT.md`, Defect
2): the top-line explanation must always name the actual, strongest
blocking reason, never a generic/orthogonal one, and must never
contradict the decision engine's own reasoning."""

from __future__ import annotations

from decimal import Decimal

from app.domain.options.decision_engine import (
    DecisionResult,
    FinalDecision,
    QualityAssessment,
    QualityLevel,
)
from app.domain.options.development import DevelopmentNarrative, DevelopmentPattern
from app.domain.options.evidence_matrix import EvidenceDirection, OverallConvergence
from app.domain.options.research_blocker import BlockerClass, determine_blockers


def _assessment(
    *,
    convergence: OverallConvergence = OverallConvergence.CONVERGENCE_BEARISH,
    market_bias: EvidenceDirection = EvidenceDirection.BEARISH,
    option_quality: QualityLevel = QualityLevel.MODERATE,
    liquidity_quality: QualityLevel = QualityLevel.MODERATE,
    data_quality: QualityLevel = QualityLevel.MODERATE,
    setup_quality: QualityLevel = QualityLevel.MODERATE,
    risk_quality: QualityLevel = QualityLevel.MODERATE,
) -> QualityAssessment:
    return QualityAssessment(
        market_bias=market_bias, convergence=convergence, setup_quality=setup_quality, option_quality=option_quality,
        liquidity_quality=liquidity_quality, data_quality=data_quality, risk_quality=risk_quality,
        supporting_evidence_count=1, conflicting_evidence_count=0,
    )


_FUTURES_STRUCTURE_NARRATIVE = DevelopmentNarrative(
    pattern=DevelopmentPattern.FUTURES_STRUCTURE,
    what_is_developing="Futures basis vs spot changed vs a comparable prior observation.",
    why_it_matters="futures discount to spot is widening",
    what_is_missing="Price structure and/or option-chain confirmation are incomplete.",
    confirm_if="x", invalidate_if="y", freshness_note="z",
)


def test_kaynes_style_case_primary_blocker_is_contract_unusable_not_confirmation_text() -> None:
    """The real, demonstrated defect: `decide()` already knows the true
    reason is contract illiquidity (`option_quality`/`liquidity_quality`
    INSUFFICIENT -> NO_TRADE, "no sufficiently liquid option candidate
    exists for this bias"), but `DevelopmentNarrative.what_is_missing`
    talks about price/chain confirmation instead. `determine_blockers()`
    must make CONTRACT_UNUSABLE the PRIMARY blocker, using the decision
    engine's own real reasoning -- never the generic development text."""
    decision = DecisionResult(
        decision=FinalDecision.NO_TRADE,
        assessment=_assessment(option_quality=QualityLevel.INSUFFICIENT, liquidity_quality=QualityLevel.INSUFFICIENT),
        reasoning="no sufficiently liquid option candidate exists for this bias",
    )
    result = determine_blockers(
        decision=decision, candles_are_current=False, chain_is_current=True, quote_is_current=True,
        day_change_pct=Decimal("0.1"), development=_FUTURES_STRUCTURE_NARRATIVE,
    )
    assert result.primary.blocker_class == BlockerClass.CONTRACT_UNUSABLE
    assert "liquid option candidate" in result.primary.explanation
    # The stale M15 candle is also real and true -- it must not disappear,
    # but it must not outrank the more fundamental contract problem either.
    assert any(s.blocker_class == BlockerClass.CORE_STREAM_STALE for s in result.secondary)
    assert not any(s.blocker_class == BlockerClass.CONTRACT_UNUSABLE for s in result.secondary)


def test_kaynes_contract_unusable_outranks_insufficient_history() -> None:
    decision = DecisionResult(
        decision=FinalDecision.NO_TRADE,
        assessment=_assessment(option_quality=QualityLevel.INSUFFICIENT, liquidity_quality=QualityLevel.INSUFFICIENT),
        reasoning="no sufficiently liquid option candidate exists for this bias",
    )
    result = determine_blockers(
        decision=decision, candles_are_current=True, chain_is_current=True, quote_is_current=True,
        day_change_pct=Decimal("0.1"), development=_FUTURES_STRUCTURE_NARRATIVE,
        historical_insufficient=True,
    )
    assert result.primary.blocker_class == BlockerClass.CONTRACT_UNUSABLE
    assert any(s.blocker_class == BlockerClass.INSUFFICIENT_HISTORY for s in result.secondary)


def test_data_insufficient_always_wins() -> None:
    decision = DecisionResult(
        decision=FinalDecision.DATA_INSUFFICIENT,
        assessment=_assessment(data_quality=QualityLevel.INSUFFICIENT, convergence=OverallConvergence.CONFLICT),
        reasoning="data quality is insufficient to reason about this instrument right now",
    )
    result = determine_blockers(
        decision=decision, candles_are_current=False, chain_is_current=False, quote_is_current=False,
        day_change_pct=None, development=None,
    )
    assert result.primary.blocker_class == BlockerClass.DATA_INSUFFICIENT


def test_conflict_outranks_stale_streams_and_contract() -> None:
    decision = DecisionResult(
        decision=FinalDecision.NO_TRADE,
        assessment=_assessment(convergence=OverallConvergence.CONFLICT, market_bias=EvidenceDirection.NEUTRAL, option_quality=QualityLevel.INSUFFICIENT),
        reasoning="evidence matrix convergence is CONFLICT -- no coherent bias to act on",
    )
    result = determine_blockers(
        decision=decision, candles_are_current=False, chain_is_current=True, quote_is_current=True,
        day_change_pct=None, development=None,
    )
    assert result.primary.blocker_class == BlockerClass.CONFLICT


def test_explanation_never_contradicts_decision_no_confirmed_language_on_data_insufficient() -> None:
    decision = DecisionResult(
        decision=FinalDecision.DATA_INSUFFICIENT,
        assessment=_assessment(data_quality=QualityLevel.INSUFFICIENT),
        reasoning="data quality is insufficient to reason about this instrument right now",
    )
    result = determine_blockers(
        decision=decision, candles_are_current=True, chain_is_current=True, quote_is_current=True,
        day_change_pct=Decimal("1.0"), development=None,
    )
    assert "confirmed" not in result.primary.explanation.lower()
    assert result.primary.blocker_class == BlockerClass.DATA_INSUFFICIENT


def test_extended_is_primary_when_nothing_more_fundamental_blocks() -> None:
    decision = DecisionResult(decision=FinalDecision.WATCH, assessment=_assessment(), reasoning="real BEARISH evidence, but quality dimensions are not decisive enough for TRADEABLE")
    result = determine_blockers(
        decision=decision, candles_are_current=True, chain_is_current=True, quote_is_current=True,
        day_change_pct=Decimal("7.5"), development=None,
    )
    assert result.primary.blocker_class == BlockerClass.EXTENDED
    assert "7.5" in result.primary.explanation


def test_required_confirmation_missing_uses_development_text_when_a_pattern_is_named() -> None:
    decision = DecisionResult(decision=FinalDecision.WATCH, assessment=_assessment(), reasoning="real BEARISH evidence, but quality dimensions are not decisive enough for TRADEABLE")
    result = determine_blockers(
        decision=decision, candles_are_current=True, chain_is_current=True, quote_is_current=True,
        day_change_pct=Decimal("0.5"), development=_FUTURES_STRUCTURE_NARRATIVE,
    )
    assert result.primary.blocker_class == BlockerClass.REQUIRED_CONFIRMATION_MISSING
    assert result.primary.explanation == _FUTURES_STRUCTURE_NARRATIVE.what_is_missing
    assert result.missing_confirmation == _FUTURES_STRUCTURE_NARRATIVE.what_is_missing


def test_none_blocker_when_tradeable_and_nothing_blocks() -> None:
    decision = DecisionResult(
        decision=FinalDecision.TRADEABLE,
        assessment=_assessment(setup_quality=QualityLevel.STRONG, option_quality=QualityLevel.STRONG, liquidity_quality=QualityLevel.STRONG, risk_quality=QualityLevel.STRONG),
        reasoning="4/4 quality dimensions STRONG, none WEAK, evidence bias is BEARISH",
    )
    result = determine_blockers(
        decision=decision, candles_are_current=True, chain_is_current=True, quote_is_current=True,
        day_change_pct=Decimal("0.5"), development=None,
    )
    assert result.primary.blocker_class == BlockerClass.NONE
    assert result.secondary == ()


def test_core_stream_stale_names_which_streams() -> None:
    decision = DecisionResult(decision=FinalDecision.WATCH, assessment=_assessment(), reasoning="real BEARISH evidence, but quality dimensions are not decisive enough for TRADEABLE")
    result = determine_blockers(
        decision=decision, candles_are_current=False, chain_is_current=False, quote_is_current=True,
        day_change_pct=Decimal("0.5"), development=None,
    )
    assert result.primary.blocker_class == BlockerClass.CORE_STREAM_STALE
    assert "option chain" in result.primary.explanation
    assert "M15 candles" in result.primary.explanation
    assert "underlying quote" not in result.primary.explanation
