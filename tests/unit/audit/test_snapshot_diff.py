from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
    QualitySection,
    TechnicalSection,
    TemporalSection,
    UnderlyingSection,
)
from app.domain.audit.snapshot_diff import build_snapshot_change_report
from app.domain.market.models import OptionRight

GENERATED_AT = datetime(2026, 8, 24, 10, 0, tzinfo=UTC)


def _quality() -> QualitySection:
    return QualitySection(
        data_quality="STRONG", evidence_quality="MODERATE", decision_quality="MODERATE", setup_quality="STRONG",
        option_quality="STRONG", liquidity_quality="STRONG", risk_quality="STRONG", supporting_evidence_count=2,
        conflicting_evidence_count=0,
    )


def _ce(ltp: Decimal, iv: Decimal, oi: int) -> ContractAssessmentSnapshot:
    return ContractAssessmentSnapshot(
        strike=Decimal("1300"), right=OptionRight.CE, moneyness="ATM", ltp=ltp, bid=ltp, ask=ltp,
        spread_pct=Decimal("1"), open_interest=oi, change_in_open_interest=None, volume=None,
        implied_volatility=iv, delta=None, theta=None, gamma=None, vega=None, liquidity_grade="excellent",
        decay_verdict=None, decay_viability_ratio=None, distance_to_support_pct=None, distance_to_resistance_pct=None,
    )


def _snapshot(
    *, audit_id: str = "audit-1", generated_at: datetime = GENERATED_AT, symbol: str = "RELIANCE",
    market_state: str = "LIVE_SNAPSHOT", spot: Decimal | None = Decimal("1300"), trend: str = "TRENDING_BULLISH",
    chain_iv: Decimal | None = Decimal("17.5"), total_call_oi: int | None = 100, decision: str = "WATCH",
    key_risks: list[str] | None = None, reference_strike: Decimal | None = Decimal("1300"),
    ce_requested: ContractAssessmentSnapshot | None = None,
) -> AnalysisSnapshot:
    return AnalysisSnapshot(
        identity=IdentitySection(audit_id=audit_id, symbol=symbol, instrument_key="NSE_EQ|X", generated_at=generated_at, market_state=market_state, analysis_version="1.0.0"),
        underlying=UnderlyingSection(spot=spot, day_change=None, day_change_pct=Decimal("1.0"), data_age_seconds=1.0, freshness_label="LIVE"),
        technical=TechnicalSection(trend=trend, ema_alignment="ASCENDING", vwap_position="ABOVE", rsi_value=Decimal("55"), atr_pct_of_price=Decimal("1.0"), regime_detail="trending"),
        futures=FuturesSection(futures_instrument_key=None, futures_ltp=None, futures_oi=None, futures_basis_pct=None, futures_interpretation=None),
        options=OptionsSection(
            expiry=(GENERATED_AT + timedelta(days=30)).date(), atm_strike=Decimal("1300"), total_call_oi=total_call_oi, total_put_oi=80,
            pcr_oi=Decimal("0.8"), atm_ce_iv=Decimal("18"), atm_pe_iv=Decimal("17"), chain_iv=chain_iv,
            ce_pe_skew=Decimal("1.0"), iv_trend="STABLE", oi_structure_detail="neutral", chain_reference=None,
        ),
        temporal=TemporalSection(), global_context=GlobalSection(inputs=[], verdict=None, detail=None), news=NewsSection(),
        evidence=EvidenceSection(rows=[], convergence="CONVERGENCE_BULLISH"),
        adversarial=AdversarialSection(
            bull_case=[], bear_case=[], contradictions=[], missing_data=[],
            key_risks=key_risks if key_risks is not None else [], opposite_case_is_equally_supported=False,
        ),
        quality=_quality(), candidates=[],
        levels=LevelsSection(levels=[]),
        decision=DecisionSection(final_bias="BULLISH", decision=decision, reasoning="test", candidate_selected=None, invalidation_level=Decimal("1290"), invalidation_description="test"),
        contracts=ContractObservationSection(reference_strike=reference_strike, requested_right=OptionRight.CE, ce_requested=ce_requested),
    )


def test_no_prior_snapshot_reports_has_prior_snapshot_false() -> None:
    report = build_snapshot_change_report(previous=None, current=_snapshot())
    assert report.has_prior_snapshot is False
    assert report.underlying == []
    assert report.notes == []


