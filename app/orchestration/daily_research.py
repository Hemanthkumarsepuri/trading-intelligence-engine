"""Daily Market Researcher — scans a defined universe, reuses the EXISTING
analytical pipeline (`run_analysis()` -> `analyze_symbol()`, unmodified)
once per symbol, and ranks the results into a transparent, explainable
shortlist of 2-3 (or fewer, or zero) opportunities for the day.

This module performs NO analysis of its own. Every field it reads
(`direction_comparison.verdict`, `.preferred_contract`,
`ContractAssessmentView.structural_quality/liquidity_grade/decay_verdict/
required_underlying_move_pct/contractual_expiry_breakeven`,
`support_resistance`, `freshness`) was already computed by the same
pipeline a manual single-symbol query uses. Ranking here is pure
selection/sorting/synthesis over that already-computed evidence -- never
a second analytical truth, never a new score fed back into the decision
engine, never an override of `FinalDecision`.

=== WHY EACH RANKING AXIS IS INCLUDED (and what is deliberately excluded
to avoid double-counting correlated evidence) ===
See the "Ranking methodology" table in the approved implementation plan.
In short: `direction_comparison.verdict` already summarizes every real
evidence group (price action, OI, IV, futures, GLOBAL context -- see
`row_global_context()` in evidence_matrix.py, itself one row inside that
same matrix), so global/price-action/OI are never separately re-scored.
`liquidity_grade` already summarizes spread + volume + OI level (see
`assess_liquidity()` in liquidity.py's own thresholds), so those three
are never separately re-scored. `structural_quality` is a derived gate,
not an independent axis. Only genuinely independent, non-derived
real fields become tie-break axes: liquidity_grade, decay_verdict,
required_underlying_move_pct, breakeven distance, and S/R headroom
(S/R rows never vote directionally in the matrix, so proximity to a
real level is new information, not a restatement of `verdict`).
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from pydantic import BaseModel, Field

from app.data.normalization.base import _MAX_CLOCK_SKEW_TOLERANCE
from app.data.providers.base import BatchedQuoteProvider, RawQuote
from app.data.providers.exceptions import ProviderError
from app.data.providers.scan_snapshot_cache import ScanSnapshotCache
from app.data.providers.upstox_fo_master import nearest_expiry
from app.data.providers.upstox_instrument_master import resolve_symbol
from app.data.providers.upstox_provider import ExchangeStatus
from app.domain.audit.research_models import (
    RankingRationale,
    RejectionRecord,
    ResearchCoverage,
    ResearchObservation,
    ResearchRunRecord,
    ResearchScanSnapshot,
    ShortlistRecord,
    SymbolFreshnessRecord,
    new_run_id,
)
from app.domain.market.trading_calendar import most_recent_trading_day_at_or_before
from app.domain.options.early_opportunity import (
    FNO_BAN_STATUS_UNKNOWN,
    UNIVERSE_SOURCE_UPSTOX_NSE_FO_EQUITY,
    ResearchBucket,
    classify_research_bucket,
    is_early_opportunity_bucket,
    is_event_to_monitor_bucket,
)
from app.domain.options.plain_language import (
    contract_usability_plain_english,
    happening_plain_english,
)
from app.domain.options.stage1_discovery import (
    DEVELOPING_MOMENTUM,
    EARLY_REVERSAL,
    FAILED_BREAKDOWN_RECLAIM_CANDIDATE,
    INTRADAY_COMPRESSION,
    NEAR_SESSION_BOUNDARY,
    ORDER_FLOW_PARTICIPATION,
    PRE_BREAKOUT_COMPRESSION_CANDIDATE,
    RELATIVE_STRENGTH_VS_INDEX,
    assert_no_stage1_option_claims,
    promotion_reason,
)
from app.orchestration.dashboard_service import AnalyzeResponse, run_analysis
from app.orchestration.options_intelligence_pipeline import PipelineConfig, Repositories, _retry
from app.orchestration.visual_data import (
    CandlePoint,
    ContractAssessmentView,
    DirectionComparisonVisual,
    LevelView,
    VisualData,
)
from app.utils.time import ensure_utc, utc_now

if TYPE_CHECKING:
    from app.data.providers.upstox_provider import UpstoxProvider
    from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy
    from app.persistence.interfaces import ResearchOutcomeRepository, ResearchRunRepository
    from app.persistence.jsonl_file import JsonlAuditJournalRepository

# `is_fo_eligible()`/`list_expiries()` (app.data.providers.upstox_fo_master)
# confirm a real F&O-eligible universe of ~29,713 NSE_FO instrument rows is
# reachable from the already-loaded `instrument_master` -- but scanning
# every one of the ~180+ distinct underlyings that implies, sequentially,
# through the FULL heavy pipeline (quote+candles+chain+futures+global+
# news per symbol), is not a "daily" operation with this codebase's
# current rate-limit-safe sequential design (confirmed: no
# asyncio.gather/concurrency anywhere multi-symbol code exists, and
# UpstoxProvider has no built-in throttling of its own -- see
# `run_watchlist()`). A full-universe scan is real future work
# (`list_fo_eligible_underlyings()` below makes it possible), not
# invented today. The DEFAULT universe is the small, explicit set this
# whole project's live UAT has already exercised.
DEFAULT_RESEARCH_UNIVERSE: tuple[str, ...] = ("GAIL", "RELIANCE", "NIFTY", "BANKNIFTY", "UNOMINDA", "INDIGO")

_STALE_MARKET_STATES = {"STALE_DATA", "INSUFFICIENT_HISTORY", "PROVIDER_UNAVAILABLE", "ERROR"}

# Lower is better in every ordinal map below -- `rank_candidates()` builds
# one ascending sort key per candidate, so "lower sorts first" uniformly
# means "better sorts first." A missing/unrecognized value maps to the
# worst tier -- never omitted in a way that could accidentally rank it
# ahead of a candidate with real, known evidence.
_DECISION_TIER = {"TRADEABLE": 0, "WATCH": 1, "NO_TRADE": 2, "DATA_INSUFFICIENT": 3}
_LIQUIDITY_ORDINAL = {"excellent": 0, "good": 1, "poor": 2, "untradeable": 3}
_DECAY_ORDINAL = {"DECAY_FAVORABLE": 0, "DECAY_ACCEPTABLE": 1, "DECAY_HEADWIND": 2, "DECAY_UNFAVORABLE": 3}
_WORST_DECISION_TIER = 4
_WORST_LIQUIDITY = 4
_WORST_DECAY = 4
_WORST_PCT = Decimal("999999")  # sentinel: worse than any real percentage this product will ever compute


def list_fo_eligible_underlyings(instrument_master: Sequence[dict[str, object]]) -> list[str]:
    """Every distinct real `underlying_symbol` with at least one real
    `NSE_FO` contract in the already-loaded instrument master -- the
    honest full universe, exposed for a caller that explicitly wants a
    wider (slower, more rate-limit-exposed) scan than
    `DEFAULT_RESEARCH_UNIVERSE`. Never invented: reads only real fields
    already present on every instrument-master row."""
    symbols = {
        str(entry["underlying_symbol"])
        for entry in instrument_master
        if entry.get("segment") == "NSE_FO" and entry.get("underlying_symbol")
    }
    return sorted(symbols)


def list_fo_eligible_equity_underlyings(instrument_master: Sequence[dict[str, object]]) -> list[str]:
    """The real F&O-eligible EQUITY universe -- `list_fo_eligible_underlyings()`
    with real index underlyings excluded (Objective 1: indices stay
    analyzable via the `?symbols=` override, but never occupy an equity
    research slot). An underlying is identified as an index by real
    cross-reference against the SAME instrument master's `NSE_INDEX` rows
    (`trading_symbol`, the exact field `resolve_symbol()` already matches
    index queries against -- see `upstox_instrument_master.py`), never a
    hardcoded index name list. Confirmed live 2026-08-31: 216 real
    NSE_FO underlyings, 6 of them real indices (BANKNIFTY, FINNIFTY,
    MIDCPNIFTY, NIFTY, NIFTYFPI, NIFTYNXT50), leaving 210 real equities,
    every one with a matching real NSE_EQ row."""
    index_symbols = {
        str(entry["trading_symbol"]).strip().upper()
        for entry in instrument_master
        if entry.get("segment") == "NSE_INDEX" and entry.get("trading_symbol")
    }
    return [s for s in list_fo_eligible_underlyings(instrument_master) if s.strip().upper() not in index_symbols]


# ============================================================
# Stage 1 -- EARLY-STAGE DISCOVERY, not a momentum-chaser. HONESTY NOTE
# (the STOP-CONDITION check the approved plan required): `get_quotes()`
# is the ONLY batchable real-time endpoint in this codebase's Upstox
# integration (one real HTTP call for many comma-separated instrument
# keys). `/v3/historical-candle/...` and `/v2/option/chain` remain
# per-instrument-key, not batchable -- so multi-day range-compression
# detection, a historical-volume baseline, relative-strength-vs-index,
# and options activity CANNOT be cheaply pre-screened; they stay
# Stage-2-only, by architectural necessity, not an oversight. What IS
# genuinely cheap (confirmed live 2026-08-31): Upstox's real quote
# response already carries `ohlc` (today's own real open/high/low/close)
# and `total_buy_quantity`/`total_sell_quantity`, on the SAME batched
# call this module already makes -- see `RawOHLC`
# (app/data/providers/base.py). This lets Stage 1 tell whether today's
# move has already run far (pinned near today's high/low, far from the
# day's own VWAP-equivalent `average_price`) rather than just how big
# the move or the volume number is -- the whole point of NOT chasing the
# biggest movers.
# ============================================================


@dataclass(frozen=True)
class ScreeningConfig:
    """Named, documented Stage-1 thresholds -- mirrors `PipelineConfig`'s
    style (explicit, undefaulted at the point of use, never a magic
    number). `survivor_cap` is a target, not a quota: fewer symbols
    genuinely qualifying simply yields fewer survivors -- never padded."""

    quote_batch_size: int = 50  # chunk size for get_quotes() -- stays safely under any undocumented Upstox batch limit
    stale_quote_tolerance: timedelta = timedelta(minutes=15)
    survivor_cap: int = 30

    # Sprint 1 -- real live defect found 2026-08-31: ~100/210 symbols lost
    # to a real batched `/v2/market-quote/quotes` timeout, with ZERO retry
    # anywhere in this path (confirmed by reading `UpstoxProvider._get()`
    # and this function, below) -- one transient timeout on a 50-symbol
    # chunk permanently drops all 50 symbols. Reuses the EXACT SAME bounded
    # linear-backoff `_retry()` helper `analyze_symbol()`'s option-chain
    # fetch already relies on (`options_intelligence_pipeline.py`) --
    # never a second retry mechanism -- and the same real transient/
    # non-transient exception split (`ProviderTimeout`/`ProviderUnavailable`/
    # `ProviderRateLimited` retried; `ProviderMalformedResponse` never is,
    # since retrying a genuinely bad response cannot help). 2 attempts (1
    # retry), not 3 like the chain fetch: this call already covers up to
    # `quote_batch_size` symbols at once, so a retry here is inherently
    # more expensive per attempt than a single-symbol chain retry --
    # deliberately conservative, not "increase retries aggressively."
    quote_batch_attempts: int = 2
    quote_batch_backoff_seconds: float = 1.5

    # Bucket A -- DEVELOPING MOMENTUM: a real move, but not yet extreme,
    # while price is still close to its own real day VWAP-equivalent
    # (`average_price`) -- i.e. hasn't run away from the day's own
    # volume-weighted average.
    momentum_min_day_change_pct: Decimal = Decimal("0.5")
    momentum_max_day_change_pct: Decimal = Decimal("4.0")
    momentum_max_vwap_distance_pct: Decimal = Decimal("1.5")

    # Bucket B -- ORDER-FLOW PARTICIPATION: a real buy/sell quantity
    # imbalance (either direction) beyond this ratio.
    participation_min_imbalance_ratio: Decimal = Decimal("1.3")

    # Bucket C -- EARLY REVERSAL: `day_change_pct`'s sign disagrees with
    # where price currently sits in today's own real range -- e.g. red
    # on the day but already well off today's low (recovering), or green
    # on the day but pulled back well off today's high (fading early).
    reversal_min_position_when_red: Decimal = Decimal("0.6")
    reversal_max_position_when_green: Decimal = Decimal("0.4")

    # Hard exclusion -- ALREADY EXTENDED INTRADAY: a large move AND
    # pinned near the extreme of today's own range in that direction.
    # Deliberately a HIGHER bar than the momentum bucket's max, and
    # checked first/independently -- this is what actually prevents
    # "biggest mover of the day" from being treated as early-stage.
    extended_min_day_change_pct: Decimal = Decimal("6.0")
    extended_min_position_when_up: Decimal = Decimal("0.9")
    extended_max_position_when_down: Decimal = Decimal("0.1")
    # Unpinned large session move -- already late even if not at the extreme.
    large_unpinned_move_pct: Decimal = Decimal("8.0")

    # Cheap compression proxy from TODAY'S quote OHLC only -- not multi-day
    # range compression and not a Stage-2 PRE_BREAKOUT_COMPRESSION claim.
    compression_max_session_range_pct: Decimal = Decimal("1.5")
    near_boundary_min_position: Decimal = Decimal("0.75")
    near_boundary_max_position: Decimal = Decimal("0.25")
    # Stock vs Nifty day-change gap (percentage points). Missing index
    # quote means this observation is simply not emitted.
    relative_strength_min_gap_pct: Decimal = Decimal("0.75")


_DEFAULT_SCREENING_CONFIG = ScreeningConfig()


# Sprint 3 -- `_most_recent_trading_day_at_or_before()` used to live here
# as a Stage-1-local helper (Sprint 2); now promoted to the shared,
# authoritative `app.domain.market.trading_calendar` module (see that
# module's own docstring) so `classify_market_data_state()` can reuse the
# EXACT SAME calendar arithmetic instead of a second implementation --
# imported below as `most_recent_trading_day_at_or_before`.


@dataclass(frozen=True)
class StageOneResult:
    universe_size: int
    candidates_evaluated: int  # symbols with a real, fresh quote (before bucket filtering)
    survivors: list[str]
    rejected: list[RejectionRecord]
    # Real Stage-1 buy/sell quantity ratio for each survivor that had one
    # (i.e. `raw.total_buy_quantity`/`total_sell_quantity` were both
    # present on the batched quote) -- carried forward so Stage 2 can
    # report an honest, real order-flow observation (`participation_note`)
    # without a second fetch and without calling it "accumulation" (a
    # stronger structural claim than this data supports).
    survivor_buy_sell_ratio: dict[str, Decimal] = field(default_factory=dict)
    survivor_observations: dict[str, tuple[str, ...]] = field(default_factory=dict)
    truncated_count: int = 0
    survivor_cap_applied: bool = False
    index_relative_strength_available: bool = False


@dataclass(frozen=True)
class _QuoteMetrics:
    """Pure, real derived figures from one `RawQuote` -- never a new
    fetch, never a guess. Any component that couldn't be computed from
    real data stays `None`."""

    day_change_pct: Decimal | None
    vwap_distance_pct: Decimal | None
    position_in_day_range: Decimal | None  # 0.0 = at today's low, 1.0 = at today's high
    buy_sell_ratio: Decimal | None
    session_range_pct: Decimal | None  # (today high-low) / previous_close * 100


def _quote_metrics(raw: RawQuote) -> _QuoteMetrics:
    day_change_pct: Decimal | None = None
    if raw.previous_close is not None and raw.previous_close != 0:
        day_change_pct = Decimal(str((raw.last_price - raw.previous_close) / raw.previous_close * 100))

    vwap_distance_pct: Decimal | None = None
    if raw.average_price is not None and raw.average_price != 0:
        vwap_distance_pct = Decimal(str((raw.last_price - raw.average_price) / raw.average_price * 100))

    position_in_day_range: Decimal | None = None
    if raw.ohlc is not None and raw.ohlc.high != raw.ohlc.low:
        position_in_day_range = Decimal(str((raw.last_price - raw.ohlc.low) / (raw.ohlc.high - raw.ohlc.low)))

    buy_sell_ratio: Decimal | None = None
    if raw.total_buy_quantity is not None and raw.total_sell_quantity is not None and raw.total_sell_quantity > 0:
        buy_sell_ratio = Decimal(raw.total_buy_quantity) / Decimal(raw.total_sell_quantity)

    session_range_pct: Decimal | None = None
    if raw.ohlc is not None and raw.previous_close is not None and raw.previous_close != 0:
        session_range_pct = Decimal(str((raw.ohlc.high - raw.ohlc.low) / raw.previous_close * 100))

    return _QuoteMetrics(
        day_change_pct=day_change_pct, vwap_distance_pct=vwap_distance_pct,
        position_in_day_range=position_in_day_range, buy_sell_ratio=buy_sell_ratio,
        session_range_pct=session_range_pct,
    )


def _already_extended_intraday(m: _QuoteMetrics, cfg: ScreeningConfig) -> bool:
    """Real, factual, conservative hard exclusion -- checked BEFORE
    bucket membership. This is the actual mechanism that stops a
    stock that already surged from being treated as early-stage."""
    if m.day_change_pct is not None and abs(m.day_change_pct) >= cfg.large_unpinned_move_pct:
        return True
    if m.day_change_pct is None or m.position_in_day_range is None:
        return False
    if m.day_change_pct >= cfg.extended_min_day_change_pct and m.position_in_day_range >= cfg.extended_min_position_when_up:
        return True
    return bool(m.day_change_pct <= -cfg.extended_min_day_change_pct and m.position_in_day_range <= cfg.extended_max_position_when_down)


def _discovery_buckets(
    m: _QuoteMetrics, cfg: ScreeningConfig, *, index_day_change_pct: Decimal | None = None,
) -> list[str]:
    """Which of the genuinely-cheap real discovery buckets this
    candidate matches -- never a score, a real membership list. Missing
    data simply cannot match a bucket that needs it (never fabricated
    into a match). Never claims option-chain or futures evidence."""
    matches: list[str] = []
    if (
        m.day_change_pct is not None and m.vwap_distance_pct is not None
        and cfg.momentum_min_day_change_pct <= abs(m.day_change_pct) <= cfg.momentum_max_day_change_pct
        and abs(m.vwap_distance_pct) <= cfg.momentum_max_vwap_distance_pct
    ):
        matches.append(DEVELOPING_MOMENTUM)
    if m.buy_sell_ratio is not None and (
        m.buy_sell_ratio >= cfg.participation_min_imbalance_ratio
        or m.buy_sell_ratio <= Decimal(1) / cfg.participation_min_imbalance_ratio
    ):
        matches.append(ORDER_FLOW_PARTICIPATION)
    if m.day_change_pct is not None and m.position_in_day_range is not None:
        recovering_from_low = m.day_change_pct < 0 and m.position_in_day_range >= cfg.reversal_min_position_when_red
        fading_from_high = m.day_change_pct > 0 and m.position_in_day_range <= cfg.reversal_max_position_when_green
        if recovering_from_low or fading_from_high:
            matches.append(EARLY_REVERSAL)
            matches.append(FAILED_BREAKDOWN_RECLAIM_CANDIDATE)
    compressing = (
        m.session_range_pct is not None
        and m.session_range_pct <= cfg.compression_max_session_range_pct
        and (m.day_change_pct is None or abs(m.day_change_pct) <= cfg.momentum_max_day_change_pct)
    )
    near_boundary = False
    if m.position_in_day_range is not None:
        near_boundary = (
            m.position_in_day_range >= cfg.near_boundary_min_position
            or m.position_in_day_range <= cfg.near_boundary_max_position
        )
    # Compression alone is not a promotion reason -- a quiet mid-range
    # tape is not an early setup. Compression near a session extreme is.
    if compressing and near_boundary:
        matches.append(INTRADAY_COMPRESSION)
        matches.append(NEAR_SESSION_BOUNDARY)
        matches.append(PRE_BREAKOUT_COMPRESSION_CANDIDATE)
    if (
        index_day_change_pct is not None
        and m.day_change_pct is not None
        and abs(m.day_change_pct - index_day_change_pct) >= cfg.relative_strength_min_gap_pct
    ):
        matches.append(RELATIVE_STRENGTH_VS_INDEX)
    assert_no_stage1_option_claims(matches)
    return matches


async def screen_universe(
    symbols: Sequence[str], *, provider: BatchedQuoteProvider, instrument_master: Sequence[dict[str, object]],
    as_of: datetime, exchange_status: ExchangeStatus = ExchangeStatus.UNKNOWN, config: ScreeningConfig | None = None,
    on_progress: Callable[[dict[str, object]], Awaitable[None]] | None = None,
) -> StageOneResult:
    """Real, cheap, batched EARLY-STAGE discovery -- see the module-level
    HONESTY NOTE above for exactly which dimensions this can and cannot
    evaluate. Never sorts by raw movement/volume magnitude -- survivors
    are those matching at least one real discovery bucket and not
    already extended, tie-broken by how many independent real buckets
    corroborate them (never a synthetic score), then alphabetically for
    full determinism.

    `exchange_status` (Sprint 2) -- the SAME real `ExchangeStatus` Stage 2
    already fetches per symbol via `provider.get_market_status()`, passed
    in ONCE here rather than re-fetched per chunk. Defaults to `UNKNOWN`
    (the same safe fallback Stage 2 already uses when the market-status
    call itself fails) so an explicit-symbols caller that never calls
    Stage 1 directly still gets the conservative behavior if this is ever
    invoked without it."""
    cfg = config or _DEFAULT_SCREENING_CONFIG
    market_is_open = exchange_status == ExchangeStatus.NORMAL_OPEN
    rejected: list[RejectionRecord] = []
    symbol_to_key: dict[str, str] = {}

    for symbol in symbols:
        # Zero-cost static check -- no network call: a symbol whose F&O
        # listing has no real upcoming expiry (e.g. delisted derivatives)
        # is genuinely unresearchable today.
        if nearest_expiry(instrument_master, symbol, as_of=as_of.date()) is None:
            rejected.append(RejectionRecord(symbol=symbol, reason="no valid upcoming F&O expiry found"))
            continue
        ref = resolve_symbol(instrument_master, symbol, segment="NSE_EQ")
        if ref is None:
            rejected.append(RejectionRecord(symbol=symbol, reason="no NSE_EQ instrument key found for this symbol"))
            continue
        symbol_to_key[symbol] = ref.instrument_key

    key_to_symbol = {key: symbol for symbol, key in symbol_to_key.items()}
    keys = list(key_to_symbol.keys())
    quotes: dict[str, RawQuote] = {}
    index_ref = resolve_symbol(instrument_master, "NIFTY", segment="NSE_INDEX")
    index_key = index_ref.instrument_key if index_ref is not None else None
    # Sprint 3 -- tracks keys already rejected by a real batch-level
    # failure below, so the per-symbol "no quote data returned" check
    # further down never produces a SECOND, redundant `RejectionRecord`
    # for the same real symbol (a genuine P2 fixed: was previously
    # harmless for `ResearchCoverage` math, which already deduplicates by
    # symbol, but inflated the raw rejection list/UI tally).
    batch_failed_keys: set[str] = set()
    for i in range(0, len(keys), cfg.quote_batch_size):
        chunk = keys[i : i + cfg.quote_batch_size]
        if i == 0 and index_key is not None and index_key not in chunk:
            chunk = [*chunk, index_key]
        try:
            # Sprint 1 -- bounded retry on real transient failures only
            # (see `ScreeningConfig.quote_batch_attempts`'s docstring);
            # `_retry()` re-raises immediately, unretried, for a genuinely
            # bad response (`ProviderMalformedResponse`) -- only
            # timeout/unavailable/rate-limited get a second attempt.
            chunk_quotes = await _retry(
                functools.partial(provider.get_quotes, chunk), attempts=cfg.quote_batch_attempts, backoff_seconds=cfg.quote_batch_backoff_seconds,
            )
        except ProviderError as exc:
            # One bad batch must never take down the whole screen -- every
            # symbol in that chunk gets a real, specific rejection reason.
            for key in chunk:
                rejected_symbol = key_to_symbol.get(key)
                if rejected_symbol is None:
                    continue
                rejected.append(RejectionRecord(symbol=rejected_symbol, reason=f"quote batch fetch failed: {exc}"))
                batch_failed_keys.add(key)
            continue
        quotes.update(chunk_quotes)
        if on_progress is not None:
            await on_progress({
                "stage": "stage1",
                "processed": min(i + cfg.quote_batch_size, len(keys)),
                "total": len(keys),
                "message": "Fast discovery — quotes",
            })
        await asyncio.sleep(0)

    index_day_change_pct: Decimal | None = None
    if index_key is not None:
        index_raw = quotes.get(index_key)
        if index_raw is not None and index_raw.previous_close is not None and index_raw.previous_close != 0:
            index_day_change_pct = Decimal(
                str((index_raw.last_price - index_raw.previous_close) / index_raw.previous_close * 100)
            )

    candidates_evaluated = 0
    matched: list[tuple[str, list[str]]] = []
    metrics_by_symbol: dict[str, _QuoteMetrics] = {}
    for symbol, key in symbol_to_key.items():
        raw = quotes.get(key)
        if raw is None:
            if key not in batch_failed_keys:
                rejected.append(RejectionRecord(symbol=symbol, reason="no quote data returned for this symbol"))
            continue
        if raw.exchange_timestamp is not None:
            quote_ts = ensure_utc(raw.exchange_timestamp)
            age = as_of - quote_ts
            # Sprint 2, Objective 2 -- a real look-ahead quote must never
            # survive Stage 1 either way (live or closed): reuses the SAME
            # bounded tolerance `normalize_quote()` already applies at
            # Stage 2 (never a second, different number) -- ordinary
            # multi-step-fetch drift within that window is tolerated
            # exactly as it already is there; anything beyond it is
            # rejected here too, not merely left for Stage 2 to catch
            # after a wasted fetch.
            if -age > _MAX_CLOCK_SKEW_TOLERANCE:
                rejected.append(RejectionRecord(
                    symbol=symbol,
                    reason=f"stale quote data (suspicious quote timestamp {quote_ts} is ahead of as_of {as_of} by more than the real clock-skew tolerance)",
                ))
                continue
            if market_is_open:
                # LIVE/PRE-OPEN/NORMAL-OPEN -- existing strict protection,
                # completely unchanged (Sprint 2, Objective 2).
                if age > cfg.stale_quote_tolerance:
                    rejected.append(RejectionRecord(symbol=symbol, reason=f"stale quote data (age {age})"))
                    continue
            else:
                # Sprint 2 -- market genuinely CLOSED (or its status could
                # not be determined, the same conservative fallback Stage 2
                # already uses) -- the latest completed session's quote is
                # valid HISTORICAL research data, never claimed "live". Real
                # `is_trading_day()` weekend/holiday-aware bound (NOT a flat
                # hours/days tolerance): the floor is the most recent
                # trading day strictly BEFORE `as_of`'s own calendar date --
                # not `as_of`'s date itself. That one-day offset is
                # deliberate: it makes the same rule correct for BOTH a
                # post-close check ("today already closed, is today's own
                # quote acceptable" -- trivially yes, today >= yesterday)
                # AND a pre-open check ("today hasn't opened yet, is
                # Friday's quote acceptable on Monday morning" -- yes,
                # Friday >= Friday) without needing to separately special-
                # case PRE_OPEN vs NORMAL_CLOSE/CLOSING_*/UNKNOWN. A quote
                # older than that floor is still rejected as genuinely
                # stale, unconditionally.
                expected_last_session = most_recent_trading_day_at_or_before(as_of.date() - timedelta(days=1))
                if quote_ts.date() < expected_last_session:
                    rejected.append(RejectionRecord(
                        symbol=symbol,
                        reason=(
                            f"stale quote data (age {age}, real quote date {quote_ts.date()} predates the "
                            f"expected last trading session {expected_last_session})"
                        ),
                    ))
                    continue
        candidates_evaluated += 1
        m = _quote_metrics(raw)
        metrics_by_symbol[symbol] = m

        if _already_extended_intraday(m, cfg):
            direction = "up" if (m.day_change_pct or Decimal(0)) > 0 else "down"
            rejected.append(RejectionRecord(
                symbol=symbol,
                reason=(
                    f"already extended intraday: {m.day_change_pct:+.2f}% today, trading near today's "
                    f"{'high' if direction == 'up' else 'low'} (position in day's range "
                    f"{(m.position_in_day_range or Decimal(0)):.0%}) -- not an early-stage candidate under this methodology"
                ),
            ))
            continue

        buckets = _discovery_buckets(m, cfg, index_day_change_pct=index_day_change_pct)
        if not buckets:
            change_text = f"{m.day_change_pct:+.2f}%" if m.day_change_pct is not None else "n/a"
            vwap_text = f"{m.vwap_distance_pct:+.2f}%" if m.vwap_distance_pct is not None else "n/a"
            rejected.append(RejectionRecord(
                symbol=symbol,
                reason=(
                    f"insufficient early-stage evidence (day change {change_text}, distance from day VWAP {vwap_text} "
                    "-- no developing-momentum/order-flow/reversal bucket matched)"
                ),
            ))
            continue
        matched.append((symbol, buckets))

    # Corroboration count (real bucket count, never a synthetic score),
    # then alphabetical -- fully deterministic, no movement/volume
    # magnitude anywhere in this ordering.
    ordered = sorted(matched, key=lambda item: (-len(item[1]), item[0]))
    survivors = [symbol for symbol, _ in ordered[: cfg.survivor_cap]]
    survivor_set = set(survivors)
    truncated_count = 0
    for symbol, buckets in ordered:
        if symbol not in survivor_set:
            truncated_count += 1
            rejected.append(RejectionRecord(
                symbol=symbol,
                reason=(
                    f"screened out at stage 1 (matched {', '.join(buckets)}, but not among the top "
                    f"{cfg.survivor_cap} by bucket corroboration -- survivor_cap is a provider-safety "
                    f"limit, not a claim that the remaining names were uninteresting)"
                ),
            ))

    survivor_buy_sell_ratio: dict[str, Decimal] = {}
    survivor_observations: dict[str, tuple[str, ...]] = {}
    for symbol, buckets in ordered:
        if symbol in survivor_set:
            survivor_observations[symbol] = tuple(buckets)
            ratio = metrics_by_symbol[symbol].buy_sell_ratio
            if ratio is not None:
                survivor_buy_sell_ratio[symbol] = ratio
    return StageOneResult(
        universe_size=len(symbols), candidates_evaluated=candidates_evaluated, survivors=survivors, rejected=rejected,
        survivor_buy_sell_ratio=survivor_buy_sell_ratio,
        survivor_observations=survivor_observations,
        truncated_count=truncated_count,
        survivor_cap_applied=truncated_count > 0,
        index_relative_strength_available=index_day_change_pct is not None,
    )


@dataclass(frozen=True)
class _GatedCandidate:
    """One symbol that survived the gates -- carries only real,
    already-computed fields, never a new calculation."""

    symbol: str
    response: AnalyzeResponse
    dc: DirectionComparisonVisual
    contract: ContractAssessmentView
    direction: str  # "BULLISH" or "BEARISH" -- copied verbatim from dc.preferred_direction
    # Real Stage-1 buy/sell quantity ratio, when Stage 1 ran and computed
    # one for this symbol -- `None` when Stage 1 was skipped (explicit
    # `?symbols=` override) or no real buy/sell quantity data was present
    # on the batched quote. Never re-derived here.
    stage_one_buy_sell_ratio: Decimal | None = None
    stage_one_observations: tuple[str, ...] = ()


@dataclass(frozen=True)
class RankedCandidate:
    rank: int
    symbol: str
    response: AnalyzeResponse
    dc: DirectionComparisonVisual
    contract: ContractAssessmentView
    direction: str
    rationale: RankingRationale
    stage_one_buy_sell_ratio: Decimal | None = None
    stage_one_observations: tuple[str, ...] = ()


@dataclass(frozen=True)
class DailyResearchResult:
    generated_at: datetime
    market_state: str | None
    universe: list[str]
    screened_count: int
    deep_analyzed_count: int
    rejected: list[RejectionRecord] = field(default_factory=list)
    shortlist: list[RankedCandidate] = field(default_factory=list)
    no_high_conviction: bool = False
    # Objective 7 -- present (not None) only when Stage 1 actually ran
    # (i.e. no explicit `symbols` override was given); `None` when a
    # caller passed explicit symbols and Stage 1 was correctly skipped.
    stage_one_survivor_count: int | None = None
    rejection_summary: dict[str, int] = field(default_factory=dict)
    # Sprint 1, Phase 4 -- present on every real run of this function;
    # `None` only for a `DailyResearchResult` built by test code that
    # never calls `run_daily_research()` itself.
    coverage: ResearchCoverage | None = None
    # 95%+ Reliability & Performance Gate -- `None` only for
    # `DailyResearchResult`s built directly by test code that never calls
    # `run_daily_research()` itself (mirrors `coverage`'s own convention).
    scan_snapshot: ResearchScanSnapshot | None = None
    # Every Stage-2 symbol that cleared `_gate()` -- source for
    # DEVELOPING NOW / ALREADY MOVED buckets. Empty default for tests
    # that construct this dataclass without running a scan.
    stage_two_gated: list[RankedCandidate] = field(default_factory=list)
    universe_source: str = UNIVERSE_SOURCE_UPSTOX_NSE_FO_EQUITY
    fno_ban_status: str = FNO_BAN_STATUS_UNKNOWN
    stage1_truncated_count: int = 0
    stage1_cap_applied: bool = False
    index_relative_strength_available: bool = False

    @property
    def rejected_as_extended_count(self) -> int:
        return self.rejection_summary.get("ALREADY_EXTENDED", 0)

    @property
    def rejected_insufficient_evidence_count(self) -> int:
        return self.rejection_summary.get("INSUFFICIENT_EARLY_STAGE_EVIDENCE", 0)


# Objective 7 -- a deterministic categorization of each REAL rejection
# reason string `_gate()`/`screen_universe()` already generate (never a
# new judgment about a symbol -- purely a string-prefix bucket over text
# that already exists). Order matters: first match wins.
_REJECTION_CATEGORY_RULES: list[tuple[str, str]] = [
    ("already extended intraday", "ALREADY_EXTENDED"),
    ("insufficient early-stage evidence", "INSUFFICIENT_EARLY_STAGE_EVIDENCE"),
    ("screened out at stage 1", "STAGE_1_SCREENED_OUT (matched a real bucket, but not among the top corroborated)"),
    ("stale quote data", "STALE_DATA"),
    ("stale/unavailable data", "STALE_DATA"),
    ("no valid upcoming f&o expiry", "DATA_UNAVAILABLE (no upcoming expiry)"),
    ("no nse_eq instrument key", "DATA_UNAVAILABLE (no instrument key)"),
    ("quote batch fetch failed", "DATA_UNAVAILABLE (quote fetch failed)"),
    ("no quote data returned", "DATA_UNAVAILABLE (no quote)"),
    ("no analysis data available", "DATA_UNAVAILABLE (no analysis)"),
    ("no direction comparison available", "DATA_UNAVAILABLE (no option chain)"),
    ("analysis error", "ANALYSIS_ERROR"),
    ("no defensible direction", "NO_DIRECTIONAL_CONVERGENCE"),
    ("contract structural quality", "POOR_CONTRACT_QUALITY"),
]


def categorize_rejection(reason: str) -> str:
    lowered = reason.lower()
    for needle, category in _REJECTION_CATEGORY_RULES:
        if needle in lowered:
            return category
    return "OTHER"


def summarize_rejections(rejected: Sequence[RejectionRecord]) -> dict[str, int]:
    """The real per-symbol reasons, bucketed and counted -- Objective 7's
    "no high-conviction candidates because: 31 failed X, 18 lacked Y..."
    Never invents a reason; a bucket with count 0 simply doesn't appear."""
    tally: dict[str, int] = {}
    for r in rejected:
        category = categorize_rejection(r.reason)
        tally[category] = tally.get(category, 0) + 1
    return tally


