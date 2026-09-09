"""Anti-look-ahead safety tests for the Strategy Input Foundation
(`assemble_candle_inputs`, `check_timeframe_requirements`), mirroring the
established pattern in `test_technical_no_lookahead.py` and
`test_snapshot_structure_no_lookahead.py`.
"""

from __future__ import annotations

import inspect
import time
from datetime import UTC, datetime, timedelta

from app.domain.market import candle_inputs as candle_inputs_module
from app.domain.market import timeframe_requirement as timeframe_requirement_module
from app.domain.market.candle_inputs import assemble_candle_inputs
from app.domain.market.models import Timeframe
from app.domain.market.timeframe_requirement import (
    TimeframeRequirement,
    check_timeframe_requirements,
)
from tests.unit.technical.factories import make_series_from_closes

T0 = datetime(2026, 8, 27, 9, 15, tzinfo=UTC)
_THROUGH_T_CLOSES = [100, 101, 99, 102, 103, 101, 104, 105]
_FUTURE_CLOSES = [500, 1, 999, 2]
_REQUIREMENTS = [TimeframeRequirement(timeframe=Timeframe.M5, minimum_candles=5)]


def test_future_candles_do_not_alter_assembled_candle_inputs() -> None:
    through_t = make_series_from_closes(_THROUGH_T_CLOSES, start=T0)
    as_of = through_t[-1].freshness.data_timestamp
    with_future = through_t + make_series_from_closes(_FUTURE_CLOSES, start=as_of + timedelta(minutes=5))

    before = assemble_candle_inputs({Timeframe.M5: through_t}, instrument_id="INST1", as_of=as_of)
    after = assemble_candle_inputs({Timeframe.M5: with_future}, instrument_id="INST1", as_of=as_of)

    assert before == after


def test_future_candles_do_not_alter_timeframe_requirement_check() -> None:
    through_t = make_series_from_closes(_THROUGH_T_CLOSES, start=T0)
    as_of = through_t[-1].freshness.data_timestamp
    with_future = through_t + make_series_from_closes(_FUTURE_CLOSES, start=as_of + timedelta(minutes=5))

    before = check_timeframe_requirements(
        {Timeframe.M5: through_t}, instrument_id="INST1", as_of=as_of, requirements=_REQUIREMENTS
    )
    after = check_timeframe_requirements(
        {Timeframe.M5: with_future}, instrument_id="INST1", as_of=as_of, requirements=_REQUIREMENTS
    )

    assert before == after


def test_insufficient_at_as_of_stays_insufficient_despite_later_sufficient_data() -> None:
    candles = make_series_from_closes(_THROUGH_T_CLOSES, start=T0)
    as_of = candles[1].freshness.data_timestamp  # only 2 of 8 candles qualify

    check = check_timeframe_requirements(
        {Timeframe.M5: candles}, instrument_id="INST1", as_of=as_of, requirements=_REQUIREMENTS
    )

    assert check.results[0].candles_available == 2
    assert check.results[0].satisfied is False


def test_results_identical_across_real_wall_clock_time() -> None:
    candles = make_series_from_closes(_THROUGH_T_CLOSES, start=T0)
    as_of = candles[-1].freshness.data_timestamp

    inputs_first = assemble_candle_inputs({Timeframe.M5: candles}, instrument_id="INST1", as_of=as_of)
    check_first = check_timeframe_requirements(
        {Timeframe.M5: candles}, instrument_id="INST1", as_of=as_of, requirements=_REQUIREMENTS
    )
    time.sleep(0.05)
    inputs_second = assemble_candle_inputs({Timeframe.M5: candles}, instrument_id="INST1", as_of=as_of)
    check_second = check_timeframe_requirements(
        {Timeframe.M5: candles}, instrument_id="INST1", as_of=as_of, requirements=_REQUIREMENTS
    )

    assert inputs_first == inputs_second
    assert check_first == check_second


def test_no_forbidden_clock_call_in_strategy_input_modules() -> None:
    forbidden = ("datetime.now(", ".utcnow(", "time.time(", "utc_now(")
    for module in (candle_inputs_module, timeframe_requirement_module):
        source = inspect.getsource(module)
        for pattern in forbidden:
            assert pattern not in source, f"{module.__name__} contains a forbidden clock call: {pattern!r}"
