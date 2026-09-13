"""Sprint 3 -- Research OUTCOME TRACKING: pure, deterministic unit tests
over the checkpoint-timing arithmetic, progression classification, and
excursion/level computations -- no network, no real analysis re-run
(that's `tests/integration/orchestration/test_research_outcome.py`).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from app.domain.audit.research_models import (
    ResearchCheckpointLabel,
    ResearchObservation,
    ResearchOutcomeCheckpoint,
    ResearchOutcomeStatus,
    ResearchProgression,
)
from app.orchestration.daily_research import (
    build_research_observation,
    build_research_thesis,
    rank_candidates,
)
from app.orchestration.dashboard_service import AnalyzeResponse
from app.orchestration.research_outcome import (
    _breakeven_reached,
    _classify_progression,
    _level_broken,
    _real_excursion_pct,
    build_research_history,
    build_research_outcome_detail,
    due_research_checkpoints,
    summarize_research_outcome,
    target_trading_session_date,
)
from app.orchestration.visual_data import (
    CandlePoint,
    ContractAssessmentView,
    ContractCaseView,
    DevelopmentNarrativeView,
    DirectionalPathsView,
    DirectionComparisonVisual,
    FreshnessVisual,
    FuturesVisual,
    LevelView,
    NewsVisual,
    OptionChainVisual,
    PriceChartData,
    SupportResistanceVisual,
    VisualData,
)
from app.persistence.jsonl_file import JsonlResearchOutcomeRepository

_FRIDAY = date(2026, 8, 28)
_MONDAY = date(2026, 8, 31)


def _contract(*, strike: str, right: str) -> ContractAssessmentView:
    return ContractAssessmentView(
        strike=Decimal(strike), right=right, moneyness=None, ltp=Decimal("10.0"), bid=None, ask=None, spread_pct=Decimal("1.0"),
        open_interest=100000, change_in_open_interest=1000, volume=50000, implied_volatility=Decimal("20"),
        delta=Decimal("0.5"), gamma=None, theta=Decimal("-1.0"), vega=None, liquidity_grade="excellent",
        distance_to_support_pct=None, distance_to_resistance_pct=None, decay_verdict="DECAY_FAVORABLE",
        decay_viability_ratio=None, required_underlying_move=None, required_underlying_move_pct=Decimal("1.0"),
        contractual_expiry_breakeven=Decimal("1020"), expected_move_over_horizon=None, expected_move_over_horizon_pct=None,
        scenarios=[], scope_note=None, structural_quality="ACCEPTABLE", data_quality_notes=[],
    )


def _visual(*, dc: DirectionComparisonVisual) -> VisualData:
    return VisualData(
        price_chart=PriceChartData(timeframe="M15", insufficient_history=False, detail="", candles=[]),
        option_chain=OptionChainVisual(), requested_contract=None, direction_comparison=dc,
        news=NewsVisual(items=[]), evidence=[], convergence=None, adversarial=None, quality=None,
        futures=FuturesVisual(instrument_key=None, ltp=None, open_interest=None, basis_pct=None, oi_interpretation=None),
        global_context=None, support_resistance=SupportResistanceVisual(support=[], resistance=[]),
        term_structure=None, freshness=FreshnessVisual(market_state="LIVE_SNAPSHOT", freshness_label=None, data_age_seconds=1.0, generated_at=datetime(2026, 8, 28, tzinfo=UTC)),
    )


# ============================================================
# target_trading_session_date / due_research_checkpoints
# ============================================================


def test_target_trading_session_date_plus_one_from_friday_is_monday() -> None:
    assert target_trading_session_date(_FRIDAY, 1) == _MONDAY


def test_target_trading_session_date_plus_three_from_friday_skips_the_weekend() -> None:
    # Mon, Tue, Wed -- the weekend never counts as an elapsed session.
    assert target_trading_session_date(_FRIDAY, 3) == date(2026, 9, 2)


def test_target_trading_session_date_plus_five_from_friday() -> None:
    assert target_trading_session_date(_FRIDAY, 5) == date(2026, 9, 4)


def _obs(generated_at: datetime) -> ResearchObservation:
    return ResearchObservation(
        run_id="run1", audit_id="audit1", generated_at=generated_at, symbol="X", direction="BULLISH",
        selected_right="CE", selected_strike="1000", early_stage_state="EARLY_DIRECTIONAL_BUILD",
        research_confidence="MODERATE", actionability="WATCH", spot_at_observation="1000.0",
        contractual_expiry_breakeven="1020.0", nearest_level_kind="resistance", nearest_level_value="1050.0",
        market_context="NEUTRAL", participation_note="none", coverage_classification="HIGH", thesis="test thesis",
    )


def test_no_checkpoint_due_before_the_first_target_session() -> None:
    observation = _obs(datetime(2026, 8, 28, 10, 0, tzinfo=UTC))
    due = due_research_checkpoints(observation, set(), as_of=datetime(2026, 8, 28, 12, 0, tzinfo=UTC))
    assert due == []


def test_plus_one_session_becomes_due_once_monday_is_reached() -> None:
    observation = _obs(datetime(2026, 8, 28, 10, 0, tzinfo=UTC))
    due = due_research_checkpoints(observation, set(), as_of=datetime(2026, 8, 31, 6, 0, tzinfo=UTC))
    assert due == [ResearchCheckpointLabel.PLUS_1_SESSION]


def test_already_captured_checkpoint_is_never_due_again() -> None:
    observation = _obs(datetime(2026, 8, 28, 10, 0, tzinfo=UTC))
    due = due_research_checkpoints(
        observation, {ResearchCheckpointLabel.PLUS_1_SESSION}, as_of=datetime(2026, 8, 31, 6, 0, tzinfo=UTC),
    )
    assert due == []


def test_a_future_target_session_never_reads_as_due_no_look_ahead() -> None:
    """The core no-look-ahead requirement: as_of BEFORE the +5-session
    target date must never mark that checkpoint due."""
    observation = _obs(datetime(2026, 8, 28, 10, 0, tzinfo=UTC))
    due = due_research_checkpoints(observation, set(), as_of=datetime(2026, 9, 3, 6, 0, tzinfo=UTC))  # one session short of +5
    assert ResearchCheckpointLabel.PLUS_5_SESSIONS not in due


# ============================================================
# Progression classification -- deterministic, rule-based, never a score.
# ============================================================


def test_progression_early_directional_build_to_developing_momentum_is_follow_through() -> None:
    assert _classify_progression("EARLY_DIRECTIONAL_BUILD", "DEVELOPING_MOMENTUM") == ResearchProgression.FOLLOW_THROUGH_OBSERVED


def test_progression_developing_momentum_to_breakout_confirmation_is_follow_through() -> None:
    assert _classify_progression("DEVELOPING_MOMENTUM", "BREAKOUT_CONFIRMATION") == ResearchProgression.FOLLOW_THROUGH_OBSERVED


def test_progression_breakout_confirmation_holding_can_still_progress_to_extended() -> None:
    assert _classify_progression("BREAKOUT_CONFIRMATION", "EXTENDED") == ResearchProgression.FOLLOW_THROUGH_OBSERVED


def test_progression_early_directional_build_to_range_bound_is_no_follow_through() -> None:
    assert _classify_progression("EARLY_DIRECTIONAL_BUILD", "RANGE_BOUND") == ResearchProgression.NO_FOLLOW_THROUGH


def test_progression_unchanged_state_is_no_follow_through() -> None:
    assert _classify_progression("EARLY_DIRECTIONAL_BUILD", "EARLY_DIRECTIONAL_BUILD") == ResearchProgression.NO_FOLLOW_THROUGH


def test_progression_to_false_breakout_risk_is_failed_setup() -> None:
    assert _classify_progression("EARLY_DIRECTIONAL_BUILD", "FALSE_BREAKOUT_RISK") == ResearchProgression.FAILED_SETUP
    assert _classify_progression("BREAKOUT_CONFIRMATION", "FALSE_BREAKOUT_RISK") == ResearchProgression.FAILED_SETUP


def test_progression_unknown_when_next_state_could_not_be_determined() -> None:
    assert _classify_progression("EARLY_DIRECTIONAL_BUILD", None) == ResearchProgression.UNKNOWN


def test_progression_unknown_when_next_state_is_insufficient_data() -> None:
    assert _classify_progression("EARLY_DIRECTIONAL_BUILD", "INSUFFICIENT_DATA") == ResearchProgression.UNKNOWN


# ============================================================
# Real excursion (MFE/MAE) from already-fetched M15 candles.
# ============================================================


def _candle(ts: datetime, *, high: str, low: str) -> CandlePoint:
    return CandlePoint(timestamp=ts, open=Decimal(low), high=Decimal(high), low=Decimal(low), close=Decimal(low), volume=1000)


def test_excursion_none_when_history_does_not_cover_the_full_window() -> None:
    obs_time = datetime(2026, 8, 28, 6, 0, tzinfo=UTC)
    candles = [_candle(datetime(2026, 8, 28, 7, 0, tzinfo=UTC), high="1010", low="995")]  # starts AFTER obs_time
    favorable, adverse = _real_excursion_pct(
        list(candles), observation_generated_at=obs_time, observation_spot=Decimal("1000"), direction="BULLISH",
    )
    assert favorable is None
    assert adverse is None


def test_excursion_real_values_when_window_fully_covered_bullish() -> None:
    obs_time = datetime(2026, 8, 28, 6, 0, tzinfo=UTC)
    candles = [
        _candle(datetime(2026, 8, 28, 5, 0, tzinfo=UTC), high="1001", low="999"),  # before observation -- covers the start
        _candle(datetime(2026, 8, 28, 7, 0, tzinfo=UTC), high="1050", low="990"),
        _candle(datetime(2026, 8, 28, 8, 0, tzinfo=UTC), high="1020", low="985"),
    ]
    favorable, adverse = _real_excursion_pct(
        list(candles), observation_generated_at=obs_time, observation_spot=Decimal("1000"), direction="BULLISH",
    )
    assert favorable == Decimal("5")  # (1050-1000)/1000*100
    assert adverse == Decimal("1.5")  # (1000-985)/1000*100


def test_excursion_real_values_when_window_fully_covered_bearish() -> None:
    obs_time = datetime(2026, 8, 28, 6, 0, tzinfo=UTC)
    candles = [
        _candle(datetime(2026, 8, 28, 5, 0, tzinfo=UTC), high="1001", low="999"),
        _candle(datetime(2026, 8, 28, 7, 0, tzinfo=UTC), high="1050", low="940"),
    ]
    favorable, adverse = _real_excursion_pct(
        list(candles), observation_generated_at=obs_time, observation_spot=Decimal("1000"), direction="BEARISH",
    )
    assert favorable == Decimal("6")  # (1000-940)/1000*100
    assert adverse == Decimal("5")  # (1050-1000)/1000*100


# ============================================================
# Swing-level / breakeven reach -- factual comparisons, never "profit".
# ============================================================


def test_swing_level_broken_bullish_resistance() -> None:
    assert _level_broken(kind="resistance", value="1050", direction="BULLISH", window_high=Decimal("1060"), window_low=Decimal("990")) is True
    assert _level_broken(kind="resistance", value="1050", direction="BULLISH", window_high=Decimal("1040"), window_low=Decimal("990")) is False


def test_swing_level_broken_none_when_no_real_level_was_recorded() -> None:
    assert _level_broken(kind=None, value=None, direction="BULLISH", window_high=Decimal("1060"), window_low=Decimal("990")) is None


def test_breakeven_reached_call_option() -> None:
    assert _breakeven_reached(breakeven="1020", right="CE", window_high=Decimal("1025"), window_low=Decimal("990")) is True
    assert _breakeven_reached(breakeven="1020", right="CE", window_high=Decimal("1010"), window_low=Decimal("990")) is False


def test_breakeven_reached_put_option() -> None:
    assert _breakeven_reached(breakeven="980", right="PE", window_high=Decimal("1010"), window_low=Decimal("975")) is True
    assert _breakeven_reached(breakeven="980", right="PE", window_high=Decimal("1010"), window_low=Decimal("990")) is False


# ============================================================
# build_research_observation -- reuses ResearchThesisView fields verbatim,
# and the resulting record is genuinely immutable.
# ============================================================


def _build_response() -> AnalyzeResponse:
    contract = _contract(strike="1000", right="CE")
    dc = DirectionComparisonVisual(
        reference_strike=Decimal("1000"), verdict="BULLISH_SIDE_BETTER_SUPPORTED", verdict_detail="test",
        ce_assessment=contract, pe_assessment=None, ce_alternatives=[], pe_alternatives=[],
        ce_decay=None, pe_decay=None, ce_case=ContractCaseView(), pe_case=ContractCaseView(),
        ce_hedge_context="", pe_hedge_context="", paths=DirectionalPathsView(spot=Decimal("1000.0")),
        bullish_supporting_groups=2, bearish_supporting_groups=0, conflicting_groups=0,
        preferred_direction="BULLISH", preferred_contract=contract, no_defensible_direction=False,
    )
    return AnalyzeResponse(
        query="X", parsed_symbol="X", parsed_strike=None, parsed_right=None, parsed_expiry_hint=None,
        has_specific_contract=False, parse_warnings=[], symbol="X", market_state="LIVE_SNAPSHOT",
        decision="WATCH", error=None, visual=_visual(dc=dc), watch_next=[], latency_seconds=0.01,
        audit_id="audit-X", generated_at=datetime(2026, 8, 28, 10, 0, tzinfo=UTC),
    )


def test_build_research_observation_reuses_thesis_fields_verbatim() -> None:
    shortlist, _ = rank_candidates({"X": _build_response()})
    candidate = shortlist[0]
    thesis = build_research_thesis(candidate)

    observation = build_research_observation(candidate, thesis, run_id="run1", coverage_classification="HIGH")

    assert observation.symbol == "X"
    assert observation.direction == "BULLISH"
    assert observation.early_stage_state == thesis.early_stage_state
    assert observation.research_confidence == thesis.research_confidence
    assert observation.actionability == thesis.actionability
    assert observation.spot_at_observation == thesis.current_spot
    assert observation.contractual_expiry_breakeven == thesis.contractual_expiry_breakeven
    assert observation.market_context == thesis.market_context
    assert observation.participation_note == thesis.participation_note
    assert observation.thesis == thesis.thesis
    assert observation.coverage_classification == "HIGH"
    assert observation.run_id == "run1"
    assert observation.audit_id == "audit-X"


def test_research_observation_is_genuinely_immutable() -> None:
    """The core no-look-ahead-leakage-backward requirement: a
    `ResearchObservation` is a frozen model -- attempting to modify it
    after the fact must raise, never silently succeed."""
    import pydantic

    shortlist, _ = rank_candidates({"X": _build_response()})
    observation = build_research_observation(shortlist[0], build_research_thesis(shortlist[0]), run_id="run1", coverage_classification="HIGH")

    try:
        observation.early_stage_state = "EXTENDED"  # type: ignore[misc]
        raised = False
    except pydantic.ValidationError:
        raised = True
    assert raised, "ResearchObservation must be frozen -- a later checkpoint must never be able to edit the original"


def _level(*, kind: str, strike: str) -> LevelView:
    return LevelView(
        kind=kind, strike=Decimal(strike), strength="MODERATE", stability="STABLE",
        distance_from_spot_pct=Decimal("1.0"), distance_pct=Decimal("1.0"),
        evidence="OI concentration", stability_detail="stable across snapshots",
    )


def test_build_research_observation_wires_a_genuine_invalidation_level_for_pre_breakout_compression() -> None:
    """Final 95% sprint (Section 6) -- the LIVE (contract-based) path,
    not just the price-only replay path: a PRE_BREAKOUT_COMPRESSION
    candidate's real OI-structural SUPPORT (BULLISH thesis's own floor)
    must populate `invalidation_level_kind`/`invalidation_level_value`,
    genuinely separate from the RESISTANCE `nearest_level_kind`/
    `nearest_level_value` above it (confirmation-relevant)."""
    response = _build_response()
    assert response.visual is not None
    response = response.model_copy(update={
        "visual": response.visual.model_copy(update={
            "development": DevelopmentNarrativeView(
                pattern="PRE_BREAKOUT_COMPRESSION", what_is_developing="compressing", why_it_matters="x",
                what_is_missing="a held break", confirm_if="holds beyond resistance", invalidate_if="support gives way",
                freshness_note="quote=current",
            ),
            "support_resistance": SupportResistanceVisual(
                support=[_level(kind="support", strike="980")], resistance=[_level(kind="resistance", strike="1030")],
            ),
        }),
    })
    shortlist, _ = rank_candidates({"X": response})
    candidate = shortlist[0]
    thesis = build_research_thesis(candidate)
    assert thesis.developing_pattern == "PRE_BREAKOUT_COMPRESSION"

    observation = build_research_observation(candidate, thesis, run_id="run1", coverage_classification="HIGH")

    assert observation.nearest_level_kind == "resistance"
    assert observation.nearest_level_value == "1030"
    assert observation.invalidation_level_kind == "support"
    assert observation.invalidation_level_value == "980"


def test_build_research_observation_leaves_invalidation_level_none_for_other_patterns() -> None:
    response = _build_response()
    assert response.visual is not None
    response = response.model_copy(update={
        "visual": response.visual.model_copy(update={
            "development": DevelopmentNarrativeView(
                pattern="RELATIVE_STRENGTH", what_is_developing="x", why_it_matters="x",
                what_is_missing="x", confirm_if="x", invalidate_if="x", freshness_note="quote=current",
            ),
            "support_resistance": SupportResistanceVisual(
                support=[_level(kind="support", strike="980")], resistance=[_level(kind="resistance", strike="1030")],
            ),
        }),
    })
    shortlist, _ = rank_candidates({"X": response})
    candidate = shortlist[0]
    thesis = build_research_thesis(candidate)
    assert thesis.developing_pattern == "RELATIVE_STRENGTH"

    observation = build_research_observation(candidate, thesis, run_id="run1", coverage_classification="HIGH")

    assert observation.invalidation_level_kind is None
    assert observation.invalidation_level_value is None


def test_old_persisted_observation_without_sprint4_fields_remains_readable() -> None:
    """Sprint 5, Objective 13.11 -- an observation JSON written before
    Sprint 4's `structural_context`/`participation_depth`/
    `relative_strength`/`pre_breakout_signal` fields existed must still
    deserialize (they default to `None`, same backward-compat-default
    pattern as every other additive field on this record)."""
    import json

    old_shape = {
        "observation_id": "obs-old", "run_id": "run1", "audit_id": "audit1",
        "generated_at": "2026-08-28T10:00:00Z", "symbol": "X", "direction": "BULLISH",
        "selected_right": "CE", "selected_strike": "1000", "early_stage_state": "EARLY_DIRECTIONAL_BUILD",
        "research_confidence": "MODERATE", "actionability": "WATCH", "spot_at_observation": "1000.0",
        "contractual_expiry_breakeven": "1020.0", "nearest_level_kind": "resistance", "nearest_level_value": "1050.0",
        "market_context": "NEUTRAL", "participation_note": "none", "coverage_classification": "HIGH", "thesis": "test thesis",
    }
    observation = ResearchObservation.model_validate_json(json.dumps(old_shape))
    assert observation.structural_context is None
    assert observation.participation_depth is None
    assert observation.relative_strength is None
    assert observation.pre_breakout_signal is None


# ============================================================
# Sprint 5, Objective 1/2/3 -- summarize_research_outcome()
# ============================================================


def _cp(
    *, label: ResearchCheckpointLabel, progression: ResearchProgression, captured_at: datetime,
    next_state: str | None = None, observation_id: str = "obs1",
) -> ResearchOutcomeCheckpoint:
    return ResearchOutcomeCheckpoint(
        observation_id=observation_id, checkpoint_label=label, target_trading_session_date=captured_at.date(),
        captured_at=captured_at, data_state="LIVE_SNAPSHOT", spot_at_checkpoint="1010.0",
        move_pct_from_observation="1.0", max_favorable_move_pct=None, max_adverse_move_pct=None,
        swing_level_broken=None, breakeven_reached=None, became_extended=None,
        next_observed_early_stage_state=next_state, progression=progression,
    )


def test_outcome_summary_pending_when_nothing_is_due_yet() -> None:
    observation = _obs(datetime(2026, 8, 28, 10, 0, tzinfo=UTC))
    summary = summarize_research_outcome(observation, [], as_of=datetime(2026, 8, 28, 12, 0, tzinfo=UTC))
    assert summary.outcome_status == ResearchOutcomeStatus.PENDING
    assert summary.earliest_follow_through_session is None
    assert summary.latest_checkpoint_state is None
    assert summary.explanation == "Outcome remains pending; no checkpoint is due yet."


def test_outcome_summary_pending_when_due_but_not_yet_captured() -> None:
    observation = _obs(datetime(2026, 8, 28, 10, 0, tzinfo=UTC))
    summary = summarize_research_outcome(observation, [], as_of=datetime(2026, 8, 31, 6, 0, tzinfo=UTC))
    assert summary.outcome_status == ResearchOutcomeStatus.PENDING
    assert "+1" in summary.explanation
    assert "due but has not yet been captured" in summary.explanation


def test_outcome_summary_plus_one_follow_through() -> None:
    observation = _obs(datetime(2026, 8, 28, 10, 0, tzinfo=UTC))
    cp = _cp(label=ResearchCheckpointLabel.PLUS_1_SESSION, progression=ResearchProgression.FOLLOW_THROUGH_OBSERVED, captured_at=datetime(2026, 8, 31, 10, 0, tzinfo=UTC))
    summary = summarize_research_outcome(observation, [cp], as_of=datetime(2026, 8, 31, 10, 0, tzinfo=UTC))
    assert summary.outcome_status == ResearchOutcomeStatus.FOLLOW_THROUGH_OBSERVED
    assert summary.earliest_follow_through_session == "+1"
    assert summary.explanation == "Follow-through observed by +1 sessions after the researched bullish setup."


def test_outcome_summary_plus_three_follow_through() -> None:
    observation = _obs(datetime(2026, 8, 28, 10, 0, tzinfo=UTC))
    cp = _cp(label=ResearchCheckpointLabel.PLUS_3_SESSIONS, progression=ResearchProgression.FOLLOW_THROUGH_OBSERVED, captured_at=datetime(2026, 9, 2, 10, 0, tzinfo=UTC))
    summary = summarize_research_outcome(observation, [cp], as_of=datetime(2026, 9, 2, 10, 0, tzinfo=UTC))
    assert summary.outcome_status == ResearchOutcomeStatus.FOLLOW_THROUGH_OBSERVED
    assert summary.earliest_follow_through_session == "+3"
    assert summary.explanation == "Follow-through observed by +3 sessions after the researched bullish setup."


def test_outcome_summary_plus_five_follow_through_bearish_wording() -> None:
    observation = _obs(datetime(2026, 8, 28, 10, 0, tzinfo=UTC))
    bearish = observation.model_copy(update={"direction": "BEARISH"})
    cp = _cp(label=ResearchCheckpointLabel.PLUS_5_SESSIONS, progression=ResearchProgression.FOLLOW_THROUGH_OBSERVED, captured_at=datetime(2026, 9, 4, 10, 0, tzinfo=UTC))
    summary = summarize_research_outcome(bearish, [cp], as_of=datetime(2026, 9, 4, 10, 0, tzinfo=UTC))
    assert summary.earliest_follow_through_session == "+5"
    assert summary.explanation == "Follow-through observed by +5 sessions after the researched bearish setup."


def test_outcome_summary_earliest_follow_through_selected_among_multiple_checkpoints() -> None:
    """+1 shows no follow-through yet, but +3 and +5 both do -- the
    EARLIEST qualifying checkpoint (+3) must be reported, never the
    latest."""
    observation = _obs(datetime(2026, 8, 28, 10, 0, tzinfo=UTC))
    checkpoints = [
        _cp(label=ResearchCheckpointLabel.PLUS_1_SESSION, progression=ResearchProgression.NO_FOLLOW_THROUGH, captured_at=datetime(2026, 8, 31, 10, 0, tzinfo=UTC)),
        _cp(label=ResearchCheckpointLabel.PLUS_3_SESSIONS, progression=ResearchProgression.FOLLOW_THROUGH_OBSERVED, captured_at=datetime(2026, 9, 2, 10, 0, tzinfo=UTC)),
        _cp(label=ResearchCheckpointLabel.PLUS_5_SESSIONS, progression=ResearchProgression.FOLLOW_THROUGH_OBSERVED, captured_at=datetime(2026, 9, 4, 10, 0, tzinfo=UTC)),
    ]
    summary = summarize_research_outcome(observation, checkpoints, as_of=datetime(2026, 9, 4, 10, 0, tzinfo=UTC))
    assert summary.outcome_status == ResearchOutcomeStatus.FOLLOW_THROUGH_OBSERVED
    assert summary.earliest_follow_through_session == "+3"


def test_outcome_summary_failed_setup() -> None:
    observation = _obs(datetime(2026, 8, 28, 10, 0, tzinfo=UTC))
    cp = _cp(label=ResearchCheckpointLabel.PLUS_1_SESSION, progression=ResearchProgression.FAILED_SETUP, next_state="FALSE_BREAKOUT_RISK", captured_at=datetime(2026, 8, 31, 10, 0, tzinfo=UTC))
    summary = summarize_research_outcome(observation, [cp], as_of=datetime(2026, 8, 31, 10, 0, tzinfo=UTC))
    assert summary.outcome_status == ResearchOutcomeStatus.FAILED_SETUP
    assert summary.earliest_follow_through_session is None
    assert summary.explanation == "Setup failed: subsequent observation entered FALSE_BREAKOUT_RISK."


def test_outcome_summary_follow_through_takes_priority_over_an_earlier_failed_checkpoint() -> None:
    """A real reversal: +1 objectively failed, but +3 objectively shows
    follow-through -- the overall status must reflect the real, later,
    more-progressed evidence, never freeze on the earlier failure."""
    observation = _obs(datetime(2026, 8, 28, 10, 0, tzinfo=UTC))
    checkpoints = [
        _cp(label=ResearchCheckpointLabel.PLUS_1_SESSION, progression=ResearchProgression.FAILED_SETUP, next_state="FALSE_BREAKOUT_RISK", captured_at=datetime(2026, 8, 31, 10, 0, tzinfo=UTC)),
        _cp(label=ResearchCheckpointLabel.PLUS_3_SESSIONS, progression=ResearchProgression.FOLLOW_THROUGH_OBSERVED, captured_at=datetime(2026, 9, 2, 10, 0, tzinfo=UTC)),
    ]
    summary = summarize_research_outcome(observation, checkpoints, as_of=datetime(2026, 9, 2, 10, 0, tzinfo=UTC))
    assert summary.outcome_status == ResearchOutcomeStatus.FOLLOW_THROUGH_OBSERVED
    assert summary.earliest_follow_through_session == "+3"


def test_outcome_summary_insufficient_outcome_data_when_every_checkpoint_is_unknown() -> None:
    observation = _obs(datetime(2026, 8, 28, 10, 0, tzinfo=UTC))
    cp = _cp(label=ResearchCheckpointLabel.PLUS_1_SESSION, progression=ResearchProgression.UNKNOWN, captured_at=datetime(2026, 8, 31, 10, 0, tzinfo=UTC))
    summary = summarize_research_outcome(observation, [cp], as_of=datetime(2026, 8, 31, 10, 0, tzinfo=UTC))
    assert summary.outcome_status == ResearchOutcomeStatus.INSUFFICIENT_OUTCOME_DATA
    assert summary.explanation == "Outcome cannot be determined because required historical data was unavailable."


def test_outcome_summary_no_follow_through_default_case() -> None:
    observation = _obs(datetime(2026, 8, 28, 10, 0, tzinfo=UTC))
    checkpoints = [
        _cp(label=ResearchCheckpointLabel.PLUS_1_SESSION, progression=ResearchProgression.NO_FOLLOW_THROUGH, captured_at=datetime(2026, 8, 31, 10, 0, tzinfo=UTC)),
        _cp(label=ResearchCheckpointLabel.PLUS_5_SESSIONS, progression=ResearchProgression.NO_FOLLOW_THROUGH, captured_at=datetime(2026, 9, 4, 10, 0, tzinfo=UTC)),
    ]
    summary = summarize_research_outcome(observation, checkpoints, as_of=datetime(2026, 9, 4, 10, 0, tzinfo=UTC))
    assert summary.outcome_status == ResearchOutcomeStatus.NO_FOLLOW_THROUGH
    assert summary.explanation == "No follow-through observed through the +5 session checkpoint."


def test_outcome_summary_latest_checkpoint_state_reflects_most_recently_captured() -> None:
    observation = _obs(datetime(2026, 8, 28, 10, 0, tzinfo=UTC))
    checkpoints = [
        _cp(label=ResearchCheckpointLabel.PLUS_1_SESSION, progression=ResearchProgression.NO_FOLLOW_THROUGH, next_state="RANGE_BOUND", captured_at=datetime(2026, 8, 31, 10, 0, tzinfo=UTC)),
        _cp(label=ResearchCheckpointLabel.PLUS_3_SESSIONS, progression=ResearchProgression.NO_FOLLOW_THROUGH, next_state="EARLY_DIRECTIONAL_BUILD", captured_at=datetime(2026, 9, 2, 10, 0, tzinfo=UTC)),
    ]
    summary = summarize_research_outcome(observation, checkpoints, as_of=datetime(2026, 9, 2, 10, 0, tzinfo=UTC))
    assert summary.latest_checkpoint_state == "EARLY_DIRECTIONAL_BUILD"


def test_outcome_summary_never_uses_forbidden_predictive_language() -> None:
    """Every explanation this function can produce must stay factual --
    never a probability/win/loss/profit claim."""
    forbidden = ("probability", "win rate", "expected return", "profit", "guaranteed", "winner", "loser", "confidence score")
    observation = _obs(datetime(2026, 8, 28, 10, 0, tzinfo=UTC))
    scenarios = [
        ([], datetime(2026, 8, 28, 12, 0, tzinfo=UTC)),
        ([_cp(label=ResearchCheckpointLabel.PLUS_1_SESSION, progression=ResearchProgression.FOLLOW_THROUGH_OBSERVED, captured_at=datetime(2026, 8, 31, 10, 0, tzinfo=UTC))], datetime(2026, 8, 31, 10, 0, tzinfo=UTC)),
        ([_cp(label=ResearchCheckpointLabel.PLUS_1_SESSION, progression=ResearchProgression.FAILED_SETUP, next_state="FALSE_BREAKOUT_RISK", captured_at=datetime(2026, 8, 31, 10, 0, tzinfo=UTC))], datetime(2026, 8, 31, 10, 0, tzinfo=UTC)),
        ([_cp(label=ResearchCheckpointLabel.PLUS_1_SESSION, progression=ResearchProgression.UNKNOWN, captured_at=datetime(2026, 8, 31, 10, 0, tzinfo=UTC))], datetime(2026, 8, 31, 10, 0, tzinfo=UTC)),
        ([_cp(label=ResearchCheckpointLabel.PLUS_1_SESSION, progression=ResearchProgression.NO_FOLLOW_THROUGH, captured_at=datetime(2026, 8, 31, 10, 0, tzinfo=UTC))], datetime(2026, 8, 31, 10, 0, tzinfo=UTC)),
    ]
    for checkpoints, as_of in scenarios:
        summary = summarize_research_outcome(observation, checkpoints, as_of=as_of)
        lowered = summary.explanation.lower()
        for word in forbidden:
            assert word not in lowered, f"{word!r} leaked into: {summary.explanation!r}"


# ============================================================
# Sprint 5, Objective 4/12 -- research history/outcome-detail views.
# ============================================================


def test_research_history_filters_by_symbol_direction_and_outcome_status(tmp_path: Path) -> None:
    repo = JsonlResearchOutcomeRepository(tmp_path / "outcomes")
    obs_a = _obs(datetime(2026, 8, 28, 10, 0, tzinfo=UTC)).model_copy(update={"observation_id": "obs-a", "symbol": "A", "direction": "BULLISH"})
    obs_b = _obs(datetime(2026, 8, 28, 11, 0, tzinfo=UTC)).model_copy(update={"observation_id": "obs-b", "symbol": "B", "direction": "BEARISH"})
    asyncio.run(repo.save_observation(obs_a))
    asyncio.run(repo.save_observation(obs_b))
    as_of = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)  # nothing due yet -> both PENDING

    all_history = asyncio.run(build_research_history(repo, as_of=as_of))
    assert all_history.matched_count == 2

    by_symbol = asyncio.run(build_research_history(repo, as_of=as_of, symbol="a"))  # lower-case, must still match
    assert [e.observation.symbol for e in by_symbol.entries] == ["A"]

    by_direction = asyncio.run(build_research_history(repo, as_of=as_of, direction="bearish"))
    assert [e.observation.symbol for e in by_direction.entries] == ["B"]

    by_status = asyncio.run(build_research_history(repo, as_of=as_of, outcome_status="pending"))
    assert by_status.matched_count == 2

    by_missing_status = asyncio.run(build_research_history(repo, as_of=as_of, outcome_status="failed_setup"))
    assert by_missing_status.matched_count == 0


def test_research_history_filters_by_date(tmp_path: Path) -> None:
    repo = JsonlResearchOutcomeRepository(tmp_path / "outcomes")
    obs_a = _obs(datetime(2026, 8, 28, 10, 0, tzinfo=UTC)).model_copy(update={"observation_id": "obs-a", "symbol": "A"})
    obs_b = _obs(datetime(2026, 8, 31, 10, 0, tzinfo=UTC)).model_copy(update={"observation_id": "obs-b", "symbol": "B"})
    asyncio.run(repo.save_observation(obs_a))
    asyncio.run(repo.save_observation(obs_b))
    as_of = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)

    result = asyncio.run(build_research_history(repo, as_of=as_of, day=date(2026, 8, 28)))
    assert [e.observation.symbol for e in result.entries] == ["A"]


def test_research_outcome_detail_returns_none_for_unknown_observation_id(tmp_path: Path) -> None:
    repo = JsonlResearchOutcomeRepository(tmp_path / "outcomes")
    result = asyncio.run(build_research_outcome_detail(repo, "does-not-exist", as_of=datetime(2026, 8, 28, tzinfo=UTC)))
    assert result is None


def test_research_outcome_detail_returns_observation_checkpoints_and_summary(tmp_path: Path) -> None:
    repo = JsonlResearchOutcomeRepository(tmp_path / "outcomes")
    observation = _obs(datetime(2026, 8, 28, 10, 0, tzinfo=UTC)).model_copy(update={"observation_id": "obs-x"})
    asyncio.run(repo.save_observation(observation))
    cp = _cp(observation_id="obs-x", label=ResearchCheckpointLabel.PLUS_1_SESSION, progression=ResearchProgression.FOLLOW_THROUGH_OBSERVED, captured_at=datetime(2026, 8, 31, 10, 0, tzinfo=UTC))
    asyncio.run(repo.save_checkpoint(cp))

    detail = asyncio.run(build_research_outcome_detail(repo, "obs-x", as_of=datetime(2026, 8, 31, 10, 0, tzinfo=UTC)))
    assert detail is not None
    assert detail.observation.observation_id == "obs-x"
    assert len(detail.checkpoints) == 1
    assert detail.outcome.outcome_status == ResearchOutcomeStatus.FOLLOW_THROUGH_OBSERVED
    assert detail.outcome.earliest_follow_through_session == "+1"