# ============================================================
# Sprint 1, Phase 4 -- RESEARCH COVERAGE: how completely THIS run actually
# evaluated the real universe. Metadata about the RUN, never a stock-
# quality signal -- computed strictly AFTER ranking/gating already
# produced the shortlist and rejections, and never read back into either
# (see `ResearchCoverage`'s own docstring). Reuses the SAME rejection-
# reason categories `categorize_rejection()` already computes -- never a
# second judgment about a symbol.
# ============================================================

# A real, evidence-based rejection or a real Stage-1 screen-out means the
# symbol WAS reliably evaluated -- excluded here on purpose. A structural
# exclusion ("no F&O expiry"/"no instrument key") is a fixed fact about
# that symbol, true every run regardless of network conditions -- also
# excluded, since re-running cannot change it (not what "coverage"
# describes). Only genuine infrastructure/data failures count.
_RELIABILITY_FAILURE_CATEGORIES: frozenset[str] = frozenset({
    "DATA_UNAVAILABLE (quote fetch failed)",
    "DATA_UNAVAILABLE (no quote)",
    "DATA_UNAVAILABLE (no analysis)",
    "DATA_UNAVAILABLE (no option chain)",
    "STALE_DATA",
    "ANALYSIS_ERROR",
    "OTHER",  # unclassified -- honesty over silently excluding an unknown failure mode
})


