"""No-lookahead FAILED-BREAKDOWN / FAILED-BREAKOUT RECLAIM structure.

The observable event `DevelopmentPattern.FAILED_BREAKDOWN_RECLAIM` has
always described: "a recent break of swing structure failed to hold and
price has reclaimed". Until this module existed, nothing in the pipeline
ever computed that fact -- `classify_development()`'s
`failed_breakdown_reclaim` parameter had no production caller and
defaulted to `False`, so the pattern was documented but structurally
unreachable in BOTH the live path and historical replay (the 2,610-episode
replay dataset built on 17 Sep 2026 contains exactly one pattern for this
reason).

This module computes the missing fact, and nothing else. It does not
fetch, does not classify direction, does not decide anything: it reports
whether a specific, mechanical price-structure event happened in the
already-fetched candle series, using the SAME IST session aggregation
`app.domain.options.historical_structure` already performs (imported, not
re-implemented) and the SAME `bounded_series(..., as_of=)` no-lookahead
boundary every other historical read uses.

The event, stated exactly (bullish side; the bearish side is the mirror):

    1. REFERENCE  -- the lowest low of the `REFERENCE_WINDOW_SESSIONS`
       completed sessions immediately BEFORE a candidate break session
       (at least `MIN_REFERENCE_SESSIONS` of them must exist). This is
       the swing structure that was broken, and it is computed only from
       sessions strictly earlier than the break itself.
    2. BREAK      -- that candidate session traded below the reference
       by at least `MIN_BREAK_PCT` AND CLOSED below it. A sub-threshold
       undercut is noise; a session that pokes through and closes back
       inside is a wick the market never accepted, and leaves nothing to
       reclaim. Neither is reported as a break.

       The closing requirement is what makes this a genuinely
       distinguishing event rather than a description of ordinary noise.
       Measured over 5,040 session-close evaluations across the local
       45-symbol store: without it the detector fired on 39.3% of
       evaluations, which is not a named structural event -- it is the
       tape. With it, the figure is in this module's own test evidence
       and the release report.
    3. RECLAIM    -- a completed session within `MAX_SESSIONS_TO_RECLAIM`
       of the break CLOSED back above the reference.
    4. HOLD       -- every completed session since the reclaim also
       closed above the reference, and the current spot is still above
       it. A reclaim that has already been lost again is not a reclaim.
    5. RECENCY    -- the reclaim is within `MAX_RECLAIM_AGE_SESSIONS`
       completed sessions of now. This is a DEVELOPING structural event;
       an identical event from two months ago is history, not a
       developing observation.

`level` is the reference itself -- the real price the market broke and
then reclaimed. That makes it the one honest, deterministic invalidation
level for this pattern ("price loses the reclaimed level again" is
literally "close/trade back through `level`"), which is what lets a
replay outcome reach a genuine INVALIDATED/NOT_INVALIDATED determination
instead of `UNKNOWN` (`app.orchestration.outcome_horizons`).

Integrity properties this module deliberately keeps:

  - A whipsaw that qualifies on BOTH sides returns `CONFLICTING` with no
    direction and no level. Two opposing structural readings of the same
    tape are not evidence for either one, and guessing between them
    would be exactly the manufactured conviction this product forbids.
  - Too little history returns `INSUFFICIENT_HISTORY` -- never `NONE`,
    because "we could not look" and "we looked and there was nothing"
    are different facts.
  - Nothing here is a score, a rate, a probability or a prediction. It
    reports a dated structural event and the price it happened at.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from app.domain.market.models import Candle, Timeframe
from app.domain.options.historical_structure import aggregate_session_bars
from app.domain.technical.series import bounded_series

# The prior structure a break is measured against, and the minimum of it
# that must actually exist. Five sessions is the same lookback horizon
# `historical_structure.MIN_COMPLETED_SESSIONS` already requires before
# it will say anything about multi-day structure at all.
REFERENCE_WINDOW_SESSIONS = 5
MIN_REFERENCE_SESSIONS = 3
# A break must be a real excursion through the level, not a tick.
MIN_BREAK_PCT = Decimal("0.30")
# How long a failed break may take to be reclaimed, and how recently the
# reclaim must have happened for this to be a DEVELOPING observation.
MAX_SESSIONS_TO_RECLAIM = 3
MAX_RECLAIM_AGE_SESSIONS = 3
# Total completed sessions needed before the question is answerable at
# all: the minimum reference window, the break, and the reclaim.
MIN_COMPLETED_SESSIONS = MIN_REFERENCE_SESSIONS + 2

BULLISH = "BULLISH"
BEARISH = "BEARISH"


class StructuralReclaimStatus(str, Enum):
    OK = "OK"
    NONE = "NONE"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    CONFLICTING = "CONFLICTING"


@dataclass(frozen=True)
class StructuralReclaim:
    """One dated structural fact, or an honest statement that there
    isn't one. `level_kind` follows the same geometry the rest of the
    codebase uses: a BULLISH reclaim's level is a `support` (below
    spot, the floor that was broken and taken back), a BEARISH one's is
    a `resistance` (above spot)."""

    status: StructuralReclaimStatus
    direction: str | None
    level_kind: str | None
    level: Decimal | None
    broken_on: date | None
    reclaimed_on: date | None
    break_depth_pct: Decimal | None
    sessions_since_reclaim: int | None
    detail: str

    @property
    def is_present(self) -> bool:
        return self.status == StructuralReclaimStatus.OK


def _none(detail: str, *, status: StructuralReclaimStatus = StructuralReclaimStatus.NONE) -> StructuralReclaim:
    return StructuralReclaim(
        status=status, direction=None, level_kind=None, level=None, broken_on=None,
        reclaimed_on=None, break_depth_pct=None, sessions_since_reclaim=None, detail=detail,
    )


@dataclass(frozen=True)
class _SessionView:
    """The only four session facts this module reads. Kept as its own
    tiny view so the scan cannot accidentally depend on anything else
    (volume, completeness) that a caller might vary."""

    session_date: date
    high: Decimal
    low: Decimal
    close: Decimal


@dataclass(frozen=True)
class _Event:
    level: Decimal
    broken_on: date
    reclaimed_on: date
    break_depth_pct: Decimal
    sessions_since_reclaim: int


def _scan(bars: list[_SessionView], *, bullish: bool, reference_price: Decimal) -> _Event | None:
    """The most recent qualifying event on one side, or `None`. Walks
    break candidates newest-first so the FIRST hit is the most recent
    one; the bounded scan window follows directly from the recency and
    reclaim-window limits (a break older than that can never produce a
    recent enough reclaim)."""
    last = len(bars) - 1
    oldest_break = max(MIN_REFERENCE_SESSIONS, last - MAX_RECLAIM_AGE_SESSIONS - MAX_SESSIONS_TO_RECLAIM)
    for i in range(last - 1, oldest_break - 1, -1):
        window = bars[max(0, i - REFERENCE_WINDOW_SESSIONS):i]
        if len(window) < MIN_REFERENCE_SESSIONS:
            continue
        reference = min(b.low for b in window) if bullish else max(b.high for b in window)
        if reference <= 0:
            continue
        excursion = (reference - bars[i].low) if bullish else (bars[i].high - reference)
        if excursion <= 0:
            continue
        depth_pct = excursion / reference * Decimal(100)
        if depth_pct < MIN_BREAK_PCT:
            continue
        # The break session must also CLOSE beyond the level. A session
        # that pokes through and closes back inside is a wick, and the
        # market never accepted the break -- there is then nothing for a
        # later session to "reclaim". See this module's own docstring.
        closed_beyond = bars[i].close < reference if bullish else bars[i].close > reference
        if not closed_beyond:
            continue
        reclaimed_at: int | None = None
        for j in range(i + 1, min(i + MAX_SESSIONS_TO_RECLAIM, last) + 1):
            back_inside = bars[j].close > reference if bullish else bars[j].close < reference
            if back_inside:
                reclaimed_at = j
                break
        if reclaimed_at is None:
            continue
        if last - reclaimed_at > MAX_RECLAIM_AGE_SESSIONS:
            continue
        held = all(
            (b.close > reference if bullish else b.close < reference)
            for b in bars[reclaimed_at + 1:]
        )
        still_holding = reference_price > reference if bullish else reference_price < reference
        if not held or not still_holding:
            continue
        return _Event(
            level=reference, broken_on=bars[i].session_date, reclaimed_on=bars[reclaimed_at].session_date,
            break_depth_pct=depth_pct, sessions_since_reclaim=last - reclaimed_at,
        )
    return None


def detect_structural_reclaim(
    candles: list[Candle], *, instrument_id: str, timeframe: Timeframe, as_of: datetime, spot: Decimal | None,
) -> StructuralReclaim:
    """Only candles with `data_timestamp <= as_of` participate, and only
    COMPLETED IST sessions are scanned -- today's still-forming session
    can neither break nor reclaim structure, because its own high, low
    and close are not final facts yet. `spot` is the current price used
    for the "still holding" test; when it is unavailable the last
    completed session's close is used instead (a real number, never a
    guess), and the detail says so."""
    if not candles:
        return _none(
            "INSUFFICIENT_HISTORY -- no candles in the bounded series to aggregate into sessions",
            status=StructuralReclaimStatus.INSUFFICIENT_HISTORY,
        )
    bounded = bounded_series(candles, instrument_id=instrument_id, timeframe=timeframe, as_of=as_of)
    completed = [
        _SessionView(session_date=b.session_date, high=b.high, low=b.low, close=b.close)
        for b in aggregate_session_bars(bounded, as_of=as_of)
        if b.completed
    ]
    if len(completed) < MIN_COMPLETED_SESSIONS:
        return _none(
            f"INSUFFICIENT_HISTORY -- {len(completed)} completed IST session(s); "
            f"need {MIN_COMPLETED_SESSIONS} to identify a break and a reclaim of prior structure",
            status=StructuralReclaimStatus.INSUFFICIENT_HISTORY,
        )

    reference_price = spot if spot is not None and spot > 0 else completed[-1].close
    spot_note = "" if spot is not None and spot > 0 else " (current spot unavailable; last completed close used)"
    bullish = _scan(completed, bullish=True, reference_price=reference_price)
    bearish = _scan(completed, bullish=False, reference_price=reference_price)

    if bullish is not None and bearish is not None:
        return _none(
            "CONFLICTING -- the same series qualifies as both a reclaimed breakdown "
            f"({bullish.level}) and a reclaimed breakout ({bearish.level}); neither is reported",
            status=StructuralReclaimStatus.CONFLICTING,
        )
    if bullish is None and bearish is None:
        return _none(
            f"NONE -- no break of prior {REFERENCE_WINDOW_SESSIONS}-session structure by at least "
            f"{MIN_BREAK_PCT}% was reclaimed and held within the last {MAX_RECLAIM_AGE_SESSIONS} completed sessions"
        )

    event = bullish if bullish is not None else bearish
    if event is None:  # unreachable: both-None returned above. Kept so this stays a total function.
        return _none("NONE -- no qualifying reclaim event")
    direction = BULLISH if bullish is not None else BEARISH
    side = "below" if direction == BULLISH else "above"
    return StructuralReclaim(
        status=StructuralReclaimStatus.OK,
        direction=direction,
        level_kind="support" if direction == BULLISH else "resistance",
        level=event.level,
        broken_on=event.broken_on,
        reclaimed_on=event.reclaimed_on,
        break_depth_pct=event.break_depth_pct,
        sessions_since_reclaim=event.sessions_since_reclaim,
        detail=(
            f"{event.level} was broken {side} by {event.break_depth_pct:.2f}% on "
            f"{event.broken_on.isoformat()} and reclaimed on {event.reclaimed_on.isoformat()}; "
            f"held for {event.sessions_since_reclaim} completed session(s) since{spot_note}"
        ),
    )
