"""The Options Intelligence pipeline — the end-to-end sequence:

    instrument resolution -> underlying quote+M15 history -> technical
    analysis (EMA/VWAP/RSI/ATR/regime) -> F&O eligibility -> expiry
    selection -> option chain fetch -> chain quality -> OI/PCR/ATM ->
    support/resistance -> IV context (+ persisted IV rank) -> futures ->
    evidence matrix -> candidates -> decision -> report

one real Upstox account, real data, one symbol at a time. Every stage's
wall-clock time is measured and reported separately (Phase 6's explicit
requirement) — this pipeline never claims "real-time" merely because its
own computation is fast; the REST latency is measured and shown as what it
is.

This is `orchestration/`'s job exactly as `watchlist_snapshot.py` and
`live_intelligence_session.py` already establish: sequence real provider
calls and hand the results to already-existing, unmodified `domain/`
functions. Nothing here reimplements EMA/VWAP/RSI/ATR/OI math, chain
parsing, or persistence — it only sequences and times them.
"""

from __future__ import annotations

import asyncio
import functools
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Protocol

import httpx
from pydantic import ValidationError

from app.data.normalization.base import DefaultNormalizer
from app.data.providers.base import (
    ProviderCapabilities,
    RawCandle,
    RawNewsItem,
    RawOptionChain,
    RawQuote,
)
from app.data.providers.exceptions import ProviderError
from app.data.providers.nse_delivery_archive import fetch_delivery_observation
from app.data.providers.upstox_fo_master import (
    futures_instrument_key,
    is_fo_eligible,
    nearest_expiry,
    nearest_futures_instrument_key,
    resolve_expiry_for_hint,
    select_relevant_expiries,
)
from app.data.providers.upstox_instrument_master import resolve_symbol
from app.data.providers.upstox_provider import ExchangeStatus
from app.domain.market.data_state import MarketDataState, classify_market_data_state
from app.domain.market.delivery_context import DeliveryFreshness, unknown_delivery
from app.domain.market.institutional_flows import (
    FlowAvailability,
    caller_supplied_institutional_flows,
    unknown_institutional_flows,
)
from app.domain.market.market_state import assemble_market_state
from app.domain.market.models import (
    Candle,
    ExchangeSegment,
    OptionChainSnapshot,
    OptionQuote,
    OptionRight,
    Quote,
    Timeframe,
)
from app.domain.market.price_consistency import classify_price_consistency
from app.domain.market.sample_breadth import SampleBreadthResult, classify_sample_breadth
from app.domain.market.trading_calendar import (
    is_trading_day,
    most_recent_trading_day_at_or_before,
)
from app.domain.news.event_classification import classify_news_event, classify_news_recency
from app.domain.news.geopolitical_transmission import build_transmission_note
from app.domain.news.models import build_news_item, deduplicate_news_items, filter_news_no_lookahead
from app.domain.options.adversarial_analysis import build_adversarial_analysis
from app.domain.options.anomaly_detection import detect_anomaly
from app.domain.options.candidate_engine import generate_candidates
from app.domain.options.chain_analysis import atm_strike, chain_totals
from app.domain.options.contract_analysis import compare_contracts
from app.domain.options.data_quality import check_chain_quality
from app.domain.options.decay_engine import attribute_decay
from app.domain.options.decay_viability import assess_decay_viability
from app.domain.options.decision_engine import (
    build_quality_assessment,
    decide,
    derive_research_state,
    market_bias_from_convergence,
    summarize_quality_tiers,
)
from app.domain.options.development import classify_development
from app.domain.options.direction_analysis import build_direction_comparison
from app.domain.options.evidence_availability import assess_evidence_availability
from app.domain.options.evidence_matrix import (
    EvidenceDirection,
    EvidenceMatrix,
    row_change_in_oi,
    row_data_freshness,
    row_futures_oi,
    row_global_context,
    row_iv_level,
    row_iv_skew,
    row_liquidity,
    row_m15_trend,
    row_market_regime,
    row_news,
    row_pcr_change,
    row_put_call_oi_structure,
    row_relative_strength,
    row_resistance,
    row_spot_futures_basis,
    row_support,
    row_volume,
    row_vwap,
    withhold_stale_stream_rows,
)
from app.domain.options.freshness_label import (
    DataStream,
    FreshnessLabel,
    StreamFreshness,
    classify_freshness_label,
    stream_freshness,
)
from app.domain.options.global_context import GlobalContextVerdict, assess_global_context
from app.domain.options.historical_structure import (
    HistoricalStructureStatus,
    assess_historical_structure,
    spot_near_levels,
)
from app.domain.options.iv_context import (
    AtmIvSummary,
    atm_iv_summary,
    classify_iv_trend,
    compute_iv_rank,
)
from app.domain.options.liquidity import LiquidityGrade, assess_liquidity
from app.domain.options.market_regime import classify_market_regime
from app.domain.options.market_regime_context import classify_macro_regime
from app.domain.options.models import ChainQualityIssueKind, ChainTotals, IvObservation
from app.domain.options.oi_migration import analyze_oi_migration
from app.domain.options.price_oi_interpretation import classify_basis_change, classify_price_oi
from app.domain.options.realized_volatility import (
    classify_iv_vs_realized,
    compute_realized_volatility,
)
from app.domain.options.research_blocker import determine_blockers
from app.domain.options.sector_strength import (
    classify_sector,
    classify_sector_relative_strength,
    sector_index_trading_symbol,
)
from app.domain.options.structural_reclaim import (
    StructuralReclaimStatus,
    detect_structural_reclaim,
)
from app.domain.options.support_resistance import (
    Level,
    assess_level_stability,
    classify_level_confluence,
    support_resistance_levels,
    technical_price_levels,
    vwap_ema_levels,
)
from app.domain.options.temporal_evidence import compute_temporal_observation
from app.domain.options.term_structure import (
    TermStructure,
    build_expiry_selection_note,
    build_expiry_summary,
)
from app.domain.strategy.current_analysis import assemble_current_analysis
from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy
from app.domain.technical.momentum import calculate_rsi
from app.domain.technical.series import IndicatorStatus
from app.domain.technical.volatility import calculate_atr
from app.domain.technical.vwap_position import compute_session_vwap_position
from app.orchestration.options_intelligence_report import (
    NewsEventNote,
    OptionsIntelligenceReport,
    StageLatency,
)
from app.persistence.interfaces import (
    IvObservationRepository,
    OptionChainRepository,
    QuoteRepository,
)


class AnalysisProvider(Protocol):
    """Phase 3 (Historical Intelligence + Early Opportunity Validation) --
    the exact, minimal set of calls `analyze_symbol()`/`run_analysis()`
    make on `provider`, gathered into one structural Protocol so the SAME
    pipeline can run against either the live `UpstoxProvider` or a
    `HistoricalReplayProvider` backed by locally persisted historical
    candles (see `app.data.providers.historical_replay_provider`) --
    "one intelligence engine, two inputs," never a second analysis path.

    `UpstoxProvider` already implements every method below; this is a pure
    typing-level abstraction (structural/duck typing via `Protocol`), not a
    new runtime wrapper, and changes no live behavior. A replay provider
    honestly raises `ProviderError` from `get_news()`/`get_chain()` when no
    genuine historical archive exists for that stream (see that module's
    own docstring) -- `analyze_symbol()` already tolerates a
    `ProviderError` from any one of these stages without failing the whole
    analysis.
    """

    name: str
    # Phase 3 gap-closure -- what this provider genuinely supports (see
    # `ProviderCapabilities`'s own docstring). `analyze_symbol()` reads
    # this to decide whether a missing option-chain/futures/news stream is
    # a structural, expected absence (degrade honestly) or a real live
    # failure (stays fatal, unchanged).
    capabilities: ProviderCapabilities

    async def get_market_status(self, *, exchange: str) -> ExchangeStatus: ...

    async def get_quote(self, *, security_id: str, exchange_segment: ExchangeSegment, as_of: datetime) -> RawQuote: ...

    async def get_ohlcv(
        self, *, security_id: str, exchange_segment: ExchangeSegment, timeframe: Timeframe,
        start: datetime, end: datetime, as_of: datetime,
    ) -> list[RawCandle]: ...

    async def get_news(self, *, instrument_key: str) -> list[RawNewsItem]: ...

    async def get_chain(self, *, underlying: str, expiry: date, as_of: datetime) -> RawOptionChain: ...

    async def get_quotes(self, security_ids: list[str]) -> dict[str, RawQuote]: ...