def _reliability_failed_symbols(rejected: Sequence[RejectionRecord]) -> set[str]:
    """Distinct symbols with at least one rejection categorized as a
    reliability failure -- a SET, not a sum. `screen_universe()`'s
    batch-failure path is deduplicated at the source since Sprint 3 (one
    real `RejectionRecord` per symbol, never two), but this stays a set
    regardless -- the correct, robust way to count real symbols even if a
    future rejection path ever produced more than one record for the same
    symbol again."""
    return {r.symbol for r in rejected if categorize_rejection(r.reason) in _RELIABILITY_FAILURE_CATEGORIES}


# Round, documented thresholds over one real ratio (reliably-evaluated /
# universe) -- deliberately NOT a multi-factor weighted formula (that would
# be exactly the "arbitrary weights" this whole project has repeatedly
# forbidden). "at least half", "at least 4 in 5", "at least 95%" are
# ordinary, recognizable reporting cutoffs, not numbers fitted to make any
# specific run's result look a certain way.
_COVERAGE_HIGH_MIN_RATIO = Decimal("0.95")
_COVERAGE_GOOD_MIN_RATIO = Decimal("0.80")
_COVERAGE_DEGRADED_MIN_RATIO = Decimal("0.50")


def compute_research_coverage(
    *,
    universe_count: int,
    stage_one_attempted: int | None,
    stage_one_rejected: Sequence[RejectionRecord],
    stage_two_attempted: int,
    stage_two_rejected: Sequence[RejectionRecord],
) -> ResearchCoverage:
    """Pure function over already-computed counts/rejection lists -- never
    fetches, never re-analyzes, never touched by `rank_candidates()`/
    `_gate()` (grep confirms neither imports this module). `stage_one_attempted`
    is `None` exactly when Stage 1 was skipped (explicit `?symbols=`) --
    the same "None means not run" convention `stage_one_survivor_count`
    already uses elsewhere in this module."""
    stage1_failed_symbols = _reliability_failed_symbols(stage_one_rejected)
    stage2_failed_symbols = _reliability_failed_symbols(stage_two_rejected)

    stage1_failed = len(stage1_failed_symbols) if stage_one_attempted is not None else None
    stage1_successful = (
        stage_one_attempted - stage1_failed if stage_one_attempted is not None and stage1_failed is not None else None
    )
    stage2_failed = len(stage2_failed_symbols)
    stage2_successful = stage_two_attempted - stage2_failed

    all_rejected = list(stage_one_rejected) + list(stage_two_rejected)
    timeout_failures = sum(1 for r in all_rejected if "timed out" in r.reason.lower())
    timestamp_quality_failures = sum(
        1 for r in all_rejected if "datafreshness" in r.reason.lower() or "received_timestamp" in r.reason.lower()
    )

    # Stage 1's failed symbols never reach Stage 2 -- the two sets are
    # disjoint by construction; union is still the correct, safe way to
    # combine them (never double-counts even if that ever changed).
    reliably_evaluated = universe_count - len(stage1_failed_symbols | stage2_failed_symbols)
    ratio = Decimal(reliably_evaluated) / Decimal(universe_count) if universe_count > 0 else Decimal(1)

    if ratio >= _COVERAGE_HIGH_MIN_RATIO:
        classification = "HIGH"
    elif ratio >= _COVERAGE_GOOD_MIN_RATIO:
        classification = "GOOD"
    elif ratio >= _COVERAGE_DEGRADED_MIN_RATIO:
        classification = "DEGRADED"
    else:
        classification = "POOR"

    return ResearchCoverage(
        universe_count=universe_count, stage1_attempted=stage_one_attempted, stage1_successful=stage1_successful,
        stage1_failed=stage1_failed, stage2_attempted=stage_two_attempted, stage2_successful=stage2_successful,
        stage2_failed=stage2_failed, timeout_failures=timeout_failures,
        timestamp_quality_failures=timestamp_quality_failures, classification=classification,
    )


def _gate(
    symbol: str, response: AnalyzeResponse, *,
    stage_one_buy_sell_ratio: Decimal | None = None,
    stage_one_observations: tuple[str, ...] = (),
) -> _GatedCandidate | RejectionRecord:
    """Disqualifying checks only -- never a scored axis. Every rejection
    carries the real, specific reason (Section 16 -- rejection
    transparency): this is what proves the researcher actually looked at
    the symbol rather than skipping it."""
    if response.error is not None:
        return RejectionRecord(symbol=symbol, reason=f"analysis error: {response.error}")
    v = response.visual
    if v is None:
        return RejectionRecord(symbol=symbol, reason="no analysis data available")
    freshness_state = v.freshness.market_state if v.freshness else response.market_state
    if freshness_state in _STALE_MARKET_STATES:
        return RejectionRecord(symbol=symbol, reason=f"stale/unavailable data ({freshness_state})")
    dc = v.direction_comparison
    if dc is None:
        return RejectionRecord(symbol=symbol, reason="no direction comparison available (insufficient option-chain data)")
    if dc.no_defensible_direction or dc.preferred_contract is None or dc.preferred_direction is None:
        return RejectionRecord(symbol=symbol, reason=f"no defensible direction (verdict: {dc.verdict})")
    contract = dc.preferred_contract
    if contract.structural_quality != "ACCEPTABLE":
        return RejectionRecord(symbol=symbol, reason=f"contract structural quality is {contract.structural_quality}, not ACCEPTABLE")
    return _GatedCandidate(
        symbol=symbol, response=response, dc=dc, contract=contract, direction=dc.preferred_direction,
        stage_one_buy_sell_ratio=stage_one_buy_sell_ratio,
        stage_one_observations=stage_one_observations,
    )


def _nearest_opposing_level(candidate: _GatedCandidate) -> LevelView | None:
    """The nearest REAL S/R level standing in the way of this candidate's
    own thesis direction (bullish -> nearest resistance above spot;
    bearish -> nearest support below spot) -- read-only, CONTEXTUAL use
    of the existing S/R levels; never turned into a directional vote
    (that architecture, and its non-directional neutrality, is
    untouched)."""
    sr = candidate.response.visual.support_resistance if candidate.response.visual else None
    if sr is None:
        return None
    levels = sr.resistance if candidate.direction == "BULLISH" else sr.support
    known = [lv for lv in levels if lv.strike is not None]
    if not known:
        return None
    return min(known, key=lambda lv: lv.distance_pct if lv.distance_pct is not None else _WORST_PCT)


def _headroom_pct(candidate: _GatedCandidate) -> Decimal | None:
    """Distance to the nearest REAL S/R level standing in the way of this
    candidate's own thesis direction. `None` when no such level exists in
    this analysis -- treated as the worst case by the caller, never as
    "unlimited headroom" (missing evidence must never become positive
    evidence)."""
    nearest = _nearest_opposing_level(candidate)
    return nearest.distance_pct if nearest is not None else None


def _underlying_move_to_breakeven_pct(contract: ContractAssessmentView | None, spot: Decimal | None) -> Decimal | None:
    """Signed underlying move (as a % of spot) from the current real spot
    to a contract's contractual expiry breakeven -- the SAME arithmetic
    already used in the frontend's `underlyingMoveToBreakeven()`
    (strike +/- premium, never re-derived here, just re-applied to the
    same real `contractual_expiry_breakeven` value). Negative means
    already past breakeven -- genuinely more favorable, so this is used
    signed (not absolute value). Works for EITHER side's contract, not
    just the preferred one -- used for both-side thesis reporting."""
    if contract is None or spot is None or contract.contractual_expiry_breakeven is None or spot == 0:
        return None
    be = contract.contractual_expiry_breakeven
    points = (be - spot) if contract.right == "CE" else (spot - be)
    return points / spot * Decimal(100)


def _breakeven_distance_pct(candidate: _GatedCandidate) -> Decimal | None:
    spot = candidate.dc.paths.spot if candidate.dc.paths else None
    return _underlying_move_to_breakeven_pct(candidate.contract, spot)


def _sort_key(candidate: _GatedCandidate) -> tuple[int, int, int, int, Decimal, Decimal, Decimal]:
    decision_tier = _DECISION_TIER.get(candidate.response.decision or "", _WORST_DECISION_TIER)
    supporting_groups = (
        candidate.dc.bullish_supporting_groups if candidate.direction == "BULLISH" else candidate.dc.bearish_supporting_groups
    )
    liquidity_tier = _LIQUIDITY_ORDINAL.get(candidate.contract.liquidity_grade, _WORST_LIQUIDITY)
    decay_tier = _DECAY_ORDINAL.get(candidate.contract.decay_verdict or "", _WORST_DECAY)
    required_move_pct = candidate.contract.required_underlying_move_pct
    breakeven_pct = _breakeven_distance_pct(candidate)
    headroom = _headroom_pct(candidate)
    return (
        decision_tier,
        -supporting_groups,  # more supporting groups is better -> negate for ascending-is-better
        liquidity_tier,
        decay_tier,
        required_move_pct if required_move_pct is not None else _WORST_PCT,
        breakeven_pct if breakeven_pct is not None else _WORST_PCT,
        -headroom if headroom is not None else _WORST_PCT,  # more headroom is better -> negate
    )


def _rationale(candidate: _GatedCandidate) -> RankingRationale:
    supporting_groups = (
        candidate.dc.bullish_supporting_groups if candidate.direction == "BULLISH" else candidate.dc.bearish_supporting_groups
    )
    required_move_pct = candidate.contract.required_underlying_move_pct
    breakeven_pct = _breakeven_distance_pct(candidate)
    headroom = _headroom_pct(candidate)
    return RankingRationale(
        decision_tier=candidate.response.decision or "n/a",
        directional_verdict=candidate.dc.verdict,
        supporting_groups=supporting_groups,
        liquidity_grade=candidate.contract.liquidity_grade,
        decay_verdict=candidate.contract.decay_verdict,
        required_underlying_move_pct=str(required_move_pct) if required_move_pct is not None else None,
        breakeven_distance_pct=str(breakeven_pct) if breakeven_pct is not None else None,
        headroom_pct=str(headroom) if headroom is not None else None,
    )


# Objective 5 -- Research Confidence is a TIER, deliberately never a
# numeric score. The ranking above is a lexicographic tuple over
# heterogeneous dimensions (a decision tier, two ordinals, three real
# percentages) -- collapsing that into e.g. "82/100" would require
# inventing relative weights between "one more supporting group" and
# "0.5% closer to breakeven," which is exactly the false-precision this
# project has repeatedly forbidden. Each rule below tests exactly one
# already-computed real field (never a blend); this can never be
# misread as a probability of profit, a win rate, or an expected return.
_RESEARCH_CONFIDENCE_ORDER = ["VERY STRONG", "STRONG", "MODERATE", "WEAK"]


def classify_research_confidence(candidate: _GatedCandidate) -> str:
    """A small, explicit, documented rule set -- one of
    VERY STRONG / STRONG / MODERATE / WEAK. This is comparative research
    strength only, never `FinalDecision` and never a probability.
    Early-move discovery phase -- also caps the tier when the real
    `early_stage_state` shows the move has already broken past recent
    structure (EXTENDED/EXHAUSTION_RISK) or shows a real failed-breakout
    signal (FALSE_BREAKOUT_RISK): a setup that is no longer early-stage
    can never read VERY STRONG/STRONG, however good its liquidity/decay/
    evidence-count otherwise look -- directly implements "penalize
    extension/exhaustion" without a second, parallel scoring field."""
    supporting_groups = (
        candidate.dc.bullish_supporting_groups if candidate.direction == "BULLISH" else candidate.dc.bearish_supporting_groups
    )
    decision = candidate.response.decision or ""
    liquidity = candidate.contract.liquidity_grade
    decay = candidate.contract.decay_verdict or ""
    strong_liquidity = liquidity in ("excellent", "good")
    favorable_decay = decay in ("DECAY_FAVORABLE", "DECAY_ACCEPTABLE")
    early_stage_state, _ = classify_early_stage_state(candidate)
    no_longer_early_stage = early_stage_state in ("EXTENDED", "EXHAUSTION_RISK", "FALSE_BREAKOUT_RISK")

    if not no_longer_early_stage and decision == "TRADEABLE" and liquidity == "excellent" and favorable_decay and supporting_groups >= 3:
        return "VERY STRONG"
    if (
        not no_longer_early_stage and decision in ("TRADEABLE", "WATCH")
        and strong_liquidity and decay != "DECAY_UNFAVORABLE" and supporting_groups >= 2
    ):
        return "STRONG"
    if strong_liquidity:
        return "MODERATE"
    return "WEAK"


