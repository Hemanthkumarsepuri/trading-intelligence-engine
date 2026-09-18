"""95% sprint, Sprint 2 -- pure, deterministic unit tests for
`aggregate_by_pattern()` and `outcome_for_replay_observation()`. No
network, no repository, no full pipeline -- descriptive counting only,
never a probability/win-rate/confidence.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from app.domain.audit.research_models import ResearchObservation, ResearchOutcomeStatus
from app.domain.market.freshness import DataFreshness
from app.domain.market.models import Candle, Timeframe
from app.orchestration.outcome_horizons import OutcomeHorizonLabel, compute_price_path_outcome
from app.orchestration.pattern_aggregation import (
    aggregate_by_pattern,
    aggregate_by_pattern_segmented,
    outcome_for_replay_observation,
    segment_by_calendar_quarter,
    segment_by_direction,
    segment_by_evidence_completeness,
    segment_by_timing_stage,
)

_T0 = datetime(2026, 8, 25, 3, 45, tzinfo=UTC)  # 09:15 IST


def _observation(
    obs_id: str, *, pattern: str | None, symbol: str = "RELIANCE", direction: str = "BULLISH",
    spot: str = "1000", breakeven: str | None = "1020", nearest_level_kind: str | None = "resistance",
    nearest_level_value: str | None = "1030",
    invalidation_level_kind: str | None = None, invalidation_level_value: str | None = None,
    early_stage_state: str = "EARLY_SETUP", derivatives_evidence_available: bool = False,
) -> ResearchObservation:
    return ResearchObservation(
        observation_id=obs_id, run_id="run1", audit_id=None, generated_at=_T0, symbol=symbol, direction=direction,
        selected_right=None, selected_strike=None, early_stage_state=early_stage_state, research_confidence="WEAK",
        actionability="WATCH", spot_at_observation=spot, contractual_expiry_breakeven=breakeven,
        nearest_level_kind=nearest_level_kind, nearest_level_value=nearest_level_value,
        invalidation_level_kind=invalidation_level_kind, invalidation_level_value=invalidation_level_value,
        market_context=None,
        participation_note=None, coverage_classification="REPLAY", thesis="test", source="REPLAY",
        derivatives_evidence_available=derivatives_evidence_available, pattern=pattern,
    )


def _candle(*, timestamp: datetime, close: str, high: str | None = None, low: str | None = None) -> Candle:
    c = Decimal(close)
    return Candle(
        provider="test", freshness=DataFreshness(data_timestamp=timestamp, received_timestamp=timestamp),
        instrument_id="NSE_EQ|TEST", timeframe=Timeframe.M15,
        open=c, high=Decimal(high) if high else c, low=Decimal(low) if low else c, close=c, volume=1000,
    )


# ============================================================
# aggregate_by_pattern -- pure counting
# ============================================================


def test_excludes_observations_with_no_named_pattern() -> None:
    observations = [_observation("a", pattern=None), _observation("b", pattern="NONE")]
    result = aggregate_by_pattern(observations, outcomes={})
    assert result == []


def test_counts_are_exact_and_grouped_by_pattern() -> None:
    observations = [
        _observation("a", pattern="PRE_BREAKOUT_COMPRESSION"),
        _observation("b", pattern="PRE_BREAKOUT_COMPRESSION"),
        _observation("c", pattern="RELATIVE_STRENGTH"),
    ]
    outcomes = {
        "a": ResearchOutcomeStatus.FOLLOW_THROUGH_OBSERVED,
        "b": ResearchOutcomeStatus.FAILED_SETUP,
        "c": ResearchOutcomeStatus.NO_FOLLOW_THROUGH,
    }
    result = aggregate_by_pattern(observations, outcomes)
    by_pattern = {r.pattern: r for r in result}
    assert by_pattern["PRE_BREAKOUT_COMPRESSION"].observations == 2
    assert by_pattern["PRE_BREAKOUT_COMPRESSION"].follow_through_observed == 1
    assert by_pattern["PRE_BREAKOUT_COMPRESSION"].failed_setup == 1
    assert by_pattern["RELATIVE_STRENGTH"].observations == 1
    assert by_pattern["RELATIVE_STRENGTH"].no_follow_through == 1


def test_missing_outcome_defaults_to_pending_never_dropped() -> None:
    observations = [_observation("a", pattern="PRE_BREAKOUT_COMPRESSION")]
    result = aggregate_by_pattern(observations, outcomes={})
    assert result[0].observations == 1
    assert result[0].pending == 1


def test_sorted_by_pattern_name_never_by_outcome_quality() -> None:
    observations = [
        _observation("a", pattern="RELATIVE_STRENGTH"),
        _observation("b", pattern="FAILED_BREAKDOWN_RECLAIM"),
        _observation("c", pattern="PRE_BREAKOUT_COMPRESSION"),
    ]
    result = aggregate_by_pattern(observations, outcomes={})
    assert [r.pattern for r in result] == ["FAILED_BREAKDOWN_RECLAIM", "PRE_BREAKOUT_COMPRESSION", "RELATIVE_STRENGTH"]


def test_symbols_are_deduplicated_and_sorted() -> None:
    observations = [
        _observation("a", pattern="RELATIVE_STRENGTH", symbol="RELIANCE"),
        _observation("b", pattern="RELATIVE_STRENGTH", symbol="KAYNES"),
        _observation("c", pattern="RELATIVE_STRENGTH", symbol="RELIANCE"),
    ]
    result = aggregate_by_pattern(observations, outcomes={})
    assert result[0].symbols == ("KAYNES", "RELIANCE")


# ============================================================
# aggregate_by_pattern_segmented -- final 95% sprint (Section 8)
# ============================================================


def test_segmented_by_direction_partitions_before_counting_by_pattern() -> None:
    observations = [
        _observation("a", pattern="PRE_BREAKOUT_COMPRESSION", direction="BULLISH"),
        _observation("b", pattern="PRE_BREAKOUT_COMPRESSION", direction="BEARISH"),
        _observation("c", pattern="PRE_BREAKOUT_COMPRESSION", direction="BULLISH"),
    ]
    outcomes = {
        "a": ResearchOutcomeStatus.FOLLOW_THROUGH_OBSERVED, "b": ResearchOutcomeStatus.NO_FOLLOW_THROUGH,
        "c": ResearchOutcomeStatus.PENDING,
    }
    result = aggregate_by_pattern_segmented(observations, outcomes, segment_by=segment_by_direction)
    assert set(result) == {"BULLISH", "BEARISH"}
    assert result["BULLISH"][0].observations == 2
    assert result["BULLISH"][0].follow_through_observed == 1
    assert result["BULLISH"][0].pending == 1
    assert result["BEARISH"][0].observations == 1
    assert result["BEARISH"][0].no_follow_through == 1


def test_segmented_counts_sum_back_to_the_unsegmented_total() -> None:
    """Never a second, independent counting path -- summing a pattern's
    counts across every segment must exactly reproduce
    `aggregate_by_pattern()`'s own unsegmented total for that pattern."""
    observations = [
        _observation("a", pattern="RELATIVE_STRENGTH", direction="BULLISH"),
        _observation("b", pattern="RELATIVE_STRENGTH", direction="BEARISH"),
        _observation("c", pattern="RELATIVE_STRENGTH", direction="BULLISH"),
    ]
    outcomes = {
        "a": ResearchOutcomeStatus.FOLLOW_THROUGH_OBSERVED, "b": ResearchOutcomeStatus.FAILED_SETUP,
        "c": ResearchOutcomeStatus.NO_FOLLOW_THROUGH,
    }
    unsegmented = aggregate_by_pattern(observations, outcomes)[0]
    segmented = aggregate_by_pattern_segmented(observations, outcomes, segment_by=segment_by_direction)
    total_observations = sum(agg.observations for aggs in segmented.values() for agg in aggs if agg.pattern == "RELATIVE_STRENGTH")
    total_follow_through = sum(agg.follow_through_observed for aggs in segmented.values() for agg in aggs if agg.pattern == "RELATIVE_STRENGTH")
    assert total_observations == unsegmented.observations
    assert total_follow_through == unsegmented.follow_through_observed


