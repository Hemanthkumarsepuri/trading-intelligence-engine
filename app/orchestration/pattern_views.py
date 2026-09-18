"""Final 95% sprint (Sections 8/9/25) -- the read-only VIEW layer that
finally makes `app.orchestration.pattern_aggregation` reachable as a
product capability rather than a library function.

Before this module, `aggregate_by_pattern()` was correct, pure and well
tested, but nothing in `app/` imported it: there was no API route and no
UI section, so the operator could not reach "what happened after this
type of observation?" at all. This module adds ONLY the view/serialization
step. It performs no counting of its own -- every number below is produced
by `pattern_aggregation.py` itself, so this surface can never disagree
with the module it displays.

Section 9's prohibition is enforced structurally, not by convention:
every field here is an integer COUNT or a literal string. There is no
float, no ratio, no percentage, and no ordering by "best" anywhere in
this file -- so a win rate, probability, confidence score or expected
return is not merely discouraged, it has nowhere to live.

Section 25's honesty requirement is the other half of the job and the
reason this is a real module rather than three lines in `main.py`. A
count is only meaningful next to the size of the pool it was drawn from,
so `PatternAggregationView` carries the real denominators alongside the
counts: how many observations exist at all, how many were EXCLUDED for
carrying no named pattern (38 of the 41 currently persisted were written
before the `pattern` field existed -- silently dropping them would make a
3-observation sample look like the system's whole history), and how many
rest on real derivatives evidence versus price evidence alone
(`PRICE_HISTORY_AVAILABLE` vs `DERIVATIVES_HISTORY_AVAILABLE`).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from app.domain.audit.research_models import ResearchObservation, ResearchOutcomeStatus
from app.orchestration.pattern_aggregation import (
    PatternAggregate,
    aggregate_by_pattern,
    aggregate_by_pattern_segmented,
    outcome_for_live_observation,
    segment_by_calendar_quarter,
    segment_by_direction,
    segment_by_evidence_completeness,
    segment_by_timing_stage,
)
from app.persistence.interfaces import ResearchOutcomeRepository
from app.persistence.jsonl_file import JsonlResearchOutcomeRepository

# The real segment dimensions Section 8 asks for, restricted to the ones
# genuinely present and STRUCTURED on `ResearchObservation` today. Market
# regime and sector are deliberately absent rather than guessed from
# `market_context`'s free text or an un-stored symbol->sector join -- see
# `aggregate_by_pattern_segmented()`'s own docstring, which this reuses
# verbatim rather than restating a second, driftable list.
_SEGMENT_DIMENSIONS = {
    "DIRECTION": segment_by_direction,
    "TIMING_STAGE": segment_by_timing_stage,
    "EVIDENCE_COMPLETENESS": segment_by_evidence_completeness,
}


class PatternAggregateView(BaseModel):
    """One named pattern's real, counted history. Every field is an exact
    tally over real observations -- never a rate (Section 9)."""

    pattern: str
    observations: int
    follow_through_observed: int
    no_follow_through: int
    failed_setup: int
    pending: int
    insufficient_outcome_data: int
    symbols: list[str] = Field(default_factory=list)
    # The honest "how much of this pattern's history has actually been
    # resolved" split, derived by addition from the counts above -- a
    # reader must not have to do that arithmetic themselves to notice
    # that a 3-observation pattern with 3 PENDING has told them nothing.
    determined: int
    undetermined: int


class PatternSegmentView(BaseModel):
    """One value of one segment dimension (e.g. dimension DIRECTION,
    segment BULLISH) and the same per-pattern counts within it."""

    segment: str
    patterns: list[PatternAggregateView] = Field(default_factory=list)


class PatternSegmentDimensionView(BaseModel):
    dimension: str
    segments: list[PatternSegmentView] = Field(default_factory=list)


class PatternAggregationView(BaseModel):
    """Descriptive historical research -- explicitly NOT a prediction,
    a win rate, or a claim about what will happen next.

    `total_observations` / `observations_without_named_pattern` are the
    Section 25 denominators: they exist so this view can never present a
    handful of observations as if it were a validated edge. A reader who
    sees `observations_with_named_pattern: 3` next to
    `total_observations: 41` immediately knows both how small the sample
    is and why."""

    generated_at: datetime
    source: str
    total_observations: int
    observations_with_named_pattern: int
    observations_without_named_pattern: int
    derivatives_evidence_observations: int
    price_only_observations: int
    # Literal, honest labels -- never "no options activity" for absent
    # history (Section 9's explicit trap).
    price_history: str = "PRICE_HISTORY_AVAILABLE"
    derivatives_history: str
    sample_size_note: str
    patterns: list[PatternAggregateView] = Field(default_factory=list)
    segments: list[PatternSegmentDimensionView] = Field(default_factory=list)


def _to_view(aggregate: PatternAggregate) -> PatternAggregateView:
    """Pure re-shaping of an already-computed `PatternAggregate` -- the
    only arithmetic is the determined/undetermined split, which is plain
    addition over counts this function did not produce."""
    determined = aggregate.follow_through_observed + aggregate.no_follow_through + aggregate.failed_setup
    undetermined = aggregate.pending + aggregate.insufficient_outcome_data
    return PatternAggregateView(
        pattern=aggregate.pattern,
        observations=aggregate.observations,
        follow_through_observed=aggregate.follow_through_observed,
        no_follow_through=aggregate.no_follow_through,
        failed_setup=aggregate.failed_setup,
        pending=aggregate.pending,
        insufficient_outcome_data=aggregate.insufficient_outcome_data,
        symbols=list(aggregate.symbols),
        determined=determined,
        undetermined=undetermined,
    )


def _sample_size_note(*, total: int, named: int, determined: int, derivatives: int) -> str:
    """The real, plain sentence Section 25 demands. Deliberately states
    the limitation FIRST: the operator must read how little this proves
    before they read any count, not after."""
    if named == 0:
        return (
            f"No aggregatable history yet. {total} observation(s) are persisted, none of which carry a named "
            "development pattern, so there is nothing to aggregate by. This is an honest empty result, not a "
            "claim that no patterns occur."
        )
    excluded = total - named
    if determined < _DESCRIPTIVE_SAMPLE_MIN_DETERMINED:
        lead = (
            f"SMALL SAMPLE -- {named} observation(s) carry a named pattern, of which {determined} have a "
            "determined outcome so far. This is descriptive history, not a validated edge, a win rate, or a "
            "probability; it is far too small to support any claim about what will happen next."
        )
    else:
        # Release gate (Section 9) -- a larger sample earns a different
        # sentence, never a stronger claim. The caveats that remain true
        # at any size are stated explicitly.
        lead = (
            f"DESCRIPTIVE SAMPLE -- {named} observation(s) carry a named pattern, of which {determined} have a "
            "determined outcome. Counts describe what happened after past observations; they are not a "
            "probability, a win rate, or a validated edge. No significance test has been run, observations "
            "within a pattern share market periods and are not independent trials, and a per-pattern or "
            "per-segment count can still be small even when this total is not."
        )
    parts = [lead]
    if excluded > 0:
        parts.append(
            f"{excluded} further persisted observation(s) are excluded because they carry no named pattern "
            "(most were recorded before the pattern field existed) -- excluded, never silently counted as a failure."
        )
    if derivatives == 0:
        parts.append(
            "No observation in this sample rests on real historical option-chain/futures evidence: "
            "DERIVATIVES_HISTORY_UNAVAILABLE. Derivatives-dependent patterns remain unevaluable -- "
            "this is missing history, NOT an observation that there was no options activity."
        )
    return " ".join(parts)


# Below this many determined outcomes the note calls the sample SMALL. A
# plain reporting cutoff, not a statistical threshold -- see the note text.
_DESCRIPTIVE_SAMPLE_MIN_DETERMINED = 30

SegmentFn = Callable[[ResearchObservation], str]


def build_pattern_aggregation(
    observations: list[ResearchObservation], outcomes: dict[str, ResearchOutcomeStatus], *, as_of: datetime, source: str,
    extra_dimensions: dict[str, SegmentFn] | None = None, provenance_note: str | None = None,
) -> PatternAggregationView:
    """Pure, deterministic, no I/O -- the same shape for a live store or a
    replay store, so both aggregation paths share one surface rather than
    growing a second one. `outcomes` is already resolved by the caller
    (live checkpoints or replay horizons), exactly as
    `aggregate_by_pattern()` itself requires."""
    aggregates = aggregate_by_pattern(observations, outcomes)
    views = [_to_view(a) for a in aggregates]
    named = sum(a.observations for a in aggregates)
    determined = sum(v.determined for v in views)
    derivatives = sum(1 for o in observations if o.derivatives_evidence_available)
    segments = [
        PatternSegmentDimensionView(
            dimension=dimension,
            segments=[
                PatternSegmentView(segment=segment, patterns=[_to_view(a) for a in segment_aggregates])
                for segment, segment_aggregates in aggregate_by_pattern_segmented(
                    observations, outcomes, segment_by=segment_by
                ).items()
                # A segment whose observations all lack a named pattern
                # aggregates to nothing -- shown as absent rather than as
                # an empty row implying a zero result was measured.
                if segment_aggregates
            ],
        )
        for dimension, segment_by in {**_SEGMENT_DIMENSIONS, **(extra_dimensions or {})}.items()
    ]
    return PatternAggregationView(
        generated_at=as_of,
        source=source,
        total_observations=len(observations),
        observations_with_named_pattern=named,
        observations_without_named_pattern=len(observations) - named,
        derivatives_evidence_observations=derivatives,
        price_only_observations=len(observations) - derivatives,
        derivatives_history=(
            "DERIVATIVES_HISTORY_AVAILABLE" if derivatives > 0 else "DERIVATIVES_HISTORY_UNAVAILABLE"
        ),
        sample_size_note=_sample_size_note(
            total=len(observations), named=named, determined=determined, derivatives=derivatives,
        ) + (f" {provenance_note}" if provenance_note else ""),
        patterns=views,
        segments=[s for s in segments if s.segments],
    )


# The instant the inverted M15 sequence-label mapping was corrected (see
# `app.domain.technical.ema_alignment`'s own docstring). Live observations
# recorded BEFORE this carry a direction produced by the old mapping,
# which read a falling EMA ordering as bullish evidence. They are real
# records of what TIRE said at the time and are never rewritten -- but a
# reader comparing them with today's behaviour has to be told, on the
# surface that aggregates them, that the two were not produced by the
# same rule.
M15_DIRECTION_FIX_AT = datetime(2026, 9, 18, tzinfo=UTC)


def _pre_direction_fix_note(observations: list[ResearchObservation]) -> str | None:
    affected = sum(1 for o in observations if o.generated_at < M15_DIRECTION_FIX_AT)
    if affected == 0:
        return None
    return (
        f"PROVENANCE: {affected} of these {len(observations)} observation(s) were recorded before "
        f"{M15_DIRECTION_FIX_AT.date().isoformat()}, when the M15 trend evidence row had its direction "
        "mapping inverted (a falling EMA ordering was reported as bullish). Their recorded directions and "
        "patterns are what TIRE actually said at the time and are kept unchanged, but they were not produced "
        "by the rule running today and should not be pooled with newer observations as if they were."
    )


async def build_live_pattern_aggregation(
    outcome_repository: ResearchOutcomeRepository, *, as_of: datetime,
) -> PatternAggregationView:
    """The LIVE-store entry point `GET /api/research/patterns` calls.
    Each observation's outcome comes from `outcome_for_live_observation()`
    -- i.e. `summarize_research_outcome()` over the real checkpoints
    actually captured for it -- never recomputed and never a new evidence
    pass."""
    observations = await outcome_repository.query_all_observations()
    outcomes = {
        observation.observation_id: await outcome_for_live_observation(
            observation, outcome_repository=outcome_repository, as_of=as_of,
        )
        for observation in observations
    }
    return build_pattern_aggregation(
        observations, outcomes, as_of=as_of, source="LIVE",
        provenance_note=_pre_direction_fix_note(observations),
    )


class ReplayDatasetUnavailable(RuntimeError):
    """The historical replay dataset has not been built on this machine."""


async def build_replay_pattern_aggregation(
    *, dataset_path: Path, summary_path: Path, observation_dir: Path, as_of: datetime,
) -> PatternAggregationView:
    """Release gate (Sections 9-11) -- the REPLAY-dataset entry point.

    Reads what `scripts.build_replay_dataset` already persisted: the
    representative observation of each EPISODE (never per-bar duplicates)
    and, per observation, the +5-session reference status computed when the
    dataset was built. Nothing is re-evaluated here, so this view can never
    disagree with the dataset rows a reader can inspect directly.

    Adds two segment dimensions that only replay rows carry in structured
    form: MARKET_CONTEXT (NIFTY_UP / NIFTY_DOWN / NIFTY_FLAT / UNKNOWN, an
    as-of bounded fact) and SECTOR (the static NSE industry classification).
    """
    if not dataset_path.exists() or not observation_dir.exists():
        raise ReplayDatasetUnavailable(
            "historical replay dataset not built -- run `python -m scripts.build_replay_dataset`"
        )
    outcomes: dict[str, ResearchOutcomeStatus] = {}
    sectors: dict[str, str] = {}
    with dataset_path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            observation_id = str(row["observation_id"])
            outcomes[observation_id] = ResearchOutcomeStatus(row["happened_after"]["reference_status_plus_5d"])
            sectors[observation_id] = str(row["knew_then"].get("sector_context") or "UNKNOWN")
    observations = [
        o for o in await JsonlResearchOutcomeRepository(observation_dir).query_all_observations()
        if o.observation_id in outcomes
    ]
    note = None
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        note = (
            f"Source: real Upstox M15 price history replayed through the unmodified engine -- "
            f"{summary.get('symbols')} symbols, {summary.get('sessions')} sessions, {summary.get('bars_evaluated')} bars, "
            f"{summary.get('raw_bar_observations')} per-bar observations collapsed into {summary.get('episodes')} episodes "
            f"(contiguous bars of one setup count once). Data ends {str(summary.get('dataset_as_of', ''))[:10]}; "
            "outcomes are read at +5 trading sessions, so the most recent observations are still PENDING."
        )
    return build_pattern_aggregation(
        observations, outcomes, as_of=as_of, source="REPLAY",
        extra_dimensions={
            "MARKET_CONTEXT": lambda o: o.market_context or "UNKNOWN",
            "SECTOR": lambda o: sectors.get(o.observation_id, "UNKNOWN"),
            # Only the replay view carries enough elapsed time for this to
            # say anything -- the live journal spans days, not quarters.
            "TIME_WINDOW": segment_by_calendar_quarter,
        },
        provenance_note=note,
    )


class ReplayDatasetRowsView(BaseModel):
    """Release gate (Sections 10/21) -- individual historical observations
    from the replay dataset, for comparison. Each row keeps the dataset's
    own two halves apart: `knew_then` (recorded at the observation instant)
    and `happened_after` (outcome horizons). Rows are returned in dataset
    order (symbol, then time) -- never ranked by outcome."""

    total_rows: int
    matched_rows: int
    returned_rows: int
    rows: list[dict[str, object]] = Field(default_factory=list)


def read_replay_dataset_rows(
    dataset_path: Path, *, symbol: str | None = None, status: str | None = None, direction: str | None = None,
    limit: int = 50,
) -> ReplayDatasetRowsView:
    """Pure filter over the persisted dataset file -- no recomputation, so
    a row here is byte-for-byte what the dataset recorded."""
    if not dataset_path.exists():
        raise ReplayDatasetUnavailable(
            "historical replay dataset not built -- run `python -m scripts.build_replay_dataset`"
        )
    wanted_symbol = symbol.strip().upper() if symbol else None
    wanted_status = status.strip().upper() if status else None
    wanted_direction = direction.strip().upper() if direction else None
    total = 0
    matched: list[dict[str, object]] = []
    matched_count = 0
    with dataset_path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            total += 1
            row = json.loads(line)
            knew_then = row["knew_then"]
            if wanted_symbol and str(knew_then.get("symbol", "")).upper() != wanted_symbol:
                continue
            if wanted_direction and str(knew_then.get("direction", "")).upper() != wanted_direction:
                continue
            if wanted_status and str(row["happened_after"].get("reference_status_plus_5d", "")).upper() != wanted_status:
                continue
            matched_count += 1
            if len(matched) < max(0, limit):
                matched.append(row)
    return ReplayDatasetRowsView(total_rows=total, matched_rows=matched_count, returned_rows=len(matched), rows=matched)
