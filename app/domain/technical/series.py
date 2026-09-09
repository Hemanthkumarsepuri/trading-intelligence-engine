"""Shared no-look-ahead boundary enforcement and status vocabulary for every
technical calculation in this package.

Every `calculate_*`/`find_*` function in `domain/technical/` funnels its
input candles through `bounded_series()` first — this is the single, tested
enforcement point for the `as_of` boundary (ARCHITECTURE.md Addendum A5
applies equally here: a technical calculation must never be influenced by a
candle timestamped after `as_of`, in live or replay mode). No indicator
function re-implements this filtering itself, and none of them read the
system clock — `as_of` is always supplied by the caller.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from enum import Enum
from itertools import pairwise

from app.domain.market.models import Candle, Timeframe


class IndicatorStatus(str, Enum):
    """Every technical result is exactly one of these — never an opaque score.

    OK: a valid value was computed from sufficient, well-formed history.
    INSUFFICIENT_HISTORY: the calculation is well-defined but there was not
        enough qualifying history at or before `as_of` to produce it. This is
        a normal, expected, frequent condition (the first hour of a new
        listing, any morning before enough of the day's bars exist, a
        newly-added instrument) — never represented as a fabricated value or
        a silent zero, and never resolved by looking past `as_of`.
    """

    OK = "OK"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"


class InvalidCandleSeriesError(ValueError):
    """Raised when the input candle series itself violates a structural
    contract this package assumes has already been enforced upstream by
    `DataQualityGate` (ARCHITECTURE.md Addendum A3) — out-of-order
    timestamps, duplicate timestamps, or candles mixing instruments/
    timeframes into a single series. This signals a data-integrity bug, not
    a normal market condition, so it raises rather than degrading to a typed
    status the caller might silently tolerate.
    """


def bounded_series(
    candles: Sequence[Candle], *, instrument_id: str, timeframe: Timeframe, as_of: datetime
) -> list[Candle]:
    """The no-look-ahead boundary.

    Returns only the candles from `candles` that: belong to `instrument_id`
    and `timeframe`, and have `freshness.data_timestamp <= as_of` — sorted
    ascending by timestamp with no duplicates verified. Reads nothing but its
    arguments; never calls the clock.

    Raises `InvalidCandleSeriesError` if any candle in `candles` belongs to a
    different instrument/timeframe, or if the resulting bounded subset is not
    strictly ascending with unique timestamps.
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")

    for candle in candles:
        if candle.instrument_id != instrument_id:
            raise InvalidCandleSeriesError(
                f"candle instrument_id {candle.instrument_id!r} does not match requested {instrument_id!r}"
            )
        if candle.timeframe != timeframe:
            raise InvalidCandleSeriesError(
                f"candle timeframe {candle.timeframe.value!r} does not match requested {timeframe.value!r}"
            )

    bounded = [c for c in candles if c.freshness.data_timestamp <= as_of]

    for earlier, later in pairwise(bounded):
        if later.freshness.data_timestamp < earlier.freshness.data_timestamp:
            raise InvalidCandleSeriesError("candles are not in strictly ascending timestamp order")
        if later.freshness.data_timestamp == earlier.freshness.data_timestamp:
            raise InvalidCandleSeriesError("duplicate candle timestamp in series")

    return bounded
