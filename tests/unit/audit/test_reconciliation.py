from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.audit.models import (
    AdversarialSection,
    AnalysisSnapshot,
    CandidateSnapshot,
    CheckpointLabel,
    CheckpointStatus,
    DecayAttributionSnapshot,
    DecisionSection,
    EvidenceRowOutcome,
    EvidenceRowSnapshot,
    EvidenceSection,
    FuturesSection,
    GlobalSection,
    IdentitySection,
    LevelOutcomeState,
    LevelSnapshot,
    LevelsSection,
    NewsSection,
    OptionOutcome,
    OptionsSection,
    OutcomeCheckpoint,
    QualitySection,
    TechnicalSection,
    TemporalSection,
    ThesisStatus,
    UnderlyingOutcome,
    UnderlyingSection,
)
from app.domain.audit.reconciliation import (
    build_reconciliation,
    classify_level_outcome,
    compute_excursion,
    compute_journal_counters,
    evaluate_thesis,
    reconcile_adversarial,
    reconcile_decay,
    reconcile_evidence,
    track_invalidation,
)
from app.domain.market.models import OptionRight

GENERATED_AT = datetime(2026, 8, 24, 10, 0, tzinfo=UTC)


def _quality() -> QualitySection:
    return QualitySection(
        data_quality="STRONG", evidence_quality="MODERATE", decision_quality="MODERATE", setup_quality="STRONG",
        option_quality="STRONG", liquidity_quality="STRONG", risk_quality="STRONG", supporting_evidence_count=2,
        conflicting_evidence_count=0,
    )


def _candidate(strike: Decimal = Decimal("1300"), right: OptionRight = OptionRight.CE, ltp: Decimal = Decimal("30")) -> CandidateSnapshot:
    return CandidateSnapshot(
        strike=strike, right=right, ltp=ltp, bid=Decimal("29.5"), ask=Decimal("30.5"), spread_pct=Decimal("3"),
        open_interest=80000, implied_volatility=Decimal("18"), delta=Decimal("0.5"), theta=Decimal("-2.0"),
        gamma=Decimal("0.01"), vega=Decimal("1.0"), liquidity_grade="excellent", is_atm=True, strikes_from_atm=0,
        invalidation_condition="thesis invalidated if the underlying closes decisively below 1290",
    )


def _snapshot(
    *, bias: str = "BULLISH", decision: str = "WATCH", invalidation_level: Decimal | None = Decimal("1290"),
    spot: Decimal | None = Decimal("1300"), rows: list[EvidenceRowSnapshot] | None = None,
    convergence: str = "CONVERGENCE_BULLISH", levels: list[LevelSnapshot] | None = None,
    bull_case: list[str] | None = None, bear_case: list[str] | None = None, opposite_equally_supported: bool = False,
    candidate: CandidateSnapshot | None = None,
) -> AnalysisSnapshot:
    default_rows = rows if rows is not None else [
        EvidenceRowSnapshot(name="M15 trend", group="underlying_price_structure", direction="BULLISH", detail="ascending"),
        EvidenceRowSnapshot(name="VWAP", group="underlying_price_structure", direction="BULLISH", detail="above vwap"),
    ]
    return AnalysisSnapshot(
        identity=IdentitySection(audit_id="audit-1", symbol="RELIANCE", instrument_key="NSE_EQ|X", generated_at=GENERATED_AT, market_state="LIVE_SNAPSHOT", analysis_version="1.0.0"),
        underlying=UnderlyingSection(spot=spot, day_change=None, day_change_pct=None, data_age_seconds=1.0, freshness_label="LIVE"),
        technical=TechnicalSection(trend="TRENDING_BULLISH", ema_alignment="ASCENDING", vwap_position="ABOVE", rsi_value=Decimal("55"), atr_pct_of_price=Decimal("1.0"), regime_detail="trending"),
        futures=FuturesSection(futures_instrument_key=None, futures_ltp=None, futures_oi=None, futures_basis_pct=None, futures_interpretation=None),
        options=OptionsSection(
            expiry=(GENERATED_AT + timedelta(days=30)).date(), atm_strike=Decimal("1300"), total_call_oi=100, total_put_oi=80,
            pcr_oi=Decimal("0.8"), atm_ce_iv=Decimal("18"), atm_pe_iv=Decimal("17"), chain_iv=Decimal("17.5"),
            ce_pe_skew=Decimal("1.0"), iv_trend="STABLE", oi_structure_detail="neutral", chain_reference=None,
        ),
        temporal=TemporalSection(), global_context=GlobalSection(inputs=[], verdict=None, detail=None), news=NewsSection(),
        evidence=EvidenceSection(rows=default_rows, convergence=convergence),
        adversarial=AdversarialSection(
            bull_case=bull_case if bull_case is not None else ["M15 trend: ascending"],
            bear_case=bear_case if bear_case is not None else [],
            contradictions=[], missing_data=[], key_risks=[], opposite_case_is_equally_supported=opposite_equally_supported,
        ),
        quality=_quality(), candidates=[candidate] if candidate is not None else [],
        levels=LevelsSection(levels=levels if levels is not None else []),
        decision=DecisionSection(final_bias=bias, decision=decision, reasoning="test", candidate_selected=candidate, invalidation_level=invalidation_level, invalidation_description="thesis invalidated below 1290"),
    )


