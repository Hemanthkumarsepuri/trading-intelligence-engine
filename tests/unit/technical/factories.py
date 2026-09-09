"""Shared fixtures for domain/technical tests."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal

from app.domain.market.freshness import DataFreshness
from app.domain.market.models import Candle, Timeframe

INSTRUMENT_ID = "INST1"


def make_candle(
    timestamp: datetime,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: int,
    instrument_id: str = INSTRUMENT_ID,
    timeframe: Timeframe = Timeframe.M5,
    provider: str = "mock",
) -> Candle:
    return Candle(
        provider=provider,
        freshness=DataFreshness(data_timestamp=timestamp, received_timestamp=timestamp),
        instrument_id=instrument_id,
        timeframe=timeframe,
        open=Decimal(str(open_)),
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(close)),
        volume=volume,
    )


def make_series_from_closes(
    closes: Sequence[float],
    *,
    start: datetime,
    step: timedelta = timedelta(minutes=5),
    volume: int = 1000,
    instrument_id: str = INSTRUMENT_ID,
    timeframe: Timeframe = Timeframe.M5,
) -> list[Candle]:
    """Builds a simple, OHLC-consistent candle series from a list of closes.

    Each candle's open equals the previous candle's close (first candle's
    open equals its own close); high/low pad the open/close by a tiny amount
    so `Candle`'s OHLC-consistency validator is always satisfied regardless
    of direction. Good enough for indicator tests, which only care about
    `close`/`high`/`low`/`volume` values, not "realistic" price action.
    """
    candles: list[Candle] = []
    previous_close = closes[0]
    for i, close in enumerate(closes):
        open_ = previous_close
        high = max(open_, close) + 0.01
        low = min(open_, close) - 0.01
        candles.append(
            make_candle(
                start + i * step,
                open_=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
                instrument_id=instrument_id,
                timeframe=timeframe,
            )
        )
        previous_close = close
    return candles
