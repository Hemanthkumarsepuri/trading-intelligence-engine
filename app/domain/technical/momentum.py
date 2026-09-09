"""RSI (Wilder) and MACD.

RSI convention: Wilder's original smoothing. The first `period` price
changes seed a simple-average gain/loss; every subsequent change is folded
in via Wilder's recursive smoothing (`avg = (avg * (period - 1) + new) /
period`). Requires `period + 1` candles (period price changes need period+1
closes). A zero average loss is treated as RSI = 100 (no losses to smooth
against), which is the standard convention, not a fabricated value.

MACD convention: `macd_line = EMA(close, fast) - EMA(close, slow)`,
`signal_line = EMA(macd_line series, signal)`, `histogram = macd_line -
signal_line`. All three values are reported together or not at all — a
result is never "partially" OK, to keep the contract simple for downstream
consumers. Minimum candles: `slow_period` to seed the slower EMA, plus
`signal_period - 1` further candles so the signal line has enough MACD
values to seed its own EMA (`slow_period + signal_period - 1` total).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from app.domain.market.models import Candle, Timeframe
from app.domain.technical.series import IndicatorStatus, bounded_series
from app.domain.technical.trend import ema_series


class RSIResult(BaseModel):
    status: IndicatorStatus
    instrument_id: str
    timeframe: Timeframe
    period: int
    as_of: datetime
    candles_used: int
    latest_candle_timestamp: datetime | None
    value: Decimal | None
    detail: str


class MACDResult(BaseModel):
    status: IndicatorStatus
    instrument_id: str
    timeframe: Timeframe
    fast_period: int
    slow_period: int
    signal_period: int
    as_of: datetime
    candles_used: int
    latest_candle_timestamp: datetime | None
    macd_line: Decimal | None
    signal_line: Decimal | None
    histogram: Decimal | None
    detail: str


def calculate_rsi(
    candles: Sequence[Candle],
    *,
    instrument_id: str,
    timeframe: Timeframe,
    period: int,
    as_of: datetime,
) -> RSIResult:
    if period < 1:
        raise ValueError("period must be >= 1")

    series = bounded_series(candles, instrument_id=instrument_id, timeframe=timeframe, as_of=as_of)

    if len(series) < period + 1:
        return RSIResult(
            status=IndicatorStatus.INSUFFICIENT_HISTORY,
            instrument_id=instrument_id,
            timeframe=timeframe,
            period=period,
            as_of=as_of,
            candles_used=len(series),
            latest_candle_timestamp=series[-1].freshness.data_timestamp if series else None,
            value=None,
            detail=f"need at least {period + 1} candles ({period} price changes), have {len(series)}",
        )

    closes = [c.close for c in series]
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else Decimal(0) for d in deltas]
    losses = [-d if d < 0 else Decimal(0) for d in deltas]

    avg_gain = sum(gains[:period], start=Decimal(0)) / Decimal(period)
    avg_loss = sum(losses[:period], start=Decimal(0)) / Decimal(period)

    for gain, loss in zip(gains[period:], losses[period:], strict=True):
        avg_gain = (avg_gain * (period - 1) + gain) / Decimal(period)
        avg_loss = (avg_loss * (period - 1) + loss) / Decimal(period)

    if avg_loss == 0:
        rsi_value = Decimal(100)
    else:
        rs = avg_gain / avg_loss
        rsi_value = Decimal(100) - (Decimal(100) / (Decimal(1) + rs))

    return RSIResult(
        status=IndicatorStatus.OK,
        instrument_id=instrument_id,
        timeframe=timeframe,
        period=period,
        as_of=as_of,
        candles_used=len(series),
        latest_candle_timestamp=series[-1].freshness.data_timestamp,
        value=rsi_value,
        detail=f"RSI({period}) computed from {len(series)} candles",
    )


def calculate_macd(
    candles: Sequence[Candle],
    *,
    instrument_id: str,
    timeframe: Timeframe,
    fast_period: int,
    slow_period: int,
    signal_period: int,
    as_of: datetime,
) -> MACDResult:
    if fast_period < 1 or slow_period < 1 or signal_period < 1:
        raise ValueError("all periods must be >= 1")
    if fast_period >= slow_period:
        raise ValueError("fast_period must be less than slow_period")

    series = bounded_series(candles, instrument_id=instrument_id, timeframe=timeframe, as_of=as_of)
    closes = [c.close for c in series]

    def _insufficient(detail: str) -> MACDResult:
        return MACDResult(
            status=IndicatorStatus.INSUFFICIENT_HISTORY,
            instrument_id=instrument_id,
            timeframe=timeframe,
            fast_period=fast_period,
            slow_period=slow_period,
            signal_period=signal_period,
            as_of=as_of,
            candles_used=len(series),
            latest_candle_timestamp=series[-1].freshness.data_timestamp if series else None,
            macd_line=None,
            signal_line=None,
            histogram=None,
            detail=detail,
        )

    fast_values = ema_series(closes, fast_period)
    slow_values = ema_series(closes, slow_period)

    if not slow_values:
        return _insufficient(f"need at least {slow_period} candles for the slow EMA, have {len(series)}")

    # fast_values[0] aligns to closes[fast_period - 1]; slow_values[0] aligns
    # to closes[slow_period - 1]. Trim fast_values to start at the same
    # closes-index as slow_values before pairing them up.
    offset = slow_period - fast_period
    aligned_fast = fast_values[offset:]
    macd_line_series = [f - s for f, s in zip(aligned_fast, slow_values, strict=True)]

    signal_values = ema_series(macd_line_series, signal_period)
    if not signal_values:
        return _insufficient(
            f"slow EMA is available, but need at least {signal_period} MACD values to seed the "
            f"signal line (have {len(macd_line_series)}) — {slow_period + signal_period - 1} candles "
            f"total are required, have {len(series)}"
        )

    macd_line = macd_line_series[-1]
    signal_line = signal_values[-1]

    return MACDResult(
        status=IndicatorStatus.OK,
        instrument_id=instrument_id,
        timeframe=timeframe,
        fast_period=fast_period,
        slow_period=slow_period,
        signal_period=signal_period,
        as_of=as_of,
        candles_used=len(series),
        latest_candle_timestamp=series[-1].freshness.data_timestamp,
        macd_line=macd_line,
        signal_line=signal_line,
        histogram=macd_line - signal_line,
        detail=f"MACD({fast_period},{slow_period},{signal_period}) computed from {len(series)} candles",
    )