def _checkpoint(
    *, label: CheckpointLabel, spot: Decimal | None, status: CheckpointStatus = CheckpointStatus.RECORDED,
    convergence: str | None = "CONVERGENCE_BULLISH", rows: list[EvidenceRowSnapshot] | None = None,
    option_pct_change: Decimal | None = None, decay_confidence: str | None = None,
    bull_case: list[str] | None = None, opposite_equally_supported: bool = False,
) -> OutcomeCheckpoint:
    underlying = UnderlyingOutcome(spot=spot, absolute_change=(spot - Decimal("1300")) if spot is not None else None, pct_change=((spot - Decimal("1300")) / Decimal("1300") * 100) if spot is not None else None) if status == CheckpointStatus.RECORDED else None
    evidence = None
    if status == CheckpointStatus.RECORDED and convergence is not None:
        default_rows = rows if rows is not None else [
            EvidenceRowSnapshot(name="M15 trend", group="underlying_price_structure", direction="BULLISH", detail="still ascending"),
        ]
        evidence = EvidenceSection(rows=default_rows, convergence=convergence)
    adversarial = None
    if status == CheckpointStatus.RECORDED:
        adversarial = AdversarialSection(
            bull_case=bull_case if bull_case is not None else ["M15 trend: still ascending"], bear_case=[], contradictions=[],
            missing_data=[], key_risks=[], opposite_case_is_equally_supported=opposite_equally_supported,
        )
    option = None
    if status == CheckpointStatus.RECORDED and option_pct_change is not None:
        decay = None
        if decay_confidence is not None:
            decay = DecayAttributionSnapshot(
                observed_price_change=Decimal("1"), underlying_price_change=Decimal("5"), iv_change=Decimal("0"),
                delta_effect=Decimal("2.5"), gamma_effect=Decimal("0.1"), vega_effect=Decimal("0"), theta_effect=Decimal("-2.0"),
                residual=Decimal("0.4"), confidence=decay_confidence, detail="decomposed",
            )
        option = OptionOutcome(
            option_ltp=Decimal("30") * (1 + option_pct_change / 100), option_pct_change=option_pct_change,
            implied_volatility=Decimal("18"), iv_change=Decimal("0"), spread_pct=Decimal("3"), liquidity_grade="excellent",
            intrinsic_value=Decimal("0"), intrinsic_value_change=Decimal("0"), time_remaining_days=29, decay_attribution=decay,
        )
    offset_map = {CheckpointLabel.FIVE_MIN: 300.0, CheckpointLabel.FIFTEEN_MIN: 900.0, CheckpointLabel.THIRTY_MIN: 1800.0, CheckpointLabel.SIXTY_MIN: 3600.0, CheckpointLabel.EOD: None}
    return OutcomeCheckpoint(
        audit_id="audit-1", checkpoint_label=label, scheduled_offset_seconds=offset_map[label],
        captured_at=GENERATED_AT + timedelta(seconds=offset_map[label] or 20000), actual_elapsed_seconds=offset_map[label] or 20000,
        status=status, underlying=underlying, option=option, evidence=evidence, adversarial=adversarial,
    )


