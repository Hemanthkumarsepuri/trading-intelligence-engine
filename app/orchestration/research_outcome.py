"""Sprint 3 — Research OUTCOME TRACKING for the Daily Market Researcher.

Answers a genuinely different question than `daily_research.py`'s own
"what looks promising now" ranking: "was this early-stage classification
actually useful, in hindsight, once real time has genuinely passed."

This is NOT trade execution, NOT a backtest, NOT ML, NOT a predictive
probability, and NEVER modifies the original research record -- see
`app.domain.audit.research_models`'s own module docstring for the full
immutability/no-look-ahead discipline this mirrors from the existing
single-symbol live-operation checkpoint system
(`app.orchestration.audit_journal`).

Checkpoints are trading-SESSION-based (+1/+3/+5), never calendar days --
`app.domain.market.trading_calendar.next_trading_day()` is the single
authoritative source for "what is the next real session", reused here
exactly as `daily_research.py`'s own Stage-1 screen and
`app.domain.market.data_state.classify_market_data_state()` already reuse
it for the opposite (backward) direction.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from pydantic import BaseModel, Field

from app.domain.audit.research_models import (
    RESEARCH_CHECKPOINT_SESSIONS_AHEAD,
    ResearchCheckpointLabel,
    ResearchObservation,
    ResearchOutcomeCheckpoint,
    ResearchOutcomeStatus,
    ResearchOutcomeSummary,
    ResearchProgression,
)
from app.domain.market.trading_calendar import NSE_HOLIDAYS, next_trading_day
from app.orchestration.daily_research import _GatedCandidate, classify_early_stage_state
from app.orchestration.dashboard_service import run_analysis
from app.utils.time import to_ist

if TYPE_CHECKING:
    from app.data.providers.upstox_provider import UpstoxProvider
    from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy
    from app.orchestration.options_intelligence_pipeline import PipelineConfig, Repositories
    from app.persistence.interfaces import ResearchOutcomeRepository
    from app.persistence.jsonl_file import (
        JsonlAuditJournalRepository,
        JsonlResearchOutcomeRepository,
    )


def target_trading_session_date(observation_date: date, sessions_ahead: int, holidays: frozenset[date] = NSE_HOLIDAYS) -> date:
    """+1/+3/+5 real trading SESSIONS ahead of `observation_date` --
    "+1 session" always means the next real trading day after
    `observation_date`, regardless of whether the observation itself was
    made mid-session or after that same day's close (a session that has
    already happened cannot be a FUTURE checkpoint for itself)."""
    candidate = observation_date
    for _ in range(sessions_ahead):
        candidate = next_trading_day(candidate, holidays)
    return candidate


def due_research_checkpoints(
    observation: ResearchObservation, captured: set[ResearchCheckpointLabel], *, as_of: datetime,
) -> list[ResearchCheckpointLabel]:
    """Which checkpoints have NOT yet been captured but their target
    trading-session date has genuinely been reached, given the real
    current `as_of`. A checkpoint's target date is a real calendar date
    (session-based, see `target_trading_session_date()`) -- "due" here
    means `as_of`'s own calendar date has reached or passed it; never a
    future date treated as already due (that would be look-ahead by
    construction)."""
    observation_date = observation.generated_at.date()
    due: list[ResearchCheckpointLabel] = []
    for label, sessions_ahead in RESEARCH_CHECKPOINT_SESSIONS_AHEAD.items():
        if label in captured:
            continue
        target = target_trading_session_date(observation_date, sessions_ahead)
        if as_of.date() >= target:
            due.append(label)
    return due


_EARLY_STAGE_MATURITY_RANK: dict[str, int] = {
    "RANGE_BOUND": 0,
    "EARLY_DIRECTIONAL_BUILD": 1,
    "DEVELOPING_MOMENTUM": 2,
    "BREAKOUT_CONFIRMATION": 3,
    "EXTENDED": 4,
    "EXHAUSTION_RISK": 5,
}
_EXTENDED_STATES = frozenset({"EXTENDED", "EXHAUSTION_RISK"})


def _classify_progression(initial_state: str, next_state: str | None) -> ResearchProgression:
    """A small, explicit, deterministic rule set over the REAL ordinal
    "maturity rank" `classify_early_stage_state()` already establishes for
    its 8 states -- never a probability, never a fitted score. Progression
    means the real classification moved to a HIGHER rank (more confirmed)
    than it started at; `FALSE_BREAKOUT_RISK` and `INSUFFICIENT_DATA`
    (real, off-the-ordinal states) are handled as their own explicit
    cases, never silently coerced onto the ordinal."""
    if next_state is None or next_state == "INSUFFICIENT_DATA":
        return ResearchProgression.UNKNOWN
    if next_state == "FALSE_BREAKOUT_RISK":
        return ResearchProgression.FAILED_SETUP
    initial_rank = _EARLY_STAGE_MATURITY_RANK.get(initial_state)
    next_rank = _EARLY_STAGE_MATURITY_RANK.get(next_state)
    if initial_rank is None or next_rank is None:
        return ResearchProgression.UNKNOWN
    if next_rank > initial_rank:
        return ResearchProgression.FOLLOW_THROUGH_OBSERVED
    return ResearchProgression.NO_FOLLOW_THROUGH


def _real_excursion_pct(
    candles: list[object], *, observation_generated_at: datetime, observation_spot: Decimal, direction: str,
) -> tuple[Decimal | None, Decimal | None]:
    """(max_favorable_move_pct, max_adverse_move_pct) computed from the
    REAL M15 candles the checkpoint's own fresh re-analysis already
    fetched (`response.visual.price_chart.candles`) -- never a new fetch.
    Honestly `(None, None)` unless that history genuinely covers the
    WHOLE observation-to-checkpoint window (the oldest available candle
    must be at or before `observation_generated_at`) -- a partial window
    could understate a real excursion, which would be worse than reporting
    nothing."""
    from app.orchestration.visual_data import CandlePoint  # local import: typing-only elsewhere

    typed_candles: list[CandlePoint] = [c for c in candles if isinstance(c, CandlePoint)]
    window = [c for c in typed_candles if c.timestamp >= observation_generated_at]
    if not window or observation_spot == 0:
        return None, None
    if typed_candles and typed_candles[0].timestamp > observation_generated_at:
        return None, None  # history doesn't reach back far enough to cover the whole window
    highest = max(c.high for c in window)
    lowest = min(c.low for c in window)
    if direction == "BULLISH":
        favorable = (highest - observation_spot) / observation_spot * Decimal(100)
        adverse = (observation_spot - lowest) / observation_spot * Decimal(100)
    else:
        favorable = (observation_spot - lowest) / observation_spot * Decimal(100)
        adverse = (highest - observation_spot) / observation_spot * Decimal(100)
    return favorable, adverse


def _level_broken(
    *, kind: str | None, value: str | None, direction: str, window_high: Decimal | None, window_low: Decimal | None,
) -> bool | None:
    if kind is None or value is None or window_high is None or window_low is None:
        return None
    level = Decimal(value)
    if direction == "BULLISH":
        return window_high >= level if kind == "resistance" else window_low <= level
    return window_low <= level if kind == "support" else window_high >= level


def _breakeven_reached(
    *, breakeven: str | None, right: str | None, window_high: Decimal | None, window_low: Decimal | None,
) -> bool | None:
    if breakeven is None or window_high is None or window_low is None:
        return None
    be = Decimal(breakeven)
    return window_high >= be if right == "CE" else window_low <= be


async def capture_research_outcome_checkpoint(
    label: ResearchCheckpointLabel, observation: ResearchObservation, *,
    as_of: datetime, provider: UpstoxProvider, instrument_master: list[dict[str, object]],
    strategy: EMAVWAPAlignmentStrategy, repositories: Repositories, config: PipelineConfig,
    mcx_instrument_master: list[dict[str, object]] | None = None, journal: JsonlAuditJournalRepository | None = None,
    nifty50_symbols: Sequence[str] | None = None,
    http_client: httpx.AsyncClient | None = None,
    delivery_cache_dir: Path | None = None,
) -> ResearchOutcomeCheckpoint:
    """Re-runs the EXACT SAME `run_analysis()` a manual query or the
    original research run used -- real, fresh data, never a second
    analysis engine. Raises `ValueError` if called before the checkpoint
    is genuinely due, mirroring `capture_outcome_checkpoint()`'s own
    precedent in `app.orchestration.audit_journal`."""
    sessions_ahead = RESEARCH_CHECKPOINT_SESSIONS_AHEAD[label]
    target = target_trading_session_date(observation.generated_at.date(), sessions_ahead)
    if as_of.date() < target:
        raise ValueError(f"checkpoint {label.value} is not due yet: as_of={as_of.isoformat()} target_session={target.isoformat()}")

    response = await run_analysis(
        observation.symbol, provider=provider, instrument_master=instrument_master, strategy=strategy,
        repositories=repositories, config=config, as_of=as_of, mcx_instrument_master=mcx_instrument_master, journal=journal,
        nifty50_symbols=nifty50_symbols, http_client=http_client, delivery_cache_dir=delivery_cache_dir,
    )

    v = response.visual
    dc = v.direction_comparison if v else None
    contract = None
    # Phase 3 gap-closure -- `selected_right` is `None` only for a
    # price-only (no derivatives evidence) observation; this live sweep
    # is only ever invoked for observations in the LIVE outcome
    # repository, which never holds one (replay observations live in
    # their own, separate repository -- see `ResearchObservation.source`'s
    # own docstring), but the check stays explicit rather than assuming.
    if dc is not None and observation.selected_right is not None:
        contract = dc.ce_assessment if observation.selected_right == "CE" else dc.pe_assessment

    spot_at_checkpoint = dc.paths.spot if dc is not None and dc.paths else None
    observation_spot = Decimal(observation.spot_at_observation) if observation.spot_at_observation is not None else None

    move_pct = None
    if spot_at_checkpoint is not None and observation_spot is not None and observation_spot != 0:
        move_pct = (spot_at_checkpoint - observation_spot) / observation_spot * Decimal(100)

    next_state: str | None = None
    if dc is not None and contract is not None and observation_spot is not None:
        gated = _GatedCandidate(symbol=observation.symbol, response=response, dc=dc, contract=contract, direction=observation.direction)
        next_state, _ = classify_early_stage_state(gated)

    favorable_pct = adverse_pct = None
    window_high = window_low = None
    if v is not None and v.price_chart is not None and observation_spot is not None:
        candles = v.price_chart.candles
        favorable_pct, adverse_pct = _real_excursion_pct(
            list(candles), observation_generated_at=observation.generated_at, observation_spot=observation_spot,
            direction=observation.direction,
        )
        window = [c for c in candles if c.timestamp >= observation.generated_at]
        if window:
            window_high = max(c.high for c in window)
            window_low = min(c.low for c in window)

    swing_broken = _level_broken(
        kind=observation.nearest_level_kind, value=observation.nearest_level_value, direction=observation.direction,
        window_high=window_high, window_low=window_low,
    )
    breakeven_reached = _breakeven_reached(
        breakeven=observation.contractual_expiry_breakeven, right=observation.selected_right,
        window_high=window_high, window_low=window_low,
    )

    note = None
    if dc is None:
        note = "no real direction-comparison data available at this checkpoint"
    elif contract is None:
        note = f"no real {observation.selected_right} contract assessment available at this checkpoint"

    return ResearchOutcomeCheckpoint(
        observation_id=observation.observation_id, checkpoint_label=label, target_trading_session_date=target,
        captured_at=as_of, data_state=response.market_state,
        spot_at_checkpoint=str(spot_at_checkpoint) if spot_at_checkpoint is not None else None,
        move_pct_from_observation=str(move_pct) if move_pct is not None else None,
        max_favorable_move_pct=str(favorable_pct) if favorable_pct is not None else None,
        max_adverse_move_pct=str(adverse_pct) if adverse_pct is not None else None,
        swing_level_broken=swing_broken, breakeven_reached=breakeven_reached,
        became_extended=(next_state in _EXTENDED_STATES) if next_state is not None else None,
        next_observed_early_stage_state=next_state,
        progression=_classify_progression(observation.early_stage_state, next_state),
        note=note,
    )


async def sweep_due_research_outcomes(
    observation: ResearchObservation, *, as_of: datetime, provider: UpstoxProvider,
    instrument_master: list[dict[str, object]], strategy: EMAVWAPAlignmentStrategy, repositories: Repositories,
    config: PipelineConfig, outcome_repository: JsonlResearchOutcomeRepository,
    mcx_instrument_master: list[dict[str, object]] | None = None, journal: JsonlAuditJournalRepository | None = None,
    nifty50_symbols: Sequence[str] | None = None,
    http_client: httpx.AsyncClient | None = None,
    delivery_cache_dir: Path | None = None,
) -> list[ResearchOutcomeCheckpoint]:
    """One observation's own due checkpoints, captured and persisted --
    mirrors `sweep_due_checkpoints()`'s precedent in
    `app.orchestration.live_operation` (symbol-scoped, one observation at
    a time; a caller sweeping many observations calls this once per
    observation, exactly like that function's own caller does per
    symbol)."""
    existing = await outcome_repository.query_checkpoints_for_observation(observation.observation_id)
    captured = {c.checkpoint_label for c in existing}
    due = due_research_checkpoints(observation, captured, as_of=as_of)

    newly_captured: list[ResearchOutcomeCheckpoint] = []
    for label in due:
        checkpoint = await capture_research_outcome_checkpoint(
            label, observation, as_of=as_of, provider=provider, instrument_master=instrument_master,
            strategy=strategy, repositories=repositories, config=config,
            mcx_instrument_master=mcx_instrument_master, journal=journal,
            nifty50_symbols=nifty50_symbols, http_client=http_client, delivery_cache_dir=delivery_cache_dir,
        )
        await outcome_repository.save_checkpoint(checkpoint)
        newly_captured.append(checkpoint)
    return newly_captured


# ============================================================
# Sprint 5, Objectives 1-4 -- OUTCOME INTELLIGENCE + RESEARCH HISTORY.
# Everything below is a pure, read-only VIEW over already-persisted
# `ResearchObservation`/`ResearchOutcomeCheckpoint` records -- no new
# fetch, no new persisted record type, no probability/score, and no
# effect whatsoever on `daily_research.py`'s ranking (see
# `test_daily_research_ranking.py`'s
# `test_ranking_is_identical_with_and_without_historical_outcome_records`
# for the structural proof: `rank_candidates()`/`_gate()`/`_sort_key()`
# do not import this module and accept no outcome-repository parameter).
# ============================================================


def _session_label(label: ResearchCheckpointLabel) -> str:
    """`"+1"`/`"+3"`/`"+5"` -- the real sessions-ahead count already on
    `RESEARCH_CHECKPOINT_SESSIONS_AHEAD`, formatted for display. Never a
    new number."""
    return f"+{RESEARCH_CHECKPOINT_SESSIONS_AHEAD[label]}"


def summarize_research_outcome(
    observation: ResearchObservation, checkpoints: list[ResearchOutcomeCheckpoint], *, as_of: datetime,
) -> ResearchOutcomeSummary:
    """Objective 1/2/3 -- a deterministic summary across every checkpoint
    captured so far for `observation`, using ONLY each checkpoint's own
    already-persisted `progression` (never recomputed -- see
    `_classify_progression()`, which already ran once, at capture time,
    for each checkpoint). Order of evaluation, matching the Sprint 5 spec
    exactly:

      1. No checkpoints at all -> PENDING (whether or not one is due --
         a due-but-uncaptured checkpoint is a sweep-timing fact, not a
         determined outcome).
      2. Any checkpoint shows FOLLOW_THROUGH_OBSERVED -> that status,
         with the EARLIEST (lowest sessions-ahead) such checkpoint's
         session as `earliest_follow_through_session`.
      3. Else any checkpoint shows FAILED_SETUP -> that status.
      4. Else, if EVERY captured checkpoint is UNKNOWN (no determinate
         evidence at all) -> INSUFFICIENT_OUTCOME_DATA.
      5. Otherwise -> NO_FOLLOW_THROUGH.

    Never alters any checkpoint's own stored `progression` -- this is a
    read-only aggregation, called fresh every time a caller wants it,
    never itself persisted.
    """
    if not checkpoints:
        due = due_research_checkpoints(observation, captured=set(), as_of=as_of)
        if not due:
            explanation = "Outcome remains pending; no checkpoint is due yet."
        else:
            explanation = (
                f"Outcome remains pending; the {_session_label(due[0])} session checkpoint is due "
                "but has not yet been captured."
            )
        return ResearchOutcomeSummary(
            observation_id=observation.observation_id, outcome_status=ResearchOutcomeStatus.PENDING,
            earliest_follow_through_session=None, latest_checkpoint_state=None, explanation=explanation,
        )

    ordered = sorted(checkpoints, key=lambda c: RESEARCH_CHECKPOINT_SESSIONS_AHEAD[c.checkpoint_label])
    latest = max(checkpoints, key=lambda c: c.captured_at)
    latest_checkpoint_state = latest.next_observed_early_stage_state

    follow_throughs = [c for c in ordered if c.progression == ResearchProgression.FOLLOW_THROUGH_OBSERVED]
    if follow_throughs:
        earliest = follow_throughs[0]
        session_label = _session_label(earliest.checkpoint_label)
        return ResearchOutcomeSummary(
            observation_id=observation.observation_id, outcome_status=ResearchOutcomeStatus.FOLLOW_THROUGH_OBSERVED,
            earliest_follow_through_session=session_label, latest_checkpoint_state=latest_checkpoint_state,
            explanation=f"Follow-through observed by {session_label} sessions after the researched {observation.direction.lower()} setup.",
        )

    failed = [c for c in ordered if c.progression == ResearchProgression.FAILED_SETUP]
    if failed:
        state = failed[0].next_observed_early_stage_state or "an unfavorable state"
        return ResearchOutcomeSummary(
            observation_id=observation.observation_id, outcome_status=ResearchOutcomeStatus.FAILED_SETUP,
            earliest_follow_through_session=None, latest_checkpoint_state=latest_checkpoint_state,
            explanation=f"Setup failed: subsequent observation entered {state}.",
        )

    if all(c.progression == ResearchProgression.UNKNOWN for c in ordered):
        return ResearchOutcomeSummary(
            observation_id=observation.observation_id, outcome_status=ResearchOutcomeStatus.INSUFFICIENT_OUTCOME_DATA,
            earliest_follow_through_session=None, latest_checkpoint_state=latest_checkpoint_state,
            explanation="Outcome cannot be determined because required historical data was unavailable.",
        )

    latest_session_label = _session_label(ordered[-1].checkpoint_label)
    return ResearchOutcomeSummary(
        observation_id=observation.observation_id, outcome_status=ResearchOutcomeStatus.NO_FOLLOW_THROUGH,
        earliest_follow_through_session=None, latest_checkpoint_state=latest_checkpoint_state,
        explanation=f"No follow-through observed through the {latest_session_label} session checkpoint.",
    )


class ResearchHistoryEntryView(BaseModel):
    """Objective 4 -- one row of the research history view: the real,
    already-persisted observation plus its freshly-derived outcome
    summary. Reuses `ResearchObservation` directly (already carries
    symbol/direction/early_stage_state/research_confidence/actionability/
    market_context/structural_context/participation_depth/
    pre_breakout_signal -- every field Objective 4 asks the history view
    to expose), matching this codebase's existing convention of reusing
    domain audit models directly as API response fields (see
    `DailyResearchView.rejected: list[RejectionRecord]`)."""

    observation: ResearchObservation
    outcome: ResearchOutcomeSummary


class ResearchHistoryView(BaseModel):
    entries: list[ResearchHistoryEntryView] = Field(default_factory=list)
    matched_count: int = 0


async def build_research_history(
    outcome_repository: ResearchOutcomeRepository, *, as_of: datetime,
    symbol: str | None = None, day: date | None = None, direction: str | None = None,
    outcome_status: str | None = None,
) -> ResearchHistoryView:
    """Objective 4 -- lightweight, deterministic filtering over every
    real persisted observation. Filters are plain equality checks in
    Python over an already-small in-memory list (the same scale
    `JsonlResearchOutcomeRepository` already loads entirely into memory
    for every other query) -- no new index, no new abstraction. Historical
    outcomes are computed here ONLY for display; nothing computed in this
    function is ever read back into `daily_research.py`'s ranking (see
    this module's own header docstring)."""
    observations = await outcome_repository.query_all_observations()
    if symbol is not None:
        canonical_symbol = symbol.strip().upper()
        observations = [o for o in observations if o.symbol == canonical_symbol]
    if day is not None:
        observations = [o for o in observations if to_ist(o.generated_at).date() == day]
    if direction is not None:
        canonical_direction = direction.strip().upper()
        observations = [o for o in observations if o.direction == canonical_direction]

    entries: list[ResearchHistoryEntryView] = []
    for observation in observations:
        checkpoints = await outcome_repository.query_checkpoints_for_observation(observation.observation_id)
        summary = summarize_research_outcome(observation, checkpoints, as_of=as_of)
        if outcome_status is not None and summary.outcome_status.value != outcome_status.strip().upper():
            continue
        entries.append(ResearchHistoryEntryView(observation=observation, outcome=summary))

    entries.sort(key=lambda e: e.observation.generated_at, reverse=True)
    return ResearchHistoryView(entries=entries, matched_count=len(entries))


class ResearchOutcomeDetailView(BaseModel):
    """Objective 12 -- the full detail for one observation:
    `GET /api/research/{observation_id}/outcome`."""

    observation: ResearchObservation
    checkpoints: list[ResearchOutcomeCheckpoint]
    outcome: ResearchOutcomeSummary


async def build_research_outcome_detail(
    outcome_repository: ResearchOutcomeRepository, observation_id: str, *, as_of: datetime,
) -> ResearchOutcomeDetailView | None:
    observation = await outcome_repository.get_observation(observation_id)
    if observation is None:
        return None
    checkpoints = await outcome_repository.query_checkpoints_for_observation(observation_id)
    summary = summarize_research_outcome(observation, checkpoints, as_of=as_of)
    return ResearchOutcomeDetailView(observation=observation, checkpoints=checkpoints, outcome=summary)
