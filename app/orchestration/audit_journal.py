"""Milestone G — orchestration layer for the post-analysis audit journal.

Two impure responsibilities live here (never in `app.domain.audit`, which
must stay pure per this codebase's dependency-direction rule): building an
`AnalysisSnapshot` from an already-computed `OptionsIntelligenceReport`
(a pure transform, but one that necessarily depends on the
orchestration-layer report type), and capturing outcome checkpoints (real
I/O — re-runs `analyze_symbol()` and reads the option-chain repository).

=== HOW A CHECKPOINT IS ACTUALLY CAPTURED ===
`capture_outcome_checkpoint()` re-runs the FULL, already-validated
`analyze_symbol()` pipeline at the real current wall-clock time (`now`) —
it does not try to rewind to exactly "generated_at + 15 minutes" if the
caller happens to invoke it late. `scheduled_offset_seconds` records the
checkpoint's target bucket; `actual_elapsed_seconds` always records the
REAL elapsed time. A late capture is recorded as late, never silently
relabeled as on-time — see `app.domain.audit.models` module docstring.
Re-running the full pipeline (rather than a bespoke lightweight fetch) is
deliberate: it reuses 100% of the already-validated evidence/adversarial/
decision computation instead of duplicating it, and it naturally persists
a fresh `OptionChainSnapshot` this module can then look back up for the
specific candidate leg being tracked.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import zip_longest

from app.data.providers.exceptions import ProviderError
from app.data.providers.upstox_provider import UpstoxProvider
from app.domain.audit.models import (
    CHECKPOINT_OFFSETS,
    CURRENT_ANALYSIS_VERSION,
    AdversarialSection,
    AnalysisSnapshot,
    AnomalySnapshot,
    CandidateSnapshot,
    CheckpointLabel,
    CheckpointStatus,
    ContractAssessmentSnapshot,
    ContractObservationSection,
    DecayAttributionSnapshot,
    DecisionSection,
    EvidenceRowSnapshot,
    EvidenceSection,
    FuturesSection,
    GlobalContextInputSnapshot,
    GlobalSection,
    IdentitySection,
    LevelSnapshot,
    LevelsSection,
    NewsItemSnapshot,
    NewsSection,
    OptionChainReference,
    OptionOutcome,
    OptionsSection,
    OutcomeCheckpoint,
    QualitySection,
    TechnicalSection,
    TemporalObservationSnapshot,
    TemporalSection,
    UnderlyingOutcome,
    UnderlyingSection,
    new_audit_id,
)
from app.domain.market.data_state import MarketDataState
from app.domain.market.models import OptionChainSnapshot, OptionQuote, OptionRight
from app.domain.market.trading_calendar import is_trading_day, next_trading_day
from app.domain.options.candidate_engine import OptionCandidate
from app.domain.options.contract_analysis import ContractAssessment, ContractComparison
from app.domain.options.decay_engine import attribute_decay
from app.domain.options.decay_viability import DecayViabilityAssessment
from app.domain.options.liquidity import assess_liquidity
from app.domain.options.temporal_evidence import compute_temporal_observation
from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy
from app.orchestration.options_intelligence_pipeline import (
    PipelineConfig,
    Repositories,
    analyze_symbol,
)
from app.orchestration.options_intelligence_report import OptionsIntelligenceReport
from app.utils.time import to_ist

# NSE's real cash/F&O close (15:30 IST) -- the same real, sourced FACT
# already documented in `app.domain.options.decay_viability`. EOD is
# anchored to this wall-clock instant on the analysis's own calendar day,
# not to a fixed duration from `generated_at`.
_NSE_CLOSE_HOUR_IST = 15
_NSE_CLOSE_MINUTE_IST = 30

# How far apart two option-chain snapshots may legitimately be for a
# checkpoint-vs-generation temporal comparison to still count as one
# "transition" -- generously wide (a full trading day) because a
# checkpoint can legitimately be captured hours after generation (the
# 60m/EOD buckets), unlike the tighter intraday windows
# `options_intelligence_pipeline.py` uses for its own temporal-evidence
# stage.
_MAX_CHECKPOINT_INTERVAL = timedelta(hours=16)


def eod_deadline(generated_at: datetime) -> datetime:
    """The real NSE market-close instant, in UTC, on `generated_at`'s own
    IST calendar day -- OR, if that calendar day is not an actual NSE
    trading day (weekend, or a listed holiday once `NSE_HOLIDAYS` is
    populated from a real source), the same 15:30 IST close on the next
    real trading day instead (Part 4A, fixing the limitation documented
    at the end of Sprint 7: a Sunday-generated snapshot must never treat
    Sunday 15:30 as a meaningful EOD). This only changes the deadline for
    a non-trading-day `generated_at`; every weekday-generated snapshot's
    deadline is unchanged (same-day 15:30 IST, exactly as before)."""
    ist_generated = to_ist(generated_at)
    session_date = ist_generated.date()
    if not is_trading_day(session_date):
        session_date = next_trading_day(session_date)
    ist_deadline = datetime(
        session_date.year, session_date.month, session_date.day,
        _NSE_CLOSE_HOUR_IST, _NSE_CLOSE_MINUTE_IST, tzinfo=ist_generated.tzinfo,
    )
    return ist_deadline.astimezone(UTC)


def due_checkpoints(generated_at: datetime, captured: set[CheckpointLabel], *, now: datetime) -> list[CheckpointLabel]:
    """Phase 2. Which checkpoints have NOT yet been captured but are now
    capturable, given the real current time `now`. Never returns a label
    whose scheduled instant is still in the future -- that would be
    look-ahead by construction.
    """
    due: list[CheckpointLabel] = []
    for label, offset in CHECKPOINT_OFFSETS.items():
        if label in captured:
            continue
        if now >= generated_at + offset:
            due.append(label)
    if CheckpointLabel.EOD not in captured and now >= eod_deadline(generated_at):
        due.append(CheckpointLabel.EOD)
    return due


def build_analysis_snapshot(
    report: OptionsIntelligenceReport,
    *,
    audit_id: str | None = None,
    analysis_version: str = CURRENT_ANALYSIS_VERSION,
    corrects_audit_id: str | None = None,
) -> AnalysisSnapshot:
    """Phase 1. A pure transform of an already-computed, already-validated
    `OptionsIntelligenceReport` into the immutable audit record. Computes
    NOTHING new -- every field here traces to a field the pipeline already
    produced.

    Raises `ValueError` for a failed analysis (`report.error is not
    None`) — there is no decision-worthy content to audit, and this is a
    caller ordering bug (the caller should check `report.error is None`
    first, exactly like every other consumer of this report), not a
    data-availability gap to silently paper over.
    """
    if report.error is not None:
        raise ValueError(f"cannot build an audit snapshot from a failed analysis: {report.error}")
    if report.decision is None:
        raise ValueError("cannot build an audit snapshot: report.decision is None")

    identity = IdentitySection(
        audit_id=audit_id or new_audit_id(), symbol=report.symbol, instrument_key=report.underlying_instrument_key,
        generated_at=report.generated_at, market_state=report.data_state.value, analysis_version=analysis_version,
        corrects_audit_id=corrects_audit_id,
    )

    underlying = UnderlyingSection(
        spot=report.spot, day_change=report.day_change, day_change_pct=report.day_change_pct,
        data_age_seconds=report.data_age_seconds,
        freshness_label=report.freshness_label.value if report.freshness_label is not None else report.market_status_label,
    )

    a = report.analysis
    technical = TechnicalSection(
        trend=report.regime.regime.value if report.regime is not None else "UNKNOWN",
        ema_alignment=a.ema_alignment.value if a is not None and a.ema_alignment is not None else None,
        vwap_position=a.price_vs_vwap.value if a is not None and a.price_vs_vwap is not None else None,
        rsi_value=report.rsi.value if report.rsi is not None else None,
        atr_pct_of_price=report.regime.atr_pct_of_price if report.regime is not None else None,
        regime_detail=report.regime.detail if report.regime is not None else "",
    )

    futures = FuturesSection(
        futures_instrument_key=report.futures_instrument_key, futures_ltp=report.futures_ltp,
        futures_oi=report.futures_oi, futures_basis_pct=report.futures_basis_pct,
        futures_interpretation=report.futures_oi_observation.conventional_reading if report.futures_oi_observation is not None else None,
    )

    expiry_date = date_from_iso(report.expiry)
    oi_row = next((r for r in report.matrix.rows if r.name == "Call/Put OI structure"), None) if report.matrix is not None else None
    options = OptionsSection(
        expiry=expiry_date, atm_strike=report.atm_strike, total_call_oi=report.total_call_oi, total_put_oi=report.total_put_oi,
        pcr_oi=report.pcr_oi,
        atm_ce_iv=report.iv_summary.atm_ce_iv if report.iv_summary is not None else None,
        atm_pe_iv=report.iv_summary.atm_pe_iv if report.iv_summary is not None else None,
        chain_iv=report.iv_summary.chain_iv if report.iv_summary is not None else None,
        ce_pe_skew=report.iv_summary.ce_pe_skew if report.iv_summary is not None else None,
        iv_trend=report.iv_trend.value if report.iv_trend is not None else None,
        oi_structure_detail=oi_row.detail if oi_row is not None else None,
        chain_reference=(
            OptionChainReference(underlying=report.underlying_instrument_key, expiry=expiry_date, data_timestamp=report.generated_at)
            if report.underlying_instrument_key is not None and expiry_date is not None
            else None
        ),
    )

    temporal = TemporalSection(
        observations=[
            TemporalObservationSnapshot(
                strike=t.strike, right=t.right, interval_seconds=t.interval.total_seconds(),
                price_change_pct=t.price_change_pct, oi_change_pct=t.oi_change_pct, volume_change=t.volume_change,
                iv_change=t.iv_change, conventional_reading=t.price_oi.conventional_reading, quality=t.quality.value,
            )
            for t in report.temporal_observations
        ],
        anomalies=[
            AnomalySnapshot(metric_name=an.metric_name, current_value=an.current_value, deviation_ratio=an.deviation_ratio, verdict=an.verdict.value, detail=an.detail)
            for an in report.anomalies
        ],
    )

    levels = LevelsSection(
        levels=[
            LevelSnapshot(
                kind=ls.level.kind.value, strike=ls.level.strike, strength=ls.level.strength.value,
                distance_from_spot_pct=ls.level.distance_from_spot_pct, stability_state=ls.state.value,
                evidence=f"{ls.level.evidence}; stability: {ls.detail}",
            )
            for ls in report.level_stability
        ]
    )

    global_context = GlobalSection(
        inputs=(
            [
                GlobalContextInputSnapshot(label=gi.label, day_change_pct=gi.day_change_pct, contributes_to_verdict=gi.contributes_to_verdict, detail=gi.detail)
                for gi in report.global_context.inputs
            ]
            if report.global_context is not None
            else []
        ),
        verdict=report.global_context.verdict.value if report.global_context is not None else None,
        detail=report.global_context.detail if report.global_context is not None else None,
    )

    news = NewsSection(
        provider="upstox",  # the pipeline always attempts this real, first-party source now
        items=[
            NewsItemSnapshot(
                source=i.source, published_at=i.published_at, title=i.title, summary=i.summary, url=i.url,
                relevance=i.relevance.value, direction=i.direction.value, evidence_quality=i.evidence_quality.value,
            )
            for i in report.news_items
        ],
        fetch_error=report.news_fetch_error,
        unavailable_reason=report.news_fetch_error,
    )

    evidence = EvidenceSection(
        rows=(
            [EvidenceRowSnapshot(name=r.name, group=r.group.value, direction=r.direction.value, detail=r.detail) for r in report.matrix.rows]
            if report.matrix is not None
            else []
        ),
        convergence=report.matrix.overall_convergence().value if report.matrix is not None else "INSUFFICIENT_EVIDENCE",
    )

    aa = report.adversarial_analysis
    adversarial = AdversarialSection(
        bull_case=list(aa.bull_case) if aa is not None else [], bear_case=list(aa.bear_case) if aa is not None else [],
        contradictions=list(aa.contradictions) if aa is not None else [], missing_data=list(aa.missing_data) if aa is not None else [],
        key_risks=list(aa.key_risks) if aa is not None else [],
        opposite_case_is_equally_supported=aa.opposite_case_is_equally_supported if aa is not None else False,
    )

    qt = report.quality_tiers
    qa = report.decision.assessment
    quality = QualitySection(
        data_quality=qt.data_quality.value if qt is not None else qa.data_quality.value,
        evidence_quality=qt.evidence_quality.value if qt is not None else "INSUFFICIENT",
        decision_quality=qt.decision_quality.value if qt is not None else "INSUFFICIENT",
        setup_quality=qa.setup_quality.value, option_quality=qa.option_quality.value,
        liquidity_quality=qa.liquidity_quality.value, risk_quality=qa.risk_quality.value,
        supporting_evidence_count=qa.supporting_evidence_count, conflicting_evidence_count=qa.conflicting_evidence_count,
    )

    # `report.decay_viability` is appended in lockstep with `report.candidates`
    # (same loop, same order) in `options_intelligence_pipeline.py`'s "decay
    # viability" stage; `zip_longest` guards against the lengths ever
    # diverging rather than assuming it.
    candidate_snapshots = [
        _candidate_snapshot(c, dv) for c, dv in zip_longest(report.candidates, report.decay_viability) if c is not None
    ]

    decision = DecisionSection(
        final_bias=report.decision.assessment.market_bias.value, decision=report.decision.decision.value,
        reasoning=report.decision.reasoning, candidate_selected=candidate_snapshots[0] if candidate_snapshots else None,
        invalidation_level=report.invalidation_level,
        invalidation_description=report.candidates[0].invalidation_condition if report.candidates else None,
    )

    contracts = _build_contract_observation_section(report)

    return AnalysisSnapshot(
        identity=identity, underlying=underlying, technical=technical, futures=futures, options=options, temporal=temporal,
        levels=levels, global_context=global_context, news=news, evidence=evidence, adversarial=adversarial, quality=quality,
        candidates=candidate_snapshots, decision=decision, contracts=contracts,
    )


def _contract_assessment_snapshot(assessment: ContractAssessment) -> ContractAssessmentSnapshot:
    dv = assessment.decay_viability
    return ContractAssessmentSnapshot(
        strike=assessment.strike, right=assessment.right, moneyness=assessment.moneyness.value if assessment.moneyness is not None else None,
        ltp=assessment.ltp, bid=assessment.bid, ask=assessment.ask, spread_pct=assessment.spread_pct,
        open_interest=assessment.open_interest, change_in_open_interest=assessment.change_in_open_interest, volume=assessment.volume,
        implied_volatility=assessment.implied_volatility, delta=assessment.delta, theta=assessment.theta,
        gamma=assessment.gamma, vega=assessment.vega, liquidity_grade=assessment.liquidity.grade.value,
        decay_verdict=dv.verdict.value if dv is not None else None, decay_viability_ratio=dv.viability_ratio if dv is not None else None,
        distance_to_support_pct=assessment.distance_to_support_pct, distance_to_resistance_pct=assessment.distance_to_resistance_pct,
    )


def _side_snapshots(comparison: ContractComparison | None) -> tuple[ContractAssessmentSnapshot | None, list[ContractAssessmentSnapshot]]:
    if comparison is None:
        return None, []
    requested = _contract_assessment_snapshot(comparison.requested) if comparison.requested is not None else None
    alternatives = [_contract_assessment_snapshot(a) for a in comparison.alternatives]
    return requested, alternatives


def _build_contract_observation_section(report: OptionsIntelligenceReport) -> ContractObservationSection:
    """Sprint 5, Phase 8. Prefers `report.direction_comparison` (Sprint 4)
    since it always carries BOTH CE and PE `ContractComparison` objects
    around the same reference strike -- exactly "requested contract,
    opposite contract, nearby CE alternatives, nearby PE alternatives" in
    one already-computed, already-symmetric structure. Falls back to
    `report.requested_contract` (Sprint 2, one side only) only if
    `direction_comparison` was not built this run (e.g. no reference
    strike was available at all) but a specific contract was still
    requested and resolved.
    """
    requested_right = report.requested_contract.requested_right if report.requested_contract is not None else None

    dc = report.direction_comparison
    if dc is not None:
        ce_requested, ce_alternatives = _side_snapshots(dc.ce_alternatives)
        pe_requested, pe_alternatives = _side_snapshots(dc.pe_alternatives)
        return ContractObservationSection(
            reference_strike=dc.reference_strike, requested_right=requested_right,
            ce_requested=ce_requested, ce_alternatives=ce_alternatives,
            pe_requested=pe_requested, pe_alternatives=pe_alternatives,
        )

    rc = report.requested_contract
    if rc is not None:
        requested, alternatives = _side_snapshots(rc)
        if rc.requested_right == OptionRight.CE:
            return ContractObservationSection(
                reference_strike=rc.requested_strike, requested_right=requested_right,
                ce_requested=requested, ce_alternatives=alternatives,
            )
        return ContractObservationSection(
            reference_strike=rc.requested_strike, requested_right=requested_right,
            pe_requested=requested, pe_alternatives=alternatives,
        )

    return ContractObservationSection()


def date_from_iso(value: str | None) -> date | None:
    if value is None:
        return None
    return date.fromisoformat(value)


def _candidate_snapshot(candidate: OptionCandidate, decay_viability: DecayViabilityAssessment | None) -> CandidateSnapshot:
    dv = decay_viability
    return CandidateSnapshot(
        strike=candidate.strike, right=candidate.right, ltp=candidate.ltp, bid=candidate.bid, ask=candidate.ask,
        spread_pct=candidate.spread_pct, open_interest=candidate.open_interest, implied_volatility=candidate.implied_volatility,
        delta=candidate.delta, theta=candidate.theta, gamma=candidate.gamma, vega=candidate.vega,
        liquidity_grade=candidate.liquidity.grade.value, is_atm=candidate.is_atm, strikes_from_atm=candidate.strikes_from_atm,
        supporting_evidence=list(candidate.supporting_evidence), contradicting_evidence=list(candidate.contradicting_evidence),
        invalidation_condition=candidate.invalidation_condition, risks=list(candidate.risks),
        decay_verdict=dv.verdict.value if dv is not None else None,
        decay_viability_ratio=dv.viability_ratio if dv is not None else None,
        required_underlying_move_pct=dv.required_underlying_move_pct if dv is not None else None,
        contractual_expiry_breakeven=dv.contractual_expiry_breakeven if dv is not None else None,
        expected_move_over_horizon_pct=dv.expected_move_over_horizon_pct if dv is not None else None,
    )


def _find_leg(snapshot: OptionChainSnapshot, *, strike: Decimal, right: OptionRight) -> OptionQuote | None:
    for leg in snapshot.legs:
        if leg.strike == strike and leg.right == right:
            return leg
    return None


async def _capture_leg_outcome(
    *, instrument_key: str, expiry: date, strike: Decimal, right: OptionRight,
    original_ltp: Decimal | None, original_iv: Decimal | None, original_spot: Decimal | None,
    original_delta: Decimal | None, original_gamma: Decimal | None, original_vega: Decimal | None, original_theta: Decimal | None,
    generated_at: datetime, checkpoint_report: OptionsIntelligenceReport, now: datetime,
    repositories: Repositories, config: PipelineConfig,
) -> OptionOutcome | None:
    """Phases 6/8 — the SAME real-outcome-capture logic regardless of which
    leg is being tracked (the bias-gated `candidate_selected`, or the
    reference-strike CE/PE legs from `AnalysisSnapshot.contracts`). Every
    caller supplies its own strike/right/original-greeks explicitly rather
    than this function reaching into a specific snapshot shape -- this is
    what lets `capture_outcome_checkpoint()` track up to three independent
    legs from one checkpoint chain fetch without duplicating the lookup.
    Returns `None` (never a fabricated/estimated value) when the leg
    cannot be genuinely found in the real checkpoint chain.
    """
    checkpoint_chain = await repositories.option_chains.latest(underlying=instrument_key, expiry=expiry, as_of=now)
    if checkpoint_chain is None:
        return None
    checkpoint_leg = _find_leg(checkpoint_chain, strike=strike, right=right)
    if checkpoint_leg is None:
        return None

    liquidity = assess_liquidity(checkpoint_leg, as_of=now, max_quote_age=config.max_option_quote_age)
    spread_pct = liquidity.spread_fraction * Decimal(100) if liquidity.spread_fraction is not None else None
    liquidity_grade = liquidity.grade.value

    option_pct_change = (
        (checkpoint_leg.last_price - original_ltp) / original_ltp * Decimal(100)
        if checkpoint_leg.last_price is not None and original_ltp is not None and original_ltp != 0
        else None
    )
    iv_change = (
        checkpoint_leg.implied_volatility - original_iv
        if checkpoint_leg.implied_volatility is not None and original_iv is not None
        else None
    )

    checkpoint_spot = checkpoint_report.spot
    intrinsic_now = _intrinsic_value(right.value, checkpoint_spot, strike)
    intrinsic_original = _intrinsic_value(right.value, original_spot, strike)
    intrinsic_change = intrinsic_now - intrinsic_original if intrinsic_now is not None and intrinsic_original is not None else None

    time_remaining_days = (expiry - now.date()).days

    decay_attribution = await _attribute_decay_between(
        instrument_key=instrument_key, expiry=expiry, strike=strike, right=right, generated_at=generated_at,
        original_spot=original_spot, original_delta=original_delta, original_gamma=original_gamma,
        original_vega=original_vega, original_theta=original_theta,
        checkpoint_chain=checkpoint_chain, checkpoint_leg=checkpoint_leg, checkpoint_spot=checkpoint_spot,
        repositories=repositories, config=config,
    )

    return OptionOutcome(
        option_ltp=checkpoint_leg.last_price, option_pct_change=option_pct_change, implied_volatility=checkpoint_leg.implied_volatility,
        iv_change=iv_change, spread_pct=spread_pct, liquidity_grade=liquidity_grade, intrinsic_value=intrinsic_now,
        intrinsic_value_change=intrinsic_change, time_remaining_days=time_remaining_days, decay_attribution=decay_attribution,
    )


async def _capture_option_outcome(
    snapshot: AnalysisSnapshot, *, checkpoint_report: OptionsIntelligenceReport, now: datetime,
    repositories: Repositories, config: PipelineConfig,
) -> OptionOutcome | None:
    candidate = snapshot.decision.candidate_selected
    if candidate is None or snapshot.options.expiry is None or snapshot.identity.instrument_key is None:
        return None
    return await _capture_leg_outcome(
        instrument_key=snapshot.identity.instrument_key, expiry=snapshot.options.expiry,
        strike=candidate.strike, right=candidate.right, original_ltp=candidate.ltp, original_iv=candidate.implied_volatility,
        original_spot=snapshot.underlying.spot, original_delta=candidate.delta, original_gamma=candidate.gamma,
        original_vega=candidate.vega, original_theta=candidate.theta, generated_at=snapshot.identity.generated_at,
        checkpoint_report=checkpoint_report, now=now, repositories=repositories, config=config,
    )


async def _capture_reference_option_outcome(
    snapshot: AnalysisSnapshot, *, right: OptionRight, checkpoint_report: OptionsIntelligenceReport, now: datetime,
    repositories: Repositories, config: PipelineConfig,
) -> OptionOutcome | None:
    """Phase 8 -- tracks whichever of `contracts.ce_requested`/
    `pe_requested` matches `right`, independent of `candidate_selected`.
    `None` when no reference-strike leg on that side was resolved at
    analysis time (e.g. that strike wasn't in the real chain, or no
    reference strike existed at all)."""
    if snapshot.options.expiry is None or snapshot.identity.instrument_key is None:
        return None
    leg = snapshot.contracts.ce_requested if right == OptionRight.CE else snapshot.contracts.pe_requested
    if leg is None:
        return None
    return await _capture_leg_outcome(
        instrument_key=snapshot.identity.instrument_key, expiry=snapshot.options.expiry,
        strike=leg.strike, right=leg.right, original_ltp=leg.ltp, original_iv=leg.implied_volatility,
        original_spot=snapshot.underlying.spot, original_delta=leg.delta, original_gamma=leg.gamma,
        original_vega=leg.vega, original_theta=leg.theta, generated_at=snapshot.identity.generated_at,
        checkpoint_report=checkpoint_report, now=now, repositories=repositories, config=config,
    )


def _intrinsic_value(right: str, spot: Decimal | None, strike: Decimal) -> Decimal | None:
    if spot is None:
        return None
    if right == "CE":
        return max(spot - strike, Decimal(0))
    return max(strike - spot, Decimal(0))


async def _attribute_decay_between(
    *, instrument_key: str, expiry: date, strike: Decimal, right: OptionRight, generated_at: datetime,
    original_spot: Decimal | None, original_delta: Decimal | None, original_gamma: Decimal | None,
    original_vega: Decimal | None, original_theta: Decimal | None,
    checkpoint_chain: OptionChainSnapshot, checkpoint_leg: OptionQuote,
    checkpoint_spot: Decimal | None, repositories: Repositories, config: PipelineConfig,
) -> DecayAttributionSnapshot | None:
    """Phase 8. Reuses `decay_engine.attribute_decay()` directly -- see
    `app.domain.audit.reconciliation.reconcile_decay()` for how the
    resulting confidence tier becomes a CONSISTENT/PARTIALLY_CONSISTENT/
    INCONSISTENT verdict. Parametrized by explicit strike/right/greeks
    (Phase 8) so the same logic serves the bias-gated candidate leg and
    the reference-strike CE/PE legs alike.
    """
    original_chain = await repositories.option_chains.latest(underlying=instrument_key, expiry=expiry, as_of=generated_at)
    if original_chain is None:
        return None

    try:
        observation = compute_temporal_observation(
            earlier=original_chain, later=checkpoint_chain, strike=strike, right=right,
            price_flat_threshold=config.option_price_flat_threshold,
            min_oi_for_high_quality=config.temporal_min_oi_for_high_quality,
            min_volume_for_high_quality=config.temporal_min_volume_for_high_quality,
            max_reasonable_interval=_MAX_CHECKPOINT_INTERVAL,
        )
    except ValueError:
        return None

    attribution = attribute_decay(
        observation, underlying_price_t0=original_spot, underlying_price_t1=checkpoint_spot,
        delta_t0=original_delta, gamma_t0=original_gamma, vega_t0=original_vega, theta_t0=original_theta,
        attributed_max_residual_fraction=config.decay_attributed_max_residual_fraction,
        unreliable_min_residual_fraction=config.decay_unreliable_min_residual_fraction,
    )
    return DecayAttributionSnapshot(
        observed_price_change=attribution.observed_price_change, underlying_price_change=attribution.underlying_price_change,
        iv_change=attribution.iv_change, delta_effect=attribution.delta_effect, gamma_effect=attribution.gamma_effect,
        vega_effect=attribution.vega_effect, theta_effect=attribution.theta_effect, residual=attribution.residual,
        confidence=attribution.confidence.value, detail=attribution.detail,
    )


async def capture_outcome_checkpoint(
    label: CheckpointLabel, snapshot: AnalysisSnapshot, *,
    now: datetime, provider: UpstoxProvider, instrument_master: Sequence[dict[str, object]], strategy: EMAVWAPAlignmentStrategy,
    repositories: Repositories, config: PipelineConfig, mcx_instrument_master: Sequence[dict[str, object]] | None = None,
) -> OutcomeCheckpoint:
    """Phases 2/3. Raises `ValueError` if called before the checkpoint is
    actually due -- a caller ordering bug (use `due_checkpoints()` first),
    never silently tolerated (that would risk a fabricated early
    checkpoint)."""
    generated_at = snapshot.identity.generated_at
    scheduled_offset = CHECKPOINT_OFFSETS.get(label)
    deadline = generated_at + scheduled_offset if scheduled_offset is not None else eod_deadline(generated_at)
    if now < deadline:
        raise ValueError(f"checkpoint {label.value} is not due yet: now={now.isoformat()} deadline={deadline.isoformat()}")

    actual_elapsed_seconds = (now - generated_at).total_seconds()
    scheduled_offset_seconds = scheduled_offset.total_seconds() if scheduled_offset is not None else None

    try:
        checkpoint_report = await analyze_symbol(
            snapshot.identity.symbol, provider=provider, instrument_master=instrument_master, strategy=strategy,
            repositories=repositories, as_of=now, config=config, mcx_instrument_master=mcx_instrument_master,
        )
    except ProviderError as exc:
        return OutcomeCheckpoint(
            audit_id=snapshot.identity.audit_id, checkpoint_label=label, scheduled_offset_seconds=scheduled_offset_seconds,
            captured_at=now, actual_elapsed_seconds=actual_elapsed_seconds, status=CheckpointStatus.INSUFFICIENT_DATA,
            detail=f"provider error at checkpoint capture: {exc}",
        )

    if checkpoint_report.error is not None:
        return OutcomeCheckpoint(
            audit_id=snapshot.identity.audit_id, checkpoint_label=label, scheduled_offset_seconds=scheduled_offset_seconds,
            captured_at=now, actual_elapsed_seconds=actual_elapsed_seconds, status=CheckpointStatus.INSUFFICIENT_DATA,
            detail=checkpoint_report.error,
        )

    if checkpoint_report.data_state == MarketDataState.MARKET_CLOSED_LATEST_DATA:
        return OutcomeCheckpoint(
            audit_id=snapshot.identity.audit_id, checkpoint_label=label, scheduled_offset_seconds=scheduled_offset_seconds,
            captured_at=now, actual_elapsed_seconds=actual_elapsed_seconds, status=CheckpointStatus.MARKET_CLOSED,
            detail="market was closed at checkpoint capture time -- no outcome fabricated",
        )

    original_spot = snapshot.underlying.spot
    underlying_outcome = None
    if checkpoint_report.spot is not None:
        abs_change = checkpoint_report.spot - original_spot if original_spot is not None else None
        pct_change = (abs_change / original_spot * Decimal(100)) if abs_change is not None and original_spot else None
        underlying_outcome = UnderlyingOutcome(spot=checkpoint_report.spot, absolute_change=abs_change, pct_change=pct_change)

    option_outcome = await _capture_option_outcome(snapshot, checkpoint_report=checkpoint_report, now=now, repositories=repositories, config=config)
    ce_option_outcome = await _capture_reference_option_outcome(
        snapshot, right=OptionRight.CE, checkpoint_report=checkpoint_report, now=now, repositories=repositories, config=config
    )
    pe_option_outcome = await _capture_reference_option_outcome(
        snapshot, right=OptionRight.PE, checkpoint_report=checkpoint_report, now=now, repositories=repositories, config=config
    )

    evidence_section = None
    if checkpoint_report.matrix is not None:
        evidence_section = EvidenceSection(
            rows=[EvidenceRowSnapshot(name=r.name, group=r.group.value, direction=r.direction.value, detail=r.detail) for r in checkpoint_report.matrix.rows],
            convergence=checkpoint_report.matrix.overall_convergence().value,
        )
    adversarial_section = None
    if checkpoint_report.adversarial_analysis is not None:
        aa = checkpoint_report.adversarial_analysis
        adversarial_section = AdversarialSection(
            bull_case=list(aa.bull_case), bear_case=list(aa.bear_case), contradictions=list(aa.contradictions),
            missing_data=list(aa.missing_data), key_risks=list(aa.key_risks),
            opposite_case_is_equally_supported=aa.opposite_case_is_equally_supported,
        )

    return OutcomeCheckpoint(
        audit_id=snapshot.identity.audit_id, checkpoint_label=label, scheduled_offset_seconds=scheduled_offset_seconds,
        captured_at=now, actual_elapsed_seconds=actual_elapsed_seconds, status=CheckpointStatus.RECORDED,
        underlying=underlying_outcome, option=option_outcome, ce_option=ce_option_outcome, pe_option=pe_option_outcome,
        evidence=evidence_section, adversarial=adversarial_section,
        detail=f"captured via a fresh analyze_symbol() re-run at as_of={now.isoformat()}",
    )
