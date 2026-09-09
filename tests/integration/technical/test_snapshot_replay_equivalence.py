"""Replay-safety proof for `TechnicalSnapshot`, mirroring
`test_replay_equivalence.py` (which proves this for a single primitive,
`calculate_ema`) at the aggregation level: running the full snapshot
assembly through the real replay path —
`InMemoryCandleRepository -> HistoricalProvider -> normalization ->
assemble_technical_snapshot` — must produce exactly the same result as
computing directly against a pre-bounded candle list. No new persistence
infrastructure is introduced; this reuses the existing in-memory repository
and `HistoricalProvider` exactly as they already exist.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from app.data.normalization.mock_normalizer import MockNormalizer
from app.data.providers.historical_provider import HistoricalProvider
from app.domain.market.models import Candle, ExchangeSegment, Timeframe
from app.domain.technical.series import IndicatorStatus
from app.domain.technical.snapshot import TechnicalSnapshotPeriods, assemble_technical_snapshot
from app.persistence.in_memory import InMemoryCandleRepository, InMemoryOptionChainRepository
from tests.unit.technical.factories import make_series_from_closes

T0 = datetime(2026, 8, 27, 9, 15, tzinfo=UTC)
INSTRUMENT_ID = "INST1"

PERIODS = TechnicalSnapshotPeriods(
    ema_period=3,
    rsi_period=2,
    macd_fast_period=2,
    macd_slow_period=3,
    macd_signal_period=2,
    atr_period=2,
    relative_volume_period=2,
    swing_left_bars=1,
    swing_right_bars=1,
)


def test_technical_snapshot_replay_path_matches_direct_bounded_computation() -> None:
    closes = [100, 101, 99, 102, 103, 101, 104, 105, 103, 106]
    future_closes = [1, 999, 1, 999]  # must never leak into the replay result

    all_candles = make_series_from_closes(closes + future_closes, start=T0, instrument_id=INSTRUMENT_ID)
    through_t = all_candles[: len(closes)]
    as_of = through_t[-1].freshness.data_timestamp

    # Direct path: assemble the snapshot against the pre-bounded list.
    direct_result = assemble_technical_snapshot(
        through_t, instrument_id=INSTRUMENT_ID, timeframe=Timeframe.M5, as_of=as_of, periods=PERIODS
    )

    # Replay path: persist EVERYTHING (future candles included), then read
    # back through HistoricalProvider + normalization, exactly as a real
    # replay run would, and assemble the snapshot from that.
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
    replay_result = assemble_technical_snapshot(
        replayed_candles, instrument_id=INSTRUMENT_ID, timeframe=Timeframe.M5, as_of=as_of, periods=PERIODS
    )

    assert replay_result == direct_result
    assert replay_result.candles_available == direct_result.candles_available == len(closes)
    assert replay_result.ema.status == IndicatorStatus.OK
    assert replay_result.macd.status == IndicatorStatus.OK
