"""Structure facts — literal comparisons between consecutive confirmed swing
points. Nothing more.

Built directly on top of `structure.find_swing_points()`. For each pair of
consecutive swing highs, records whether the later one is strictly greater
than, strictly less than, or equal to the earlier one (`HIGHER_HIGH` /
`LOWER_HIGH` / `EQUAL_HIGH`); the mirror comparison applies to consecutive
swing lows (`HIGHER_LOW` / `LOWER_LOW` / `EQUAL_LOW`). Each classification is
a plain `>`/`<`/`==` on two already-computed prices — no threshold, no
smoothing, no judgment call.

This module deliberately stops here. It does NOT synthesize these pairwise
facts into an "uptrend"/"downtrend"/"bullish structure"/"reversal" verdict —
how many consecutive HH/HL observations would constitute a "confirmed"
structure is exactly the kind of threshold ARCHITECTURE.md does not specify
(see the "Directional Vote Specification" gate note there), and inventing
one here would smuggle a strategy assumption into a facts layer. That
synthesis, if and when the architecture specifies it, belongs to
`engines/setup_detection.py`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from enum import Enum
from itertools import pairwise

from pydantic import BaseModel

from app.domain.market.models import Candle, Timeframe
from app.domain.technical.series import IndicatorStatus
from app.domain.technical.structure import SwingPoint, find_swing_points


class HighComparison(str, Enum):
    HIGHER_HIGH = "HIGHER_HIGH"
    LOWER_HIGH = "LOWER_HIGH"
    EQUAL_HIGH = "EQUAL_HIGH"


class LowComparison(str, Enum):
    HIGHER_LOW = "HIGHER_LOW"
    LOWER_LOW = "LOWER_LOW"
    EQUAL_LOW = "EQUAL_LOW"


class SwingHighComparison(BaseModel):
    previous: SwingPoint
    current: SwingPoint
    comparison: HighComparison


class SwingLowComparison(BaseModel):
    previous: SwingPoint
    current: SwingPoint
    comparison: LowComparison


class StructureFactsResult(BaseModel):
    status: IndicatorStatus
    instrument_id: str
    timeframe: Timeframe
    left_bars: int
    right_bars: int
    as_of: datetime
    candles_used: int
    latest_candle_timestamp: datetime | None
    high_comparisons: list[SwingHighComparison]
    low_comparisons: list[SwingLowComparison]
    detail: str


def _compare_highs(previous: SwingPoint, current: SwingPoint) -> HighComparison:
    if current.price > previous.price:
        return HighComparison.HIGHER_HIGH
    if current.price < previous.price:
        return HighComparison.LOWER_HIGH
    return HighComparison.EQUAL_HIGH


def _compare_lows(previous: SwingPoint, current: SwingPoint) -> LowComparison:
    if current.price > previous.price:
        return LowComparison.HIGHER_LOW
    if current.price < previous.price:
        return LowComparison.LOWER_LOW
    return LowComparison.EQUAL_LOW


def compute_structure_facts(
    candles: Sequence[Candle],
    *,
    instrument_id: str,
    timeframe: Timeframe,
    left_bars: int,
    right_bars: int,
    as_of: datetime,
) -> StructureFactsResult:
    """Delegates entirely to `find_swing_points()` for the no-look-ahead
    boundary and the underlying swing-point math — this function only
    compares the prices `find_swing_points()` already produced. Malformed
    input propagates as `InvalidCandleSeriesError`, exactly as it does from
    `find_swing_points()` itself; nothing here catches or converts it.
    """
    swings = find_swing_points(
        candles, instrument_id=instrument_id, timeframe=timeframe, left_bars=left_bars, right_bars=right_bars, as_of=as_of
    )

    if swings.status == IndicatorStatus.INSUFFICIENT_HISTORY:
        return StructureFactsResult(
            status=IndicatorStatus.INSUFFICIENT_HISTORY,
            instrument_id=instrument_id,
            timeframe=timeframe,
            left_bars=left_bars,
            right_bars=right_bars,
            as_of=as_of,
            candles_used=swings.candles_used,
            latest_candle_timestamp=swings.latest_candle_timestamp,
            high_comparisons=[],
            low_comparisons=[],
            detail=swings.detail,
        )

    high_comparisons = [
        SwingHighComparison(previous=previous, current=current, comparison=_compare_highs(previous, current))
        for previous, current in pairwise(swings.swing_highs)
    ]
    low_comparisons = [
        SwingLowComparison(previous=previous, current=current, comparison=_compare_lows(previous, current))
        for previous, current in pairwise(swings.swing_lows)
    ]

    return StructureFactsResult(
        status=IndicatorStatus.OK,
        instrument_id=instrument_id,
        timeframe=timeframe,
        left_bars=left_bars,
        right_bars=right_bars,
        as_of=as_of,
        candles_used=swings.candles_used,
        latest_candle_timestamp=swings.latest_candle_timestamp,
        high_comparisons=high_comparisons,
        low_comparisons=low_comparisons,
        detail=(
            f"{len(high_comparisons)} high-pair comparison(s), {len(low_comparisons)} low-pair comparison(s) "
            f"from {len(swings.swing_highs)} swing high(s) / {len(swings.swing_lows)} swing low(s)"
        ),
    )