def test_valid_diff_reports_real_before_after_values() -> None:
    previous = _snapshot(audit_id="audit-1", generated_at=GENERATED_AT, spot=Decimal("1300"), trend="TRENDING_BULLISH", chain_iv=Decimal("17.5"), total_call_oi=100)
    current = _snapshot(audit_id="audit-2", generated_at=GENERATED_AT + timedelta(minutes=15), spot=Decimal("1320"), trend="TRENDING_BEARISH", chain_iv=Decimal("19.0"), total_call_oi=150)

    report = build_snapshot_change_report(previous=previous, current=current)

    assert report.has_prior_snapshot is True
    assert report.previous_audit_id == "audit-1"
    assert report.time_since_previous_seconds == 900.0

    spot_change = next(f for f in report.underlying if f.label == "Spot")
    assert spot_change.before == "1300" and spot_change.after == "1320" and spot_change.changed is True

    trend_change = next(f for f in report.underlying if f.label == "Trend")
    assert trend_change.changed is True

    iv_change = next(f for f in report.options_structure if f.label == "Chain IV")
    assert iv_change.before == "17.5" and iv_change.after == "19.0" and iv_change.changed is True

    oi_change = next(f for f in report.options_structure if f.label == "Total CE OI")
    assert oi_change.changed is True


def test_unchanged_fields_report_changed_false_not_omitted() -> None:
    previous = _snapshot(spot=Decimal("1300"), decision="WATCH")
    current = _snapshot(audit_id="audit-2", generated_at=GENERATED_AT + timedelta(minutes=5), spot=Decimal("1300"), decision="WATCH")

    report = build_snapshot_change_report(previous=previous, current=current)

    spot_change = next(f for f in report.underlying if f.label == "Spot")
    assert spot_change.changed is False
    decision_change = next(f for f in report.decision if f.label == "Decision")
    assert decision_change.changed is False


def test_unavailable_value_on_either_side_reports_changed_as_none_never_a_false_change() -> None:
    previous = _snapshot(chain_iv=None)
    current = _snapshot(audit_id="audit-2", generated_at=GENERATED_AT + timedelta(minutes=5), chain_iv=Decimal("18"))

    report = build_snapshot_change_report(previous=previous, current=current)

    iv_change = next(f for f in report.options_structure if f.label == "Chain IV")
    assert iv_change.before is None
    assert iv_change.after == "18"
    assert iv_change.changed is None  # never fabricated as True/False when one side is unavailable


def test_reference_strike_change_is_flagged_as_a_note_not_silently_compared() -> None:
    previous = _snapshot(reference_strike=Decimal("1300"))
    current = _snapshot(audit_id="audit-2", generated_at=GENERATED_AT + timedelta(minutes=5), reference_strike=Decimal("1320"))

    report = build_snapshot_change_report(previous=previous, current=current)

    assert any("reference strike changed" in n for n in report.notes)


def test_ce_requested_price_change_is_reported() -> None:
    previous = _snapshot(ce_requested=_ce(Decimal("30"), Decimal("18"), 80000))
    current = _snapshot(audit_id="audit-2", generated_at=GENERATED_AT + timedelta(minutes=5), ce_requested=_ce(Decimal("35"), Decimal("19"), 90000))

    report = build_snapshot_change_report(previous=previous, current=current)

    ltp_change = next(f for f in report.ce_requested if f.label == "CE LTP")
    assert ltp_change.before == "30" and ltp_change.after == "35" and ltp_change.changed is True


def test_newly_triggered_and_newly_resolved_risks_are_named() -> None:
    previous = _snapshot(key_risks=["theta decay accelerating", "low liquidity"])
    current = _snapshot(audit_id="audit-2", generated_at=GENERATED_AT + timedelta(minutes=5), key_risks=["theta decay accelerating", "IV crush risk"])

    report = build_snapshot_change_report(previous=previous, current=current)

    assert report.newly_triggered_risks == ["IV crush risk"]
    assert report.newly_resolved_risks == ["low liquidity"]


def test_mismatched_symbol_refuses_to_compare() -> None:
    previous = _snapshot(symbol="RELIANCE")
    current = _snapshot(audit_id="audit-2", symbol="TCS", generated_at=GENERATED_AT + timedelta(minutes=5))

    report = build_snapshot_change_report(previous=previous, current=current)

    assert report.has_prior_snapshot is False
    assert any("does not match" in n for n in report.notes)


def test_never_reports_change_when_nothing_differs_at_all() -> None:
    previous = _snapshot()
    current = _snapshot(audit_id="audit-2", generated_at=GENERATED_AT + timedelta(minutes=5))

    report = build_snapshot_change_report(previous=previous, current=current)

    assert all(f.changed is not True for f in report.underlying + report.options_structure + report.decision)
    assert report.newly_triggered_risks == []
    assert report.newly_resolved_risks == []
