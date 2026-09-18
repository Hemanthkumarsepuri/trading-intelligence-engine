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
from app.orchestration.options_intelligence_pipeline import Repositories
from app.orchestration.outcome_horizons import (
    InvalidationOutcome,
    OutcomeHorizonLabel,
    compute_price_path_outcome,
)
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
        repositories=_repositories(tmp_path), config=None,
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
        repositories=_repositories(tmp_path), config=None,
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
        repositories=_repositories(tmp_path), config=None,
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
        repositories=repositories, config=None,
    ))
    second = asyncio.run(replay_symbol_session(
        "RELIANCE", date(2026, 8, 27), candle_repository=repo, instrument_master=_MASTER,
        repositories=repositories, config=None,
    ))
    assert first.bars_evaluated == second.bars_evaluated
    assert len(first.observations) == len(second.observations)


def test_window_replay_covers_every_real_trading_session_in_range(tmp_path: Path) -> None:
    # A Thursday through the following Monday (skips the weekend).
    candles = _session_candles(_SESSION_START, sessions_back=20)
    repo = _seed(tmp_path, candles)
    result = asyncio.run(replay_symbol_window(
        "RELIANCE", date(2026, 8, 27), date(2026, 8, 31), candle_repository=repo, instrument_master=_MASTER,
        repositories=_repositories(tmp_path), config=None,
    ))
    assert [r.session_date for r in result.session_results] == [date(2026, 8, 27), date(2026, 8, 28), date(2026, 8, 31)]


def test_window_replay_rejects_an_inverted_range(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="is before start_date"):
        asyncio.run(replay_symbol_window(
            "RELIANCE", date(2026, 8, 27), date(2026, 8, 20), candle_repository=_seed(tmp_path, []),
            instrument_master=_MASTER, repositories=_repositories(tmp_path),
        ))


def _reclaim_candles(session_start: datetime) -> list[Candle]:
    """A real failed-breakdown-and-reclaim shape, expressed as genuine
    M15 bars: eight prior sessions building a ~1290 floor, one session
    that breaks it to 1265, a session that closes back above it, and the
    replay session itself trading above the reclaimed level. Built here
    (rather than reusing `_session_candles`' flat series) because the
    pattern this exercises is, by definition, a specific price
    STRUCTURE -- flat data cannot produce it."""
    shapes: list[tuple[float, float, float, float]] = [
        (1300.0, 1310.0, 1292.0, 1305.0),
        (1305.0, 1312.0, 1291.0, 1300.0),
        (1300.0, 1308.0, 1290.0, 1296.0),
        (1296.0, 1304.0, 1290.5, 1299.0),
        (1299.0, 1306.0, 1291.0, 1302.0),
        (1302.0, 1305.0, 1265.0, 1270.0),   # the break: 1290 floor lost by ~1.9%
        (1270.0, 1300.0, 1268.0, 1298.0),   # reclaimed: closes back above 1290
        (1298.0, 1316.0, 1296.0, 1314.0),   # held, and pushing up
    ]
    candles: list[Candle] = []
    day_offsets = [11, 10, 7, 6, 5, 4, 3, 2]  # real weekdays before the replay session
    for offset, (o, h, low, c) in zip(day_offsets, shapes, strict=True):
        day_start = session_start - timedelta(days=offset)
        legs = [(o, o, o), (h, h, h), (low, low, low), (c, c, c)]
        for index, (bo, bh, bl) in enumerate(legs):
            ts = day_start + timedelta(minutes=15 * index)
            candles.append(Candle(
                provider="test", freshness=DataFreshness(data_timestamp=ts, received_timestamp=ts),
                instrument_id=_INSTRUMENT_KEY, timeframe=Timeframe.M15,
                open=Decimal(str(bo)), high=Decimal(str(bh)), low=Decimal(str(bl)), close=Decimal(str(bl)),
                volume=20000,
            ))
        for index in range(4, 26):
            ts = day_start + timedelta(minutes=15 * index)
            candles.append(Candle(
                provider="test", freshness=DataFreshness(data_timestamp=ts, received_timestamp=ts),
                instrument_id=_INSTRUMENT_KEY, timeframe=Timeframe.M15,
                open=Decimal(str(c)), high=Decimal(str(c)), low=Decimal(str(c)), close=Decimal(str(c)),
                volume=20000,
            ))
    # The replay session itself: a steady advance above the reclaimed level.
    for bar in range(26):
        ts = session_start + timedelta(minutes=15 * bar)
        price = 1316.0 + bar * 0.6
        candles.append(Candle(
            provider="test", freshness=DataFreshness(data_timestamp=ts, received_timestamp=ts),
            instrument_id=_INSTRUMENT_KEY, timeframe=Timeframe.M15,
            open=Decimal(str(price)), high=Decimal(str(price + 1)), low=Decimal(str(price - 1)), close=Decimal(str(price)),
            volume=20000,
        ))
    return candles


