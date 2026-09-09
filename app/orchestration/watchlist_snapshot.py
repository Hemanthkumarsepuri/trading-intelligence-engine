"""Watchlist current-analysis orchestration.

The one place that sequences real calls to `data/providers` (Upstox REST:
market status, batch quotes, historical M15) and hands the results to
`domain/strategy`'s already-existing, unmodified `assemble_current_analysis()`
— per ARCHITECTURE.md §6, this is exactly what `orchestration/` exists for
("the only layer that knows about the *sequence* of stages, calls `data/`
[...]"). This is the only new orchestration-layer code this milestone adds;
it is one function producing one typed result per instrument, not a
generic multi-provider/multi-strategy framework.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from app.data.normalization.base import DefaultNormalizer
from app.data.providers.exceptions import ProviderError
from app.data.providers.upstox_instrument_master import InstrumentRef, resolve_symbol
from app.data.providers.upstox_provider import ExchangeStatus, UpstoxProvider
from app.domain.market.data_state import MarketDataState, classify_market_data_state
from app.domain.market.market_state import assemble_market_state
from app.domain.market.models import Candle, ExchangeSegment, Quote
from app.domain.strategy.current_analysis import CurrentAnalysisResult, assemble_current_analysis
from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy


@dataclass
class InstrumentSnapshot:
    requested_symbol: str
    instrument: InstrumentRef | None
    state: MarketDataState
    exchange_status: ExchangeStatus | None
    quote: Quote | None
    previous_close: Decimal | None
    day_change: Decimal | None
    day_change_pct: Decimal | None
    data_age_seconds: float | None
    analysis: CurrentAnalysisResult | None
    error: str | None


async def build_watchlist_snapshot(
    *,
    provider: UpstoxProvider,
    instrument_master: Sequence[dict[str, object]],
    symbols: Sequence[str],
    strategy: EMAVWAPAlignmentStrategy,
    as_of: datetime,
    history_lookback: timedelta = timedelta(days=10),
    stale_threshold: timedelta = timedelta(hours=1),
) -> list[InstrumentSnapshot]:
    """Builds one `InstrumentSnapshot` per requested symbol. Never invents a
    value: an unresolvable symbol, a failed provider call, or insufficient
    history each produce an explicit, reasoned state rather than a
    fabricated result.
    """
    normalizer = DefaultNormalizer(provider_name=provider.name)

    try:
        exchange_status: ExchangeStatus | None = await provider.get_market_status(exchange="NSE")
    except ProviderError:
        # A failed status call doesn't have to block every instrument --
        # each instrument's own quote/history calls independently determine
        # whether it can produce a result; market status is UNKNOWN here,
        # not treated as "closed" (which would be a fabricated inference).
        exchange_status = ExchangeStatus.UNKNOWN

    instrument_refs = {symbol: resolve_symbol(instrument_master, symbol) for symbol in symbols}
    resolved_keys = [ref.instrument_key for ref in instrument_refs.values() if ref is not None]

    quotes_error: str | None = None
    try:
        raw_quotes = await provider.get_quotes(resolved_keys) if resolved_keys else {}
    except ProviderError as exc:
        raw_quotes = {}
        quotes_error = str(exc)

    requirement = strategy.required_timeframes()[0]
    results: list[InstrumentSnapshot] = []

    for symbol in symbols:
        ref = instrument_refs[symbol]
        if ref is None:
            results.append(
                _error_snapshot(
                    symbol, None, exchange_status, f"symbol {symbol!r} not found in the Upstox instrument master"
                )
            )
            continue

        if quotes_error is not None:
            results.append(
                InstrumentSnapshot(
                    requested_symbol=symbol,
                    instrument=ref,
                    state=MarketDataState.PROVIDER_UNAVAILABLE,
                    exchange_status=exchange_status,
                    quote=None,
                    previous_close=None,
                    day_change=None,
                    day_change_pct=None,
                    data_age_seconds=None,
                    analysis=None,
                    error=quotes_error,
                )
            )
            continue

        raw_quote = raw_quotes.get(ref.instrument_key)
        if raw_quote is None:
            results.append(_error_snapshot(symbol, ref, exchange_status, "no quote returned for this instrument"))
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
        except ProviderError as exc:
            results.append(
                InstrumentSnapshot(
                    requested_symbol=symbol,
                    instrument=ref,
                    state=MarketDataState.PROVIDER_UNAVAILABLE,
                    exchange_status=exchange_status,
                    quote=None,
                    previous_close=None,
                    day_change=None,
                    day_change_pct=None,
                    data_age_seconds=None,
                    analysis=None,
                    error=str(exc),
                )
            )
            continue

        quote = normalizer.normalize_quote(raw_quote, instrument_id=ref.instrument_key, received_at=as_of)
        candles: list[Candle] = [
            normalizer.normalize_candle(
                raw, instrument_id=ref.instrument_key, timeframe=requirement.timeframe, received_at=raw.timestamp
            )
            for raw in raw_candles
        ]

        data_age = as_of - quote.freshness.data_timestamp
        state = classify_market_data_state(
            exchange_status_is_open=exchange_status == ExchangeStatus.NORMAL_OPEN,
            is_live_stream=False,  # this path is REST-only; the WS path is scripts/live_feed_session.py
            data_age=data_age,
            data_date=quote.freshness.data_timestamp.date(),
            as_of_date=as_of.date(),
            candles_available=len(candles),
            minimum_candles=requirement.minimum_candles,
            stale_threshold=stale_threshold,
        )

        previous_close = quote.previous_close
        day_change = quote.last_price - previous_close if previous_close is not None else None
        day_change_pct = (day_change / previous_close * Decimal(100)) if day_change is not None and previous_close else None

        analysis: CurrentAnalysisResult | None = None
        if state not in (MarketDataState.INSUFFICIENT_HISTORY, MarketDataState.PROVIDER_UNAVAILABLE, MarketDataState.ERROR):
            market_state = assemble_market_state(quote, as_of=as_of)
            analysis = assemble_current_analysis(
                market_state=market_state,
                completed_candles={requirement.timeframe: candles},
                current_partial_candle=None,
                strategy=strategy,
                as_of=as_of,
            )

        results.append(
            InstrumentSnapshot(
                requested_symbol=symbol,
                instrument=ref,
                state=state,
                exchange_status=exchange_status,
                quote=quote,
                previous_close=previous_close,
                day_change=day_change,
                day_change_pct=day_change_pct,
                data_age_seconds=data_age.total_seconds(),
                analysis=analysis,
                error=None,
            )
        )

    return results


def _error_snapshot(
    symbol: str, ref: InstrumentRef | None, exchange_status: ExchangeStatus | None, reason: str
) -> InstrumentSnapshot:
    return InstrumentSnapshot(
        requested_symbol=symbol,
        instrument=ref,
        state=MarketDataState.ERROR,
        exchange_status=exchange_status,
        quote=None,
        previous_close=None,
        day_change=None,
        day_change_pct=None,
        data_age_seconds=None,
        analysis=None,
        error=reason,
    )
