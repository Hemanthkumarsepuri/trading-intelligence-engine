from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.market.models import Timeframe
from app.domain.technical.series import InvalidCandleSeriesError, bounded_series
from tests.unit.technical.factories import make_candle, make_series_from_closes

T0 = datetime(2026, 8, 27, 9, 15, tzinfo=UTC)


def test_bounded_series_excludes_candles_after_as_of() -> None:
    candles = make_series_from_closes([100, 101, 102, 103, 104], start=T0)
    as_of = candles[2].freshness.data_timestamp

    result = bounded_series(candles, instrument_id="INST1", timeframe=Timeframe.M5, as_of=as_of)

    assert len(result) == 3
    assert all(c.freshness.data_timestamp <= as_of for c in result)


def test_bounded_series_includes_candle_exactly_at_as_of() -> None:
    candles = make_series_from_closes([100, 101], start=T0)
    as_of = candles[-1].freshness.data_timestamp

    result = bounded_series(candles, instrument_id="INST1", timeframe=Timeframe.M5, as_of=as_of)

    assert result == candles


def test_bounded_series_empty_when_as_of_before_all_data() -> None:
    candles = make_series_from_closes([100, 101], start=T0)
    as_of = T0 - timedelta(minutes=5)

    result = bounded_series(candles, instrument_id="INST1", timeframe=Timeframe.M5, as_of=as_of)

    assert result == []


def test_bounded_series_rejects_naive_as_of() -> None:
    candles = make_series_from_closes([100], start=T0)
    with pytest.raises(ValueError, match="timezone-aware"):
        bounded_series(
            candles, instrument_id="INST1", timeframe=Timeframe.M5, as_of=datetime(2026, 8, 27, 9, 15)  # noqa: DTZ001
        )


def test_bounded_series_rejects_mismatched_instrument() -> None:
    candles = make_series_from_closes([100, 101], start=T0, instrument_id="OTHER")
    with pytest.raises(InvalidCandleSeriesError, match="instrument_id"):
        bounded_series(candles, instrument_id="INST1", timeframe=Timeframe.M5, as_of=T0 + timedelta(hours=1))


def test_bounded_series_rejects_mismatched_timeframe() -> None:
    candles = make_series_from_closes([100, 101], start=T0, timeframe=Timeframe.M15)
    with pytest.raises(InvalidCandleSeriesError, match="timeframe"):
        bounded_series(candles, instrument_id="INST1", timeframe=Timeframe.M5, as_of=T0 + timedelta(hours=1))


def test_bounded_series_rejects_out_of_order_timestamps() -> None:
    c1 = make_candle(T0, open_=100, high=101, low=99, close=100, volume=10)
    c2 = make_candle(T0 - timedelta(minutes=5), open_=100, high=101, low=99, close=100, volume=10)

    with pytest.raises(InvalidCandleSeriesError, match="ascending"):
        bounded_series([c1, c2], instrument_id="INST1", timeframe=Timeframe.M5, as_of=T0 + timedelta(hours=1))


def test_bounded_series_rejects_duplicate_timestamps() -> None:
    c1 = make_candle(T0, open_=100, high=101, low=99, close=100, volume=10)
    c2 = make_candle(T0, open_=100, high=101, low=99, close=101, volume=10)

    with pytest.raises(InvalidCandleSeriesError, match="duplicate"):
        bounded_series([c1, c2], instrument_id="INST1", timeframe=Timeframe.M5, as_of=T0 + timedelta(hours=1))
