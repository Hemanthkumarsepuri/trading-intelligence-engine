"""EMA alignment — a literal comparison of already-computed EMA values.
Nothing more.

Built directly on top of `trend.calculate_ema()`. Given a caller-supplied
list of periods, ordered fastest-to-slowest (e.g. `[9, 21, 50]`, matching
ARCHITECTURE.md §9's chosen Trend periods, but this function does not
hardcode those specific numbers — any strictly increasing period list is
accepted), it computes one EMA per period and classifies the *numeric
sequence* of resulting values:

    ASCENDING  — reading the values in the exact period order supplied
                 (fastest to slowest), each value is strictly less than
                 the next: EMA(fast) < EMA(medium) < EMA(slow), as plain
                 numbers.
    DESCENDING — the mirror: each value is strictly greater than the next.
    MIXED      — neither holds for the whole sequence (includes any tie
                 between adjacent values, and any non-monotonic ordering).

IMPORTANT — this is a description of the numeric sequence ONLY. It is not a
trend read and not a directional claim. In conventional technical-analysis
vocabulary, a *falling* market is often associated with the faster EMA
sitting *below* the slower one — i.e. the "ASCENDING" value-sequence label
this module produces does not correspond to "price is ascending" or
"bullish" in any reliable, unqualified way, and callers must not treat it
as one. ARCHITECTURE.md's Directional Vote Final Gate (2026-08-27,
adversarial review) found that attaching directional vocabulary (BULLISH/
BEARISH) to exactly this kind of threshold-free comparison was an
unjustified interpretive leap — this module exists specifically to provide
the fact half of that finding without repeating its error. Turning this
sequence classification into a trading read is `StrategyDefinition`'s job,
not this module's, exactly as `structure_facts.py` already treats
HIGHER_HIGH/LOWER_HIGH.

WHAT THE GEOMETRY ACTUALLY IS, so no caller has to re-derive it: with the
conventional fastest-to-slowest period order `[9, 21, 50]`,

    ASCENDING  == EMA9 < EMA21 < EMA50 -- the FASTER averages sit BELOW
                  the slower ones, the ordering a FALLING series produces.
    DESCENDING == EMA9 > EMA21 > EMA50 -- the FASTER averages sit ABOVE
                  the slower ones, the ordering a RISING series produces.

Every caller in this repo read that backwards until 18 Sep 2026: an
`ASCENDING` sequence was mapped to BULLISH evidence, a BULLISH strategy
setup and a `TRENDING_BULLISH` regime. Measured over 1,404 real M15
samples from the local 45-symbol candle store, price had FALLEN over the
trailing 50 bars in 90.2% of `ASCENDING` samples and RISEN in 89.3% of
`DESCENDING` samples, so the labels were being attached to the opposite
of what the tape had done. The warning above was correct; the callers
simply ignored it. `tests/unit/technical/test_ema_alignment.py` now pins
the geometry against real rising and falling series so the sequence and
its meaning cannot drift apart again.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from enum import Enum
from itertools import pairwise

from pydantic import BaseModel

from app.domain.market.models import Candle, Timeframe
from app.domain.technical.series import IndicatorStatus, bounded_series
from app.domain.technical.trend import calculate_ema


class EMAAlignmentState(str, Enum):
    ASCENDING = "ASCENDING"
    DESCENDING = "DESCENDING"
    MIXED = "MIXED"


class EMAAlignmentResult(BaseModel):
    status: IndicatorStatus
    instrument_id: str
    timeframe: Timeframe
    periods: list[int]
    as_of: datetime
    candles_used: int
    latest_candle_timestamp: datetime | None
    values: list[Decimal] | None
    state: EMAAlignmentState | None
    detail: str


def compute_ema_alignment(
    candles: Sequence[Candle],
    *,
    instrument_id: str,
    timeframe: Timeframe,
    periods: Sequence[int],
    as_of: datetime,
) -> EMAAlignmentResult:
    """`periods` must be supplied fastest-to-slowest and strictly increasing
    (e.g. `[9, 21, 50]`) — this function does not choose or default any
    period itself. Delegates all no-look-ahead filtering and EMA math to
    `bounded_series()`/`calculate_ema()`; computes nothing new numerically.
    """
    if len(periods) < 2:
        raise ValueError("periods must contain at least 2 values (fastest to slowest)")
    for earlier, later in pairwise(periods):
        if later <= earlier:
            raise ValueError("periods must be strictly increasing (fastest to slowest)")

    bounded = bounded_series(candles, instrument_id=instrument_id, timeframe=timeframe, as_of=as_of)
    candles_used = len(bounded)
    latest_candle_timestamp = bounded[-1].freshness.data_timestamp if bounded else None

    ema_results = [
        calculate_ema(candles, instrument_id=instrument_id, timeframe=timeframe, period=period, as_of=as_of)
        for period in periods
    ]

    values: list[Decimal] = []
    insufficient = False
    for result in ema_results:
        if result.status == IndicatorStatus.INSUFFICIENT_HISTORY or result.value is None:
            insufficient = True
        else:
            values.append(result.value)

    if insufficient:
        return EMAAlignmentResult(
            status=IndicatorStatus.INSUFFICIENT_HISTORY,
            instrument_id=instrument_id,
            timeframe=timeframe,
            periods=list(periods),
            as_of=as_of,
            candles_used=candles_used,
            latest_candle_timestamp=latest_candle_timestamp,
            values=None,
            state=None,
            detail=f"at least one of periods {list(periods)} lacks sufficient history",
        )

    ascending = all(earlier < later for earlier, later in pairwise(values))
    descending = all(earlier > later for earlier, later in pairwise(values))
    if ascending:
        state = EMAAlignmentState.ASCENDING
    elif descending:
        state = EMAAlignmentState.DESCENDING
    else:
        state = EMAAlignmentState.MIXED

    return EMAAlignmentResult(
        status=IndicatorStatus.OK,
        instrument_id=instrument_id,
        timeframe=timeframe,
        periods=list(periods),
        as_of=as_of,
        candles_used=candles_used,
        latest_candle_timestamp=latest_candle_timestamp,
        values=values,
        state=state,
        detail=f"EMA alignment across periods {list(periods)} from {candles_used} candles -> {state.value}",
    )
