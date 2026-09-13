"""Phase 3 -- `HistoricalReplayProvider` unit tests: real historical
candles in, honest degradation for everything Upstox cannot supply
historically, and no lookahead through the whole surface (Section 8).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.data.providers.exceptions import ProviderUnavailable
from app.data.providers.historical_replay_provider import (
    HistoricalReplayProvider,
    deterministic_exchange_status,
)
from app.data.providers.upstox_provider import ExchangeStatus
from app.domain.market.freshness import DataFreshness
from app.domain.market.models import Candle, ExchangeSegment, Timeframe
from app.persistence.jsonl_file import JsonlCandleRepository

_INSTRUMENT = "NSE_EQ|TEST"


def _candle(*, timestamp: datetime, close: str, volume: int = 1000) -> Candle:
    return Candle(
        provider="test", freshness=DataFreshness(data_timestamp=timestamp, received_timestamp=timestamp),
        instrument_id=_INSTRUMENT, timeframe=Timeframe.M15,
        open=Decimal(close), high=Decimal(close) + 1, low=Decimal(close) - 1, close=Decimal(close), volume=volume,
    )


def _seed_repo(path: Path, candles: list[Candle]) -> JsonlCandleRepository:
    repo = JsonlCandleRepository(path)

    async def _save_all() -> None:
        for c in candles:
            await repo.save(c)

    asyncio.run(_save_all())
    return repo


# ============================================================
# deterministic_exchange_status
# ============================================================


def test_status_is_open_during_session_hours_on_a_trading_day() -> None:
    # 2026-08-28 is a Friday; 10:00 IST = 04:30 UTC.
    as_of = datetime(2026, 8, 28, 4, 30, tzinfo=UTC)
    assert deterministic_exchange_status(as_of) == ExchangeStatus.NORMAL_OPEN


def test_status_is_closed_before_session_open() -> None:
    as_of = datetime(2026, 8, 28, 2, 0, tzinfo=UTC)  # 07:30 IST
    assert deterministic_exchange_status(as_of) == ExchangeStatus.NORMAL_CLOSE


def test_status_is_closed_after_session_close() -> None:
    as_of = datetime(2026, 8, 28, 11, 0, tzinfo=UTC)  # 16:30 IST
    assert deterministic_exchange_status(as_of) == ExchangeStatus.NORMAL_CLOSE


def test_status_is_closed_on_a_weekend() -> None:
    as_of = datetime(2026, 8, 29, 5, 0, tzinfo=UTC)  # Saturday, 10:30 IST
    assert deterministic_exchange_status(as_of) == ExchangeStatus.NORMAL_CLOSE


# ============================================================
# get_market_status -- requires advance_to() first
# ============================================================


def test_get_market_status_raises_before_advance_to_is_called(tmp_path: Path) -> None:
    provider = HistoricalReplayProvider(candles=JsonlCandleRepository(tmp_path / "c.jsonl"))
    with pytest.raises(RuntimeError):
        asyncio.run(provider.get_market_status(exchange="NSE"))


def test_get_market_status_reflects_the_replay_clock(tmp_path: Path) -> None:
    provider = HistoricalReplayProvider(candles=JsonlCandleRepository(tmp_path / "c.jsonl"))
    provider.advance_to(datetime(2026, 8, 28, 4, 30, tzinfo=UTC))
    status = asyncio.run(provider.get_market_status(exchange="NSE"))
    assert status == ExchangeStatus.NORMAL_OPEN


# ============================================================
# get_ohlcv / no lookahead
# ============================================================


def test_get_ohlcv_never_returns_a_candle_beyond_as_of(tmp_path: Path) -> None:
    t0 = datetime(2026, 8, 28, 3, 45, tzinfo=UTC)
    candles = [_candle(timestamp=t0 + timedelta(minutes=15 * i), close="100") for i in range(10)]
    repo = _seed_repo(tmp_path / "c.jsonl", candles)
    provider = HistoricalReplayProvider(candles=repo)

    as_of = t0 + timedelta(minutes=45)  # covers indices 0..3
    result = asyncio.run(provider.get_ohlcv(
        security_id=_INSTRUMENT, exchange_segment=ExchangeSegment.NSE_EQ, timeframe=Timeframe.M15,
        start=t0, end=t0 + timedelta(hours=3), as_of=as_of,
    ))
    assert all(c.timestamp <= as_of for c in result)
    assert len(result) == 4


# ============================================================
# get_quote -- reconstructed from the latest completed candle
# ============================================================


def test_get_quote_reconstructs_from_the_latest_candle_at_or_before_as_of(tmp_path: Path) -> None:
    t0 = datetime(2026, 8, 28, 3, 45, tzinfo=UTC)
    candles = [_candle(timestamp=t0 + timedelta(minutes=15 * i), close=str(100 + i)) for i in range(4)]
    repo = _seed_repo(tmp_path / "c.jsonl", candles)
    provider = HistoricalReplayProvider(candles=repo)

    as_of = t0 + timedelta(minutes=30)  # the 3rd candle (close=102) is the latest at/before this instant
    quote = asyncio.run(provider.get_quote(security_id=_INSTRUMENT, exchange_segment=ExchangeSegment.NSE_EQ, as_of=as_of))
    assert quote.last_price == 102.0
    assert quote.exchange_timestamp == t0 + timedelta(minutes=30)


def test_get_quote_raises_when_no_local_history_exists(tmp_path: Path) -> None:
    provider = HistoricalReplayProvider(candles=JsonlCandleRepository(tmp_path / "c.jsonl"))
    with pytest.raises(ProviderUnavailable):
        asyncio.run(provider.get_quote(security_id=_INSTRUMENT, exchange_segment=ExchangeSegment.NSE_EQ, as_of=datetime(2026, 8, 28, 5, 0, tzinfo=UTC)))


def test_get_quote_previous_close_comes_from_the_prior_session(tmp_path: Path) -> None:
    # Thursday 2026-08-27 session (last bar 15:15 IST) and Friday
    # 2026-08-28 session's first bar.
    thursday_close = datetime(2026, 8, 27, 9, 45, tzinfo=UTC)  # 15:15 IST
    friday_open = datetime(2026, 8, 28, 3, 45, tzinfo=UTC)  # 09:15 IST
    candles = [_candle(timestamp=thursday_close, close="200"), _candle(timestamp=friday_open, close="205")]
    repo = _seed_repo(tmp_path / "c.jsonl", candles)
    provider = HistoricalReplayProvider(candles=repo)

    quote = asyncio.run(provider.get_quote(security_id=_INSTRUMENT, exchange_segment=ExchangeSegment.NSE_EQ, as_of=friday_open))
    assert quote.last_price == 205.0
    assert quote.previous_close == 200.0


def test_get_quotes_batched_omits_symbols_with_no_local_history(tmp_path: Path) -> None:
    t0 = datetime(2026, 8, 28, 3, 45, tzinfo=UTC)
    repo = _seed_repo(tmp_path / "c.jsonl", [_candle(timestamp=t0, close="100")])
    provider = HistoricalReplayProvider(candles=repo)
    provider.advance_to(t0)

    result = asyncio.run(provider.get_quotes([_INSTRUMENT, "NSE_EQ|NO_HISTORY"]))
    assert set(result.keys()) == {_INSTRUMENT}


# ============================================================
# get_news / get_chain -- honestly unavailable, never fabricated
# ============================================================


def test_get_news_is_always_unavailable(tmp_path: Path) -> None:
    provider = HistoricalReplayProvider(candles=JsonlCandleRepository(tmp_path / "c.jsonl"))
    with pytest.raises(ProviderUnavailable):
        asyncio.run(provider.get_news(instrument_key=_INSTRUMENT))


def test_get_chain_is_always_unavailable(tmp_path: Path) -> None:
    provider = HistoricalReplayProvider(candles=JsonlCandleRepository(tmp_path / "c.jsonl"))
    with pytest.raises(ProviderUnavailable):
        asyncio.run(provider.get_chain(underlying=_INSTRUMENT, expiry=date(2026, 9, 24), as_of=datetime(2026, 8, 28, 5, 0, tzinfo=UTC)))
