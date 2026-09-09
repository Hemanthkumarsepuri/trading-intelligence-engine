"""Anti-look-ahead safety tests for `domain/technical/*`.

These are dedicated safety tests, not general unit tests, because a
regression here — a future candle silently influencing a "current" reading —
is exactly the failure mode ARCHITECTURE.md Addendum A5 exists to prevent
system-wide, extended here to the Technical Engine specifically as required
by this milestone's brief.

Test A — a future candle appended to the series must not change the result
          for a fixed `as_of` that predates it.
Test B — when `as_of` has insufficient history, the function must not fall
          back to a later candle to "help" — it must report
          INSUFFICIENT_HISTORY honestly.
Test E — no indicator's result may depend on the wall clock; calling the
          same function with the same arguments at two different real
          instants must produce identical results, and no forbidden clock
          call appears anywhere in the package's source.
"""

from __future__ import annotations

import inspect
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from app.domain.market.models import Candle, Timeframe
from app.domain.technical import momentum, structure, trend, volatility, volume
from app.domain.technical.series import IndicatorStatus
from tests.unit.technical.factories import make_series_from_closes

T0 = datetime(2026, 8, 27, 9, 15, tzinfo=UTC)

# A 12-close series is enough warm-up for every indicator under test below
# with the small periods chosen here, plus room to append "future" candles.
_THROUGH_T_CLOSES = [100, 101, 99, 102, 103, 101, 104, 105, 103, 106, 107, 105]
_FUTURE_CLOSES = [500, 1, 999, 2]  # deliberately extreme so a leak would be obvious


class _HasStatus(Protocol):
    """Every technical result model has this field — enough to write
    provider-agnostic assertions here without needing each concrete result
    type.
    """

    status: IndicatorStatus


IndicatorCall = Callable[[list[Candle], datetime], _HasStatus]


def _call_ema(candles: list[Candle], as_of: datetime) -> trend.EMAResult:
    return trend.calculate_ema(candles, instrument_id="INST1", timeframe=Timeframe.M5, period=5, as_of=as_of)


def _call_rsi(candles: list[Candle], as_of: datetime) -> momentum.RSIResult:
    return momentum.calculate_rsi(candles, instrument_id="INST1", timeframe=Timeframe.M5, period=5, as_of=as_of)


def _call_macd(candles: list[Candle], as_of: datetime) -> momentum.MACDResult:
    return momentum.calculate_macd(
        candles, instrument_id="INST1", timeframe=Timeframe.M5, fast_period=3, slow_period=6, signal_period=3, as_of=as_of
    )


def _call_atr(candles: list[Candle], as_of: datetime) -> volatility.ATRResult:
    return volatility.calculate_atr(candles, instrument_id="INST1", timeframe=Timeframe.M5, period=5, as_of=as_of)


def _call_vwap(candles: list[Candle], as_of: datetime) -> structure.VWAPResult:
    return structure.calculate_vwap(candles, instrument_id="INST1", timeframe=Timeframe.M5, as_of=as_of)


def _call_swings(candles: list[Candle], as_of: datetime) -> structure.SwingPointsResult:
    return structure.find_swing_points(
        candles, instrument_id="INST1", timeframe=Timeframe.M5, left_bars=2, right_bars=2, as_of=as_of
    )


def _call_relative_volume(candles: list[Candle], as_of: datetime) -> volume.RelativeVolumeResult:
    return volume.calculate_relative_volume(candles, instrument_id="INST1", timeframe=Timeframe.M5, period=5, as_of=as_of)


_ALL_CALLS: dict[str, IndicatorCall] = {
    "ema": _call_ema,
    "rsi": _call_rsi,
    "macd": _call_macd,
    "atr": _call_atr,
    "vwap": _call_vwap,
    "swings": _call_swings,
    "relative_volume": _call_relative_volume,
}


# -- Test A: future candles never change a fixed-as_of result -------------


