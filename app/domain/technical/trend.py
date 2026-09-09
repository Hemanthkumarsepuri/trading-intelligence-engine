"""Exponential Moving Average.

Convention: EMA is seeded with a simple average of the earliest `period`
closes in the bounded series, then recursively smoothed forward through
every remaining candle up to (and including) the most recent one at or
before `as_of`. This is the standard textbook EMA definition — no
strategy-specific variant (no gap adjustment, no alternate seeding). More
history beyond `period` candles improves convergence but is not required;
the minimum viable series length is exactly `period` candles.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from app.domain.market.models import Candle, Timeframe
from app.domain.technical.series import IndicatorStatus, bounded_series


class EMAResult(BaseModel):
    status: IndicatorStatus
    instrument_id: str
    timeframe: Timeframe
    period: int
    as_of: datetime
    candles_used: int
    latest_candle_timestamp: datetime | None
    value: Decimal | None
    detail: str


def ema_series(values: Sequence[Decimal], period: int) -> list[Decimal]:
    """Pure numeric primitive, reused by `calculate_ema` and by `momentum.py`
    (MACD is two EMAs of price plus one EMA of the resulting MACD line).

    Returns EMA values aligned to `values[period - 1:]` — i.e. `result[0]`
    is the EMA as of `values[period - 1]`, seeded by the simple average of
    `values[0:period]`. Returns `[]` if `len(values) < period`.
    """
    if period < 1:
        raise ValueError("period must be >= 1")
    if len(values) < period:
        return []

    multiplier = Decimal(2) / Decimal(period + 1)
    ema = sum(values[:period], start=Decimal(0)) / Decimal(period)
    result = [ema]
    for value in values[period:]:
        ema = (value - ema) * multiplier + ema
        result.append(ema)
    return result


def calculate_ema(
    candles: Sequence[Candle],
    *,
    instrument_id: str,
    timeframe: Timeframe,
    period: int,
    as_of: datetime,
) -> EMAResult:
    series = bounded_series(candles, instrument_id=instrument_id, timeframe=timeframe, as_of=as_of)
    values = ema_series([c.close for c in series], period)

    if not values:
        return EMAResult(
            status=IndicatorStatus.INSUFFICIENT_HISTORY,
            instrument_id=instrument_id,
            timeframe=timeframe,
            period=period,
            as_of=as_of,
            candles_used=len(series),
            latest_candle_timestamp=series[-1].freshness.data_timestamp if series else None,
            value=None,
            detail=f"need at least {period} candles at or before as_of, have {len(series)}",
        )

    return EMAResult(
        status=IndicatorStatus.OK,
        instrument_id=instrument_id,
        timeframe=timeframe,
        period=period,
        as_of=as_of,
        candles_used=len(series),
        latest_candle_timestamp=series[-1].freshness.data_timestamp,
        value=values[-1],
        detail=f"EMA({period}) computed from {len(series)} candles",
    )
