"""Replay-safety proof for the Strategy Input Foundation, mirroring
`test_replay_equivalence.py`/`test_snapshot_replay_equivalence.py`: running
`assemble_candle_inputs`/`check_timeframe_requirements` through the real
replay path — `InMemoryCandleRepository -> HistoricalProvider ->
normalization -> assemble_candle_inputs` — must produce exactly the same
result as computing directly against a pre-bounded candle list. No new
persistence infrastructure is introduced.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from app.data.normalization.mock_normalizer import MockNormalizer
from app.data.providers.historical_provider import HistoricalProvider
from app.domain.market.candle_inputs import assemble_candle_inputs
from app.domain.market.models import Candle, ExchangeSegment, Timeframe
from app.domain.market.timeframe_requirement import (
    TimeframeRequirement,
    check_timeframe_requirements,
)
from app.persistence.in_memory import InMemoryCandleRepository, InMemoryOptionChainRepository
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
            timeframe=Timeframe.M5,
            start=T0 - timedelta(hours=1),
            end=T0 + timedelta(hours=1),
            as_of=as_of,
        )
        return [
            normalizer.normalize_candle(raw, instrument_id=INSTRUMENT_ID, timeframe=Timeframe.M5, received_at=raw.timestamp)
            for raw in raw_candles
        ]

    return asyncio.run(run())


def test_assemble_candle_inputs_replay_path_matches_direct_bounded_computation() -> None:
    closes = [100, 101, 99, 102, 103, 101, 104, 105]
    future_closes = [1, 999, 1, 999]

    all_candles = make_series_from_closes(closes + future_closes, start=T0, instrument_id=INSTRUMENT_ID)
    through_t = all_candles[: len(closes)]
    as_of = through_t[-1].freshness.data_timestamp

    direct_result = assemble_candle_inputs({Timeframe.M5: through_t}, instrument_id=INSTRUMENT_ID, as_of=as_of)
    replayed_candles = _replay_candles(all_candles, as_of)
    replay_result = assemble_candle_inputs(
        {Timeframe.M5: replayed_candles}, instrument_id=INSTRUMENT_ID, as_of=as_of
    )

    assert replay_result == direct_result
    assert len(replay_result[Timeframe.M5]) == len(closes)


def test_check_timeframe_requirements_replay_path_matches_direct_bounded_computation() -> None:
    closes = [100, 101, 99, 102, 103, 101, 104, 105]
    future_closes = [1, 999, 1, 999]
    requirements = [TimeframeRequirement(timeframe=Timeframe.M5, minimum_candles=5)]

    all_candles = make_series_from_closes(closes + future_closes, start=T0, instrument_id=INSTRUMENT_ID)
    through_t = all_candles[: len(closes)]
    as_of = through_t[-1].freshness.data_timestamp

    direct_result = check_timeframe_requirements(
        {Timeframe.M5: through_t}, instrument_id=INSTRUMENT_ID, as_of=as_of, requirements=requirements
    )
    replayed_candles = _replay_candles(all_candles, as_of)
    replay_result = check_timeframe_requirements(
        {Timeframe.M5: replayed_candles}, instrument_id=INSTRUMENT_ID, as_of=as_of, requirements=requirements
    )

    assert replay_result == direct_result
    assert replay_result.all_satisfied