@dataclass(frozen=True)
class PipelineConfig:
    """Every threshold below with no single objectively-correct value is
    an explicit, named field here — visible, overridable, and documented
    at its point of use in the module it belongs to (`chain_analysis.py`,
    `liquidity.py`, `market_regime.py`, etc.) — never a silent constant
    buried in this orchestration function.
    """

    history_lookback: timedelta = timedelta(days=10)
    underlying_stale_threshold: timedelta = timedelta(minutes=5)
    strikes_each_side: int = 3
    min_candidate_liquidity_grade: LiquidityGrade = LiquidityGrade.GOOD
    min_iv_rank_observations: int = 5
    iv_trend_meaningful_change_points: Decimal = Decimal("1.0")
    iv_rank_lookback: timedelta = timedelta(days=365)
    near_level_pct_threshold: Decimal = Decimal("2.0")
    min_meaningful_option_volume: int = 1000
    option_price_flat_threshold: Decimal = Decimal("0.5")  # absolute points on the option's own premium
    futures_price_flat_threshold: Decimal = Decimal("0.5")  # absolute points on the futures LTP
    # Final Hardening Pass, Phase 6 -- see price_oi_interpretation.
    # classify_basis_change()'s own docstring. Percentage-POINTS on the
    # basis itself (not the underlying price), so a smaller number than
    # the price-based thresholds above is correct.
    meaningful_basis_change_pct: Decimal = Decimal("0.2")
    meaningful_iv_skew: Decimal = Decimal("1.0")  # IV points
    # Sprint 6 -- underlying-price consistency thresholds (see
    # app.domain.market.price_consistency's own docstring for the full
    # trace/rationale). Engineering heuristics, not fitted.
    max_consistent_price_diff_pct: Decimal = Decimal("0.5")
    max_partially_aligned_price_diff_pct: Decimal = Decimal("2.0")
    # Sprint 6 -- how many of the most recent real M15 candles
    # `technical_price_levels()` reads its swing high/low from. Matches
    # `daily_research.py`'s own `EARLY_STAGE_LOOKBACK_CANDLES` (~1.6
    # trading sessions) for consistency, declared independently here to
    # avoid a cross-module import/coupling between the two orchestrators.
    technical_level_lookback_candles: int = 40
    # Sprint 7A -- how old the LATEST real M15 candle may be, while the
    # market is genuinely open, before M15 trend/VWAP/regime evidence is
    # withheld as not-current (see `_candle_series_is_current()`).
    # Deliberately larger than the underlying-quote staleness threshold
    # (`underlying_stale_threshold`, 5 min) -- an M15 candle only closes
    # every 15 minutes by construction, so a tighter bound would falsely
    # flag perfectly normal data every single run.
    candle_series_stale_threshold: timedelta = timedelta(minutes=20)
    # Sprint 7A, Objective 4 -- the real PCR(OI) point-change (vs the
    # most recent real prior snapshot) below which a change is treated
    # as noise, not a real shift in put/call positioning.
    meaningful_pcr_change: Decimal = Decimal("0.1")
    # Sprint 7A, Objective 3 -- multi-strike OI migration.
    oi_migration_top_n: int = 3
    oi_migration_meaningful_shift_pct: Decimal = Decimal("1.0")
    # Sprint 7A, Objective 6 -- the day-change-percentage-point gap
    # (underlying vs NIFTY 50) below which relative strength reads
    # NEUTRAL rather than OUTPERFORMING/UNDERPERFORMING.
    relative_strength_meaningful_gap_pct: Decimal = Decimal("1.0")
    # Sprint 7A, Objective 7 -- IV vs realized-vol ratio bands.
    iv_vs_realized_low_ratio: Decimal = Decimal("0.8")
    iv_vs_realized_high_ratio: Decimal = Decimal("1.3")
    high_vol_atr_pct: Decimal = Decimal("1.5")  # ATR as % of price
    low_vol_atr_pct: Decimal = Decimal("0.3")
    max_chain_age: timedelta = timedelta(seconds=60)
    max_chain_spread_fraction: Decimal = Decimal("0.20")
    max_option_quote_age: timedelta = timedelta(minutes=5)
    chain_fetch_attempts: int = 3
    chain_fetch_backoff_seconds: float = 1.0
    # Sprint 2 -- the real underlying-quote fetch (step 3 below) had ZERO
    # retry until now, unlike the option-chain fetch just above -- a real
    # transient timeout here (confirmed live: UNOMINDA/BANKNIFTY on
    # 2026-08-3x) permanently failed the whole symbol. Reuses the SAME
    # `_retry()` helper/exception split, deliberately 2 attempts (not 3
    # like the chain fetch) -- this is the smallest bounded retry, not a
    # blanket "retry everything more."
    underlying_quote_fetch_attempts: int = 2
    underlying_quote_fetch_backoff_seconds: float = 1.0
    rsi_period: int = 14
    atr_period: int = 14
    # Phase 4 (multi-expiry term structure) and Phase 6 (temporal evidence) —
    # both ENGINEERING HEURISTIC selections/windows, not empirically tuned:
    compare_expiries: bool = True
    temporal_lookback: timedelta = timedelta(minutes=30)
    max_temporal_observations: int = 3
    temporal_min_oi_for_high_quality: int = 10_000
    temporal_min_volume_for_high_quality: int = 1_000
    # Section 6 (decay attribution) — THRESHOLD, residual as a fraction of
    # the total observed move:
    decay_attributed_max_residual_fraction: Decimal = Decimal("0.25")
    decay_unreliable_min_residual_fraction: Decimal = Decimal("0.75")
    # Section 13 (anomaly detection) — THRESHOLD, multiples of the real
    # historical mean; min_sample_size deliberately small given this
    # product's real accumulated history is currently thin:
    anomaly_min_sample_size: int = 5
    anomaly_elevated_ratio: Decimal = Decimal("2.0")
    anomaly_unusual_ratio: Decimal = Decimal("4.0")
    # Section 5 (level stability) — THRESHOLD, fractional OI change:
    level_stability_meaningful_oi_change_fraction: Decimal = Decimal("0.10")
    # Section 21 (freshness gradient) — THRESHOLD, seconds:
    recent_max_age_seconds: float = 5.0
    # Phase 12 (global/market-wide context) — THRESHOLD, day-change percent:
    global_context_meaningful_move_pct: Decimal = Decimal("0.3")
    # Stage 8 (decay viability) — THRESHOLD, holding horizon in real hours
    # (default: one NSE trading session) and viability-ratio cutoffs:
    decay_viability_holding_horizon_hours: Decimal = Decimal("6.25")
    decay_viability_iv_scenario_points: Decimal = Decimal("1.0")
    decay_viability_favorable_min_ratio: Decimal = Decimal("3.0")
    decay_viability_acceptable_min_ratio: Decimal = Decimal("1.5")
    decay_viability_headwind_min_ratio: Decimal = Decimal("0.75")
    # Sprint 4 -- news (Part V: bounded timeout so a slow/failed provider
    # never blocks the rest of the analysis; THRESHOLD, no single correct
    # "too old to count as fresh" window) and direction comparison
    # (THRESHOLD, no single correct "how many rows counts as strong"):
    news_fetch_timeout_seconds: float = 5.0
    news_fresh_within: timedelta = timedelta(days=2)
    direction_min_supporting_rows_for_strong: int = 3
    # Sprint 7B, Objective 1 -- macro regime (market_regime_context.py's
    # own voting rule genuinely differs from global_context.py's, so a
    # separate threshold, not silently shared).
    market_regime_meaningful_move_pct: Decimal = Decimal("0.3")
    # Sprint 7B, Objective 6 -- how recent counts as INTRADAY for a news
    # item (see event_classification.classify_news_recency()).
    news_intraday_within: timedelta = timedelta(hours=6)


@dataclass
class Repositories:
    quotes: QuoteRepository
    option_chains: OptionChainRepository
    iv_observations: IvObservationRepository


_DEFAULT_CONFIG = PipelineConfig()


async def _retry[T](coro_factory: Callable[[], Awaitable[T]], *, attempts: int, backoff_seconds: float) -> T:
    """Retries a transient-failure-prone provider call with linear backoff.
    Only used around the option-chain fetch, empirically the highest-
    variance real call this session measured (51ms-4.9s across otherwise
    identical requests). Never retries a `ProviderMalformedResponse` (a
    genuinely bad response, not a transient failure) — only
    `ProviderTimeout`/`ProviderUnavailable`/`ProviderRateLimited`.
    """
    from app.data.providers.exceptions import (
        ProviderRateLimited,
        ProviderTimeout,
        ProviderUnavailable,
    )

    last_exc: ProviderError | None = None
    for attempt in range(attempts):
        try:
            return await coro_factory()
        except (ProviderTimeout, ProviderUnavailable, ProviderRateLimited) as exc:
            last_exc = exc
            if attempt < attempts - 1:
                await asyncio.sleep(backoff_seconds * (attempt + 1))
    assert last_exc is not None
    raise last_exc


def _blank_report(symbol: str, *, generated_at: datetime, error: str) -> OptionsIntelligenceReport:
    return OptionsIntelligenceReport(
        symbol=symbol, underlying_instrument_key=None, generated_at=generated_at,
        data_state=MarketDataState.ERROR, data_age_seconds=None, error=error,
    )