# -- track_invalidation -------------------------------------------------


def test_invalidation_none_when_bias_not_directional() -> None:
    result = track_invalidation(bias="NEUTRAL", invalidation_level=Decimal("1290"), checkpoints=[])
    assert result.reached is None


def test_invalidation_none_when_level_missing() -> None:
    result = track_invalidation(bias="BULLISH", invalidation_level=None, checkpoints=[_checkpoint(label=CheckpointLabel.FIVE_MIN, spot=Decimal("1280"))])
    assert result.reached is None


def test_invalidation_none_with_no_recorded_checkpoints() -> None:
    cp = _checkpoint(label=CheckpointLabel.FIVE_MIN, spot=None, status=CheckpointStatus.MARKET_CLOSED)
    result = track_invalidation(bias="BULLISH", invalidation_level=Decimal("1290"), checkpoints=[cp])
    assert result.reached is None


def test_invalidation_false_when_never_breached() -> None:
    checkpoints = [_checkpoint(label=CheckpointLabel.FIVE_MIN, spot=Decimal("1305")), _checkpoint(label=CheckpointLabel.FIFTEEN_MIN, spot=Decimal("1310"))]
    result = track_invalidation(bias="BULLISH", invalidation_level=Decimal("1290"), checkpoints=checkpoints)
    assert result.reached is False


def test_invalidation_true_bullish_when_spot_drops_below_level() -> None:
    checkpoints = [_checkpoint(label=CheckpointLabel.FIVE_MIN, spot=Decimal("1305")), _checkpoint(label=CheckpointLabel.FIFTEEN_MIN, spot=Decimal("1285"))]
    result = track_invalidation(bias="BULLISH", invalidation_level=Decimal("1290"), checkpoints=checkpoints)
    assert result.reached is True
    assert result.reached_at_checkpoint == "15m"


def test_invalidation_true_bearish_when_spot_rises_above_level() -> None:
    checkpoints = [_checkpoint(label=CheckpointLabel.FIVE_MIN, spot=Decimal("1295"))]
    result = track_invalidation(bias="BEARISH", invalidation_level=Decimal("1290"), checkpoints=checkpoints)
    assert result.reached is True


# -- compute_excursion ---------------------------------------------------


def test_excursion_empty_without_original_spot() -> None:
    assert compute_excursion(None, [_checkpoint(label=CheckpointLabel.FIVE_MIN, spot=Decimal("1305"))]) == []


def test_excursion_running_max_up_and_down() -> None:
    checkpoints = [
        _checkpoint(label=CheckpointLabel.FIVE_MIN, spot=Decimal("1310")),  # +0.77%
        _checkpoint(label=CheckpointLabel.FIFTEEN_MIN, spot=Decimal("1290")),  # -0.77%, new down extreme
        _checkpoint(label=CheckpointLabel.THIRTY_MIN, spot=Decimal("1305")),  # back up, up extreme unchanged
    ]
    samples = compute_excursion(Decimal("1300"), checkpoints)
    assert len(samples) == 3
    assert samples[0].up_move_pct > 0 and samples[0].down_move_pct == 0
    assert samples[1].down_move_pct > 0
    assert samples[2].up_move_pct == samples[0].up_move_pct  # running max preserved
    assert samples[2].down_move_pct == samples[1].down_move_pct  # running max preserved


