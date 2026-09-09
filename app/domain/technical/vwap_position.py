"""VWAP position — a literal comparison of price against VWAP. Nothing more.

Built directly on top of `structure.calculate_vwap()`. `ABOVE`/`BELOW`/`AT`
is an exact `Decimal` comparison — `AT` means exact equality, the same
zero-tolerance convention `structure_facts.py` already uses for
`EQUAL_HIGH`/`EQUAL_LOW`. No tolerance band is invented here; none exists
elsewhere in this package, and adding one for this module alone would be an
unjustified, unprecedented departure. In practice `AT` will be rare, because
`vwap_value` is a division result (typically many decimal places) while
`current_price` is tick-granular — that rarity is expected and correct, not
a defect, exactly as `EQUAL_HIGH` is also expected to be rare.

IMPORTANT — `ABOVE`/`BELOW` are positional facts, not trading conclusions.
"Price above VWAP" does not mean "bullish," for two independent reasons:
(1) attaching directional vocabulary to a threshold-free comparison is
exactly the interpretive leap ARCHITECTURE.md's Directional Vote Final Gate
(2026-08-27) found unjustified for EMA/MACD/VWAP alike; and (2) the
underlying `calculate_vwap()` deliberately performs no session reset — it
computes VWAP over exactly the candle series it is given, so "above" a
multi-day VWAP and "above" a same-day VWAP are different, non-interchangeable
facts. This module does not know or enforce which one it was given; that
remains the caller's responsibility, unchanged from `calculate_vwap()`'s own
documented contract. Turning this positional fact into a trading read is
`StrategyDefinition`'s job, not this module's.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel

from app.domain.market.models import Candle, Timeframe
from app.domain.technical.series import IndicatorStatus
from app.domain.technical.structure import calculate_session_vwap, calculate_vwap


class VWAPPositionState(str, Enum):
    ABOVE = "ABOVE"
    BELOW = "BELOW"
    AT = "AT"


class VWAPPositionResult(BaseModel):
    status: IndicatorStatus
    instrument_id: str
    timeframe: Timeframe
    as_of: datetime
    candles_used: int
    latest_candle_timestamp: datetime | None
    current_price: Decimal | None
    vwap_value: Decimal | None
    state: VWAPPositionState | None
    detail: str


def compute_vwap_position(
    candles: Sequence[Candle],
    *,
    instrument_id: str,
    timeframe: Timeframe,
    as_of: datetime,
) -> VWAPPositionResult:
    """Delegates all no-look-ahead filtering and VWAP math to
    `calculate_vwap()`; computes nothing new numerically, only compares its
    two already-computed outputs.
    """
    vwap_result = calculate_vwap(candles, instrument_id=instrument_id, timeframe=timeframe, as_of=as_of)

    current_price = vwap_result.current_price
    vwap_value = vwap_result.value

    if vwap_result.status == IndicatorStatus.INSUFFICIENT_HISTORY or current_price is None or vwap_value is None:
        # The `current_price is None or vwap_value is None` arm is a
        # defensive re-check, not expected to trigger independently of
        # `status == INSUFFICIENT_HISTORY` — `calculate_vwap()` always
        # populates both together on its OK path. Kept explicit rather than
        # a bare `assert` so this stays a typed, non-crashing result even if
        # that invariant were ever violated upstream.
        return VWAPPositionResult(
            status=IndicatorStatus.INSUFFICIENT_HISTORY,
            instrument_id=instrument_id,
            timeframe=timeframe,
            as_of=as_of,
            candles_used=vwap_result.candles_used,
            latest_candle_timestamp=vwap_result.latest_candle_timestamp,
            current_price=None,
            vwap_value=None,
            state=None,
            detail=vwap_result.detail,
        )

    if current_price > vwap_value:
        state = VWAPPositionState.ABOVE
    elif current_price < vwap_value:
        state = VWAPPositionState.BELOW
    else:
        state = VWAPPositionState.AT

    return VWAPPositionResult(
        status=IndicatorStatus.OK,
        instrument_id=instrument_id,
        timeframe=timeframe,
        as_of=as_of,
        candles_used=vwap_result.candles_used,
        latest_candle_timestamp=vwap_result.latest_candle_timestamp,
        current_price=current_price,
        vwap_value=vwap_value,
        state=state,
        detail=f"VWAP position from {vwap_result.candles_used} candles -> {state.value}",
    )


def compute_session_vwap_position(
    candles: Sequence[Candle],
    *,
    instrument_id: str,
    timeframe: Timeframe,
    as_of: datetime,
) -> VWAPPositionResult:
    """Positional fact vs NSE cash-session VWAP (IST 09:15-15:30)."""
    vwap_result = calculate_session_vwap(candles, instrument_id=instrument_id, timeframe=timeframe, as_of=as_of)
    current_price = vwap_result.current_price
    vwap_value = vwap_result.value
    if vwap_result.status == IndicatorStatus.INSUFFICIENT_HISTORY or current_price is None or vwap_value is None:
        return VWAPPositionResult(
            status=IndicatorStatus.INSUFFICIENT_HISTORY,
            instrument_id=instrument_id,
            timeframe=timeframe,
            as_of=as_of,
            candles_used=vwap_result.candles_used,
            latest_candle_timestamp=vwap_result.latest_candle_timestamp,
            current_price=None,
            vwap_value=None,
            state=None,
            detail=vwap_result.detail,
        )
    if current_price > vwap_value:
        state = VWAPPositionState.ABOVE
    elif current_price < vwap_value:
        state = VWAPPositionState.BELOW
    else:
        state = VWAPPositionState.AT
    return VWAPPositionResult(
        status=IndicatorStatus.OK,
        instrument_id=instrument_id,
        timeframe=timeframe,
        as_of=as_of,
        candles_used=vwap_result.candles_used,
        latest_candle_timestamp=vwap_result.latest_candle_timestamp,
        current_price=current_price,
        vwap_value=vwap_value,
        state=state,
        detail=f"session VWAP position from {vwap_result.candles_used} current-session M15 bars -> {state.value}",
    )