async def analyze_symbol(
    symbol: str,
    *,
    provider: AnalysisProvider,
    instrument_master: Sequence[dict[str, object]],
    strategy: EMAVWAPAlignmentStrategy,
    repositories: Repositories,
    as_of: datetime,
    config: PipelineConfig = _DEFAULT_CONFIG,
    mcx_instrument_master: Sequence[dict[str, object]] | None = None,
    requested_strike: Decimal | None = None,
    requested_right: OptionRight | None = None,
    requested_expiry_hint: str | None = None,
    requested_expiry_year: int | None = None,
    # Sprint 8, Objective P1 -- the real `{trading_symbol: industry}`
    # mapping from `nse_sector_index.fetch_sector_index()`. Optional and
    # caller-supplied (same pattern as `mcx_instrument_master`): when
    # omitted, sector classification is honestly UNKNOWN, never guessed.
    sector_map: dict[str, str] | None = None,
    nifty50_symbols: Sequence[str] | None = None,
    http_client: httpx.AsyncClient | None = None,
    fii_cash_net: Decimal | None = None,
    dii_cash_net: Decimal | None = None,
    index_fo_net: Decimal | None = None,
    fii_dii_as_of: date | None = None,
    fii_dii_source: str | None = None,
    delivery_cache_dir: Path | None = None,
) -> OptionsIntelligenceReport:
    stage_latencies: list[StageLatency] = []
    total_started = time.perf_counter()

    def _measure(stage: str, started: float) -> None:
        stage_latencies.append(StageLatency(stage=stage, seconds=time.perf_counter() - started))

    normalizer = DefaultNormalizer(provider_name=provider.name)

    # -- 1. instrument resolution --------------------------------------
    t = time.perf_counter()
    ref = resolve_symbol(instrument_master, symbol)
    _measure("instrument_resolution", t)
    if ref is None:
        return _blank_report(symbol, generated_at=as_of, error=f"{symbol!r} not found in the real Upstox instrument master")

    # -- 2. market status -------------------------------------------------
    t = time.perf_counter()
    try:
        exchange_status = await provider.get_market_status(exchange="NSE")
    except ProviderError:
        exchange_status = ExchangeStatus.UNKNOWN
    # Final Hardening Pass, Phase 2 -- the real NSE pre-open call-auction
    # window, distinct from plain after-hours closure (see
    # MarketDataState.PRE_MARKET's own docstring).
    is_pre_market = exchange_status in (ExchangeStatus.PRE_OPEN_START, ExchangeStatus.PRE_OPEN_END)
    _measure("market_status", t)

    # -- 3. underlying quote ------------------------------------------
    t = time.perf_counter()
    try:
        raw_quote = await _retry(
            functools.partial(provider.get_quote, security_id=ref.instrument_key, exchange_segment=ExchangeSegment.NSE_EQ, as_of=as_of),
            attempts=config.underlying_quote_fetch_attempts, backoff_seconds=config.underlying_quote_fetch_backoff_seconds,
        )
    except ProviderError as exc:
        _measure("quote", t)
        return _blank_report(symbol, generated_at=as_of, error=f"real quote fetch failed: {exc}")
    _measure("quote", t)
    quote = normalizer.normalize_quote(raw_quote, instrument_id=ref.instrument_key, received_at=as_of)
    await repositories.quotes.save(quote)

    # -- 4. M15 historical candles ----------------------------------------
    t = time.perf_counter()
    try:
        raw_candles = await provider.get_ohlcv(
            security_id=ref.instrument_key, exchange_segment=ExchangeSegment.NSE_EQ, timeframe=Timeframe.M15,
            start=as_of - config.history_lookback, end=as_of, as_of=as_of,
        )
    except ProviderError:
        raw_candles = []
    _measure("historical_candles", t)
    candles = [
        normalizer.normalize_candle(raw, instrument_id=ref.instrument_key, timeframe=Timeframe.M15, received_at=raw.timestamp)
        for raw in raw_candles
    ]

    requirement = strategy.required_timeframes()[0]
    data_age = as_of - quote.freshness.data_timestamp
    data_state = classify_market_data_state(
        exchange_status_is_open=exchange_status == ExchangeStatus.NORMAL_OPEN, is_live_stream=False, data_age=data_age,
        data_date=quote.freshness.data_timestamp.date(), as_of_date=as_of.date(),
        candles_available=len(candles), minimum_candles=requirement.minimum_candles,
        stale_threshold=config.underlying_stale_threshold, is_pre_market=is_pre_market,
    )

    # Sprint 7A, Objective 1 -- hard market-data synchronization: a
    # current-session quote must never be silently interpreted alongside
    # M15-trend/VWAP/regime evidence derived from a STALE (previous-
    # session) candle series. Reuses `classify_market_data_state()` a
    # second time -- same real function, same real market-state-aware
    # rule already established for the quote (Sprint 2/3) -- evaluated
    # against the LATEST CANDLE's own real timestamp instead. Candles are
    # NEVER discarded or hidden for display/charting; this only gates
    # whether they are trusted as CURRENT evidence.
    candles_are_current = True
    if candles:
        last_candle_state = classify_market_data_state(
            exchange_status_is_open=exchange_status == ExchangeStatus.NORMAL_OPEN, is_live_stream=False,
            data_age=as_of - candles[-1].freshness.data_timestamp, data_date=candles[-1].freshness.data_timestamp.date(),
            as_of_date=as_of.date(), candles_available=len(candles), minimum_candles=1,
            stale_threshold=config.candle_series_stale_threshold, is_pre_market=is_pre_market,
        )
        candles_are_current = last_candle_state != MarketDataState.STALE_DATA

    report = OptionsIntelligenceReport(
        symbol=symbol, underlying_instrument_key=ref.instrument_key, generated_at=as_of, data_state=data_state,
        data_age_seconds=data_age.total_seconds(), spot=quote.last_price, candles=candles,
        candles_are_current=candles_are_current,
        historical_structure=assess_historical_structure(
            candles, instrument_id=ref.instrument_key, timeframe=Timeframe.M15, as_of=as_of, spot=quote.last_price,
        ),
        # The dated break-and-reclaim fact behind FAILED_BREAKDOWN_RECLAIM.
        # Same already-fetched candles, same `as_of` boundary, no fetch.
        structural_reclaim=detect_structural_reclaim(
            candles, instrument_id=ref.instrument_key, timeframe=Timeframe.M15, as_of=as_of, spot=quote.last_price,
        ),
    )
    if quote.previous_close is not None:
        report.day_change = quote.last_price - quote.previous_close
        report.day_change_pct = report.day_change / quote.previous_close * Decimal(100)

    # -- 4b. news (Sprint 4, Part A/V) -- real, first-party, already-
    # authorized Upstox source (confirmed live 2026-08-29, see
    # docs/data-sources/PROVIDER_DECISION.md). A bounded timeout and a
    # broad except ensure a failed/slow news fetch NEVER blocks or breaks
    # the rest of the analysis -- graceful degradation, source isolation.
    t = time.perf_counter()
    try:
        raw_news = await asyncio.wait_for(provider.get_news(instrument_key=ref.instrument_key), timeout=config.news_fetch_timeout_seconds)
        news_items = [
            build_news_item(
                source=provider.name, heading=raw.heading, summary=raw.summary, url=raw.article_link,
                published_at=raw.published_time, retrieved_at=as_of, symbol=symbol, as_of=as_of,
                fresh_within=config.news_fresh_within,
            )
            for raw in raw_news
        ]
        report.news_items = filter_news_no_lookahead(deduplicate_news_items(news_items), as_of=as_of)
    except (ProviderError, TimeoutError) as exc:
        report.news_fetch_error = str(exc)
    _measure("news", t)

    # -- 5. technical analysis (EMA/VWAP/RSI/ATR/regime) -------------------
    t = time.perf_counter()
    if data_state != MarketDataState.INSUFFICIENT_HISTORY:
        market_state = assemble_market_state(quote, as_of=as_of)
        report.analysis = assemble_current_analysis(
            market_state=market_state, completed_candles={Timeframe.M15: candles}, current_partial_candle=None,
            strategy=strategy, as_of=as_of,
        )
        report.rsi = calculate_rsi(candles, instrument_id=ref.instrument_key, timeframe=Timeframe.M15, period=config.rsi_period, as_of=as_of)
        atr = calculate_atr(candles, instrument_id=ref.instrument_key, timeframe=Timeframe.M15, period=config.atr_period, as_of=as_of)
        report.regime = classify_market_regime(
            ema_alignment=report.analysis.ema_alignment, ema_status=report.analysis.ema_status,
            vwap_state=report.analysis.price_vs_vwap, vwap_status=report.analysis.vwap_status, atr=atr,
            current_price=quote.last_price, high_vol_atr_pct=config.high_vol_atr_pct, low_vol_atr_pct=config.low_vol_atr_pct,
        )
    else:
        report.data_warnings.append("insufficient M15 history for EMA/VWAP/RSI/ATR/regime")
    _measure("technical_analysis", t)

    if data_state in (MarketDataState.INSUFFICIENT_HISTORY, MarketDataState.PROVIDER_UNAVAILABLE, MarketDataState.ERROR):
        report.stage_latencies = stage_latencies
        report.total_latency_seconds = time.perf_counter() - total_started
        report.error = f"underlying data state is {data_state.value} -- cannot proceed to options analysis"
        return report

    # -- 6. F&O eligibility -------------------------------------------------
    t = time.perf_counter()
    eligible = is_fo_eligible(instrument_master, ref.trading_symbol)
    _measure("fo_eligibility", t)
    if not eligible:
        report.stage_latencies = stage_latencies
        report.total_latency_seconds = time.perf_counter() - total_started
        report.error = f"{ref.trading_symbol} has no F&O (options/futures) segment in the real instrument master"
        return report

    # -- 7. expiry selection ------------------------------------------------
    t = time.perf_counter()
    if requested_expiry_hint:
        expiry_info = resolve_expiry_for_hint(
            instrument_master, ref.trading_symbol, hint=requested_expiry_hint,
            as_of=as_of.date(), year=requested_expiry_year,
        )
        if expiry_info is None:
            _measure("expiry_selection", t)
            report.stage_latencies = stage_latencies
            report.total_latency_seconds = time.perf_counter() - total_started
            year_bit = f" {requested_expiry_year}" if requested_expiry_year is not None else ""
            report.error = (
                f"CONTRACT UNAVAILABLE — no verified {requested_expiry_hint}{year_bit} expiry "
                f"in the instrument master for {ref.trading_symbol}. Another expiry was not substituted."
            )
            return report
    else:
        expiry_info = nearest_expiry(instrument_master, ref.trading_symbol, as_of=as_of.date())
    _measure("expiry_selection", t)
    if expiry_info is None:
        report.stage_latencies = stage_latencies
        report.total_latency_seconds = time.perf_counter() - total_started
        report.error = "no upcoming expiry found in the real instrument master"
        return report
    report.expiry = expiry_info.expiry.isoformat()

    # -- 8. option chain (with retry/backoff) -------------------------------
    t = time.perf_counter()
    previous_snapshot = await repositories.option_chains.latest(
        underlying=ref.instrument_key, expiry=expiry_info.expiry, as_of=as_of - timedelta(microseconds=1)
    )
    temporal_history = await repositories.option_chains.query_range(
        underlying=ref.instrument_key, expiry=expiry_info.expiry, start=as_of - config.temporal_lookback,
        end=as_of - timedelta(microseconds=1), as_of=as_of - timedelta(microseconds=1),
    )
    snapshot: OptionChainSnapshot | None
    # Final release gate (Section 7) -- retry ONLY a provider that claims
    # to serve this stream. A provider declaring
    # `historical_option_chain=False` (i.e. `HistoricalReplayProvider`)
    # raises `ProviderUnavailable` unconditionally and by design: there is
    # no historical option-chain endpoint to be transiently unavailable,
    # so every retry is a guaranteed failure bought with a real backoff
    # sleep. Measured on a real replay session: 3 attempts x 2 expiries of
    # linear backoff cost ~6.2 s per bar, which WAS essentially the entire
    # replay runtime (profiling showed 18.1 s of 19.6 s across 3 bars was
    # the event loop sleeping, against ~0.34 s of actual provider work).
    # The graceful "provider itself declares it never had this data" path
    # immediately below is unchanged and still handles the failure --
    # this only stops paying for retries that cannot succeed.
    #
    # The LIVE path is untouched: `UpstoxProvider` declares
    # `historical_option_chain=True`, so it still gets the full
    # `chain_fetch_attempts`/`chain_fetch_backoff_seconds` treatment for
    # the genuinely transient failures that helper exists for.
    chain_attempts = config.chain_fetch_attempts if provider.capabilities.historical_option_chain else 1
    try:
        raw_chain = await _retry(
            lambda: provider.get_chain(underlying=ref.instrument_key, expiry=expiry_info.expiry, as_of=as_of),
            attempts=chain_attempts, backoff_seconds=config.chain_fetch_backoff_seconds,
        )
    except ProviderError as exc:
        _measure("option_chain", t)
        if provider.capabilities.historical_option_chain:
            # The provider CLAIMS to support this stream -- a real, live
            # failure. Unchanged from before this phase: fatal, exactly
            # as documented (this pipeline is Options Intelligence; a
            # live chain that should exist but genuinely doesn't right
            # now leaves nothing trustworthy to reason about).
            report.data_warnings.append(f"option chain fetch failed after {config.chain_fetch_attempts} attempt(s): {exc}")
            report.stage_latencies = stage_latencies
            report.total_latency_seconds = time.perf_counter() - total_started
            report.error = f"real option chain fetch failed: {exc}"
            return report
        # Phase 3 gap-closure -- the provider ITSELF declares it never had
        # historical option-chain data for this instant (a
        # `HistoricalReplayProvider` replaying a date before any real
        # snapshot was captured). This is a structural, expected absence,
        # never a live outage -- every derivatives-dependent stage below
        # degrades honestly (None/empty inputs its own row builders/
        # quality functions already handle for a genuinely thin live
        # chain) instead of the analysis dying here. Never fabricated:
        # `report.option_chain` stays `None`.
        report.derivatives_history_available = False
        report.data_warnings.append(f"historical option-chain evidence unavailable: {exc}")
        snapshot = None
    else:
        _measure("option_chain", t)
        snapshot = normalizer.normalize_option_chain(raw_chain, received_at=as_of)
        await repositories.option_chains.save(snapshot)
        report.option_chain = snapshot

    # -- 9. chain quality / OI-PCR/ATM/support-resistance -------------------
    t = time.perf_counter()
    atm: Decimal | None
    levels: list[Level]
    if snapshot is not None:
        report.chain_quality_issues = check_chain_quality(
            snapshot, as_of=as_of, max_age=config.max_chain_age, max_spread_fraction=config.max_chain_spread_fraction
        )
        totals = chain_totals(snapshot)
        report.total_call_oi = totals.total_call_oi
        report.total_put_oi = totals.total_put_oi
        report.pcr_oi = totals.put_call_ratio_oi
        # Sprint 7A, Objective 4 -- the real prior chain's own totals, so PCR
        # CHANGE + OI BALANCE can be computed from a genuine prior snapshot,
        # never a second fetch (reuses the SAME `previous_snapshot` already
        # fetched above for level-stability).
        previous_totals = chain_totals(previous_snapshot) if previous_snapshot is not None else None

        # Sprint 7A, Objective 3 -- multi-strike OI migration, informational
        # (see app.domain.options.oi_migration's own docstring). Reuses the
        # SAME real current/previous snapshots, zero new fetch.
        report.ce_oi_migration = analyze_oi_migration(
            current_snapshot=snapshot, previous_snapshot=previous_snapshot, right=OptionRight.CE,
            top_n=config.oi_migration_top_n, meaningful_shift_pct=config.oi_migration_meaningful_shift_pct,
        )
        report.pe_oi_migration = analyze_oi_migration(
            current_snapshot=snapshot, previous_snapshot=previous_snapshot, right=OptionRight.PE,
            top_n=config.oi_migration_top_n, meaningful_shift_pct=config.oi_migration_meaningful_shift_pct,
        )
        atm = atm_strike(snapshot)
        report.atm_strike = atm
        levels = support_resistance_levels(snapshot, limit=3)
        report.support_levels = [lv for lv in levels if lv.kind.value == "support"]
        report.resistance_levels = [lv for lv in levels if lv.kind.value == "resistance"]
        report.level_stability = assess_level_stability(
            levels, previous_snapshot=previous_snapshot,
            meaningful_oi_change_fraction=config.level_stability_meaningful_oi_change_fraction,
        )
        # Same real `snapshot.underlying_last_price` reference the OI-level
        # geometry above already uses (`support_resistance_levels()`) --
        # never mixed with a different "spot" source (see the traced KAYNES
        # price-consistency root cause, app.domain.market.price_consistency).
        vwap_ema_spot = snapshot.underlying_last_price
    else:
        # Phase 3 gap-closure -- no OI/chain-derived evidence exists for
        # this instant (see stage 8's own comment). Every value below is
        # the SAME "no data" shape `check_chain_quality()`/`chain_totals()`/
        # `atm_strike()`/`support_resistance_levels()` would themselves
        # produce for a genuinely empty chain -- never a new sentinel, and
        # never a claim that a chain existed and happened to be empty
        # (that would be exactly the fabrication Phase 3 forbids;
        # `report.derivatives_history_available` is the authoritative
        # "no chain at all" signal, not these zeros).
        report.chain_quality_issues = []
        totals = ChainTotals(
            total_call_oi=0, total_put_oi=0, total_call_volume=0, total_put_volume=0,
            put_call_ratio_oi=None, put_call_ratio_volume=None, legs_with_known_oi=0, legs_with_missing_oi=0,
        )
        report.total_call_oi = totals.total_call_oi
        report.total_put_oi = totals.total_put_oi
        report.pcr_oi = totals.put_call_ratio_oi
        previous_totals = None
        report.ce_oi_migration = None
        report.pe_oi_migration = None
        atm = None
        report.atm_strike = None
        levels = []
        report.support_levels = []
        report.resistance_levels = []
        report.level_stability = []
        # No chain-derived spot reference exists -- fall back to the same
        # real quote LTP every other stage already trusts as spot.
        vwap_ema_spot = quote.last_price
    technical_levels = technical_price_levels(candles, lookback=config.technical_level_lookback_candles)
    # Sprint 7A, Objective 11 -- VWAP/EMA50 as additional real, already-
    # computed confluence sources (zero new fetch -- see
    # vwap_ema_levels()'s own docstring).
    technical_levels = technical_levels + vwap_ema_levels(
        spot=vwap_ema_spot, vwap_value=report.analysis.vwap_value if report.analysis else None,
        ema_value=report.analysis.ema50 if report.analysis else None,
    )
    report.technical_levels = technical_levels
    report.level_classifications = classify_level_confluence(
        levels, technical_levels, near_pct_threshold=config.near_level_pct_threshold,
    )
    report.freshness_label = classify_freshness_label(
        data_state=data_state, data_age_seconds=data_age.total_seconds(),
        has_quality_issues=bool(report.chain_quality_issues), recent_max_age_seconds=config.recent_max_age_seconds,
    )
    _measure("chain_aggregation", t)

    # -- 10. IV context (+ persisted rank) ---------------------------------
    t = time.perf_counter()
    # No chain -> the exact same "no data" shape `atm_iv_summary()` itself
    # returns for a chain with no resolvable ATM strike (never a new
    # sentinel).
    report.iv_summary = (
        atm_iv_summary(snapshot) if snapshot is not None
        else AtmIvSummary(atm_strike=None, atm_ce_iv=None, atm_pe_iv=None, chain_iv=None, ce_pe_skew=None)
    )
    iv_history = await repositories.iv_observations.query_history(
        underlying=ref.instrument_key, as_of=as_of, lookback=config.iv_rank_lookback
    )
    report.iv_rank = compute_iv_rank(iv_history, current_chain_iv=report.iv_summary.chain_iv, min_observations=config.min_iv_rank_observations)
    report.iv_trend = classify_iv_trend(
        current_chain_iv=report.iv_summary.chain_iv, history=iv_history,
        meaningful_change_points=config.iv_trend_meaningful_change_points,
    )
    if report.iv_summary.atm_strike is not None and snapshot is not None:
        await repositories.iv_observations.save(
            IvObservation(
                provider=provider.name, freshness=snapshot.freshness, underlying=ref.instrument_key, expiry=expiry_info.expiry,
                atm_strike=report.iv_summary.atm_strike, atm_ce_iv=report.iv_summary.atm_ce_iv,
                atm_pe_iv=report.iv_summary.atm_pe_iv, chain_iv=report.iv_summary.chain_iv,
            )
        )
    _measure("iv_context", t)

    # Sprint 7A, Objective 7 -- realized volatility vs implied volatility,
    # from the SAME already-fetched M15 candle series, zero new fetch.
    report.realized_vol_5d = compute_realized_volatility(candles, window_trading_days=5, window_label="5D")
    report.realized_vol_10d = compute_realized_volatility(candles, window_trading_days=10, window_label="10D")
    report.realized_vol_20d = compute_realized_volatility(candles, window_trading_days=20, window_label="20D")
    # Live-verified 2026-09-02 (KAYNES/RELIANCE/NIFTY/HDFCBANK): the
    # default `history_lookback` (10 CALENDAR days) only ever yields
    # ~6-7 real trading sessions, so `realized_vol_10d` is honestly
    # INSUFFICIENT_DATA on every real run -- comparing against it here
    # would make this comparison permanently unusable. `realized_vol_5d`
    # is the window this system can actually, reliably compute; it
    # remains genuinely INSUFFICIENT_DATA too on a real short history,
    # which still correctly propagates as INSUFFICIENT_DATA below.
    report.iv_vs_realized_state, report.iv_vs_realized_detail = classify_iv_vs_realized(
        report.iv_summary.chain_iv, report.realized_vol_5d.annualized_vol_pct,
        low_ratio=config.iv_vs_realized_low_ratio, high_ratio=config.iv_vs_realized_high_ratio,
    )

    # -- 11. price+OI for the ATM legs (needs persisted chain history) -----
    ce_oi_observation = pe_oi_observation = None
    if atm is not None and snapshot is not None:
        current_ce = _find_leg(snapshot, strike=atm, right=OptionRight.CE)
        current_pe = _find_leg(snapshot, strike=atm, right=OptionRight.PE)
        previous_ce = _find_leg(previous_snapshot, strike=atm, right=OptionRight.CE) if previous_snapshot else None
        previous_pe = _find_leg(previous_snapshot, strike=atm, right=OptionRight.PE) if previous_snapshot else None
        ce_oi_observation = classify_price_oi(
            current_price=current_ce.last_price if current_ce else None, previous_price=previous_ce.last_price if previous_ce else None,
            current_oi=current_ce.open_interest if current_ce else None, previous_oi=previous_ce.open_interest if previous_ce else None,
            price_flat_threshold=config.option_price_flat_threshold,
        )
        pe_oi_observation = classify_price_oi(
            current_price=current_pe.last_price if current_pe else None, previous_price=previous_pe.last_price if previous_pe else None,
            current_oi=current_pe.open_interest if current_pe else None, previous_oi=previous_pe.open_interest if previous_pe else None,
            price_flat_threshold=config.option_price_flat_threshold,
        )
        if previous_snapshot is None:
            report.data_warnings.append("no prior persisted option-chain snapshot yet -- change-in-OI evidence is INSUFFICIENT_DATA on this run")

    # -- 11b. temporal evidence (Phase 6: real multi-snapshot transitions,
    # against whatever prior snapshots actually exist within the lookback
    # window -- real elapsed intervals, never fabricated 1/5/15-minute
    # buckets that don't correspond to when this pipeline actually ran) --
    t = time.perf_counter()
    if atm is not None and snapshot is not None:
        for prior in temporal_history[-config.max_temporal_observations :]:
            for right in (OptionRight.CE, OptionRight.PE):
                try:
                    observation = compute_temporal_observation(
                        earlier=prior, later=snapshot, strike=atm, right=right,
                        price_flat_threshold=config.option_price_flat_threshold,
                        min_oi_for_high_quality=config.temporal_min_oi_for_high_quality,
                        min_volume_for_high_quality=config.temporal_min_volume_for_high_quality,
                        max_reasonable_interval=config.temporal_lookback,
                    )
                except ValueError:
                    continue  # defensive: query_range's own as_of bound already guarantees `prior` precedes `snapshot`
                report.temporal_observations.append(observation)
    _measure("temporal_evidence", t)

    # -- 11d. decay attribution (Section 6) ----------------------------------
    t = time.perf_counter()
    for observation in report.temporal_observations:
        # `report.temporal_observations` is only ever populated inside the
        # `snapshot is not None` branch above -- guaranteed non-None here.
        assert snapshot is not None
        prior_snapshot = next((s for s in temporal_history if s.freshness.data_timestamp == observation.t0), None)
        if prior_snapshot is None:
            continue
        prior_leg = _find_leg(prior_snapshot, strike=observation.strike, right=observation.right)
        report.decay_attributions.append(
            attribute_decay(
                observation, underlying_price_t0=prior_snapshot.underlying_last_price,
                underlying_price_t1=snapshot.underlying_last_price,
                delta_t0=prior_leg.delta if prior_leg else None, gamma_t0=prior_leg.gamma if prior_leg else None,
                vega_t0=prior_leg.vega if prior_leg else None, theta_t0=prior_leg.theta if prior_leg else None,
                attributed_max_residual_fraction=config.decay_attributed_max_residual_fraction,
                unreliable_min_residual_fraction=config.decay_unreliable_min_residual_fraction,
            )
        )
    _measure("decay_attribution", t)

    # -- 11e. unusual activity / anomaly detection (Section 13) -------------
    t = time.perf_counter()
    if atm is not None and temporal_history and snapshot is not None:
        current_ce = _find_leg(snapshot, strike=atm, right=OptionRight.CE)
        current_pe = _find_leg(snapshot, strike=atm, right=OptionRight.PE)
        for right_label, leg in (("ATM CE", current_ce), ("ATM PE", current_pe)):
            if leg is None:
                continue
            historical_legs = [hl for s in temporal_history if (hl := _find_leg(s, strike=atm, right=leg.right)) is not None]
            volume_hist = [Decimal(hl.volume) for hl in historical_legs if hl.volume is not None]
            oi_hist = [Decimal(hl.open_interest) for hl in historical_legs if hl.open_interest is not None]
            iv_hist = [hl.implied_volatility for hl in historical_legs if hl.implied_volatility is not None and hl.implied_volatility > 0]

            report.anomalies.append(
                detect_anomaly(
                    metric_name=f"{right_label} volume", current_value=Decimal(leg.volume) if leg.volume is not None else None,
                    historical_values=volume_hist, min_sample_size=config.anomaly_min_sample_size,
                    elevated_ratio=config.anomaly_elevated_ratio, unusual_ratio=config.anomaly_unusual_ratio,
                )
            )
            report.anomalies.append(
                detect_anomaly(
                    metric_name=f"{right_label} OI", current_value=Decimal(leg.open_interest) if leg.open_interest is not None else None,
                    historical_values=oi_hist, min_sample_size=config.anomaly_min_sample_size,
                    elevated_ratio=config.anomaly_elevated_ratio, unusual_ratio=config.anomaly_unusual_ratio,
                )
            )
            if leg.implied_volatility is not None and leg.implied_volatility > 0:
                report.anomalies.append(
                    detect_anomaly(
                        metric_name=f"{right_label} IV", current_value=leg.implied_volatility, historical_values=iv_hist,
                        min_sample_size=config.anomaly_min_sample_size, elevated_ratio=config.anomaly_elevated_ratio,
                        unusual_ratio=config.anomaly_unusual_ratio,
                    )
                )
    _measure("anomaly_detection", t)

    # -- 11c. multi-expiry term structure (Phase 4) -------------------------
    t = time.perf_counter()
    if config.compare_expiries:
        relevant_expiries = select_relevant_expiries(instrument_master, ref.trading_symbol, as_of=as_of.date())
        summaries = []

        def _fetch_other_chain(expiry_date: date) -> Callable[[], Awaitable[RawOptionChain]]:
            async def _call() -> RawOptionChain:
                return await provider.get_chain(underlying=ref.instrument_key, expiry=expiry_date, as_of=as_of)

            return _call

        for info in relevant_expiries:
            if info.expiry == expiry_info.expiry:
                if snapshot is None:
                    continue  # already recorded as a data_warning at stage 8
                chain_for_summary = snapshot
            else:
                try:
                    # Same capability-aware retry as stage 8 above, and
                    # for the same reason: a provider that declares it has
                    # no historical option chain cannot transiently
                    # succeed on a second attempt, so the backoff is pure
                    # waste. Reuses stage 8's own `chain_attempts` rather
                    # than recomputing the same condition.
                    raw_other = await _retry(
                        _fetch_other_chain(info.expiry),
                        attempts=chain_attempts, backoff_seconds=config.chain_fetch_backoff_seconds,
                    )
                except ProviderError as exc:
                    report.data_warnings.append(f"term structure: chain fetch failed for expiry {info.expiry.isoformat()}: {exc}")
                    continue
                chain_for_summary = normalizer.normalize_option_chain(raw_other, received_at=as_of)
                await repositories.option_chains.save(chain_for_summary)  # persisted too -- future temporal evidence for this expiry
            summaries.append(
                build_expiry_summary(
                    chain_for_summary, is_weekly=info.is_weekly, as_of=as_of,
                    max_chain_age_for_quality_check=config.max_chain_age,
                    max_spread_fraction_for_quality_check=config.max_chain_spread_fraction,
                )
            )
        if summaries:
            report.term_structure = TermStructure(expiries=summaries)
            report.expiry_selection_note = build_expiry_selection_note(report.term_structure)
    _measure("term_structure", t)

    # -- 12. futures -----------------------------------------------------
    t = time.perf_counter()
    fut_key = futures_instrument_key(instrument_master, ref.trading_symbol, expiry=expiry_info.expiry)
    futures_oi_observation = None
    if fut_key is not None:
        previous_fut_quote = await repositories.quotes.latest(instrument_id=fut_key, as_of=as_of - timedelta(microseconds=1))
        # Final Hardening Pass, Phase 6 -- the underlying's own quote is
        # ALSO already persisted to this SAME repository every run (see
        # the `repositories.quotes.save(quote)` call above, in the
        # underlying-quote stage) -- zero new fetch to recover a genuine
        # prior basis alongside the prior futures quote already fetched
        # here.
        previous_underlying_quote = await repositories.quotes.latest(instrument_id=ref.instrument_key, as_of=as_of - timedelta(microseconds=1))
        try:
            # Sprint 3, Priority 4 -- reuses the SAME bounded retry/config
            # values as the underlying-quote fetch above (this is the same
            # kind of call: a single-key `get_quotes()`), rather than a
            # third near-duplicate config field. Safe to add here
            # specifically because a failure already degrades gracefully
            # (a data_warning, never a failed symbol) -- the retry can
            # only recover more real futures context, never make this
            # path riskier.
            # Final release gate (Section 7) -- capability-aware, exactly
            # as for the option chain above: a provider declaring
            # `historical_futures=False` has no historical futures stream
            # that could come back on a retry, so the backoff buys a
            # guaranteed second failure. The graceful `data_warning` path
            # below is unchanged; the live provider
            # (`historical_futures=True`) keeps its full retry budget.
            futures_attempts = (
                config.underlying_quote_fetch_attempts if provider.capabilities.historical_futures else 1
            )
            fut_quotes = await _retry(
                functools.partial(provider.get_quotes, [fut_key]),
                attempts=futures_attempts, backoff_seconds=config.underlying_quote_fetch_backoff_seconds,
            )
        except ProviderError as exc:
            report.data_warnings.append(f"futures quote fetch failed: {exc}")
        else:
            raw_fut = fut_quotes.get(fut_key)
            if raw_fut is not None:
                try:
                    # Trader Decision-Support Audit -- real defect found live
                    # 2026-08-31 (market open, INDIGO): Upstox's futures leg
                    # can report `last_trade_time` a few seconds ahead of this
                    # request's frozen `as_of` (identical root cause to the
                    # already-known "malformed last_trade_time" issue for
                    # USDINR/crude derivative keys). `DataFreshness` correctly
                    # rejects that as an impossible "received before it
                    # happened" state (a real no-look-ahead guard, not a bug
                    # to relax) -- but this futures leg is optional/
                    # supplementary (see the `ProviderError` handling one
                    # line above, and the "no futures contract found" warning
                    # below), so a single bad futures timestamp must degrade
                    # this ONE section, never crash the whole analysis the
                    # way it previously did (uncaught `ValidationError`).
                    fut_quote = normalizer.normalize_quote(raw_fut, instrument_id=fut_key, received_at=as_of)
                except ValidationError as exc:
                    report.data_warnings.append(f"futures quote timestamp invalid (provider clock skew): {exc}")
                else:
                    await repositories.quotes.save(fut_quote)
                    report.futures_instrument_key = fut_key
                    report.futures_ltp = fut_quote.last_price
                    report.futures_oi = Decimal(fut_quote.open_interest) if fut_quote.open_interest is not None else None
                    report.futures_volume = fut_quote.volume
                    report.futures_expiry = expiry_info.expiry.isoformat()
                    report.futures_basis_pct = (fut_quote.last_price - quote.last_price) / quote.last_price * Decimal(100)
                    # Final Hardening Pass, Phase 6 -- real prior basis,
                    # computed only when BOTH real prior quotes (futures
                    # AND underlying) exist and the prior underlying price
                    # is a real, positive value -- never guessed/derived
                    # from the current basis.
                    previous_basis_pct = None
                    if (
                        previous_fut_quote is not None and previous_underlying_quote is not None
                        and previous_underlying_quote.last_price > 0
                    ):
                        previous_basis_pct = (
                            (previous_fut_quote.last_price - previous_underlying_quote.last_price)
                            / previous_underlying_quote.last_price * Decimal(100)
                        )
                    report.futures_basis_change = classify_basis_change(
                        current_basis_pct=report.futures_basis_pct, previous_basis_pct=previous_basis_pct,
                        meaningful_change_pct=config.meaningful_basis_change_pct,
                    )
                    futures_oi_observation = classify_price_oi(
                        current_price=fut_quote.last_price, previous_price=previous_fut_quote.last_price if previous_fut_quote else None,
                        current_oi=fut_quote.open_interest, previous_oi=previous_fut_quote.open_interest if previous_fut_quote else None,
                        price_flat_threshold=config.futures_price_flat_threshold,
                    )
                    report.futures_oi_observation = futures_oi_observation
                    report.futures_oi_change = futures_oi_observation.oi_change
                    report.futures_timestamp = fut_quote.freshness.data_timestamp
            else:
                report.data_warnings.append("futures instrument key resolved but no quote was returned")
    else:
        report.data_warnings.append(f"no futures contract found for {ref.trading_symbol} at expiry {expiry_info.expiry} (may be a weekly-only expiry with no matching monthly futures contract)")
    _measure("futures", t)

    # -- 11b. underlying-price consistency (Sprint 6) -----------------------
    # Every real, already-fetched price source is available by this point
    # -- see app.domain.market.price_consistency's own docstring for the
    # full traced root cause this closes. Zero new fetches.
    last_candle = candles[-1] if candles else None
    quote_is_current = data_state != MarketDataState.STALE_DATA
    chain_is_current = not any(
        i.kind in (ChainQualityIssueKind.STALE_SNAPSHOT, ChainQualityIssueKind.DEAD_CHAIN)
        for i in report.chain_quality_issues
    )
    futures_are_current = True
    if report.futures_ltp is not None:
        if report.futures_timestamp is None:
            futures_are_current = False
        else:
            fut_state = classify_market_data_state(
                exchange_status_is_open=exchange_status == ExchangeStatus.NORMAL_OPEN,
                is_live_stream=False,
                data_age=as_of - report.futures_timestamp,
                data_date=report.futures_timestamp.date(),
                as_of_date=as_of.date(),
                candles_available=1,
                minimum_candles=1,
                stale_threshold=config.underlying_stale_threshold,
                is_pre_market=is_pre_market,
            )
            futures_are_current = fut_state != MarketDataState.STALE_DATA
    report.price_consistency = classify_price_consistency(
        quote_price=quote.last_price, quote_timestamp=quote.freshness.data_timestamp,
        candle_price=last_candle.close if last_candle is not None else None,
        candle_timestamp=last_candle.freshness.data_timestamp if last_candle is not None else None,
        option_chain_price=snapshot.underlying_last_price if snapshot is not None else None,
        option_chain_timestamp=snapshot.freshness.data_timestamp if snapshot is not None else None,
        futures_price=report.futures_ltp, futures_timestamp=report.futures_timestamp,
        max_consistent_diff_pct=config.max_consistent_price_diff_pct,
        max_partially_aligned_diff_pct=config.max_partially_aligned_price_diff_pct,
        candle_is_current=candles_are_current,
        quote_is_current=quote_is_current,
        chain_is_current=chain_is_current,
    )

    # -- 12b. global / market-wide context (Phase 12) -----------------------
    # NIFTY/BANKNIFTY/VIX reuse the SAME already-loaded NSE instrument
    # master. USD/INR (real NCD_FO futures, same NSE master) and MCX Crude
    # Oil (real MCX_FO futures, in the SEPARATE `mcx_instrument_master` the
    # caller may optionally supply -- confirmed live 2026-08-29, see
    # `nearest_futures_instrument_key()`) are now wired in too; if the
    # caller doesn't supply an MCX master, crude oil is honestly omitted
    # (never fabricated), not silently guessed from a proxy.
    t = time.perf_counter()
    context_keys: dict[str, str] = {}
    index_keys: dict[str, str] = {}
    for label, context_symbol in (("NIFTY", "NIFTY50"), ("BANKNIFTY", "BANKNIFTY"), ("VIX", "INDIA VIX")):
        context_ref = resolve_symbol(instrument_master, context_symbol)
        if context_ref is not None:
            index_keys[label] = context_ref.instrument_key

    # Sprint 8, Objective P1 -- real official sector classification (see
    # sector_strength.py's own docstring: from NSE's own published Nifty
    # 500 constituent file, never inferred from the symbol's name), and
    # -- only for the industries with a real, verified sectoral INDEX
    # instrument -- that index's own real quote, batched into the SAME
    # NSE_INDEX request as NIFTY/BANKNIFTY/VIX (zero new fetch pattern).
    report.sector_info = classify_sector(ref.trading_symbol, sector_map) if sector_map is not None else None
    if report.sector_info is not None:
        sector_symbol = sector_index_trading_symbol(report.sector_info.industry)
        if sector_symbol is not None:
            sector_ref = resolve_symbol(instrument_master, sector_symbol)
            if sector_ref is not None:
                index_keys["SECTOR"] = sector_ref.instrument_key
    context_keys.update(index_keys)

    derivative_keys: dict[str, str] = {}
    usdinr_key = nearest_futures_instrument_key(instrument_master, "USDINR", segment="NCD_FO", as_of=as_of.date())
    if usdinr_key is not None:
        derivative_keys["USDINR"] = usdinr_key
    crude_key = (
        nearest_futures_instrument_key(mcx_instrument_master, "CRUDEOIL", segment="MCX_FO", as_of=as_of.date())
        if mcx_instrument_master is not None else None
    )
    if crude_key is not None:
        derivative_keys["CRUDE"] = crude_key
    context_keys.update(derivative_keys)

    # Two SEPARATE batch calls, not one: NIFTY/BANKNIFTY/VIX (NSE_INDEX,
    # long-proven stable) must never be taken down by a real, observed
    # failure mode in the newer NCD_FO/MCX_FO derivative quotes (confirmed
    # live 2026-08-29: Upstox returned `last_trade_time: '0'` for these,
    # which this codebase's quote parser correctly rejects as malformed --
    # but that rejection must not poison an otherwise-healthy index batch
    # it happened to share a request with).
    context_quotes: dict[str, object] = {}
    if index_keys:
        try:
            context_quotes.update(await provider.get_quotes(list(index_keys.values())))
        except ProviderError as exc:
            report.data_warnings.append(f"global context index quote fetch failed: {exc}")
    if derivative_keys:
        try:
            context_quotes.update(await provider.get_quotes(list(derivative_keys.values())))
        except ProviderError as exc:
            report.data_warnings.append(f"global context USD/INR or crude oil quote fetch failed: {exc}")

    def _context_day_change_pct(label: str) -> Decimal | None:
        key = context_keys.get(label)
        if key is None:
            return None
        raw_ctx = context_quotes.get(key)
        if raw_ctx is None:
            return None
        assert isinstance(raw_ctx, RawQuote)
        if raw_ctx.previous_close is None or raw_ctx.previous_close == 0:
            return None
        # Sprint 7B live UAT -- real defect found (2026-09-02): a real
        # NCD_FO USD/INR quote came back with `last_price == 0` (a
        # genuinely impossible price for a live currency future), which
        # this helper previously turned into a nonsensical exact -100.00%
        # "day change" -- silently treated as real, contributing macro
        # evidence. Same "impossible price" discipline as
        # `quality_gate._check_impossible_price_single()` (`price <= 0`),
        # applied here too since context quotes never pass through that
        # gate. `previous_close <= 0` is equally impossible and was only
        # partially guarded above (`== 0`, not `< 0`).
        if raw_ctx.last_price <= 0 or raw_ctx.previous_close < 0:
            return None
        return (Decimal(str(raw_ctx.last_price)) - Decimal(str(raw_ctx.previous_close))) / Decimal(str(raw_ctx.previous_close)) * Decimal(100)

    global_context = assess_global_context(
        nifty_day_change_pct=_context_day_change_pct("NIFTY"), bank_nifty_day_change_pct=_context_day_change_pct("BANKNIFTY"),
        india_vix_day_change_pct=_context_day_change_pct("VIX"), usdinr_day_change_pct=_context_day_change_pct("USDINR"),
        crude_oil_day_change_pct=_context_day_change_pct("CRUDE"), meaningful_move_pct=config.global_context_meaningful_move_pct,
    )
    report.global_context = global_context

    # Sprint 7B, Objective 1 -- market regime, from the SAME real macro
    # inputs above, zero new fetch (see market_regime_context.py's own
    # docstring for why USD/INR genuinely votes here but not in
    # global_context.py's own stock-tailwind verdict).
    report.market_regime_context = classify_macro_regime(
        nifty_day_change_pct=_context_day_change_pct("NIFTY"), bank_nifty_day_change_pct=_context_day_change_pct("BANKNIFTY"),
        india_vix_day_change_pct=_context_day_change_pct("VIX"), crude_oil_day_change_pct=_context_day_change_pct("CRUDE"),
        usdinr_day_change_pct=_context_day_change_pct("USDINR"), meaningful_move_pct=config.market_regime_meaningful_move_pct,
    )
    _measure("global_context", t)

    # Sprint 8, Objective P1 -- 3-level relative strength (STOCK vs
    # SECTOR vs MARKET), from the SAME real quote batch above, zero new
    # fetch. Structural context, never independent evidence -- see
    # sector_strength.py's own docstring; this never becomes an
    # EvidenceRow.
    if report.sector_info is not None:
        report.sector_relative_strength = classify_sector_relative_strength(
            sector_info=report.sector_info, stock_day_change_pct=report.day_change_pct,
            sector_day_change_pct=_context_day_change_pct("SECTOR"), market_day_change_pct=_context_day_change_pct("NIFTY"),
            meaningful_gap_pct=config.relative_strength_meaningful_gap_pct,
        )

    # Sprint 7B, Objectives 2/5/6 -- news event category/recency +
    # geopolitical/macro transmission, over the SAME real, already-
    # fetched/filtered news items -- zero new fetch.
    report.news_event_notes = [
        NewsEventNote(
            item=item, category=classify_news_event(item.title, item.summary),
            recency=classify_news_recency(published_at=item.published_at, as_of=as_of, intraday_within=config.news_intraday_within),
        )
        for item in report.news_items
    ]
    report.transmission_notes = [
        note for n in report.news_event_notes
        if (note := build_transmission_note(
            title=n.item.title, summary=n.item.summary, category=n.category,
            crude_day_change_pct=_context_day_change_pct("CRUDE"), usdinr_day_change_pct=_context_day_change_pct("USDINR"),
            vix_day_change_pct=_context_day_change_pct("VIX"), meaningful_move_pct=config.global_context_meaningful_move_pct,
        )) is not None
    ]

    # -- 13. evidence matrix -------------------------------------------------
    t = time.perf_counter()
    a = report.analysis
    assert a is not None  # guaranteed: we returned early above if data_state was INSUFFICIENT_HISTORY
    assert report.regime is not None

    atm_ce_leg = _find_leg(snapshot, strike=atm, right=OptionRight.CE) if atm is not None and snapshot is not None else None
    atm_pe_leg = _find_leg(snapshot, strike=atm, right=OptionRight.PE) if atm is not None and snapshot is not None else None
    atm_ce_liquidity = assess_liquidity(atm_ce_leg, as_of=as_of, max_quote_age=config.max_option_quote_age) if atm_ce_leg is not None else None
    atm_pe_liquidity = assess_liquidity(atm_pe_leg, as_of=as_of, max_quote_age=config.max_option_quote_age) if atm_pe_leg is not None else None
    liquidity_candidates = [x for x in (atm_ce_liquidity, atm_pe_liquidity) if x is not None]
    best_atm_liquidity = max(liquidity_candidates, key=lambda x: x.grade != LiquidityGrade.UNTRADEABLE, default=None)
    best_atm_grade = best_atm_liquidity.grade if best_atm_liquidity is not None else None

    is_fresh = data_state in (MarketDataState.LIVE_STREAMING, MarketDataState.LIVE_SNAPSHOT)
    session_vwap = compute_session_vwap_position(
        candles, instrument_id=ref.instrument_key, timeframe=Timeframe.M15, as_of=as_of,
    )
    rows = [
        row_m15_trend(ema_alignment=a.ema_alignment, ema_status=a.ema_status, data_is_current=candles_are_current),
        row_vwap(
            vwap_state=session_vwap.state, vwap_status=session_vwap.status,
            data_is_current=candles_are_current, session_vwap=True,
        ),
        row_market_regime(report.regime, data_is_current=candles_are_current),
        row_spot_futures_basis(spot=quote.last_price, futures_ltp=report.futures_ltp),
        row_futures_oi(futures_oi_observation or classify_price_oi(current_price=None, previous_price=None, current_oi=None, previous_oi=None, price_flat_threshold=config.futures_price_flat_threshold)),
        row_put_call_oi_structure(totals.put_call_ratio_oi),
        row_pcr_change(
            current_pcr=totals.put_call_ratio_oi, previous_pcr=previous_totals.put_call_ratio_oi if previous_totals else None,
            current_call_oi=totals.total_call_oi, previous_call_oi=previous_totals.total_call_oi if previous_totals else None,
            current_put_oi=totals.total_put_oi, previous_put_oi=previous_totals.total_put_oi if previous_totals else None,
            meaningful_pcr_change=config.meaningful_pcr_change,
            meaningful_oi_change_fraction=config.level_stability_meaningful_oi_change_fraction,
        ),
        row_volume(call_volume=totals.total_call_volume, put_volume=totals.total_put_volume, min_meaningful_volume=config.min_meaningful_option_volume),
        row_support(levels, near_pct_threshold=config.near_level_pct_threshold),
        row_resistance(levels, near_pct_threshold=config.near_level_pct_threshold),
        row_iv_level(report.iv_summary),
        row_iv_skew(
            report.iv_summary, meaningful_skew=config.meaningful_iv_skew,
            ce_liquidity_grade=atm_ce_liquidity.grade if atm_ce_liquidity is not None else None,
            pe_liquidity_grade=atm_pe_liquidity.grade if atm_pe_liquidity is not None else None,
        ),
        row_relative_strength(
            underlying_day_change_pct=report.day_change_pct, nifty_day_change_pct=_context_day_change_pct("NIFTY"),
            meaningful_gap_pct=config.relative_strength_meaningful_gap_pct,
        ),
        row_liquidity(best_atm_grade),
        row_data_freshness(is_fresh=is_fresh, detail=f"data state {data_state.value}, age {data_age.total_seconds():.0f}s"),
        row_global_context(global_context),
        row_news(report.news_items, fetch_error=report.news_fetch_error),
    ]
    if ce_oi_observation is not None:
        rows.append(row_change_in_oi(ce_oi_observation, right_label="ATM CE"))
    if pe_oi_observation is not None:
        rows.append(row_change_in_oi(pe_oi_observation, right_label="ATM PE"))
    rows = withhold_stale_stream_rows(
        rows,
        chain_is_current=chain_is_current,
        futures_are_current=futures_are_current,
        quote_is_current=quote_is_current,
    )
    matrix = EvidenceMatrix(rows=rows)
    report.matrix = matrix
    def _row_dir(name: str) -> EvidenceDirection:
        for r in matrix.rows:
            if r.name == name:
                return r.direction
        return EvidenceDirection.UNKNOWN
    hist = report.historical_structure
    pre_breakout = False
    if hist is not None and hist.status == HistoricalStructureStatus.OK and hist.multi_day_compression:
        near_structure = hist.near_lookback_extreme or spot_near_levels(
            quote.last_price,
            supports=[lv.strike for lv in report.support_levels],
            resistances=[lv.strike for lv in report.resistance_levels],
            max_pct=config.near_level_pct_threshold,
        )
        pre_breakout = near_structure
    # FAILED_BREAKDOWN_RECLAIM's own missing fact (this pattern had no
    # producer at all before `structural_reclaim` existed -- it was
    # documented but unreachable). The detector reports a DIRECTED
    # structural event; a BULLISH reclaim must not be narrated onto a
    # BEARISH thesis, so it is only offered to `classify_development()`
    # when its direction matches the evidence matrix's own convergence
    # bias -- the SAME `market_bias_from_convergence()` mapping stage 14
    # below already uses, never a second directional judgment. This gate
    # only ever WITHHOLDS the pattern; it can never create one.
    reclaim = report.structural_reclaim
    reclaim_supports_thesis = (
        reclaim is not None
        and reclaim.status == StructuralReclaimStatus.OK
        and reclaim.direction == market_bias_from_convergence(matrix.overall_convergence()).value
    )
    report.development = classify_development(
        convergence=matrix.overall_convergence(),
        chain_is_current=chain_is_current,
        candles_are_current=candles_are_current,
        futures_are_current=futures_are_current,
        quote_is_current=quote_is_current,
        ce_migration=report.ce_oi_migration,
        pe_migration=report.pe_oi_migration,
        rs_direction=_row_dir("Relative strength"),
        m15_direction=_row_dir("M15 trend"),
        basis_change=report.futures_basis_change,
        pre_breakout_compression=pre_breakout,
        failed_breakdown_reclaim=reclaim_supports_thesis,
        sector_rs_tier=(
            report.sector_relative_strength.tier.value if report.sector_relative_strength is not None else None
        ),
    )
    _measure("evidence_matrix", t)

    # -- 13b. adversarial analysis (Phase 9) -- pure synthesis of `matrix`,
    # no new data; runs regardless of which side (if any) the bias gate later
    # picks, so it can answer "what would make the opposite trade correct?"
    t = time.perf_counter()
    report.adversarial_analysis = build_adversarial_analysis(matrix)
    _measure("adversarial_analysis", t)

    # -- 14. candidates -------------------------------------------------
    t = time.perf_counter()
    bias = market_bias_from_convergence(matrix.overall_convergence())
    supporting = [f"{r.name}: {r.detail}" for r in matrix.voting_rows(bias)]
    opposite = EvidenceDirection.BEARISH if bias == EvidenceDirection.BULLISH else EvidenceDirection.BULLISH
    contradicting = [f"{r.name}: {r.detail}" for r in matrix.voting_rows(opposite)]
    nearest_support = min((lv.strike for lv in report.support_levels), key=lambda s: abs(s - (quote.last_price)), default=None)
    nearest_resistance = min((lv.strike for lv in report.resistance_levels), key=lambda s: abs(s - (quote.last_price)), default=None)
    # Surface the same numeric level `generate_candidates()` uses internally
    # to build its `invalidation_condition` text -- needed by the audit
    # journal (Milestone G) to check, at a later checkpoint, whether price
    # actually breached it. Not a new decision rule, just exposing an
    # already-computed number.
    if bias == EvidenceDirection.BULLISH:
        report.invalidation_level = nearest_support
    elif bias == EvidenceDirection.BEARISH:
        report.invalidation_level = nearest_resistance
    candidates = (
        generate_candidates(
            snapshot, bias=bias, supporting_evidence=supporting, contradicting_evidence=contradicting,
            strikes_each_side=config.strikes_each_side, as_of=as_of, max_quote_age=config.max_option_quote_age,
            min_liquidity_grade=config.min_candidate_liquidity_grade, nearest_support_strike=nearest_support,
            nearest_resistance_strike=nearest_resistance,
        )
        if snapshot is not None
        # Phase 3 gap-closure -- no chain, so no contract can genuinely be
        # selected. Never invented: the exact same "no candidates" shape a
        # live chain with zero acceptable-liquidity legs would also
        # produce, which `build_quality_assessment()` already handles
        # (`assess_option_quality([])`/`assess_liquidity_quality([])` ->
        # `QualityLevel.INSUFFICIENT`).
        else []
    )
    report.candidates = candidates
    _measure("candidates", t)

    # -- 14b. decay viability (Stage 8 -- explicit "major priority") --------
    t = time.perf_counter()
    for candidate in candidates:
        viability = assess_decay_viability(
            strike=candidate.strike, right=candidate.right, spot=quote.last_price, expiry=expiry_info.expiry, as_of=as_of,
            implied_volatility=candidate.implied_volatility, delta=candidate.delta, gamma=candidate.gamma,
            vega=candidate.vega, theta=candidate.theta, ltp=candidate.ltp, bid=candidate.bid, ask=candidate.ask,
            holding_horizon_hours=config.decay_viability_holding_horizon_hours,
            iv_scenario_points=config.decay_viability_iv_scenario_points,
            favorable_min_ratio=config.decay_viability_favorable_min_ratio,
            acceptable_min_ratio=config.decay_viability_acceptable_min_ratio,
            headwind_min_ratio=config.decay_viability_headwind_min_ratio,
        )
        report.decay_viability.append(viability)
    _measure("decay_viability", t)

    # -- 14c. requested contract (Sprint 2) -- ALWAYS analyzed when the
    # caller asked for one, regardless of `bias` above: the master
    # directive is explicit that a requested contract must never be
    # skipped merely because the overall evidence-matrix bias points the
    # other way (see `contract_analysis.py` module docstring).
    t = time.perf_counter()
    if requested_strike is not None and requested_right is not None and snapshot is not None:
        report.requested_contract = compare_contracts(
            snapshot, requested_strike=requested_strike, requested_right=requested_right,
            strikes_each_side=config.strikes_each_side, expiry=expiry_info.expiry, as_of=as_of,
            max_quote_age=config.max_option_quote_age, support_levels=report.support_levels,
            resistance_levels=report.resistance_levels,
            decay_viability_holding_horizon_hours=config.decay_viability_holding_horizon_hours,
            decay_viability_iv_scenario_points=config.decay_viability_iv_scenario_points,
            decay_viability_favorable_min_ratio=config.decay_viability_favorable_min_ratio,
            decay_viability_acceptable_min_ratio=config.decay_viability_acceptable_min_ratio,
            decay_viability_headwind_min_ratio=config.decay_viability_headwind_min_ratio,
        )
    elif requested_strike is not None and requested_right is not None and snapshot is None:
        report.data_warnings.append("a specific contract was requested but no historical option chain is available to assess it")
    _measure("requested_contract", t)

    # -- 14d. direction-neutral CE/PE comparison (Sprint 4, Parts D-K) --
    # `reference_strike` is an ANCHOR, never a preference: the user's
    # requested strike if one was given, otherwise ATM. Built and analyzed
    # for BOTH rights regardless of `bias` above -- this never feeds, and
    # is never fed by, `decide()` (see direction_analysis.py docstring).
    t = time.perf_counter()
    direction_reference_strike = requested_strike if requested_strike is not None else atm
    if direction_reference_strike is not None and report.adversarial_analysis is not None and snapshot is not None:
        report.direction_comparison = build_direction_comparison(
            snapshot, matrix=matrix, adversarial=report.adversarial_analysis, reference_strike=direction_reference_strike,
            expiry=expiry_info.expiry, as_of=as_of, max_quote_age=config.max_option_quote_age,
            strikes_each_side=config.strikes_each_side, support_levels=report.support_levels,
            resistance_levels=report.resistance_levels, level_stability=report.level_stability,
            min_supporting_rows_for_strong=config.direction_min_supporting_rows_for_strong,
            decay_viability_holding_horizon_hours=config.decay_viability_holding_horizon_hours,
            decay_viability_iv_scenario_points=config.decay_viability_iv_scenario_points,
            decay_viability_favorable_min_ratio=config.decay_viability_favorable_min_ratio,
            decay_viability_acceptable_min_ratio=config.decay_viability_acceptable_min_ratio,
            decay_viability_headwind_min_ratio=config.decay_viability_headwind_min_ratio,
        )
    _measure("direction_comparison", t)

    # -- 15. decision -------------------------------------------------------
    t = time.perf_counter()
    assessment = build_quality_assessment(
        matrix=matrix, regime=report.regime.regime, candidates=candidates, chain_issues=report.chain_quality_issues,
        underlying_data_state_is_ok=data_state not in (MarketDataState.INSUFFICIENT_HISTORY, MarketDataState.ERROR, MarketDataState.PROVIDER_UNAVAILABLE),
        iv_rank=report.iv_rank,
        price_consistency=report.price_consistency.classification if report.price_consistency is not None else None,
    )
    report.decision = decide(assessment, derivatives_evidence_available=report.derivatives_history_available)
    report.quality_tiers = summarize_quality_tiers(report.decision)
    report.research_state = derive_research_state(
        decision=report.decision, candles_are_current=candles_are_current, day_change_pct=report.day_change_pct,
        chain_is_current=chain_is_current, quote_is_current=quote_is_current,
        development_pattern=report.development.pattern.value if report.development is not None else None,
    )
    report.blockers = determine_blockers(
        decision=report.decision, candles_are_current=candles_are_current, chain_is_current=chain_is_current,
        quote_is_current=quote_is_current, day_change_pct=report.day_change_pct, development=report.development,
        historical_insufficient=(
            report.historical_structure is not None
            and report.historical_structure.status == HistoricalStructureStatus.INSUFFICIENT_HISTORY
        ),
        derivatives_evidence_available=report.derivatives_history_available,
    )
    _measure("decision", t)

    await _attach_cash_context(
        report, provider=provider, instrument_master=instrument_master, symbol=ref.trading_symbol,
        as_of=as_of, exchange_status=exchange_status, nifty50_symbols=nifty50_symbols,
        http_client=http_client, delivery_cache_dir=delivery_cache_dir,
        fii_cash_net=fii_cash_net, dii_cash_net=dii_cash_net, index_fo_net=index_fo_net,
        fii_dii_as_of=fii_dii_as_of, fii_dii_source=fii_dii_source,
    )
    report.stream_freshness = _assemble_stream_freshness(
        report, quote_retrieved_at=as_of, candles_are_current=candles_are_current,
        last_candle=last_candle, snapshot=snapshot, quote=quote,
        chain_is_current=chain_is_current, futures_are_current=futures_are_current,
    )
    # Sprint 3.2 -- Historical Validation Contract. Derived from the SAME
    # already-established facts (and the matrix's own group verdicts) the
    # stream-freshness list above is; nothing here is fetched or re-derived.
    report.evidence_availability = assess_evidence_availability(
        price_history_sufficient=a.ema_status == IndicatorStatus.OK,
        candles_present=bool(candles),
        candles_are_current=candles_are_current,
        market_context_present=(
            report.global_context is not None
            and report.global_context.verdict != GlobalContextVerdict.INSUFFICIENT_DATA
        ),
        chain_present=snapshot is not None,
        chain_is_current=chain_is_current,
        provider_has_chain_history=provider.capabilities.historical_option_chain,
        futures_present=report.futures_ltp is not None,
        futures_are_current=futures_are_current,
        provider_has_futures_history=provider.capabilities.historical_futures,
        news_fetch_failed=report.news_fetch_error is not None,
        provider_has_news_history=provider.capabilities.historical_news,
        matrix=report.matrix,
    )

    if report.chain_quality_issues:
        report.data_warnings.extend(f"chain quality: {i.kind.value} -- {i.detail}" for i in report.chain_quality_issues)

    report.stage_latencies = stage_latencies
    report.total_latency_seconds = time.perf_counter() - total_started
    return report