def test_segmented_by_timing_stage() -> None:
    observations = [
        _observation("a", pattern="PRE_BREAKOUT_COMPRESSION", early_stage_state="EARLY_SETUP"),
        _observation("b", pattern="PRE_BREAKOUT_COMPRESSION", early_stage_state="WATCH"),
    ]
    result = aggregate_by_pattern_segmented(observations, outcomes={}, segment_by=segment_by_timing_stage)
    assert set(result) == {"EARLY_SETUP", "WATCH"}
    assert result["EARLY_SETUP"][0].observations == 1
    assert result["WATCH"][0].observations == 1


def test_segmented_by_evidence_completeness() -> None:
    observations = [
        _observation("a", pattern="PRE_BREAKOUT_COMPRESSION", derivatives_evidence_available=True),
        _observation("b", pattern="PRE_BREAKOUT_COMPRESSION", derivatives_evidence_available=False),
    ]
    result = aggregate_by_pattern_segmented(observations, outcomes={}, segment_by=segment_by_evidence_completeness)
    assert set(result) == {"DERIVATIVES_EVIDENCE_AVAILABLE", "PRICE_ONLY_EVIDENCE"}
    assert result["DERIVATIVES_EVIDENCE_AVAILABLE"][0].observations == 1
    assert result["PRICE_ONLY_EVIDENCE"][0].observations == 1


