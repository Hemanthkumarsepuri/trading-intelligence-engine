"""TechnicalSnapshot — a fact container, not a trading decision.

Aggregates the seven existing single-timeframe technical primitives
(EMA, RSI, MACD, ATR, VWAP, swing points, relative volume) for one
`(instrument_id, timeframe, as_of)` into one structured, auditable object.

This module does not compute any indicator math itself — it only calls the
existing `calculate_*`/`find_*` functions in this package and bundles their
results verbatim. Every sub-result's `status`/`value`/`candles_used` is
preserved exactly as that function produced it: `INSUFFICIENT_HISTORY` is
never collapsed into `0`, and no missing value is ever reinterpreted as
"neutral."

`TechnicalSnapshot` deliberately does NOT contain a bullish/bearish label, a
confidence score, or any aggregate score. Turning these raw facts into a
directional read is `engines/setup_detection.py`'s job, once the
architecture specifies how (ARCHITECTURE.md documents this as an open,
unresolved gate — see the "Directional Vote Specification" note there). This
module stops at facts by design, not by oversight.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from pydantic import BaseModel

from app.domain.market.models import Candle, Timeframe
from app.domain.technical.momentum import MACDResult, RSIResult, calculate_macd, calculate_rsi
from app.domain.technical.series import bounded_series
from app.domain.technical.structure import (
    SwingPointsResult,
    VWAPResult,
    calculate_vwap,
    find_swing_points,
)
from app.domain.technical.trend import EMAResult, calculate_ema
from app.domain.technical.volatility import ATRResult, calculate_atr
from app.domain.technical.volume import RelativeVolumeResult, calculate_relative_volume


class TechnicalSnapshotPeriods(BaseModel):
    """Every period/window this snapshot needs, explicit and required — no
    baked-in defaults anywhere in this package. Choosing these values is a
    strategy-level decision this module deliberately does not make; the
    caller must supply every one of them.
    """

    ema_period: int
    rsi_period: int
    macd_fast_period: int
    macd_slow_period: int
    macd_signal_period: int
    atr_period: int
    relative_volume_period: int
    swing_left_bars: int
    swing_right_bars: int


class TechnicalSnapshot(BaseModel):
    """A fact container: the seven technical primitives, each preserved
    exactly as computed, for one instrument/timeframe/as_of. See the module
    docstring for what this deliberately does not contain.
    """

    instrument_id: str
    timeframe: Timeframe
    as_of: datetime
    periods: TechnicalSnapshotPeriods
    candles_available: int
    latest_candle_timestamp: datetime | None

    ema: EMAResult
    rsi: RSIResult
    macd: MACDResult
    atr: ATRResult
    vwap: VWAPResult
    swing_points: SwingPointsResult
    relative_volume: RelativeVolumeResult


def assemble_technical_snapshot(
    candles: Sequence[Candle],
    *,
    instrument_id: str,
    timeframe: Timeframe,
    as_of: datetime,
    periods: TechnicalSnapshotPeriods,
) -> TechnicalSnapshot:
    """Computes all seven primitives against the same `as_of` boundary and
    bundles them. Each primitive independently re-applies `bounded_series()`
    (via its own function) — this call additionally applies it once more,
    directly, purely to derive the snapshot-level `candles_available` /
    `latest_candle_timestamp` metadata; it is not a second, competing
    filtering implementation, just a reuse of the same shared boundary for
    an audit field. Malformed input (out-of-order/duplicate timestamps,
    mismatched instrument/timeframe) is not caught here — it propagates as
    `InvalidCandleSeriesError`, exactly as it does from every individual
    indicator function, because that is a data-integrity bug signal, not a
    business condition this snapshot should silently absorb.
    """
    bounded = bounded_series(candles, instrument_id=instrument_id, timeframe=timeframe, as_of=as_of)

    return TechnicalSnapshot(
        instrument_id=instrument_id,
        timeframe=timeframe,
        as_of=as_of,
        periods=periods,
        candles_available=len(bounded),
        latest_candle_timestamp=bounded[-1].freshness.data_timestamp if bounded else None,
        ema=calculate_ema(
            candles, instrument_id=instrument_id, timeframe=timeframe, period=periods.ema_period, as_of=as_of
        ),
        rsi=calculate_rsi(
            candles, instrument_id=instrument_id, timeframe=timeframe, period=periods.rsi_period, as_of=as_of
        ),
        macd=calculate_macd(
            candles,
            instrument_id=instrument_id,
            timeframe=timeframe,
            fast_period=periods.macd_fast_period,
            slow_period=periods.macd_slow_period,
            signal_period=periods.macd_signal_period,
            as_of=as_of,
        ),
        atr=calculate_atr(
            candles, instrument_id=instrument_id, timeframe=timeframe, period=periods.atr_period, as_of=as_of
        ),
        vwap=calculate_vwap(candles, instrument_id=instrument_id, timeframe=timeframe, as_of=as_of),
        swing_points=find_swing_points(
            candles,
            instrument_id=instrument_id,
            timeframe=timeframe,
            left_bars=periods.swing_left_bars,
            right_bars=periods.swing_right_bars,
            as_of=as_of,
        ),
        relative_volume=calculate_relative_volume(
            candles,
            instrument_id=instrument_id,
            timeframe=timeframe,
            period=periods.relative_volume_period,
            as_of=as_of,
        ),
    )
