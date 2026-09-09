from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.market.models import Candle, Timeframe
from app.domain.technical.series import IndicatorStatus
from app.domain.technical.volatility import calculate_atr
from tests.unit.technical.factories import make_candle

T0 = datetime(2026, 8, 27, 9, 15, tzinfo=UTC)
STEP = timedelta(minutes=5)


def _candles(n: int) -> list[Candle]:
    # Deliberately chosen high/low/close so True Range is easy to hand-verify:
    # candle0: O=H=L=C=10 (baseline; contributes no TR — needs a previous close)
    # candle1: H=12,L=8,C=11  (prev close=10) -> TR = max(4, |12-10|=2, |8-10|=2)  = 4
    # candle2: H=13,L=11,C=12 (prev close=11) -> TR = max(2, |13-11|=2, |11-11|=0) = 2
    # candle3: H=14,L=9,C=10  (prev close=12) -> TR = max(5, |14-12|=2, |9-12|=3)  = 5
    rows = [
        (10, 10, 10, 10),
        (11, 12, 8, 11),
        (12, 13, 11, 12),
        (10, 14, 9, 10),
    ]
    return [
        make_candle(T0 + i * STEP, open_=o, high=h, low=lo, close=c, volume=1000)
        for i, (o, h, lo, c) in enumerate(rows[:n])
    ]


def test_atr_insufficient_history() -> None:
    candles = _candles(2)  # period=2 needs 3 candles (period+1)
    result = calculate_atr(candles, instrument_id="INST1", timeframe=Timeframe.M5, period=2, as_of=candles[-1].freshness.data_timestamp)

    assert result.status == IndicatorStatus.INSUFFICIENT_HISTORY
    assert result.value is None


def test_atr_exactly_sufficient_history_equals_seed_average() -> None:
    candles = _candles(3)  # TRs = [4, 2] -> seed = mean(4, 2) = 3, no smoothing step yet
    result = calculate_atr(candles, instrument_id="INST1", timeframe=Timeframe.M5, period=2, as_of=candles[-1].freshness.data_timestamp)

    assert result.status == IndicatorStatus.OK
    assert result.value == Decimal(3)


def test_atr_hand_verified_value_with_one_smoothing_step() -> None:
    # TRs = [4, 2, 5]; seed = mean(4, 2) = 3; step(TR=5): (3*1 + 5)/2 = 4
    candles = _candles(4)
    result = calculate_atr(candles, instrument_id="INST1", timeframe=Timeframe.M5, period=2, as_of=candles[-1].freshness.data_timestamp)

    assert result.status == IndicatorStatus.OK
    assert result.value == Decimal(4)
    assert result.candles_used == 4


def test_atr_is_never_negative_on_a_volatile_series() -> None:
    rows = [(100, 105, 95, 102), (102, 110, 90, 95), (95, 130, 60, 100), (100, 101, 99, 100), (100, 120, 80, 110)]
    candles = [
        make_candle(T0 + i * STEP, open_=o, high=h, low=lo, close=c, volume=1000)
        for i, (o, h, lo, c) in enumerate(rows)
    ]
    result = calculate_atr(candles, instrument_id="INST1", timeframe=Timeframe.M5, period=3, as_of=candles[-1].freshness.data_timestamp)

    assert result.status == IndicatorStatus.OK
    assert result.value is not None
    assert result.value > 0
