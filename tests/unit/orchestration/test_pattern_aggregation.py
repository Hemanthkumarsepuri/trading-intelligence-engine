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
from app.orchestration.pattern_aggregation import (
    aggregate_by_pattern,
    outcome_for_replay_observation,
)

_T0 = datetime(2026, 8, 25, 3, 45, tzinfo=UTC)  # 09:15 IST


def _observation(
    obs_id: str, *, pattern: str | None, symbol: str = "RELIANCE", direction: str = "BULLISH",
    spot: str = "1000", breakeven: str | None = "1020", nearest_level_kind: str | None = "resistance",
    nearest_level_value: str | None = "1030",
) -> ResearchObservation:
    return ResearchObservation(
        observation_id=obs_id, run_id="run1", audit_id=None, generated_at=_T0, symbol=symbol, direction=direction,
        selected_right=None, selected_strike=None, early_stage_state="EARLY_SETUP", research_confidence="WEAK",
        actionability="WATCH", spot_at_observation=spot, contractual_expiry_breakeven=breakeven,
        nearest_level_kind=nearest_level_kind, nearest_level_value=nearest_level_value, market_context=None,
        participation_note=None, coverage_classification="REPLAY", thesis="test", source="REPLAY",
        derivatives_evidence_available=False, pattern=pattern,
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


def test_no_failed_setup_bucket_is_ever_produced_for_replay() -> None:
    """95% sprint, Sprint 1 correctness fix: no genuine invalidation-side
    level is tracked, so `outcome_for_replay_observation()` never
    fabricates FAILED_SETUP -- even a price move well below spot, against
    a BULLISH thesis, is honestly NO_FOLLOW_THROUGH (not confirmed),
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
