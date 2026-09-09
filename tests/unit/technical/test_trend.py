from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.domain.market.models import Timeframe
from app.domain.technical.series import IndicatorStatus
from app.domain.technical.trend import calculate_ema
from tests.unit.technical.factories import make_series_from_closes

T0 = datetime(2026, 8, 27, 9, 15, tzinfo=UTC)


def test_ema_insufficient_history_one_candle_short() -> None:
    candles = make_series_from_closes([1, 2], start=T0)  # period=3 needs 3
    result = calculate_ema(candles, instrument_id="INST1", timeframe=Timeframe.M5, period=3, as_of=candles[-1].freshness.data_timestamp)

    assert result.status == IndicatorStatus.INSUFFICIENT_HISTORY
    assert result.value is None
    assert result.candles_used == 2


def test_ema_exactly_sufficient_history_equals_seed_average() -> None:
    candles = make_series_from_closes([1, 2, 3], start=T0)
    result = calculate_ema(candles, instrument_id="INST1", timeframe=Timeframe.M5, period=3, as_of=candles[-1].freshness.data_timestamp)

    # With exactly `period` candles, EMA has no smoothing step yet — it's
    # just the simple average of the seed window: (1+2+3)/3 = 2.
    assert result.status == IndicatorStatus.OK
    assert result.value == Decimal(2)
    assert result.candles_used == 3


def test_ema_hand_verified_value() -> None:
    # closes = [1, 2, 3, 4, 5], period=3
    # seed = avg(1,2,3) = 2, multiplier k = 2/(3+1) = 0.5
    # step 1 (close=4): (4-2)*0.5 + 2 = 3
    # step 2 (close=5): (5-3)*0.5 + 3 = 4
    candles = make_series_from_closes([1, 2, 3, 4, 5], start=T0)
    result = calculate_ema(candles, instrument_id="INST1", timeframe=Timeframe.M5, period=3, as_of=candles[-1].freshness.data_timestamp)

    assert result.status == IndicatorStatus.OK
    assert result.value == Decimal(4)
    assert result.candles_used == 5


def test_ema_as_of_before_full_history_uses_only_bounded_prefix() -> None:
    # Same series as above, but as_of pinned to the 3rd candle (close=3) —
    # must equal the "exactly sufficient" case, not the full-series result.
    candles = make_series_from_closes([1, 2, 3, 4, 5], start=T0)
    as_of = candles[2].freshness.data_timestamp  # the close=3 candle

    result = calculate_ema(candles, instrument_id="INST1", timeframe=Timeframe.M5, period=3, as_of=as_of)

    assert result.status == IndicatorStatus.OK
    assert result.value == Decimal(2)
    assert result.candles_used == 3


def test_ema_flat_price_series_converges_to_that_price() -> None:
    candles = make_series_from_closes([50, 50, 50, 50, 50, 50], start=T0)
    result = calculate_ema(candles, instrument_id="INST1", timeframe=Timeframe.M5, period=3, as_of=candles[-1].freshness.data_timestamp)

    assert result.status == IndicatorStatus.OK
    assert result.value == Decimal(50)
