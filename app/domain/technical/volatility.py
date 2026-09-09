"""Average True Range (Wilder).

Convention: True Range for candle `i` (i >= 1) is
`max(high_i - low_i, |high_i - close_{i-1}|, |low_i - close_{i-1}|)`. The
first candle in a series has no prior close, so it contributes no TR value —
this is standard, not a gap in coverage. ATR is Wilder-smoothed exactly like
RSI's average gain/loss: the first `period` true ranges seed a simple
average, then each subsequent TR is folded in recursively. Requires
`period + 1` candles (period true ranges need period+1 candles).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from app.domain.market.models import Candle, Timeframe
from app.domain.technical.series import IndicatorStatus, bounded_series


class ATRResult(BaseModel):
    status: IndicatorStatus
    instrument_id: str
    timeframe: Timeframe
    period: int
    as_of: datetime
    candles_used: int
    latest_candle_timestamp: datetime | None
    value: Decimal | None
    detail: str


def calculate_atr(
    candles: Sequence[Candle],
    *,
    instrument_id: str,
    timeframe: Timeframe,
    period: int,
    as_of: datetime,
) -> ATRResult:
    if period < 1:
        raise ValueError("period must be >= 1")

    series = bounded_series(candles, instrument_id=instrument_id, timeframe=timeframe, as_of=as_of)

    if len(series) < period + 1:
        return ATRResult(
            status=IndicatorStatus.INSUFFICIENT_HISTORY,
            instrument_id=instrument_id,
            timeframe=timeframe,
            period=period,
            as_of=as_of,
            candles_used=len(series),
            latest_candle_timestamp=series[-1].freshness.data_timestamp if series else None,
            value=None,
            detail=f"need at least {period + 1} candles ({period} true ranges), have {len(series)}",
        )

    true_ranges: list[Decimal] = []
    for i in range(1, len(series)):
        high = series[i].high
        low = series[i].low
        prev_close = series[i - 1].close
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))

    atr = sum(true_ranges[:period], start=Decimal(0)) / Decimal(period)
    for tr in true_ranges[period:]:
        atr = (atr * (period - 1) + tr) / Decimal(period)

    return ATRResult(
        status=IndicatorStatus.OK,
        instrument_id=instrument_id,
        timeframe=timeframe,
        period=period,
        as_of=as_of,
        candles_used=len(series),
        latest_candle_timestamp=series[-1].freshness.data_timestamp,
        value=atr,
        detail=f"ATR({period}) computed from {len(series)} candles",
    )
