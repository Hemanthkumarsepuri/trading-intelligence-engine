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
`invalidation_outcome` is honestly `UNKNOWN` today: this architecture
tracks only that ONE confirmation-relevant opposing level, never a
separate "structure that must NOT break" invalidation-side level, so a
real INVALIDATED/NOT_INVALIDATED determination isn't available yet --
never guessed from an unrelated fact (Section 7: "Otherwise: UNKNOWN.
Never guess."). A named, scoped follow-up once that level exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal
from enum import Enum

from app.domain.audit.research_models import ResearchObservation
from app.domain.market.models import Candle
from app.orchestration.research_outcome import target_trading_session_date
from app.utils.time import IST, ensure_utc


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
# The real NSE cash-session close (same fact documented in
# `app.orchestration.audit_journal`/`historical_replay_provider`) -- a
# session-count horizon's target instant is that target session's own
# close, the natural, deterministic "end of session N" instant.
_SESSION_CLOSE = time(15, 30)
# How close the local candle series must reach to a horizon's target
# instant to honestly count as "covers it" -- one M15 bar's worth of
# slack, never treated as reaching all the way to a target it falls short
# of by more than this.
_COVERAGE_TOLERANCE = timedelta(minutes=20)


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


def horizon_target_timestamp(observation: ResearchObservation, horizon: OutcomeHorizonLabel) -> datetime:
    """The real instant this horizon targets -- `generated_at + offset` for
    the two intraday horizons, or the target trading session's own 15:30
    IST close for the three session-count horizons."""
    intraday_offset = _INTRADAY_OFFSETS.get(horizon)
    if intraday_offset is not None:
        return observation.generated_at + intraday_offset
    sessions_ahead = _SESSION_OFFSETS[horizon]
    target_session = target_trading_session_date(observation.generated_at.date(), sessions_ahead)
    return ensure_utc(datetime.combine(target_session, _SESSION_CLOSE, tzinfo=IST))


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


def _breakeven_reached(*, breakeven: str | None, right: str | None, window_high: Decimal, window_low: Decimal) -> bool | None:
    if breakeven is None:
        return None
    be = Decimal(breakeven)
    return window_high >= be if right == "CE" else window_low <= be


def compute_price_path_outcome(
    observation: ResearchObservation, horizon: OutcomeHorizonLabel, candles: list[Candle], *, as_of: datetime,
) -> PricePathOutcome:
    """`candles` must be `observation.symbol`'s own real M15 series --
    ordinarily the same locally persisted historical series a replay run
    already holds on disk (never a live re-fetch). Every early return
    below is an honest `data_sufficient=False`, never a guessed or
    partial number (Section 16: "only calculate horizons for which enough
    future data exists")."""
    target_timestamp = horizon_target_timestamp(observation, horizon)
    if target_timestamp > as_of:
        return PricePathOutcome(
            horizon=horizon, target_timestamp=target_timestamp, data_sufficient=False,
            note="target instant has not been reached yet as of this evaluation",
        )
    ordered = sorted(candles, key=lambda c: c.freshness.data_timestamp)
    if not ordered or ordered[0].freshness.data_timestamp > observation.generated_at:
        return PricePathOutcome(
            horizon=horizon, target_timestamp=target_timestamp, data_sufficient=False,
            note="local historical candle series does not reach back to the observation instant",
        )
    window = [c for c in ordered if observation.generated_at <= c.freshness.data_timestamp <= target_timestamp]
    if not window or window[-1].freshness.data_timestamp < target_timestamp - _COVERAGE_TOLERANCE:
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
    # No genuinely separate INVALIDATION-side level is tracked on
    # `ResearchObservation` today (only the CONFIRMATION-relevant
    # opposing level above -- see its own docstring); a real invalidation
    # determination would need a distinct "structure that must NOT break"
    # level this architecture doesn't yet populate. Honestly `UNKNOWN`
    # rather than fabricated from the same level's breach (Section 7:
    # "Otherwise: UNKNOWN. Never guess.") -- a real, named, scoped
    # follow-up, not a silent gap (see `docs/HISTORICAL_REPLAY.md`).
    invalidation = InvalidationOutcome.UNKNOWN
    return PricePathOutcome(
        horizon=horizon, target_timestamp=target_timestamp, data_sufficient=True,
        subsequent_high=window_high, subsequent_low=window_low, subsequent_close=subsequent_close,
        max_favorable_move_pct=favorable, max_adverse_move_pct=adverse,
        confirmation_outcome=confirmation, invalidation_outcome=invalidation,
    )


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
