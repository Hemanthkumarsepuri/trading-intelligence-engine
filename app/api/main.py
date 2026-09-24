"""Presentation layer — the localhost dashboard (Sprint 1 of the master
personal-terminal directive). This module ONLY does HTTP wiring: parsing
requests, calling `app.orchestration.dashboard_service.run_analysis()`
(which itself only calls the already-existing, already-validated
`analyze_symbol()` pipeline and renderers), and returning the result.
No domain computation happens here.

Run with: `uvicorn app.api.main:app --host 127.0.0.1 --port 8000`
(or `python -m app.api.main`, which does the same thing).

Safety: this module contains no code path capable of placing, modifying,
or cancelling a broker order — it only ever calls `analyze_symbol()`,
which is itself read-only. See SAFETY_LOCK.txt.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.api.schemas import (
    AnalyzeRequest,
    CreateResearchWatchRequest,
    ExplainRequest,
    FollowupRequest,
    IPOAnalyzeRequest,
    IPOCompareRequest,
    MigrateResearchWatchesRequest,
    PersonalJournalEntryRequest,
    PersonalJournalOutcomeRequest,
    UpdateWatchLatestRequest,
    WatchlistRequest,
)
from app.config.settings import settings
from app.data.providers.nse_sector_index import NIFTY50_CONSTITUENT_URL, fetch_sector_index
from app.data.providers.upstox_instrument_master import fetch_instrument_master
from app.data.providers.upstox_provider import UpstoxProvider
from app.domain.ipo.comparison import IPOComparisonInput, build_comparison_entry
from app.domain.ipo.hidden_opportunity import HiddenOpportunityAssessment, assess_hidden_opportunity
from app.domain.ipo.models import (
    CategorySubscription,
    GMPObservation,
    IPOIdentity,
    IssueType,
    SubscriptionCategory,
    SubscriptionSnapshot,
)
from app.domain.ipo.query_parser import is_ipo_query
from app.domain.ipo.shareholder_quota import build_shareholder_quota_info
from app.domain.journal.personal_journal import (
    MistakeClass,
    PersonalJournalEntry,
    PersonalJournalOutcome,
)
from app.domain.market.models import OptionRight
from app.domain.market.trading_calendar import (
    apply_nse_calendar_file,
    classify_session,
    classify_session_window,
    session_window_payload,
)
from app.domain.research.watch_record import same_instrument_scope
from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy
from app.llm.qwen_narrative import QwenNarrativeAdapter
from app.orchestration.daily_research import (
    DailyResearchView,
    build_daily_research_view,
    run_daily_research,
)
from app.orchestration.dashboard_service import (
    AnalyzeResponse,
    WatchlistEntry,
    run_analysis,
    run_watchlist,
)
from app.orchestration.forward_capture import ForwardCaptureView, build_forward_capture_view
from app.orchestration.ipo_audit_journal import build_ipo_analysis_snapshot
from app.orchestration.ipo_intelligence import (
    IPOAnalysisConfig,
    IPOAnalysisResult,
    analyze_ipo_query_live,
    discover_ipos,
)
from app.orchestration.journal_views import (
    JournalStatusView,
    OutcomeTrackingView,
    build_journal_status,
    build_outcome_tracking,
)
from app.orchestration.observed_market import build_observed_market
from app.orchestration.options_intelligence_pipeline import PipelineConfig, Repositories
from app.orchestration.pattern_views import (
    PatternAggregationView,
    ReplayDatasetRowsView,
    ReplayDatasetUnavailable,
    build_live_pattern_aggregation,
    build_replay_pattern_aggregation,
    read_replay_dataset_rows,
)
from app.orchestration.query_context import (
    ContextState,
    FollowupKind,
    IPOQueryContext,
    OptionsQueryContext,
    resolve_ipo_followup,
    resolve_options_followup,
)
from app.orchestration.research_jobs import ResearchJob, ResearchJobRegistry
from app.orchestration.research_outcome import (
    ResearchHistoryView,
    ResearchOutcomeDetailView,
    build_research_history,
    build_research_outcome_detail,
)
from app.orchestration.research_watch import (
    DuplicateWatchError,
    ResearchWatchService,
    WatchUnavailableError,
    observation_from_client,
)
from app.orchestration.system_status import as_health_payload, build_system_status
from app.persistence.jsonl_file import (
    JsonlAuditJournalRepository,
    JsonlIPOAuditJournalRepository,
    JsonlIvObservationRepository,
    JsonlOptionChainRepository,
    JsonlPersonalJournalRepository,
    JsonlQuoteRepository,
    JsonlResearchOutcomeRepository,
    JsonlResearchRunRepository,
    JsonlWatchRecordRepository,
)
from app.utils.time import to_ist, utc_now

_APP_DIR = Path(__file__).resolve().parent
_STATIC_DIR = _APP_DIR / "static"
_DATA_ROOT = Path(settings.tire_data_root)
_PACKAGED_HOLIDAY_FILE = _APP_DIR.parent / "data" / "nse_trading_holidays.txt"
_MASTER_CACHE_PATH = _DATA_ROOT / "reference" / "upstox_nse_instruments.json"
_MCX_MASTER_CACHE_PATH = _DATA_ROOT / "reference" / "upstox_mcx_instruments.json"
_MCX_MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/MCX.json.gz"
_SECTOR_INDEX_CACHE_PATH = _DATA_ROOT / "reference" / "nse_sector_index.csv"
_NIFTY50_CACHE_PATH = _DATA_ROOT / "reference" / "nse_nifty50_constituents.csv"
_DELIVERY_CACHE_DIR = _DATA_ROOT / "reference" / "nse_delivery"
_NSE_HOLIDAY_FILE = _PACKAGED_HOLIDAY_FILE if _PACKAGED_HOLIDAY_FILE.is_file() else (_DATA_ROOT / "reference" / "nse_trading_holidays.txt")
_PERSISTENCE_DIR = _DATA_ROOT / "persistence"
_JOURNAL_DIR = _DATA_ROOT / "persistence" / "audit_journal"
_IPO_JOURNAL_DIR = _DATA_ROOT / "persistence" / "ipo_audit_journal"
_RESEARCH_JOURNAL_DIR = _DATA_ROOT / "persistence" / "research_journal"
_RESEARCH_OUTCOME_DIR = _DATA_ROOT / "persistence" / "research_outcomes"
_PERSONAL_JOURNAL_DIR = _DATA_ROOT / "persistence" / "personal_journal"
_WATCH_DIR = _DATA_ROOT / "persistence" / "research_watches"
_REPLAY_DATASET_DIR = _DATA_ROOT / "research_dataset"
_REPLAY_OUTCOME_DIR = _DATA_ROOT / "persistence" / "replay_research_outcomes"


@asynccontextmanager
async def real_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Wires REAL Upstox access and REAL JSONL persistence into
    `app.state` exactly once, at process startup — never per-request (the
    instrument master is a large, slow-changing reference file; refetching
    it on every browser request would be needlessly slow and wasteful).
    Overridden by a no-op lifespan in tests (see
    `tests/integration/api/test_dashboard_api.py`) so tests never touch
    the real network.
    """
    settings.assert_broker_execution_disabled()
    apply_nse_calendar_file(_NSE_HOLIDAY_FILE)
    client = httpx.AsyncClient()
    app.state.http_client = client
    app.state.qwen = QwenNarrativeAdapter(
        base_url=settings.qwen_base_url,
        model=settings.qwen_model,
        timeout_seconds=settings.qwen_timeout_seconds,
        health_timeout_seconds=settings.qwen_health_timeout_seconds,
        enabled=settings.qwen_enabled,
    )
    app.state.strategy = EMAVWAPAlignmentStrategy()
    app.state.config = PipelineConfig()
    app.state.repositories = Repositories(
        quotes=JsonlQuoteRepository(_PERSISTENCE_DIR / "quotes.jsonl"),
        option_chains=JsonlOptionChainRepository(_PERSISTENCE_DIR / "option_chains.jsonl"),
        iv_observations=JsonlIvObservationRepository(_PERSISTENCE_DIR / "iv_observations.jsonl"),
    )
    # Sprint 5, Phase 1 -- the audit journal, previously built (Milestone
    # G) but never wired into the running dashboard (see Sprint 5's Phase
    # 0 audit). Every successful `/api/analyze` call now also persists an
    # immutable `AnalysisSnapshot` here.
    app.state.journal = JsonlAuditJournalRepository(_JOURNAL_DIR)
    # This milestone, Phase 8 -- the IPO audit journal, a separate
    # append-only store from the options side's `journal` above (never a
    # second incompatible system; same discipline, own files).
    app.state.ipo_journal = JsonlIPOAuditJournalRepository(_IPO_JOURNAL_DIR)
    # Daily Market Researcher -- its own append-only journal, separate
    # from `journal` above (a different record type, its own file; the
    # researcher's per-symbol deep analyses still go through `journal`
    # exactly like a manual query would).
    app.state.research_journal = JsonlResearchRunRepository(_RESEARCH_JOURNAL_DIR)
    # Sprint 3 -- research OUTCOME TRACKING: a separate append-only store
    # from `research_journal` above (a different record type -- what
    # actually happened afterward, never the run itself), same discipline.
    app.state.outcome_repository = JsonlResearchOutcomeRepository(_RESEARCH_OUTCOME_DIR)
    app.state.personal_journal = JsonlPersonalJournalRepository(_PERSONAL_JOURNAL_DIR)
    app.state.watch_repository = JsonlWatchRecordRepository(_WATCH_DIR)
    # Section 6 -- the deterministic follow-up mechanism's in-memory
    # context, process lifetime only (see query_context.py's module
    # docstring, rule 7: a restart intentionally clears this, and a
    # fresh analysis immediately re-establishes it).
    app.state.query_context = ContextState()
    if settings.upstox_access_token:
        app.state.provider = UpstoxProvider(client=client, access_token=settings.upstox_access_token)
        app.state.instrument_master = await fetch_instrument_master(client, cache_path=_MASTER_CACHE_PATH)
        try:
            app.state.mcx_instrument_master = await fetch_instrument_master(
                client, cache_path=_MCX_MASTER_CACHE_PATH, master_url=_MCX_MASTER_URL
            )
        except Exception:  # noqa: BLE001 -- MCX is a "nice to have" context input; its absence must never block startup
            app.state.mcx_instrument_master = None
        # Sprint 8, Objective P1 -- real, official NSE sector classification
        # (see nse_sector_index.py's own docstring). A "nice to have"
        # context input exactly like MCX above -- its absence must never
        # block startup or force a guessed sector.
        try:
            app.state.sector_map = await fetch_sector_index(client, cache_path=_SECTOR_INDEX_CACHE_PATH)
        except Exception:  # noqa: BLE001
            app.state.sector_map = None
        try:
            nifty50_map = await fetch_sector_index(
                client, cache_path=_NIFTY50_CACHE_PATH, source_url=NIFTY50_CONSTITUENT_URL,
            )
            app.state.nifty50_symbols = tuple(nifty50_map.keys())
        except Exception:  # noqa: BLE001
            app.state.nifty50_symbols = None
        app.state.delivery_cache_dir = _DELIVERY_CACHE_DIR
    else:
        app.state.provider = None
        app.state.instrument_master = None
        app.state.mcx_instrument_master = None
        app.state.sector_map = None
        app.state.nifty50_symbols = None
        app.state.delivery_cache_dir = None
    try:
        yield
    finally:
        await client.aclose()


