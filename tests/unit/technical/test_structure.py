from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.market.models import Candle, Timeframe
from app.domain.technical.series import IndicatorStatus
from app.domain.technical.structure import SwingKind, calculate_vwap, find_swing_points
from tests.unit.technical.factories import make_candle

T0 = datetime(2026, 8, 27, 9, 15, tzinfo=UTC)
STEP = timedelta(minutes=5)


# -- VWAP -----------------------------------------------------------------


def test_vwap_hand_verified_value() -> None:
    # typical price = (H+L+C)/3
    # c1: H=10,L=8,C=9,  vol=100 -> typical=9  -> pv=900
    # c2: H=12,L=10,C=11,vol=200 -> typical=11 -> pv=2200
    # c3: H=11,L=9,C=10, vol=100 -> typical=10 -> pv=1000
    # total_pv=4100, total_volume=400 -> vwap=10.25
    # current_price (last close) = 10 -> deviation = 10 - 10.25 = -0.25
    rows = [(9, 10, 8, 9, 100), (11, 12, 10, 11, 200), (10, 11, 9, 10, 100)]
    candles = [
        make_candle(T0 + i * STEP, open_=o, high=h, low=lo, close=c, volume=v)
        for i, (o, h, lo, c, v) in enumerate(rows)
    ]

    result = calculate_vwap(candles, instrument_id="INST1", timeframe=Timeframe.M5, as_of=candles[-1].freshness.data_timestamp)

    assert result.status == IndicatorStatus.OK
    assert result.value == Decimal("10.25")
    assert result.current_price == Decimal(10)
    assert result.deviation == Decimal("-0.25")
    assert result.deviation_pct is not None
    assert abs(result.deviation_pct - (Decimal("-0.25") / Decimal("10.25") * 100)) < Decimal("0.0000001")


def test_vwap_zero_total_volume_is_insufficient() -> None:
    candles = [
        make_candle(T0, open_=10, high=11, low=9, close=10, volume=0),
        make_candle(T0 + STEP, open_=10, high=11, low=9, close=10, volume=0),
    ]

    result = calculate_vwap(candles, instrument_id="INST1", timeframe=Timeframe.M5, as_of=candles[-1].freshness.data_timestamp)

    assert result.status == IndicatorStatus.INSUFFICIENT_HISTORY
    assert result.value is None


def test_vwap_no_candles_is_insufficient() -> None:
    result = calculate_vwap([], instrument_id="INST1", timeframe=Timeframe.M5, as_of=T0)

    assert result.status == IndicatorStatus.INSUFFICIENT_HISTORY
    assert result.candles_used == 0


# -- Swing points -----------------------------------------------------------


def _swing_series() -> list[Candle]:
    # index: high, low  (open/close set equal to a midpoint; irrelevant here)
    # 0: H=10, L=8
    # 1: H=12, L=9   <- swing high vs window [0,1,2]
    # 2: H=9,  L=5   <- swing low  vs window [1,2,3]
    # 3: H=11, L=7   <- swing high vs window [2,3,4]
    # 4: H=8,  L=6   (most recent — cannot be confirmed with right_bars=1)
    rows = [(10, 8), (12, 9), (9, 5), (11, 7), (8, 6)]
    return [
        make_candle(T0 + i * STEP, open_=(h + lo) / 2, high=h, low=lo, close=(h + lo) / 2, volume=1000)
        for i, (h, lo) in enumerate(rows)
    ]


def test_swing_points_insufficient_history() -> None:
    candles = _swing_series()[:2]  # need left+right+1 = 3, only have 2
    result = find_swing_points(
        candles, instrument_id="INST1", timeframe=Timeframe.M5, left_bars=1, right_bars=1, as_of=candles[-1].freshness.data_timestamp
    )

    assert result.status == IndicatorStatus.INSUFFICIENT_HISTORY
    assert result.swing_highs == []
    assert result.swing_lows == []


def test_swing_points_hand_verified() -> None:
    candles = _swing_series()
    result = find_swing_points(
        candles, instrument_id="INST1", timeframe=Timeframe.M5, left_bars=1, right_bars=1, as_of=candles[-1].freshness.data_timestamp
    )

    assert result.status == IndicatorStatus.OK
    assert [p.price for p in result.swing_highs] == [Decimal(12), Decimal(11)]
    assert all(p.kind == SwingKind.HIGH for p in result.swing_highs)
    assert [p.price for p in result.swing_lows] == [Decimal(5)]
    assert result.swing_lows[0].kind == SwingKind.LOW
    # index 4 (the most recent candle) can never be confirmed with right_bars=1
    assert result.swing_highs[-1].timestamp == candles[3].freshness.data_timestamp
    assert result.swing_lows[-1].timestamp == candles[2].freshness.data_timestamp


def test_swing_points_none_found_is_ok_not_insufficient() -> None:
    # Strictly monotonic high/low series -> no interior candle is a strict
    # local extremum. This must be status=OK with empty lists — genuinely
    # different from INSUFFICIENT_HISTORY's empty lists (we had enough data
    # and checked; there just weren't any swings).
    rows = [(10, 5), (11, 6), (12, 7), (13, 8), (14, 9)]
    candles = [
        make_candle(T0 + i * STEP, open_=(h + lo) / 2, high=h, low=lo, close=(h + lo) / 2, volume=1000)
        for i, (h, lo) in enumerate(rows)
    ]

    result = find_swing_points(
        candles, instrument_id="INST1", timeframe=Timeframe.M5, left_bars=1, right_bars=1, as_of=candles[-1].freshness.data_timestamp
    )

    assert result.status == IndicatorStatus.OK
    assert result.swing_highs == []
    assert result.swing_lows == []


def test_swing_points_tie_does_not_count() -> None:
    # A plateau (equal highs) must not register as a swing high — the
    # comparison is strict, not >=.
    rows = [(10, 5), (12, 5), (12, 5), (12, 5), (10, 5)]
    candles = [
        make_candle(T0 + i * STEP, open_=(h + lo) / 2, high=h, low=lo, close=(h + lo) / 2, volume=1000)
        for i, (h, lo) in enumerate(rows)
    ]

    result = find_swing_points(
        candles, instrument_id="INST1", timeframe=Timeframe.M5, left_bars=1, right_bars=1, as_of=candles[-1].freshness.data_timestamp
    )

    assert result.status == IndicatorStatus.OK
    assert result.swing_highs == []