def test_excursion_skips_non_recorded_checkpoints() -> None:
    checkpoints = [_checkpoint(label=CheckpointLabel.FIVE_MIN, spot=None, status=CheckpointStatus.MARKET_CLOSED)]
    assert compute_excursion(Decimal("1300"), checkpoints) == []


# -- classify_level_outcome ----------------------------------------------


def _support(strike: Decimal = Decimal("1290")) -> LevelSnapshot:
    return LevelSnapshot(kind="support", strike=strike, strength="strong", distance_from_spot_pct=Decimal("0.5"), stability_state="STABLE", evidence="evidence")


def test_level_outcome_insufficient_data_without_checkpoints() -> None:
    result = classify_level_outcome(_support(), checkpoints=[], test_proximity_pct=Decimal("1.0"))
    assert result.state == LevelOutcomeState.INSUFFICIENT_DATA


def test_level_outcome_not_tested_when_price_stays_far() -> None:
    checkpoints = [_checkpoint(label=CheckpointLabel.FIVE_MIN, spot=Decimal("1320"))]
    result = classify_level_outcome(_support(), checkpoints=checkpoints, test_proximity_pct=Decimal("1.0"))
    assert result.state == LevelOutcomeState.NOT_TESTED


def test_level_outcome_held_when_tested_then_moves_away() -> None:
    checkpoints = [_checkpoint(label=CheckpointLabel.FIVE_MIN, spot=Decimal("1291")), _checkpoint(label=CheckpointLabel.FIFTEEN_MIN, spot=Decimal("1310"))]
    result = classify_level_outcome(_support(), checkpoints=checkpoints, test_proximity_pct=Decimal("1.0"))
    assert result.state == LevelOutcomeState.HELD
    assert result.first_test_checkpoint == "5m"


def test_level_outcome_tested_when_still_near_at_latest() -> None:
    checkpoints = [_checkpoint(label=CheckpointLabel.FIVE_MIN, spot=Decimal("1291"))]
    result = classify_level_outcome(_support(), checkpoints=checkpoints, test_proximity_pct=Decimal("1.0"))
    assert result.state == LevelOutcomeState.TESTED


def test_level_outcome_broken_when_latest_is_beyond() -> None:
    checkpoints = [_checkpoint(label=CheckpointLabel.FIVE_MIN, spot=Decimal("1280"))]
    result = classify_level_outcome(_support(), checkpoints=checkpoints, test_proximity_pct=Decimal("1.0"))
    assert result.state == LevelOutcomeState.BROKEN


def test_level_outcome_failed_when_broken_then_reclaimed() -> None:
    checkpoints = [_checkpoint(label=CheckpointLabel.FIVE_MIN, spot=Decimal("1280")), _checkpoint(label=CheckpointLabel.FIFTEEN_MIN, spot=Decimal("1305"))]
    result = classify_level_outcome(_support(), checkpoints=checkpoints, test_proximity_pct=Decimal("1.0"))
    assert result.state == LevelOutcomeState.FAILED


# -- reconcile_evidence ----------------------------------------------------


def test_reconcile_evidence_insufficient_when_no_latest() -> None:
    original = EvidenceSection(rows=[EvidenceRowSnapshot(name="M15 trend", group="g", direction="BULLISH", detail="x")], convergence="CONVERGENCE_BULLISH")
    results = reconcile_evidence(original, None)
    assert results[0].outcome == EvidenceRowOutcome.INSUFFICIENT_DATA


def test_reconcile_evidence_confirmed_when_direction_unchanged() -> None:
    original = EvidenceSection(rows=[EvidenceRowSnapshot(name="M15 trend", group="g", direction="BULLISH", detail="x")], convergence="CONVERGENCE_BULLISH")
    latest = EvidenceSection(rows=[EvidenceRowSnapshot(name="M15 trend", group="g", direction="BULLISH", detail="y")], convergence="CONVERGENCE_BULLISH")
    results = reconcile_evidence(original, latest)
    assert results[0].outcome == EvidenceRowOutcome.CONFIRMED