def rank_candidates(
    responses: dict[str, AnalyzeResponse], *, top_n: int = 3,
    stage_one_buy_sell_ratio: dict[str, Decimal] | None = None,
    stage_one_observations: dict[str, tuple[str, ...]] | None = None,
) -> tuple[list[RankedCandidate], list[RejectionRecord]]:
    """Pure, deterministic (stable sort, fully explicit tie-break tuple --
    no dict/set-ordering dependence) ranking over already-computed
    `AnalyzeResponse` objects. Never fetches, never re-analyzes, never
    overrides `response.decision`. `top_n` caps the shortlist -- 0-3
    entries, never padded to fill a quota (Section 6). `stage_one_buy_sell_ratio`
    is carried through for reporting only (`participation_note`) -- it is
    never read by `_sort_key`/`_rationale`, so it cannot become a second,
    undocumented ranking input."""
    ratios = stage_one_buy_sell_ratio or {}
    observations = stage_one_observations or {}
    gated: list[_GatedCandidate] = []
    rejected: list[RejectionRecord] = []
    for symbol, response in responses.items():
        result = _gate(
            symbol, response,
            stage_one_buy_sell_ratio=ratios.get(symbol),
            stage_one_observations=observations.get(symbol, ()),
        )
        if isinstance(result, RejectionRecord):
            rejected.append(result)
        else:
            gated.append(result)

    ordered = sorted(gated, key=_sort_key)
    shortlist = [
        RankedCandidate(
            rank=i + 1, symbol=c.symbol, response=c.response, dc=c.dc, contract=c.contract,
            direction=c.direction, rationale=_rationale(c), stage_one_buy_sell_ratio=c.stage_one_buy_sell_ratio,
            stage_one_observations=c.stage_one_observations,
        )
        for i, c in enumerate(ordered[:top_n])
    ]
    return shortlist, rejected


def collect_gated_candidates(
    responses: dict[str, AnalyzeResponse], *,
    stage_one_buy_sell_ratio: dict[str, Decimal] | None = None,
    stage_one_observations: dict[str, tuple[str, ...]] | None = None,
) -> list[RankedCandidate]:
    """Every Stage-2 symbol that cleared `_gate()`, not just the top-N
    shortlist. `rank` is 0 -- this is a bucket source, not a ranking.
    Deterministic: original `responses` insertion order is preserved
    (dict insertion order of `stage_two_universe`)."""
    ratios = stage_one_buy_sell_ratio or {}
    observations = stage_one_observations or {}
    gated: list[RankedCandidate] = []
    for symbol, response in responses.items():
        result = _gate(
            symbol, response,
            stage_one_buy_sell_ratio=ratios.get(symbol),
            stage_one_observations=observations.get(symbol, ()),
        )
        if isinstance(result, _GatedCandidate):
            gated.append(RankedCandidate(
                rank=0, symbol=result.symbol, response=result.response, dc=result.dc,
                contract=result.contract, direction=result.direction, rationale=_rationale(result),
                stage_one_buy_sell_ratio=result.stage_one_buy_sell_ratio,
                stage_one_observations=result.stage_one_observations,
            ))
    return gated


async def run_daily_research(
    symbols: Sequence[str] | None = None,
    *,
    provider: UpstoxProvider,
    instrument_master: Sequence[dict[str, object]],
    strategy: EMAVWAPAlignmentStrategy,
    repositories: Repositories,
    config: PipelineConfig,
    as_of: datetime,
    mcx_instrument_master: Sequence[dict[str, object]] | None = None,
    sector_map: dict[str, str] | None = None,
    journal: JsonlAuditJournalRepository | None = None,
    research_journal: ResearchRunRepository | None = None,
    outcome_repository: ResearchOutcomeRepository | None = None,
    top_n: int = 3,
    screening_config: ScreeningConfig | None = None,
    nifty50_symbols: Sequence[str] | None = None,
    http_client: httpx.AsyncClient | None = None,
    delivery_cache_dir: Path | None = None,
    stage_two_concurrency: int = 6,
    on_progress: Callable[[dict[str, object]], Awaitable[None]] | None = None,
) -> DailyResearchResult:
    """When `symbols` is explicit (a caller's own list, or the old
    `DEFAULT_RESEARCH_UNIVERSE` fast/manual override), Stage 1 is skipped
    entirely and every one of those symbols goes straight to Stage 2 --
    the ORIGINAL, unchanged, fully backward-compatible behavior.

    When `symbols` is omitted, the real dynamic F&O equity universe
    (`list_fo_eligible_equity_underlyings()`) is screened by
    `screen_universe()` (Stage 1, cheap, batched) and only the survivors
    proceed to Stage 2.

    Stage 2 (95%+ Reliability & Performance Gate -- see
    `docs/TIRE_SCAN_PERFORMANCE.md` for the measured before/after): now
    runs with BOUNDED concurrency, `stage_two_concurrency` symbols at a
    time (default 6 -- conservative, well under any documented Upstox
    per-second limit since each symbol itself already issues several
    sequential HTTP calls inside `run_analysis()`; never "210 requests
    become 2100 requests" -- this only overlaps the WAITING time of
    independent symbols, it does not add a single extra HTTP call).
    Failure isolation is identical to the old sequential loop -- one
    symbol's unexpected exception still becomes that symbol's own
    `AnalyzeResponse(error=...)`, never propagated, never affecting any
    other symbol. Ranking and thesis synthesis are pure post-processing
    over the results; nothing here feeds back into the decision engine."""
    stage_one_survivor_count: int | None = None
    stage_one_attempted: int | None = None
    stage_one_rejected: list[RejectionRecord] = []
    stage_one_buy_sell_ratio: dict[str, Decimal] = {}
    stage_one_observations: dict[str, tuple[str, ...]] = {}
    stage1_truncated_count = 0
    stage1_cap_applied = False
    index_rs_available = False
    universe_source = UNIVERSE_SOURCE_UPSTOX_NSE_FO_EQUITY
    universe_discovery_seconds = 0.0
    stage1_seconds = 0.0
    if symbols is not None:
        # Explicit override -- Stage 1 correctly skipped; `full_universe`
        # and the Stage-2 input are the same list, exactly as before this
        # phase (zero regression for every existing `?symbols=` caller).
        full_universe = list(symbols)
        stage_two_universe = full_universe
        universe_source = "explicit_symbols_override"
    else:
        full_universe = list_fo_eligible_equity_underlyings(instrument_master)
        # Sprint 2 -- fetched ONCE for the whole screen (not per chunk/
        # symbol) -- the SAME real endpoint and the SAME conservative
        # UNKNOWN fallback Stage 2's own `analyze_symbol()` already uses
        # when this call itself fails, so Stage 1 and Stage 2 agree on
        # what "market open" means from the identical authoritative source
        # (Objective 7 -- no second, duplicate market-state implementation).
        stage1_started = utc_now()
        try:
            exchange_status = await provider.get_market_status(exchange="NSE")
        except ProviderError:
            exchange_status = ExchangeStatus.UNKNOWN
        stage_one = await screen_universe(
            full_universe, provider=provider, instrument_master=instrument_master, as_of=as_of,
            exchange_status=exchange_status, config=screening_config, on_progress=on_progress,
        )
        stage1_seconds = (utc_now() - stage1_started).total_seconds()
        stage_two_universe = stage_one.survivors
        stage_one_survivor_count = len(stage_one.survivors)
        stage_one_attempted = len(full_universe)
        stage_one_rejected = stage_one.rejected
        stage_one_buy_sell_ratio = stage_one.survivor_buy_sell_ratio
        stage_one_observations = stage_one.survivor_observations
        stage1_truncated_count = stage_one.truncated_count
        stage1_cap_applied = stage_one.survivor_cap_applied
        index_rs_available = stage_one.index_relative_strength_available

    # Sprint 1 -- root-cause fix for a real live defect found 2026-08-31:
    # `normalize_quote()` (app/data/normalization/base.py) already floors a
    # small, benign forward skew between a quote's real exchange timestamp
    # and the `received_at` it's normalized against (see
    # `_MAX_CLOCK_SKEW_TOLERANCE`, 5 minutes -- reused here UNCHANGED, not
    # widened). That tolerance is correctly sized for ONE symbol's own
    # multi-step fetch. Stage 2 here is a SEQUENTIAL loop over up to
    # `survivor_cap` symbols, each doing its own real multi-step fetch --
    # under real API pressure (confirmed live: ~100/210 Stage-1 quote-batch
    # timeouts in one run) the total elapsed wall-clock time across this
    # loop can itself exceed 5 minutes. Passing the SAME `as_of` (frozen
    # once, at the top of this whole call) to every symbol means a
    # late-processed symbol's real exchange timestamp is compared against
    # an `as_of` that is, by then, genuinely stale by more than the
    # per-symbol tolerance was ever meant to absorb -- confirmed via a live
    # single-symbol probe (real skew is small and negative -- data slightly
    # OLDER than as_of -- under normal conditions; this failure mode only
    # appears when `as_of` itself has fallen behind real wall-clock time).
    #
    # The fix does NOT touch the 5-minute tolerance and does NOT let any
    # symbol's own real skew go unchecked -- it only keeps this loop's
    # notion of "now" honest as real wall-clock time actually advances
    # while the scan runs, exactly mirroring what a fresh `/api/analyze`
    # call for that symbol would see at that later real moment. `utc_now()`
    # is used only to measure a REAL ELAPSED DURATION since this loop
    # started, never to override the caller-supplied `as_of` outright --
    # in a fast/mocked run (every existing test) that duration is
    # microseconds, so behavior is unchanged; `generated_at` and every
    # other run-level use of `as_of` (Stage 1, rejection timestamps,
    # ShortlistRecord) are untouched, still anchored to the original,
    # caller-supplied `as_of`.
    scan_provider: UpstoxProvider = ScanSnapshotCache(provider)  # type: ignore[assignment]
    stage_two_started_wallclock = utc_now()
    scan_started_wallclock_dt = stage_two_started_wallclock
    semaphore = asyncio.Semaphore(max(1, stage_two_concurrency))
    stage2_done = 0
    stage2_lock = asyncio.Lock()

    async def _emit(payload: dict[str, object]) -> None:
        if on_progress is not None:
            await on_progress(payload)
        await asyncio.sleep(0)

    if on_progress is not None:
        await _emit({
            "stage": "stage2" if symbols is None else "explicit",
            "processed": 0,
            "total": len(stage_two_universe),
            "message": "Deep research on promoted names" if symbols is None else "Researching requested names",
            "cache_hits": getattr(scan_provider, "hits", 0),
            "cache_misses": getattr(scan_provider, "misses", 0),
        })

    async def _analyze_one(symbol: str) -> tuple[str, AnalyzeResponse]:
        nonlocal stage2_done
        async with semaphore:
            symbol_as_of = as_of + (utc_now() - stage_two_started_wallclock)
            try:
                response = await run_analysis(
                    symbol, provider=scan_provider, instrument_master=instrument_master, strategy=strategy,
                    repositories=repositories, config=config, as_of=symbol_as_of, mcx_instrument_master=mcx_instrument_master,
                    sector_map=sector_map, journal=journal,
                    nifty50_symbols=nifty50_symbols, http_client=http_client, delivery_cache_dir=delivery_cache_dir,
                )
            except Exception as exc:  # noqa: BLE001 -- one symbol's unexpected failure must never break the rest of the daily scan
                response = AnalyzeResponse(
                    query=symbol, parsed_symbol=None, parsed_strike=None, parsed_right=None, parsed_expiry_hint=None,
                    has_specific_contract=False, parse_warnings=[], error=f"unexpected failure: {exc}", latency_seconds=0.0,
                )
            async with stage2_lock:
                stage2_done += 1
                done = stage2_done
            await _emit({
                "stage": "stage2",
                "processed": done,
                "total": len(stage_two_universe),
                "symbol": symbol,
                "total_ms": int(response.latency_seconds * 1000),
                "cache_hit": "hit" if getattr(scan_provider, "hits", 0) else "miss",
                "provider": str(getattr(scan_provider, "name", getattr(getattr(scan_provider, "_inner", None), "name", "unknown"))),
                "message": f"Deep research {done}/{len(stage_two_universe)}",
                "cache_hits": getattr(scan_provider, "hits", 0),
                "cache_misses": getattr(scan_provider, "misses", 0),
            })
            return symbol, response

    # Order of completion is not guaranteed under concurrency, but the
    # RESULT is identical to the old sequential loop for every symbol:
    # `responses` is a dict keyed by symbol (order-independent), and each
    # symbol's own `run_analysis()` call receives the same kind of
    # monotonically-advancing `symbol_as_of` (now advanced from the same
    # scan start, not from a serialized predecessor -- honest, since the
    # symbols are now genuinely fetched concurrently, not sequentially).
    analyzed = await asyncio.gather(*(_analyze_one(symbol) for symbol in stage_two_universe))
    responses_by_symbol = dict(analyzed)
    responses: dict[str, AnalyzeResponse] = {}
    market_state: str | None = None
    for symbol in stage_two_universe:
        response = responses_by_symbol[symbol]
        responses[symbol] = response
        # Deterministic regardless of completion order: the FIRST real
        # market_state in `stage_two_universe`'s own fixed order -- every
        # symbol analyzed in the same scan is observed during the same
        # market session, so this is not an arbitrary pick among
        # disagreeing values, just a deterministic tie-break.
        if market_state is None and response.market_state is not None:
            market_state = response.market_state

    scan_completed_wallclock_dt = utc_now()
    stage2_elapsed = (scan_completed_wallclock_dt - stage_two_started_wallclock).total_seconds()

    shortlist, stage_two_rejected = rank_candidates(
        responses, top_n=top_n, stage_one_buy_sell_ratio=stage_one_buy_sell_ratio,
        stage_one_observations=stage_one_observations,
    )
    stage_two_gated = collect_gated_candidates(
        responses, stage_one_buy_sell_ratio=stage_one_buy_sell_ratio,
        stage_one_observations=stage_one_observations,
    )
    all_rejected = stage_one_rejected + stage_two_rejected
    rejection_summary = summarize_rejections(all_rejected)
    coverage = compute_research_coverage(
        universe_count=len(full_universe), stage_one_attempted=stage_one_attempted, stage_one_rejected=stage_one_rejected,
        stage_two_attempted=len(responses), stage_two_rejected=stage_two_rejected,
    )

    stage2_failed_symbols = _reliability_failed_symbols(stage_two_rejected)
    stage2_latencies = [r.latency_seconds for r in responses.values()]
    avg_stage2_latency = (sum(stage2_latencies) / len(stage2_latencies)) if stage2_latencies else 0.0
    scan_snapshot = ResearchScanSnapshot(
        started_at=scan_started_wallclock_dt, completed_at=scan_completed_wallclock_dt,
        duration_seconds=(scan_completed_wallclock_dt - scan_started_wallclock_dt).total_seconds(),
        universe_version=f"{len(full_universe)} F&O-eligible equity underlyings",
        requested_symbols=len(stage_two_universe), successful_symbols=len(responses) - len(stage2_failed_symbols),
        failed_symbols=len(stage2_failed_symbols), provider="upstox", coverage=coverage,
        per_symbol_freshness=[
            SymbolFreshnessRecord(
                symbol=symbol, generated_at=response.generated_at, latency_seconds=response.latency_seconds,
                market_state=response.market_state, succeeded=response.error is None,
            )
            for symbol, response in responses.items()
        ],
        generated_at=as_of,
        universe_source=universe_source,
        fno_ban_status=FNO_BAN_STATUS_UNKNOWN,
        universe_discovery_seconds=universe_discovery_seconds,
        stage1_seconds=stage1_seconds,
        stage2_seconds=stage2_elapsed,
        assembly_seconds=0.0,
        avg_stage2_latency_seconds=avg_stage2_latency,
        survivor_cap_applied=stage1_cap_applied,
        truncated_at_stage1=stage1_truncated_count,
        index_relative_strength_available=index_rs_available,
    )

    result = DailyResearchResult(
        generated_at=as_of, market_state=market_state, universe=full_universe, screened_count=len(full_universe),
        deep_analyzed_count=len(responses), rejected=all_rejected, shortlist=shortlist,
        no_high_conviction=not shortlist, stage_one_survivor_count=stage_one_survivor_count,
        rejection_summary=rejection_summary, coverage=coverage, scan_snapshot=scan_snapshot,
        stage_two_gated=stage_two_gated,
        universe_source=universe_source,
        fno_ban_status=FNO_BAN_STATUS_UNKNOWN,
        stage1_truncated_count=stage1_truncated_count,
        stage1_cap_applied=stage1_cap_applied,
        index_relative_strength_available=index_rs_available,
    )

    run_id = new_run_id()

    if on_progress is not None:
        await on_progress({
            "stage": "complete",
            "processed": len(stage_two_universe),
            "total": len(stage_two_universe),
            "message": "Scan complete.",
            "cache_hits": getattr(scan_provider, "hits", 0),
            "cache_misses": getattr(scan_provider, "misses", 0),
        })

    if research_journal is not None:
        record = ResearchRunRecord(
            run_id=run_id, generated_at=as_of, market_state=market_state or "UNKNOWN", universe=full_universe,
            screened_count=len(full_universe), deep_analyzed_count=len(responses), rejected=all_rejected,
            shortlist=[
                ShortlistRecord(
                    rank=c.rank, symbol=c.symbol, audit_id=c.response.audit_id,
                    selected_right=c.contract.right, selected_strike=str(c.contract.strike), rationale=c.rationale,
                    research_confidence=classify_research_confidence(
                        _GatedCandidate(
                            symbol=c.symbol, response=c.response, dc=c.dc, contract=c.contract, direction=c.direction,
                            stage_one_buy_sell_ratio=c.stage_one_buy_sell_ratio,
                        )
                    ),
                    early_stage_state=classify_early_stage_state(
                        _GatedCandidate(
                            symbol=c.symbol, response=c.response, dc=c.dc, contract=c.contract, direction=c.direction,
                            stage_one_buy_sell_ratio=c.stage_one_buy_sell_ratio,
                        )
                    )[0],
                    participation_note=_participation_note(
                        _GatedCandidate(
                            symbol=c.symbol, response=c.response, dc=c.dc, contract=c.contract, direction=c.direction,
                            stage_one_buy_sell_ratio=c.stage_one_buy_sell_ratio,
                        )
                    ),
                    market_context=_market_context(c.response, c.direction)[0],
                )
                for c in shortlist
            ],
            no_high_conviction=not shortlist, stage_one_survivor_count=stage_one_survivor_count,
            rejection_summary=rejection_summary,
            rejected_as_extended_count=rejection_summary.get("ALREADY_EXTENDED", 0),
            rejected_insufficient_evidence_count=rejection_summary.get("INSUFFICIENT_EARLY_STAGE_EVIDENCE", 0),
            coverage=coverage,
        )
        await research_journal.save_run(record)

    if outcome_repository is not None:
        # Sprint 3 -- one immutable ResearchObservation per shortlisted
        # candidate, for later outcome tracking
        # (`app.orchestration.research_outcome`). Reuses the SAME
        # `build_research_thesis()` the API/UI already calls -- never a
        # second computation. A shortlist of length 0 (a real
        # NO_HIGH_CONVICTION result) simply persists nothing -- never
        # manufactured.
        coverage_classification = coverage.classification
        for c in shortlist:
            observation = build_research_observation(
                c, build_research_thesis(c), run_id=run_id, coverage_classification=coverage_classification,
            )
            await outcome_repository.save_observation(observation)

    return result


