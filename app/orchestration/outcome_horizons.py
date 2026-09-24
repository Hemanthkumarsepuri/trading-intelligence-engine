"""Phase 3 -- price-path outcome horizons computed directly from an
already-available historical candle series, plus CONFIRMATION and
INVALIDATION tracked as two SEPARATE deterministic outcomes (Sections
16-19 of the Phase-3 spec).

This is the REPLAY-specific complement to `app.orchestration
.research_outcome`'s live +1/+3/+5 SESSION sweep. That sweep must re-run
`analyze_symbol()` at real wall-clock time because live data for the
future genuinely doesn't exist yet when the observation is made. For a
replay run the situation is different: the "future" candles the
observation needs already sit in local storage (the replay backfill
covers the whole historical window up front), so every horizon can be
computed once, immediately, straight from that one candle series --
no separate later sweep step, no second network call, and (like every
other read in this codebase) still filtered to `as_of` so a horizon whose
target instant hasn't been reached yet is honestly reported as not
computable rather than silently using data from beyond it.

Never a probability, a score, or a trade result -- `confirmation_outcome`/
`invalidation_outcome` each answer one factual yes/no/unknown question
about whether a specific, already-recorded condition on the ORIGINAL
`ResearchObservation` was actually crossed, nothing more.

95% sprint, Sprint 1 correctness note: `confirmation_outcome` is CONFIRMED
when EITHER the option's own contractual expiry breakeven was reached
(contract-based observations) OR the observation's recorded nearest
OPPOSING level was broken through in the thesis's own favorable
direction (available for a price-only observation too) -- both are real
"the setup did what it needed to" facts; every named pattern's own
documented `confirm_if` text agrees (`app.domain.options.development`).

Final 95% sprint (Section 6) correctness note: `invalidation_outcome` is
now a GENUINE determination (INVALIDATED/NOT_INVALIDATED), not just
UNKNOWN, for the one case where a deterministic invalidation-side level
can be established safely -- `ResearchObservation.invalidation_level_kind`/
`invalidation_level_value`, populated for the patterns whose own
`invalidate_if` text names a specific level: PRE_BREAKOUT_COMPRESSION
(its own supporting structure) and, since 18 Sep 2026,
FAILED_BREAKDOWN_RECLAIM (the reclaimed level itself). It stays honestly
`UNKNOWN` for every other pattern and for observations written before
this field existed -- never guessed from an unrelated fact (Section 7 /
Section 6: "If a deterministic invalidation rule cannot be established
safely: return UNKNOWN. Do not manufacture a rule."). Nothing here reads
the pattern name: this module only reads whether a level was recorded.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum

from app.domain.audit.research_models import ResearchObservation, ResearchOutcomeStatus
from app.domain.market.models import Candle
from app.orchestration.outcome_sessions import horizon_close_utc


class OutcomeHorizonLabel(str, Enum):
    PLUS_30M = "PLUS_30M"
    PLUS_1H = "PLUS_1H"
    PLUS_1D = "PLUS_1D"
    PLUS_3D = "PLUS_3D"
    PLUS_5D = "PLUS_5D"


class ConfirmationOutcome(str, Enum):
    CONFIRMED = "CONFIRMED"
    NOT_CONFIRMED = "NOT_CONFIRMED"
    UNKNOWN = "UNKNOWN"


class InvalidationOutcome(str, Enum):
    INVALIDATED = "INVALIDATED"
    NOT_INVALIDATED = "NOT_INVALIDATED"
    UNKNOWN = "UNKNOWN"


# Calendar-time horizons -- straight offsets from the observation instant.
_INTRADAY_OFFSETS: dict[OutcomeHorizonLabel, timedelta] = {
    OutcomeHorizonLabel.PLUS_30M: timedelta(minutes=30),
    OutcomeHorizonLabel.PLUS_1H: timedelta(hours=1),
}
# Session-count horizons -- real NSE trading sessions, never calendar days
# (Section 7), reusing `research_outcome.py`'s own authoritative function
# rather than a second calendar computation.
_SESSION_OFFSETS: dict[OutcomeHorizonLabel, int] = {
    OutcomeHorizonLabel.PLUS_1D: 1,
    OutcomeHorizonLabel.PLUS_3D: 3,
    OutcomeHorizonLabel.PLUS_5D: 5,
}
# A session-count horizon's target instant is that target session's own
# close (`outcome_sessions.horizon_close_utc`), the deterministic "end of
# session N" instant -- one definition shared with the forward sweep.
# How close the local candle series must reach to a horizon's target
# instant to honestly count as "covers it" -- one M15 bar's worth of
# slack, never treated as reaching all the way to a target it falls short
# of by more than this.
_COVERAGE_TOLERANCE = timedelta(minutes=20)


@dataclass(frozen=True)
class PriceBar:
    """The four facts an outcome needs from one bar. Both paths reduce their
    candles to this (replay: stored `Candle`s; forward sweep: the analysis's
    `CandlePoint`s) so ONE computation defines MFE/MAE, confirmation and
    invalidation for both."""

    timestamp: datetime
    high: Decimal
    low: Decimal
    close: Decimal


@dataclass(frozen=True)
class PricePathOutcome:
    horizon: OutcomeHorizonLabel
    target_timestamp: datetime
    data_sufficient: bool
    subsequent_high: Decimal | None = None
    subsequent_low: Decimal | None = None
    subsequent_close: Decimal | None = None
    max_favorable_move_pct: Decimal | None = None
    max_adverse_move_pct: Decimal | None = None
    confirmation_outcome: ConfirmationOutcome = ConfirmationOutcome.UNKNOWN
    invalidation_outcome: InvalidationOutcome = InvalidationOutcome.UNKNOWN
    note: str | None = None
    # Release gate (Section 12) -- the timestamp of the FIRST M15 bar in the
    # window whose range crossed each level, or `None` when it was never
    # crossed (or cannot be evaluated). Two separate facts, so a reader can
    # tell "confirmed, later invalidated" from "invalidated, never
    # confirmed" -- and so can `outcome_for_replay_observation()`.
    first_confirmation_at: datetime | None = None
    first_invalidation_at: datetime | None = None


def horizon_target_timestamp(observation: ResearchObservation, horizon: OutcomeHorizonLabel) -> datetime:
    """The real instant this horizon targets -- `generated_at + offset` for
    the two intraday horizons, or the target trading session's own 15:30
    IST close for the three session-count horizons."""
    intraday_offset = _INTRADAY_OFFSETS.get(horizon)
    if intraday_offset is not None:
        return observation.generated_at + intraday_offset
    return horizon_close_utc(observation, _SESSION_OFFSETS[horizon])


def _opposing_level_broken_through(
    *, kind: str | None, value: str | None, direction: str, window_high: Decimal, window_low: Decimal,
) -> bool | None:
    """Whether price broke THROUGH the observation's own recorded nearest
    OPPOSING level (`nearest_level_kind`/`nearest_level_value` -- the
    obstacle above spot for a BULLISH thesis, below spot for a BEARISH
    one; see `app.orchestration.daily_research
    ._nearest_opposing_level_in()`/`_nearest_opposing_technical_level()`,
    which are what populate it). `None` (never guessed) when no such
    level was recorded.

    95% sprint, Sprint 1 correctness fix: this event is CONFIRMATION-
    relevant, not invalidation. Every real pattern's own documented
    `confirm_if` text agrees (`app.domain.options.development`, e.g.
    PRE_BREAKOUT_COMPRESSION: "Price holds BEYOND the nearby opposing
    level...") -- holding beyond the obstacle in the thesis's own
    favorable direction is what a breakout achieving its setup looks
    like, not what invalidates it. The previous version of this function
    fed this fact into `InvalidationOutcome` backwards (labeling a real
    breakout above resistance, on a BULLISH thesis, as "invalidated") --
    caught by `tests/unit/orchestration/test_pattern_aggregation.py`
    before it could mislabel a real result.
    """
    if kind is None or value is None:
        return None
    level = Decimal(value)
    if direction == "BULLISH":
        # `kind` is always "resistance" here in practice (the opposing
        # side above spot) -- the `support` branch is dead in production
        # but kept explicit rather than assumed, for any older persisted
        # observation with a differently-populated value.
        return window_low <= level if kind == "support" else window_high >= level
    return window_high >= level if kind == "resistance" else window_low <= level


def _supporting_level_broken_through(
    *, kind: str | None, value: str | None, direction: str, window_high: Decimal, window_low: Decimal,
) -> bool | None:
    """Whether price broke THROUGH the observation's own recorded
    INVALIDATION-side SUPPORTING level (`invalidation_level_kind`/
    `invalidation_level_value` -- the thesis's OWN structure: support
    below spot for a BULLISH thesis, resistance above spot for a
    BEARISH one; see `app.orchestration.daily_research
    ._nearest_supporting_level_in()`/`_nearest_supporting_technical_level()`,
    which are what populate it -- see
    `ResearchObservation.invalidation_level_kind`'s own docstring for
    which patterns record one and why). `None` (never guessed) when no
    such level was recorded -- every pattern that does not name a
    specific level, and every observation written before this field
    existed.

    Final 95% sprint (Section 6) addition -- the genuinely SEPARATE
    counterpart to `_opposing_level_broken_through()` above. That
    function's breach is CONFIRMATION (the obstacle in the thesis's
    favorable direction giving way); this one's breach is INVALIDATION
    (the thesis's OWN supporting floor/ceiling giving way on the WRONG
    side) -- PRE_BREAKOUT_COMPRESSION's own documented `invalidate_if`
    text ("compression expands without a break, the nearby level
    rejects") is exactly this geometry.
    """
    if kind is None or value is None:
        return None
    level = Decimal(value)
    if direction == "BULLISH":
        # `kind` is always "support" here in practice (the thesis's own
        # floor, below spot) -- the `resistance` branch kept explicit
        # rather than assumed, for any differently-populated value.
        return window_low <= level if kind == "support" else window_high >= level
    return window_high >= level if kind == "resistance" else window_low <= level


def _breakeven_reached(*, breakeven: str | None, right: str | None, window_high: Decimal, window_low: Decimal) -> bool | None:
    if breakeven is None:
        return None
    be = Decimal(breakeven)
    return window_high >= be if right == "CE" else window_low <= be


def bars_from_candles(candles: list[Candle]) -> list[PriceBar]:
    return [
        PriceBar(timestamp=c.freshness.data_timestamp, high=c.high, low=c.low, close=c.close)
        for c in sorted(candles, key=lambda c: c.freshness.data_timestamp)
    ]


def compute_price_path_outcome(
    observation: ResearchObservation, horizon: OutcomeHorizonLabel, candles: list[Candle], *, as_of: datetime,
) -> PricePathOutcome:
    return compute_price_path_outcome_from_bars(observation, horizon, bars_from_candles(candles), as_of=as_of)


def compute_price_path_outcome_from_bars(
    observation: ResearchObservation, horizon: OutcomeHorizonLabel, bars: list[PriceBar], *, as_of: datetime,
) -> PricePathOutcome:
    """NO-LOOKAHEAD BY CONSTRUCTION: only bars with `generated_at <= timestamp
    <= target_timestamp` are read, so the result for a horizon is identical
    whether or not later bars exist in `bars` (proved by the adversarial
    tests). MFE/MAE follow the existing direction convention: for a BULLISH
    thesis favorable = (window high - spot) / spot and adverse = (spot -
    window low) / spot; BEARISH mirrors it. They are research measurements
    of the underlying's price path from the T0 spot, not option P&L and not
    a performance claim.

    `bars` must be `observation.symbol`'s own real M15 series (replay: the
    locally persisted series; forward sweep: the analysis's price chart).
    Every early return below is an honest `data_sufficient=False`, never a
    guessed or partial number (Section 16: "only calculate horizons for
    which enough future data exists").
    """
    target_timestamp = horizon_target_timestamp(observation, horizon)
    if target_timestamp > as_of:
        return PricePathOutcome(
            horizon=horizon, target_timestamp=target_timestamp, data_sufficient=False,
            note="target instant has not been reached yet as of this evaluation",
        )
    ordered = sorted(bars, key=lambda b: b.timestamp)
    if not ordered or ordered[0].timestamp > observation.generated_at:
        return PricePathOutcome(
            horizon=horizon, target_timestamp=target_timestamp, data_sufficient=False,
            note="local historical candle series does not reach back to the observation instant",
        )
    window = [c for c in ordered if observation.generated_at <= c.timestamp <= target_timestamp]
    if not window or window[-1].timestamp < target_timestamp - _COVERAGE_TOLERANCE:
        return PricePathOutcome(
            horizon=horizon, target_timestamp=target_timestamp, data_sufficient=False,
            note="local historical candle series does not yet reach this horizon's target instant",
        )
    if observation.spot_at_observation is None:
        return PricePathOutcome(
            horizon=horizon, target_timestamp=target_timestamp, data_sufficient=False,
            note="no real spot_at_observation on the original observation to measure the move from",
        )
    observation_spot = Decimal(observation.spot_at_observation)
    if observation_spot == 0:
        return PricePathOutcome(horizon=horizon, target_timestamp=target_timestamp, data_sufficient=False, note="observation spot was zero")

    window_high = max(c.high for c in window)
    window_low = min(c.low for c in window)
    subsequent_close = window[-1].close
    if observation.direction == "BULLISH":
        favorable = (window_high - observation_spot) / observation_spot * Decimal(100)
        adverse = (observation_spot - window_low) / observation_spot * Decimal(100)
    else:
        favorable = (observation_spot - window_low) / observation_spot * Decimal(100)
        adverse = (window_high - observation_spot) / observation_spot * Decimal(100)

    # 95% sprint, Sprint 1 correctness fix -- CONFIRMATION now combines
    # BOTH real signals this observation might carry: the option's own
    # contractual breakeven (contract-based, live observations) and the
    # recorded opposing level actually being broken through (available
    # for a price-only observation too -- see
    # `_opposing_level_broken_through()`'s own docstring). Either real
    # `True` confirms; both honestly absent/unreached is the only way to
    # land on NOT_CONFIRMED or UNKNOWN.
    breakeven_hit = _breakeven_reached(
        breakeven=observation.contractual_expiry_breakeven, right=observation.selected_right,
        window_high=window_high, window_low=window_low,
    )
    level_hit = _opposing_level_broken_through(
        kind=observation.nearest_level_kind, value=observation.nearest_level_value, direction=observation.direction,
        window_high=window_high, window_low=window_low,
    )
    if breakeven_hit is None and level_hit is None:
        confirmation = ConfirmationOutcome.UNKNOWN
    elif breakeven_hit or level_hit:
        confirmation = ConfirmationOutcome.CONFIRMED
    else:
        confirmation = ConfirmationOutcome.NOT_CONFIRMED
    # Final 95% sprint (Section 6) -- a genuinely separate INVALIDATION-
    # side level (`invalidation_level_kind`/`invalidation_level_value`)
    # is now tracked, for the patterns that record a real level
    # (see that field's own docstring for why it stays scoped that
    # narrowly). `_supporting_level_broken_through()` returns `None`
    # (never guessed) for every observation that doesn't carry it --
    # every other pattern, and every observation written before this
    # field existed -- so `invalidation` stays honestly `UNKNOWN` there
    # too (Section 7: "Otherwise: UNKNOWN. Never guess.").
    invalidation_hit = _supporting_level_broken_through(
        kind=observation.invalidation_level_kind, value=observation.invalidation_level_value,
        direction=observation.direction, window_high=window_high, window_low=window_low,
    )
    if invalidation_hit is None:
        invalidation = InvalidationOutcome.UNKNOWN
    elif invalidation_hit:
        invalidation = InvalidationOutcome.INVALIDATED
    else:
        invalidation = InvalidationOutcome.NOT_INVALIDATED
    return PricePathOutcome(
        horizon=horizon, target_timestamp=target_timestamp, data_sufficient=True,
        subsequent_high=window_high, subsequent_low=window_low, subsequent_close=subsequent_close,
        max_favorable_move_pct=favorable, max_adverse_move_pct=adverse,
        confirmation_outcome=confirmation, invalidation_outcome=invalidation,
        first_confirmation_at=_first_bar_crossing(window, observation, confirmation=True)
        if confirmation == ConfirmationOutcome.CONFIRMED else None,
        first_invalidation_at=_first_bar_crossing(window, observation, confirmation=False)
        if invalidation == InvalidationOutcome.INVALIDATED else None,
    )


def _first_bar_crossing(window: list[PriceBar], observation: ResearchObservation, *, confirmation: bool) -> datetime | None:
    """The first bar (window is time-ordered) whose OWN high/low crossed
    the confirmation-side (breakeven or opposing level) or the
    invalidation-side level -- the same predicates as the whole-window
    determination above, applied bar by bar, so the two can never
    disagree about WHETHER a crossing happened, only add WHEN."""
    for candle in window:
        if confirmation:
            crossed = bool(
                _breakeven_reached(
                    breakeven=observation.contractual_expiry_breakeven, right=observation.selected_right,
                    window_high=candle.high, window_low=candle.low,
                )
                or _opposing_level_broken_through(
                    kind=observation.nearest_level_kind, value=observation.nearest_level_value,
                    direction=observation.direction, window_high=candle.high, window_low=candle.low,
                )
            )
        else:
            crossed = bool(
                _supporting_level_broken_through(
                    kind=observation.invalidation_level_kind, value=observation.invalidation_level_value,
                    direction=observation.direction, window_high=candle.high, window_low=candle.low,
                )
            )
        if crossed:
            return candle.timestamp
    return None


def compute_all_horizons(observation: ResearchObservation, candles: list[Candle], *, as_of: datetime) -> list[PricePathOutcome]:
    """All five horizons for one observation, in ascending order -- the
    convenience entry point `app.orchestration.historical_replay` and the
    replay HISTORY API actually call."""
    return [
        compute_price_path_outcome(observation, horizon, candles, as_of=as_of)
        for horizon in (
            OutcomeHorizonLabel.PLUS_30M, OutcomeHorizonLabel.PLUS_1H, OutcomeHorizonLabel.PLUS_1D,
            OutcomeHorizonLabel.PLUS_3D, OutcomeHorizonLabel.PLUS_5D,
        )
    ]


def resolve_first_event(outcome: PricePathOutcome) -> ResearchOutcomeStatus:
    """The ONE rule turning a horizon's confirmation/invalidation facts into
    an outcome status, for replay and forward checkpoints alike: whichever
    event happened FIRST wins and a later reversal never rewrites it; both
    inside the SAME M15 bar means the order is unknowable from M15 data ->
    `INSUFFICIENT_OUTCOME_DATA`, never a guess. Insufficient price data is
    `INSUFFICIENT_OUTCOME_DATA` too (callers that can tell "not reached yet"
    from "reached but missing" decide `PENDING` themselves)."""
    if not outcome.data_sufficient:
        return ResearchOutcomeStatus.INSUFFICIENT_OUTCOME_DATA
    invalidated_at = outcome.first_invalidation_at if outcome.invalidation_outcome == InvalidationOutcome.INVALIDATED else None
    confirmed_at = outcome.first_confirmation_at if outcome.confirmation_outcome == ConfirmationOutcome.CONFIRMED else None
    if invalidated_at is not None and confirmed_at is not None:
        if invalidated_at == confirmed_at:
            return ResearchOutcomeStatus.INSUFFICIENT_OUTCOME_DATA
        return ResearchOutcomeStatus.FAILED_SETUP if invalidated_at < confirmed_at else ResearchOutcomeStatus.FOLLOW_THROUGH_OBSERVED
    if invalidated_at is not None:
        return ResearchOutcomeStatus.FAILED_SETUP
    if confirmed_at is not None:
        return ResearchOutcomeStatus.FOLLOW_THROUGH_OBSERVED
    return ResearchOutcomeStatus.NO_FOLLOW_THROUGH