def test_reconcile_evidence_invalidated_when_direction_flips() -> None:
    original = EvidenceSection(rows=[EvidenceRowSnapshot(name="M15 trend", group="g", direction="BULLISH", detail="x")], convergence="CONVERGENCE_BULLISH")
    latest = EvidenceSection(rows=[EvidenceRowSnapshot(name="M15 trend", group="g", direction="BEARISH", detail="y")], convergence="CONVERGENCE_BEARISH")
    results = reconcile_evidence(original, latest)
    assert results[0].outcome == EvidenceRowOutcome.INVALIDATED


def test_reconcile_evidence_insufficient_when_row_missing_in_latest() -> None:
    original = EvidenceSection(rows=[EvidenceRowSnapshot(name="M15 trend", group="g", direction="BULLISH", detail="x")], convergence="CONVERGENCE_BULLISH")
    latest = EvidenceSection(rows=[], convergence="INSUFFICIENT_EVIDENCE")
    results = reconcile_evidence(original, latest)
    assert results[0].outcome == EvidenceRowOutcome.INSUFFICIENT_DATA


def test_reconcile_evidence_unchanged_for_neutral_rows() -> None:
    original = EvidenceSection(rows=[EvidenceRowSnapshot(name="IV", group="g", direction="NEUTRAL", detail="x")], convergence="INSUFFICIENT_EVIDENCE")
    latest = EvidenceSection(rows=[EvidenceRowSnapshot(name="IV", group="g", direction="NEUTRAL", detail="y")], convergence="INSUFFICIENT_EVIDENCE")
    results = reconcile_evidence(original, latest)
    assert results[0].outcome == EvidenceRowOutcome.UNCHANGED


# -- reconcile_adversarial --------------------------------------------------


def test_reconcile_adversarial_none_when_no_latest_checkpoint() -> None:
    original = AdversarialSection(bull_case=["a"], bear_case=[], contradictions=[], missing_data=[], key_risks=[], opposite_case_is_equally_supported=True)
    result = reconcile_adversarial(original, None)
    assert result.bull_case_count_latest is None
    assert result.contradiction_resolved is None


def test_reconcile_adversarial_resolved_when_conflict_stops() -> None:
    original = AdversarialSection(bull_case=["a"], bear_case=["b"], contradictions=[], missing_data=[], key_risks=[], opposite_case_is_equally_supported=True)
    latest_cp = _checkpoint(label=CheckpointLabel.FIFTEEN_MIN, spot=Decimal("1310"), bull_case=["a", "c"], opposite_equally_supported=False)
    result = reconcile_adversarial(original, latest_cp)
    assert result.contradiction_resolved is True
    assert result.bull_case_count_latest == 2


def test_reconcile_adversarial_not_resolved_when_conflict_persists() -> None:
    original = AdversarialSection(bull_case=["a"], bear_case=["b"], contradictions=[], missing_data=[], key_risks=[], opposite_case_is_equally_supported=True)
    latest_cp = _checkpoint(label=CheckpointLabel.FIFTEEN_MIN, spot=Decimal("1310"), opposite_equally_supported=True)
    result = reconcile_adversarial(original, latest_cp)
    assert result.contradiction_resolved is False


def test_reconcile_adversarial_none_when_original_had_no_conflict() -> None:
    original = AdversarialSection(bull_case=["a"], bear_case=[], contradictions=[], missing_data=[], key_risks=[], opposite_case_is_equally_supported=False)
    latest_cp = _checkpoint(label=CheckpointLabel.FIFTEEN_MIN, spot=Decimal("1310"))
    result = reconcile_adversarial(original, latest_cp)
    assert result.contradiction_resolved is None


