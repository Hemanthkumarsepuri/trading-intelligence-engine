from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.domain.market.models import Timeframe
from app.domain.options.historical_structure import (
    HistoricalStructureStatus,
    assess_historical_structure,
)
from tests.unit.technical.factories import make_candle

IST = ZoneInfo("Asia/Kolkata")
INSTRUMENT = "NSE_EQ|TEST"


def _as_of_after_close(day: datetime) -> datetime:
    return day.replace(hour=16, minute=0, second=0, microsecond=0)


def _session_pair(day: datetime, *, high: float, low: float, close: float) -> list:
    open_ = (high + low) / 2
    return [
        make_candle(
            day.replace(hour=9, minute=15),
            open_=open_, high=high, low=low, close=close, volume=1000,
            instrument_id=INSTRUMENT, timeframe=Timeframe.M15,
        ),
        make_candle(
            day.replace(hour=15, minute=15),
            open_=close, high=high, low=low, close=close, volume=1100,
            instrument_id=INSTRUMENT, timeframe=Timeframe.M15,
        ),
    ]


def test_insufficient_history_when_fewer_than_five_completed_sessions() -> None:
    day = datetime(2026, 8, 31, tzinfo=IST)
    candles = _session_pair(day, high=101, low=99, close=100) + _session_pair(
        day + timedelta(days=1), high=101, low=99, close=100,
    )
    as_of = _as_of_after_close(day + timedelta(days=1))
    result = assess_historical_structure(
        candles, instrument_id=INSTRUMENT, timeframe=Timeframe.M15, as_of=as_of, spot=Decimal("100"),
    )
    assert result.status == HistoricalStructureStatus.INSUFFICIENT_HISTORY
    assert result.multi_day_compression is False
    assert "INSUFFICIENT_HISTORY" in result.detail


def test_no_lookahead_future_session_is_ignored() -> None:
    start = datetime(2026, 8, 24, tzinfo=IST)  # Monday
    candles = []
    day = start
    for i in range(6):
        while day.weekday() >= 5:
            day += timedelta(days=1)
        high, low = (102.0, 98.0) if i < 3 else (100.3, 99.8)
        candles.extend(_session_pair(day, high=high, low=low, close=100.0))
        day += timedelta(days=1)
    as_of = _as_of_after_close(candles[-1].freshness.data_timestamp)
    future = make_candle(
        as_of + timedelta(days=1),
        open_=120, high=130, low=110, close=125, volume=9999,
        instrument_id=INSTRUMENT, timeframe=Timeframe.M15,
    )
    result = assess_historical_structure(
        candles + [future], instrument_id=INSTRUMENT, timeframe=Timeframe.M15, as_of=as_of, spot=Decimal("100"),
    )
    assert result.status == HistoricalStructureStatus.OK
    assert result.lookback_high is not None
    assert result.lookback_high < Decimal("110")
    assert result.multi_day_compression is True


def test_wide_range_is_not_compression() -> None:
    start = datetime(2026, 8, 24, tzinfo=IST)
    candles = []
    day = start
    for _i in range(6):
        while day.weekday() >= 5:
            day += timedelta(days=1)
        candles.extend(_session_pair(day, high=115, low=90, close=100))
        day += timedelta(days=1)
    as_of = _as_of_after_close(candles[-1].freshness.data_timestamp)
    result = assess_historical_structure(
        candles, instrument_id=INSTRUMENT, timeframe=Timeframe.M15, as_of=as_of, spot=Decimal("100"),
    )
    assert result.status == HistoricalStructureStatus.OK
    assert result.multi_day_compression is False
