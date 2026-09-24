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

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from pydantic import BaseModel, Field

from app.domain.audit.research_models import (
    RESEARCH_CHECKPOINT_SESSIONS_AHEAD,
    HorizonState,
    ResearchCheckpointLabel,
    ResearchHorizonProgress,
    ResearchObservation,
    ResearchOutcomeCheckpoint,
    ResearchOutcomeStatus,
    ResearchOutcomeSummary,
    ResearchProgression,
)
from app.orchestration.daily_research import _GatedCandidate, classify_early_stage_state
from app.orchestration.dashboard_service import run_analysis
from app.orchestration.outcome_horizons import (
    OutcomeHorizonLabel,
    PriceBar,
    compute_price_path_outcome_from_bars,
)
from app.orchestration.outcome_sessions import (
    horizon_close_utc,
    horizon_session_date,
    next_session_open_after,
    session_close_utc,
    target_trading_session_date,
)
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

_LOG = logging.getLogger(__name__)

# `target_trading_session_date` now lives in `outcome_sessions` (one session
# definition for replay and the forward sweep); re-exported here because
# callers and tests import it from this module.
__all__ = ["target_trading_session_date"]

_HORIZON_BY_LABEL: dict[ResearchCheckpointLabel, OutcomeHorizonLabel] = {
    ResearchCheckpointLabel.PLUS_1_SESSION: OutcomeHorizonLabel.PLUS_1D,
    ResearchCheckpointLabel.PLUS_3_SESSIONS: OutcomeHorizonLabel.PLUS_3D,
    ResearchCheckpointLabel.PLUS_5_SESSIONS: OutcomeHorizonLabel.PLUS_5D,
}


class CheckpointDataUnavailable(RuntimeError):
    """The re-analysis a checkpoint is built from failed outright (provider
    or quote error). Nothing is persisted, so a later sweep retries instead of
    freezing an infrastructure hole into the append-only journal as if it were
    an outcome."""