# -- reconcile_decay ---------------------------------------------------------


def test_reconcile_decay_maps_confidence_tiers() -> None:
    checkpoints = [
        _checkpoint(label=CheckpointLabel.FIVE_MIN, spot=Decimal("1305"), option_pct_change=Decimal("2"), decay_confidence="ATTRIBUTED"),
        _checkpoint(label=CheckpointLabel.FIFTEEN_MIN, spot=Decimal("1310"), option_pct_change=Decimal("3"), decay_confidence="UNRELIABLE"),
    ]
    entries = reconcile_decay(checkpoints)
    assert len(entries) == 2
    assert entries[0].verdict.value == "CONSISTENT"
    assert entries[1].verdict.value == "INCONSISTENT"


def test_reconcile_decay_skips_checkpoints_without_option_data() -> None:
    checkpoints = [_checkpoint(label=CheckpointLabel.FIVE_MIN, spot=Decimal("1305"))]
    assert reconcile_decay(checkpoints) == []


# -- evaluate_thesis ----------------------------------------------------


def test_evaluate_thesis_insufficient_data_with_no_checkpoints() -> None:
    snapshot = _snapshot(bias="BULLISH")
    invalidation = track_invalidation(bias="BULLISH", invalidation_level=Decimal("1290"), checkpoints=[])
    underlying_status, option_status, note = evaluate_thesis(snapshot, [], invalidation)
    assert underlying_status == ThesisStatus.INSUFFICIENT_DATA
    assert option_status is None and note is None


def test_evaluate_thesis_unresolved_for_no_trade_bias() -> None:
    snapshot = _snapshot(bias="NEUTRAL", decision="NO_TRADE", convergence="CONFLICT")
    checkpoints = [_checkpoint(label=CheckpointLabel.FIVE_MIN, spot=Decimal("1305"), convergence="CONFLICT")]
    invalidation = track_invalidation(bias="NEUTRAL", invalidation_level=None, checkpoints=checkpoints)
    underlying_status, option_status, _note = evaluate_thesis(snapshot, checkpoints, invalidation)
    assert underlying_status == ThesisStatus.UNRESOLVED
    assert option_status is None


def test_evaluate_thesis_invalidated_when_level_breached() -> None:
    snapshot = _snapshot(bias="BULLISH")
    checkpoints = [_checkpoint(label=CheckpointLabel.FIVE_MIN, spot=Decimal("1280"))]
    invalidation = track_invalidation(bias="BULLISH", invalidation_level=Decimal("1290"), checkpoints=checkpoints)
    underlying_status, _, _ = evaluate_thesis(snapshot, checkpoints, invalidation)
    assert underlying_status == ThesisStatus.INVALIDATED


def test_evaluate_thesis_confirmed_when_convergence_matches_bias() -> None:
    snapshot = _snapshot(bias="BULLISH")
    checkpoints = [_checkpoint(label=CheckpointLabel.FIVE_MIN, spot=Decimal("1305"), convergence="CONVERGENCE_BULLISH")]
    invalidation = track_invalidation(bias="BULLISH", invalidation_level=Decimal("1290"), checkpoints=checkpoints)
    underlying_status, _, _ = evaluate_thesis(snapshot, checkpoints, invalidation)
    assert underlying_status == ThesisStatus.CONFIRMED


def test_evaluate_thesis_mixed_when_convergence_conflicts() -> None:
    snapshot = _snapshot(bias="BULLISH")
    checkpoints = [_checkpoint(label=CheckpointLabel.FIVE_MIN, spot=Decimal("1305"), convergence="CONFLICT")]
    invalidation = track_invalidation(bias="BULLISH", invalidation_level=Decimal("1290"), checkpoints=checkpoints)
    underlying_status, _, _ = evaluate_thesis(snapshot, checkpoints, invalidation)
    assert underlying_status == ThesisStatus.MIXED


