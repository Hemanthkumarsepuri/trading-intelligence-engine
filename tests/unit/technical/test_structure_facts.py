from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.market.models import Candle, Timeframe
from app.domain.technical.series import IndicatorStatus, InvalidCandleSeriesError
from app.domain.technical.structure_facts import (
    HighComparison,
    LowComparison,
    compute_structure_facts,
)
from tests.unit.technical.factories import make_candle

T0 = datetime(2026, 8, 27, 9, 15, tzinfo=UTC)
STEP = timedelta(minutes=5)


def _candles(rows: Sequence[tuple[float, float]]) -> list[Candle]:
    return [
        make_candle(T0 + i * STEP, open_=(h + lo) / 2, high=h, low=lo, close=(h + lo) / 2, volume=1000)
        for i, (h, lo) in enumerate(rows)
    ]


def test_structure_facts_insufficient_history() -> None:
    candles = _candles([(10, 8), (12, 9)])  # left+right+1 = 3 needed, only 2
    result = compute_structure_facts(candles, instrument_id="INST1", timeframe=Timeframe.M5, left_bars=1, right_bars=1, as_of=candles[-1].freshness.data_timestamp)

    assert result.status == IndicatorStatus.INSUFFICIENT_HISTORY
    assert result.high_comparisons == []
    assert result.low_comparisons == []


def test_structure_facts_higher_high_and_higher_low() -> None:
    # Swing highs at idx1(H=12) then idx4(H=14) with idx1<idx4 in price -> HIGHER_HIGH
    # Swing lows  at idx2(L=5)  then idx5(L=6)  with idx2<idx5 in price -> HIGHER_LOW
    rows = [(10, 8), (12, 9), (9, 5), (11, 7), (14, 10), (12, 6), (10, 8)]
    candles = _candles(rows)
    result = compute_structure_facts(candles, instrument_id="INST1", timeframe=Timeframe.M5, left_bars=1, right_bars=1, as_of=candles[-1].freshness.data_timestamp)

    assert result.status == IndicatorStatus.OK
    high_labels = [c.comparison for c in result.high_comparisons]
    low_labels = [c.comparison for c in result.low_comparisons]
    assert HighComparison.HIGHER_HIGH in high_labels
    assert LowComparison.HIGHER_LOW in low_labels


def test_structure_facts_lower_high_and_lower_low() -> None:
    # Same shape as the HIGHER_HIGH/HIGHER_LOW series above, time-reversed,
    # so the swing sequence descends instead of ascends: swing highs at
    # idx2(14) then idx5(12) -> LOWER_HIGH; swing lows at idx1(6) then
    # idx4(5) -> LOWER_LOW.
    rows = [(10, 8), (12, 6), (14, 10), (11, 7), (9, 5), (12, 9), (10, 8)]
    candles = _candles(rows)
    result = compute_structure_facts(candles, instrument_id="INST1", timeframe=Timeframe.M5, left_bars=1, right_bars=1, as_of=candles[-1].freshness.data_timestamp)

    assert result.status == IndicatorStatus.OK
    high_labels = [c.comparison for c in result.high_comparisons]
    low_labels = [c.comparison for c in result.low_comparisons]
    assert HighComparison.LOWER_HIGH in high_labels
    assert LowComparison.LOWER_LOW in low_labels


def test_structure_facts_equal_high_and_equal_low() -> None:
    # Two swing highs of the exact same price, separated by a lower point,
    # each still individually a strict local max/min within its own window.
    rows = [(10, 5), (13, 6), (9, 3), (13, 6), (10, 5)]
    candles = _candles(rows)
    result = compute_structure_facts(candles, instrument_id="INST1", timeframe=Timeframe.M5, left_bars=1, right_bars=1, as_of=candles[-1].freshness.data_timestamp)

    assert result.status == IndicatorStatus.OK
    high_labels = [c.comparison for c in result.high_comparisons]
    assert HighComparison.EQUAL_HIGH in high_labels