def test_future_candles_do_not_change_any_indicator_result() -> None:
    through_t = make_series_from_closes(_THROUGH_T_CLOSES, start=T0)
    as_of = through_t[-1].freshness.data_timestamp

    with_future = through_t + make_series_from_closes(
        _FUTURE_CLOSES, start=as_of + timedelta(minutes=5)
    )

    for name, call in _ALL_CALLS.items():
        result_before = call(through_t, as_of)
        result_after = call(with_future, as_of)
        assert result_before == result_after, f"{name}: future candles changed the result for a fixed as_of"
        assert result_before.status == IndicatorStatus.OK, f"{name}: expected OK with {len(through_t)} candles of warm-up"


def test_many_future_candles_appended_still_no_effect() -> None:
    # Stress variant: a large, wildly-varying future block must still have
    # zero effect — not just a small nudge that happens to round away.
    through_t = make_series_from_closes(_THROUGH_T_CLOSES, start=T0)
    as_of = through_t[-1].freshness.data_timestamp
    huge_future = make_series_from_closes(
        [1, 100000, 1, 100000, 1, 100000, 1, 100000], start=as_of + timedelta(minutes=5)
    )
    with_future = through_t + huge_future

    for name, call in _ALL_CALLS.items():
        assert call(through_t, as_of) == call(with_future, as_of), f"{name}: large future block changed the result"


# -- Test B: insufficient-at-as_of must not fall back to later data -------


def test_ema_insufficient_at_as_of_does_not_borrow_a_later_candle() -> None:
    # 12 closes total, but as_of pinned to the 3rd candle -> only 3 candles
    # qualify, which is short of the period=5 requirement. A leak would show
    # up as a spuriously "OK" result using candles beyond as_of.
    candles = make_series_from_closes(_THROUGH_T_CLOSES, start=T0)
    as_of = candles[2].freshness.data_timestamp

    result = _call_ema(candles, as_of)

    assert result.status == IndicatorStatus.INSUFFICIENT_HISTORY
    assert result.value is None
    assert result.candles_used == 3


def test_swing_points_insufficient_at_as_of_does_not_borrow_later_candles() -> None:
    candles = make_series_from_closes(_THROUGH_T_CLOSES, start=T0)
    as_of = candles[1].freshness.data_timestamp  # only 2 candles qualify; need 2+2+1=5

    result = _call_swings(candles, as_of)

    assert result.status == IndicatorStatus.INSUFFICIENT_HISTORY
    assert result.swing_highs == []
    assert result.swing_lows == []


def test_macd_insufficient_at_as_of_does_not_borrow_later_candles() -> None:
    candles = make_series_from_closes(_THROUGH_T_CLOSES, start=T0)
    as_of = candles[3].freshness.data_timestamp  # 4 candles; slow=6+signal=3-1=8 required

    result = _call_macd(candles, as_of)

    assert result.status == IndicatorStatus.INSUFFICIENT_HISTORY
    assert result.macd_line is None
    assert result.signal_line is None
    assert result.histogram is None


# -- Test E: no wall-clock dependency --------------------------------------


def test_results_are_identical_across_real_wall_clock_time() -> None:
    candles = make_series_from_closes(_THROUGH_T_CLOSES, start=T0)
    as_of = candles[-1].freshness.data_timestamp

    first_pass = {name: call(candles, as_of) for name, call in _ALL_CALLS.items()}
    time.sleep(0.05)  # let real wall-clock time actually move
    second_pass = {name: call(candles, as_of) for name, call in _ALL_CALLS.items()}

    for name in _ALL_CALLS:
        assert first_pass[name] == second_pass[name], f"{name}: result changed between calls despite identical inputs"


def test_no_forbidden_clock_call_anywhere_in_domain_technical() -> None:
    forbidden = ("datetime.now(", ".utcnow(", "time.time(", "utc_now(")
    modules = [trend, momentum, volatility, structure, volume]
    for module in modules:
        source = inspect.getsource(module)
        for pattern in forbidden:
            assert pattern not in source, f"{module.__name__} contains a forbidden clock call: {pattern!r}"
