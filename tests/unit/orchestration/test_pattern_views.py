"""Final 95% sprint (Sections 8/9/25) -- tests for the pattern-aggregation
VIEW layer that makes `app.orchestration.pattern_aggregation` reachable
as a product capability.

Two things are under test here, and only these two: that the view
faithfully re-shapes counts it did NOT produce (never a second counting
implementation), and that Section 9's "never a rate/probability/
confidence" plus Section 25's sample-size honesty hold structurally
rather than by convention.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domain.audit.research_models import ResearchObservation, ResearchOutcomeStatus
from app.orchestration.pattern_aggregation import aggregate_by_pattern
from app.orchestration.pattern_views import (
    PatternAggregateView,
    build_live_pattern_aggregation,
    build_pattern_aggregation,
)

_T0 = datetime(2026, 8, 25, 3, 45, tzinfo=UTC)  # 09:15 IST


def _observation(
    obs_id: str, *, pattern: str | None, symbol: str = "RELIANCE", direction: str = "BULLISH",
    early_stage_state: str = "EARLY_SETUP", derivatives_evidence_available: bool = False,
) -> ResearchObservation:
    return ResearchObservation(
        observation_id=obs_id, run_id="run1", audit_id=None, generated_at=_T0, symbol=symbol, direction=direction,
        selected_right=None, selected_strike=None, early_stage_state=early_stage_state, research_confidence="WEAK",
        actionability="WATCH", spot_at_observation="1000", contractual_expiry_breakeven=None,
        nearest_level_kind=None, nearest_level_value=None, market_context=None, participation_note=None,
        coverage_classification="REPLAY", thesis="test", source="REPLAY",
        derivatives_evidence_available=derivatives_evidence_available, pattern=pattern,
    )


# ============================================================
# Faithful re-shaping -- never a second counting implementation
# ============================================================


def test_view_counts_exactly_match_aggregate_by_pattern() -> None:
    observations = [
        _observation("a", pattern="PRE_BREAKOUT_COMPRESSION"),
        _observation("b", pattern="PRE_BREAKOUT_COMPRESSION", symbol="KAYNES"),
        _observation("c", pattern="RELATIVE_STRENGTH"),
    ]
    outcomes = {
        "a": ResearchOutcomeStatus.FOLLOW_THROUGH_OBSERVED,
        "b": ResearchOutcomeStatus.FAILED_SETUP,
        "c": ResearchOutcomeStatus.NO_FOLLOW_THROUGH,
    }
    expected = {a.pattern: a for a in aggregate_by_pattern(observations, outcomes)}
    view = build_pattern_aggregation(observations, outcomes, as_of=_T0, source="REPLAY")

    assert [p.pattern for p in view.patterns] == sorted(expected)
    for rendered in view.patterns:
        source = expected[rendered.pattern]
        assert rendered.observations == source.observations
        assert rendered.follow_through_observed == source.follow_through_observed
        assert rendered.no_follow_through == source.no_follow_through
        assert rendered.failed_setup == source.failed_setup
        assert rendered.pending == source.pending
        assert rendered.insufficient_outcome_data == source.insufficient_outcome_data
        assert rendered.symbols == list(source.symbols)


def test_determined_and_undetermined_partition_every_observation() -> None:
    """The only arithmetic the view does -- it must never lose or double-
    count an observation."""
    observations = [
        _observation("a", pattern="PRE_BREAKOUT_COMPRESSION"),
        _observation("b", pattern="PRE_BREAKOUT_COMPRESSION"),
        _observation("c", pattern="PRE_BREAKOUT_COMPRESSION"),
        _observation("d", pattern="PRE_BREAKOUT_COMPRESSION"),
    ]
    outcomes = {
        "a": ResearchOutcomeStatus.FOLLOW_THROUGH_OBSERVED,
        "b": ResearchOutcomeStatus.FAILED_SETUP,
        "c": ResearchOutcomeStatus.PENDING,
        "d": ResearchOutcomeStatus.INSUFFICIENT_OUTCOME_DATA,
    }
    rendered = build_pattern_aggregation(observations, outcomes, as_of=_T0, source="REPLAY").patterns[0]
    assert rendered.determined == 2
    assert rendered.undetermined == 2
    assert rendered.determined + rendered.undetermined == rendered.observations


def test_segments_are_present_for_every_real_dimension() -> None:
    observations = [
        _observation("a", pattern="PRE_BREAKOUT_COMPRESSION", direction="BULLISH"),
        _observation("b", pattern="PRE_BREAKOUT_COMPRESSION", direction="BEARISH"),
    ]
    view = build_pattern_aggregation(observations, outcomes={}, as_of=_T0, source="REPLAY")
    dimensions = {d.dimension for d in view.segments}
    assert dimensions == {"DIRECTION", "TIMING_STAGE", "EVIDENCE_COMPLETENESS"}
    direction = next(d for d in view.segments if d.dimension == "DIRECTION")
    assert {s.segment for s in direction.segments} == {"BULLISH", "BEARISH"}


def test_segment_counts_sum_back_to_the_unsegmented_total() -> None:
    """Segmenting is a partition, so a pattern's counts summed across a
    dimension's segments must reproduce its own overall count exactly."""
    observations = [
        _observation("a", pattern="RELATIVE_STRENGTH", direction="BULLISH"),
        _observation("b", pattern="RELATIVE_STRENGTH", direction="BEARISH"),
        _observation("c", pattern="RELATIVE_STRENGTH", direction="BULLISH"),
    ]
    outcomes = {
        "a": ResearchOutcomeStatus.FOLLOW_THROUGH_OBSERVED,
        "b": ResearchOutcomeStatus.FAILED_SETUP,
        "c": ResearchOutcomeStatus.NO_FOLLOW_THROUGH,
    }
    view = build_pattern_aggregation(observations, outcomes, as_of=_T0, source="REPLAY")
    overall = view.patterns[0]
    direction = next(d for d in view.segments if d.dimension == "DIRECTION")
    per_segment = [p for s in direction.segments for p in s.patterns if p.pattern == "RELATIVE_STRENGTH"]
    assert sum(p.observations for p in per_segment) == overall.observations
    assert sum(p.follow_through_observed for p in per_segment) == overall.follow_through_observed
    assert sum(p.failed_setup for p in per_segment) == overall.failed_setup


