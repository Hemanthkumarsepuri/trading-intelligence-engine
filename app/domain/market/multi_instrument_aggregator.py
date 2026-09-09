"""Routes a single interleaved tick stream (as Upstox's WebSocket sends,
once subscribed to more than one instrument) to one `LiveCandleAggregator`
per instrument — the piece `scripts/live_feed_session.py`'s single-
instrument design didn't need, but a real watchlist does.

Pure routing only: no new aggregation logic. Each instrument's
`LiveCandleAggregator` is the exact same, already-tested class; this module
does not duplicate or reimplement any of its no-look-ahead/out-of-order/
partial-vs-completed behavior.

`on_tick()` takes plain `instrument_key`/`price`/`volume_since_last_tick`/
`timestamp` parameters — the same shape `LiveCandleAggregator.on_tick()`
itself already uses — rather than a provider-specific `RawTick` object,
because `domain/*` may not import from `data/providers/*`
(ARCHITECTURE.md §6). The caller (an orchestration/script layer) unpacks a
`RawTick` into these plain values before calling in.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.domain.market.live_candle_aggregator import LiveCandleAggregator, PartialCandle
from app.domain.market.models import Candle, Timeframe


class MultiInstrumentCandleAggregator:
    """One `LiveCandleAggregator` per instrument key, created lazily on that
    instrument's first tick — so subscribing to N instruments doesn't
    require pre-declaring all N up front.
    """

    def __init__(self, *, timeframe: Timeframe, provider_name: str) -> None:
        self._timeframe = timeframe
        self._provider_name = provider_name
        self._aggregators: dict[str, LiveCandleAggregator] = {}

    def on_tick(
        self, *, instrument_key: str, price: Decimal, volume_since_last_tick: int, timestamp: datetime
    ) -> None:
        aggregator = self._aggregators.get(instrument_key)
        if aggregator is None:
            aggregator = LiveCandleAggregator(
                instrument_id=instrument_key, timeframe=self._timeframe, provider_name=self._provider_name
            )
            self._aggregators[instrument_key] = aggregator
        aggregator.on_tick(price=price, volume_since_last_tick=volume_since_last_tick, timestamp=timestamp)

    def completed_candles(self, instrument_key: str) -> list[Candle]:
        aggregator = self._aggregators.get(instrument_key)
        return aggregator.completed_candles if aggregator is not None else []

    def current_partial_candle(self, instrument_key: str) -> PartialCandle | None:
        aggregator = self._aggregators.get(instrument_key)
        return aggregator.current_partial_candle if aggregator is not None else None

    def known_instruments(self) -> list[str]:
        """Instrument keys that have received at least one tick so far."""
        return list(self._aggregators)
