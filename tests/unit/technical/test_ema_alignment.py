from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.market.models import Timeframe
from app.domain.technical.ema_alignment import EMAAlignmentState, compute_ema_alignment
from app.domain.technical.series import IndicatorStatus, InvalidCandleSeriesError
from tests.unit.technical.factories import make_candle, make_series_from_closes

T0 = datetime(2026, 8, 27, 9, 15, tzinfo=UTC)


def test_ema_alignment_ascending_hand_verified() -> None:
    # closes = [10,9,8,7,6,5], periods=[2,3,4]
    # EMA(2): seed=avg(10,9)=9.5; each subsequent step: diff=-1.5, k=2/3 -> term=-1 -> final=5.5
    # EMA(3): seed=avg(10,9,8)=9; each step: diff=-2, k=1/2 -> term=-1 -> final=6
    # EMA(4): seed=avg(10,9,8,7)=8.5; each step: diff=-2.5, k=2/5 -> term=-1 -> final=6.5
    # 5.5 < 6 < 6.5 -> ASCENDING
    candles = make_series_from_closes([10, 9, 8, 7, 6, 5], start=T0)
    result = compute_ema_alignment(
        candles, instrument_id="INST1", timeframe=Timeframe.M5, periods=[2, 3, 4], as_of=candles[-1].freshness.data_timestamp
    )

    assert result.status == IndicatorStatus.OK
    assert result.state == EMAAlignmentState.ASCENDING
    assert result.values == [Decimal("5.5"), Decimal("6"), Decimal("6.5")]


def test_ema_alignment_descending_hand_verified() -> None:
    # Mirror of the ascending case: closes = [5,6,7,8,9,10]
    # EMA(2)=9.5, EMA(3)=9, EMA(4)=8.5 -> 9.5 > 9 > 8.5 -> DESCENDING
    candles = make_series_from_closes([5, 6, 7, 8, 9, 10], start=T0)
    result = compute_ema_alignment(
        candles, instrument_id="INST1", timeframe=Timeframe.M5, periods=[2, 3, 4], as_of=candles[-1].freshness.data_timestamp
    )

    assert result.status == IndicatorStatus.OK
    assert result.state == EMAAlignmentState.DESCENDING


def test_ema_alignment_mixed_on_flat_price() -> None:
    # A perfectly flat price series makes every EMA converge to the same
    # value immediately (seed = that value, diff = 0 forever) -> all equal
    # -> neither strictly ascending nor strictly descending -> MIXED.
    candles = make_series_from_closes([10, 10, 10, 10, 10, 10], start=T0)
    result = compute_ema_alignment(
        candles, instrument_id="INST1", timeframe=Timeframe.M5, periods=[2, 3, 4], as_of=candles[-1].freshness.data_timestamp
    )

    assert result.status == IndicatorStatus.OK
    assert result.state == EMAAlignmentState.MIXED
    assert result.values is not None
    assert all(v == result.values[0] for v in result.values)


def test_ema_alignment_insufficient_history() -> None:
    # periods=[2,3,4] needs at least 4 candles for the slowest period; only 3 given.
    candles = make_series_from_closes([10, 9, 8], start=T0)
    result = compute_ema_alignment(
        candles, instrument_id="INST1", timeframe=Timeframe.M5, periods=[2, 3, 4], as_of=candles[-1].freshness.data_timestamp
    )

    assert result.status == IndicatorStatus.INSUFFICIENT_HISTORY
    assert result.values is None
    assert result.state is None


def test_ema_alignment_rejects_fewer_than_two_periods() -> None:
    candles = make_series_from_closes([10, 9, 8], start=T0)
    with pytest.raises(ValueError, match="at least 2"):
        compute_ema_alignment(
            candles, instrument_id="INST1", timeframe=Timeframe.M5, periods=[5], as_of=candles[-1].freshness.data_timestamp
        )


def test_ema_alignment_rejects_non_increasing_periods() -> None:
    candles = make_series_from_closes([10, 9, 8], start=T0)
    with pytest.raises(ValueError, match="strictly increasing"):
        compute_ema_alignment(
            candles, instrument_id="INST1", timeframe=Timeframe.M5, periods=[21, 9, 50], as_of=candles[-1].freshness.data_timestamp
        )
    with pytest.raises(ValueError, match="strictly increasing"):
        compute_ema_alignment(
            candles, instrument_id="INST1", timeframe=Timeframe.M5, periods=[9, 9, 21], as_of=candles[-1].freshness.data_timestamp
        )


def test_ema_alignment_propagates_invalid_candle_series_error() -> None:
    c1 = make_candle(T0, open_=10, high=11, low=9, close=10, volume=10)
    c2 = make_candle(T0 - timedelta(minutes=5), open_=10, high=11, low=9, close=10, volume=10)  # out of order

    with pytest.raises(InvalidCandleSeriesError):
        compute_ema_alignment(
            [c1, c2], instrument_id="INST1", timeframe=Timeframe.M5, periods=[2, 3], as_of=T0 + timedelta(hours=1)
        )


def test_ema_alignment_result_has_no_directional_vocabulary() -> None:
    # Structural guarantee, not just a docstring claim: the result model
    # must never grow a bullish/bearish/direction/confidence field.
    candles = make_series_from_closes([10, 9, 8, 7, 6, 5], start=T0)
    result = compute_ema_alignment(
        candles, instrument_id="INST1", timeframe=Timeframe.M5, periods=[2, 3, 4], as_of=candles[-1].freshness.data_timestamp
    )
    field_names = set(type(result).model_fields.keys())
    forbidden = {"bullish", "bearish", "direction", "confidence", "signal", "trend"}
    assert field_names.isdisjoint(forbidden)

    state_values = {s.value for s in EMAAlignmentState}
    assert state_values == {"ASCENDING", "DESCENDING", "MIXED"}
    assert state_values.isdisjoint({"BULLISH", "BEARISH", "NEUTRAL"})
