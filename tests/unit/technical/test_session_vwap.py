from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.domain.market.models import Timeframe
from app.domain.technical.series import IndicatorStatus
from app.domain.technical.structure import (
    calculate_session_vwap,
    calculate_vwap,
    nse_cash_session_candles,
)
from tests.unit.technical.factories import make_candle

IST = ZoneInfo("Asia/Kolkata")


def test_session_vwap_uses_only_current_session_bars() -> None:
    prev = make_candle(
        datetime(2026, 9, 8, 12, 0, tzinfo=IST),
        open_=100, high=110, low=90, close=100, volume=10_000, timeframe=Timeframe.M15,
    )
    first = make_candle(
        datetime(2026, 9, 9, 9, 15, tzinfo=IST),
        open_=10, high=10, low=8, close=9, volume=100, timeframe=Timeframe.M15,
    )
    second = make_candle(
        datetime(2026, 9, 9, 9, 30, tzinfo=IST),
        open_=11, high=12, low=10, close=11, volume=200, timeframe=Timeframe.M15,
    )
    as_of = datetime(2026, 9, 9, 10, 0, tzinfo=IST)
    session = nse_cash_session_candles([prev, first, second], as_of=as_of)
    assert prev not in session
    assert session == [first, second]
    rolling = calculate_vwap([prev, first, second], instrument_id="INST1", timeframe=Timeframe.M15, as_of=as_of)
    session_vwap = calculate_session_vwap([prev, first, second], instrument_id="INST1", timeframe=Timeframe.M15, as_of=as_of)
    assert rolling.status == IndicatorStatus.OK
    assert session_vwap.status == IndicatorStatus.OK
    assert session_vwap.value is not None
    assert rolling.value != session_vwap.value
    assert "session" in session_vwap.detail.lower()
    assert "no session reset" in rolling.detail.lower()


def test_session_vwap_first_bar_equals_typical_price() -> None:
    bar = make_candle(
        datetime(2026, 9, 9, 9, 15, tzinfo=IST),
        open_=10, high=12, low=8, close=10, volume=100, timeframe=Timeframe.M15,
    )
    as_of = datetime(2026, 9, 9, 9, 20, tzinfo=IST)
    result = calculate_session_vwap([bar], instrument_id="INST1", timeframe=Timeframe.M15, as_of=as_of)
    assert result.status == IndicatorStatus.OK
    assert result.value == (12 + 8 + 10) / 3


def test_session_vwap_does_not_look_ahead() -> None:
    now = make_candle(
        datetime(2026, 9, 9, 10, 0, tzinfo=IST),
        open_=10, high=10, low=10, close=10, volume=100, timeframe=Timeframe.M15,
    )
    future = make_candle(
        datetime(2026, 9, 9, 10, 15, tzinfo=IST),
        open_=50, high=50, low=50, close=50, volume=100_000, timeframe=Timeframe.M15,
    )
    as_of = datetime(2026, 9, 9, 10, 5, tzinfo=IST)
    result = calculate_session_vwap([now, future], instrument_id="INST1", timeframe=Timeframe.M15, as_of=as_of)
    assert result.status == IndicatorStatus.OK
    assert result.value == 10
    assert result.candles_used == 1


def test_pre_open_bar_is_excluded_from_session_vwap() -> None:
    pre = make_candle(
        datetime(2026, 9, 9, 9, 0, tzinfo=IST),
        open_=1, high=1, low=1, close=1, volume=999_999, timeframe=Timeframe.M15,
    )
    open_bar = make_candle(
        datetime(2026, 9, 9, 9, 15, tzinfo=IST),
        open_=10, high=10, low=10, close=10, volume=100, timeframe=Timeframe.M15,
    )
    as_of = datetime(2026, 9, 9, 10, 0, tzinfo=IST)
    result = calculate_session_vwap([pre, open_bar], instrument_id="INST1", timeframe=Timeframe.M15, as_of=as_of)
    assert result.value == 10