LifespanFactory = Callable[[FastAPI], AbstractAsyncContextManager[None]]


def create_app(*, lifespan: LifespanFactory = real_lifespan) -> FastAPI:
    app = FastAPI(title="Options Intelligence Terminal", lifespan=lifespan)
    origins = [item.strip() for item in settings.cors_allow_origins.split(",") if item.strip()]
    if origins:
        if "*" in origins:
            raise RuntimeError("CORS_ALLOW_ORIGINS must not include '*' — set explicit frontend origins.")
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type", "Accept"],
        )
    app.state.research_jobs = ResearchJobRegistry()

    async def _discover_job_runner(job: ResearchJob) -> dict[str, object]:
        provider: UpstoxProvider | None = getattr(app.state, "provider", None)
        instrument_master: Sequence[dict[str, object]] | None = getattr(app.state, "instrument_master", None)
        if provider is None or instrument_master is None:
            raise RuntimeError("UPSTOX_ACCESS_TOKEN is not configured")

        async def on_progress(payload: dict[str, object]) -> None:
            job.stage = str(payload.get("stage") or job.stage)
            processed = payload.get("processed")
            if isinstance(processed, int):
                job.processed = processed
            total = payload.get("total")
            if isinstance(total, int):
                job.total = total
            message = payload.get("message")
            if isinstance(message, str) and message:
                job.message = message
            hits = payload.get("cache_hits")
            if isinstance(hits, int):
                job.cache_hits = hits
            misses = payload.get("cache_misses")
            if isinstance(misses, int):
                job.cache_misses = misses

        async def _run_on_worker_loop() -> dict[str, object]:
            requested = list(job.symbols) if job.symbols is not None else None
            async with httpx.AsyncClient() as client:
                worker_provider = UpstoxProvider(client=client, access_token=settings.upstox_access_token or "")
                result = await run_daily_research(
                    requested, provider=worker_provider, instrument_master=instrument_master,
                    mcx_instrument_master=getattr(app.state, "mcx_instrument_master", None),
                    sector_map=getattr(app.state, "sector_map", None),
                    strategy=app.state.strategy, repositories=app.state.repositories, config=app.state.config,
                    as_of=utc_now(), journal=getattr(app.state, "journal", None),
                    research_journal=getattr(app.state, "research_journal", None),
                    outcome_repository=getattr(app.state, "outcome_repository", None),
                    nifty50_symbols=getattr(app.state, "nifty50_symbols", None),
                    http_client=client,
                    delivery_cache_dir=getattr(app.state, "delivery_cache_dir", None),
                    on_progress=on_progress,
                )
            return dict(build_daily_research_view(result).model_dump(mode="json"))

        def _blocking() -> dict[str, object]:
            return asyncio.run(_run_on_worker_loop())

        loop = asyncio.get_running_loop()
        registry: ResearchJobRegistry = app.state.research_jobs
        return await loop.run_in_executor(registry.executor, _blocking)

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(_STATIC_DIR / "index.html")

    @app.get("/api/health")
    async def health() -> dict[str, object]:
        """Factual status. GREEN on a stream requires configured + reachable +
        a valid response. This endpoint does not probe Qwen or option-chain
        (those would hide a slow/failing dependency behind a health call)."""
        qwen_verified: bool | None = getattr(app.state, "qwen_verified_ok", None)
        now = utc_now()
        view = build_system_status(
            token_configured=getattr(app.state, "provider", None) is not None,
            instrument_master_loaded=getattr(app.state, "instrument_master", None) is not None,
            sector_map_loaded=bool(getattr(app.state, "sector_map", None)),
            qwen_enabled=settings.qwen_enabled,
            qwen_model=settings.qwen_model,
            qwen_verified_ok=qwen_verified,
            primary_provider=settings.market_data_provider,
            dhan_configured=bool(settings.dhan_access_token),
            fivepaisa_configured=False,
        )
        window = classify_session_window(now)
        payload = as_health_payload(
            view,
            server_time_utc=now.isoformat(),
            session_window=session_window_payload(window),
        )
        payload["data_root"] = settings.tire_data_root
        payload["qwen_enabled"] = settings.qwen_enabled
        return payload

    @app.get("/api/market/observed")
    async def market_observed() -> dict[str, object]:
        """Latest persisted index prints. Never calls a provider. Missing
        observations stay UNAVAILABLE. Closed session still returns last
        observed values when they exist."""
        repos = getattr(app.state, "repositories", None)
        return await build_observed_market(
            instrument_master=getattr(app.state, "instrument_master", None),
            quotes=getattr(repos, "quotes", None),
            as_of=utc_now(),
        )

    @app.get("/api/qwen/health")
    async def qwen_health() -> dict[str, object]:
        """Fail-open connectivity probe. Never mutates research state."""
        adapter: QwenNarrativeAdapter | None = getattr(app.state, "qwen", None)
        if adapter is None:
            adapter = QwenNarrativeAdapter(
                base_url=settings.qwen_base_url,
                model=settings.qwen_model,
                timeout_seconds=settings.qwen_timeout_seconds,
                health_timeout_seconds=settings.qwen_health_timeout_seconds,
                enabled=settings.qwen_enabled,
            )
        client: httpx.AsyncClient | None = getattr(app.state, "http_client", None)
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient()
        try:
            result = await adapter.connectivity_probe(client)
        finally:
            if owns_client:
                await client.aclose()
        app.state.qwen_verified_ok = result.ok
        explanation = result.explanation
        return {
            "ok": result.ok,
            "status": result.status,
            "detail": result.detail,
            "model": result.model,
            "endpoint": result.endpoint,
            "latency_ms": result.latency_ms,
            "summary": explanation.summary if explanation is not None else None,
            "source_evidence_ids": list(explanation.source_evidence_ids) if explanation is not None else [],
        }

    @app.post("/api/research/explain")
    async def research_explain(body: ExplainRequest) -> dict[str, object]:
        """Optional Qwen paraphrase of already-retrieved TIRE facts.

        Fail-open. Never mutates research state. Never returns BUY/SELL,
        confidence, probability, or target prices.
        """
        adapter: QwenNarrativeAdapter | None = getattr(app.state, "qwen", None)
        if adapter is None:
            adapter = QwenNarrativeAdapter(
                base_url=settings.qwen_base_url,
                model=settings.qwen_model,
                timeout_seconds=settings.qwen_timeout_seconds,
                health_timeout_seconds=settings.qwen_health_timeout_seconds,
                enabled=settings.qwen_enabled,
            )
        client: httpx.AsyncClient | None = getattr(app.state, "http_client", None)
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient()
        try:
            result = await adapter.explain(
                client,
                facts=body.facts,
                source_evidence_ids=tuple(body.source_evidence_ids),
            )
        finally:
            if owns_client:
                await client.aclose()
        explanation = result.explanation
        payload: dict[str, object] = {
            "ok": result.ok,
            "status": result.status,
            "detail": result.detail,
            "fallback": "deterministic" if not result.ok else None,
            "latency_ms": result.latency_ms,
            "model": result.model,
        }
        if explanation is not None:
            payload["explanation"] = {
                "summary": explanation.summary,
                "developing_observation": explanation.developing_observation,
                "supporting_evidence": list(explanation.supporting_evidence),
                "conflicting_evidence": list(explanation.conflicting_evidence),
                "missing_evidence": list(explanation.missing_evidence),
                "confirmation_condition": explanation.confirmation_condition,
                "invalidation_condition": explanation.invalidation_condition,
                "data_quality_note": explanation.data_quality_note,
                "source_evidence_ids": list(explanation.source_evidence_ids),
            }
        return payload

    @app.post("/api/analyze", response_model=AnalyzeResponse)
    async def analyze(request: AnalyzeRequest) -> AnalyzeResponse:
        # Part 28 -- IPO and options are separate analytical domains that
        # must never be silently coupled. This is the ONLY coupling
        # point: a single routing check, before any options-pipeline work
        # starts, so an IPO-shaped query never gets misinterpreted as an
        # unresolvable stock/option symbol.
        if is_ipo_query(request.query):
            return AnalyzeResponse(
                query=request.query, parsed_symbol=None, parsed_strike=None, parsed_right=None,
                parsed_expiry_hint=None, has_specific_contract=False, parse_warnings=[],
                error="This looks like an IPO query, not an options query -- use POST /api/ipo/analyze instead.",
                latency_seconds=0.0,
            )
        provider: UpstoxProvider | None = getattr(app.state, "provider", None)
        instrument_master: Sequence[dict[str, object]] | None = getattr(app.state, "instrument_master", None)
        if provider is None or instrument_master is None:
            raise HTTPException(
                status_code=503,
                detail="UPSTOX_ACCESS_TOKEN is not configured -- see docs/data-sources/PROVIDER_DECISION.md",
            )
        response = await run_analysis(
            request.query, provider=provider, instrument_master=instrument_master,
            mcx_instrument_master=getattr(app.state, "mcx_instrument_master", None),
            sector_map=getattr(app.state, "sector_map", None),
            strategy=app.state.strategy, repositories=app.state.repositories, config=app.state.config,
            as_of=utc_now(), journal=getattr(app.state, "journal", None),
            nifty50_symbols=getattr(app.state, "nifty50_symbols", None),
            http_client=getattr(app.state, "http_client", None),
            delivery_cache_dir=getattr(app.state, "delivery_cache_dir", None),
            fii_cash_net=request.fii_cash_net, dii_cash_net=request.dii_cash_net, index_fo_net=request.index_fo_net,
            fii_dii_as_of=request.fii_dii_as_of, fii_dii_source=request.fii_dii_source,
        )
        # Section 6 -- a successful analysis of a real symbol establishes
        # (overwrites, never merges) the options follow-up context. A
        # parse/analysis error never touches the context -- an ambiguous
        # or failed query must not silently corrupt a valid prior context.
        if response.error is None and response.symbol is not None:
            context_state: ContextState | None = getattr(app.state, "query_context", None)
            if context_state is not None:
                context_state.options = OptionsQueryContext(
                    query=request.query, symbol=response.symbol,
                    strike=Decimal(response.parsed_strike) if response.parsed_strike is not None else None,
                    right=OptionRight(response.parsed_right) if response.parsed_right is not None else None,
                    audit_id=response.audit_id, established_at=utc_now(),
                )
            watch_repo = getattr(app.state, "watch_repository", None)
            if watch_repo is not None:
                try:
                    existing = await watch_repo.active_for_symbol(response.symbol)
                    if existing is not None:
                        obs = observation_from_client(response.model_dump(mode="json"), kind="analyze")
                        if obs is not None:
                            t0 = existing.t0
                            if (
                                t0 is None
                                or existing.t0_unavailable
                                or same_instrument_scope(t0, obs)
                            ):
                                await ResearchWatchService(watch_repo).update_latest(existing.watch_id, obs)
                except Exception:
                    logging.getLogger(__name__).exception("watch latest refresh failed for %s", response.symbol)
        return response

    @app.post("/api/watchlist")
    async def watchlist(request: WatchlistRequest) -> list[WatchlistEntry]:
        """Part 4H -- multiple independent symbol analyses compared side
        by side, each retaining its own audit_id/snapshot/CE/PE identity
        (never mixed, never ranked against each other)."""
        provider: UpstoxProvider | None = getattr(app.state, "provider", None)
        instrument_master: Sequence[dict[str, object]] | None = getattr(app.state, "instrument_master", None)
        if provider is None or instrument_master is None:
            raise HTTPException(
                status_code=503,
                detail="UPSTOX_ACCESS_TOKEN is not configured -- see docs/data-sources/PROVIDER_DECISION.md",
            )
        return await run_watchlist(
            request.queries, provider=provider, instrument_master=instrument_master,
            mcx_instrument_master=getattr(app.state, "mcx_instrument_master", None),
            sector_map=getattr(app.state, "sector_map", None),
            strategy=app.state.strategy, repositories=app.state.repositories, config=app.state.config,
            as_of=utc_now(), journal=getattr(app.state, "journal", None),
            nifty50_symbols=getattr(app.state, "nifty50_symbols", None),
            http_client=getattr(app.state, "http_client", None),
            delivery_cache_dir=getattr(app.state, "delivery_cache_dir", None),
        )

    @app.post("/api/followup")
    async def followup(request: FollowupRequest) -> dict[str, object]:
        """Section 6 -- resolves a short options follow-up ('what about
        PE?', 'which expiry?', 'what changed?') against the in-memory
        context left by the most recent `/api/analyze` call. `ANSWERED_
        FROM_CONTEXT`/`NEEDS_CLARIFICATION`/`NO_CONTEXT` never re-run the
        pipeline (`analysis` is `null`); only `RESOLVED_QUERY` does, via
        the SAME `run_analysis()` every direct `/api/analyze` call uses --
        no separate analysis path exists for a follow-up."""
        context_state: ContextState | None = getattr(app.state, "query_context", None)
        resolution = resolve_options_followup(request.text, context=context_state.options if context_state is not None else None, now=utc_now())

        analysis: AnalyzeResponse | None = None
        if resolution.kind == FollowupKind.RESOLVED_QUERY and resolution.action == "reanalyze" and resolution.resolved_query is not None:
            provider: UpstoxProvider | None = getattr(app.state, "provider", None)
            instrument_master: Sequence[dict[str, object]] | None = getattr(app.state, "instrument_master", None)
            if provider is None or instrument_master is None:
                raise HTTPException(status_code=503, detail="UPSTOX_ACCESS_TOKEN is not configured -- see docs/data-sources/PROVIDER_DECISION.md")
            analysis = await run_analysis(
                resolution.resolved_query, provider=provider, instrument_master=instrument_master,
                mcx_instrument_master=getattr(app.state, "mcx_instrument_master", None),
                sector_map=getattr(app.state, "sector_map", None),
                strategy=app.state.strategy, repositories=app.state.repositories, config=app.state.config,
                as_of=utc_now(), journal=getattr(app.state, "journal", None),
                nifty50_symbols=getattr(app.state, "nifty50_symbols", None),
                http_client=getattr(app.state, "http_client", None),
                delivery_cache_dir=getattr(app.state, "delivery_cache_dir", None),
            )
            if analysis.error is None and analysis.symbol is not None and context_state is not None:
                context_state.options = OptionsQueryContext(
                    query=resolution.resolved_query, symbol=analysis.symbol,
                    strike=Decimal(analysis.parsed_strike) if analysis.parsed_strike is not None else None,
                    right=OptionRight(analysis.parsed_right) if analysis.parsed_right is not None else None,
                    audit_id=analysis.audit_id, established_at=utc_now(),
                )

        return {
            "resolution": {"kind": resolution.kind.value, "message": resolution.message, "resolved_query": resolution.resolved_query, "action": resolution.action},
            "analysis": analysis.model_dump(mode="json") if analysis is not None else None,
        }

    @app.post("/api/ipo/analyze")
    async def ipo_analyze(request: IPOAnalyzeRequest) -> IPOAnalysisResult:
        """IPO Intelligence (see docs/data-sources/IPO_DATA_SOURCE_DECISION.md).

        Real IPO identity/timeline/registrar/listing-price come from
        Upstox's authorized `/v2/ipos` when the query names a company
        that genuinely exists in that real feed (`analyze_ipo_query_live`).
        GMP/category-wise subscription/exact shareholder-quota terms
        remain data the CALLER supplies (no authorized source exists for
        those -- see the doc above). A caller-supplied `company_name`
        always wins over the live lookup.
        """
        identity: IPOIdentity | None = None
        if request.company_name is not None:
            identity = IPOIdentity(
                company_name=request.company_name,
                issue_type=IssueType(request.issue_type) if request.issue_type is not None else IssueType.UNKNOWN,
                price_band_low=request.price_band_low, price_band_high=request.price_band_high, lot_size=request.lot_size,
                issue_size_crore=request.issue_size_crore, fresh_issue_crore=request.fresh_issue_crore,
                offer_for_sale_crore=request.offer_for_sale_crore,
            )

        gmp_observations = [
            GMPObservation(source=o.source, value=o.value, observed_at=o.observed_at, retrieved_at=utc_now())
            for o in request.gmp_observations
        ]

        subscription: SubscriptionSnapshot | None = None
        if request.subscription_categories:
            subscription = SubscriptionSnapshot(
                captured_at=request.subscription_captured_at or utc_now(),
                source=request.subscription_source,
                categories=[
                    CategorySubscription(
                        category=SubscriptionCategory(c.category), shares_offered=c.shares_offered,
                        shares_bid_for=c.shares_bid_for, times_subscribed=c.times_subscribed,
                        as_of_day=c.as_of_day, is_final=c.is_final,
                    )
                    for c in request.subscription_categories
                ],
            )

        # A caller who explicitly asserted or named shareholder-quota
        # fields always wins over the live "SHA" category lookup; a
        # caller who supplied nothing lets `analyze_ipo_query_live()`
        # derive it from real data when a match exists.
        shareholder_quota = None
        if request.shareholder_quota_confirmed_by_offer_document or request.shareholder_eligibility_company or request.shareholder_parent_company:
            shareholder_quota = build_shareholder_quota_info(
                confirmed_by_offer_document=request.shareholder_quota_confirmed_by_offer_document,
                eligibility_company=request.shareholder_eligibility_company,
                parent_company=request.shareholder_parent_company,
                reserved_shares=request.shareholder_reserved_shares,
            )

        provider: UpstoxProvider | None = getattr(app.state, "provider", None)
        result = await analyze_ipo_query_live(
            request.query, as_of=utc_now(), provider=provider, identity=identity, gmp_observations=gmp_observations,
            subscription=subscription, retail_valid_applications=request.retail_valid_applications,
            shareholder_quota=shareholder_quota, user_holds_parent_shares=request.user_holds_parent_shares,
        )

        # Product Effectiveness Patch, P1 #2 -- computed unconditionally
        # (previously only computed when a journal happened to be
        # configured, which accidentally hid this classification from the
        # HTTP response whenever persistence was disabled). Same function,
        # same thresholds, same result as `/api/ipo/compare` already
        # returns -- attached to the response via `dataclasses.replace()`,
        # never recomputed differently.
        hidden: HiddenOpportunityAssessment | None = None
        if result.identity is not None:
            hidden = assess_hidden_opportunity(
                result.identity, gmp_summary=result.gmp_summary, allotment_estimate=result.allotment_estimate,
                high_gmp_pct_threshold=IPOAnalysisConfig().high_gmp_pct_threshold,
                low_gmp_pct_threshold=IPOAnalysisConfig().low_gmp_pct_threshold,
                high_subscription_threshold=IPOAnalysisConfig().high_subscription_threshold,
                low_subscription_threshold=IPOAnalysisConfig().low_subscription_threshold,
            )
            result = dataclasses.replace(result, hidden_opportunity=hidden)

        # Phase 8 -- persist an immutable snapshot of this analysis,
        # additive telemetry that must never break the response itself.
        ipo_journal: JsonlIPOAuditJournalRepository | None = getattr(app.state, "ipo_journal", None)
        if ipo_journal is not None:
            try:
                snapshot = build_ipo_analysis_snapshot(result, hidden_opportunity=hidden)
                await ipo_journal.save_snapshot(snapshot)
            except Exception as exc:  # noqa: BLE001 -- persistence is additive; it must never break the analysis response, and there is no response field to surface it in
                logging.getLogger(__name__).warning("IPO audit snapshot persistence failed: %s", exc)

        # Section 6 -- a successful IPO analysis establishes the IPO
        # follow-up context, structurally independent of the options
        # context above (separate field on the same `ContextState`).
        if result.identity is not None:
            context_state = getattr(app.state, "query_context", None)
            if context_state is not None:
                context_state.ipo = IPOQueryContext(
                    query=request.query, company_name=result.identity.company_name, ipo_id=result.identity.ipo_id,
                    audit_id=None, established_at=utc_now(),
                )

        return result

    @app.post("/api/ipo/compare")
    async def ipo_compare(request: IPOCompareRequest) -> dict[str, object]:
        """Parts 2/3 -- explicit-evidence comparison + hidden-opportunity
        assessment across multiple IPOs. Empty `queries` scans the real
        Upstox `open` bucket instead. Never a ranking -- entries are
        returned in the same order they were resolved, and any query
        with no real match is reported separately, never silently
        dropped."""
        provider: UpstoxProvider | None = getattr(app.state, "provider", None)
        config = IPOAnalysisConfig()

        queries = list(request.queries)
        if not queries:
            if provider is None:
                raise HTTPException(status_code=503, detail="UPSTOX_ACCESS_TOKEN is not configured -- cannot scan the real open-IPO universe without it")
            open_identities, _ = await discover_ipos(provider=provider, status="open")
            queries = [i.company_name for i in open_identities]

        entries = []
        not_found = []
        for q in queries:
            result = await analyze_ipo_query_live(q, as_of=utc_now(), provider=provider, config=config)
            if result.identity is None:
                not_found.append(q)
                continue
            comparison_entry = build_comparison_entry(IPOComparisonInput(
                identity=result.identity, gmp_summary=result.gmp_summary, listing_range=result.listing_range,
                subscription=result.subscription, allotment_estimate=result.allotment_estimate, shareholder_quota=result.shareholder_quota,
            ))
            hidden = assess_hidden_opportunity(
                result.identity, gmp_summary=result.gmp_summary, allotment_estimate=result.allotment_estimate,
                high_gmp_pct_threshold=config.high_gmp_pct_threshold, low_gmp_pct_threshold=config.low_gmp_pct_threshold,
                high_subscription_threshold=config.high_subscription_threshold, low_subscription_threshold=config.low_subscription_threshold,
            )
            identity_dict: dict[str, object] = dataclasses.asdict(comparison_entry.identity)
            identity_dict["issue_type"] = comparison_entry.identity.issue_type.value
            entries.append({
                "query": q,
                "comparison": {
                    "identity": identity_dict,
                    "dimensions": [dataclasses.asdict(d) for d in comparison_entry.dimensions],
                    "data_completeness": str(comparison_entry.data_completeness),
                },
                "hidden_opportunity": dataclasses.asdict(hidden),
            })

        return {"entries": entries, "not_found": not_found}

    @app.get("/api/ipo/list")
    async def ipo_list(status: str = "open", page_number: int = 1) -> dict[str, object]:
        """Part 3/5 -- real IPO discovery via Upstox's authorized
        `/v2/ipos`. `status` must be one of upcoming/open/closed/listed."""
        provider: UpstoxProvider | None = getattr(app.state, "provider", None)
        if provider is None:
            raise HTTPException(status_code=503, detail="UPSTOX_ACCESS_TOKEN is not configured -- IPO discovery needs the same real Upstox credential the options side uses")
        try:
            identities, page = await discover_ipos(provider=provider, status=status, page_number=page_number)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return {
            "status": status,
            "page": {"page_number": page.page_number, "total_pages": page.total_pages, "records": page.records, "total_records": page.total_records},
            "ipos": [{**dataclasses.asdict(i), "issue_type": i.issue_type.value} for i in identities],
        }

    @app.get("/api/ipo/journal/{ipo_id}")
    async def ipo_journal(ipo_id: str) -> list[dict[str, object]]:
        """Read path for the IPO audit journal (previously write-only --
        see the last product audit). `ipo_id` is Upstox's own real id
        (e.g. `"tempsens-instruments-india-limited-ipo"`, already present
        on any `/api/ipo/analyze`/`/api/ipo/compare` response's
        `identity.ipo_id`). Every persisted analysis of this real IPO is
        returned in the order it was recorded, oldest first, each paired
        with its real listing-outcome record if one was ever recorded --
        never merged into or overwriting the original snapshot (Phase 8's
        no-look-ahead discipline, unchanged, just read back here for the
        first time).
        """
        ipo_journal_repo: JsonlIPOAuditJournalRepository | None = getattr(app.state, "ipo_journal", None)
        if ipo_journal_repo is None:
            raise HTTPException(status_code=503, detail="IPO audit journal is not configured")
        snapshots = await ipo_journal_repo.query_by_ipo_id(ipo_id)
        entries: list[dict[str, object]] = []
        for snapshot in sorted(snapshots, key=lambda s: s.analyzed_at):
            outcome = await ipo_journal_repo.get_listing_outcome(snapshot.audit_id)
            entries.append({
                "snapshot": snapshot.model_dump(mode="json"),
                "listing_outcome": outcome.model_dump(mode="json") if outcome is not None else None,
            })
        return entries

    @app.post("/api/ipo/followup")
    async def ipo_followup(request: FollowupRequest) -> dict[str, object]:
        """Section 6, IPO side -- mirrors `/api/followup` exactly, but
        against the separate `query_context.ipo` field (structurally
        isolated from the options context; see `query_context.py`'s
        module docstring, rule 5). Only the `audit_history` action is
        actually executed here (a real, fully-known `ipo_id` is enough to
        fetch its journal); `compare_add` only returns the pointer --
        comparing needs OTHER IPOs this context does not have, and this
        system never guesses a company to compare against (rule 4)."""
        context_state: ContextState | None = getattr(app.state, "query_context", None)
        resolution = resolve_ipo_followup(request.text, context=context_state.ipo if context_state is not None else None, now=utc_now())

        journal_entries: list[dict[str, object]] | None = None
        if resolution.kind == FollowupKind.RESOLVED_QUERY and resolution.action == "audit_history" and resolution.resolved_query is not None:
            ipo_journal_repo: JsonlIPOAuditJournalRepository | None = getattr(app.state, "ipo_journal", None)
            if ipo_journal_repo is not None:
                snapshots = await ipo_journal_repo.query_by_ipo_id(resolution.resolved_query)
                journal_entries = []
                for snapshot in sorted(snapshots, key=lambda s: s.analyzed_at):
                    outcome = await ipo_journal_repo.get_listing_outcome(snapshot.audit_id)
                    journal_entries.append({
                        "snapshot": snapshot.model_dump(mode="json"),
                        "listing_outcome": outcome.model_dump(mode="json") if outcome is not None else None,
                    })

        return {
            "resolution": {"kind": resolution.kind.value, "message": resolution.message, "resolved_query": resolution.resolved_query, "action": resolution.action},
            "journal": journal_entries,
        }

    @app.get("/api/status")
    async def status() -> dict[str, object]:
        """Phase 14 -- a non-sensitive operational status view. Never
        exposes tokens/keys/credentials/env values -- only whether a
        provider is configured (a boolean), real market/journal state.
        """
        journal: JsonlAuditJournalRepository | None = getattr(app.state, "journal", None)
        journal_view: JournalStatusView | None = await build_journal_status(journal) if journal is not None else None
        now = utc_now()
        session = classify_session(to_ist(now).date())
        return {
            "app_status": "RUNNING",
            "server_time_utc": now.isoformat(),
            "provider_configured": getattr(app.state, "provider", None) is not None,
            "journal": journal_view.model_dump(mode="json") if journal_view is not None else None,
            # Part 4A -- descriptive only (weekend detection is a real
            # fact; holiday detection is honestly empty absent an
            # authorized source -- see trading_calendar.py). Never a
            # claim about whether the market is open RIGHT NOW (that is
            # MarketDataState's job, derived from the real Upstox
            # market-status endpoint on the next `/api/analyze` call).
            "market_calendar": {
                "calendar_date_ist": session.calendar_date.isoformat(),
                "is_trading_day": session.is_trading_day,
                "is_weekend": session.is_weekend,
                "is_holiday": session.is_holiday,
                "is_special_session": session.is_special_session,
                "next_trading_day": session.next_trading_day.isoformat() if session.next_trading_day is not None else None,
            },
        }

    @app.get("/api/outcomes/{audit_id}")
    async def outcomes(audit_id: str) -> OutcomeTrackingView:
        """Phase 12 -- the dashboard's LIVE OUTCOME TRACKING section for
        one already-open analysis (`audit_id` from that analysis's
        `AnalyzeResponse.audit_id`)."""
        journal: JsonlAuditJournalRepository | None = getattr(app.state, "journal", None)
        if journal is None:
            raise HTTPException(status_code=503, detail="audit journal is not configured")
        view = await build_outcome_tracking(journal, audit_id)
        if view is None:
            raise HTTPException(status_code=404, detail=f"no analysis found for audit_id={audit_id!r}")
        return view

    @app.get("/api/research/daily", response_model=DailyResearchView)
    async def research_daily(symbols: str | None = None) -> DailyResearchView:
        """Daily Market Researcher -- read-only. Scans `symbols` (comma-
        separated, e.g. `?symbols=GAIL,RELIANCE`) or the default curated
        universe, reusing the exact same `run_analysis()` path a manual
        `/api/analyze` call uses for every symbol (same audit-journal
        persistence, same decision engine, same everything) -- this
        endpoint only ranks and synthesizes over those real results. Never
        places, modifies, or references a broker order (see
        SAFETY_LOCK.txt); this route has no such capability anywhere in
        its call graph."""
        provider: UpstoxProvider | None = getattr(app.state, "provider", None)
        instrument_master: Sequence[dict[str, object]] | None = getattr(app.state, "instrument_master", None)
        if provider is None or instrument_master is None:
            raise HTTPException(
                status_code=503,
                detail="UPSTOX_ACCESS_TOKEN is not configured -- see docs/data-sources/PROVIDER_DECISION.md",
            )
        requested_symbols = [s.strip().upper() for s in symbols.split(",") if s.strip()] if symbols else None
        result = await run_daily_research(
            requested_symbols, provider=provider, instrument_master=instrument_master,
            mcx_instrument_master=getattr(app.state, "mcx_instrument_master", None),
            sector_map=getattr(app.state, "sector_map", None),
            strategy=app.state.strategy, repositories=app.state.repositories, config=app.state.config,
            as_of=utc_now(), journal=getattr(app.state, "journal", None),
            research_journal=getattr(app.state, "research_journal", None),
            outcome_repository=getattr(app.state, "outcome_repository", None),
            nifty50_symbols=getattr(app.state, "nifty50_symbols", None),
            http_client=getattr(app.state, "http_client", None),
            delivery_cache_dir=getattr(app.state, "delivery_cache_dir", None),
        )
        return build_daily_research_view(result)

    @app.get("/api/research/discover", response_model=DailyResearchView)
    async def research_discover(symbols: str | None = None) -> DailyResearchView:
        """Whole-market discovery alias of GET /api/research/daily.

        Same two-stage researcher, same gates, same response shape -- added
        so the product contract can say /discover without a second engine.
        """
        return await research_daily(symbols)

    @app.post("/api/research/jobs/discover")
    async def start_discover_job(symbols: str | None = None) -> dict[str, object]:
        """Start discovery as a background job. Does not occupy the HTTP request.

        Whole-market live Discover stays blocked unless the calendar session
        is OPEN. Closed-session last prints are not a live F&O scan (Gate 1).
        Symbol analyze remains available separately.
        """
        window = classify_session_window(utc_now())
        if window.session_window != "OPEN":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "LIVE_DISCOVER_UNAVAILABLE",
                    "message": "LIVE DISCOVER UNAVAILABLE — MARKET CLOSED",
                    **session_window_payload(window),
                },
            )
        provider: UpstoxProvider | None = getattr(app.state, "provider", None)
        instrument_master: Sequence[dict[str, object]] | None = getattr(app.state, "instrument_master", None)
        if provider is None or instrument_master is None:
            raise HTTPException(
                status_code=503,
                detail="UPSTOX_ACCESS_TOKEN is not configured -- see docs/data-sources/PROVIDER_DECISION.md",
            )
        requested = tuple(s.strip().upper() for s in symbols.split(",") if s.strip()) if symbols else None
        registry: ResearchJobRegistry = app.state.research_jobs
        job = await registry.start(symbols=requested, runner=_discover_job_runner)
        return job.as_dict()

    @app.get("/api/research/jobs/latest")
    async def latest_discover_job() -> dict[str, object]:
        registry: ResearchJobRegistry = app.state.research_jobs
        window = classify_session_window(utc_now())
        session = session_window_payload(window)
        job = registry.latest()
        if job is None:
            message = (
                "LIVE DISCOVER UNAVAILABLE — MARKET CLOSED"
                if window.session_window != "OPEN"
                else "Ready to scan the market"
            )
            return {"status": "NONE", "message": message, "result": None, **session}
        payload = job.as_dict()
        payload.update(session)
        return payload

    @app.get("/api/research/jobs/{job_id}")
    async def get_discover_job(job_id: str) -> dict[str, object]:
        registry: ResearchJobRegistry = app.state.research_jobs
        job = registry.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown research job")
        return job.as_dict()

    @app.get("/api/research/history", response_model=ResearchHistoryView)
    async def research_history(
        symbol: str | None = None, day: str | None = None, direction: str | None = None, outcome_status: str | None = None,
    ) -> ResearchHistoryView:
        """Sprint 5, Objective 4/12 -- read-only review of previously
        researched candidates and what has objectively happened to them
        since. Filters are lightweight, deterministic equality checks
        (`symbol`, `day` as `YYYY-MM-DD`, `direction`, `outcome_status`);
        never a ranking -- historical outcomes shown here have no effect
        on today's `/api/research/daily` shortlist (see
        `build_research_history()`'s own docstring)."""
        outcome_repository: JsonlResearchOutcomeRepository | None = getattr(app.state, "outcome_repository", None)
        if outcome_repository is None:
            raise HTTPException(status_code=503, detail="research outcome tracking is not configured")
        try:
            parsed_day = date.fromisoformat(day) if day else None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"day must be YYYY-MM-DD: {exc}") from exc
        return await build_research_history(
            outcome_repository, as_of=utc_now(), symbol=symbol, day=parsed_day, direction=direction, outcome_status=outcome_status,
        )

    @app.get("/api/research/patterns", response_model=PatternAggregationView)
    async def research_patterns(source: str = "live") -> PatternAggregationView:
        """Final 95% sprint (Sections 8/9/25) -- deterministic historical
        PATTERN AGGREGATION: "when this named pattern appeared
        historically, what actually happened afterward?"

        Read-only and purely DESCRIPTIVE. Every number is an exact count
        over real persisted observations whose outcomes were already
        determined by their own outcome mechanism -- never a win rate,
        probability, confidence score, expected return, or prediction
        (there is no float field in the response model at all). The
        response leads with `sample_size_note`, which states the real
        sample size and its limitations BEFORE any count, and reports
        `DERIVATIVES_HISTORY_UNAVAILABLE` honestly rather than implying
        an absence of options activity.

        Registered BEFORE `/api/research/{observation_id}/outcome` so the
        literal path segment `patterns` is never captured as an
        observation id by that route's path parameter.
        """
        # Release gate (Section 11) -- `?source=replay` reads the historical
        # replay DATASET (episodes over real price history) instead of the
        # live store. The two are never merged into one count.
        if source.lower() == "replay":
            try:
                return await build_replay_pattern_aggregation(
                    dataset_path=_REPLAY_DATASET_DIR / "replay_dataset.jsonl",
                    summary_path=_REPLAY_DATASET_DIR / "replay_dataset_summary.json",
                    observation_dir=_REPLAY_OUTCOME_DIR, as_of=utc_now(),
                )
            except ReplayDatasetUnavailable as exc:
                raise HTTPException(
                    status_code=404,
                    detail="HISTORICAL REPLAY DATASET NOT AVAILABLE IN THIS DEPLOYMENT",
                ) from exc
        if source.lower() != "live":
            raise HTTPException(status_code=422, detail="source must be 'live' or 'replay'")
        outcome_repository: JsonlResearchOutcomeRepository | None = getattr(app.state, "outcome_repository", None)
        if outcome_repository is None:
            raise HTTPException(status_code=503, detail="research outcome tracking is not configured")
        return await build_live_pattern_aggregation(outcome_repository, as_of=utc_now())

    @app.get("/api/research/replay-dataset", response_model=ReplayDatasetRowsView)
    async def research_replay_dataset(
        symbol: str | None = None, status: str | None = None, direction: str | None = None, limit: int = 50,
    ) -> ReplayDatasetRowsView:
        """Release gate (Sections 10/21) -- read-only browse of individual
        historical replay observations, "what TIRE knew then" kept apart
        from "what happened after". Registered before the
        `{observation_id}` route for the same reason as `/patterns`."""
        try:
            return read_replay_dataset_rows(
                _REPLAY_DATASET_DIR / "replay_dataset.jsonl", symbol=symbol, status=status, direction=direction,
                limit=max(1, min(limit, 200)),
            )
        except ReplayDatasetUnavailable as exc:
            raise HTTPException(
                status_code=404,
                detail="HISTORICAL REPLAY DATASET NOT AVAILABLE IN THIS DEPLOYMENT",
            ) from exc

    def _watch_service() -> ResearchWatchService:
        repo = getattr(app.state, "watch_repository", None)
        if repo is None:
            raise HTTPException(status_code=503, detail="research watch persistence is not configured")
        return ResearchWatchService(repo)

    @app.post("/api/research/watches")
    async def create_research_watch(body: CreateResearchWatchRequest) -> dict[str, object]:
        """Pin a personal research observation. Never places an order."""
        service = _watch_service()
        kind = body.snapshot_kind if body.snapshot_kind in {"analyze", "screener", "raw"} else "analyze"
        observation = observation_from_client(body.observation, kind=kind)
        try:
            view = await service.create(
                symbol=body.symbol,
                query=body.query,
                observation=observation,
                t0_unavailable=body.t0_unavailable,
            )
        except DuplicateWatchError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except WatchUnavailableError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return view.model_dump(mode="json")

    @app.get("/api/research/watches")
    async def list_research_watches() -> dict[str, object]:
        service = _watch_service()
        views = await service.list_watches()
        outcome_repository = getattr(app.state, "outcome_repository", None)
        if outcome_repository is not None:
            enriched = []
            for view in views:
                oid = view.t0.observation_id if view.t0 is not None else None
                labels: list[str] = []
                if oid:
                    try:
                        checkpoints = await outcome_repository.query_checkpoints_for_observation(oid)
                        labels = [c.checkpoint_label.value for c in checkpoints]
                    except Exception:
                        logging.getLogger(__name__).exception("watch outcome labels failed for %s", oid)
                        labels = []
                enriched.append(view.model_copy(update={"outcome_checkpoint_labels": labels}))
            views = enriched
        return {"watches": [v.model_dump(mode="json") for v in views]}

    @app.post("/api/research/watches/migrate")
    async def migrate_research_watches(body: MigrateResearchWatchesRequest) -> dict[str, object]:
        service = _watch_service()
        kind = body.snapshot_kind if body.snapshot_kind in {"analyze", "screener", "raw"} else "screener"
        observations = {
            key.strip().upper(): obs
            for key, raw in body.observations.items()
            if (obs := observation_from_client(raw, kind=kind)) is not None
        }
        views = await service.migrate_symbols(body.symbols, observations)
        return {"watches": [v.model_dump(mode="json") for v in views]}

    @app.get("/api/research/watches/{watch_id}")
    async def get_research_watch(watch_id: str) -> dict[str, object]:
        view = await _watch_service().get(watch_id)
        if view is None:
            raise HTTPException(status_code=404, detail="watch not found")
        return view.model_dump(mode="json")

    @app.post("/api/research/watches/{watch_id}/latest")
    async def update_research_watch_latest(watch_id: str, body: UpdateWatchLatestRequest) -> dict[str, object]:
        kind = body.snapshot_kind if body.snapshot_kind in {"analyze", "screener", "raw"} else "analyze"
        observation = observation_from_client(body.observation, kind=kind)
        if observation is None:
            raise HTTPException(status_code=400, detail="observation is required")
        try:
            view = await _watch_service().update_latest(watch_id, observation)
        except WatchUnavailableError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return view.model_dump(mode="json")

    @app.delete("/api/research/watches/{watch_id}")
    async def delete_research_watch(watch_id: str) -> dict[str, object]:
        removed = await _watch_service().remove(watch_id)
        if not removed:
            raise HTTPException(status_code=404, detail="watch not found")
        return {"removed": True, "watch_id": watch_id}

    @app.get("/api/research/{observation_id}/outcome", response_model=ResearchOutcomeDetailView)
    async def research_observation_outcome(observation_id: str) -> ResearchOutcomeDetailView:
        """Sprint 5, Objective 1/12 -- one previously-researched
        candidate's real observation, every real checkpoint captured for
        it so far, and the deterministic outcome summary derived from
        them (never a probability/score -- see `ResearchOutcomeSummary`'s
        own docstring)."""
        outcome_repository: JsonlResearchOutcomeRepository | None = getattr(app.state, "outcome_repository", None)
        if outcome_repository is None:
            raise HTTPException(status_code=503, detail="research outcome tracking is not configured")
        view = await build_research_outcome_detail(outcome_repository, observation_id, as_of=utc_now())
        if view is None:
            raise HTTPException(status_code=404, detail=f"no research observation found for observation_id={observation_id!r}")
        return view

    @app.get("/api/research/{observation_id}/capture", response_model=ForwardCaptureView)
    async def research_observation_capture(observation_id: str) -> ForwardCaptureView:
        """Sprint 3.3 -- read-only inspection/verification of one observation's
        forward-capture record: its capture kind (forward live / historical
        replay / legacy live), its frozen T0 provenance, and the integrity
        verdict (`verified` + `problems`) recomputed from the persisted record
        alone. Writes nothing."""
        outcome_repository: JsonlResearchOutcomeRepository | None = getattr(app.state, "outcome_repository", None)
        if outcome_repository is None:
            raise HTTPException(status_code=503, detail="research outcome tracking is not configured")
        observation = await outcome_repository.get_observation(observation_id)
        if observation is None:
            raise HTTPException(status_code=404, detail=f"no research observation found for observation_id={observation_id!r}")
        return build_forward_capture_view(observation)

    @app.post("/api/journal/personal")
    async def save_personal_journal(body: PersonalJournalEntryRequest) -> dict[str, object]:
        """Append-only operator research note. Read-only market system --
        this route never places, modifies, or references a broker order."""
        repo: JsonlPersonalJournalRepository | None = getattr(app.state, "personal_journal", None)
        if repo is None:
            raise HTTPException(status_code=503, detail="personal journal is not configured")
        entry = PersonalJournalEntry(
            recorded_at=utc_now(),
            symbol=body.symbol.strip().upper(),
            user_reasoning=body.user_reasoning,
            market_regime=body.market_regime,
            sector=body.sector,
            research_state=body.research_state,
            research_bucket=body.research_bucket,
            timing_stage=body.timing_stage,
            direction_hypothesis=body.direction_hypothesis,
            underlying_price=body.underlying_price,
            option_contract=body.option_contract,
            strike=body.strike,
            expiry=body.expiry,
            dte=body.dte,
            iv=body.iv,
            spread=body.spread,
            liquidity=body.liquidity,
            supporting_evidence=body.supporting_evidence,
            missing_evidence=body.missing_evidence,
            confirmation=body.confirmation,
            invalidation=body.invalidation,
            audit_id=body.audit_id,
        )
        await repo.save_entry(entry)
        return entry.model_dump(mode="json")

    @app.get("/api/journal/personal")
    async def list_personal_journal(symbol: str | None = None) -> dict[str, object]:
        repo: JsonlPersonalJournalRepository | None = getattr(app.state, "personal_journal", None)
        if repo is None:
            raise HTTPException(status_code=503, detail="personal journal is not configured")
        wanted = symbol.strip().upper() if symbol else None
        entries = await repo.query_entries(symbol=wanted)
        return {"entries": [e.model_dump(mode="json") for e in entries]}

    @app.post("/api/journal/personal/{journal_id}/outcome")
    async def save_personal_journal_outcome(journal_id: str, body: PersonalJournalOutcomeRequest) -> dict[str, object]:
        repo: JsonlPersonalJournalRepository | None = getattr(app.state, "personal_journal", None)
        if repo is None:
            raise HTTPException(status_code=503, detail="personal journal is not configured")
        try:
            mistake = MistakeClass(body.mistake_class)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"unknown mistake_class: {body.mistake_class}") from exc
        outcome = PersonalJournalOutcome(
            journal_id=journal_id,
            captured_at=utc_now(),
            what_i_thought=body.what_i_thought,
            what_tire_observed=body.what_tire_observed,
            what_actually_happened=body.what_actually_happened,
            mistake_class=mistake,
            notes=body.notes,
        )
        await repo.save_outcome(outcome)
        return outcome.model_dump(mode="json")

    @app.get("/api/journal/personal/{journal_id}/outcomes")
    async def list_personal_journal_outcomes(journal_id: str) -> dict[str, object]:
        repo: JsonlPersonalJournalRepository | None = getattr(app.state, "personal_journal", None)
        if repo is None:
            raise HTTPException(status_code=503, detail="personal journal is not configured")
        outcomes = await repo.query_outcomes(journal_id)
        return {"outcomes": [o.model_dump(mode="json") for o in outcomes]}

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.api.main:app", host="127.0.0.1", port=8000, reload=False)
