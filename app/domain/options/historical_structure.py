"""No-lookahead multi-session structure from already-fetched candles.

Does not fetch. Does not invent daily bars from a vendor. Aggregates the
bounded M15 (or other) series into IST session bars, then reports ATR,
lookback high/low, and whether range has compressed. Missing sessions stay
`INSUFFICIENT_HISTORY` -- never labeled stable or compressing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum

from app.domain.market.models import Candle, Timeframe
from app.domain.technical.series import bounded_series
from app.utils.time import to_ist

MIN_COMPLETED_SESSIONS = 5
COMPRESSION_LOOKBACK_RANGE_PCT = Decimal("8")
NEAR_EXTREME_PCT = Decimal("1.5")
RANGE_CONTRACTION_RATIO = Decimal("0.90")
NSE_CLOSE = time(15, 30)


class HistoricalStructureStatus(str, Enum):
    OK = "OK"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"


@dataclass(frozen=True)
class SessionBar:
    session_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    completed: bool


@dataclass(frozen=True)
class HistoricalStructure:
    status: HistoricalStructureStatus
    session_count: int
    completed_session_count: int
    lookback_high: Decimal | None
    lookback_low: Decimal | None
    last_completed_close: Decimal | None
    atr: Decimal | None
    atr_pct: Decimal | None
    multi_day_range_pct: Decimal | None
    multi_day_compression: bool
    distance_to_lookback_high_pct: Decimal | None
    distance_to_lookback_low_pct: Decimal | None
    near_lookback_high: bool
    near_lookback_low: bool
    detail: str

    @property
    def near_lookback_extreme(self) -> bool:
        return self.near_lookback_high or self.near_lookback_low


def _true_range(bar: SessionBar, previous_close: Decimal | None) -> Decimal:
    span = bar.high - bar.low
    if previous_close is None:
        return span
    return max(span, abs(bar.high - previous_close), abs(bar.low - previous_close))


def _session_completed(session_day: date, as_of: datetime) -> bool:
    ist = to_ist(as_of)
    if session_day < ist.date():
        return True
    if session_day > ist.date():
        return False
    return ist.hour > NSE_CLOSE.hour or (ist.hour == NSE_CLOSE.hour and ist.minute >= NSE_CLOSE.minute)


def aggregate_session_bars(
    candles: list[Candle],
    *,
    as_of: datetime,
) -> list[SessionBar]:
    """One bar per IST calendar date present in the bounded series."""
    by_day: dict[date, list[Candle]] = {}
    for candle in candles:
        day = to_ist(candle.freshness.data_timestamp).date()
        by_day.setdefault(day, []).append(candle)
    bars: list[SessionBar] = []
    for day in sorted(by_day):
        series = sorted(by_day[day], key=lambda c: c.freshness.data_timestamp)
        first, last = series[0], series[-1]
        high = max(c.high for c in series)
        low = min(c.low for c in series)
        volume = sum(c.volume for c in series)
        bars.append(
            SessionBar(
                session_date=day,
                open=first.open,
                high=high,
                low=low,
                close=last.close,
                volume=volume,
                completed=_session_completed(day, as_of),
            )
        )
    return bars


def assess_historical_structure(
    candles: list[Candle],
    *,
    instrument_id: str,
    timeframe: Timeframe,
    as_of: datetime,
    spot: Decimal | None,
) -> HistoricalStructure:
    """Only candles with `data_timestamp <= as_of` participate."""
    empty = HistoricalStructure(
        status=HistoricalStructureStatus.INSUFFICIENT_HISTORY,
        session_count=0,
        completed_session_count=0,
        lookback_high=None,
        lookback_low=None,
        last_completed_close=None,
        atr=None,
        atr_pct=None,
        multi_day_range_pct=None,
        multi_day_compression=False,
        distance_to_lookback_high_pct=None,
        distance_to_lookback_low_pct=None,
        near_lookback_high=False,
        near_lookback_low=False,
        detail="INSUFFICIENT_HISTORY -- not enough completed sessions in the bounded candle series",
    )
    if not candles:
        return empty
    bounded = bounded_series(candles, instrument_id=instrument_id, timeframe=timeframe, as_of=as_of)
    bars = aggregate_session_bars(bounded, as_of=as_of)
    completed = [b for b in bars if b.completed]
    if len(completed) < MIN_COMPLETED_SESSIONS:
        return HistoricalStructure(
            status=HistoricalStructureStatus.INSUFFICIENT_HISTORY,
            session_count=len(bars),
            completed_session_count=len(completed),
            lookback_high=None,
            lookback_low=None,
            last_completed_close=completed[-1].close if completed else None,
            atr=None,
            atr_pct=None,
            multi_day_range_pct=None,
            multi_day_compression=False,
            distance_to_lookback_high_pct=None,
            distance_to_lookback_low_pct=None,
            near_lookback_high=False,
            near_lookback_low=False,
            detail=(
                f"INSUFFICIENT_HISTORY -- {len(completed)} completed IST session(s); "
                f"need {MIN_COMPLETED_SESSIONS} to validate multi-day compression"
            ),
        )

    lookback_high = max(b.high for b in completed)
    lookback_low = min(b.low for b in completed)
    last_close = completed[-1].close
    true_ranges: list[Decimal] = []
    prev_close: Decimal | None = None
    for bar in completed:
        true_ranges.append(_true_range(bar, prev_close))
        prev_close = bar.close
    atr = sum(true_ranges, Decimal(0)) / Decimal(len(true_ranges))
    atr_pct = (atr / last_close * Decimal(100)) if last_close > 0 else None
    span = lookback_high - lookback_low
    multi_day_range_pct = (span / last_close * Decimal(100)) if last_close > 0 else None

    first_half = true_ranges[:3]
    last_half = true_ranges[-3:]
    first_avg = sum(first_half, Decimal(0)) / Decimal(len(first_half))
    last_avg = sum(last_half, Decimal(0)) / Decimal(len(last_half))
    contracting = first_avg > 0 and last_avg <= first_avg * RANGE_CONTRACTION_RATIO
    compressed = (
        multi_day_range_pct is not None
        and multi_day_range_pct <= COMPRESSION_LOOKBACK_RANGE_PCT
        and contracting
    )

    dist_high = dist_low = None
    near_high = near_low = False
    ref = spot if spot is not None and spot > 0 else last_close
    if ref > 0:
        dist_high = abs(lookback_high - ref) / ref * Decimal(100)
        dist_low = abs(ref - lookback_low) / ref * Decimal(100)
        near_high = dist_high <= NEAR_EXTREME_PCT
        near_low = dist_low <= NEAR_EXTREME_PCT

    return HistoricalStructure(
        status=HistoricalStructureStatus.OK,
        session_count=len(bars),
        completed_session_count=len(completed),
        lookback_high=lookback_high,
        lookback_low=lookback_low,
        last_completed_close=last_close,
        atr=atr,
        atr_pct=atr_pct,
        multi_day_range_pct=multi_day_range_pct,
        multi_day_compression=compressed,
        distance_to_lookback_high_pct=dist_high,
        distance_to_lookback_low_pct=dist_low,
        near_lookback_high=near_high,
        near_lookback_low=near_low,
        detail=(
            f"{len(completed)} completed sessions; ATR {atr_pct:.2f}% of close; "
            f"lookback range {multi_day_range_pct:.2f}%"
            if atr_pct is not None and multi_day_range_pct is not None
            else f"{len(completed)} completed sessions"
        ),
    )


def spot_near_levels(
    spot: Decimal,
    *,
    supports: list[Decimal],
    resistances: list[Decimal],
    max_pct: Decimal,
) -> bool:
    if spot <= 0:
        return False
    for level in supports + resistances:
        if abs(level - spot) / spot * Decimal(100) <= max_pct:
            return True
    return False
