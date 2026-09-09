from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.market.models import Timeframe
from app.domain.technical.series import IndicatorStatus, InvalidCandleSeriesError
from app.domain.technical.snapshot import TechnicalSnapshotPeriods, assemble_technical_snapshot
from tests.unit.technical.factories import make_candle, make_series_from_closes

T0 = datetime(2026, 8, 27, 9, 15, tzinfo=UTC)

# Small, distinct periods so it's easy to reason about which sub-results
# should be OK vs INSUFFICIENT_HISTORY at a given series length.
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


def test_snapshot_all_indicators_ok_with_sufficient_history() -> None:
    # MACD needs the most: slow(3) + signal(2) - 1 = 4 candles minimum.
    # Give everything comfortable headroom beyond every indicator's minimum.
    candles = make_series_from_closes([100, 101, 99, 102, 103, 101, 104, 105], start=T0)
    as_of = candles[-1].freshness.data_timestamp

    snapshot = assemble_technical_snapshot(
        candles, instrument_id="INST1", timeframe=Timeframe.M5, as_of=as_of, periods=PERIODS
    )

    assert snapshot.instrument_id == "INST1"
    assert snapshot.timeframe == Timeframe.M5
    assert snapshot.as_of == as_of
    assert snapshot.periods == PERIODS
    assert snapshot.candles_available == len(candles)
    assert snapshot.latest_candle_timestamp == as_of

    assert snapshot.ema.status == IndicatorStatus.OK
    assert snapshot.rsi.status == IndicatorStatus.OK
    assert snapshot.macd.status == IndicatorStatus.OK
    assert snapshot.atr.status == IndicatorStatus.OK
    assert snapshot.vwap.status == IndicatorStatus.OK
    assert snapshot.swing_points.status == IndicatorStatus.OK
    assert snapshot.relative_volume.status == IndicatorStatus.OK


def test_snapshot_reports_each_indicators_own_insufficient_history_independently() -> None:
    # 2 candles: enough for ATR(2)/RSI(2) to still be short (need period+1=3),
    # but MACD (needs 4) and EMA(3) are further short too. Every sub-result
    # must independently say INSUFFICIENT_HISTORY — none may be silently
    # dropped, defaulted, or converted to a fabricated value.
    candles = make_series_from_closes([100, 101], start=T0)
    as_of = candles[-1].freshness.data_timestamp

    snapshot = assemble_technical_snapshot(
        candles, instrument_id="INST1", timeframe=Timeframe.M5, as_of=as_of, periods=PERIODS
    )

    assert snapshot.ema.status == IndicatorStatus.INSUFFICIENT_HISTORY
    assert snapshot.ema.value is None
    assert snapshot.rsi.status == IndicatorStatus.INSUFFICIENT_HISTORY
    assert snapshot.rsi.value is None
    assert snapshot.macd.status == IndicatorStatus.INSUFFICIENT_HISTORY
    assert snapshot.macd.macd_line is None
    assert snapshot.atr.status == IndicatorStatus.INSUFFICIENT_HISTORY
    assert snapshot.atr.value is None
    assert snapshot.swing_points.status == IndicatorStatus.INSUFFICIENT_HISTORY
    assert snapshot.swing_points.swing_highs == []
    assert snapshot.relative_volume.status == IndicatorStatus.INSUFFICIENT_HISTORY
    assert snapshot.relative_volume.ratio is None

    # VWAP only needs >=1 candle with nonzero volume — it's OK here even
    # though everything else is short. Different indicators, different
    # minimums; the snapshot must not force them into lockstep.
    assert snapshot.vwap.status == IndicatorStatus.OK

    # None of the above ever became a fabricated 0 or a "neutral" label —
    # confirmed by asserting value/None directly above rather than truthiness.
    assert snapshot.candles_available == 2


def test_snapshot_no_candles_before_as_of_is_all_insufficient() -> None:
    candles = make_series_from_closes([100, 101, 102], start=T0)
    as_of = T0 - timedelta(minutes=5)  # before any candle

    snapshot = assemble_technical_snapshot(
        candles, instrument_id="INST1", timeframe=Timeframe.M5, as_of=as_of, periods=PERIODS
    )

    assert snapshot.candles_available == 0
    assert snapshot.latest_candle_timestamp is None
    assert snapshot.ema.status == IndicatorStatus.INSUFFICIENT_HISTORY
    assert snapshot.vwap.status == IndicatorStatus.INSUFFICIENT_HISTORY


def test_snapshot_as_of_matches_requested_value_not_latest_candle() -> None:
    candles = make_series_from_closes([100, 101, 99, 102, 103, 101, 104, 105], start=T0)
    as_of = candles[4].freshness.data_timestamp  # earlier than the series' actual last candle

    snapshot = assemble_technical_snapshot(
        candles, instrument_id="INST1", timeframe=Timeframe.M5, as_of=as_of, periods=PERIODS
    )

    assert snapshot.as_of == as_of
    assert snapshot.latest_candle_timestamp == as_of
    assert snapshot.candles_available == 5


def test_snapshot_is_deterministic() -> None:
    candles = make_series_from_closes([100, 101, 99, 102, 103, 101, 104, 105], start=T0)
    as_of = candles[-1].freshness.data_timestamp

    first = assemble_technical_snapshot(candles, instrument_id="INST1", timeframe=Timeframe.M5, as_of=as_of, periods=PERIODS)
    second = assemble_technical_snapshot(candles, instrument_id="INST1", timeframe=Timeframe.M5, as_of=as_of, periods=PERIODS)

    assert first == second


def test_snapshot_future_candles_do_not_change_the_result() -> None:
    through_t = make_series_from_closes([100, 101, 99, 102, 103, 101, 104, 105], start=T0)
    as_of = through_t[-1].freshness.data_timestamp
    future = make_series_from_closes([9999, 1, 9999, 1], start=as_of + timedelta(minutes=5))

    before = assemble_technical_snapshot(through_t, instrument_id="INST1", timeframe=Timeframe.M5, as_of=as_of, periods=PERIODS)
    after = assemble_technical_snapshot(through_t + future, instrument_id="INST1", timeframe=Timeframe.M5, as_of=as_of, periods=PERIODS)

    assert before == after


def test_snapshot_propagates_invalid_candle_series_error_rather_than_swallowing_it() -> None:
    c1 = make_candle(T0, open_=100, high=101, low=99, close=100, volume=10)
    c2 = make_candle(T0 - timedelta(minutes=5), open_=100, high=101, low=99, close=100, volume=10)  # out of order

    with pytest.raises(InvalidCandleSeriesError):
        assemble_technical_snapshot(
            [c1, c2], instrument_id="INST1", timeframe=Timeframe.M5, as_of=T0 + timedelta(hours=1), periods=PERIODS
        )


def test_snapshot_does_not_contain_a_directional_label_or_score() -> None:
    # Structural guarantee, not just documentation: TechnicalSnapshot must
    # never grow a bullish/bearish/score field by accident.
    candles = make_series_from_closes([100, 101, 99, 102, 103, 101, 104, 105], start=T0)
    snapshot = assemble_technical_snapshot(
        candles, instrument_id="INST1", timeframe=Timeframe.M5, as_of=candles[-1].freshness.data_timestamp, periods=PERIODS
    )
    field_names = set(type(snapshot).model_fields.keys())
    forbidden = {"direction", "bullish", "bearish", "score", "confidence", "signal", "recommendation"}
    assert field_names.isdisjoint(forbidden)
