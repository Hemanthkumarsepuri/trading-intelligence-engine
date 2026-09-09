from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.market.models import Candle, Timeframe
from app.domain.technical.series import IndicatorStatus
from app.domain.technical.volume import calculate_relative_volume
from tests.unit.technical.factories import make_candle

T0 = datetime(2026, 8, 27, 9, 15, tzinfo=UTC)
STEP = timedelta(minutes=5)


def _candles_with_volumes(volumes: list[int]) -> list[Candle]:
    return [
        make_candle(T0 + i * STEP, open_=100, high=101, low=99, close=100, volume=v)
        for i, v in enumerate(volumes)
    ]


def test_relative_volume_insufficient_history() -> None:
    candles = _candles_with_volumes([100, 200])  # period=2 needs 3 candles (period + current)
    result = calculate_relative_volume(candles, instrument_id="INST1", timeframe=Timeframe.M5, period=2, as_of=candles[-1].freshness.data_timestamp)

    assert result.status == IndicatorStatus.INSUFFICIENT_HISTORY
    assert result.ratio is None


def test_relative_volume_hand_verified() -> None:
    # baseline = the 2 candles preceding the latest: volumes [200, 300] -> avg = 250
    # current volume = 900 -> ratio = 900 / 250 = 3.6
    candles = _candles_with_volumes([100, 200, 300, 900])
    result = calculate_relative_volume(candles, instrument_id="INST1", timeframe=Timeframe.M5, period=2, as_of=candles[-1].freshness.data_timestamp)

    assert result.status == IndicatorStatus.OK
    assert result.current_volume == 900
    assert result.average_volume == Decimal(250)
    assert result.ratio == Decimal("3.6")


def test_relative_volume_excludes_current_candle_from_its_own_baseline() -> None:
    # If the current (huge) candle were folded into its own baseline average,
    # the ratio would be diluted well below what actually happened. Confirm
    # it is not: baseline here is exactly [100, 100], average = 100, and the
    # 1000-volume candle is compared only against that.
    candles = _candles_with_volumes([100, 100, 1000])
    result = calculate_relative_volume(candles, instrument_id="INST1", timeframe=Timeframe.M5, period=2, as_of=candles[-1].freshness.data_timestamp)

    assert result.status == IndicatorStatus.OK
    assert result.average_volume == Decimal(100)
    assert result.ratio == Decimal(10)


def test_relative_volume_zero_baseline_is_insufficient() -> None:
    candles = _candles_with_volumes([0, 0, 500])
    result = calculate_relative_volume(candles, instrument_id="INST1", timeframe=Timeframe.M5, period=2, as_of=candles[-1].freshness.data_timestamp)

    assert result.status == IndicatorStatus.INSUFFICIENT_HISTORY
    assert result.ratio is None
    assert result.current_volume == 500