def _find_leg(snapshot: OptionChainSnapshot, *, strike: Decimal, right: OptionRight) -> OptionQuote | None:
    for leg in snapshot.legs:
        if leg.strike == strike and leg.right == right:
            return leg
    return None


def _assemble_stream_freshness(
    report: OptionsIntelligenceReport,
    *,
    quote_retrieved_at: datetime,
    candles_are_current: bool,
    last_candle: Candle | None,
    snapshot: OptionChainSnapshot | None,
    quote: Quote,
    chain_is_current: bool,
    futures_are_current: bool,
) -> list[StreamFreshness]:
    quote_label = classify_freshness_label(
        data_state=report.data_state, data_age_seconds=report.data_age_seconds,
        has_quality_issues=False, recent_max_age_seconds=60.0,
    )
    streams = [
        stream_freshness(
            stream=DataStream.UNDERLYING_QUOTE, label=quote_label,
            data_timestamp=quote.freshness.data_timestamp, retrieved_at=quote.freshness.received_timestamp,
            session=report.data_state.value, source="upstox /v2/market-quote/quotes",
            blocks_entire_report=report.data_state
            in (MarketDataState.INSUFFICIENT_HISTORY, MarketDataState.ERROR, MarketDataState.PROVIDER_UNAVAILABLE),
            usable_for_vote=quote_label not in (FreshnessLabel.STALE, FreshnessLabel.UNAVAILABLE, FreshnessLabel.ERROR),
            completeness="STALE" if quote_label == FreshnessLabel.STALE else "COMPLETE",
            detail="underlying LTP / previous close / day OHLC from the live quote",
        )
    ]
    if not candles_are_current:
        streams.append(stream_freshness(
            stream=DataStream.CANDLES_M15, label=FreshnessLabel.STALE,
            data_timestamp=last_candle.freshness.data_timestamp if last_candle is not None else None,
            retrieved_at=quote_retrieved_at, session="PREVIOUS_SESSION", source="upstox historical M15",
            blocks_entire_report=False,
            withheld_calculations=("m15_trend", "rolling_vwap_position", "intraday_regime", "breakout_confirmation"),
            usable_for_vote=False, completeness="STALE",
            detail="M15 = STALE -- technical confirmation pending; live quote/chain are still usable",
        ))
    elif last_candle is None:
        streams.append(stream_freshness(
            stream=DataStream.CANDLES_M15, label=FreshnessLabel.UNAVAILABLE,
            retrieved_at=quote_retrieved_at, source="upstox historical M15",
            withheld_calculations=("m15_trend", "rolling_vwap_position"),
            usable_for_vote=False, completeness="MISSING",
            detail="no M15 candles this run",
        ))
    else:
        streams.append(stream_freshness(
            stream=DataStream.CANDLES_M15, label=FreshnessLabel.LIVE,
            data_timestamp=last_candle.freshness.data_timestamp, retrieved_at=quote_retrieved_at,
            session=report.data_state.value, source="upstox historical M15",
            detail="latest M15 candle is current enough for technical evidence",
        ))

    if snapshot is None:
        streams.append(stream_freshness(
            stream=DataStream.OPTION_CHAIN, label=FreshnessLabel.UNAVAILABLE, retrieved_at=quote_retrieved_at,
            source="upstox /v2/option/chain",
            # Phase 3 gap-closure -- `snapshot is None` reaching this far
            # (past the fatal early-return stage 8 still uses for a real
            # live failure) means the provider structurally never had
            # chain history for this instant -- distinct from "unavailable
            # because a live fetch just failed" (that path never reaches
            # here at all; see the option-chain stage's own comment).
            # Never `blocks_entire_report=True` here: the rest of this
            # report (price/technical evidence, research_state) is
            # genuinely usable.
            blocks_entire_report=False, usable_for_vote=False, completeness="MISSING",
            detail=(
                "historical option-chain evidence unavailable for this instant (replay)"
                if not report.derivatives_history_available
                else "option chain not available"
            ),
        ))
    else:
        streams.append(stream_freshness(
            stream=DataStream.OPTION_CHAIN,
            label=FreshnessLabel.STALE if not chain_is_current else FreshnessLabel.LIVE,
            data_timestamp=snapshot.freshness.data_timestamp, retrieved_at=snapshot.freshness.received_timestamp,
            session=report.data_state.value, source="upstox /v2/option/chain",
            withheld_calculations=(
                ("chain_oi", "pcr", "option_volume", "iv", "atm_liquidity", "chain_support_resistance")
                if not chain_is_current else ()
            ),
            usable_for_vote=chain_is_current, completeness="PARTIAL" if not chain_is_current else "COMPLETE",
            detail=(
                "option-chain as_of is HTTP receipt time, not an exchange matching-engine timestamp "
                "(per-leg exchange timestamps are not provided by this producer)"
                + ("; chain-derived evidence withheld" if not chain_is_current else "")
            ),
        ))

    if report.futures_instrument_key is None:
        streams.append(stream_freshness(
            stream=DataStream.FUTURES, label=FreshnessLabel.NOT_APPLICABLE, retrieved_at=quote_retrieved_at,
            detail="no matching futures contract for this expiry",
        ))
    elif report.futures_ltp is None:
        streams.append(stream_freshness(
            stream=DataStream.FUTURES, label=FreshnessLabel.UNAVAILABLE, retrieved_at=quote_retrieved_at,
            source="upstox quotes", detail="futures key resolved but no usable quote",
        ))
    else:
        streams.append(stream_freshness(
            stream=DataStream.FUTURES,
            label=FreshnessLabel.LIVE if futures_are_current else FreshnessLabel.STALE,
            data_timestamp=report.futures_timestamp,
            retrieved_at=quote_retrieved_at, source="upstox quotes",
            withheld_calculations=("spot_futures_basis", "futures_oi") if not futures_are_current else (),
            usable_for_vote=futures_are_current,
            completeness="STALE" if not futures_are_current else "COMPLETE",
            detail="futures LTP/OI" + ("; futures evidence withheld" if not futures_are_current else ""),
        ))

    streams.append(stream_freshness(
        stream=DataStream.MACRO,
        label=FreshnessLabel.LIVE if report.global_context is not None else FreshnessLabel.UNKNOWN,
        retrieved_at=quote_retrieved_at, source="upstox index/derivative quotes",
        detail="NIFTY/BANKNIFTY/VIX (and optional USDINR/crude) day-change context",
    ))
    if report.news_fetch_error:
        news_label, news_detail = FreshnessLabel.ERROR, f"news fetch failed: {report.news_fetch_error}"
    elif report.news_items:
        news_label, news_detail = FreshnessLabel.LIVE, f"{len(report.news_items)} instrument-scoped headline(s)"
    else:
        news_label, news_detail = FreshnessLabel.UNKNOWN, "no headlines this run"
    streams.append(stream_freshness(
        stream=DataStream.NEWS, label=news_label, retrieved_at=quote_retrieved_at, source="upstox /v2/news",
        detail=news_detail,
    ))
    streams.append(stream_freshness(
        stream=DataStream.SECTOR_MAP,
        label=FreshnessLabel.LIVE if report.sector_info is not None else FreshnessLabel.UNKNOWN,
        retrieved_at=quote_retrieved_at, source="NSE Nifty 500 constituent list",
        detail=report.sector_info.source if report.sector_info is not None else "sector map not supplied this run",
    ))
    if report.sample_breadth is None:
        streams.append(stream_freshness(
            stream=DataStream.SAMPLE_BREADTH, label=FreshnessLabel.UNKNOWN, retrieved_at=quote_retrieved_at,
            usable_for_vote=False, completeness="UNKNOWN",
            detail="sample breadth not computed this run",
        ))
    else:
        b = report.sample_breadth
        streams.append(stream_freshness(
            stream=DataStream.SAMPLE_BREADTH,
            label=FreshnessLabel.LIVE if b.coverage.value in {"COMPLETE", "PARTIAL"} else FreshnessLabel.UNKNOWN,
            data_timestamp=b.as_of, retrieved_at=b.retrieved_at, session=b.session, source=b.source,
            usable_for_vote=False, completeness=b.coverage.value,
            detail=b.detail,
        ))
    if report.delivery is None:
        streams.append(stream_freshness(
            stream=DataStream.DELIVERY, label=FreshnessLabel.UNKNOWN, retrieved_at=quote_retrieved_at,
            usable_for_vote=False, completeness="UNKNOWN",
            detail="delivery not assessed this run",
        ))
    else:
        d = report.delivery
        label = {
            DeliveryFreshness.EOD: FreshnessLabel.EOD,
            DeliveryFreshness.PREVIOUS_SESSION: FreshnessLabel.EOD,
            DeliveryFreshness.UNKNOWN: FreshnessLabel.UNKNOWN,
            DeliveryFreshness.UNAVAILABLE: FreshnessLabel.UNAVAILABLE,
        }[d.session]
        streams.append(stream_freshness(
            stream=DataStream.DELIVERY, label=label, retrieved_at=d.retrieved_at,
            session=d.session.value, source=d.source, usable_for_vote=False, completeness="EOD",
            detail=d.detail,
        ))
    if report.institutional_flows is None:
        streams.append(stream_freshness(
            stream=DataStream.FII_DII, label=FreshnessLabel.UNKNOWN, retrieved_at=quote_retrieved_at,
            usable_for_vote=False, completeness="UNKNOWN",
            detail="FII/DII not assessed this run",
        ))
    else:
        f = report.institutional_flows
        fii_label = FreshnessLabel.UNKNOWN if f.availability == FlowAvailability.UNKNOWN else FreshnessLabel.EOD
        streams.append(stream_freshness(
            stream=DataStream.FII_DII, label=fii_label,
            retrieved_at=f.retrieved_at, source=f.source, usable_for_vote=False, completeness="CALLER_OR_UNKNOWN",
            detail=f.detail,
        ))
    return streams