def test_evaluate_thesis_option_status_tracked_independently() -> None:
    snapshot = _snapshot(bias="BULLISH", candidate=_candidate())
    checkpoints = [_checkpoint(label=CheckpointLabel.FIVE_MIN, spot=Decimal("1305"), convergence="CONVERGENCE_BULLISH", option_pct_change=Decimal("-10"))]
    invalidation = track_invalidation(bias="BULLISH", invalidation_level=Decimal("1290"), checkpoints=checkpoints)
    underlying_status, option_status, note = evaluate_thesis(snapshot, checkpoints, invalidation)
    # Underlying direction correct, but the option LOST value -- exactly Phase 7's scenario.
    assert underlying_status == ThesisStatus.CONFIRMED
    assert option_status == ThesisStatus.INVALIDATED
    assert note is not None and "underlying direction was correct but the option lost value" in note


def test_evaluate_thesis_option_status_none_without_candidate() -> None:
    snapshot = _snapshot(bias="BULLISH", candidate=None)
    checkpoints = [_checkpoint(label=CheckpointLabel.FIVE_MIN, spot=Decimal("1305"), convergence="CONVERGENCE_BULLISH")]
    invalidation = track_invalidation(bias="BULLISH", invalidation_level=Decimal("1290"), checkpoints=checkpoints)
    _, option_status, _ = evaluate_thesis(snapshot, checkpoints, invalidation)
    assert option_status is None


# -- build_reconciliation / compute_journal_counters -------------------------


def test_build_reconciliation_is_final_only_with_eod() -> None:
    snapshot = _snapshot(bias="BULLISH", candidate=_candidate(), levels=[_support()])
    checkpoints = [_checkpoint(label=CheckpointLabel.FIVE_MIN, spot=Decimal("1305"), convergence="CONVERGENCE_BULLISH")]
    result_interim = build_reconciliation(snapshot, checkpoints, now=GENERATED_AT + timedelta(minutes=10), near_level_pct_threshold=Decimal("1.0"))
    assert result_interim.is_final is False

    checkpoints_with_eod = checkpoints + [_checkpoint(label=CheckpointLabel.EOD, spot=Decimal("1310"), convergence="CONVERGENCE_BULLISH")]
    result_final = build_reconciliation(snapshot, checkpoints_with_eod, now=GENERATED_AT + timedelta(hours=6), near_level_pct_threshold=Decimal("1.0"))
    assert result_final.is_final is True
    assert result_final.audit_id == snapshot.identity.audit_id


def test_journal_counters_are_observed_counts_only() -> None:
    from app.domain.audit.models import InvalidationTracking, ReconciliationResult

    analyses = [_snapshot(bias="BULLISH")]
    reconciliations = [
        ReconciliationResult(
            audit_id="audit-1", computed_at=GENERATED_AT, checkpoints_used=["5m"], is_final=True,
            underlying_thesis_status=ThesisStatus.CONFIRMED, option_thesis_status=None, option_vs_underlying_divergence_note=None,
            invalidation=InvalidationTracking(invalidation_level=None, reached=None, reached_at_checkpoint=None, reached_at=None),
            adversarial_reconciliation=None, detail="x",
        )
    ]
    counters = compute_journal_counters(analyses, reconciliations)
    assert counters.analyses_count == 1
    assert counters.outcomes_count == 1
    assert counters.confirmed_count == 1
    assert counters.unresolved_count == 0
    assert counters.invalidated_count == 0


def test_journal_counters_never_computes_a_rate() -> None:
    # Structural guarantee, not just a runtime assertion: `JournalCounters`
    # has no field that could be mistaken for a rate/percentage/accuracy.
    from app.domain.audit.models import JournalCounters

    assert set(JournalCounters.model_fields) == {"analyses_count", "outcomes_count", "unresolved_count", "invalidated_count", "confirmed_count"}