def test_segmented_excludes_no_named_pattern_same_as_unsegmented() -> None:
    observations = [_observation("a", pattern="NONE", direction="BULLISH")]
    result = aggregate_by_pattern_segmented(observations, outcomes={}, segment_by=segment_by_direction)
    assert result == {"BULLISH": []}


# ============================================================
# outcome_for_replay_observation -- deterministic translation from
# outcome_horizons.py's own already-computed facts
# ============================================================


def test_pending_when_the_plus5_session_target_has_not_been_reached() -> None:
    obs = _observation("a", pattern="PRE_BREAKOUT_COMPRESSION")
    result = outcome_for_replay_observation(obs, [], as_of=_T0 + timedelta(hours=1))
    assert result == ResearchOutcomeStatus.PENDING


def test_insufficient_outcome_data_when_target_passed_but_no_local_candles() -> None:
    obs = _observation("a", pattern="PRE_BREAKOUT_COMPRESSION")
    # Friday 09:15 + 5 sessions is the following Friday's 15:30 close.
    far_future = _T0 + timedelta(days=10)
    result = outcome_for_replay_observation(obs, [], as_of=far_future)
    assert result == ResearchOutcomeStatus.INSUFFICIENT_OUTCOME_DATA


def test_no_failed_setup_when_no_invalidation_level_is_recorded() -> None:
    """Every pattern besides PRE_BREAKOUT_COMPRESSION, and every
    observation written before `invalidation_level_kind` existed, carries
    no genuine invalidation-side level -- `outcome_for_replay_observation()`
    must never fabricate FAILED_SETUP from an unrelated fact (here, a
    price move well below spot against a BULLISH thesis, with only the
    CONFIRMATION-relevant resistance recorded): honestly NO_FOLLOW_THROUGH,
    never a guessed "failed" verdict. Realistic price-only fixture:
    `breakeven=None` (a real price-only observation never has one)."""
    obs = _observation(
        "a", pattern="PRE_BREAKOUT_COMPRESSION", breakeven=None,
        nearest_level_kind="resistance", nearest_level_value="1030",
    )
    target_session = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    candles = [
        _candle(timestamp=_T0, close="1000"),
        _candle(timestamp=target_session, close="985", high="1005", low="985"),
    ]
    result = outcome_for_replay_observation(obs, candles, as_of=target_session + timedelta(hours=7))
    assert result == ResearchOutcomeStatus.NO_FOLLOW_THROUGH


def test_failed_setup_when_a_genuine_invalidation_level_is_broken() -> None:
    """Final 95% sprint (Section 6): once a genuine invalidation-side
    supporting level IS recorded (PRE_BREAKOUT_COMPRESSION only), a real
    breach of it now correctly yields FAILED_SETUP -- no longer
    permanently unreachable."""
    obs = _observation(
        "a", pattern="PRE_BREAKOUT_COMPRESSION", breakeven=None,
        nearest_level_kind="resistance", nearest_level_value="1030",
        invalidation_level_kind="support", invalidation_level_value="990",
    )
    target_session = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    candles = [
        _candle(timestamp=_T0, close="1000"),
        _candle(timestamp=target_session, close="985", high="1005", low="985"),
    ]
    result = outcome_for_replay_observation(obs, candles, as_of=target_session + timedelta(hours=7))
    assert result == ResearchOutcomeStatus.FAILED_SETUP


def _both_levels_obs() -> ResearchObservation:
    return _observation(
        "a", pattern="PRE_BREAKOUT_COMPRESSION", breakeven=None,
        nearest_level_kind="resistance", nearest_level_value="1010",
        invalidation_level_kind="support", invalidation_level_value="990",
    )


