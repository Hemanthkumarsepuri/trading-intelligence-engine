"""Phase 3 -- historical replay orchestration integration tests: runs the
REAL `run_analysis()` -> `analyze_symbol()` path (no HTTP, no live
provider) against locally persisted historical M15 candles.

Gap-closure (see `docs/HISTORICAL_REPLAY.md`): `analyze_symbol()` no
longer treats a structurally-unavailable historical option chain as
fatal (`HistoricalReplayProvider.get_chain()` still always honestly
raises `ProviderUnavailable` -- Upstox has no historical option-chain
endpoint -- but the pipeline now degrades to price-only evidence instead
of aborting), and `build_price_only_observation()` can produce a real
`ResearchObservation` from that price-only evidence when a genuine,
named development pattern is present. A replay session over genuinely
FLAT, non-directional synthetic data still correctly produces ZERO
observations (there is no defensible direction to observe) --
`test_flat_synthetic_data_produces_no_observations` pins that honest
negative result explicitly, and
`test_real_historical_data_produces_genuine_price_only_observations`
(using the real sample RELIANCE CSV already in this repo) proves the
positive case actually works end to end.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.market.freshness import DataFreshness
from app.domain.market.models import Candle, Timeframe
from app.orchestration.historical_replay import (
    replay_symbol_session,
    replay_symbol_window,
)
from app.orchestration.options_intelligence_pipeline import PipelineConfig, Repositories
from app.persistence.jsonl_file import (
    JsonlCandleRepository,
    JsonlIvObservationRepository,
    JsonlOptionChainRepository,
    JsonlQuoteRepository,
)
from scripts.csv_candle_loader import load_candles_from_csv

_INSTRUMENT_KEY = "NSE_EQ|INE002A01018"
_MASTER: list[dict[str, object]] = [
    {"segment": "NSE_EQ", "name": "RELIANCE INDUSTRIES", "exchange": "NSE", "instrument_type": "EQ", "instrument_key": _INSTRUMENT_KEY, "trading_symbol": "RELIANCE"},
    {"segment": "NSE_FO", "underlying_symbol": "RELIANCE", "instrument_type": "CE", "expiry": int(datetime(2026, 9, 24, tzinfo=UTC).timestamp() * 1000), "weekly": False, "lot_size": 500, "instrument_key": "NSE_FO|R1", "strike_price": 1300.0},
    {"segment": "NSE_FO", "underlying_symbol": "RELIANCE", "instrument_type": "PE", "expiry": int(datetime(2026, 9, 24, tzinfo=UTC).timestamp() * 1000), "weekly": False, "lot_size": 500, "instrument_key": "NSE_FO|R2", "strike_price": 1300.0},
    {"segment": "NSE_FO", "underlying_symbol": "RELIANCE", "instrument_type": "FUT", "expiry": int(datetime(2026, 9, 24, tzinfo=UTC).timestamp() * 1000), "weekly": False, "lot_size": 500, "instrument_key": "NSE_FO|R3"},
]

_SESSION_START = datetime(2026, 8, 27, 3, 45, tzinfo=UTC)  # 09:15 IST, a real Thursday


def _session_candles(session_start: datetime, *, sessions_back: int = 15, price: float = 1300.0) -> list[Candle]:
    """A realistic multi-session M15 series ending at `session_start`'s own
    session close, so the pipeline's `history_lookback` (10 calendar days)
    has enough real prior sessions to clear INSUFFICIENT_HISTORY."""
    candles: list[Candle] = []
    for day_offset in range(sessions_back, -1, -1):
        day_start = session_start - timedelta(days=day_offset)
        for bar in range(26):  # 09:15 to 15:30 inclusive-ish, 15-min bars
            ts = day_start + timedelta(minutes=15 * bar)
            candles.append(Candle(
                provider="test", freshness=DataFreshness(data_timestamp=ts, received_timestamp=ts),
                instrument_id=_INSTRUMENT_KEY, timeframe=Timeframe.M15,
                open=Decimal(str(price)), high=Decimal(str(price + 2)), low=Decimal(str(price - 2)), close=Decimal(str(price)),
                volume=10000,
            ))
    return candles


def _repositories(tmp_path: Path) -> Repositories:
    return Repositories(
        quotes=JsonlQuoteRepository(tmp_path / "quotes.jsonl"),
        option_chains=JsonlOptionChainRepository(tmp_path / "chains.jsonl"),
        iv_observations=JsonlIvObservationRepository(tmp_path / "iv.jsonl"),
    )


def _seed(tmp_path: Path, candles: list[Candle]) -> JsonlCandleRepository:
    repo = JsonlCandleRepository(tmp_path / "candles.jsonl")

    async def _save_all() -> None:
        for c in candles:
            await repo.save(c)

    asyncio.run(_save_all())
    return repo


def test_non_trading_day_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not an NSE trading day"):
        asyncio.run(replay_symbol_session(
            "RELIANCE", date(2026, 8, 30), candle_repository=_seed(tmp_path, []),
            instrument_master=_MASTER, repositories=_repositories(tmp_path),
        ))


def test_unresolvable_symbol_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not found in the real Upstox instrument master"):
        asyncio.run(replay_symbol_session(
            "NOT_A_REAL_SYMBOL", date(2026, 8, 27), candle_repository=_seed(tmp_path, []),
            instrument_master=_MASTER, repositories=_repositories(tmp_path),
        ))


def test_walks_every_real_local_bar_in_the_session(tmp_path: Path) -> None:
    candles = _session_candles(_SESSION_START)
    repo = _seed(tmp_path, candles)
    result = asyncio.run(replay_symbol_session(
        "RELIANCE", date(2026, 8, 27), candle_repository=repo, instrument_master=_MASTER,
        repositories=_repositories(tmp_path), config=PipelineConfig(),
    ))
    assert result.symbol == "RELIANCE"
    assert result.session_date == date(2026, 8, 27)
    assert result.bars_evaluated == 26


def test_flat_synthetic_data_produces_no_observations(tmp_path: Path) -> None:
    """The honest negative result: perfectly flat, non-directional
    synthetic price data has no defensible convergence
    (`v.convergence` is neither `CONVERGENCE_BULLISH` nor
    `CONVERGENCE_BEARISH`), so `build_price_only_observation()` correctly
    declines -- this is NOT the old "chain-fetch-is-fatal" limitation
    (fixed, see this file's own module docstring); it is simply that
    flat data has nothing to observe."""
    candles = _session_candles(_SESSION_START)
    repo = _seed(tmp_path, candles)
    result = asyncio.run(replay_symbol_session(
        "RELIANCE", date(2026, 8, 27), candle_repository=repo, instrument_master=_MASTER,
        repositories=_repositories(tmp_path), config=PipelineConfig(),
    ))
    assert result.observations == []


def test_real_historical_data_produces_genuine_price_only_observations(tmp_path: Path) -> None:
    """The positive case, end to end, against the real sample RELIANCE
    M15 CSV already in this repo (2026-07-29 .. 2026-08-27) -- proves the
    gap-closure fix actually works, not just that it doesn't crash. Every
    observation must be honestly price-only: no contract, no fabricated
    derivatives confirmation."""
    csv_path = Path(__file__).resolve().parents[3] / "data" / "historical_replay" / "RELIANCE_M15.csv"
    real_candles = load_candles_from_csv(csv_path, instrument_id=_INSTRUMENT_KEY)
    repo = _seed(tmp_path, real_candles)
    result = asyncio.run(replay_symbol_session(
        "RELIANCE", date(2026, 8, 25), candle_repository=repo, instrument_master=_MASTER,
        repositories=_repositories(tmp_path), config=PipelineConfig(),
    ))
    assert result.observations, "expected at least one real price-only observation from the real sample session"
    for obs in result.observations:
        assert obs.source == "REPLAY"
        assert obs.derivatives_evidence_available is False
        assert obs.selected_right is None
        assert obs.selected_strike is None
        assert obs.contractual_expiry_breakeven is None
        assert obs.direction in ("BULLISH", "BEARISH")
        assert obs.missing_evidence is not None
        assert "unavailable" in obs.missing_evidence.lower()


def test_replay_is_reproducible(tmp_path: Path) -> None:
    candles = _session_candles(_SESSION_START)
    repo = _seed(tmp_path, candles)
    repositories = _repositories(tmp_path)
    first = asyncio.run(replay_symbol_session(
        "RELIANCE", date(2026, 8, 27), candle_repository=repo, instrument_master=_MASTER,
        repositories=repositories, config=PipelineConfig(),
    ))
    second = asyncio.run(replay_symbol_session(
        "RELIANCE", date(2026, 8, 27), candle_repository=repo, instrument_master=_MASTER,
        repositories=repositories, config=PipelineConfig(),
    ))
    assert first.bars_evaluated == second.bars_evaluated
    assert len(first.observations) == len(second.observations)


def test_window_replay_covers_every_real_trading_session_in_range(tmp_path: Path) -> None:
    # A Thursday through the following Monday (skips the weekend).
    candles = _session_candles(_SESSION_START, sessions_back=20)
    repo = _seed(tmp_path, candles)
    result = asyncio.run(replay_symbol_window(
        "RELIANCE", date(2026, 8, 27), date(2026, 8, 31), candle_repository=repo, instrument_master=_MASTER,
        repositories=_repositories(tmp_path), config=PipelineConfig(),
    ))
    assert [r.session_date for r in result.session_results] == [date(2026, 8, 27), date(2026, 8, 28), date(2026, 8, 31)]


def test_window_replay_rejects_an_inverted_range(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="is before start_date"):
        asyncio.run(replay_symbol_window(
            "RELIANCE", date(2026, 8, 27), date(2026, 8, 20), candle_repository=_seed(tmp_path, []),
            instrument_master=_MASTER, repositories=_repositories(tmp_path),
        ))
