from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.domain.market.models import Timeframe
from app.domain.technical.momentum import calculate_macd, calculate_rsi
from app.domain.technical.series import IndicatorStatus
from tests.unit.technical.factories import make_series_from_closes

T0 = datetime(2026, 8, 27, 9, 15, tzinfo=UTC)


# -- RSI ----------------------------------------------------------------


def test_rsi_insufficient_history() -> None:
    candles = make_series_from_closes([10, 11], start=T0)  # period=2 needs 3 candles
    result = calculate_rsi(candles, instrument_id="INST1", timeframe=Timeframe.M5, period=2, as_of=candles[-1].freshness.data_timestamp)

    assert result.status == IndicatorStatus.INSUFFICIENT_HISTORY
    assert result.value is None


def test_rsi_all_gains_is_exactly_100() -> None:
    candles = make_series_from_closes([10, 11, 12, 13, 14], start=T0)
    result = calculate_rsi(candles, instrument_id="INST1", timeframe=Timeframe.M5, period=2, as_of=candles[-1].freshness.data_timestamp)

    assert result.status == IndicatorStatus.OK
    assert result.value == Decimal(100)


def test_rsi_all_losses_is_exactly_zero() -> None:
    candles = make_series_from_closes([14, 13, 12, 11, 10], start=T0)
    result = calculate_rsi(candles, instrument_id="INST1", timeframe=Timeframe.M5, period=2, as_of=candles[-1].freshness.data_timestamp)

    assert result.status == IndicatorStatus.OK
    assert result.value == Decimal(0)


def test_rsi_hand_verified_value() -> None:
    # closes = [10, 12, 11, 13, 10], period = 2
    # deltas:      +2   -1   +2   -3
    # gains:  [2, 0, 2, 0]   losses: [0, 1, 0, 3]
    # seed avg_gain = mean(2, 0) = 1        seed avg_loss = mean(0, 1) = 0.5
    # step (gain=2, loss=0): avg_gain = (1*1 + 2)/2 = 1.5   avg_loss = (0.5*1 + 0)/2 = 0.25
    # step (gain=0, loss=3): avg_gain = (1.5*1 + 0)/2 = 0.75  avg_loss = (0.25*1 + 3)/2 = 1.625
    # RS = 0.75 / 1.625 = 6/13;  RSI = 100 - 100/(1 + 6/13) = 100 - 1300/19 = 600/19 = 31.578947368...
    candles = make_series_from_closes([10, 12, 11, 13, 10], start=T0)
    result = calculate_rsi(candles, instrument_id="INST1", timeframe=Timeframe.M5, period=2, as_of=candles[-1].freshness.data_timestamp)

    assert result.status == IndicatorStatus.OK
    assert result.value is not None
    assert abs(result.value - Decimal(600) / Decimal(19)) < Decimal("0.0000001")


def test_rsi_as_of_bounds_to_correct_prefix() -> None:
    candles = make_series_from_closes([10, 12, 11, 13, 10], start=T0)
    as_of = candles[2].freshness.data_timestamp  # stop right after the close=11 candle

    result = calculate_rsi(candles, instrument_id="INST1", timeframe=Timeframe.M5, period=2, as_of=as_of)

    # Only [10, 12, 11] available -> deltas [+2, -1] -> exactly period=2 deltas,
    # no Wilder smoothing step yet: avg_gain=mean(2,0)=1, avg_loss=mean(0,1)=0.5
    # RS = 1/0.5 = 2; RSI = 100 - 100/3 = 200/3
    assert result.status == IndicatorStatus.OK
    assert result.value is not None
    assert abs(result.value - Decimal(200) / Decimal(3)) < Decimal("0.0000001")


# -- MACD -----------------------------------------------------------------


def test_macd_insufficient_history() -> None:
    # fast=2, slow=3, signal=2 -> minimum = slow + signal - 1 = 4 candles
    candles = make_series_from_closes([1, 2, 3], start=T0)
    result = calculate_macd(
        candles,
        instrument_id="INST1",
        timeframe=Timeframe.M5,
        fast_period=2,
        slow_period=3,
        signal_period=2,
        as_of=candles[-1].freshness.data_timestamp,
    )

    assert result.status == IndicatorStatus.INSUFFICIENT_HISTORY
    assert result.macd_line is None
    assert result.signal_line is None
    assert result.histogram is None


def test_macd_hand_verified_at_exact_minimum_history() -> None:
    # closes = [1, 2, 3, 4], fast=2, slow=3, signal=2 (minimum = 4 candles)
    # fast EMA(2): seed=avg(1,2)=1.5 @idx1; @idx2(close=3): (3-1.5)*2/3+1.5=2.5; @idx3(close=4): (4-2.5)*2/3+2.5=3.5
    # slow EMA(3): seed=avg(1,2,3)=2 @idx2; @idx3(close=4): (4-2)*0.5+2=3
    # aligned fast (offset=1) = [2.5, 3.5] vs slow = [2, 3] -> macd_line_series = [0.5, 0.5]
    # signal EMA(2) over [0.5, 0.5]: seed=avg(0.5,0.5)=0.5 (exactly sufficient, no smoothing step)
    # macd_line = 0.5, signal_line = 0.5, histogram = 0
    candles = make_series_from_closes([1, 2, 3, 4], start=T0)
    result = calculate_macd(
        candles,
        instrument_id="INST1",
        timeframe=Timeframe.M5,
        fast_period=2,
        slow_period=3,
        signal_period=2,
        as_of=candles[-1].freshness.data_timestamp,
    )

    assert result.status == IndicatorStatus.OK
    assert result.macd_line == Decimal("0.5")
    assert result.signal_line == Decimal("0.5")
    assert result.histogram == Decimal(0)


def test_macd_hand_verified_with_extra_history() -> None:
    # Same fast/slow/signal as above but a longer linear series [1..7] — two
    # EMAs of a straight line settle into a constant lag, so macd_line stays
    # exactly 0.5 once past warm-up. Confirms `as_of`/history beyond the
    # minimum doesn't corrupt the result and the recursion is stable.
    candles = make_series_from_closes([1, 2, 3, 4, 5, 6, 7], start=T0)
    result = calculate_macd(
        candles,
        instrument_id="INST1",
        timeframe=Timeframe.M5,
        fast_period=2,
        slow_period=3,
        signal_period=2,
        as_of=candles[-1].freshness.data_timestamp,
    )

    assert result.status == IndicatorStatus.OK
    assert result.macd_line == Decimal("0.5")
    assert result.signal_line == Decimal("0.5")
    assert result.histogram == Decimal(0)


def test_macd_histogram_always_equals_macd_minus_signal() -> None:
    # Non-linear series — no hand-derived expected value, just verifies the
    # structural invariant every valid MACD result must satisfy.
    candles = make_series_from_closes([1, 2, 3, 4, 3, 2, 1, 2, 3, 4], start=T0)
    result = calculate_macd(
        candles,
        instrument_id="INST1",
        timeframe=Timeframe.M5,
        fast_period=3,
        slow_period=6,
        signal_period=2,
        as_of=candles[-1].freshness.data_timestamp,
    )

    assert result.status == IndicatorStatus.OK
    assert result.macd_line is not None
    assert result.signal_line is not None
    assert result.histogram == result.macd_line - result.signal_line