# ============================================================
# Early-stage state (Stage 2) -- classification over data ALREADY
# fetched for the existing evidence matrix/chart (`visual.price_chart`'s
# `candles`/`ema`/`vwap` series, `visual.evidence`'s M15-trend/VWAP rows,
# and this module's own `_headroom_pct()`). Zero new fetches, zero new
# EMA/VWAP computation (the existing evidence rows are READ, never
# recomputed -- avoiding both a duplicate implementation and double-
# counting the same evidence). This is presentation/classification over
# Stage 2's existing output -- it never touches `direction_comparison`,
# never gates Stage 2, and is never used as a ranking key (`_sort_key()`
# is unchanged) -- so it cannot become a second competing decision
# engine.
# ============================================================

# How many recent M15 candles count as "recent structure" for a swing
# high/low -- ~1.6 trading sessions (NSE trading day ~=25 M15 bars),
# conservative and documented, not a universal/arbitrary number reused
# from anywhere else. Below the minimum, the state is honestly
# INSUFFICIENT_DATA rather than guessed from a thin sample. Stage 2
# already fetches up to `PipelineConfig.history_lookback` (10 real
# days) of M15 history for the existing evidence matrix -- this window
# is a conservative SUBSET of what's already fetched, not a new fetch.
EARLY_STAGE_LOOKBACK_CANDLES = 40
EARLY_STAGE_MIN_CANDLES = 20
# The most recent candles checked for a real "broke, then fell back"
# false-breakout signal -- ~1 hour, deliberately short (a breakout
# attempt failing within the last few bars, not days ago).
EARLY_STAGE_RECENT_CANDLES_FOR_BREAKOUT_CHECK = 4
# Severity tiers for how far price has broken past the recent real swing
# extreme, in the thesis's own direction -- same conservative,
# real-percentage-threshold discipline as Stage 1's intraday-extension
# check, applied here to the real M15 structure. Ordered:
# not-yet-broken (<=2%) -> BREAKOUT_CONFIRMATION (2-5%, holding) ->
# EXTENDED (5-8%) -> EXHAUSTION_RISK (>8%).
EARLY_STAGE_EXTENDED_BEYOND_SWING_PCT = Decimal("2.0")
EARLY_STAGE_BREAKOUT_HOLD_MAX_PCT = Decimal("5.0")
EARLY_STAGE_EXHAUSTION_BEYOND_SWING_PCT = Decimal("8.0")


def _evidence_direction(response: AnalyzeResponse, row_name: str) -> str | None:
    """Reads the ALREADY-COMPUTED direction of one named evidence row
    (e.g. "M15 trend", "VWAP") -- never recomputes EMA/VWAP math."""
    v = response.visual
    if v is None:
        return None
    return next((row.direction for row in v.evidence if row.name == row_name), None)


def _market_regime_value(response: AnalyzeResponse) -> str | None:
    """Reads the ALREADY-COMPUTED real regime classification (`MarketRegime`
    -- TRENDING_BULLISH/TRENDING_BEARISH/RANGE/HIGH_VOLATILITY/
    LOW_VOLATILITY/MIXED/DATA_INSUFFICIENT, from real ATR+EMA+VWAP) off
    the existing "Market regime" evidence row's `detail` text (its first
    token, per `row_market_regime()` in evidence_matrix.py -- confirmed
    by reading that function directly). Never recomputes ATR/regime math;
    this is the real, already-fetched compression/volatility signal."""
    v = response.visual
    if v is None:
        return None
    row = next((r for r in v.evidence if r.name == "Market regime"), None)
    if row is None or ":" not in row.detail:
        return None
    return row.detail.split(":", 1)[0].strip()


def _recent_candles(response: AnalyzeResponse) -> list[CandlePoint]:
    v = response.visual
    if v is None or v.price_chart is None:
        return []
    return list(v.price_chart.candles[-EARLY_STAGE_LOOKBACK_CANDLES:])


def _recent_swing_extreme(response: AnalyzeResponse, direction: str) -> Decimal | None:
    """The real recent swing high (bullish thesis) or low (bearish
    thesis) over the last `EARLY_STAGE_LOOKBACK_CANDLES` M15 candles
    already on `visual.price_chart` -- no new fetch. `None` when there
    isn't enough real candle history to trust it."""
    candles = _recent_candles(response)
    if len(candles) < EARLY_STAGE_MIN_CANDLES:
        return None
    if direction == "BULLISH":
        return max(c.high for c in candles)
    return min(c.low for c in candles)


def _false_breakout_detected(response: AnalyzeResponse, direction: str, spot: Decimal) -> bool:
    """Real, computed off the SAME already-fetched candle series: did the
    most recent few candles break past the PRIOR swing extreme, only for
    the latest close (and current spot) to fall back through it? A
    genuine "broke, then failed" signal -- never guessed."""
    candles = _recent_candles(response)
    n = EARLY_STAGE_RECENT_CANDLES_FOR_BREAKOUT_CHECK
    if len(candles) < EARLY_STAGE_MIN_CANDLES + n:
        return False
    prior_window, recent_window = candles[:-n], candles[-n:]
    latest_close = recent_window[-1].close
    if direction == "BULLISH":
        prior_extreme = max(c.high for c in prior_window)
        recent_extreme = max(c.high for c in recent_window)
        broke_out = recent_extreme > prior_extreme
        fell_back = latest_close < prior_extreme and spot <= prior_extreme
    else:
        prior_extreme = min(c.low for c in prior_window)
        recent_extreme = min(c.low for c in recent_window)
        broke_out = recent_extreme < prior_extreme
        fell_back = latest_close > prior_extreme and spot >= prior_extreme
    return broke_out and fell_back


def classify_early_stage_state(candidate: _GatedCandidate) -> tuple[str, Decimal | None]:
    """Returns (state, extension_beyond_swing_pct). One of RANGE_BOUND /
    EARLY_DIRECTIONAL_BUILD / DEVELOPING_MOMENTUM / BREAKOUT_CONFIRMATION /
    FALSE_BREAKOUT_RISK / EXTENDED / EXHAUSTION_RISK / INSUFFICIENT_DATA.
    Deterministic precedence, no numeric blend -- see the module-level
    comment above for exactly which already-computed real fields feed
    this."""
    swing = _recent_swing_extreme(candidate.response, candidate.direction)
    spot = candidate.dc.paths.spot if candidate.dc.paths else None
    if swing is None or spot is None or swing == 0:
        return "INSUFFICIENT_DATA", None

    if _false_breakout_detected(candidate.response, candidate.direction, spot):
        return "FALSE_BREAKOUT_RISK", None

    if candidate.direction == "BULLISH":
        extension_pct = (spot - swing) / swing * Decimal(100)
    else:
        extension_pct = (swing - spot) / swing * Decimal(100)

    if extension_pct > EARLY_STAGE_EXHAUSTION_BEYOND_SWING_PCT:
        return "EXHAUSTION_RISK", extension_pct
    if extension_pct > EARLY_STAGE_BREAKOUT_HOLD_MAX_PCT:
        return "EXTENDED", extension_pct
    if extension_pct > EARLY_STAGE_EXTENDED_BEYOND_SWING_PCT:
        return "BREAKOUT_CONFIRMATION", extension_pct

    # Not yet broken past recent structure -- classify by how much of the
    # real evidence (regime + trend + VWAP, all already computed) already
    # agrees with the thesis direction. This is the "quiet before move"
    # branch: reachable at ANY day-change magnitude, including a tiny one.
    regime = _market_regime_value(candidate.response)
    trend_direction = _evidence_direction(candidate.response, "M15 trend")
    vwap_direction = _evidence_direction(candidate.response, "VWAP")
    trend_confirms = trend_direction == candidate.direction
    vwap_confirms = vwap_direction == candidate.direction
    regime_trending_this_way = (
        (regime == "TRENDING_BULLISH" and candidate.direction == "BULLISH")
        or (regime == "TRENDING_BEARISH" and candidate.direction == "BEARISH")
    )

    if regime_trending_this_way and trend_confirms and vwap_confirms:
        return "DEVELOPING_MOMENTUM", extension_pct
    if trend_confirms or vwap_confirms:
        return "EARLY_DIRECTIONAL_BUILD", extension_pct
    return "RANGE_BOUND", extension_pct


# ============================================================
# Final Hardening Pass, Phase 19 -- quiet-setup-vs-chasing AUDIT.
#
# NOT a score, NOT a new field on any persisted record, NOT fed into
# ranking -- a pure, deterministic REPORT over a run's own already-
# computed shortlist, reusing `classify_early_stage_state()` exactly the
# way the journal-write path above already does (same `_GatedCandidate`
# reconstruction, same function, never a second implementation). Exists
# to make one question directly auditable: is this system disproportion-
# ately shortlisting stocks simply because they already made the largest
# recent move, or is it genuinely surfacing developing setups before
# confirmation? See `EarlyStageDistribution`'s own docstring.
# ============================================================


@dataclass(frozen=True)
class EarlyStageDistribution:
    """One run's shortlist, broken down by real `early_stage_state` --
    an already-computed fact for every candidate, never recalculated or
    blended into a single number. `already_extended_or_later_count`
    counts candidates whose OWN real classification is BREAKOUT_CONFIRMATION/
    EXTENDED/EXHAUSTION_RISK/FALSE_BREAKOUT_RISK (i.e. the move has already
    happened by the system's own definition) -- compare against
    `early_or_developing_count` (RANGE_BOUND/EARLY_DIRECTIONAL_BUILD/
    DEVELOPING_MOMENTUM) to see which the shortlist actually favors."""

    state_counts: dict[str, int]
    early_or_developing_count: int
    already_extended_or_later_count: int


_EARLY_OR_DEVELOPING_STATES = ("RANGE_BOUND", "EARLY_DIRECTIONAL_BUILD", "DEVELOPING_MOMENTUM")
_ALREADY_EXTENDED_OR_LATER_STATES = ("BREAKOUT_CONFIRMATION", "EXTENDED", "EXHAUSTION_RISK", "FALSE_BREAKOUT_RISK")


def summarize_early_stage_distribution(shortlist: list[RankedCandidate]) -> EarlyStageDistribution:
    """Pure aggregation over an already-produced shortlist -- zero new
    fetch, zero new evidence computation. Reads each candidate's own real
    `early_stage_state` via the SAME `classify_early_stage_state()` call
    the journal-write path already makes (never a second implementation),
    and simply counts them."""
    state_counts: dict[str, int] = {}
    for c in shortlist:
        gated = _GatedCandidate(
            symbol=c.symbol, response=c.response, dc=c.dc, contract=c.contract, direction=c.direction,
            stage_one_buy_sell_ratio=c.stage_one_buy_sell_ratio,
        )
        state, _ = classify_early_stage_state(gated)
        state_counts[state] = state_counts.get(state, 0) + 1

    early_count = sum(state_counts.get(s, 0) for s in _EARLY_OR_DEVELOPING_STATES)
    extended_count = sum(state_counts.get(s, 0) for s in _ALREADY_EXTENDED_OR_LATER_STATES)
    return EarlyStageDistribution(
        state_counts=state_counts, early_or_developing_count=early_count, already_extended_or_later_count=extended_count,
    )


# ============================================================
# Section 8 -- research thesis synthesis, and the presentation-layer view
# used by the API/UI. Every sentence below is copied/derived directly
# from an already-computed real field -- never invented. Mirrors the
# style of `options_intelligence_report.py`'s confirmation/invalidation copy
# synthesis and the frontend's `findRelevantWatchCondition()`.
# ============================================================


def _relevant_watch_condition(response: AnalyzeResponse, direction: str) -> str | None:
    """The single existing watch_next condition most relevant to this
    candidate's own thesis direction (bullish -> nearest-support
    condition; bearish -> nearest-resistance condition) -- the exact same
    selection the frontend's `findRelevantWatchCondition()` makes,
    reimplemented here so the API response doesn't require the browser
    to synthesize the thesis. Never invents a new condition."""
    needle = "remains above the nearest support" if direction == "BULLISH" else "remains below the nearest resistance"
    for condition in response.watch_next:
        if needle in condition.watch.lower():
            return condition.watch
    return None


class SideCaseView(BaseModel):
    """The BULLISH or BEARISH case for one symbol, built from fields
    `direction_comparison` already computes for BOTH sides unconditionally
    (`ce_assessment`/`pe_assessment`/`ce_case`/`pe_case`) -- never derived
    only for whichever side happens to be preferred. This is what proves
    the researcher looked at both sides rather than assuming CE."""

    direction: str
    verdict: str
    supporting_groups: int
    opposing_groups: int
    preferred: bool
    contract_right: str | None
    contract_strike: str | None
    contract_ltp: str | None
    structural_quality: str | None
    liquidity_grade: str | None
    decay_verdict: str | None
    required_underlying_move_pct: str | None
    contractual_expiry_breakeven: str | None
    breakeven_distance_pct: str | None
    could_work: list[str]
    could_fail: list[str]
    what_would_confirm: str | None


