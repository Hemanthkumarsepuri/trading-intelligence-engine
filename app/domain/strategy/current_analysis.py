"""Current/live analysis assembly — answers "at this exact `as_of`, what
does the strategy see, and why" as one auditable, typed, replayable object.

This is a NEW, separate reporting layer built on top of — never inside —
`detect_setup()`. It calls the same already-existing, unmodified functions
(`compute_ema_alignment`, `compute_vwap_position`,
`EMAVWAPAlignmentStrategy.detect_setup`) a second time purely to expose
their intermediate facts for audit; it does not change what the strategy
decides, only what is visible about how it decided. No new interpretation,
no new threshold, no new directional logic is introduced here.

Deliberately does NOT read `current_partial_candle` into the strategy's own
`candles` input — only `completed_candles` ever reaches `detect_setup()`,
exactly as `app/domain/market/live_candle_aggregator.py` requires. The
partial candle's own close/period, if supplied, is surfaced purely for
human visibility (e.g. "PRICE: <partial candle's latest tick>" in a
current-state display) — it never participates in the EMA/VWAP computation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from app.domain.market.live_candle_aggregator import PartialCandle
from app.domain.market.market_state import MarketState
from app.domain.market.models import Candle, Timeframe
from app.domain.strategy.contracts import Setup
from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy
from app.domain.technical.ema_alignment import EMAAlignmentState, compute_ema_alignment
from app.domain.technical.series import IndicatorStatus
from app.domain.technical.vwap_position import VWAPPositionState, compute_vwap_position


class CurrentAnalysisResult(BaseModel):
    """One auditable snapshot of what the strategy sees at one `as_of`.
    Every field here traces directly to an already-existing, unmodified
    computation — nothing is invented for presentation purposes.
    """

    instrument_id: str
    timeframe: Timeframe
    as_of: datetime
    strategy_name: str
    strategy_version: str

    last_completed_candle_timestamp: datetime | None
    candles_available: int

    ema9: Decimal | None
    ema21: Decimal | None
    ema50: Decimal | None
    ema_alignment: EMAAlignmentState | None
    ema_status: IndicatorStatus

    vwap_value: Decimal | None
    price: Decimal | None
    price_vs_vwap: VWAPPositionState | None
    vwap_status: IndicatorStatus

    setup: Setup | None

    data_freshness_seconds: float
    current_partial_candle_period_start: datetime | None
    current_partial_candle_close: Decimal | None


def assemble_current_analysis(
    *,
    market_state: MarketState,
    completed_candles: Mapping[Timeframe, Sequence[Candle]],
    current_partial_candle: PartialCandle | None,
    strategy: EMAVWAPAlignmentStrategy,
    as_of: datetime,
) -> CurrentAnalysisResult:
    """Pure function: every input is caller-supplied, nothing reads the
    clock, nothing fetches data. Raises whatever `detect_setup()`/
    `compute_ema_alignment()`/`compute_vwap_position()` would raise for
    malformed input (e.g. `InvalidCandleSeriesError`) — this function adds
    no new validation and no new tolerance for bad data.
    """
    timeframe = strategy.timeframe
    series = completed_candles.get(timeframe, [])

    alignment = compute_ema_alignment(
        series,
        instrument_id=market_state.instrument_id,
        timeframe=timeframe,
        periods=strategy.ema_periods,
        as_of=as_of,
    )
    vwap_position = compute_vwap_position(
        series, instrument_id=market_state.instrument_id, timeframe=timeframe, as_of=as_of
    )
    setup = strategy.detect_setup(market_state=market_state, candles=dict(completed_candles), as_of=as_of)

    ema9 = ema21 = ema50 = None
    if alignment.values is not None and len(alignment.values) == len(strategy.ema_periods):
        ema9, ema21, ema50 = alignment.values

    freshness_seconds = (as_of - market_state.quote.freshness.data_timestamp).total_seconds()

    return CurrentAnalysisResult(
        instrument_id=market_state.instrument_id,
        timeframe=timeframe,
        as_of=as_of,
        strategy_name=strategy.name,
        strategy_version=strategy.version,
        last_completed_candle_timestamp=vwap_position.latest_candle_timestamp,
        candles_available=vwap_position.candles_used,
        ema9=ema9,
        ema21=ema21,
        ema50=ema50,
        ema_alignment=alignment.state,
        ema_status=alignment.status,
        vwap_value=vwap_position.vwap_value,
        price=vwap_position.current_price,
        price_vs_vwap=vwap_position.state,
        vwap_status=vwap_position.status,
        setup=setup,
        data_freshness_seconds=freshness_seconds,
        current_partial_candle_period_start=current_partial_candle.period_start if current_partial_candle else None,
        current_partial_candle_close=current_partial_candle.close if current_partial_candle else None,
    )
