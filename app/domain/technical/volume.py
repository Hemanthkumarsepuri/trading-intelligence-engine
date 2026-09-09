"""Relative volume.

Convention: the ratio of the latest candle's volume to the simple average
volume of the `period` candles immediately preceding it. The latest candle
is deliberately excluded from its own baseline average — including it would
make an unusually large print partially dilute the very ratio meant to flag
it. Requires `period + 1` candles (period baseline candles + the current
one).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from app.domain.market.models import Candle, Timeframe
from app.domain.technical.series import IndicatorStatus, bounded_series


class RelativeVolumeResult(BaseModel):
    status: IndicatorStatus
    instrument_id: str
    timeframe: Timeframe
    period: int
    as_of: datetime
    candles_used: int
    latest_candle_timestamp: datetime | None
    current_volume: int | None
    average_volume: Decimal | None
    ratio: Decimal | None
    detail: str


def calculate_relative_volume(
    candles: Sequence[Candle],
    *,
    instrument_id: str,
    timeframe: Timeframe,
    period: int,
    as_of: datetime,
) -> RelativeVolumeResult:
    if period < 1:
        raise ValueError("period must be >= 1")

    series = bounded_series(candles, instrument_id=instrument_id, timeframe=timeframe, as_of=as_of)

    def _insufficient(detail: str, current_volume: int | None = None) -> RelativeVolumeResult:
        return RelativeVolumeResult(
            status=IndicatorStatus.INSUFFICIENT_HISTORY,
            instrument_id=instrument_id,
            timeframe=timeframe,
            period=period,
            as_of=as_of,
            candles_used=len(series),
            latest_candle_timestamp=series[-1].freshness.data_timestamp if series else None,
            current_volume=current_volume,
            average_volume=None,
            ratio=None,
            detail=detail,
        )

    if len(series) < period + 1:
        return _insufficient(f"need at least {period + 1} candles ({period} baseline + current), have {len(series)}")

    current = series[-1]
    baseline = series[-(period + 1) : -1]
    average_volume = Decimal(sum(c.volume for c in baseline)) / Decimal(period)

    if average_volume == 0:
        return _insufficient("baseline average volume is zero; relative volume is undefined", current_volume=current.volume)

    return RelativeVolumeResult(
        status=IndicatorStatus.OK,
        instrument_id=instrument_id,
        timeframe=timeframe,
        period=period,
        as_of=as_of,
        candles_used=len(series),
        latest_candle_timestamp=current.freshness.data_timestamp,
        current_volume=current.volume,
        average_volume=average_volume,
        ratio=Decimal(current.volume) / average_volume,
        detail=f"relative volume computed from {period} baseline candles preceding the current one",
    )