def _build_side_case(response: AnalyzeResponse, dc: DirectionComparisonVisual, direction: str, *, preferred: bool) -> SideCaseView:
    assessment = dc.ce_assessment if direction == "BULLISH" else dc.pe_assessment
    case = dc.ce_case if direction == "BULLISH" else dc.pe_case
    supporting = dc.bullish_supporting_groups if direction == "BULLISH" else dc.bearish_supporting_groups
    opposing = dc.bearish_supporting_groups if direction == "BULLISH" else dc.bullish_supporting_groups
    spot = dc.paths.spot if dc.paths else None
    be_pct = _underlying_move_to_breakeven_pct(assessment, spot)
    return SideCaseView(
        direction=direction, verdict=dc.verdict, supporting_groups=supporting, opposing_groups=opposing, preferred=preferred,
        contract_right=assessment.right if assessment else None,
        contract_strike=str(assessment.strike) if assessment else None,
        contract_ltp=str(assessment.ltp) if assessment is not None and assessment.ltp is not None else None,
        structural_quality=assessment.structural_quality if assessment else None,
        liquidity_grade=assessment.liquidity_grade if assessment else None,
        decay_verdict=assessment.decay_verdict if assessment else None,
        required_underlying_move_pct=str(assessment.required_underlying_move_pct) if assessment is not None and assessment.required_underlying_move_pct is not None else None,
        contractual_expiry_breakeven=str(assessment.contractual_expiry_breakeven) if assessment is not None and assessment.contractual_expiry_breakeven is not None else None,
        breakeven_distance_pct=str(be_pct) if be_pct is not None else None,
        could_work=list(case.could_work), could_fail=list(case.could_fail),
        what_would_confirm=_relevant_watch_condition(response, direction),
    )


_LIQUIDITY_RANK_FOR_COMPARISON = {"excellent": 3, "good": 2, "poor": 1, "untradeable": 0}
_DECAY_RANK_FOR_COMPARISON = {"DECAY_FAVORABLE": 3, "DECAY_ACCEPTABLE": 2, "DECAY_HEADWIND": 1, "DECAY_UNFAVORABLE": 0}


def _why_this_contract(dc: DirectionComparisonVisual, direction: str, preferred_contract: ContractAssessmentView) -> str:
    """A real, plain-language comparison against the nearest already-
    computed alternative strike (`ce_alternatives`/`pe_alternatives`
    -- built for every analysis, not just an explicitly-requested
    contract), across spread/required-move/liquidity/decay -- the SAME
    four real, non-redundant dimensions the frontend's `dominanceVerdict()`
    treats as independent (see that function's own documented reasoning
    for why each is non-redundant). Deliberately NOT a full port of its
    5-dimension machinery -- a plain-language summary of real differences
    is enough to answer "why this contract" without a second
    implementation of the same comparison logic."""
    alternatives = dc.ce_alternatives if direction == "BULLISH" else dc.pe_alternatives
    if not alternatives:
        return "no nearby alternative strikes were available for comparison this run."
    nearest = min(alternatives, key=lambda a: abs(a.strike - preferred_contract.strike))
    parts: list[str] = []
    if nearest.spread_pct is not None and preferred_contract.spread_pct is not None and nearest.spread_pct != preferred_contract.spread_pct:
        parts.append("tighter spread" if preferred_contract.spread_pct < nearest.spread_pct else "wider spread")
    if (
        nearest.required_underlying_move_pct is not None
        and preferred_contract.required_underlying_move_pct is not None
        and nearest.required_underlying_move_pct != preferred_contract.required_underlying_move_pct
    ):
        parts.append(
            "a lower required move to cover modeled cost"
            if preferred_contract.required_underlying_move_pct < nearest.required_underlying_move_pct
            else "a higher required move to cover modeled cost"
        )
    preferred_liquidity_rank = _LIQUIDITY_RANK_FOR_COMPARISON.get(preferred_contract.liquidity_grade)
    nearest_liquidity_rank = _LIQUIDITY_RANK_FOR_COMPARISON.get(nearest.liquidity_grade)
    if preferred_liquidity_rank is not None and nearest_liquidity_rank is not None and preferred_liquidity_rank != nearest_liquidity_rank:
        parts.append("better liquidity" if preferred_liquidity_rank > nearest_liquidity_rank else "worse liquidity")
    preferred_decay_rank = _DECAY_RANK_FOR_COMPARISON.get(preferred_contract.decay_verdict or "")
    nearest_decay_rank = _DECAY_RANK_FOR_COMPARISON.get(nearest.decay_verdict or "")
    if preferred_decay_rank is not None and nearest_decay_rank is not None and preferred_decay_rank != nearest_decay_rank:
        parts.append("more favorable decay" if preferred_decay_rank > nearest_decay_rank else "less favorable decay")
    if not parts:
        return f"compared to the nearest alternative ({nearest.right} {nearest.strike}): no material difference on spread, required move, liquidity, or decay this run."
    return f"compared to the nearest alternative ({nearest.right} {nearest.strike}): {', '.join(parts)}."


_GLOBAL_CONTEXT_VERDICT_LABELS = {
    "GLOBAL_TAILWIND": "TAILWIND", "GLOBAL_HEADWIND": "HEADWIND", "MIXED": "MIXED",
    "LOW_RELEVANCE": "LOW RELEVANCE", "INSUFFICIENT_DATA": "INSUFFICIENT DATA",
}


def _market_context(response: AnalyzeResponse, direction: str) -> tuple[str, str]:
    """SUPPORTIVE/OPPOSING/NEUTRAL/UNKNOWN + a real reason, derived only
    from the already-computed `global_context.verdict` (GLOBAL_TAILWIND/
    GLOBAL_HEADWIND/MIXED/LOW_RELEVANCE/INSUFFICIENT_DATA -- real NIFTY/
    BANKNIFTY/VIX context Stage 2 already fetches). That verdict is
    already one of the evidence groups feeding `dc.verdict` (see
    `evidence_matrix.py`'s `GlobalContextVerdict` -> `EvidenceDirection`
    mapping) -- this only *reports* it against the candidate's own
    preferred direction, never a second ranking input on top of the
    supporting-group count `dc.verdict` already reflects."""
    v = response.visual
    if v is None or v.global_context is None or v.global_context.verdict is None:
        return "UNKNOWN", "no real NIFTY/BANKNIFTY/VIX context was computed for this run"
    verdict = v.global_context.verdict
    label = _GLOBAL_CONTEXT_VERDICT_LABELS.get(verdict, verdict)
    detail = v.global_context.detail or f"global context verdict: {label}"
    if verdict == "GLOBAL_TAILWIND":
        return ("SUPPORTIVE" if direction == "BULLISH" else "OPPOSING"), detail
    if verdict == "GLOBAL_HEADWIND":
        return ("OPPOSING" if direction == "BULLISH" else "SUPPORTIVE"), detail
    return "NEUTRAL", detail


def _room_to_breakeven_text(candidate: _GatedCandidate, contract: ContractAssessmentView) -> str | None:
    """"STRUCTURAL ROOM TO BREAKEVEN" -- compares the already-computed
    `contractual_expiry_breakeven` against the same real recent swing
    high/low `classify_early_stage_state()` already establishes, to say
    whether the breakeven realistically falls within the structure the
    underlying has actually traded through recently. Deliberately NOT
    "profit potential" -- a real structural comparison, nothing predictive,
    and S/R stays contextual here exactly as everywhere else in this
    module (never a directional vote)."""
    breakeven = contract.contractual_expiry_breakeven
    if breakeven is None:
        return None
    swing = _recent_swing_extreme(candidate.response, candidate.direction)
    if swing is None:
        return "structural room to breakeven could not be assessed -- insufficient recent M15 history for a swing reference."
    within_range = breakeven <= swing if candidate.direction == "BULLISH" else breakeven >= swing
    swing_label = "recent swing high" if candidate.direction == "BULLISH" else "recent swing low"
    if within_range:
        return f"breakeven {breakeven} sits within the recent M15 structure ({swing_label} {swing}) -- the underlying has already traded through this level recently."
    distance_pct = abs(breakeven - swing) / swing * Decimal(100)
    return (
        f"breakeven {breakeven} sits beyond the {swing_label} ({swing}) by {distance_pct:.2f}% -- "
        "the underlying has not recently traded through this level; reaching breakeven would require a fresh structural move, not just a return to recent highs/lows."
    )


def _participation_note(candidate: _GatedCandidate) -> str:
    """A real, honest report of the Stage-1 buy/sell quantity ratio, when
    one was computed -- deliberately called "participation," never
    "accumulation": a buy/sell quantity imbalance alone cannot distinguish
    genuine accumulation from any number of other real causes (a single
    large order, index-linked flow, etc.), so this module makes no
    stronger claim than the data supports (see the objective this
    implements: honesty over terminology)."""
    ratio = candidate.stage_one_buy_sell_ratio
    if ratio is None:
        return "no real Stage-1 order-flow signal available this run (either Stage 1 was skipped for an explicit symbol query, or no real buy/sell quantity data was present on the quote)."
    if ratio >= Decimal("1.3"):
        return f"real Stage-1 buy/sell quantity ratio was {ratio:.2f}:1 -- more buy-side than sell-side quantity was recorded today (participation build, not confirmed accumulation)."
    if ratio <= Decimal(1) / Decimal("1.3"):
        return f"real Stage-1 buy/sell quantity ratio was {ratio:.2f}:1 -- more sell-side than buy-side quantity was recorded today (participation build, not confirmed distribution)."
    return f"real Stage-1 buy/sell quantity ratio was {ratio:.2f}:1 -- no material buy/sell imbalance was recorded today."


# ============================================================
# Sprint 4 -- MULTI-DAY EARLY-MOVE CONTEXT: real, deterministic signals
# computed from the FULL already-fetched M15 candle history
# (`visual.price_chart.candles` -- confirmed by reading
# `_build_price_chart()` in visual_data.py: it carries every real candle
# Stage 2 fetched, up to `PipelineConfig.history_lookback` (10 real
# days), never sliced to `EARLY_STAGE_LOOKBACK_CANDLES` like the
# single-day maturity classifier above uses). Zero new fetches -- this
# reads a WIDER window of the SAME data already on the response.
#
# Deliberately REPORTING-ONLY: none of these are read by `_sort_key()`/
# `rank_candidates()` or fed into `classify_early_stage_state()`'s own
# ordinal -- see each function's own docstring for why it is an
# independent, non-duplicate signal, never a second vote on top of
# evidence a higher-level field already reflects.
# ============================================================

# "Recent" ~= 1 real trading session (~25 M15 bars during NSE's ~6.25h
# session); "baseline" is everything real and older than that, up to
# whatever the 10-day fetch provides. A larger recent window than
# `EARLY_STAGE_LOOKBACK_CANDLES` (40, ~1.6 sessions) would blur "today's
# behavior" into the multi-day baseline it's being compared against.
MULTI_DAY_RECENT_CANDLES = 26
# The baseline needs a real, meaningfully longer history than "recent" to
# be a genuine multi-day comparison, not noise -- ~2 real sessions,
# conservative and honest: below this, INSUFFICIENT_DATA, never guessed.
MULTI_DAY_BASELINE_MIN_CANDLES = 50
# Recent average per-candle range at or below 60% of the baseline average
# -- a real >=40% contraction in realized volatility -- is the bar for
# calling it genuine compression, not merely "a bit quieter." Round,
# documented, not fitted.
RANGE_COMPRESSION_MAX_RATIO = Decimal("0.6")
# Reuses the EXACT SAME real imbalance threshold Stage 1's own
# `ScreeningConfig.participation_min_imbalance_ratio` already uses for
# buy/sell quantity -- the same "meaningfully imbalanced" bar, applied
# here to real M15 volume instead, not a new invented number.
PARTICIPATION_BUILD_MIN_RATIO = Decimal("1.3")
PARTICIPATION_WEAK_MAX_RATIO = Decimal(1) / PARTICIPATION_BUILD_MIN_RATIO
# The same real "meaningful move" order of magnitude
# `PipelineConfig.global_context_meaningful_move_pct` already defaults to
# for classifying a real index day-change as directionally meaningful.
RELATIVE_STRENGTH_MEANINGFUL_MOVE_PCT = Decimal("0.3")
# How close (real S/R headroom, already computed by `_headroom_pct()`)
# price must be to the nearest opposing level for a real compression +
# early-build read to also count as "pressure building near a level" --
# the PRE_BREAKOUT signal. Round, conservative.
PRE_BREAKOUT_MAX_HEADROOM_PCT = Decimal("3.0")


def _multi_day_windows(response: AnalyzeResponse) -> tuple[list[CandlePoint], list[CandlePoint]] | None:
    """(recent, baseline) -- the FULL real M15 series already fetched
    (unsliced, unlike `_recent_candles()`), split into a real "last
    session" window and everything real and older. `None` when there
    isn't enough real history in EITHER window to trust a comparison."""
    v = response.visual
    if v is None or v.price_chart is None:
        return None
    candles = list(v.price_chart.candles)
    if len(candles) < MULTI_DAY_RECENT_CANDLES + MULTI_DAY_BASELINE_MIN_CANDLES:
        return None
    recent = candles[-MULTI_DAY_RECENT_CANDLES:]
    baseline = candles[:-MULTI_DAY_RECENT_CANDLES]
    return recent, baseline


def classify_structural_context(candidate: _GatedCandidate) -> tuple[str, str]:
    """RANGE_COMPRESSION / NORMAL_RANGE / INSUFFICIENT_DATA -- compares
    the real average per-candle (high-low) range in the recent window
    against the real baseline window's own average range, over the SAME
    already-fetched multi-day M15 series `classify_early_stage_state()`
    only uses a 40-candle slice of. Independent of that classifier (a
    different, wider window; a range measure, not a directional one) --
    never a second vote on direction, never merged into
    `early_stage_state`'s own ordinal. A genuine range contraction is
    the real precondition for "quiet base" behavior this sprint asks to
    detect; it does NOT by itself claim a direction or a breakout.

    Invalidated by: the ratio rising back above the threshold at a later
    run (a real, later observation, not tracked here). Missing data is
    reported as INSUFFICIENT_DATA, honestly, whenever either window is
    too thin -- never approximated from a partial window."""
    windows = _multi_day_windows(candidate.response)
    if windows is None:
        return "INSUFFICIENT_DATA", "not enough real M15 history for a multi-day range comparison"
    recent, baseline = windows
    recent_avg_range = sum((c.high - c.low for c in recent), Decimal(0)) / len(recent)
    baseline_avg_range = sum((c.high - c.low for c in baseline), Decimal(0)) / len(baseline)
    if baseline_avg_range <= 0:
        return "INSUFFICIENT_DATA", "baseline window shows no real range to compare against"
    ratio = recent_avg_range / baseline_avg_range
    if ratio <= RANGE_COMPRESSION_MAX_RATIO:
        return "RANGE_COMPRESSION", f"recent average M15 range is {ratio:.2f}x the real {len(baseline)}-candle baseline -- a genuine contraction"
    return "NORMAL_RANGE", f"recent average M15 range is {ratio:.2f}x the real {len(baseline)}-candle baseline -- no meaningful contraction"


def classify_participation_depth(candidate: _GatedCandidate) -> tuple[str, str]:
    """PARTICIPATION_BUILD / PARTICIPATION_CONFIRMING / PARTICIPATION_WEAK
    / PARTICIPATION_UNAVAILABLE -- real M15 volume (already fetched on
    every candle, `CandlePoint.volume`) in the recent window vs the real
    baseline window's own average -- the stock's OWN recent history, not
    a market-wide or sector baseline (neither of which this provider
    supplies -- never fabricated). Deliberately never "accumulation"/
    "distribution" -- a volume ratio alone cannot support that stronger
    claim (see `_participation_note()`'s own reasoning, reused here)."""
    windows = _multi_day_windows(candidate.response)
    if windows is None:
        return "PARTICIPATION_UNAVAILABLE", "not enough real M15 history for a multi-day participation comparison"
    recent, baseline = windows
    recent_avg_volume = Decimal(sum(c.volume for c in recent)) / len(recent)
    baseline_avg_volume = Decimal(sum(c.volume for c in baseline)) / len(baseline)
    if baseline_avg_volume <= 0:
        return "PARTICIPATION_UNAVAILABLE", "baseline window shows no real volume to compare against"
    ratio = recent_avg_volume / baseline_avg_volume
    if ratio >= PARTICIPATION_BUILD_MIN_RATIO:
        return "PARTICIPATION_BUILD", f"recent average M15 volume is {ratio:.2f}x the real {len(baseline)}-candle baseline"
    if ratio <= PARTICIPATION_WEAK_MAX_RATIO:
        return "PARTICIPATION_WEAK", f"recent average M15 volume is {ratio:.2f}x the real {len(baseline)}-candle baseline"
    return "PARTICIPATION_CONFIRMING", f"recent average M15 volume is {ratio:.2f}x the real {len(baseline)}-candle baseline -- in line with its own recent behavior"


