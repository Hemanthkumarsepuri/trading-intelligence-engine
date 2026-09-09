from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.domain.market.models import Timeframe
from app.domain.market.timeframe_requirement import (
    TimeframeRequirement,
    check_timeframe_requirements,
)
from app.domain.technical.series import InvalidCandleSeriesError
from tests.unit.technical.factories import make_candle, make_series_from_closes

T0 = datetime(2026, 8, 27, 9, 15, tzinfo=UTC)


def test_requirement_rejects_non_positive_minimum_candles() -> None:
    with pytest.raises(ValidationError):
        TimeframeRequirement(timeframe=Timeframe.M5, minimum_candles=0)
    with pytest.raises(ValidationError):
        TimeframeRequirement(timeframe=Timeframe.M5, minimum_candles=-1)


def test_satisfied_when_enough_candles_available() -> None:
    m5 = make_series_from_closes([100, 101, 102, 103, 104], start=T0, timeframe=Timeframe.M5)
    as_of = m5[-1].freshness.data_timestamp

    check = check_timeframe_requirements(
        {Timeframe.M5: m5},
        instrument_id="INST1",
        as_of=as_of,
        requirements=[TimeframeRequirement(timeframe=Timeframe.M5, minimum_candles=3)],
    )

    assert check.all_satisfied
    assert check.results[0].candles_available == 5
    assert check.results[0].satisfied is True
    assert check.results[0].latest_candle_timestamp == as_of


def test_unsatisfied_when_not_enough_candles_available() -> None:
    m5 = make_series_from_closes([100, 101, 102], start=T0, timeframe=Timeframe.M5)
    as_of = m5[-1].freshness.data_timestamp

    check = check_timeframe_requirements(
        {Timeframe.M5: m5},
        instrument_id="INST1",
        as_of=as_of,
        requirements=[TimeframeRequirement(timeframe=Timeframe.M5, minimum_candles=10)],
    )

    assert not check.all_satisfied
    assert check.unsatisfied == check.results
    assert check.results[0].candles_available == 3
    assert check.results[0].satisfied is False


def test_missing_timeframe_reports_zero_available_not_an_error() -> None:
    as_of = T0 + timedelta(hours=1)

    check = check_timeframe_requirements(
        {},
        instrument_id="INST1",
        as_of=as_of,
        requirements=[TimeframeRequirement(timeframe=Timeframe.M5, minimum_candles=1)],
    )

    assert check.results[0].candles_available == 0
    assert check.results[0].satisfied is False
    assert check.results[0].latest_candle_timestamp is None


def test_multiple_requirements_evaluated_independently() -> None:
    m5 = make_series_from_closes([100, 101, 102, 103, 104], start=T0, timeframe=Timeframe.M5)
    h1 = make_series_from_closes([200, 201], start=T0, timeframe=Timeframe.H1, step=timedelta(hours=1))
    as_of = T0 + timedelta(hours=2)

    check = check_timeframe_requirements(
        {Timeframe.M5: m5, Timeframe.H1: h1},
        instrument_id="INST1",
        as_of=as_of,
        requirements=[
            TimeframeRequirement(timeframe=Timeframe.M5, minimum_candles=3),  # satisfied
            TimeframeRequirement(timeframe=Timeframe.H1, minimum_candles=5),  # not satisfied
        ],
    )

    assert not check.all_satisfied
    assert len(check.unsatisfied) == 1
    assert check.unsatisfied[0].timeframe == Timeframe.H1


def test_independently_re_bounds_to_as_of_even_if_caller_passed_unbounded_input() -> None:
    # Belt-and-suspenders: pass a series with candles AFTER as_of directly in
    # (simulating a caller who forgot to pre-bound) and confirm the check
    # still only counts candles at or before as_of.
    m5 = make_series_from_closes([100, 101, 102, 103, 104], start=T0, timeframe=Timeframe.M5)
    as_of = m5[1].freshness.data_timestamp  # only first 2 candles qualify

    check = check_timeframe_requirements(
        {Timeframe.M5: m5},  # full, unbounded 5-candle series passed directly
        instrument_id="INST1",
        as_of=as_of,
        requirements=[TimeframeRequirement(timeframe=Timeframe.M5, minimum_candles=3)],
    )

    assert check.results[0].candles_available == 2
    assert check.results[0].satisfied is False  # would be True if future candles leaked in


def test_propagates_invalid_candle_series_error() -> None:
    c1 = make_candle(T0, open_=100, high=101, low=99, close=100, volume=10)
    c2 = make_candle(T0, open_=100, high=101, low=99, close=101, volume=10)  # duplicate timestamp

    with pytest.raises(InvalidCandleSeriesError):
        check_timeframe_requirements(
            {Timeframe.M5: [c1, c2]},
            instrument_id="INST1",
            as_of=T0 + timedelta(hours=1),
            requirements=[TimeframeRequirement(timeframe=Timeframe.M5, minimum_candles=1)],
        )