async def _attach_cash_context(
    report: OptionsIntelligenceReport,
    *,
    provider: AnalysisProvider,
    instrument_master: Sequence[dict[str, object]],
    symbol: str,
    as_of: datetime,
    exchange_status: ExchangeStatus,
    nifty50_symbols: Sequence[str] | None,
    http_client: httpx.AsyncClient | None,
    delivery_cache_dir: Path | None,
    fii_cash_net: Decimal | None,
    dii_cash_net: Decimal | None,
    index_fo_net: Decimal | None,
    fii_dii_as_of: date | None,
    fii_dii_source: str | None,
) -> None:
    """Optional cash confirmation -- never raises into the options path."""
    if fii_dii_source and fii_dii_as_of is not None and (
        fii_cash_net is not None or dii_cash_net is not None or index_fo_net is not None
    ):
        report.institutional_flows = caller_supplied_institutional_flows(
            fii_cash_net=fii_cash_net, dii_cash_net=dii_cash_net, index_fo_net=index_fo_net,
            as_of_date=fii_dii_as_of, source=fii_dii_source, retrieved_at=as_of,
        )
    else:
        report.institutional_flows = unknown_institutional_flows(retrieved_at=as_of)

    live_session = exchange_status == ExchangeStatus.NORMAL_OPEN
    if nifty50_symbols:
        try:
            report.sample_breadth = await _compute_sample_breadth(
                provider, instrument_master, tuple(nifty50_symbols), as_of=as_of,
                session="LIVE_SESSION" if live_session else "MARKET_CLOSED",
            )
        except Exception as exc:  # noqa: BLE001 -- cash context must not block options
            report.data_warnings.append(f"sample breadth unavailable: {exc}")

    if http_client is None:
        report.delivery = unknown_delivery(symbol=symbol, retrieved_at=as_of, reason="no HTTP client for NSE archive")
        return
    try:
        as_of_date = as_of.date()
        if live_session:
            trade_date = most_recent_trading_day_at_or_before(as_of_date - timedelta(days=1))
            session = DeliveryFreshness.PREVIOUS_SESSION
        elif is_trading_day(as_of_date):
            trade_date = as_of_date
            session = DeliveryFreshness.EOD
        else:
            trade_date = most_recent_trading_day_at_or_before(as_of_date)
            session = DeliveryFreshness.PREVIOUS_SESSION
        report.delivery = await fetch_delivery_observation(
            http_client, symbol=symbol, trade_date=trade_date, retrieved_at=as_of, session=session,
            cache_dir=delivery_cache_dir,
        )
    except Exception as exc:  # noqa: BLE001
        report.delivery = unknown_delivery(
            symbol=symbol, retrieved_at=as_of, reason=f"delivery archive unavailable: {exc}",
        )


