"""Sprint 5 — the thin live-operation orchestration layer wiring the
already-existing, already-tested Milestone-G audit journal machinery
(`build_analysis_snapshot`, `capture_outcome_checkpoint`, `due_checkpoints`,
`build_reconciliation`) into a runnable sequence: analyze -> snapshot ->
persist -> due checkpoints -> capture -> persist -> reconcile -> persist.

Nothing here computes a new market judgment. Every domain computation is
reused verbatim from `app.orchestration.options_intelligence_pipeline`
(analysis), `app.orchestration.audit_journal` (snapshot/checkpoint
capture), and `app.domain.audit.reconciliation` (reconciliation). This
module's only job is SEQUENCING and IDEMPOTENCY -- deciding *when* to call
each of those, never re-implementing what they compute. Per Sprint 5's own
Phase 0 audit finding: none of this machinery was previously invoked by
any running code path (CLI or dashboard) -- this module is exactly and
only that missing wiring.

=== IDEMPOTENCY / RESTART SAFETY (Phase 4/10) ===
Nothing here keeps in-memory scheduling state across restarts. Every
decision ("is this checkpoint due yet", "has it already been captured") is
recomputed from the append-only `JsonlAuditJournalRepository` itself on
every call -- so a process restart naturally resumes exactly where the
persisted journal left off, and running the same tick twice back-to-back
captures nothing new the second time (the just-captured label is already
in the persisted checkpoint set `due_checkpoints()` reads).

=== CONCURRENCY (documented limitation, not solved here) ===
An in-process `asyncio.Lock` per symbol prevents two overlapping calls
*within the same process* from double-running analysis for the same
requested observation (Phase 2/18). This does NOT protect against two
SEPARATE processes racing on the same JSONL files -- this is a personal,
single-operator system with one scheduler process by design (see the
Sprint 5 final report's Known Limitations), not a multi-writer service;
true multi-process file locking is out of scope for this sprint.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from app.data.providers.exceptions import ProviderError
from app.data.providers.upstox_provider import UpstoxProvider
from app.domain.audit.models import AnalysisSnapshot, OutcomeCheckpoint, ReconciliationResult
from app.domain.audit.reconciliation import build_reconciliation
from app.domain.options.query_parser import parse_instrument_query
from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy
from app.orchestration.audit_journal import (
    build_analysis_snapshot,
    capture_outcome_checkpoint,
    due_checkpoints,
)
from app.orchestration.options_intelligence_pipeline import (
    PipelineConfig,
    Repositories,
    analyze_symbol,
)
from app.orchestration.options_intelligence_report import OptionsIntelligenceReport
from app.persistence.jsonl_file import JsonlAuditJournalRepository


class LiveAnalysisStatus:
    """Not an Enum -- these are orchestration-layer OUTCOME LABELS for a
    single `run_live_analysis()` call, distinct from `MarketDataState`
    (which the pipeline already owns) and from `CheckpointStatus` (which
    the audit journal already owns). Kept as plain string constants rather
    than a new domain Enum so this stays clearly an orchestration-layer
    concept.
    """

    PERSISTED = "PERSISTED"
    SKIPPED_NO_DECISION = "SKIPPED_NO_DECISION"
    INVALID_QUERY = "INVALID_QUERY"
    ERROR = "ERROR"


@dataclass(frozen=True)
class AnalysisOutcome:
    """What happened when this orchestration layer tried to run and
    persist one analysis for one query. `report`/`snapshot` are the real
    computed objects (never re-serialized/re-summarized) so a caller (CLI,
    dashboard) can render exactly what was actually produced.
    """

    status: str
    query: str
    symbol: str | None
    audit_id: str | None
    market_state: str | None
    error: str | None
    report: OptionsIntelligenceReport | None
    snapshot: AnalysisSnapshot | None


@dataclass(frozen=True)
class CheckpointRunOutcome:
    """What happened when this orchestration layer checked one audit_id
    for due checkpoints and attempted to capture them."""

    audit_id: str
    captured: list[OutcomeCheckpoint] = field(default_factory=list)
    reconciliation: ReconciliationResult | None = None
    error: str | None = None


class LiveOperationContext:
    """Bundles the real dependencies every live-operation function needs,
    plus the per-symbol lock registry (Phase 2/18: never overlapping runs
    for the same requested observation, within this one process).
    """

    def __init__(
        self,
        *,
        provider: UpstoxProvider,
        instrument_master: Sequence[dict[str, object]],
        strategy: EMAVWAPAlignmentStrategy,
        repositories: Repositories,
        journal: JsonlAuditJournalRepository,
        config: PipelineConfig,
        mcx_instrument_master: Sequence[dict[str, object]] | None = None,
    ) -> None:
        self.provider = provider
        self.instrument_master = instrument_master
        self.strategy = strategy
        self.repositories = repositories
        self.journal = journal
        self.config = config
        self.mcx_instrument_master = mcx_instrument_master
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock


async def run_live_analysis(query: str, *, ctx: LiveOperationContext, now: datetime) -> AnalysisOutcome:
    """Phase 1/2. Parses `query`, runs the real analysis (`analyze_symbol`
    -- no duplicate analysis engine), and persists an immutable
    `AnalysisSnapshot` when there is genuinely decision-worthy content.
    Never fabricates a snapshot for a failed or empty analysis --
    `SKIPPED_NO_DECISION`/`ERROR` are honest, first-class outcomes, not
    exceptions swallowed silently.
    """
    parsed = parse_instrument_query(query)
    if parsed.symbol is None or parsed.errors:
        detail = "; ".join(parsed.errors) if parsed.errors else "no symbol recognized in query"
        return AnalysisOutcome(
            status=LiveAnalysisStatus.INVALID_QUERY, query=query, symbol=None, audit_id=None,
            market_state=None, error=detail, report=None, snapshot=None,
        )

    lock = ctx._lock_for(parsed.symbol)
    async with lock:
        try:
            report = await analyze_symbol(
                parsed.symbol, provider=ctx.provider, instrument_master=ctx.instrument_master,
                strategy=ctx.strategy, repositories=ctx.repositories, as_of=now, config=ctx.config,
                mcx_instrument_master=ctx.mcx_instrument_master,
                requested_strike=parsed.strike if parsed.has_specific_contract else None,
                requested_right=parsed.right if parsed.has_specific_contract else None,
            )
        except ProviderError as exc:
            return AnalysisOutcome(
                status=LiveAnalysisStatus.ERROR, query=query, symbol=parsed.symbol, audit_id=None,
                market_state=None, error=str(exc), report=None, snapshot=None,
            )

        if report.error is not None:
            return AnalysisOutcome(
                status=LiveAnalysisStatus.ERROR, query=query, symbol=parsed.symbol, audit_id=None,
                market_state=report.data_state.value, error=report.error, report=report, snapshot=None,
            )

        if report.decision is None:
            # An honest, non-error empty case (e.g. no usable chain this
            # run) -- `build_analysis_snapshot()` itself raises ValueError
            # for exactly this case; this is the caller-side check that
            # contract requires, not a workaround of it.
            return AnalysisOutcome(
                status=LiveAnalysisStatus.SKIPPED_NO_DECISION, query=query, symbol=parsed.symbol, audit_id=None,
                market_state=report.data_state.value, error=None, report=report, snapshot=None,
            )

        snapshot = build_analysis_snapshot(report)
        await ctx.journal.save_analysis(snapshot)
        return AnalysisOutcome(
            status=LiveAnalysisStatus.PERSISTED, query=query, symbol=parsed.symbol, audit_id=snapshot.identity.audit_id,
            market_state=report.data_state.value, error=None, report=report, snapshot=snapshot,
        )


async def process_due_checkpoints(audit_id: str, *, ctx: LiveOperationContext, now: datetime) -> CheckpointRunOutcome:
    """Phases 4/5/10/11. Recomputes "what's due" fresh from the persisted
    journal every call (see module docstring's IDEMPOTENCY note) -- never
    caches a schedule in memory, so a restart or a duplicate tick is
    naturally safe: `due_checkpoints()` never returns a label already
    present in `captured`, and never returns a label whose real instant is
    still in the future (no-look-ahead by construction, reused unmodified
    from `app.orchestration.audit_journal`).
    """
    lock = ctx._lock_for(f"checkpoint:{audit_id}")
    async with lock:
        snapshot = await ctx.journal.get_analysis(audit_id)
        if snapshot is None:
            return CheckpointRunOutcome(audit_id=audit_id, error=f"no AnalysisSnapshot found for audit_id={audit_id}")

        existing = await ctx.journal.get_outcomes(audit_id)
        captured = {c.checkpoint_label for c in existing}
        due = due_checkpoints(snapshot.identity.generated_at, captured, now=now)

        newly_captured: list[OutcomeCheckpoint] = []
        for label in due:
            checkpoint = await capture_outcome_checkpoint(
                label, snapshot, now=now, provider=ctx.provider, instrument_master=ctx.instrument_master,
                strategy=ctx.strategy, repositories=ctx.repositories, config=ctx.config,
                mcx_instrument_master=ctx.mcx_instrument_master,
            )
            await ctx.journal.save_checkpoint(checkpoint)
            newly_captured.append(checkpoint)

        reconciliation: ReconciliationResult | None = None
        all_checkpoints = existing + newly_captured
        if all_checkpoints:
            reconciliation = build_reconciliation(
                snapshot, all_checkpoints, now=now, near_level_pct_threshold=ctx.config.near_level_pct_threshold
            )
            await ctx.journal.save_reconciliation(reconciliation)

        return CheckpointRunOutcome(audit_id=audit_id, captured=newly_captured, reconciliation=reconciliation)


async def run_controlled_observation_set(symbol: str, *, ctx: LiveOperationContext, now: datetime) -> list[AnalysisOutcome]:
    """Sprint 6, Phase 3 -- for a bare underlying query (e.g. "KAYNES"),
    resolves the LIVE relevant strike from the real option chain
    (`report.atm_strike`, the same ATM the pipeline itself already
    computes -- never a hardcoded guess that may not exist in the current
    chain) and runs CE and PE at that exact strike too. Exactly ONE fetch
    of the underlying's own analysis (via `run_live_analysis()`, which
    also persists it) -- the CE/PE follow-up queries reuse ITS
    `report.atm_strike` rather than re-fetching to "discover" the strike
    separately.

    A query that already names a specific contract (e.g. "KAYNES 4000
    CE") is not a bare-underlying query -- it is run as-is, unexpanded.
    If no ATM strike could be resolved this run (no chain available, or
    the underlying analysis itself failed), only the underlying's own
    outcome is returned -- never an invented strike.
    """
    parsed = parse_instrument_query(symbol)
    if parsed.symbol is None or parsed.errors or parsed.has_specific_contract:
        return [await run_live_analysis(symbol, ctx=ctx, now=now)]

    underlying_outcome = await run_live_analysis(symbol, ctx=ctx, now=now)
    if underlying_outcome.report is None or underlying_outcome.report.atm_strike is None:
        return [underlying_outcome]

    strike = underlying_outcome.report.atm_strike
    ce_outcome = await run_live_analysis(f"{parsed.symbol} {strike} CE", ctx=ctx, now=now)
    pe_outcome = await run_live_analysis(f"{parsed.symbol} {strike} PE", ctx=ctx, now=now)
    return [underlying_outcome, ce_outcome, pe_outcome]


async def sweep_due_checkpoints(symbol: str, *, ctx: LiveOperationContext, now: datetime) -> list[CheckpointRunOutcome]:
    """Phase 4 -- "restart must preserve prior snapshots/checkpoints and
    only attempt genuinely due unresolved ones": finds every unresolved
    (not yet finally-reconciled) `AnalysisSnapshot` for this symbol and
    processes due checkpoints for each. Symbol-scoped (not global) so one
    `run_live_analysis()` call for one query naturally catches up only its
    own prior observations, never touches another symbol's journal.
    """
    unresolved = await ctx.journal.query_unresolved()
    own = [s for s in unresolved if s.identity.symbol == symbol]
    return [await process_due_checkpoints(s.identity.audit_id, ctx=ctx, now=now) for s in own]