def test_both_levels_crossed_in_the_same_bar_is_not_resolvable() -> None:
    """A single M15 bar that spans BOTH levels cannot say which was crossed
    first -- honestly INSUFFICIENT_OUTCOME_DATA, never a guess either way."""
    target_session = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    candles = [
        _candle(timestamp=_T0, close="1000"),
        _candle(timestamp=target_session, close="995", high="1015", low="985"),
    ]
    result = outcome_for_replay_observation(_both_levels_obs(), candles, as_of=target_session + timedelta(hours=7))
    assert result == ResearchOutcomeStatus.INSUFFICIENT_OUTCOME_DATA


def test_invalidation_crossed_before_confirmation_is_a_failed_setup() -> None:
    target_session = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    candles = [
        _candle(timestamp=_T0, close="1000"),
        _candle(timestamp=_T0 + timedelta(days=1), close="985", high="1000", low="985"),
        _candle(timestamp=target_session, close="1015", high="1015", low="1000"),
    ]
    result = outcome_for_replay_observation(_both_levels_obs(), candles, as_of=target_session + timedelta(hours=7))
    assert result == ResearchOutcomeStatus.FAILED_SETUP


def test_confirmation_crossed_before_a_later_retrace_stays_follow_through() -> None:
    """Release gate correctness fix: a breakout on day 1 that later retraces
    through the old floor is not rewritten as FAILED -- the later crossing
    stays visible via `first_invalidation_at`."""
    target_session = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    early = _T0 + timedelta(days=1)
    candles = [
        _candle(timestamp=_T0, close="1000"),
        _candle(timestamp=early, close="1015", high="1015", low="1000"),
        _candle(timestamp=target_session, close="985", high="1000", low="985"),
    ]
    obs = _both_levels_obs()
    as_of = target_session + timedelta(hours=7)
    assert outcome_for_replay_observation(obs, candles, as_of=as_of) == ResearchOutcomeStatus.FOLLOW_THROUGH_OBSERVED
    facts = compute_price_path_outcome(obs, OutcomeHorizonLabel.PLUS_5D, candles, as_of=as_of)
    assert facts.first_confirmation_at == early
    assert facts.first_invalidation_at == target_session


def test_follow_through_observed_when_the_opposing_level_is_broken_by_plus5() -> None:
    """A price-only observation (no breakeven) can still reach
    FOLLOW_THROUGH_OBSERVED via its recorded opposing level breaking
    through -- see `outcome_horizons._opposing_level_broken_through()`."""
    obs = _observation(
        "a", pattern="PRE_BREAKOUT_COMPRESSION", breakeven=None,
        nearest_level_kind="resistance", nearest_level_value="1010",
    )
    target_session = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    candles = [
        _candle(timestamp=_T0, close="1000"),
        _candle(timestamp=target_session, close="1015", high="1015", low="1000"),
    ]
    result = outcome_for_replay_observation(obs, candles, as_of=target_session + timedelta(hours=7))
    assert result == ResearchOutcomeStatus.FOLLOW_THROUGH_OBSERVED


def test_no_follow_through_when_neither_confirmed_nor_invalidated_by_plus5() -> None:
    obs = _observation("a", pattern="PRE_BREAKOUT_COMPRESSION", breakeven=None, nearest_level_kind="resistance", nearest_level_value="1030")
    target_session = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    candles = [
        _candle(timestamp=_T0, close="1000"),
        _candle(timestamp=target_session, close="1005", high="1008", low="998"),
    ]
    result = outcome_for_replay_observation(obs, candles, as_of=target_session + timedelta(hours=7))
    assert result == ResearchOutcomeStatus.NO_FOLLOW_THROUGH


# ============================================================
# build_pattern_aggregation_for_replay -- end-to-end over a real
# (JSONL-backed) outcome repository
# ============================================================


def test_build_pattern_aggregation_for_replay_end_to_end(tmp_path: Path) -> None:
    import asyncio

    from app.orchestration.pattern_aggregation import (
        PatternAggregate,
        build_pattern_aggregation_for_replay,
    )
    from app.persistence.jsonl_file import JsonlResearchOutcomeRepository

    repo = JsonlResearchOutcomeRepository(tmp_path)
    target_session = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    candles = [
        _candle(timestamp=_T0, close="1000"),
        _candle(timestamp=target_session, close="1015", high="1015", low="1000"),
    ]

    async def run() -> list[PatternAggregate]:
        await repo.save_observation(_observation("a", pattern="PRE_BREAKOUT_COMPRESSION", breakeven=None, nearest_level_kind="resistance", nearest_level_value="1010"))
        await repo.save_observation(_observation("b", pattern="NONE"))  # excluded -- no named pattern

        async def candles_by_symbol(symbol: str) -> list[Candle]:
            return candles

        return await build_pattern_aggregation_for_replay(
            repo, candles_by_symbol=candles_by_symbol, as_of=target_session + timedelta(hours=7),
        )

    result = asyncio.run(run())
    assert len(result) == 1
    assert result[0].pattern == "PRE_BREAKOUT_COMPRESSION"
    assert result[0].observations == 1
    assert result[0].follow_through_observed == 1