def classify_relative_strength(candidate: _GatedCandidate) -> tuple[str, str]:
    """RELATIVE_STRENGTH_ALIGNED / RELATIVE_STRENGTH_DIVERGING /
    RELATIVE_STRENGTH_INDEX_FLAT / RELATIVE_STRENGTH_DATA_UNAVAILABLE.

    Honesty note (Sprint 4, Phase 4): this compares the candidate's OWN
    real move over its recent M15 window against NIFTY's real, already-
    fetched `day_change_pct` (`visual.global_context` -- the SAME real
    NIFTY/BANKNIFTY/VIX quote Stage 2 already fetches for
    `_market_context()`, never a second fetch) -- an approximately-
    same-session comparison, NOT a genuine multi-day relative-strength
    series (that would require fetching NIFTY's own historical candles,
    which this pipeline does not currently do for any symbol -- a real,
    explicit limitation, not approximated further). Reported as CONTEXT
    only -- never a second directional vote on top of `dc.verdict` or
    `_market_context()`'s own read of the same real global context."""
    v = candidate.response.visual
    if v is None or v.global_context is None:
        return "RELATIVE_STRENGTH_DATA_UNAVAILABLE", "no real global market context was computed for this run"
    nifty_change = next((i.day_change_pct for i in v.global_context.inputs if i.label == "NIFTY 50"), None)
    windows = _multi_day_windows(candidate.response)
    if nifty_change is None or windows is None:
        return "RELATIVE_STRENGTH_DATA_UNAVAILABLE", "real NIFTY day-change or real M15 history unavailable this run"
    recent, _ = windows
    if recent[0].close == 0:
        return "RELATIVE_STRENGTH_DATA_UNAVAILABLE", "real recent-window close was zero"
    candidate_change = (recent[-1].close - recent[0].close) / recent[0].close * Decimal(100)
    if abs(nifty_change) < RELATIVE_STRENGTH_MEANINGFUL_MOVE_PCT:
        return (
            "RELATIVE_STRENGTH_INDEX_FLAT",
            f"NIFTY real day-change ({nifty_change:+.2f}%) is not meaningful -- the candidate's own {candidate_change:+.2f}% move over its recent window stands alone, this session only",
        )
    same_sign = (candidate_change > 0) == (nifty_change > 0)
    if same_sign:
        return (
            "RELATIVE_STRENGTH_ALIGNED",
            f"candidate's real recent-window move ({candidate_change:+.2f}%) moves with NIFTY's real day-change ({nifty_change:+.2f}%), this session only",
        )
    return (
        "RELATIVE_STRENGTH_DIVERGING",
        f"candidate's real recent-window move ({candidate_change:+.2f}%) moves against NIFTY's real day-change ({nifty_change:+.2f}%), this session only",
    )


def _pre_breakout_signal(
    candidate: _GatedCandidate, *, early_stage_state: str, structural_context: str,
) -> tuple[bool, str | None]:
    """A real, explicit COMBINATION of three already-computed independent
    signals -- never a new primary state (keeps the existing, extensively
    tested `early_stage_state` 8-value ordinal, its maturity cap, ranking,
    coverage math, and outcome-tracking persistence all completely
    unchanged): the thesis is still early (`EARLY_DIRECTIONAL_BUILD`/
    `DEVELOPING_MOMENTUM`, not yet broken out), the stock has genuinely
    compressed (`RANGE_COMPRESSION`), and price sits close to the real
    nearest opposing S/R level (`_headroom_pct()`, already computed,
    contextual, never a directional vote). This is the real, auditable
    "quiet base -> directional build -> pressure near a level" signature
    this sprint's objective describes -- reported only, never fed back
    into ranking."""
    if early_stage_state not in ("EARLY_DIRECTIONAL_BUILD", "DEVELOPING_MOMENTUM"):
        return False, None
    if structural_context != "RANGE_COMPRESSION":
        return False, None
    headroom = _headroom_pct(candidate)
    if headroom is None or headroom > PRE_BREAKOUT_MAX_HEADROOM_PCT:
        return False, None
    return True, f"real range compression + {early_stage_state.replace('_', ' ').lower()} + only {headroom:.2f}% of real headroom to the nearest opposing level"


_EARLY_STAGE_LABELS = {
    "RANGE_BOUND": "RANGE BOUND",
    "EARLY_DIRECTIONAL_BUILD": "EARLY DIRECTIONAL BUILD",
    "DEVELOPING_MOMENTUM": "DEVELOPING MOMENTUM",
    "BREAKOUT_CONFIRMATION": "BREAKOUT CONFIRMATION",
    "FALSE_BREAKOUT_RISK": "FALSE BREAKOUT RISK",
    "EXTENDED": "ALREADY EXTENDED",
    "EXHAUSTION_RISK": "EXHAUSTION RISK",
    "INSUFFICIENT_DATA": "INSUFFICIENT DATA",
}


def _early_stage_narrative(
    state: str, direction: str, extension_pct: Decimal | None, trend_dir: str | None, vwap_dir: str | None,
) -> tuple[str, str, str]:
    """(what_is_developing, what_has_not_happened_yet, why_not_extended) --
    each one real, factual, deliberately non-predictive sentence derived
    only from the state/values `classify_early_stage_state()` already
    computed. Never "will rise"/"guaranteed" language -- see the module
    docstring's forbidden-language list."""
    dir_word = direction.lower()
    ext = f"{extension_pct:.2f}%" if extension_pct is not None else "an unspecified amount"

    if state == "INSUFFICIENT_DATA":
        return (
            "not enough recent M15 history to assess the structure this run.",
            "a reliable recent swing high/low could not be established.",
            "extension could not be evaluated -- this is a data gap, not a claim either way.",
        )
    if state == "FALSE_BREAKOUT_RISK":
        return (
            "the recent candle series broke past its prior swing extreme, then fell back through it.",
            "a real, held reclaim of that level has not yet happened.",
            f"it is NOT considered early-stage: the {dir_word} breakout attempt already failed to hold in the real M15 series.",
        )
    if state == "EXHAUSTION_RISK":
        return (
            f"the {dir_word} move already broke past its recent M15 structure by {ext} -- a severe real margin.",
            "nothing further is being claimed to develop -- the move has already happened, and by a wide margin.",
            f"it is NOT considered early-stage: price is {ext} beyond the recent swing extreme, well past the moderate-breakout range.",
        )
    if state == "EXTENDED":
        return (
            f"the {dir_word} move already broke past its recent M15 structure by {ext}.",
            "nothing further is being claimed to develop -- the move has already happened.",
            f"it is NOT considered early-stage: price is {ext} beyond the recent swing extreme in the {dir_word} direction.",
        )
    if state == "BREAKOUT_CONFIRMATION":
        return (
            f"price has broken past its recent M15 structure by {ext} and the latest real candle is still holding beyond it.",
            "continued holding above the broken level -- a real reclaim/failure has not yet been decided either way.",
            f"the breakout is recent and moderate ({ext}) -- not yet in the severe-extension range.",
        )
    if state == "DEVELOPING_MOMENTUM":
        return (
            f"M15 trend and VWAP position both already read {dir_word} (a real TRENDING regime), and price has not broken past recent structure.",
            "nothing beyond continued confirmation -- trend and VWAP already align.",
            "price remains within the recent swing range in this direction -- structure has not yet been broken.",
        )
    if state == "EARLY_DIRECTIONAL_BUILD":
        confirmed_by = "M15 trend" if trend_dir == direction else ("VWAP" if vwap_dir == direction else "partial evidence")
        return (
            f"{confirmed_by} already reads {dir_word}, while the rest of the picture is still forming (this is the 'quiet before move' state -- reachable at any day-change magnitude).",
            "full M15 trend + VWAP confirmation has not yet happened.",
            "price has not broken past the recent swing structure in this direction.",
        )
    return (  # RANGE_BOUND
        "the preferred direction is supported by the evidence matrix, but the real M15 regime, trend, and VWAP do not yet confirm it.",
        "M15 trend and VWAP confirmation have not yet happened.",
        "price has not broken past the recent swing structure in this direction.",
    )


_EVENT_RISK_RECENT_WINDOW = timedelta(hours=48)


def classify_event_risk(response: AnalyzeResponse) -> tuple[str, str]:
    """NO_KNOWN_EVENT / NEWS_DATA_UNAVAILABLE / FRESH_MATERIAL_EVENT /
    MAJOR_EVENT_RISK / STALE_EVENT -- never treat a fetch failure as
    'no news'."""
    v = response.visual
    if v is None or v.news is None:
        return "NEWS_DATA_UNAVAILABLE", "news payload missing for this run"
    if v.news.fetch_error is not None:
        return "NEWS_DATA_UNAVAILABLE", f"news fetch failed: {v.news.fetch_error}"
    items = v.news.items
    if not items:
        return "NO_KNOWN_EVENT", "no recent company news found (absence of news is not proof no event exists)"
    generated_at = response.generated_at
    if generated_at is None:
        return "FRESH_MATERIAL_EVENT", f"{len(items)} real news item(s) found (recency could not be verified)"
    recent = [i for i in items if generated_at - i.published_at <= _EVENT_RISK_RECENT_WINDOW]
    if len(recent) >= 2:
        return "MAJOR_EVENT_RISK", f"{len(recent)} real news item(s) within the last 48h"
    if len(recent) == 1:
        return "FRESH_MATERIAL_EVENT", "1 real news item within the last 48h"
    return "STALE_EVENT", f"{len(items)} real news item(s) found, none within the last 48h"


def _sector_note(visual: object | None) -> str:
    """Use official sector_map output when present. Never infer from the name."""
    if visual is None:
        return _SECTOR_UNAVAILABLE_NOTE
    sector = getattr(visual, "sector", None)
    if sector is None:
        return _SECTOR_UNAVAILABLE_NOTE
    classification = getattr(sector, "classification", None)
    industry = getattr(sector, "industry", None)
    source = getattr(sector, "source", None) or "NSE Nifty 500 constituent list"
    detail = getattr(sector, "detail", None)
    rs_tier = getattr(sector, "relative_strength_tier", None)
    if classification in (None, "UNAVAILABLE"):
        return (
            f"Sector classification unavailable -- SECTOR_DATA_UNAVAILABLE: "
            f"{detail or 'sector map was not supplied for this analysis.'}"
        )
    if classification == "UNKNOWN" or industry is None:
        return f"Sector classification unavailable -- SECTOR_UNKNOWN: {source} has no official industry entry for this symbol."
    rs_bit = f" -- stock/sector/market RS {rs_tier}" if rs_tier and rs_tier != "UNKNOWN" else ""
    return f"SECTOR {industry} ({source}){rs_bit}"


_SECTOR_UNAVAILABLE_NOTE = (
    "Sector classification unavailable -- SECTOR_DATA_UNAVAILABLE: sector map was not supplied for this analysis."
)


class ResearchThesisView(BaseModel):
    """One shortlisted opportunity, structured per Section 8's spec.
    Every field is copied or lightly formatted from an already-computed
    real field on the candidate's own `AnalyzeResponse` -- nothing here
    is a new calculation."""

    rank: int
    symbol: str
    current_spot: str | None
    contract_expiry: str | None
    research_confidence: str
    early_stage_state: str
    what_is_developing: str
    what_has_not_happened_yet: str
    why_not_extended: str
    nearest_important_level: str | None
    room_before_level_pct: str | None
    room_to_breakeven: str | None
    market_context: str
    market_context_detail: str
    participation_note: str
    event_risk: str
    event_risk_reason: str
    sector_note: str
    thesis: str
    confirming_evidence: list[str]
    opposing_evidence: list[str]
    contract_right: str
    contract_strike: str
    contract_ltp: str | None
    contract_quality: str
    liquidity_grade: str
    decay_verdict: str | None
    required_underlying_move_pct: str | None
    contractual_expiry_breakeven: str | None
    breakeven_distance_pct: str | None
    why_this_contract: str
    what_would_confirm: str | None
    what_invalidates: str | None
    risk: str
    actionability: str
    bullish_case: SideCaseView
    bearish_case: SideCaseView
    # Sprint 4 -- multi-day early-move context (see the "MULTI-DAY
    # EARLY-MOVE CONTEXT" section above). Reported only -- never fed into
    # ranking, coverage, or `early_stage_state`'s own ordinal.
    structural_context: str
    structural_context_detail: str
    participation_depth: str
    participation_depth_detail: str
    relative_strength: str
    relative_strength_detail: str
    pre_breakout_signal: bool
    pre_breakout_detail: str | None
    # Early-opportunity discovery -- bucket/timing/pattern, never a score.
    # Copied from `classify_research_bucket()` over already-computed fields.
    research_bucket: str
    timing_stage: str
    universe_tier: str
    developing_pattern: str
    why_investigate_now: str
    already_moved: bool
    move_context: str = "UNKNOWN"
    research_state: str | None = None
    what_is_happening: str = ""
    why_watching: str = ""
    what_is_missing: str = ""
    contract_usability: str = ""
    promoted_because: str = ""
    stage_one_observations: list[str] = Field(default_factory=list)
    options_data_quality: str = ""
    news_data_quality: str = ""
    observed_at: datetime | None = None


def _atr_pct_from_visual(visual: VisualData | None) -> Decimal | None:
    if visual is None or visual.historical_structure is None:
        return None
    return visual.historical_structure.atr_pct


def _session_range_pct_from_visual(visual: VisualData | None) -> Decimal | None:
    """Intraday high-low of the last completed session in the already-fetched
    M15 chart -- never a new fetch, never future candles (chart series is
    already bounded at as_of)."""
    if visual is None:
        return None
    candles = visual.price_chart.candles
    if not candles:
        return None
    last_day = candles[-1].timestamp.date()
    session = [c for c in candles if c.timestamp.date() == last_day]
    if not session:
        return None
    high = max(c.high for c in session)
    low = min(c.low for c in session)
    close = session[-1].close
    if close == 0:
        return None
    return (high - low) / close * Decimal("100")


def _stream_quality_line(visual: object | None, stream_name: str) -> str:
    freshness = getattr(visual, "freshness", None) if visual is not None else None
    streams = getattr(freshness, "streams", None) if freshness is not None else None
    if not streams:
        return f"{stream_name}: unavailable"
    for stream in streams:
        if getattr(stream, "stream", None) == stream_name:
            available = "Available" if getattr(stream, "usable_for_display", False) else "Unavailable"
            label = getattr(stream, "label", None) or "UNKNOWN"
            withheld = "" if getattr(stream, "usable_for_vote", True) else "; confirmation withheld"
            return f"{available}; {label}{withheld}"
    return f"{stream_name}: unavailable"


def _developing_pattern_for_thesis(*, visual: object | None, pre_breakout_signal: bool) -> str:
    """Named pattern for the research card. Pipeline `classify_development()`
    does not yet receive the daily-scan pre-breakout flags, so a True
    `_pre_breakout_signal()` is surfaced here as PRE_BREAKOUT_COMPRESSION
    when the visual narrative is still NONE. A non-NONE pipeline pattern
    always wins -- never invent a second pattern on top of a real one."""
    visual_pattern: str | None = None
    development = getattr(visual, "development", None) if visual is not None else None
    if development is not None:
        visual_pattern = getattr(development, "pattern", None)
    if visual_pattern not in (None, "NONE", ""):
        return str(visual_pattern)
    if pre_breakout_signal:
        return "PRE_BREAKOUT_COMPRESSION"
    return visual_pattern or "NONE"


