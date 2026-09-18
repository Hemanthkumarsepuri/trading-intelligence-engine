"""Replay-safety proof for `EMAVWAPAlignmentStrategy`, mirroring
`test_candle_inputs_replay_equivalence.py`: running the strategy through the
real replay path — `InMemoryCandleRepository -> HistoricalProvider ->
normalization -> strategy` — must produce exactly the same `Setup` as
computing directly against a pre-bounded candle list. No new persistence
infrastructure is introduced.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.data.normalization.mock_normalizer import MockNormalizer
from app.data.providers.historical_provider import HistoricalProvider
from app.domain.market.freshness import DataFreshness
from app.domain.market.market_state import assemble_market_state
from app.domain.market.models import Candle, ExchangeSegment, Quote, Timeframe
from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy
from app.persistence.in_memory import InMemoryCandleRepository, InMemoryOptionChainRepository
from tests.unit.strategy.fixtures import FALLING_CLOSES, RISING_CLOSES
from tests.unit.technical.factories import make_series_from_closes

T0 = datetime(2026, 8, 27, 9, 15, tzinfo=UTC)
INSTRUMENT_ID = "INST1"


def _replay_candles(all_candles: list[Candle], as_of: datetime) -> list[Candle]:
    normalizer = MockNormalizer()

    async def run() -> list[Candle]:
        repo = InMemoryCandleRepository()
        for candle in all_candles:
            await repo.save(candle)

        provider = HistoricalProvider(candles=repo, option_chains=InMemoryOptionChainRepository())
        raw_candles = await provider.get_ohlcv(
            security_id=INSTRUMENT_ID,
            exchange_segment=ExchangeSegment.NSE_EQ,
            timeframe=Timeframe.M15,
            start=T0 - timedelta(hours=1),
            end=T0 + timedelta(hours=20),
            as_of=as_of,
        )
        return [
            normalizer.normalize_candle(
                raw, instrument_id=INSTRUMENT_ID, timeframe=Timeframe.M15, received_at=raw.timestamp
            )
            for raw in raw_candles
        ]

    return asyncio.run(run())


def _run_case(closes: list[float], expected_direction: str) -> None:
    strategy = EMAVWAPAlignmentStrategy()
    future_closes = [1, 999, 1, 999]

    through_t = make_series_from_closes(
        closes, start=T0, step=timedelta(minutes=15), timeframe=Timeframe.M15, instrument_id=INSTRUMENT_ID
    )
    as_of = through_t[-1].freshness.data_timestamp
    future = make_series_from_closes(
        future_closes,
        start=as_of + timedelta(minutes=15),
        step=timedelta(minutes=15),
        timeframe=Timeframe.M15,
        instrument_id=INSTRUMENT_ID,
    )
    all_candles = through_t + future

    quote = Quote(
        provider="mock",
        freshness=DataFreshness(data_timestamp=as_of, received_timestamp=as_of),
        instrument_id=INSTRUMENT_ID,
        last_price=Decimal("100"),
    )
    market_state = assemble_market_state(quote, as_of=as_of)

    direct_result = strategy.detect_setup(market_state=market_state, candles={Timeframe.M15: through_t}, as_of=as_of)

    replayed_candles = _replay_candles(all_candles, as_of)
    replay_result = strategy.detect_setup(
        market_state=market_state, candles={Timeframe.M15: replayed_candles}, as_of=as_of
    )

    assert replay_result == direct_result
    assert direct_result is not None
    assert direct_result.direction == expected_direction


def test_bullish_case_replay_path_matches_direct_bounded_computation() -> None:
    _run_case(RISING_CLOSES, "BULLISH")


def test_bearish_case_replay_path_matches_direct_bounded_computation() -> None:
    _run_case(FALLING_CLOSES, "BEARISH")