# ============================================================
# Section 9 -- never a rate, probability, or confidence score
# ============================================================


def test_no_field_in_the_view_can_hold_a_rate_or_probability() -> None:
    """Structural, not stylistic: every count field is typed `int`, so a
    ratio/percentage/probability literally has nowhere to live. This test
    fails the moment somebody adds a float field to this surface."""
    numeric_fields = {
        name: field.annotation
        for name, field in PatternAggregateView.model_fields.items()
        if name not in ("pattern", "symbols")
    }
    assert numeric_fields, "expected counted fields on the view"
    assert all(annotation is int for annotation in numeric_fields.values()), numeric_fields


def test_no_forbidden_vocabulary_appears_in_the_rendered_data() -> None:
    """Checked over the DATA fields only. `sample_size_note` is excluded
    deliberately -- it is the disclaimer, and it has to be able to say
    "this is not a win rate, or a probability" in order to deny them.
    Scanning it for those words would fail the payload for being honest.
    """
    observations = [_observation("a", pattern="PRE_BREAKOUT_COMPRESSION")]
    view = build_pattern_aggregation(
        observations, {"a": ResearchOutcomeStatus.FOLLOW_THROUGH_OBSERVED}, as_of=_T0, source="REPLAY",
    )
    data = view.model_dump(exclude={"sample_size_note"})
    payload = str(data).casefold()
    for forbidden in ("win rate", "probability", "confidence", "expected return", "target price", "recommend"):
        assert forbidden not in payload, forbidden


# ============================================================
# Section 25 -- sample-size and derivatives honesty
# ============================================================


