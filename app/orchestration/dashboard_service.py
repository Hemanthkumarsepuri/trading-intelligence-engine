"""Orchestration-layer service backing the localhost dashboard (Sprints
1-3).

Wraps the ALREADY-EXISTING `analyze_symbol()` pipeline, the ALREADY-
EXISTING `render_compact()`/`render_text()` renderers, and (Sprint 3) the
ALREADY-EXISTING `build_visual_data()` chart-data assembler — this module
performs NO new computation of its own. Its only job is: parse the
free-text query, run the real pipeline for the resolved symbol, and
package the result into a thin, presentation-ready `AnalyzeResponse`.

"Do not duplicate domain calculations in frontend" is enforced
structurally here: `AnalyzeResponse` carries the FULLY pre-rendered
`compact_report`/`detailed_report` text, a handful of scalar fields for
dashboard chrome, AND (Sprint 3) the fully-computed `visual` chart/table
payload — never raw inputs for the frontend to re-derive a number from.

The parsed strike/right (`ParsedQuery.has_specific_contract`) selects
which specific contract `analyze_symbol()` analyzes independently of its
own bias (Sprint 2's `contract_analysis.py`); the resulting comparison is
in both the rendered text AND the structured `visual.requested_contract`.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
from pydantic import BaseModel

from app.data.providers.upstox_provider import UpstoxProvider
from app.domain.audit.snapshot_diff import (
    FieldChange,
    SnapshotChangeReport,
    build_snapshot_change_report,
)
from app.domain.market.trading_calendar import classify_session_window, session_window_payload
from app.domain.options.early_opportunity import TimingStage, classify_research_bucket
from app.domain.options.freshness_label import DataStream
from app.domain.options.query_parser import ParsedQuery, parse_instrument_query
from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy
from app.orchestration.audit_journal import build_analysis_snapshot
from app.orchestration.options_intelligence_pipeline import (
    AnalysisProvider,
    PipelineConfig,
    Repositories,
    analyze_symbol,
)
from app.orchestration.tomorrow_watch import TomorrowWatchView, build_tomorrow_watch
from app.orchestration.visual_data import VisualData, build_visual_data
from app.orchestration.watch_next import WatchCondition, build_watch_conditions
from app.persistence.jsonl_file import JsonlAuditJournalRepository


class FieldChangeView(BaseModel):
    label: str
    before: str | None
    after: str | None
    changed: bool | None


class WhatChangedView(BaseModel):
    """Section 7/9 -- "What changed?" relative to the most recent PRIOR
    analysis of this same symbol already in the audit journal. Every
    value here is copied from `SnapshotChangeReport`
    (`app.domain.audit.snapshot_diff`) -- no computation happens in this
    view, only serialization for the API response."""

    has_prior_snapshot: bool
    previous_audit_id: str | None = None
    previous_generated_at: datetime | None = None
    time_since_previous_seconds: float | None = None
    underlying: list[FieldChangeView] = []
    options_structure: list[FieldChangeView] = []
    ce_requested: list[FieldChangeView] = []
    pe_requested: list[FieldChangeView] = []
    decision: list[FieldChangeView] = []
    newly_triggered_risks: list[str] = []
    newly_resolved_risks: list[str] = []
    notes: list[str] = []

    @classmethod
    def from_report(cls, report: SnapshotChangeReport) -> WhatChangedView:
        def _fields(items: list[FieldChange]) -> list[FieldChangeView]:
            return [FieldChangeView(label=f.label, before=f.before, after=f.after, changed=f.changed) for f in items]

        return cls(
            has_prior_snapshot=report.has_prior_snapshot, previous_audit_id=report.previous_audit_id,
            previous_generated_at=report.previous_generated_at, time_since_previous_seconds=report.time_since_previous_seconds,
            underlying=_fields(list(report.underlying)), options_structure=_fields(list(report.options_structure)),
            ce_requested=_fields(list(report.ce_requested)), pe_requested=_fields(list(report.pe_requested)),
            decision=_fields(list(report.decision)), newly_triggered_risks=list(report.newly_triggered_risks),
            newly_resolved_risks=list(report.newly_resolved_risks), notes=list(report.notes),
        )


class WatchConditionView(BaseModel):
    watch: str
    why: str
    current: str
    status: str

    @classmethod
    def from_condition(cls, condition: WatchCondition) -> WatchConditionView:
        return cls(watch=condition.watch, why=condition.why, current=condition.current, status=condition.status.value)


class SessionContextView(BaseModel):
    """Calendar research context. Never converts last-observed prints into LIVE."""

    session_window: str
    research_session_mode: str
    observation_kind: str
    next_session_open_ist: datetime | None = None
    next_session_open_ist_label: str | None = None
    live_discover_available: bool


def _tomorrow_watch_for(
    session: SessionContextView,
    *,
    research_state: str | None,
    visual: VisualData | None,
    has_specific_contract: bool,
    invalidation_condition: str | None,
    market_observed_at: datetime | None,
) -> TomorrowWatchView | None:
    return build_tomorrow_watch(
        session_window=session.session_window,
        observation_kind=session.observation_kind,
        research_session_mode=session.research_session_mode,
        next_session_open_ist_label=session.next_session_open_ist_label,
        research_state=research_state,
        visual=visual,
        has_specific_contract=has_specific_contract,
        invalidation_condition=invalidation_condition,
        market_observed_at=market_observed_at,
    )


def session_context_view(as_of: datetime) -> SessionContextView:
    payload = session_window_payload(classify_session_window(as_of))
    return SessionContextView(
        session_window=str(payload["session_window"]),
        research_session_mode=str(payload["research_session_mode"]),
        observation_kind=str(payload["observation_kind"]),
        next_session_open_ist=datetime.fromisoformat(str(payload["next_session_open_ist"])),
        next_session_open_ist_label=str(payload["next_session_open_ist_label"]),
        live_discover_available=bool(payload["live_discover_available"]),
    )


class AnalyzeResponse(BaseModel):
    query: str
    parsed_symbol: str | None
    parsed_strike: str | None
    parsed_right: str | None
    parsed_expiry_hint: str | None
    has_specific_contract: bool
    parse_warnings: list[str]

    symbol: str | None = None
    generated_at: datetime | None = None
    market_state: str | None = None
    data_quality: str | None = None
    evidence_quality: str | None = None
    decision_quality: str | None = None
    decision: str | None = None
    research_state: str | None = None
    timing_stage: str | None = None
    timing_reason: str | None = None
    market_observed_at: datetime | None = None
    session: SessionContextView | None = None
    tomorrow_watch: TomorrowWatchView | None = None

    error: str | None = None
    compact_report: str | None = None
    detailed_report: str | None = None
    visual: VisualData | None = None

    # Sprint 5, Phase 1/12 -- when a `journal` repository is wired in (the
    # live FastAPI app; omitted in most tests), this analysis is also
    # persisted as an immutable `AnalysisSnapshot` and this response says
    # so, so the dashboard can distinguish CURRENT ANALYSIS from what is
    # now trackable in LIVE OUTCOME TRACKING. `journal_status` is one of
    # "PERSISTED" / "SKIPPED_NO_DECISION" / "DISABLED" / an "ERROR: ..."
    # detail -- never silently swallowed.
    audit_id: str | None = None
    journal_status: str | None = None

    # Master Product Grooming Sprint, Sections 7/8 -- deterministic
    # synthesis over already-computed/already-persisted evidence only.
    # `what_changed` is `None` (not an empty view) when there is no
    # journal wired in at all -- distinct from "wired in but no prior
    # snapshot exists yet" (`has_prior_snapshot=False`).
    what_changed: WhatChangedView | None = None
    watch_next: list[WatchConditionView] = []

    # Product Effectiveness Patch, P1 #1 -- surfaces the SAME reasoning/
    # invalidation the detailed text report already contains, as
    # structured fields, so the Final Assessment panel does not require
    # expanding "Detailed Audit Report" to see WHY or WHAT would
    # invalidate the assessment. Copied verbatim from already-computed
    # `report` fields -- no new computation, no reinterpretation.
    # `None` (never a fabricated placeholder) when the underlying report
    # genuinely has no decision/candidate to draw from.
    reasoning: str | None = None
    invalidation_level: str | None = None
    invalidation_condition: str | None = None

    latency_seconds: float


def timing_from_report(report: object) -> tuple[str, str | None]:
    """Same `classify_research_bucket()` timing the screener already uses.

    `early_stage_state` is not recomputed here: that gated daily-scan
    ordinal needs a ranked candidate. Timing still comes from the same
    classifier, with research state, named pattern, day-change, and
    contract liquidity copied from this report. UNKNOWN is returned with
    a reason when the classifier cannot determine a stage.
    """
    development = getattr(report, "development", None)
    pattern = getattr(development, "pattern", None)
    pattern_value = pattern.value if pattern is not None and hasattr(pattern, "value") else (str(pattern) if pattern else None)
    research_state = getattr(report, "research_state", None)
    state = research_state.value if research_state is not None and hasattr(research_state, "value") else research_state
    liquidity_grade = None
    candidates = getattr(report, "candidates", None) or []
    if candidates:
        liquidity = getattr(candidates[0], "liquidity", None)
        grade = getattr(liquidity, "grade", None)
        liquidity_grade = grade.value if grade is not None and hasattr(grade, "value") else (str(grade) if grade else None)
    assessment = classify_research_bucket(
        research_state=str(state) if state is not None else None,
        early_stage_state="UNKNOWN",
        development_pattern=pattern_value,
        pre_breakout_signal=False,
        event_risk="UNKNOWN",
        liquidity_grade=liquidity_grade,
        day_change_pct=getattr(report, "day_change_pct", None),
    )
    timing = assessment.timing.value
    reason = None
    if assessment.timing == TimingStage.UNKNOWN:
        reason = "Insufficient evidence to classify timing."
    return timing, reason


def market_observed_at_from_report(report: object) -> datetime | None:
    """Underlying quote print time — not `generated_at` (analysis clock)."""
    for stream in getattr(report, "stream_freshness", None) or []:
        name = getattr(getattr(stream, "stream", None), "value", getattr(stream, "stream", None))
        stamp = getattr(stream, "data_timestamp", None)
        if name == DataStream.UNDERLYING_QUOTE.value and isinstance(stamp, datetime):
            return stamp
    return None


def _parsed_response_fields(parsed: ParsedQuery) -> dict[str, object]:
    return {
        "parsed_symbol": parsed.symbol,
        "parsed_strike": str(parsed.strike) if parsed.strike is not None else None,
        "parsed_right": parsed.right.value if parsed.right is not None else None,
        "parsed_expiry_hint": parsed.expiry_hint,
        "has_specific_contract": parsed.has_specific_contract,
        "parse_warnings": list(parsed.errors),
    }


async def run_analysis(
    query: str,
    *,
    provider: AnalysisProvider,
    instrument_master: Sequence[dict[str, object]],
    strategy: EMAVWAPAlignmentStrategy,
    repositories: Repositories,
    config: PipelineConfig,
    as_of: datetime,
    mcx_instrument_master: Sequence[dict[str, object]] | None = None,
    sector_map: dict[str, str] | None = None,
    journal: JsonlAuditJournalRepository | None = None,
    nifty50_symbols: Sequence[str] | None = None,
    http_client: httpx.AsyncClient | None = None,
    delivery_cache_dir: Path | None = None,
    fii_cash_net: Decimal | None = None,
    dii_cash_net: Decimal | None = None,
    index_fo_net: Decimal | None = None,
    fii_dii_as_of: date | None = None,
    fii_dii_source: str | None = None,
) -> AnalyzeResponse:
    started = time.perf_counter()
    parsed = parse_instrument_query(query)
    session = session_context_view(as_of)

    if parsed.symbol is None:
        return AnalyzeResponse(
            query=query, **_parsed_response_fields(parsed), error="could not parse a symbol from the query",
            latency_seconds=time.perf_counter() - started, session=session,
            tomorrow_watch=_tomorrow_watch_for(
                session,
                research_state=None,
                visual=None,
                has_specific_contract=False,
                invalidation_condition=None,
                market_observed_at=None,
            ),
        )

    report = await analyze_symbol(
        parsed.symbol, provider=provider, instrument_master=instrument_master, strategy=strategy,
        repositories=repositories, as_of=as_of, config=config, mcx_instrument_master=mcx_instrument_master,
        sector_map=sector_map,
        # Sprint 2: when the user asked about a specific strike/right (e.g.
        # "KAYNES 4000 CE"), it is analyzed regardless of the overall bias
        # -- see contract_analysis.py's module docstring. The rendered
        # REQUESTED CONTRACT/CONTRACT ALTERNATIVES sections this produces
        # already appear inside `compact_report`/`detailed_report` below;
        # no separate response fields are needed.
        requested_strike=parsed.strike if parsed.has_specific_contract else None,
        requested_right=parsed.right if parsed.has_specific_contract else None,
        requested_expiry_hint=parsed.expiry_hint,
        requested_expiry_year=parsed.expiry_year,
        nifty50_symbols=nifty50_symbols,
        http_client=http_client,
        delivery_cache_dir=delivery_cache_dir,
        fii_cash_net=fii_cash_net, dii_cash_net=dii_cash_net, index_fo_net=index_fo_net,
        fii_dii_as_of=fii_dii_as_of, fii_dii_source=fii_dii_source,
    )
    latency = time.perf_counter() - started

    if report.error is not None:
        return AnalyzeResponse(
            query=query, **_parsed_response_fields(parsed), symbol=report.symbol, generated_at=report.generated_at,
            market_state=report.data_state.value, error=report.error, latency_seconds=latency, session=session,
            tomorrow_watch=_tomorrow_watch_for(
                session,
                research_state=report.research_state.value if report.research_state is not None else None,
                visual=None,
                has_specific_contract=parsed.has_specific_contract,
                invalidation_condition=None,
                market_observed_at=market_observed_at_from_report(report),
            ),
        )

    audit_id: str | None = None
    journal_status: str | None = None
    what_changed: WhatChangedView | None = None
    if journal is None:
        journal_status = "DISABLED"
    elif report.decision is None:
        journal_status = "SKIPPED_NO_DECISION"
    else:
        try:
            # Section 7/9 -- look up the most recent PRIOR snapshot for
            # this symbol BEFORE persisting the current one (so it never
            # sees itself as its own prior). No prior snapshot is a valid,
            # honest state (`has_prior_snapshot=False`), not an error.
            prior_snapshots = await journal.query_by_symbol(report.symbol)
            previous_snapshot = max(prior_snapshots, key=lambda s: s.identity.generated_at) if prior_snapshots else None

            snapshot = build_analysis_snapshot(report)
            await journal.save_analysis(snapshot)
            audit_id = snapshot.identity.audit_id
            journal_status = "PERSISTED"
            what_changed = WhatChangedView.from_report(build_snapshot_change_report(previous=previous_snapshot, current=snapshot))
        except Exception as exc:  # noqa: BLE001 -- persistence/what-changed is additive telemetry; it must never break the dashboard's own analysis response
            journal_status = f"ERROR: {exc}"

    watch_next = [WatchConditionView.from_condition(c) for c in build_watch_conditions(report)]

    # P1 #1 -- copied verbatim from already-computed `report` fields; no
    # new computation, no reinterpretation. `invalidation_condition`
    # defaults to `""` on `OptionCandidate` (never `None`) -- normalized
    # to `None` here so an empty string is never rendered as if it were
    # meaningful data.
    reasoning = report.decision.reasoning if report.decision is not None else None
    invalidation_level = str(report.invalidation_level) if report.invalidation_level is not None else None
    invalidation_condition = (report.candidates[0].invalidation_condition or None) if report.candidates else None
    timing_stage, timing_reason = timing_from_report(report)
    visual = build_visual_data(report)
    research_state = report.research_state.value if report.research_state is not None else None
    observed_at = market_observed_at_from_report(report)

    return AnalyzeResponse(
        query=query, **_parsed_response_fields(parsed), symbol=report.symbol, generated_at=report.generated_at,
        market_state=report.data_state.value,
        data_quality=report.quality_tiers.data_quality.value if report.quality_tiers is not None else None,
        evidence_quality=report.quality_tiers.evidence_quality.value if report.quality_tiers is not None else None,
        decision_quality=report.quality_tiers.decision_quality.value if report.quality_tiers is not None else None,
        decision=report.decision.decision.value if report.decision is not None else None,
        research_state=research_state,
        timing_stage=timing_stage,
        timing_reason=timing_reason,
        market_observed_at=observed_at,
        compact_report=report.render_compact(), detailed_report=report.render_text(),
        visual=visual, latency_seconds=latency,
        audit_id=audit_id, journal_status=journal_status, what_changed=what_changed, watch_next=watch_next,
        reasoning=reasoning, invalidation_level=invalidation_level, invalidation_condition=invalidation_condition,
        session=session,
        tomorrow_watch=_tomorrow_watch_for(
            session,
            research_state=research_state,
            visual=visual,
            has_specific_contract=parsed.has_specific_contract,
            invalidation_condition=invalidation_condition,
            market_observed_at=observed_at,
        ),
    )


class WatchlistEntry(BaseModel):
    """Part 4H -- one symbol's independent summary within a watchlist
    comparison. Every field here is copied from that symbol's OWN
    standalone `AnalyzeResponse` (`run_analysis()`, unmodified) -- no
    cross-symbol computation, no ranking, no score. `has_specific_contract`/
    `parsed_strike`/`parsed_right` keep a bare underlying query ("KAYNES")
    visibly distinct from a specific-contract query ("KAYNES 4000 CE"),
    exactly like the single-analysis dashboard already does."""

    query: str
    symbol: str | None
    parsed_strike: str | None
    parsed_right: str | None
    has_specific_contract: bool
    market_state: str | None
    decision: str | None
    data_quality: str | None
    evidence_quality: str | None
    audit_id: str | None
    journal_status: str | None
    error: str | None
    latency_seconds: float


async def run_watchlist(
    queries: Sequence[str],
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
    nifty50_symbols: Sequence[str] | None = None,
    http_client: httpx.AsyncClient | None = None,
    delivery_cache_dir: Path | None = None,
    fii_cash_net: Decimal | None = None,
    dii_cash_net: Decimal | None = None,
    index_fo_net: Decimal | None = None,
    fii_dii_as_of: date | None = None,
    fii_dii_source: str | None = None,
) -> list[WatchlistEntry]:
    """Part 4H -- runs `run_analysis()` once per query, sequentially (no
    overlapping requests to the same provider), each with its own
    identity and persistence, isolated from every other query's failure.
    Reuses the exact same analysis path a single `/api/analyze` call
    uses -- this function performs no analysis of its own, only sequencing
    and summarization into a lighter, comparison-friendly shape (the full
    `visual`/rendered-report payloads for N symbols would be needlessly
    large for a side-by-side view; open any one symbol's full analysis via
    a normal `/api/analyze` call for that).
    """
    entries: list[WatchlistEntry] = []
    for query in queries:
        try:
            response = await run_analysis(
                query, provider=provider, instrument_master=instrument_master, strategy=strategy,
                repositories=repositories, config=config, as_of=as_of, mcx_instrument_master=mcx_instrument_master,
                sector_map=sector_map, journal=journal, nifty50_symbols=nifty50_symbols,
                http_client=http_client, delivery_cache_dir=delivery_cache_dir,
                fii_cash_net=fii_cash_net, dii_cash_net=dii_cash_net, index_fo_net=index_fo_net,
                fii_dii_as_of=fii_dii_as_of, fii_dii_source=fii_dii_source,
            )
        except Exception as exc:  # noqa: BLE001 -- one symbol's unexpected failure must never break the rest of the watchlist
            entries.append(WatchlistEntry(
                query=query, symbol=None, parsed_strike=None, parsed_right=None, has_specific_contract=False,
                market_state=None, decision=None, data_quality=None, evidence_quality=None, audit_id=None,
                journal_status=None, error=f"unexpected failure: {exc}", latency_seconds=0.0,
            ))
            continue
        entries.append(WatchlistEntry(
            query=query, symbol=response.symbol, parsed_strike=response.parsed_strike, parsed_right=response.parsed_right,
            has_specific_contract=response.has_specific_contract, market_state=response.market_state,
            decision=response.decision, data_quality=response.data_quality, evidence_quality=response.evidence_quality,
            audit_id=response.audit_id, journal_status=response.journal_status, error=response.error,
            latency_seconds=response.latency_seconds,
        ))
    return entries
