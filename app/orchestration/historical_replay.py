"""Phase 3 (Historical Intelligence + Early Opportunity Validation) --
the actual historical REPLAY mechanism: run the UNMODIFIED intelligence
engine (`run_analysis()` -> `analyze_symbol()`, exactly the same path a
manual query or the live daily researcher uses) against a chosen past
instant, using ONLY locally persisted historical candles -- never a live
call, never a future candle.

    historical date/session
            |
    walk each real M15 bar's own close as `as_of`
            |
    HistoricalReplayProvider (local candles only, as_of-bounded)
            |
    run_analysis() -- SAME pipeline as live
            |
    collect_gated_candidates() / build_research_thesis() /
    build_research_observation() -- SAME functions `daily_research.py`'s
    live shortlist already uses, called here for one symbol at a time
            |
    ResearchObservation(source="REPLAY")

This module duplicates NO evidence/classification logic -- see the
docstring of `app.orchestration.options_intelligence_pipeline
.AnalysisProvider` for the "one intelligence engine, two inputs"
architecture this implements. Every observation this produces is written
to its OWN, separate repository (never the live `research_observations
.jsonl`) -- Section 41: the live GREEN system's own data must never be
touched by a replay run.

NO LOOKAHEAD: each bar's `as_of` is that bar's own real timestamp; the
pipeline's every downstream fetch is bounded to `as_of` by
`HistoricalReplayProvider`/`CandleRepository.query()` (`data_timestamp <=
as_of`, unconditionally -- `persistence/interfaces.py`'s own contract).
The bar list itself is read once, upfront, purely to know which real
timestamps to iterate over (never to peek at their OHLC values before
their own turn) -- the exact same structure already proven by
`scripts/replay_core.run_replay()` (`visible = candles[:i+1]`) and its own
`tests/scripts/test_real_data_replay.py`.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from app.data.providers.historical_replay_provider import HistoricalReplayProvider
from app.data.providers.upstox_instrument_master import resolve_symbol
from app.domain.audit.research_models import ResearchObservation
from app.domain.market.models import Timeframe
from app.domain.market.trading_calendar import is_trading_day, next_trading_day
from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy
from app.orchestration.daily_research import (
    build_price_only_observation,
    build_research_observation,
    build_research_thesis,
    collect_gated_candidates,
)
from app.orchestration.dashboard_service import run_analysis
from app.orchestration.options_intelligence_pipeline import PipelineConfig, Repositories
from app.persistence.caching import CachedCandleRepository
from app.persistence.interfaces import CandleRepository
from app.utils.time import IST, ensure_utc

# The real, fixed NSE cash-session window -- same fact documented (and
# duplicated for the same reason: no private cross-module import) in
# `app.data.providers.historical_replay_provider`.
_SESSION_OPEN = time(9, 15)
_SESSION_CLOSE = time(15, 30)

COVERAGE_CLASSIFICATION_REPLAY = "REPLAY"

# 95% sprint, Sprint 2b -- the REAL replay performance bottleneck,
# found by profiling (not guessed): `PipelineConfig`'s default retry
# settings (`chain_fetch_attempts=3`/`chain_fetch_backoff_seconds=1.0`,
# `underlying_quote_fetch_attempts=2`/`...backoff_seconds=1.0`) exist to
# ride out a REAL live provider's transient network blips
# (`options_intelligence_pipeline._retry()` retries on
# `ProviderTimeout`/`ProviderUnavailable`/`ProviderRateLimited`). A
# `HistoricalReplayProvider`'s `ProviderUnavailable` is never transient
# -- it is a permanent, structural fact about this instant (no chain
# will ever exist for it) -- so those same retries just sleep
# `backoff_seconds` between guaranteed-to-fail attempts, for zero
# benefit, on EVERY chain-fetch call (the main one, PLUS one per
# comparable expiry in the term-structure stage). Profiled: this
# accounted for the overwhelming majority of the ~3s/bar measured before
# this fix (cProfile showed 3.0 of 3.2s in the asyncio event loop's I/O
# wait, i.e. `asyncio.sleep()` inside `_retry()`'s backoff -- NOT actual
# computation, and NOT `JsonlCandleRepository` file re-reads, which the
# Sprint 2b `CachedCandleRepository` fix above addresses separately and
# remains worth keeping for a larger local candle store). `attempts=1`
# means "try once, fail fast" -- this changes NOTHING about what a
# replay run can determine (a permanently-unavailable stream stays
# unavailable either way), only how much real wall-clock time is spent
# finding that out. A caller-supplied `config` always overrides this.
_DEFAULT_REPLAY_CONFIG = PipelineConfig(chain_fetch_attempts=1, underlying_quote_fetch_attempts=1)


@dataclass(frozen=True)
class ReplaySessionResult:
    """One session's real replay outcome -- `bars_evaluated` is the count
    of real local M15 bars the replay actually walked (0 when the local
    candle store has no data for this session at all, honestly distinct
    from "walked every bar and found nothing interesting")."""

    symbol: str
    session_date: date
    run_id: str
    bars_evaluated: int
    observations: list[ResearchObservation] = field(default_factory=list)


def _session_bounds_utc(session_date: date) -> tuple[datetime, datetime]:
    start = datetime.combine(session_date, _SESSION_OPEN, tzinfo=IST)
    end = datetime.combine(session_date, _SESSION_CLOSE, tzinfo=IST) + timedelta(minutes=1)
    return ensure_utc(start), ensure_utc(end)


async def replay_symbol_session(
    symbol: str, session_date: date, *,
    candle_repository: CandleRepository,
    instrument_master: Sequence[dict[str, object]],
    repositories: Repositories,
    strategy: EMAVWAPAlignmentStrategy | None = None,
    config: PipelineConfig | None = None,
    mcx_instrument_master: Sequence[dict[str, object]] | None = None,
    run_id: str | None = None,
) -> ReplaySessionResult:
    """Section 14/15 -- single-symbol, single-session replay: walk
    `session_date`'s own real, locally persisted M15 bars one at a time
    and record a `ResearchObservation` whenever that instant's
    classification genuinely clears the same structural gate a live
    shortlist entry must clear (`_gate()`/`collect_gated_candidates()`,
    reused verbatim from `app.orchestration.daily_research` -- never
    duplicated, never a bucket/pattern filter invented on top of it, the
    same criterion `run_daily_research()`'s own live shortlist uses).

    Raises `ValueError` for a non-trading `session_date` (there is no
    session to replay) or an unresolvable `symbol` -- caller-ordering
    bugs, never silently skipped.
    """
    if not is_trading_day(session_date):
        raise ValueError(f"{session_date.isoformat()} is not an NSE trading day -- nothing to replay")
    ref = resolve_symbol(instrument_master, symbol)
    if ref is None:
        raise ValueError(f"{symbol!r} not found in the real Upstox instrument master")

    strategy = strategy or EMAVWAPAlignmentStrategy()
    config = config or _DEFAULT_REPLAY_CONFIG
    run_id = run_id or uuid.uuid4().hex
    # 95% sprint, Sprint 2b -- one session walks dozens of real bars, and
    # every bar's analyze_symbol() call re-queries this same symbol's
    # candles at least twice; without this, `JsonlCandleRepository`
    # re-reads and re-parses its ENTIRE backing file on every single one
    # of those calls (measured: ~3s/bar). Wrapping it here reads the
    # backing store once for this session and serves the rest from
    # memory -- no-lookahead is unaffected (`CachedCandleRepository`
    # applies the exact same `as_of` filtering per call; see its own
    # docstring). Never persisted or shared beyond this one call.
    cached_candles = CachedCandleRepository(candle_repository)
    provider = HistoricalReplayProvider(candles=cached_candles)

    session_start, session_end = _session_bounds_utc(session_date)
    session_bars = await cached_candles.query(
        instrument_id=ref.instrument_key, timeframe=Timeframe.M15, start=session_start, end=session_end, as_of=session_end,
    )

    observations: list[ResearchObservation] = []
    for bar in session_bars:
        as_of = bar.freshness.data_timestamp
        provider.advance_to(as_of)
        response = await run_analysis(
            symbol, provider=provider, instrument_master=instrument_master, strategy=strategy,
            repositories=repositories, config=config, as_of=as_of, mcx_instrument_master=mcx_instrument_master,
        )
        # Try the SAME contract-selecting gate the live shortlist uses
        # first (Phase 3 gap-closure never weakens this path) -- it will
        # succeed whenever real historical chain data genuinely exists
        # (e.g. a session this system already captured live). Only when
        # that gate finds nothing does the price-only gate run, and ONLY
        # because no chain evidence exists to select a contract from --
        # see `build_price_only_observation()`'s own docstring for
        # exactly what it does and does not relax.
        candidates = collect_gated_candidates({symbol: response})
        if candidates:
            candidate = candidates[0]
            thesis = build_research_thesis(candidate)
            observation = build_research_observation(
                candidate, thesis, run_id=run_id, coverage_classification=COVERAGE_CLASSIFICATION_REPLAY,
            )
            observations.append(observation.model_copy(update={"source": "REPLAY"}))
            continue
        price_only = build_price_only_observation(
            symbol, response, run_id=run_id, coverage_classification=COVERAGE_CLASSIFICATION_REPLAY,
        )
        if price_only is not None:
            observations.append(price_only)

    return ReplaySessionResult(
        symbol=symbol, session_date=session_date, run_id=run_id, bars_evaluated=len(session_bars), observations=observations,
    )


@dataclass(frozen=True)
class ReplayWindowResult:
    symbol: str
    start_date: date
    end_date: date
    session_results: list[ReplaySessionResult] = field(default_factory=list)

    @property
    def observations(self) -> list[ResearchObservation]:
        return [o for r in self.session_results for o in r.observations]


async def replay_symbol_window(
    symbol: str, start_date: date, end_date: date, *,
    candle_repository: CandleRepository,
    instrument_master: Sequence[dict[str, object]],
    repositories: Repositories,
    strategy: EMAVWAPAlignmentStrategy | None = None,
    config: PipelineConfig | None = None,
    mcx_instrument_master: Sequence[dict[str, object]] | None = None,
    run_id: str | None = None,
) -> ReplayWindowResult:
    """Section 15 -- "selected historical window" mode: `replay_symbol_session()`
    once per real trading session in `[start_date, end_date]`, inclusive.
    Deliberately NOT whole-universe (Section 15: "do NOT implement the
    expensive whole-universe historical replay first") -- one controlled
    symbol, a controlled date range.
    """
    if end_date < start_date:
        raise ValueError(f"end_date {end_date.isoformat()} is before start_date {start_date.isoformat()}")
    run_id = run_id or uuid.uuid4().hex

    sessions: list[date] = []
    if is_trading_day(start_date):
        sessions.append(start_date)
    cursor = start_date
    while True:
        cursor = next_trading_day(cursor)
        if cursor > end_date:
            break
        sessions.append(cursor)

    results = [
        await replay_symbol_session(
            symbol, session_date, candle_repository=candle_repository, instrument_master=instrument_master,
            repositories=repositories, strategy=strategy, config=config, mcx_instrument_master=mcx_instrument_master,
            run_id=run_id,
        )
        for session_date in sessions
    ]
    return ReplayWindowResult(symbol=symbol, start_date=start_date, end_date=end_date, session_results=results)