# -- Section 30: independent time windows --------------------------------


def test_calendar_quarter_segmentation_uses_the_real_ist_quarter() -> None:
    q2 = _observation("a", pattern="PRE_BREAKOUT_COMPRESSION").model_copy(
        update={"generated_at": datetime(2026, 5, 14, 5, 0, tzinfo=UTC)}
    )
    q3 = _observation("b", pattern="PRE_BREAKOUT_COMPRESSION").model_copy(
        update={"generated_at": datetime(2026, 8, 25, 5, 0, tzinfo=UTC)}
    )
    # 31 Mar 2026, 21:00 UTC is already 1 Apr IST -- the quarter must follow
    # the trading calendar's own timezone, not UTC.
    boundary = _observation("c", pattern="PRE_BREAKOUT_COMPRESSION").model_copy(
        update={"generated_at": datetime(2026, 3, 31, 21, 0, tzinfo=UTC)}
    )
    assert segment_by_calendar_quarter(q2) == "2026-Q2"
    assert segment_by_calendar_quarter(q3) == "2026-Q3"
    assert segment_by_calendar_quarter(boundary) == "2026-Q2"


def test_quarter_segments_partition_the_same_counts_without_changing_them() -> None:
    """A segmentation must only PARTITION: every pattern's per-quarter
    counts must sum back to its unsegmented total, or the split is
    inventing or losing observations."""
    observations = [
        _observation("a", pattern="PRE_BREAKOUT_COMPRESSION").model_copy(
            update={"generated_at": datetime(2026, 5, 14, 5, 0, tzinfo=UTC)}
        ),
        _observation("b", pattern="PRE_BREAKOUT_COMPRESSION").model_copy(
            update={"generated_at": datetime(2026, 8, 25, 5, 0, tzinfo=UTC)}
        ),
        _observation("c", pattern="FAILED_BREAKDOWN_RECLAIM").model_copy(
            update={"generated_at": datetime(2026, 8, 26, 5, 0, tzinfo=UTC)}
        ),
    ]
    outcomes = {
        "a": ResearchOutcomeStatus.FOLLOW_THROUGH_OBSERVED,
        "b": ResearchOutcomeStatus.FAILED_SETUP,
        "c": ResearchOutcomeStatus.NO_FOLLOW_THROUGH,
    }
    overall = {a.pattern: a.observations for a in aggregate_by_pattern(observations, outcomes)}
    segmented = aggregate_by_pattern_segmented(observations, outcomes, segment_by=segment_by_calendar_quarter)
    assert sorted(segmented) == ["2026-Q2", "2026-Q3"]
    summed: dict[str, int] = {}
    for aggregates in segmented.values():
        for aggregate in aggregates:
            summed[aggregate.pattern] = summed.get(aggregate.pattern, 0) + aggregate.observations
    assert summed == overall


def test_pre_fix_live_observations_are_flagged_not_silently_pooled() -> None:
    """Observations recorded before the M15 direction mapping was
    corrected carry directions the current rule would not produce. They
    are never rewritten -- but the surface that aggregates them has to
    say so, or a reader pools two different rules into one count."""
    from app.orchestration.pattern_views import M15_DIRECTION_FIX_AT, _pre_direction_fix_note

    before = _observation("a", pattern="PRE_BREAKOUT_COMPRESSION").model_copy(
        update={"generated_at": M15_DIRECTION_FIX_AT - timedelta(days=1)}
    )
    after = _observation("b", pattern="PRE_BREAKOUT_COMPRESSION").model_copy(
        update={"generated_at": M15_DIRECTION_FIX_AT + timedelta(days=1)}
    )
    note = _pre_direction_fix_note([before, after])
    assert note is not None
    assert "1 of these 2 observation(s)" in note
    assert "inverted" in note
    assert _pre_direction_fix_note([after]) is None
    assert _pre_direction_fix_note([]) is None