def test_failed_breakdown_reclaim_is_reachable_end_to_end(tmp_path: Path) -> None:
    """The second named pattern, through the REAL pipeline.

    `classify_development()` has always been able to narrate
    FAILED_BREAKDOWN_RECLAIM, but nothing ever computed the fact behind
    it -- `failed_breakdown_reclaim` had no production caller, so the
    pattern was unreachable in both the live path and replay (which is
    why a 2,610-episode replay dataset contained exactly one pattern).
    This pins the whole chain: real candles -> `detect_structural_reclaim()`
    -> pipeline -> `classify_development()` -> a real `ResearchObservation`
    carrying the reclaimed level as its invalidation level."""
    repo = _seed(tmp_path, _reclaim_candles(_SESSION_START))
    result = asyncio.run(replay_symbol_session(
        "RELIANCE", date(2026, 8, 27), candle_repository=repo, instrument_master=_MASTER,
        repositories=_repositories(tmp_path), config=None,
    ))
    reclaims = [o for o in result.observations if o.pattern == "FAILED_BREAKDOWN_RECLAIM"]
    assert reclaims, (
        "expected at least one FAILED_BREAKDOWN_RECLAIM observation from a real "
        f"break-and-reclaim series; got patterns {[o.pattern for o in result.observations]}"
    )
    for obs in reclaims:
        assert obs.direction == "BULLISH"
        # The invalidation level is the RECLAIMED level itself -- the real
        # 1290 floor that was broken and taken back, not a nearby swing.
        assert obs.invalidation_level_kind == "support"
        assert obs.invalidation_level_value == "1290.0"
        assert obs.derivatives_evidence_available is False


def test_a_reclaim_observation_can_reach_a_determined_outcome(tmp_path: Path) -> None:
    """Research-integrity consequence of the level above: before it, every
    pattern except PRE_BREAKOUT_COMPRESSION had `invalidation_outcome`
    permanently UNKNOWN, so a reclaim episode could never be counted as
    FAILED_SETUP no matter what price did. With a real recorded level the
    determination becomes genuine -- here price loses the reclaimed floor,
    and the outcome says so."""
    candles = _reclaim_candles(_SESSION_START)
    repo = _seed(tmp_path, candles)
    result = asyncio.run(replay_symbol_session(
        "RELIANCE", date(2026, 8, 27), candle_repository=repo, instrument_master=_MASTER,
        repositories=_repositories(tmp_path), config=None,
    ))
    observation = next(o for o in result.observations if o.pattern == "FAILED_BREAKDOWN_RECLAIM")

    # A later session that breaks back below the reclaimed 1290 floor.
    after = list(candles)
    next_day = _SESSION_START + timedelta(days=1)
    for bar in range(26):
        ts = next_day + timedelta(minutes=15 * bar)
        after.append(Candle(
            provider="test", freshness=DataFreshness(data_timestamp=ts, received_timestamp=ts),
            instrument_id=_INSTRUMENT_KEY, timeframe=Timeframe.M15,
            open=Decimal("1285"), high=Decimal("1286"), low=Decimal("1280"), close=Decimal("1282"),
            volume=20000,
        ))
    outcome = compute_price_path_outcome(
        observation, OutcomeHorizonLabel.PLUS_1D, after,
        as_of=next_day + timedelta(days=2),
    )
    assert outcome.data_sufficient
    assert outcome.invalidation_outcome == InvalidationOutcome.INVALIDATED
    assert outcome.first_invalidation_at is not None