def test_observations_without_a_named_pattern_are_reported_not_silently_dropped() -> None:
    """The real shape of the persisted live store: a few observations
    carry a pattern, many predate the field. Excluding them silently
    would make a 1-observation sample look like the whole history."""
    observations = [_observation("a", pattern="PRE_BREAKOUT_COMPRESSION")] + [
        _observation(f"legacy{i}", pattern=None) for i in range(38)
    ]
    view = build_pattern_aggregation(observations, outcomes={}, as_of=_T0, source="LIVE")
    assert view.total_observations == 39
    assert view.observations_with_named_pattern == 1
    assert view.observations_without_named_pattern == 38
    assert "38 further persisted observation(s) are excluded" in view.sample_size_note


def test_absent_derivatives_history_is_never_reported_as_no_options_activity() -> None:
    observations = [_observation("a", pattern="PRE_BREAKOUT_COMPRESSION", derivatives_evidence_available=False)]
    view = build_pattern_aggregation(observations, outcomes={}, as_of=_T0, source="REPLAY")
    assert view.derivatives_history == "DERIVATIVES_HISTORY_UNAVAILABLE"
    assert view.derivatives_evidence_observations == 0
    assert view.price_only_observations == 1
    assert "DERIVATIVES_HISTORY_UNAVAILABLE" in view.sample_size_note
    assert "NOT an observation that there was no options activity" in view.sample_size_note


def test_real_derivatives_evidence_is_reported_as_available() -> None:
    observations = [_observation("a", pattern="OI_MIGRATION", derivatives_evidence_available=True)]
    view = build_pattern_aggregation(observations, outcomes={}, as_of=_T0, source="LIVE")
    assert view.derivatives_history == "DERIVATIVES_HISTORY_AVAILABLE"
    assert view.derivatives_evidence_observations == 1
    assert "DERIVATIVES_HISTORY_UNAVAILABLE" not in view.sample_size_note


def test_sample_size_note_states_the_limitation_before_any_count() -> None:
    observations = [_observation("a", pattern="PRE_BREAKOUT_COMPRESSION")]
    note = build_pattern_aggregation(
        observations, {"a": ResearchOutcomeStatus.FOLLOW_THROUGH_OBSERVED}, as_of=_T0, source="REPLAY",
    ).sample_size_note
    assert note.startswith("SMALL SAMPLE")
    assert "not a validated edge" in note


def test_empty_history_is_an_honest_empty_result_not_a_claim() -> None:
    view = build_pattern_aggregation([], outcomes={}, as_of=_T0, source="LIVE")
    assert view.patterns == []
    assert view.segments == []
    assert view.total_observations == 0
    assert "No aggregatable history yet" in view.sample_size_note


def test_observations_with_no_named_pattern_only_is_empty_not_zeroed() -> None:
    observations = [_observation(f"legacy{i}", pattern=None) for i in range(5)]
    view = build_pattern_aggregation(observations, outcomes={}, as_of=_T0, source="LIVE")
    assert view.patterns == []
    assert view.total_observations == 5
    assert view.observations_with_named_pattern == 0
    assert "none of which carry a named development pattern" in view.sample_size_note


# ============================================================
# Live repository entry point
# ============================================================


@pytest.mark.asyncio
async def test_build_live_pattern_aggregation_resolves_outcomes_from_real_checkpoints(tmp_path: object) -> None:
    from pathlib import Path

    from app.persistence.jsonl_file import JsonlResearchOutcomeRepository

    assert isinstance(tmp_path, Path)
    repository = JsonlResearchOutcomeRepository(tmp_path)
    await repository.save_observation(_observation("a", pattern="PRE_BREAKOUT_COMPRESSION"))
    await repository.save_observation(_observation("b", pattern=None))

    view = await build_live_pattern_aggregation(repository, as_of=_T0)
    assert view.source == "LIVE"
    assert view.total_observations == 2
    assert view.observations_with_named_pattern == 1
    assert [p.pattern for p in view.patterns] == ["PRE_BREAKOUT_COMPRESSION"]
    # No checkpoints exist yet, so the one named observation is honestly
    # undetermined -- never counted as a success or a failure.
    assert view.patterns[0].determined == 0
    assert view.patterns[0].undetermined == 1
