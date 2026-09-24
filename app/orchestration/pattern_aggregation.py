"""95% sprint, Sprint 2 -- deterministic historical PATTERN AGGREGATION:
"when this named pattern appeared historically, what happened
afterward?" A lightweight, read-only summary over already-persisted
`ResearchObservation`s and their already-computed outcomes -- no new
evidence computation, no probability, no win rate, no expected return,
no BUY/SELL. Reuses `ResearchOutcomeStatus` (`app.domain.audit
.research_models`) verbatim as the one shared outcome vocabulary for
BOTH live and replay observations, rather than inventing a second one.

Live and replay observations track outcomes through two different
mechanisms (a live observation's outcome comes from real, later-captured
`ResearchOutcomeCheckpoint`s -- `app.orchestration.research_outcome`; a
replay observation's comes from `app.orchestration.outcome_horizons`,
computed directly from the local candle series already available at
generation time) -- this module does not re-implement either. It only
aggregates whatever `ResearchOutcomeStatus` each observation's own
outcome mechanism already determined, via a small adapter function per
source (`outcome_for_live_observation()` / `outcome_for_replay_observation()`),
so the counting logic itself (`aggregate_by_pattern()`) is pure, source-
agnostic, and trivially testable without any I/O.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime

from app.domain.audit.research_models import (
    ResearchObservation,
    ResearchOutcomeStatus,
)
from app.domain.market.models import Candle
from app.orchestration.outcome_horizons import (
    OutcomeHorizonLabel,
    compute_price_path_outcome,
    horizon_target_timestamp,
    resolve_first_event,
)
from app.orchestration.research_outcome import summarize_research_outcome
from app.persistence.interfaces import ResearchOutcomeRepository
from app.utils.time import to_ist


@dataclass(frozen=True)
class PatternAggregate:
    """One named pattern's real, counted history -- descriptive only.
    Every count is an exact tally over real observations; nothing here is
    a rate, percentage, or probability (Section 9: never turn this into
    "probability of profit"/"win rate"/"confidence %")."""

    pattern: str
    observations: int
    follow_through_observed: int
    no_follow_through: int
    failed_setup: int
    pending: int
    insufficient_outcome_data: int
    # Real symbols this pattern was observed on, for a reader who wants
    # to look at the underlying observations themselves -- never a
    # ranking, never sorted by any notion of "best."
    symbols: tuple[str, ...] = field(default_factory=tuple)


def aggregate_by_pattern(
    observations: list[ResearchObservation], outcomes: dict[str, ResearchOutcomeStatus],
) -> list[PatternAggregate]:
    """Pure, deterministic, no I/O. `outcomes` maps
    `observation_id -> ResearchOutcomeStatus`, already resolved by a
    caller (live checkpoints or replay horizons -- see this module's own
    docstring) -- an observation missing from `outcomes` is treated as
    `PENDING` (its outcome genuinely has not been determined yet), never
    silently dropped from the count. Observations with `pattern is None`
    or `pattern == "NONE"` are excluded -- there is no named pattern to
    aggregate by (mirrors `build_price_only_observation()`'s own "WATCH +
    pattern NONE is never a developing setup" rule). Sorted by pattern
    name for a stable, reproducible report -- never by any notion of
    "best performing."
    """
    by_pattern: dict[str, list[ResearchObservation]] = {}
    for obs in observations:
        if obs.pattern is None or obs.pattern == "NONE":
            continue
        by_pattern.setdefault(obs.pattern, []).append(obs)

    results: list[PatternAggregate] = []
    for pattern in sorted(by_pattern):
        group = by_pattern[pattern]
        counts = {status: 0 for status in ResearchOutcomeStatus}
        for obs in group:
            status = outcomes.get(obs.observation_id, ResearchOutcomeStatus.PENDING)
            counts[status] += 1
        results.append(
            PatternAggregate(
                pattern=pattern, observations=len(group),
                follow_through_observed=counts[ResearchOutcomeStatus.FOLLOW_THROUGH_OBSERVED],
                no_follow_through=counts[ResearchOutcomeStatus.NO_FOLLOW_THROUGH],
                failed_setup=counts[ResearchOutcomeStatus.FAILED_SETUP],
                pending=counts[ResearchOutcomeStatus.PENDING],
                insufficient_outcome_data=counts[ResearchOutcomeStatus.INSUFFICIENT_OUTCOME_DATA],
                symbols=tuple(sorted({obs.symbol for obs in group})),
            )
        )
    return results


def aggregate_by_pattern_segmented(
    observations: list[ResearchObservation], outcomes: dict[str, ResearchOutcomeStatus], *,
    segment_by: Callable[[ResearchObservation], str],
) -> dict[str, list[PatternAggregate]]:
    """Final 95% sprint (Section 8) -- the SAME counting as
    `aggregate_by_pattern()`, split first by a caller-supplied segment
    key, then by pattern within each segment. Purely a partition step:
    every count inside each segment is produced by `aggregate_by_pattern()`
    itself, never a second counting implementation, so segmenting can
    never disagree with the unsegmented total (`sum` of a segment's
    counts for a pattern, across all segments, always equals that
    pattern's own `aggregate_by_pattern()` total). Still purely
    descriptive -- Section 9's "never a rate/probability/confidence"
    applies identically inside a segment as it does overall.

    Segmented ONLY by fields genuinely present and structured on
    `ResearchObservation` today (`segment_by_direction`/
    `segment_by_timing_stage`/`segment_by_evidence_completeness` below) --
    market regime (would need a real index/breadth classification
    threaded onto the observation, not just `market_context`'s free
    text) and sector (would need a real symbol->sector join, not
    currently stored on the observation) are deliberately NOT segmented
    here rather than guessed from an unstructured field (Section 8:
    "segment... where data allows" -- not where it doesn't).
    """
    by_segment: dict[str, list[ResearchObservation]] = {}
    for obs in observations:
        by_segment.setdefault(segment_by(obs), []).append(obs)
    return {segment: aggregate_by_pattern(obs_list, outcomes) for segment, obs_list in sorted(by_segment.items())}


def segment_by_direction(observation: ResearchObservation) -> str:
    """BULLISH / BEARISH -- the real thesis direction already recorded."""
    return observation.direction


def segment_by_timing_stage(observation: ResearchObservation) -> str:
    """The real, already-computed early-stage/timing classification
    (RANGE_BOUND / EARLY_DIRECTIONAL_BUILD / ... for a live, contract-based
    observation; the price-only `response.research_state` vocabulary --
    EARLY_SETUP / WATCH / ... -- for a replay observation; see
    `build_research_observation()`/`build_price_only_observation()`'s own
    docstrings for exactly which). Never re-derived here -- this function
    only reads the field."""
    return observation.early_stage_state


def segment_by_calendar_quarter(observation: ResearchObservation) -> str:
    """The real IST calendar quarter the observation was made in --
    `2026-Q2`, `2026-Q3`, and so on.

    Section 30's "more independent time windows" made inspectable rather
    than asserted. A sample drawn from one continuous stretch of market
    can look consistent purely because it is one regime wearing many
    dates; splitting the SAME counts by the period they came from lets a
    reader see whether a pattern's history is spread across the sample or
    concentrated in one quarter of it. Calendar quarters are used rather
    than "halves of whatever was collected" so the boundaries do not move
    when the dataset grows, and so two builds of different sizes remain
    comparable.

    Still purely descriptive: this partitions existing counts, it never
    compares quarters, ranks them, or computes a rate within one.
    """
    ist = to_ist(observation.generated_at)
    return f"{ist.year}-Q{(ist.month - 1) // 3 + 1}"


def segment_by_evidence_completeness(observation: ResearchObservation) -> str:
    """Whether real historical derivatives evidence (option chain/futures)
    was available for this observation -- the honest
    PRICE_HISTORY_AVAILABLE-vs-DERIVATIVES_HISTORY_AVAILABLE distinction
    (Section 25), read directly from the already-recorded
    `derivatives_evidence_available` flag, never re-derived."""
    return "DERIVATIVES_EVIDENCE_AVAILABLE" if observation.derivatives_evidence_available else "PRICE_ONLY_EVIDENCE"


async def outcome_for_live_observation(
    observation: ResearchObservation, *, outcome_repository: ResearchOutcomeRepository, as_of: datetime,
) -> ResearchOutcomeStatus:
    """The real, already-persisted outcome for a LIVE observation --
    exactly `summarize_research_outcome()`'s own result over whatever
    real `ResearchOutcomeCheckpoint`s exist for it so far. Never
    recomputed, never a new evidence pass."""
    checkpoints = await outcome_repository.query_checkpoints_for_observation(observation.observation_id)
    return summarize_research_outcome(observation, checkpoints, as_of=as_of).outcome_status


# The reference horizon a replay observation's outcome is read from --
# +5 real trading sessions, matching the live sweep's own furthest
# checkpoint (`RESEARCH_CHECKPOINT_SESSIONS_AHEAD`'s largest value), so
# the two aggregation paths describe a comparable amount of elapsed time.
_REPLAY_REFERENCE_HORIZON = OutcomeHorizonLabel.PLUS_5D


def outcome_for_replay_observation(observation: ResearchObservation, candles: list[Candle], *, as_of: datetime) -> ResearchOutcomeStatus:
    """The real outcome for a REPLAY observation, read from
    `app.orchestration.outcome_horizons` at the +5-session reference
    horizon -- `candles` must be the real local M15 series for
    `observation.symbol` (the same series a replay run already holds).
    Deterministic translation from that module's own
    already-computed facts, never a new evidence computation:

    - horizon not yet reached / local series doesn't reach that far
      -> `PENDING` (a due-but-not-yet-determinable outcome) if the
      target instant is still ahead of `as_of`, else
      `INSUFFICIENT_OUTCOME_DATA` (the target instant has passed but the
      local candle store doesn't reach that far).
    - the observation's own recorded INVALIDATION-side supporting level
      (final 95% sprint, Section 6 -- genuinely tracked ONLY for
      `PRE_BREAKOUT_COMPRESSION` observations; see
      `ResearchObservation.invalidation_level_kind`'s own docstring) and
      its recorded CONFIRMATION (contractual expiry breakeven reached, OR
      the recorded opposing level broken through -- see
      `outcome_horizons._opposing_level_broken_through()`) are resolved
      by WHICH WAS CROSSED FIRST:
        - invalidation crossed, and confirmation never or later ->
          `FAILED_SETUP`;
        - confirmation crossed, and invalidation never or later ->
          `FOLLOW_THROUGH_OBSERVED` (a later reversal stays visible in
          the per-horizon facts; it does not rewrite what happened first);
        - both crossed inside the SAME M15 bar -> the order is not
          knowable from M15 data -> `INSUFFICIENT_OUTCOME_DATA`, never a
          guess in either direction.
      Release gate (Section 12) correctness fix: this previously checked
      invalidation first over the WHOLE +5-session window, so a setup that
      broke out on day 1 and retraced through its old floor on day 4 was
      counted as FAILED. On the real 15-symbol replay dataset that rule
      labelled 661 of 894 determined episodes FAILED for crossing both.
    - reached the horizon without either -> `NO_FOLLOW_THROUGH`.

    For every observation that does NOT carry a genuine invalidation
    level (every pattern besides `PRE_BREAKOUT_COMPRESSION`, and every
    observation written before that field existed),
    `outcome.invalidation_outcome` is honestly `UNKNOWN` (never guessed --
    Section 7: "Otherwise: UNKNOWN. Never guess."), so `FAILED_SETUP` is
    simply never reached for those -- the same conservative behavior this
    function had before the Section 6 fix.
    """
    outcome = compute_price_path_outcome(observation, _REPLAY_REFERENCE_HORIZON, candles, as_of=as_of)
    if not outcome.data_sufficient:
        target = horizon_target_timestamp(observation, _REPLAY_REFERENCE_HORIZON)
        return ResearchOutcomeStatus.PENDING if target > as_of else ResearchOutcomeStatus.INSUFFICIENT_OUTCOME_DATA
    return resolve_first_event(outcome)


async def build_pattern_aggregation_for_replay(
    outcome_repository: ResearchOutcomeRepository, *, candles_by_symbol: Callable[[str], Awaitable[list[Candle]]], as_of: datetime,
) -> list[PatternAggregate]:
    """Convenience entry point for a replay-only outcome repository
    (Section 9's worked example, `PRE_BREAKOUT_COMPRESSION`): resolves
    every real persisted observation's outcome via
    `outcome_for_replay_observation()` and aggregates. `candles_by_symbol`
    is caller-supplied (typically `candle_repository.query(...)` for the
    symbol's full local series) so this module never constructs its own
    repository/provider."""
    observations = await outcome_repository.query_all_observations()
    outcomes: dict[str, ResearchOutcomeStatus] = {}
    candle_cache: dict[str, list[Candle]] = {}
    for obs in observations:
        if obs.symbol not in candle_cache:
            candle_cache[obs.symbol] = await candles_by_symbol(obs.symbol)
        outcomes[obs.observation_id] = outcome_for_replay_observation(obs, candle_cache[obs.symbol], as_of=as_of)
    return aggregate_by_pattern(observations, outcomes)
