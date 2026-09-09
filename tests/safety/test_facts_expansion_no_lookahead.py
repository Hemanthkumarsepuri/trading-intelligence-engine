"""Anti-look-ahead safety tests for the two new fact modules
(`ema_alignment.py`, `vwap_position.py`), mirroring the existing pattern in
`test_technical_no_lookahead.py` and `test_snapshot_structure_no_lookahead.py`.
"""

from __future__ import annotations

import inspect
import time
from datetime import UTC, datetime, timedelta

from app.domain.market.models import Candle, Timeframe
from app.domain.technical import ema_alignment as ema_alignment_module
from app.domain.technical import vwap_position as vwap_position_module
from app.domain.technical.ema_alignment import EMAAlignmentResult, compute_ema_alignment
from app.domain.technical.series import IndicatorStatus
from app.domain.technical.vwap_position import VWAPPositionResult, compute_vwap_position
from tests.unit.technical.factories import make_series_from_closes

T0 = datetime(2026, 8, 27, 9, 15, tzinfo=UTC)
_THROUGH_T_CLOSES = [10, 9, 8, 7, 6, 5, 6, 7]
_FUTURE_CLOSES = [500, 1, 999, 2]


def _ema(candles: list[Candle], as_of: datetime) -> EMAAlignmentResult:
    return compute_ema_alignment(candles, instrument_id="INST1", timeframe=Timeframe.M5, periods=[2, 3, 4], as_of=as_of)


def _vwap(candles: list[Candle], as_of: datetime) -> VWAPPositionResult:
    return compute_vwap_position(candles, instrument_id="INST1", timeframe=Timeframe.M5, as_of=as_of)


def test_future_candles_do_not_alter_ema_alignment() -> None:
    through_t = make_series_from_closes(_THROUGH_T_CLOSES, start=T0)
    as_of = through_t[-1].freshness.data_timestamp
    with_future = through_t + make_series_from_closes(_FUTURE_CLOSES, start=as_of + timedelta(minutes=5))

    assert _ema(through_t, as_of) == _ema(with_future, as_of)


def test_future_candles_do_not_alter_vwap_position() -> None:
    through_t = make_series_from_closes(_THROUGH_T_CLOSES, start=T0)
    as_of = through_t[-1].freshness.data_timestamp
    with_future = through_t + make_series_from_closes(_FUTURE_CLOSES, start=as_of + timedelta(minutes=5))

    assert _vwap(through_t, as_of) == _vwap(with_future, as_of)


def test_ema_alignment_insufficient_at_as_of_ignores_later_sufficient_data() -> None:
    candles = make_series_from_closes(_THROUGH_T_CLOSES, start=T0)
    as_of = candles[1].freshness.data_timestamp  # only 2 candles qualify; periods=[2,3,4] needs 4

    result = _ema(candles, as_of)

    assert result.status == IndicatorStatus.INSUFFICIENT_HISTORY
    assert result.values is None
    assert result.candles_used == 2


def test_vwap_position_insufficient_at_as_of_ignores_later_sufficient_data() -> None:
    # as_of pinned before the only candle in the series -> zero candles qualify.
    candles = make_series_from_closes(_THROUGH_T_CLOSES, start=T0)
    as_of = T0 - timedelta(minutes=5)

    result = _vwap(candles, as_of)

    assert result.status == IndicatorStatus.INSUFFICIENT_HISTORY
    assert result.candles_used == 0


def test_results_identical_across_real_wall_clock_time() -> None:
    candles = make_series_from_closes(_THROUGH_T_CLOSES, start=T0)
    as_of = candles[-1].freshness.data_timestamp

    ema_first, vwap_first = _ema(candles, as_of), _vwap(candles, as_of)
    time.sleep(0.05)
    ema_second, vwap_second = _ema(candles, as_of), _vwap(candles, as_of)

    assert ema_first == ema_second
    assert vwap_first == vwap_second


def test_no_forbidden_clock_call_in_new_modules() -> None:
    forbidden = ("datetime.now(", ".utcnow(", "time.time(", "utc_now(")
    for module in (ema_alignment_module, vwap_position_module):
        source = inspect.getsource(module)
        for pattern in forbidden:
            assert pattern not in source, f"{module.__name__} contains a forbidden clock call: {pattern!r}"