def build_research_thesis(candidate: RankedCandidate) -> ResearchThesisView:
    c = candidate.contract
    case = candidate.dc.ce_case if candidate.direction == "BULLISH" else candidate.dc.pe_case
    thesis = (
        f"{candidate.symbol}: evidence currently favors {candidate.direction.lower()} "
        f"({candidate.dc.verdict}, {candidate.rationale.supporting_groups} independent supporting group(s)) "
        f"and the {c.right} {c.strike} contract is structurally {c.structural_quality.lower()}."
    )
    spot = candidate.dc.paths.spot if candidate.dc.paths else None
    breakeven_pct = _underlying_move_to_breakeven_pct(c, spot)
    risk = case.could_fail[0] if case.could_fail else "no specific opposing evidence recorded for this run"
    v = candidate.response.visual
    contract_expiry = None
    if v is not None and v.term_structure is not None and v.term_structure.expiries:
        contract_expiry = v.term_structure.expiries[0].expiry.isoformat()
    bullish_case = _build_side_case(candidate.response, candidate.dc, "BULLISH", preferred=candidate.direction == "BULLISH")
    bearish_case = _build_side_case(candidate.response, candidate.dc, "BEARISH", preferred=candidate.direction == "BEARISH")

    gated = _GatedCandidate(
        symbol=candidate.symbol, response=candidate.response, dc=candidate.dc, contract=c, direction=candidate.direction,
        stage_one_buy_sell_ratio=candidate.stage_one_buy_sell_ratio,
        stage_one_observations=candidate.stage_one_observations,
    )
    early_stage_state, extension_pct = classify_early_stage_state(gated)
    trend_dir = _evidence_direction(candidate.response, "M15 trend")
    vwap_dir = _evidence_direction(candidate.response, "VWAP")
    what_is_developing, what_has_not_happened_yet, why_not_extended = _early_stage_narrative(
        early_stage_state, candidate.direction, extension_pct, trend_dir, vwap_dir,
    )
    nearest_level = _nearest_opposing_level(gated)
    event_risk, event_risk_reason = classify_event_risk(candidate.response)
    market_context, market_context_detail = _market_context(candidate.response, candidate.direction)
    structural_context, structural_context_detail = classify_structural_context(gated)
    participation_depth, participation_depth_detail = classify_participation_depth(gated)
    relative_strength, relative_strength_detail = classify_relative_strength(gated)
    pre_breakout_signal, pre_breakout_detail = _pre_breakout_signal(
        gated, early_stage_state=early_stage_state, structural_context=structural_context,
    )
    developing_pattern = _developing_pattern_for_thesis(
        visual=v, pre_breakout_signal=pre_breakout_signal,
    )
    assessment = classify_research_bucket(
        research_state=candidate.response.research_state,
        early_stage_state=early_stage_state,
        development_pattern=developing_pattern,
        pre_breakout_signal=pre_breakout_signal,
        event_risk=event_risk,
        liquidity_grade=c.liquidity_grade,
        fo_eligible=True,
        day_change_pct=(v.extension_distance.day_change_pct if v is not None and v.extension_distance is not None else None),
        structural_context=structural_context,
        session_range_pct=_session_range_pct_from_visual(v),
        atr_pct=_atr_pct_from_visual(v),
    )
    development = getattr(v, "development", None) if v is not None else None
    happening = happening_plain_english(
        pattern=developing_pattern, bucket=assessment.bucket.value, event_risk=event_risk,
    )
    if development is not None and assessment.bucket.value not in ("EVENT_DRIVEN", "DATA_INSUFFICIENT", "CONFLICT"):
        happening = development.what_is_developing or happening
    missing = what_has_not_happened_yet
    if development is not None and development.what_is_missing:
        missing = development.what_is_missing
    confirm_if = _relevant_watch_condition(candidate.response, candidate.direction)
    if development is not None and development.confirm_if:
        confirm_if = development.confirm_if
    invalidate = candidate.response.invalidation_condition
    if development is not None and development.invalidate_if:
        invalidate = development.invalidate_if
    observations = list(candidate.stage_one_observations)

    return ResearchThesisView(
        rank=candidate.rank, symbol=candidate.symbol, current_spot=str(spot) if spot is not None else None,
        contract_expiry=contract_expiry, research_confidence=classify_research_confidence(gated),
        early_stage_state=early_stage_state, what_is_developing=what_is_developing,
        what_has_not_happened_yet=what_has_not_happened_yet, why_not_extended=why_not_extended,
        nearest_important_level=(f"{nearest_level.kind} {nearest_level.strike}" if nearest_level is not None else None),
        room_before_level_pct=(str(nearest_level.distance_pct) if nearest_level is not None and nearest_level.distance_pct is not None else None),
        room_to_breakeven=_room_to_breakeven_text(gated, c),
        market_context=market_context, market_context_detail=market_context_detail,
        participation_note=_participation_note(gated),
        event_risk=event_risk, event_risk_reason=event_risk_reason, sector_note=_sector_note(v),
        thesis=thesis, confirming_evidence=list(case.could_work), opposing_evidence=list(case.could_fail),
        contract_right=c.right, contract_strike=str(c.strike), contract_ltp=str(c.ltp) if c.ltp is not None else None,
        contract_quality=c.structural_quality, liquidity_grade=c.liquidity_grade, decay_verdict=c.decay_verdict,
        required_underlying_move_pct=str(c.required_underlying_move_pct) if c.required_underlying_move_pct is not None else None,
        contractual_expiry_breakeven=str(c.contractual_expiry_breakeven) if c.contractual_expiry_breakeven is not None else None,
        breakeven_distance_pct=str(breakeven_pct) if breakeven_pct is not None else None,
        why_this_contract=_why_this_contract(candidate.dc, candidate.direction, c),
        what_would_confirm=confirm_if,
        what_invalidates=invalidate,
        risk=risk, actionability=candidate.response.decision or "DATA_INSUFFICIENT",
        bullish_case=bullish_case, bearish_case=bearish_case,
        structural_context=structural_context, structural_context_detail=structural_context_detail,
        participation_depth=participation_depth, participation_depth_detail=participation_depth_detail,
        relative_strength=relative_strength, relative_strength_detail=relative_strength_detail,
        pre_breakout_signal=pre_breakout_signal, pre_breakout_detail=pre_breakout_detail,
        research_bucket=assessment.bucket.value, timing_stage=assessment.timing.value,
        universe_tier=assessment.universe_tier.value, developing_pattern=developing_pattern,
        why_investigate_now=assessment.why_investigate_now, already_moved=assessment.already_moved,
        move_context=assessment.move_context.value,
        research_state=candidate.response.research_state,
        what_is_happening=happening,
        why_watching=assessment.why_investigate_now,
        what_is_missing=missing,
        contract_usability=contract_usability_plain_english(c.liquidity_grade),
        promoted_because=promotion_reason(observations),
        stage_one_observations=observations,
        options_data_quality=_stream_quality_line(v, "option_chain"),
        news_data_quality=_stream_quality_line(v, "news"),
        observed_at=candidate.response.generated_at,
    )


def build_research_observation(
    candidate: RankedCandidate, thesis: ResearchThesisView, *, run_id: str, coverage_classification: str | None,
) -> ResearchObservation:
    """Sprint 3 -- one shortlisted candidate, at the exact moment
    `run_daily_research()` identified it, for OUTCOME TRACKING
    (`app.orchestration.research_outcome`). Every field copied verbatim
    from the SAME `ResearchThesisView` the API/UI already renders for
    this candidate -- never a second calculation.
    `nearest_level_kind`/`nearest_level_value` are read separately (the
    same real, already-computed S/R level `build_research_thesis()`
    itself already formats into `nearest_important_level`) only because a
    later checkpoint needs the raw numeric value, not display text."""
    c = candidate.contract
    gated = _GatedCandidate(
        symbol=candidate.symbol, response=candidate.response, dc=candidate.dc, contract=c, direction=candidate.direction,
        stage_one_buy_sell_ratio=candidate.stage_one_buy_sell_ratio,
    )
    nearest_level = _nearest_opposing_level(gated)
    generated_at = candidate.response.generated_at
    if generated_at is None:
        raise ValueError(f"{candidate.symbol}: cannot build a research observation without a real generated_at")
    return ResearchObservation(
        run_id=run_id, audit_id=candidate.response.audit_id, generated_at=generated_at,
        symbol=candidate.symbol, direction=candidate.direction, selected_right=c.right, selected_strike=str(c.strike),
        early_stage_state=thesis.early_stage_state, research_confidence=thesis.research_confidence,
        actionability=thesis.actionability, spot_at_observation=thesis.current_spot,
        contractual_expiry_breakeven=thesis.contractual_expiry_breakeven,
        nearest_level_kind=nearest_level.kind if nearest_level is not None else None,
        nearest_level_value=str(nearest_level.strike) if nearest_level is not None else None,
        market_context=thesis.market_context, participation_note=thesis.participation_note,
        coverage_classification=coverage_classification, thesis=thesis.thesis,
        structural_context=thesis.structural_context, participation_depth=thesis.participation_depth,
        relative_strength=thesis.relative_strength, pre_breakout_signal=thesis.pre_breakout_signal,
    )


def _coverage_result_note(*, no_high_conviction: bool, coverage: ResearchCoverage | None) -> str:
    """Sprint 1, Phase 5 -- the real, honest sentence distinguishing "the
    full eligible universe was screened and nothing qualified" from
    "nothing qualified among the reliably evaluated universe" from
    "coverage was too poor to draw a market-wide conclusion at all". Never
    lowers gates, never hides a real shortlist -- states plainly how much
    of the real market this run's result actually covers. `coverage is
    None` only for a hand-built `DailyResearchResult` that never called
    `compute_research_coverage()` -- an honest "not computed" fallback,
    never a fabricated classification."""
    if coverage is None:
        return "Research coverage was not computed for this run."
    # Sprint 5, Objective 8 -- every tier now leads with the real,
    # specific "X/Y symbols reliably evaluated" count (previously only
    # the POOR branch embedded a count) so the reader never has to infer
    # how much of the market was actually covered from the classification
    # word alone. `reliably_evaluated` reuses the exact arithmetic
    # `compute_research_coverage()` itself already used for its ratio --
    # never a second judgment, just restated from already-stored fields
    # (`stage1_failed`/`stage2_failed` sets are disjoint by construction,
    # see that function's own docstring).
    reliably_evaluated = coverage.universe_count - (coverage.stage1_failed or 0) - coverage.stage2_failed
    prefix = f"{coverage.classification} -- {reliably_evaluated}/{coverage.universe_count} symbols reliably evaluated."
    if coverage.classification == "POOR":
        failed = (coverage.stage1_failed or 0) + coverage.stage2_failed
        return (
            f"{prefix} RESEARCH INCOMPLETE -- insufficient market coverage to make a strong market-wide conclusion "
            f"({failed} of {coverage.universe_count} real symbols could not be reliably evaluated this run)."
        )
    if coverage.classification == "DEGRADED":
        if no_high_conviction:
            return (
                f"{prefix} NO HIGH CONVICTION -- no candidate cleared the gates among the reliably evaluated universe. "
                "Research coverage was degraded this run."
            )
        return f"{prefix} Research coverage was degraded this run -- this result applies to the reliably evaluated universe, not the full real universe."
    # HIGH or GOOD
    if no_high_conviction:
        return f"{prefix} NO HIGH CONVICTION -- the full eligible universe was reliably screened and no candidate cleared the gates."
    return f"{prefix} The full eligible universe was reliably screened this run."


class DailyResearchView(BaseModel):
    """The full API/UI response shape for `GET /api/research/daily`."""

    generated_at: datetime
    market_state: str | None
    universe: list[str]
    screened_count: int
    stage_one_survivor_count: int | None
    deep_analyzed_count: int
    shortlisted_count: int
    no_high_conviction: bool
    opportunities: list[ResearchThesisView]
    rejected: list[RejectionRecord]
    rejection_summary: dict[str, int]
    rejected_as_extended_count: int
    rejected_insufficient_evidence_count: int
    # Sprint 1, Phase 4/5/6 -- metadata about how reliably THIS run
    # evaluated the real universe; never a stock-quality signal, never
    # read by ranking (see `ResearchCoverage`'s own docstring).
    coverage: ResearchCoverage | None
    coverage_note: str
    # 95%+ Reliability & Performance Gate -- lets the UI say "scan
    # completed Xs ago; N/M symbols successfully analyzed" instead of
    # implying the entire scan happened at one single instant.
    scan_snapshot: ResearchScanSnapshot | None = None
    # Early-opportunity buckets over every Stage-2 gated symbol, not the
    # ranked top-N. Empty lists are a correct result (including
    # ZERO_VALID_EARLY_OPPORTUNITIES when developing_now is empty).
    developing_now: list[ResearchThesisView] = Field(default_factory=list)
    already_moved: list[ResearchThesisView] = Field(default_factory=list)
    confirmed: list[ResearchThesisView] = Field(default_factory=list)
    extended: list[ResearchThesisView] = Field(default_factory=list)
    conflict: list[ResearchThesisView] = Field(default_factory=list)
    data_insufficient: list[ResearchThesisView] = Field(default_factory=list)
    already_moved_rejections: list[str] = Field(default_factory=list)
    zero_valid_early_opportunities: bool = True
    events_to_monitor: list[ResearchThesisView] = Field(default_factory=list)
    confirmation_pending: list[ResearchThesisView] = Field(default_factory=list)
    data_issues: list[ResearchThesisView] = Field(default_factory=list)
    scan_mode: str = "DEFAULT_WHOLE_FO_SCAN"
    universe_source: str = UNIVERSE_SOURCE_UPSTOX_NSE_FO_EQUITY
    fno_ban_status: str = FNO_BAN_STATUS_UNKNOWN
    stage1_truncated_count: int = 0
    stage1_cap_applied: bool = False
    index_relative_strength_available: bool = False


_DEVELOPING_BUCKET_ORDER = {
    ResearchBucket.HIGH_QUALITY_DEVELOPING.value: 0,
    ResearchBucket.DEVELOPING.value: 1,
}


def _sort_developing_now(theses: list[ResearchThesisView]) -> list[ResearchThesisView]:
    """Named-bucket then symbol -- explainable, never a numeric score."""
    return sorted(
        theses,
        key=lambda t: (_DEVELOPING_BUCKET_ORDER.get(t.research_bucket, 9), t.symbol),
    )


def build_daily_research_view(result: DailyResearchResult) -> DailyResearchView:
    opportunities = [build_research_thesis(c) for c in result.shortlist]
    gated_theses = [build_research_thesis(c) for c in result.stage_two_gated]
    developing_now = _sort_developing_now(
        [t for t in gated_theses if is_early_opportunity_bucket(ResearchBucket(t.research_bucket))],
    )
    events_to_monitor = sorted(
        [t for t in gated_theses if is_event_to_monitor_bucket(ResearchBucket(t.research_bucket))],
        key=lambda t: t.symbol,
    )
    already_moved = [t for t in gated_theses if t.research_bucket in ("ALREADY_MOVED",)]
    confirmed = [t for t in gated_theses if t.research_bucket == "CONFIRMED"]
    extended = [t for t in gated_theses if t.research_bucket == "EXTENDED"]
    conflict = [t for t in gated_theses if t.research_bucket == "CONFLICT"]
    data_insufficient = [t for t in gated_theses if t.research_bucket == "DATA_INSUFFICIENT"]
    confirmation_pending = sorted(
        [
            t for t in gated_theses
            if t.research_state == "CONFIRMATION_PENDING" or t.timing_stage == "CONFIRMING"
        ],
        key=lambda t: t.symbol,
    )
    data_issues = sorted(data_insufficient + conflict, key=lambda t: t.symbol)
    already_moved_rejections = sorted({
        r.symbol for r in result.rejected if categorize_rejection(r.reason) == "ALREADY_EXTENDED"
    })
    return DailyResearchView(
        generated_at=result.generated_at, market_state=result.market_state, universe=result.universe,
        screened_count=result.screened_count, stage_one_survivor_count=result.stage_one_survivor_count,
        deep_analyzed_count=result.deep_analyzed_count, shortlisted_count=len(opportunities),
        no_high_conviction=result.no_high_conviction, opportunities=opportunities, rejected=result.rejected,
        rejection_summary=result.rejection_summary, rejected_as_extended_count=result.rejected_as_extended_count,
        scan_snapshot=result.scan_snapshot,
        rejected_insufficient_evidence_count=result.rejected_insufficient_evidence_count,
        coverage=result.coverage,
        coverage_note=_coverage_result_note(no_high_conviction=result.no_high_conviction, coverage=result.coverage),
        developing_now=developing_now, already_moved=already_moved, confirmed=confirmed,
        extended=extended, conflict=conflict, data_insufficient=data_insufficient,
        already_moved_rejections=already_moved_rejections,
        zero_valid_early_opportunities=len(developing_now) == 0,
        events_to_monitor=events_to_monitor,
        confirmation_pending=confirmation_pending,
        data_issues=data_issues,
        scan_mode=(
            "EXPLICIT_SYMBOL_QUERY"
            if result.coverage is not None and result.coverage.stage1_attempted is None
            else "DEFAULT_WHOLE_FO_SCAN"
        ),
        universe_source=result.universe_source,
        fno_ban_status=result.fno_ban_status,
        stage1_truncated_count=result.stage1_truncated_count,
        stage1_cap_applied=result.stage1_cap_applied,
        index_relative_strength_available=result.index_relative_strength_available,
    )