def test_structure_facts_no_swings_found_is_ok_not_insufficient() -> None:
    # Strictly monotonic -> zero swing points -> zero comparisons, but the
    # underlying series was sufficient, so this is OK, not INSUFFICIENT_HISTORY.
    rows = [(10, 5), (11, 6), (12, 7), (13, 8), (14, 9)]
    candles = _candles(rows)
    result = compute_structure_facts(candles, instrument_id="INST1", timeframe=Timeframe.M5, left_bars=1, right_bars=1, as_of=candles[-1].freshness.data_timestamp)

    assert result.status == IndicatorStatus.OK
    assert result.high_comparisons == []
    assert result.low_comparisons == []


def test_structure_facts_single_swing_has_no_comparison_pair() -> None:
    # Exactly enough candles for one confirmed swing high/low but not a
    # second one to pair it against -> status OK, comparisons empty.
    rows = [(10, 8), (12, 5), (9, 7)]
    candles = _candles(rows)
    result = compute_structure_facts(candles, instrument_id="INST1", timeframe=Timeframe.M5, left_bars=1, right_bars=1, as_of=candles[-1].freshness.data_timestamp)

    assert result.status == IndicatorStatus.OK
    assert result.high_comparisons == []
    assert result.low_comparisons == []


def test_structure_facts_future_swing_points_excluded_by_as_of() -> None:
    rows = [(10, 8), (12, 9), (9, 5), (11, 7), (14, 10), (12, 6), (10, 8)]
    all_candles = _candles(rows)
    # Pin as_of to exclude the last two candles (which contain the second,
    # higher swing high/low) -> only the earlier swing should be visible,
    # so no HIGHER_HIGH/HIGHER_LOW pair can appear yet.
    as_of = all_candles[3].freshness.data_timestamp

    result = compute_structure_facts(all_candles, instrument_id="INST1", timeframe=Timeframe.M5, left_bars=1, right_bars=1, as_of=as_of)

    assert result.status == IndicatorStatus.OK
    assert result.high_comparisons == []  # only one swing high confirmable within this bound
    assert result.candles_used == 4


def test_structure_facts_deterministic_and_future_invariant() -> None:
    rows = [(10, 8), (12, 9), (9, 5), (11, 7), (14, 10), (12, 6), (10, 8)]
    through_t = _candles(rows)
    as_of = through_t[-1].freshness.data_timestamp
    future = [
        make_candle(as_of + (i + 1) * STEP, open_=1, high=9999, low=1, close=1, volume=1000) for i in range(3)
    ]

    before = compute_structure_facts(through_t, instrument_id="INST1", timeframe=Timeframe.M5, left_bars=1, right_bars=1, as_of=as_of)
    after = compute_structure_facts(through_t + future, instrument_id="INST1", timeframe=Timeframe.M5, left_bars=1, right_bars=1, as_of=as_of)

    assert before == after


def test_structure_facts_propagates_invalid_candle_series_error() -> None:
    c1 = make_candle(T0, open_=10, high=11, low=9, close=10, volume=10)
    c2 = make_candle(T0, open_=10, high=11, low=9, close=10, volume=10)  # duplicate timestamp

    with pytest.raises(InvalidCandleSeriesError):
        compute_structure_facts([c1, c2], instrument_id="INST1", timeframe=Timeframe.M5, left_bars=1, right_bars=1, as_of=T0 + timedelta(hours=1))


def test_structure_facts_does_not_contain_trend_or_reversal_labels() -> None:
    rows = [(10, 8), (12, 9), (9, 5), (11, 7), (14, 10), (12, 6), (10, 8)]
    candles = _candles(rows)
    result = compute_structure_facts(candles, instrument_id="INST1", timeframe=Timeframe.M5, left_bars=1, right_bars=1, as_of=candles[-1].freshness.data_timestamp)

    field_names = set(type(result).model_fields.keys())
    forbidden = {"trend", "uptrend", "downtrend", "reversal", "breakout", "breakdown", "bullish", "bearish"}
    assert field_names.isdisjoint(forbidden)
