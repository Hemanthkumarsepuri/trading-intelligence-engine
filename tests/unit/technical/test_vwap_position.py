from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.market.models import Timeframe
from app.domain.technical.series import IndicatorStatus, InvalidCandleSeriesError
from app.domain.technical.vwap_position import VWAPPositionState, compute_vwap_position
from tests.unit.technical.factories import make_candle

T0 = datetime(2026, 8, 27, 9, 15, tzinfo=UTC)
STEP = timedelta(minutes=5)


def test_vwap_position_at_exact_equality() -> None:
    # Single candle, H and L symmetric around C -> typical price == C == vwap
    # (single candle: vwap = typical price exactly). This exercises AT via
    # exact Decimal equality, the same zero-tolerance convention
    # structure_facts.py already uses for EQUAL_HIGH/EQUAL_LOW.
    candles = [make_candle(T0, open_=10, high=12, low=8, close=10, volume=1000)]
    result = compute_vwap_position(candles, instrument_id="INST1", timeframe=Timeframe.M5, as_of=T0)

    assert result.status == IndicatorStatus.OK
    assert result.vwap_value == Decimal(10)
    assert result.current_price == Decimal(10)
    assert result.state == VWAPPositionState.AT


def test_vwap_position_below_hand_verified() -> None:
    # Same series as test_structure.py's hand-verified VWAP case:
    # c1: H=10,L=8,C=9,vol=100 -> typical=9  -> pv=900
    # c2: H=12,L=10,C=11,vol=200 -> typical=11 -> pv=2200
    # c3: H=11,L=9,C=10,vol=100 -> typical=10 -> pv=1000
    # total_pv=4100, total_volume=400 -> vwap=10.25; current_price(last close)=10 < 10.25 -> BELOW
    rows = [(9, 10, 8, 9, 100), (11, 12, 10, 11, 200), (10, 11, 9, 10, 100)]
    candles = [
        make_candle(T0 + i * STEP, open_=o, high=h, low=lo, close=c, volume=v)
        for i, (o, h, lo, c, v) in enumerate(rows)
    ]
    result = compute_vwap_position(candles, instrument_id="INST1", timeframe=Timeframe.M5, as_of=candles[-1].freshness.data_timestamp)

    assert result.status == IndicatorStatus.OK
    assert result.vwap_value == Decimal("10.25")
    assert result.current_price == Decimal(10)
    assert result.state == VWAPPositionState.BELOW


def test_vwap_position_above_hand_verified() -> None:
    # c1: H=10,L=8,C=9,vol=100  -> typical=9  -> pv=900
    # c2: H=13,L=11,C=12,vol=100 -> typical=12 -> pv=1200
    # total_pv=2100, total_volume=200 -> vwap=10.5; current_price=12 > 10.5 -> ABOVE
    rows = [(9, 10, 8, 9, 100), (12, 13, 11, 12, 100)]
    candles = [
        make_candle(T0 + i * STEP, open_=o, high=h, low=lo, close=c, volume=v)
        for i, (o, h, lo, c, v) in enumerate(rows)
    ]
    result = compute_vwap_position(candles, instrument_id="INST1", timeframe=Timeframe.M5, as_of=candles[-1].freshness.data_timestamp)

    assert result.status == IndicatorStatus.OK
    assert result.vwap_value == Decimal("10.5")
    assert result.current_price == Decimal(12)
    assert result.state == VWAPPositionState.ABOVE


def test_vwap_position_insufficient_history_on_zero_volume() -> None:
    candles = [
        make_candle(T0, open_=10, high=11, low=9, close=10, volume=0),
        make_candle(T0 + STEP, open_=10, high=11, low=9, close=10, volume=0),
    ]
    result = compute_vwap_position(candles, instrument_id="INST1", timeframe=Timeframe.M5, as_of=candles[-1].freshness.data_timestamp)

    assert result.status == IndicatorStatus.INSUFFICIENT_HISTORY
    assert result.state is None
    assert result.current_price is None
    assert result.vwap_value is None


def test_vwap_position_insufficient_history_on_no_candles() -> None:
    result = compute_vwap_position([], instrument_id="INST1", timeframe=Timeframe.M5, as_of=T0)

    assert result.status == IndicatorStatus.INSUFFICIENT_HISTORY
    assert result.candles_used == 0


def test_vwap_position_propagates_invalid_candle_series_error() -> None:
    c1 = make_candle(T0, open_=10, high=11, low=9, close=10, volume=10)
    c2 = make_candle(T0, open_=10, high=11, low=9, close=10, volume=10)  # duplicate timestamp

    with pytest.raises(InvalidCandleSeriesError):
        compute_vwap_position([c1, c2], instrument_id="INST1", timeframe=Timeframe.M5, as_of=T0 + timedelta(hours=1))


def test_vwap_position_result_has_no_directional_vocabulary() -> None:
    candles = [make_candle(T0, open_=10, high=12, low=8, close=10, volume=1000)]
    result = compute_vwap_position(candles, instrument_id="INST1", timeframe=Timeframe.M5, as_of=T0)

    field_names = set(type(result).model_fields.keys())
    forbidden = {"bullish", "bearish", "direction", "confidence", "signal", "trend"}
    assert field_names.isdisjoint(forbidden)

    state_values = {s.value for s in VWAPPositionState}
    assert state_values == {"ABOVE", "BELOW", "AT"}
    assert state_values.isdisjoint({"BULLISH", "BEARISH", "NEUTRAL"})
