"""VWAP and swing high/low (fractal) points.

VWAP convention: volume-weighted typical price, `sum(((high+low+close)/3) *
volume) / sum(volume)`, computed over *exactly* the candle series given.
This function has no concept of a "trading session" and does not reset at
day boundaries — session-windowing (e.g. "only today's candles") is the
caller's responsibility, left to a future orchestration/session-aware layer.
Baking a session-reset rule in here would be inventing a convention the
architecture does not specify; this keeps the function's contract to what is
unambiguous: VWAP of the given series, nothing more.

Swing point convention: an N-left/M-right fractal. Candle `i` is a swing
high only if its `high` is *strictly* greater than every other high in the
window `[i - left_bars, i + right_bars]`; a tie (a plateau) does not count.
Swing low is the mirror (`low` strictly less than every other low in the
window). This is standard fractal/pivot geometry, not a strategy rule —
`left_bars`/`right_bars` have no default; the caller must choose them
explicitly rather than inherit an assumption from this module. A swing point
is inherently a *lagging* confirmation: the most recent `right_bars` candles
in the bounded series cannot yet have a confirmed swing point, because there
isn't enough subsequent data to confirm one — that is correct behavior, not
a bug, and this function does not "peek" past `as_of` to manufacture one.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, time
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel

from app.domain.market.models import Candle, Timeframe
from app.domain.technical.series import IndicatorStatus, bounded_series


class VWAPResult(BaseModel):
    status: IndicatorStatus
    instrument_id: str
    timeframe: Timeframe
    as_of: datetime
    candles_used: int
    latest_candle_timestamp: datetime | None
    value: Decimal | None
    current_price: Decimal | None
    deviation: Decimal | None
    deviation_pct: Decimal | None
    detail: str


def calculate_vwap(
    candles: Sequence[Candle],
    *,
    instrument_id: str,
    timeframe: Timeframe,
    as_of: datetime,
) -> VWAPResult:
    series = bounded_series(candles, instrument_id=instrument_id, timeframe=timeframe, as_of=as_of)

    def _insufficient(detail: str) -> VWAPResult:
        return VWAPResult(
            status=IndicatorStatus.INSUFFICIENT_HISTORY,
            instrument_id=instrument_id,
            timeframe=timeframe,
            as_of=as_of,
            candles_used=len(series),
            latest_candle_timestamp=series[-1].freshness.data_timestamp if series else None,
            value=None,
            current_price=None,
            deviation=None,
            deviation_pct=None,
            detail=detail,
        )

    if not series:
        return _insufficient("no candles at or before as_of")

    total_volume = sum((c.volume for c in series), start=0)
    if total_volume == 0:
        return _insufficient("total volume across the provided series is zero; VWAP is undefined")

    total_pv = sum(
        (((c.high + c.low + c.close) / Decimal(3)) * c.volume for c in series), start=Decimal(0)
    )
    vwap = total_pv / Decimal(total_volume)
    current_price = series[-1].close
    deviation = current_price - vwap
    deviation_pct = (deviation / vwap) * Decimal(100) if vwap != 0 else None

    return VWAPResult(
        status=IndicatorStatus.OK,
        instrument_id=instrument_id,
        timeframe=timeframe,
        as_of=as_of,
        candles_used=len(series),
        latest_candle_timestamp=series[-1].freshness.data_timestamp,
        value=vwap,
        current_price=current_price,
        deviation=deviation,
        deviation_pct=deviation_pct,
        detail=f"VWAP computed from {len(series)} candles (typical price, no session reset)",
    )


def nse_cash_session_candles(
    candles: Sequence[Candle],
    *,
    as_of: datetime,
) -> list[Candle]:
    """M15 bars whose IST clock falls in the cash regular session
    09:15-15:30 on the IST calendar date of `as_of`. No previous-session
    bars. `as_of` still bounds the series (no look-ahead).
    """
    from app.utils.time import to_ist

    session_date = to_ist(as_of).date()
    out: list[Candle] = []
    for c in candles:
        if c.freshness.data_timestamp > as_of:
            continue
        local = to_ist(c.freshness.data_timestamp)
        if local.date() != session_date:
            continue
        clock = local.time()
        if clock < time(9, 15) or clock > time(15, 30):
            continue
        out.append(c)
    return out


def calculate_session_vwap(
    candles: Sequence[Candle],
    *,
    instrument_id: str,
    timeframe: Timeframe,
    as_of: datetime,
) -> VWAPResult:
    """True NSE cash-session VWAP from M15 typical-price * volume on the
    IST session date of `as_of`. Not a rolling multi-day window.
    """
    session = nse_cash_session_candles(candles, as_of=as_of)
    result = calculate_vwap(session, instrument_id=instrument_id, timeframe=timeframe, as_of=as_of)
    if result.status == IndicatorStatus.OK:
        return result.model_copy(update={
            "detail": (
                f"NSE cash-session VWAP from {result.candles_used} M15 bars "
                f"(IST 09:15-15:30 on {to_ist_date_label(as_of)}; typical price, session reset)"
            ),
        })
    return result.model_copy(update={
        "detail": "session VWAP unavailable -- no current-session M15 bars with volume",
    })


def to_ist_date_label(as_of: datetime) -> str:
    from app.utils.time import to_ist
    return to_ist(as_of).date().isoformat()


class SwingKind(str, Enum):
    HIGH = "HIGH"
    LOW = "LOW"


class SwingPoint(BaseModel):
    timestamp: datetime
    price: Decimal
    kind: SwingKind


class SwingPointsResult(BaseModel):
    status: IndicatorStatus
    instrument_id: str
    timeframe: Timeframe
    left_bars: int
    right_bars: int
    as_of: datetime
    candles_used: int
    latest_candle_timestamp: datetime | None
    swing_highs: list[SwingPoint]
    swing_lows: list[SwingPoint]
    detail: str


def find_swing_points(
    candles: Sequence[Candle],
    *,
    instrument_id: str,
    timeframe: Timeframe,
    left_bars: int,
    right_bars: int,
    as_of: datetime,
) -> SwingPointsResult:
    if left_bars < 1 or right_bars < 1:
        raise ValueError("left_bars and right_bars must both be >= 1")

    series = bounded_series(candles, instrument_id=instrument_id, timeframe=timeframe, as_of=as_of)
    required = left_bars + right_bars + 1

    if len(series) < required:
        return SwingPointsResult(
            status=IndicatorStatus.INSUFFICIENT_HISTORY,
            instrument_id=instrument_id,
            timeframe=timeframe,
            left_bars=left_bars,
            right_bars=right_bars,
            as_of=as_of,
            candles_used=len(series),
            latest_candle_timestamp=series[-1].freshness.data_timestamp if series else None,
            swing_highs=[],
            swing_lows=[],
            detail=f"need at least {required} candles ({left_bars} left + 1 + {right_bars} right), have {len(series)}",
        )

    swing_highs: list[SwingPoint] = []
    swing_lows: list[SwingPoint] = []

    for i in range(left_bars, len(series) - right_bars):
        candidate = series[i]
        window = series[i - left_bars : i + right_bars + 1]
        others = window[:left_bars] + window[left_bars + 1 :]

        if all(candidate.high > c.high for c in others):
            swing_highs.append(
                SwingPoint(timestamp=candidate.freshness.data_timestamp, price=candidate.high, kind=SwingKind.HIGH)
            )
        if all(candidate.low < c.low for c in others):
            swing_lows.append(
                SwingPoint(timestamp=candidate.freshness.data_timestamp, price=candidate.low, kind=SwingKind.LOW)
            )

    return SwingPointsResult(
        status=IndicatorStatus.OK,
        instrument_id=instrument_id,
        timeframe=timeframe,
        left_bars=left_bars,
        right_bars=right_bars,
        as_of=as_of,
        candles_used=len(series),
        latest_candle_timestamp=series[-1].freshness.data_timestamp,
        swing_highs=swing_highs,
        swing_lows=swing_lows,
        detail=(
            f"{len(swing_highs)} swing high(s), {len(swing_lows)} swing low(s) found among {len(series)} candles "
            f"(most recent {right_bars} candle(s) cannot yet be confirmed)"
        ),
    )
