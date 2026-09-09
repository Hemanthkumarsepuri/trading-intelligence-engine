"""Live tick-to-candle aggregation — turns a stream of individual trade
ticks into completed `Candle`s plus one in-progress `PartialCandle`,
without ever letting the in-progress bucket be mistaken for a completed one.

This module is pure: no I/O, no clock reads (every tick's timestamp is
caller-supplied), fully deterministic given the same tick sequence. It does
not decide session/holiday windowing — it only buckets ticks into
fixed-width, top-of-hour-aligned periods, the same "caller controls
windowing" boundary `calculate_vwap()` already documents for candle series.

`PartialCandle` is a DIFFERENT type from `Candle` — not a subclass, not an
optional-fields variant — specifically so it can never be accidentally
passed into `bounded_series()`/a `StrategyDefinition`'s `candles` input and
mistaken for a completed bar. Nothing in this module, or anywhere else in
this codebase, feeds a `PartialCandle` into that path.

A partial candle is finalized into a `Candle` only when an actual tick
arrives in a later bucket — never merely because wall-clock time or `as_of`
has passed the bucket's nominal end. Promoting it on time alone would mean
fabricating a close price for a bucket that might still receive more ticks
(e.g. a brief feed stall) — the same "never fabricate on an assumption"
discipline `IndicatorStatus.INSUFFICIENT_HISTORY` already enforces
elsewhere. A gap in ticks (a genuinely quiet period) therefore produces a
gap in `completed_candles`, not an invented flat candle.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from pydantic import BaseModel

from app.domain.market.freshness import DataFreshness
from app.domain.market.models import Candle, Timeframe

_TIMEFRAME_MINUTES: dict[Timeframe, int] = {
    Timeframe.M1: 1,
    Timeframe.M5: 5,
    Timeframe.M15: 15,
    Timeframe.H1: 60,
}


def _bucket_start(timestamp: datetime, *, minutes: int) -> datetime:
    """Floors `timestamp` to the start of its `minutes`-wide bucket, computed
    on the absolute UTC instant. This produces the same bucket boundaries as
    flooring in IST (the exchange's own local time) would, because IST's UTC
    offset (5h30m = 330 minutes) is itself an exact multiple of every bucket
    width this module supports (1/5/15/60 minutes) — so no timezone
    conversion is needed for correctness, only tz-awareness.
    """
    if timestamp.tzinfo is None:
        raise ValueError("tick timestamp must be timezone-aware")
    bucket_seconds = minutes * 60
    floored_epoch = (int(timestamp.timestamp()) // bucket_seconds) * bucket_seconds
    return datetime.fromtimestamp(floored_epoch, tz=UTC)


class PartialCandle(BaseModel):
    """An in-progress, not-yet-closed candle bucket. See module docstring
    for why this is deliberately not a `Candle`.
    """

    instrument_id: str
    timeframe: Timeframe
    period_start: datetime
    period_end: datetime  # the instant this bucket will close and become a Candle
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal  # latest tick price seen so far in this bucket
    volume: int  # cumulative volume so far in this bucket
    last_tick_timestamp: datetime


class LiveCandleAggregator:
    """Stateful, single-instrument/single-timeframe tick aggregator.

    `on_tick()` is the only mutator. `completed_candles`/
    `current_partial_candle` are read-only snapshots — callers get a copy,
    never a reference into this instance's internal state, so external code
    cannot mutate history out from under it.
    """

    def __init__(self, *, instrument_id: str, timeframe: Timeframe, provider_name: str) -> None:
        if timeframe not in _TIMEFRAME_MINUTES:
            raise ValueError(f"LiveCandleAggregator does not support timeframe {timeframe.value}")
        self._instrument_id = instrument_id
        self._timeframe = timeframe
        self._minutes = _TIMEFRAME_MINUTES[timeframe]
        self._provider_name = provider_name
        self._completed: list[Candle] = []
        self._current: PartialCandle | None = None

    @property
    def completed_candles(self) -> list[Candle]:
        return list(self._completed)

    @property
    def current_partial_candle(self) -> PartialCandle | None:
        return self._current

    def on_tick(self, *, price: Decimal, volume_since_last_tick: int, timestamp: datetime) -> None:
        """Feeds one tick. `volume_since_last_tick` is the trade volume this
        tick itself represents (not a cumulative day-total) — the caller is
        responsible for that distinction, mirroring every other module's
        "caller supplies already-meaningful values" convention.

        Raises `ValueError` if `timestamp` is naive, or if it falls in a
        bucket strictly before the currently in-progress one (out-of-order
        ticks are rejected, never silently reordered).
        """
        bucket_start = _bucket_start(timestamp, minutes=self._minutes)
        bucket_end = bucket_start + timedelta(minutes=self._minutes)
        volume = max(volume_since_last_tick, 0)

        if self._current is None:
            self._current = PartialCandle(
                instrument_id=self._instrument_id,
                timeframe=self._timeframe,
                period_start=bucket_start,
                period_end=bucket_end,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=volume,
                last_tick_timestamp=timestamp,
            )
            return

        if bucket_start == self._current.period_start:
            self._current = self._current.model_copy(
                update={
                    "high": max(self._current.high, price),
                    "low": min(self._current.low, price),
                    "close": price,
                    "volume": self._current.volume + volume,
                    "last_tick_timestamp": timestamp,
                }
            )
            return

        if bucket_start < self._current.period_start:
            raise ValueError(
                f"tick timestamp {timestamp.isoformat()} falls in a bucket before the current in-progress "
                f"one ({self._current.period_start.isoformat()}) -- out-of-order ticks are rejected, "
                "not silently reordered"
            )

        # A later bucket: the current partial candle's period is genuinely
        # over (a real tick arrived after it, proving trading continued past
        # the boundary) -- finalize it, then start a new partial candle.
        self._completed.append(self._finalize(self._current))
        self._current = PartialCandle(
            instrument_id=self._instrument_id,
            timeframe=self._timeframe,
            period_start=bucket_start,
            period_end=bucket_end,
            open=price,
            high=price,
            low=price,
            close=price,
            volume=volume,
            last_tick_timestamp=timestamp,
        )

    def _finalize(self, partial: PartialCandle) -> Candle:
        return Candle(
            provider=self._provider_name,
            freshness=DataFreshness(data_timestamp=partial.period_start, received_timestamp=partial.last_tick_timestamp),
            instrument_id=partial.instrument_id,
            timeframe=partial.timeframe,
            open=partial.open,
            high=partial.high,
            low=partial.low,
            close=partial.close,
            volume=partial.volume,
        )
