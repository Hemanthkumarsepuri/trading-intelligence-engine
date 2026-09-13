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
`ResearchObservation` (its nearest opposing S/R level; its option's
contractual expiry breakeven) was actually crossed, nothing more.
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


def _level_broken(*, kind: str | None, value: str | None, direction: str, window_high: Decimal, window_low: Decimal) -> InvalidationOutcome:
    if kind is None or value is None:
        return InvalidationOutcome.UNKNOWN
    level = Decimal(value)
    if direction == "BULLISH":
        broken = window_low <= level if kind == "support" else window_high >= level
    else:
        broken = window_high >= level if kind == "resistance" else window_low <= level
    return InvalidationOutcome.INVALIDATED if broken else InvalidationOutcome.NOT_INVALIDATED


def _breakeven_reached(*, breakeven: str | None, right: str | None, window_high: Decimal, window_low: Decimal) -> ConfirmationOutcome:
    if breakeven is None:
        return ConfirmationOutcome.UNKNOWN
    be = Decimal(breakeven)
    reached = window_high >= be if right == "CE" else window_low <= be
    return ConfirmationOutcome.CONFIRMED if reached else ConfirmationOutcome.NOT_CONFIRMED


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

    confirmation = _breakeven_reached(
        breakeven=observation.contractual_expiry_breakeven, right=observation.selected_right,
        window_high=window_high, window_low=window_low,
    )
    invalidation = _level_broken(
        kind=observation.nearest_level_kind, value=observation.nearest_level_value, direction=observation.direction,
        window_high=window_high, window_low=window_low,
    )
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