def due_research_checkpoints(
    observation: ResearchObservation, captured: set[ResearchCheckpointLabel], *, as_of: datetime,
) -> list[ResearchCheckpointLabel]:
    """Which checkpoints have NOT yet been captured and whose horizon has
    genuinely been reached: `as_of` is at or after the CLOSE of the target
    trading session (`outcome_sessions.horizon_close_utc`) -- the same
    instant replay's horizon ends at. Merely reaching the target DATE is not
    enough: a checkpoint captured mid-session would be frozen (the journal is
    append-only) with a partial session behind it. Never a future horizon
    treated as already due (look-ahead by construction)."""
    return [
        label
        for label, sessions_ahead in RESEARCH_CHECKPOINT_SESSIONS_AHEAD.items()
        if label not in captured and as_of >= horizon_close_utc(observation, sessions_ahead)
    ]


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
    """One horizon's checkpoint for `observation`. The observation is only
    ever READ: T0 (price, contract identity, evidence availability, state,
    pattern, levels, provenance) is frozen and nothing here can write to it.

    PRICE FACTS are horizon-bounded and shared with replay: the analysis's
    M15 series is reduced to `PriceBar`s and passed through
    `compute_price_path_outcome_from_bars`, which reads only bars from T0 up
    to the target session's close. So MFE/MAE, level-broken, breakeven,
    confirmation/invalidation, their first-crossing bars and the close at the
    horizon are identical whether this sweep runs at the horizon or sessions
    later, and never see a later session's data. They describe the
    UNDERLYING's price path from the T0 spot (the existing architecture) --
    not option P&L; the T0 contract identity is used only for its recorded
    breakeven/levels and is never swapped for today's expiry/strike/right.

    STATE FACTS (`next_observed_early_stage_state`, `became_extended`,
    `progression`) come from a fresh analysis at `as_of`. They describe the
    horizon only when no later session has begun, so a checkpoint captured
    after the next session opened (`captured_late`) withholds them (`None` /
    UNKNOWN) instead of presenting a later moment as the horizon. They are
    withheld too when the horizon's price path is not determinable, so missing
    data is never read as "no follow-through".

    Raises `ValueError` before the horizon session has closed and
    `CheckpointDataUnavailable` when the re-analysis itself failed (nothing is
    persisted, so it is retried)."""
    sessions_ahead = RESEARCH_CHECKPOINT_SESSIONS_AHEAD[label]
    target = horizon_session_date(observation, sessions_ahead)
    horizon_close = session_close_utc(target)
    if as_of < horizon_close:
        raise ValueError(
            f"checkpoint {label.value} is not due yet: as_of={as_of.isoformat()} "
            f"target_session_close={horizon_close.isoformat()}"
        )
    captured_late = as_of >= next_session_open_after(target)

    response = await run_analysis(
        observation.symbol, provider=provider, instrument_master=instrument_master, strategy=strategy,
        repositories=repositories, config=config, as_of=as_of, mcx_instrument_master=mcx_instrument_master, journal=journal,
        nifty50_symbols=nifty50_symbols, http_client=http_client, delivery_cache_dir=delivery_cache_dir,
    )
    if response.error is not None:
        raise CheckpointDataUnavailable(f"{observation.symbol}: re-analysis failed: {response.error}")

    v = response.visual
    dc = v.direction_comparison if v else None
    observation_spot = Decimal(observation.spot_at_observation) if observation.spot_at_observation is not None else None

    bars = (
        [PriceBar(timestamp=c.timestamp, high=c.high, low=c.low, close=c.close) for c in v.price_chart.candles]
        if v is not None and v.price_chart is not None else []
    )
    price = compute_price_path_outcome_from_bars(observation, _HORIZON_BY_LABEL[label], bars, as_of=as_of)
    determined = price.data_sufficient

    spot_at_checkpoint = price.subsequent_close if determined else None
    move_pct = None
    if spot_at_checkpoint is not None and observation_spot is not None and observation_spot != 0:
        move_pct = (spot_at_checkpoint - observation_spot) / observation_spot * Decimal(100)

    swing_broken = _level_broken(
        kind=observation.nearest_level_kind, value=observation.nearest_level_value, direction=observation.direction,
        window_high=price.subsequent_high, window_low=price.subsequent_low,
    )
    breakeven_reached = _breakeven_reached(
        breakeven=observation.contractual_expiry_breakeven, right=observation.selected_right,
        window_high=price.subsequent_high, window_low=price.subsequent_low,
    )

    next_state: str | None = None
    notes: list[str] = []
    if captured_late:
        notes.append(
            "captured after the next session opened: price facts are bounded to the horizon; the state "
            "at the horizon cannot be reconstructed, so state-derived fields are withheld"
        )
    elif not determined:
        # No price path for this horizon means nothing can honestly be said about
        # progression: a classification from whatever thin data exists would read as
        # "no follow-through" (i.e. no move) for data that is simply missing.
        notes.append("state-derived fields withheld: the horizon's price path is not determinable")
    else:
        contract = None
        # `selected_right` is `None` only for a price-only observation (replay); a forward
        # observation always has a contract, but the check stays explicit.
        if dc is not None and observation.selected_right is not None:
            contract = dc.ce_assessment if observation.selected_right == "CE" else dc.pe_assessment
        if dc is None:
            notes.append("no real direction-comparison data available at this checkpoint")
        elif contract is None:
            notes.append(f"no real {observation.selected_right} contract assessment available at this checkpoint")
        if dc is not None and contract is not None and observation_spot is not None:
            gated = _GatedCandidate(symbol=observation.symbol, response=response, dc=dc, contract=contract, direction=observation.direction)
            next_state, _ = classify_early_stage_state(gated)
    if not determined and price.note is not None:
        notes.append(f"price path not determinable for this horizon: {price.note}")

    return ResearchOutcomeCheckpoint(
        observation_id=observation.observation_id, checkpoint_label=label, target_trading_session_date=target,
        captured_at=as_of, data_state=response.market_state,
        spot_at_checkpoint=str(spot_at_checkpoint) if spot_at_checkpoint is not None else None,
        move_pct_from_observation=str(move_pct) if move_pct is not None else None,
        max_favorable_move_pct=str(price.max_favorable_move_pct) if price.max_favorable_move_pct is not None else None,
        max_adverse_move_pct=str(price.max_adverse_move_pct) if price.max_adverse_move_pct is not None else None,
        swing_level_broken=swing_broken, breakeven_reached=breakeven_reached,
        became_extended=(next_state in _EXTENDED_STATES) if next_state is not None else None,
        next_observed_early_stage_state=next_state,
        progression=_classify_progression(observation.early_stage_state, next_state),
        note="; ".join(notes) if notes else None,
        evaluated_through=horizon_close,
        confirmation_outcome=price.confirmation_outcome.value if determined else None,
        invalidation_outcome=price.invalidation_outcome.value if determined else None,
        first_confirmation_at=price.first_confirmation_at, first_invalidation_at=price.first_invalidation_at,
        captured_late=captured_late,
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
    symbol).

    Safely repeatable: a horizon that already has a checkpoint is never
    recomputed, and persistence itself refuses a second checkpoint for the
    same (observation, horizon) (`save_checkpoint_once`), so a retry, a
    restart or two overlapping sweeps yield exactly one record per horizon.
    A horizon whose re-analysis failed outright is skipped (nothing is
    written) and retried by a later sweep. Only the returned checkpoints were
    newly written."""
    existing = await outcome_repository.query_checkpoints_for_observation(observation.observation_id)
    captured = {c.checkpoint_label for c in existing}
    due = due_research_checkpoints(observation, captured, as_of=as_of)

    newly_captured: list[ResearchOutcomeCheckpoint] = []
    for label in due:
        try:
            checkpoint = await capture_research_outcome_checkpoint(
                label, observation, as_of=as_of, provider=provider, instrument_master=instrument_master,
                strategy=strategy, repositories=repositories, config=config,
                mcx_instrument_master=mcx_instrument_master, journal=journal,
                nifty50_symbols=nifty50_symbols, http_client=http_client, delivery_cache_dir=delivery_cache_dir,
            )
        except CheckpointDataUnavailable as exc:
            _LOG.warning("outcome checkpoint %s skipped, will retry: %s", label.value, exc)
            continue
        if await outcome_repository.save_checkpoint_once(checkpoint):
            newly_captured.append(checkpoint)
    return newly_captured


@dataclass(frozen=True)
class OutcomeSweepResult:
    """What one full sweep did, for observability. Every count is derived from
    the canonical due rule (`due_research_checkpoints`) and the persisted
    checkpoints -- nothing here decides due-ness or outcomes."""

    as_of: datetime
    observations_total: int
    observations_examined: int  # persisted observations that, when the sweep began, did not yet hold all three horizons
    observations_still_awaiting: int  # ... and still do not after it (a pending or unresolved horizon, or a failure)
    observations_failed: int  # an exception while sweeping one observation (isolated; the rest proceeded)
    horizons_created: int
    horizons_created_insufficient: int  # created, but the horizon's price data was not available
    horizons_already_complete: int  # among examined observations: horizons that already had a checkpoint
    horizons_not_yet_due: int  # their horizon session has not closed yet
    horizons_due_unresolved: int  # due, but nothing persisted (re-analysis failed) -- retried next sweep
    insufficient_horizons_total: int  # every persisted checkpoint whose price data was insufficient
    checkpoints: tuple[ResearchOutcomeCheckpoint, ...] = ()
    errors: tuple[str, ...] = ()  # exception type names only
    malformed_lines: int = 0  # persisted lines that did not parse (skipped by reads; never silently ignored)


async def sweep_all_due_research_outcomes_detailed(
    *, as_of: datetime, provider: UpstoxProvider, instrument_master: list[dict[str, object]],
    strategy: EMAVWAPAlignmentStrategy, repositories: Repositories, config: PipelineConfig,
    outcome_repository: JsonlResearchOutcomeRepository,
    mcx_instrument_master: list[dict[str, object]] | None = None,
) -> OutcomeSweepResult:
    """Every persisted observation that could still have a checkpoint, swept
    once each (`query_observations_due_for_sweep` -> `sweep_due_research_outcomes`),
    with per-observation failure isolation: one observation's unexpected error
    is recorded and the remaining observations are still processed. `as_of` is
    whatever the caller passes (the scheduler passes the real clock); due-ness
    is decided only by the canonical outcome engine. Safe to call as often as
    desired -- existing checkpoints are never recomputed or duplicated."""
    observations = await outcome_repository.query_all_observations()
    existing_all = await outcome_repository.query_all_checkpoints()
    labels_by_observation: dict[str, set[ResearchCheckpointLabel]] = {}
    for checkpoint in existing_all:
        labels_by_observation.setdefault(checkpoint.observation_id, set()).add(checkpoint.checkpoint_label)

    created: list[ResearchOutcomeCheckpoint] = []
    errors: list[str] = []
    already_complete = not_yet_due = due_unresolved = still_awaiting = 0
    awaiting = await outcome_repository.query_observations_due_for_sweep(as_of=as_of)
    for observation in awaiting:
        before = labels_by_observation.get(observation.observation_id, set())
        try:
            newly = await sweep_due_research_outcomes(
                observation, as_of=as_of, provider=provider, instrument_master=instrument_master, strategy=strategy,
                repositories=repositories, config=config, outcome_repository=outcome_repository,
                mcx_instrument_master=mcx_instrument_master,
            )
        except Exception as exc:  # noqa: BLE001 -- one observation must never stop the others; nothing was written for it
            _LOG.warning("outcome sweep failed for observation %s: %s", observation.observation_id, type(exc).__name__)
            errors.append(type(exc).__name__)
            still_awaiting += 1
            continue
        created.extend(newly)
        newly_labels = {c.checkpoint_label for c in newly}
        if (set(before) | newly_labels) != set(RESEARCH_CHECKPOINT_SESSIONS_AHEAD):
            still_awaiting += 1
        due_before = set(due_research_checkpoints(observation, set(before), as_of=as_of))
        already_complete += len(before)
        due_unresolved += len(due_before - newly_labels)
        not_yet_due += len(set(RESEARCH_CHECKPOINT_SESSIONS_AHEAD) - before - due_before)

    insufficient_created = [c for c in created if c.spot_at_checkpoint is None]
    insufficient_total = sum(1 for c in existing_all if c.spot_at_checkpoint is None) + len(insufficient_created)
    return OutcomeSweepResult(
        as_of=as_of, observations_total=len(observations), observations_examined=len(awaiting),
        observations_still_awaiting=still_awaiting, observations_failed=len(errors), horizons_created=len(created), horizons_created_insufficient=len(insufficient_created),
        horizons_already_complete=already_complete, horizons_not_yet_due=not_yet_due, horizons_due_unresolved=due_unresolved,
        insufficient_horizons_total=insufficient_total, checkpoints=tuple(created), errors=tuple(errors),
        malformed_lines=await outcome_repository.count_malformed_lines(),
    )


async def sweep_all_due_research_outcomes(
    *, as_of: datetime, provider: UpstoxProvider, instrument_master: list[dict[str, object]],
    strategy: EMAVWAPAlignmentStrategy, repositories: Repositories, config: PipelineConfig,
    outcome_repository: JsonlResearchOutcomeRepository,
    mcx_instrument_master: list[dict[str, object]] | None = None,
) -> list[ResearchOutcomeCheckpoint]:
    """The newly written checkpoints of `sweep_all_due_research_outcomes_detailed`."""
    result = await sweep_all_due_research_outcomes_detailed(
        as_of=as_of, provider=provider, instrument_master=instrument_master, strategy=strategy,
        repositories=repositories, config=config, outcome_repository=outcome_repository,
        mcx_instrument_master=mcx_instrument_master,
    )
    return list(result.checkpoints)


def build_horizon_progress(
    observation: ResearchObservation, checkpoints: list[ResearchOutcomeCheckpoint], *, as_of: datetime,
) -> list[ResearchHorizonProgress]:
    """T0 -> +1 -> +3 -> +5 with each horizon's honest status. Pure and
    deterministic; derived from the persisted checkpoints only -- it creates
    no placeholder checkpoint and never marks an observation failed merely
    because a horizon is still ahead."""
    by_label: dict[ResearchCheckpointLabel, ResearchOutcomeCheckpoint] = {}
    for checkpoint in sorted(checkpoints, key=lambda c: c.captured_at):
        by_label.setdefault(checkpoint.checkpoint_label, checkpoint)  # first persisted wins
    view: list[ResearchHorizonProgress] = []
    for label, sessions_ahead in RESEARCH_CHECKPOINT_SESSIONS_AHEAD.items():
        target = horizon_session_date(observation, sessions_ahead)
        available_from = session_close_utc(target)
        reached = as_of >= available_from
        recorded = by_label.get(label)
        if recorded is None:
            state = HorizonState.PENDING
            detail = "horizon session has closed; awaiting the outcome sweep" if reached else "horizon session has not closed yet"
        elif recorded.spot_at_checkpoint is not None:
            state = HorizonState.AVAILABLE
            detail = "checkpoint captured" + (
                " (after the next session opened; state-derived fields withheld)" if recorded.captured_late else ""
            )
        else:
            state = HorizonState.INSUFFICIENT
            detail = "checkpoint captured but the price data needed to evaluate this horizon was not available"
        view.append(ResearchHorizonProgress(
            checkpoint_label=label, sessions_ahead=sessions_ahead, target_trading_session_date=target,
            available_from=available_from, state=state, reached=reached,
            captured_at=recorded.captured_at if recorded is not None else None, detail=detail,
        ))
    return view


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
    # Sprint 3.4 -- T0 -> +1 -> +3 -> +5 with each horizon's honest status.
    horizons: list[ResearchHorizonProgress] = Field(default_factory=list)


async def build_research_outcome_detail(
    outcome_repository: ResearchOutcomeRepository, observation_id: str, *, as_of: datetime,
) -> ResearchOutcomeDetailView | None:
    observation = await outcome_repository.get_observation(observation_id)
    if observation is None:
        return None
    checkpoints = await outcome_repository.query_checkpoints_for_observation(observation_id)
    summary = summarize_research_outcome(observation, checkpoints, as_of=as_of)
    return ResearchOutcomeDetailView(
        observation=observation, checkpoints=checkpoints, outcome=summary,
        horizons=build_horizon_progress(observation, checkpoints, as_of=as_of),
    )
