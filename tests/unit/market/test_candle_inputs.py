from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.market.candle_inputs import InvalidCandleSeriesError, assemble_candle_inputs
from app.domain.market.models import Timeframe
from tests.unit.technical.factories import make_candle, make_series_from_closes

T0 = datetime(2026, 8, 27, 9, 15, tzinfo=UTC)


def test_assembles_multiple_timeframes_independently() -> None:
    m5 = make_series_from_closes([100, 101, 102], start=T0, timeframe=Timeframe.M5)
    h1 = make_series_from_closes([200, 201], start=T0, timeframe=Timeframe.H1, step=timedelta(hours=1))
    as_of = T0 + timedelta(hours=2)

    result = assemble_candle_inputs({Timeframe.M5: m5, Timeframe.H1: h1}, instrument_id="INST1", as_of=as_of)

    assert set(result.keys()) == {Timeframe.M5, Timeframe.H1}
    assert result[Timeframe.M5] == m5
    assert result[Timeframe.H1] == h1


def test_bounds_each_timeframe_to_as_of_independently() -> None:
    m5 = make_series_from_closes([100, 101, 102, 103], start=T0, timeframe=Timeframe.M5)
    as_of = m5[1].freshness.data_timestamp  # only the first 2 of 4 candles qualify

    result = assemble_candle_inputs({Timeframe.M5: m5}, instrument_id="INST1", as_of=as_of)

    assert result[Timeframe.M5] == m5[:2]


def test_declared_but_empty_timeframe_yields_empty_list_not_an_error() -> None:
    as_of = T0 + timedelta(hours=1)
    result = assemble_candle_inputs({Timeframe.M5: []}, instrument_id="INST1", as_of=as_of)
    assert result[Timeframe.M5] == []


def test_undeclared_timeframe_is_absent_from_result() -> None:
    m5 = make_series_from_closes([100, 101], start=T0, timeframe=Timeframe.M5)
    as_of = T0 + timedelta(hours=1)

    result = assemble_candle_inputs({Timeframe.M5: m5}, instrument_id="INST1", as_of=as_of)

    assert Timeframe.H1 not in result


def test_out_of_order_candles_raise_and_are_not_silently_sorted() -> None:
    c1 = make_candle(T0, open_=100, high=101, low=99, close=100, volume=10)
    c2 = make_candle(T0 - timedelta(minutes=5), open_=100, high=101, low=99, close=100, volume=10)

    with pytest.raises(InvalidCandleSeriesError, match="ascending"):
        assemble_candle_inputs(
            {Timeframe.M5: [c1, c2]}, instrument_id="INST1", as_of=T0 + timedelta(hours=1)
        )


def test_duplicate_timestamps_raise_and_are_not_silently_deduplicated() -> None:
    c1 = make_candle(T0, open_=100, high=101, low=99, close=100, volume=10)
    c2 = make_candle(T0, open_=100, high=101, low=99, close=101, volume=10)

    with pytest.raises(InvalidCandleSeriesError, match="duplicate"):
        assemble_candle_inputs(
            {Timeframe.M5: [c1, c2]}, instrument_id="INST1", as_of=T0 + timedelta(hours=1)
        )


def test_candle_mismatched_to_its_own_dict_key_timeframe_raises() -> None:
    # Candle genuinely tagged M5, filed under the H1 key -> caught by the
    # existing bounded_series() mismatch check, not silently accepted.
    m5_candle = make_candle(T0, open_=100, high=101, low=99, close=100, volume=10, timeframe=Timeframe.M5)

    with pytest.raises(InvalidCandleSeriesError, match="timeframe"):
        assemble_candle_inputs(
            {Timeframe.H1: [m5_candle]}, instrument_id="INST1", as_of=T0 + timedelta(hours=1)
        )


def test_candle_mismatched_instrument_id_raises() -> None:
    other_instrument_candle = make_candle(
        T0, open_=100, high=101, low=99, close=100, volume=10, instrument_id="OTHER"
    )

    with pytest.raises(InvalidCandleSeriesError, match="instrument_id"):
        assemble_candle_inputs(
            {Timeframe.M5: [other_instrument_candle]}, instrument_id="INST1", as_of=T0 + timedelta(hours=1)
        )
