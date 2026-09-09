"""Sprint 5, Phase 8 — the additive `ContractObservationSection`
(`AnalysisSnapshot.contracts`) and `OutcomeCheckpoint.ce_option`/
`pe_option` fields must not break a record persisted before this sprint
(neither field existed yet). Mirrors the same real-JSON-string discipline
`test_news_section_compatibility.py` established for Sprint 4's additive
`NewsSection` fields -- a pre-Sprint-5 shape is built by taking a REAL
current record and deleting the new keys, rather than hand-typing a
guessed JSON string.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal

from app.domain.audit.models import (
    AdversarialSection,
    AnalysisSnapshot,
    ContractAssessmentSnapshot,
    ContractObservationSection,
    DecisionSection,
    EvidenceSection,
    FuturesSection,
    GlobalSection,
    IdentitySection,
    LevelsSection,
    NewsSection,
    OptionsSection,
    OutcomeCheckpoint,
    QualitySection,
    TechnicalSection,
    TemporalSection,
    UnderlyingSection,
    new_audit_id,
)
from app.domain.market.models import OptionRight

_GENERATED_AT = datetime(2026, 8, 26, 6, 0, tzinfo=UTC)


def _real_snapshot() -> AnalysisSnapshot:
    return AnalysisSnapshot(
        identity=IdentitySection(audit_id=new_audit_id(), symbol="RELIANCE", instrument_key="NSE_EQ|X", generated_at=_GENERATED_AT, market_state="LIVE_SNAPSHOT", analysis_version="1.0.0"),
        underlying=UnderlyingSection(spot=None, day_change=None, day_change_pct=None, data_age_seconds=None, freshness_label=None),
        technical=TechnicalSection(trend="UNKNOWN", ema_alignment=None, vwap_position=None, rsi_value=None, atr_pct_of_price=None, regime_detail=""),
        futures=FuturesSection(futures_instrument_key=None, futures_ltp=None, futures_oi=None, futures_basis_pct=None, futures_interpretation=None),
        options=OptionsSection(expiry=date(2026, 9, 24), atm_strike=None, total_call_oi=None, total_put_oi=None, pcr_oi=None, atm_ce_iv=None, atm_pe_iv=None, chain_iv=None, ce_pe_skew=None, iv_trend=None, oi_structure_detail=None, chain_reference=None),
        temporal=TemporalSection(), levels=LevelsSection(), global_context=GlobalSection(inputs=[], verdict=None, detail=None),
        news=NewsSection(provider="upstox", items=[], fetch_error=None, unavailable_reason=None),
        evidence=EvidenceSection(rows=[], convergence="INSUFFICIENT_EVIDENCE"),
        adversarial=AdversarialSection(bull_case=[], bear_case=[], contradictions=[], missing_data=[], key_risks=[], opposite_case_is_equally_supported=False),
        quality=QualitySection(data_quality="INSUFFICIENT", evidence_quality="INSUFFICIENT", decision_quality="INSUFFICIENT", setup_quality="INSUFFICIENT", option_quality="INSUFFICIENT", liquidity_quality="INSUFFICIENT", risk_quality="INSUFFICIENT", supporting_evidence_count=0, conflicting_evidence_count=0),
        candidates=[],
        decision=DecisionSection(final_bias="NEUTRAL", decision="NO_TRADE", reasoning="insufficient data", candidate_selected=None, invalidation_level=None, invalidation_description=None),
    )


def test_pre_sprint5_analysis_snapshot_json_without_contracts_still_deserializes() -> None:
    real = _real_snapshot()
    old_shaped = json.loads(real.model_dump_json())
    assert "contracts" in old_shaped
    del old_shaped["contracts"]  # simulate a record written before this field existed

    restored = AnalysisSnapshot.model_validate_json(json.dumps(old_shaped))
    assert restored.contracts == ContractObservationSection()
    assert restored.contracts.ce_requested is None
    assert restored.contracts.pe_requested is None


def test_pre_sprint5_outcome_checkpoint_json_without_ce_pe_option_still_deserializes() -> None:
    checkpoint = OutcomeCheckpoint(
        audit_id="abc", checkpoint_label="5m", scheduled_offset_seconds=300.0, captured_at=_GENERATED_AT,
        actual_elapsed_seconds=300.0, status="MARKET_CLOSED", underlying=None, option=None, evidence=None, adversarial=None, detail="",
    )
    old_shaped = json.loads(checkpoint.model_dump_json())
    assert "ce_option" in old_shaped and "pe_option" in old_shaped
    del old_shaped["ce_option"]
    del old_shaped["pe_option"]

    restored = OutcomeCheckpoint.model_validate_json(json.dumps(old_shaped))
    assert restored.ce_option is None
    assert restored.pe_option is None


def test_contract_observation_section_round_trips_with_real_data() -> None:
    section = ContractObservationSection(
        reference_strike=Decimal("4000"), requested_right=OptionRight.CE,
        ce_requested=ContractAssessmentSnapshot(
            strike=Decimal("4000"), right=OptionRight.CE, moneyness="ATM", ltp=Decimal("168.0"), bid=Decimal("167.5"), ask=Decimal("168.5"),
            spread_pct=Decimal("0.3"), open_interest=1000, change_in_open_interest=50, volume=500, implied_volatility=Decimal("37.5"),
            delta=Decimal("0.5"), theta=Decimal("-2.0"), gamma=Decimal("0.01"), vega=Decimal("1.0"), liquidity_grade="GOOD",
            decay_verdict="DECAY_ACCEPTABLE", decay_viability_ratio=Decimal("2.0"), distance_to_support_pct=None, distance_to_resistance_pct=None,
        ),
        ce_alternatives=[], pe_requested=None, pe_alternatives=[],
    )
    round_tripped = ContractObservationSection.model_validate_json(section.model_dump_json())
    assert round_tripped == section