async def _compute_sample_breadth(
    provider: AnalysisProvider,
    instrument_master: Sequence[dict[str, object]],
    symbols: tuple[str, ...],
    *,
    as_of: datetime,
    session: str,
) -> SampleBreadthResult:
    keys: list[str] = []
    key_to_symbol: dict[str, str] = {}
    for sym in symbols:
        resolved = resolve_symbol(instrument_master, sym)
        if resolved is None:
            continue
        keys.append(resolved.instrument_key)
        key_to_symbol[resolved.instrument_key] = sym
    day_change: dict[str, Decimal | None] = {sym: None for sym in symbols}
    if keys:
        raw_quotes = await provider.get_quotes(keys)
        for key, raw in raw_quotes.items():
            mapped = key_to_symbol.get(key)
            if mapped is None or not isinstance(raw, RawQuote):
                continue
            if raw.previous_close is None or raw.previous_close <= 0 or raw.last_price <= 0:
                continue
            day_change[mapped] = (
                (Decimal(str(raw.last_price)) - Decimal(str(raw.previous_close)))
                / Decimal(str(raw.previous_close)) * Decimal(100)
            )
    return classify_sample_breadth(
        universe="Nifty 50",
        source="NSE official ind_nifty50list.csv + Upstox quotes",
        expected_symbols=symbols, day_change_pct_by_symbol=day_change, as_of=as_of,
        retrieved_at=as_of, session=session,
    )
