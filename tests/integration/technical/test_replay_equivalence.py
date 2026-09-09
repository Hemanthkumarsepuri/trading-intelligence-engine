"""Replay-safety proof required by this milestone's brief (section 16):
for the same historical candle set and the same `as_of`, running the
Technical Engine through the real replay path —
`InMemoryCandleRepository` -> `HistoricalProvider` -> normalization ->
`domain/technical` — produces exactly the same result as computing directly
against a pre-bounded candle list. This is what proves the Technical Engine
is safe to run in `REPLAY` mode through the existing Data Foundation
contracts, not just safe in isolation.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.data.normalization.mock_normalizer import MockNormalizer
from app.data.providers.historical_provider import HistoricalProvider
from app.domain.market.models import Candle, ExchangeSegment, Timeframe
from app.domain.technical.series import IndicatorStatus
from app.domain.technical.trend import calculate_ema
from app.persistence.in_memory import InMemoryCandleRepository, InMemoryOptionChainRepository
from tests.unit.technical.factories import make_series_from_closes

T0 = datetime(2026, 8, 27, 9, 15, tzinfo=UTC)
INSTRUMENT_ID = "INST1"


def test_replay_path_matches_direct_bounded_computation() -> None:
    closes = [100, 101, 99, 102, 103, 101, 104, 105, 103, 106]
    future_closes = [1, 999, 1, 999]  # must never leak into the replay result

    all_candles = make_series_from_closes(closes + future_closes, start=T0, instrument_id=INSTRUMENT_ID)
    through_t = all_candles[: len(closes)]
    as_of = through_t[-1].freshness.data_timestamp

    # Direct path: compute EMA against the pre-bounded (already-through-T) list.
    direct_result = calculate_ema(through_t, instrument_id=INSTRUMENT_ID, timeframe=Timeframe.M5, period=5, as_of=as_of)

    # Replay path: persist EVERYTHING (including the future candles) into the
    # repository, then read back through HistoricalProvider + normalization,
    # exactly as a real replay run would.
    normalizer = MockNormalizer()

    async def run() -> list[Candle]:
        repo = InMemoryCandleRepository()
        for candle in all_candles:
            await repo.save(candle)

        provider = HistoricalProvider(candles=repo, option_chains=InMemoryOptionChainRepository())
        raw_candles = await provider.get_ohlcv(
            security_id=INSTRUMENT_ID,
            exchange_segment=ExchangeSegment.NSE_EQ,
            timeframe=Timeframe.M5,
            start=T0 - timedelta(hours=1),
            end=T0 + timedelta(hours=1),
            as_of=as_of,
        )
        return [
            normalizer.normalize_candle(raw, instrument_id=INSTRUMENT_ID, timeframe=Timeframe.M5, received_at=raw.timestamp)
            for raw in raw_candles
        ]

    replayed_candles = asyncio.run(run())
    replay_result = calculate_ema(
        replayed_candles, instrument_id=INSTRUMENT_ID, timeframe=Timeframe.M5, period=5, as_of=as_of
    )

    assert replay_result.status == IndicatorStatus.OK
    assert direct_result.status == IndicatorStatus.OK
    assert replay_result.value == direct_result.value
    assert replay_result.candles_used == direct_result.candles_used == len(closes)
    # Sanity: the future block was extreme enough that a leak would have
    # produced a wildly different EMA — confirm the value stayed sane.
    assert replay_result.value is not None
    assert Decimal(90) < replay_result.value < Decimal(115)
