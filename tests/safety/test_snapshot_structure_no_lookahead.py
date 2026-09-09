"""Anti-look-ahead safety tests for `TechnicalSnapshot` and `StructureFacts`.

Mirrors `test_technical_no_lookahead.py`'s pattern, extended to the two new
aggregation modules built on top of the primitives that file already covers.
Both new modules delegate all filtering to the existing `bounded_series()`
mechanism (via the primitives they call) rather than introducing a second,
competing implementation — these tests confirm that delegation actually
holds end to end, not just that it was intended to.
"""

from __future__ import annotations

import inspect
import time
from datetime import UTC, datetime, timedelta

from app.domain.market.models import Candle, Timeframe
from app.domain.technical import snapshot as snapshot_module
from app.domain.technical import structure_facts as structure_facts_module
from app.domain.technical.series import IndicatorStatus
from app.domain.technical.snapshot import (
    TechnicalSnapshot,
    TechnicalSnapshotPeriods,
    assemble_technical_snapshot,
)
from app.domain.technical.structure_facts import StructureFactsResult, compute_structure_facts
from tests.unit.technical.factories import make_series_from_closes

T0 = datetime(2026, 8, 27, 9, 15, tzinfo=UTC)

PERIODS = TechnicalSnapshotPeriods(
    ema_period=3,
    rsi_period=2,
    macd_fast_period=2,
    macd_slow_period=3,
    macd_signal_period=2,
    atr_period=2,
    relative_volume_period=2,
    swing_left_bars=1,
    swing_right_bars=1,
)

_THROUGH_T_CLOSES = [100, 101, 99, 102, 103, 101, 104, 105, 103, 106, 107, 105]
_FUTURE_CLOSES = [500, 1, 999, 2]


def _snapshot(candles: list[Candle], as_of: datetime) -> TechnicalSnapshot:
    return assemble_technical_snapshot(candles, instrument_id="INST1", timeframe=Timeframe.M5, as_of=as_of, periods=PERIODS)


def _facts(candles: list[Candle], as_of: datetime) -> StructureFactsResult:
    return compute_structure_facts(candles, instrument_id="INST1", timeframe=Timeframe.M5, left_bars=2, right_bars=2, as_of=as_of)


# -- 1 & 2: future candles do not alter the result -------------------------


def test_future_candles_do_not_alter_technical_snapshot() -> None:
    through_t = make_series_from_closes(_THROUGH_T_CLOSES, start=T0)
    as_of = through_t[-1].freshness.data_timestamp
    with_future = through_t + make_series_from_closes(_FUTURE_CLOSES, start=as_of + timedelta(minutes=5))

    before = _snapshot(through_t, as_of)
    after = _snapshot(with_future, as_of)

    assert before == after


def test_future_candles_do_not_alter_structure_facts() -> None:
    through_t = make_series_from_closes(_THROUGH_T_CLOSES, start=T0)
    as_of = through_t[-1].freshness.data_timestamp
    with_future = through_t + make_series_from_closes(_FUTURE_CLOSES, start=as_of + timedelta(minutes=5))

    before = _facts(through_t, as_of)
    after = _facts(with_future, as_of)

    assert before == after


# -- 3: insufficient history at as_of stays insufficient regardless of later data --


def test_snapshot_insufficient_at_as_of_ignores_later_sufficient_data() -> None:
    candles = make_series_from_closes(_THROUGH_T_CLOSES, start=T0)
    as_of = candles[1].freshness.data_timestamp  # only 2 candles qualify; MACD alone needs 4

    result = _snapshot(candles, as_of)

    assert result.macd.status == IndicatorStatus.INSUFFICIENT_HISTORY
    assert result.macd.macd_line is None
    assert result.ema.status == IndicatorStatus.INSUFFICIENT_HISTORY
    assert result.candles_available == 2


def test_structure_facts_insufficient_at_as_of_ignores_later_sufficient_data() -> None:
    candles = make_series_from_closes(_THROUGH_T_CLOSES, start=T0)
    as_of = candles[2].freshness.data_timestamp  # 3 candles; left=2,right=2 needs 5

    result = _facts(candles, as_of)

    assert result.status == IndicatorStatus.INSUFFICIENT_HISTORY
    assert result.high_comparisons == []
    assert result.low_comparisons == []


# -- 4: identical results across real wall-clock time ----------------------


def test_snapshot_and_structure_facts_identical_across_wall_clock_time() -> None:
    candles = make_series_from_closes(_THROUGH_T_CLOSES, start=T0)
    as_of = candles[-1].freshness.data_timestamp

    snap_first, facts_first = _snapshot(candles, as_of), _facts(candles, as_of)
    time.sleep(0.05)
    snap_second, facts_second = _snapshot(candles, as_of), _facts(candles, as_of)

    assert snap_first == snap_second
    assert facts_first == facts_second


# -- 5: no wall-clock dependency in source --------------------------------


def test_no_forbidden_clock_call_in_snapshot_or_structure_facts() -> None:
    forbidden = ("datetime.now(", ".utcnow(", "time.time(", "utc_now(")
    for module in (snapshot_module, structure_facts_module):
        source = inspect.getsource(module)
        for pattern in forbidden:
            assert pattern not in source, f"{module.__name__} contains a forbidden clock call: {pattern!r}"


# -- extra: deep future-value stress, mirroring the primitives-level test --


def test_snapshot_ignores_a_large_extreme_future_block() -> None:
    through_t = make_series_from_closes(_THROUGH_T_CLOSES, start=T0)
    as_of = through_t[-1].freshness.data_timestamp
    huge_future = make_series_from_closes([1, 100000, 1, 100000, 1, 100000], start=as_of + timedelta(minutes=5))

    assert _snapshot(through_t, as_of) == _snapshot(through_t + huge_future, as_of)
    assert _facts(through_t, as_of) == _facts(through_t + huge_future, as_of)
