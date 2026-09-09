"""Live, multi-instrument market-intelligence session — the integration
layer connecting already-existing, already-tested pieces:

    UpstoxLiveFeedClient.stream()   (unchanged)
        -> RawTick
        -> MultiInstrumentCandleAggregator.on_tick()   (unchanged)
        -> merged with REST-seeded historical candles  (new: this module)
        -> assemble_current_analysis()                 (unchanged)
        -> InstrumentSnapshot                            (unchanged type)
        -> analysis_change.detect_changes()             (unchanged)

Nothing here reimplements WebSocket handling, protobuf decoding, candle
aggregation, EMA/VWAP math, or the strategy — this module's only new logic
is: (1) seeding each instrument with real REST history at startup so the
engine doesn't need 50 live ticks before it means anything, (2) merging
that seeded history with whatever the live aggregator itself completes,
without duplicating a boundary candle, and (3) REST reconciliation that
never overwrites a newer WebSocket-derived state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from app.data.normalization.base import DefaultNormalizer
from app.data.providers.base import RawTick
from app.data.providers.exceptions import ProviderError
from app.data.providers.upstox_instrument_master import InstrumentRef, resolve_symbol
from app.data.providers.upstox_provider import ExchangeStatus, UpstoxProvider
from app.domain.market.data_state import MarketDataState, classify_market_data_state
from app.domain.market.freshness import DataFreshness
from app.domain.market.market_state import assemble_market_state
from app.domain.market.models import Candle, ExchangeSegment, Quote
from app.domain.market.multi_instrument_aggregator import MultiInstrumentCandleAggregator
from app.domain.strategy.current_analysis import assemble_current_analysis
from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy
from app.orchestration.analysis_change import ChangeEvent, detect_changes
from app.orchestration.watchlist_snapshot import InstrumentSnapshot


@dataclass
class _InstrumentState:
    instrument: InstrumentRef
    seeded_candles: list[Candle]
    last_ws_tick: RawTick | None = None
    previous_snapshot: InstrumentSnapshot | None = None
    latest_snapshot: InstrumentSnapshot | None = None


class LiveMarketIntelligenceSession:
    """Owns per-instrument state for one watchlist. `on_tick()` is the only
    WebSocket-driven mutator; `reconcile_via_rest()` is the only REST-driven
    one. Both funnel through the same `_refresh()` so there is exactly one
    place that builds an `InstrumentSnapshot`.
    """

    def __init__(
        self,
        *,
        provider_name: str,
        strategy: EMAVWAPAlignmentStrategy,
        exchange_status: ExchangeStatus,
        states: dict[str, _InstrumentState],
        stale_threshold: timedelta,
    ) -> None:
        self._provider_name = provider_name
        self._strategy = strategy
        self._requirement = strategy.required_timeframes()[0]
        self._exchange_status = exchange_status
        self._states = states
        self._stale_threshold = stale_threshold
        self._aggregator = MultiInstrumentCandleAggregator(
            timeframe=self._requirement.timeframe, provider_name=provider_name
        )
        self._normalizer = DefaultNormalizer(provider_name=provider_name)

    @classmethod
    async def start(
        cls,
        *,
        provider: UpstoxProvider,
        instrument_master: list[dict[str, object]],
        symbols: list[str],
        strategy: EMAVWAPAlignmentStrategy,
        as_of: datetime,
        history_lookback: timedelta = timedelta(days=10),
        stale_threshold: timedelta = timedelta(seconds=15),
    ) -> LiveMarketIntelligenceSession:
        """Phase-6 startup: resolve every symbol, fetch real market status
        once, and real REST history per instrument -- so the session has a
        real, evidenced market picture before a single WebSocket tick has
        arrived. Never fabricates a candle for an unresolvable symbol; that
        symbol is simply absent from the session's watchlist (the caller
        can inspect which symbols resolved via `instruments()`).
        """
        try:
            exchange_status = await provider.get_market_status(exchange="NSE")
        except ProviderError:
            exchange_status = ExchangeStatus.UNKNOWN

        requirement = strategy.required_timeframes()[0]
        states: dict[str, _InstrumentState] = {}
        for symbol in symbols:
            ref = resolve_symbol(instrument_master, symbol)
            if ref is None:
                continue
            try:
                raw_candles = await provider.get_ohlcv(
                    security_id=ref.instrument_key,
                    exchange_segment=ExchangeSegment.NSE_EQ,
                    timeframe=requirement.timeframe,
                    start=as_of - history_lookback,
                    end=as_of,
                    as_of=as_of,
                )
            except ProviderError:
                raw_candles = []
            normalizer = DefaultNormalizer(provider_name=provider.name)
            seeded = [
                normalizer.normalize_candle(
                    raw, instrument_id=ref.instrument_key, timeframe=requirement.timeframe, received_at=raw.timestamp
                )
                for raw in raw_candles
            ]
            states[ref.instrument_key] = _InstrumentState(instrument=ref, seeded_candles=seeded)

        return cls(
            provider_name=provider.name,
            strategy=strategy,
            exchange_status=exchange_status,
            states=states,
            stale_threshold=stale_threshold,
        )

    def instruments(self) -> list[InstrumentRef]:
        return [s.instrument for s in self._states.values()]

    @property
    def exchange_status(self) -> ExchangeStatus:
        return self._exchange_status

    def set_exchange_status(self, status: ExchangeStatus) -> None:
        self._exchange_status = status

    def merged_completed_candles(self, instrument_key: str) -> list[Candle]:
        """REST-seeded history plus whatever the live aggregator has itself
        completed since session start -- deduplicated at the boundary
        (never both the seeded and the live-aggregated version of the same
        M15 bucket) rather than left for `bounded_series()` to reject as a
        duplicate timestamp.
        """
        state = self._states.get(instrument_key)
        if state is None:
            return []
        live = self._aggregator.completed_candles(instrument_key)
        if not live:
            return list(state.seeded_candles)
        cutoff = state.seeded_candles[-1].freshness.data_timestamp if state.seeded_candles else None
        new_live = [c for c in live if cutoff is None or c.freshness.data_timestamp > cutoff]
        return state.seeded_candles + new_live

    def on_tick(self, tick: RawTick) -> InstrumentSnapshot | None:
        """Returns `None` for a tick on an instrument outside this
        session's resolved watchlist -- ignored, not an error (Upstox may
        echo other subscriptions; this session only tracks its own).
        """
        state = self._states.get(tick.instrument_key)
        if state is None:
            return None

        self._aggregator.on_tick(
            instrument_key=tick.instrument_key,
            price=_to_decimal(tick.price),
            volume_since_last_tick=tick.quantity,
            timestamp=tick.timestamp,
        )
        state.last_ws_tick = tick

        quote = Quote(
            provider=self._provider_name,
            freshness=DataFreshness(data_timestamp=tick.timestamp, received_timestamp=tick.timestamp),
            instrument_id=tick.instrument_key,
            last_price=_to_decimal(tick.price),
        )
        # An `initial_feed`-sourced tick (Upstox's one-time post-subscribe
        # snapshot) is real data, but it is NOT continuous live streaming --
        # only a genuine `live_feed` tick may be classified as such.
        return self._refresh(
            tick.instrument_key, quote=quote, as_of=tick.timestamp, is_live=not tick.is_initial_snapshot
        )

    async def reconcile_via_rest(
        self, instrument_key: str, *, provider: UpstoxProvider, as_of: datetime
    ) -> InstrumentSnapshot | None:
        """REST fallback: refetches the real quote for one instrument and
        refreshes its snapshot from it -- but only if that REST quote is
        NOT older than the most recent WebSocket tick already known for
        this instrument (a REST poll must never overwrite fresher
        WebSocket-derived state with a stale one).
        """
        state = self._states.get(instrument_key)
        if state is None:
            return None

        try:
            raw_quote = await provider.get_quote(
                security_id=instrument_key, exchange_segment=ExchangeSegment.NSE_EQ, as_of=as_of
            )
        except ProviderError:
            return None

        quote = self._normalizer.normalize_quote(raw_quote, instrument_id=instrument_key, received_at=as_of)

        if state.last_ws_tick is not None and quote.freshness.data_timestamp <= state.last_ws_tick.timestamp:
            # The REST snapshot is not newer than what WebSocket already
            # gave us -- do not regress the state with it.
            return state.latest_snapshot

        return self._refresh(instrument_key, quote=quote, as_of=as_of, is_live=False)

    def is_stale(self, instrument_key: str, *, as_of: datetime) -> bool:
        """Whether this instrument's most recent WebSocket tick (if any) is
        older than `stale_threshold` as of `as_of` -- the trigger condition
        for a caller to invoke `reconcile_via_rest()`.
        """
        state = self._states.get(instrument_key)
        if state is None or state.last_ws_tick is None:
            return True
        return (as_of - state.last_ws_tick.timestamp) > self._stale_threshold

    def changes_for(self, instrument_key: str) -> list[ChangeEvent]:
        state = self._states.get(instrument_key)
        if state is None or state.latest_snapshot is None:
            return []
        return detect_changes(previous=state.previous_snapshot, current=state.latest_snapshot)

    def latest_snapshot(self, instrument_key: str) -> InstrumentSnapshot | None:
        state = self._states.get(instrument_key)
        return state.latest_snapshot if state is not None else None

    # -- internal -------------------------------------------------------

    def _refresh(self, instrument_key: str, *, quote: Quote, as_of: datetime, is_live: bool) -> InstrumentSnapshot:
        state = self._states[instrument_key]
        candles = self.merged_completed_candles(instrument_key)
        data_age = as_of - quote.freshness.data_timestamp

        data_state = classify_market_data_state(
            exchange_status_is_open=self._exchange_status == ExchangeStatus.NORMAL_OPEN,
            is_live_stream=is_live,
            data_age=data_age,
            data_date=quote.freshness.data_timestamp.date(),
            as_of_date=as_of.date(),
            candles_available=len(candles),
            minimum_candles=self._requirement.minimum_candles,
            stale_threshold=self._stale_threshold,
        )

        analysis = None
        if data_state not in (MarketDataState.INSUFFICIENT_HISTORY, MarketDataState.ERROR, MarketDataState.PROVIDER_UNAVAILABLE):
            market_state = assemble_market_state(quote, as_of=as_of)
            analysis = assemble_current_analysis(
                market_state=market_state,
                completed_candles={self._requirement.timeframe: candles},
                current_partial_candle=self._aggregator.current_partial_candle(instrument_key),
                strategy=self._strategy,
                as_of=as_of,
            )

        previous_close = quote.previous_close
        day_change = quote.last_price - previous_close if previous_close is not None else None
        day_change_pct = (day_change / previous_close * Decimal(100)) if day_change is not None and previous_close else None

        snapshot = InstrumentSnapshot(
            requested_symbol=state.instrument.requested_symbol,
            instrument=state.instrument,
            state=data_state,
            exchange_status=self._exchange_status,
            quote=quote,
            previous_close=previous_close,
            day_change=day_change,
            day_change_pct=day_change_pct,
            data_age_seconds=data_age.total_seconds(),
            analysis=analysis,
            error=None,
        )

        state.previous_snapshot = state.latest_snapshot
        state.latest_snapshot = snapshot
        return snapshot


def _to_decimal(value: float) -> Decimal:
    return Decimal(str(value))
