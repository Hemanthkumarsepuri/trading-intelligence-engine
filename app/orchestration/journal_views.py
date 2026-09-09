"""Sprint 5, Phases 12/14 — read-only view builders over the audit journal
for the dashboard's "LIVE OUTCOME TRACKING" and "HISTORICAL OBSERVATIONS"
sections, plus the non-sensitive operational status view.

Every number here is a raw COUNT or a directly-observed value copied from
an already-persisted `AnalysisSnapshot`/`OutcomeCheckpoint`/
`ReconciliationResult` -- this module computes NO accuracy percentage, win
rate, or confidence score (Phase 12/18's explicit prohibition). Pending
vs. recorded vs. market-closed is a simple lookup of what actually exists
in the append-only journal, never inferred or estimated.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.domain.audit.models import (
    CHECKPOINT_ORDER,
    CePeOutcomeComparison,
    CheckpointLabel,
    CheckpointStatus,
)
from app.domain.audit.reconciliation import build_ce_pe_comparison, compute_journal_counters
from app.persistence.jsonl_file import JsonlAuditJournalRepository

# A real, wide-but-bounded range used only to enumerate "every persisted
# analysis" through the public `query_by_date_range()` Protocol method --
# no repository implementation exposes an unconditional "all records"
# accessor, and this module deliberately stays on the public Protocol
# rather than reaching into a repository's private internals.
_EPOCH_START = datetime(2000, 1, 1, tzinfo=UTC)
_EPOCH_END = datetime(2100, 1, 1, tzinfo=UTC)


class CheckpointStatusCounts(BaseModel):
    """Raw counts of checkpoint SLOTS across every unresolved and resolved
    analysis currently in the journal, bucketed by what actually happened
    to each of the five labels (5m/15m/30m/60m/EOD) -- PENDING means
    "not yet captured", nothing more."""

    pending: int
    recorded: int
    market_closed: int
    insufficient_data: int


class LastAnalysisSummary(BaseModel):
    audit_id: str
    symbol: str
    generated_at: datetime
    market_state: str
    decision: str


class ObservationCounts(BaseModel):
    """Sprint 6, Phase 11 -- raw counts of genuinely CAPTURED (status
    RECORDED) observations, split by what was observed. Never an
    accuracy/success rate; a count only. `underlying` counts RECORDED
    checkpoints with an `underlying` outcome present; `ce`/`pe` count
    RECORDED checkpoints with a `ce_option`/`pe_option` outcome present
    (Sprint 5's reference-strike tracking, independent of which side, if
    any, was originally requested)."""

    underlying: int
    ce: int
    pe: int


class JournalStatusView(BaseModel):
    """Phase 12/14 -- everything the dashboard's HISTORICAL OBSERVATIONS
    section and the operational status view need, and nothing else (no
    tokens, no credentials -- see Phase 14's explicit prohibition)."""

    analyses_count: int
    reconciliations_count: int
    unresolved_count: int
    invalidated_count: int
    confirmed_count: int
    checkpoints: CheckpointStatusCounts
    observations: ObservationCounts
    last_analysis: LastAnalysisSummary | None


async def build_journal_status(journal: JsonlAuditJournalRepository) -> JournalStatusView:
    analyses = await journal.query_by_date_range(_EPOCH_START, _EPOCH_END)
    reconciliations = [r for a in analyses if (r := await journal.get_reconciliation(a.identity.audit_id)) is not None]
    counters = compute_journal_counters(analyses, reconciliations)

    pending = recorded = market_closed = insufficient_data = 0
    underlying_obs = ce_obs = pe_obs = 0
    for a in analyses:
        outcomes = await journal.get_outcomes(a.identity.audit_id)
        captured_labels = {c.checkpoint_label for c in outcomes}
        pending += len(CheckpointLabel) - len(captured_labels)
        for c in outcomes:
            if c.status == CheckpointStatus.RECORDED:
                recorded += 1
                if c.underlying is not None:
                    underlying_obs += 1
                if c.ce_option is not None:
                    ce_obs += 1
                if c.pe_option is not None:
                    pe_obs += 1
            elif c.status == CheckpointStatus.MARKET_CLOSED:
                market_closed += 1
            else:
                insufficient_data += 1

    last_analysis = None
    if analyses:
        latest = max(analyses, key=lambda a: a.identity.generated_at)
        last_analysis = LastAnalysisSummary(
            audit_id=latest.identity.audit_id, symbol=latest.identity.symbol, generated_at=latest.identity.generated_at,
            market_state=latest.identity.market_state, decision=latest.decision.decision,
        )

    return JournalStatusView(
        analyses_count=counters.analyses_count, reconciliations_count=counters.outcomes_count,
        unresolved_count=counters.unresolved_count, invalidated_count=counters.invalidated_count,
        confirmed_count=counters.confirmed_count,
        checkpoints=CheckpointStatusCounts(pending=pending, recorded=recorded, market_closed=market_closed, insufficient_data=insufficient_data),
        observations=ObservationCounts(underlying=underlying_obs, ce=ce_obs, pe=pe_obs),
        last_analysis=last_analysis,
    )


class CheckpointView(BaseModel):
    label: str
    status: str  # "PENDING" | CheckpointStatus.value
    captured_at: datetime | None
    underlying_spot: str | None
    underlying_pct_change: str | None
    option_ltp: str | None
    option_pct_change: str | None
    detail: str | None


class CePeCheckpointView(BaseModel):
    """Sprint 6, Phase 9 -- the dashboard's side-by-side CE|PE table row
    for one checkpoint label. Mirrors `app.domain.audit.models.
    CePeCheckpointObservation` with string-encoded decimals (the same
    wire convention every other view in this module already uses)."""

    checkpoint_label: str
    status: str
    underlying_spot: str | None
    underlying_pct_change: str | None
    ce_price: str | None
    ce_pct_change: str | None
    pe_price: str | None
    pe_pct_change: str | None


class CePeComparisonView(BaseModel):
    """Sprint 6, Phase 8/9 -- the empirical CE-vs-PE comparison, ready for
    direct rendering. `ce_thesis_status`/`pe_thesis_status` are backend-
    computed labels (CONFIRMED/INVALIDATED/MIXED/INSUFFICIENT_DATA) -- the
    frontend never derives them itself (Phase 9's explicit requirement)."""

    reference_strike: str | None
    ce_initial_price: str | None
    pe_initial_price: str | None
    underlying_initial_spot: str | None
    ce_thesis_status: str | None
    pe_thesis_status: str | None
    checkpoints: list[CePeCheckpointView]


class NormalizedMovementPoint(BaseModel):
    """Sprint 6, Phase 10 -- one point on the "OBSERVED MOVEMENT -- NOT A
    PROFITABILITY FORECAST" chart. `*_index` is `100 + pct_change`
    (algebraically identical to `100 * current/initial`; see
    `_normalize()`) -- `None` when that leg's value was not recorded at
    this checkpoint, never interpolated or fabricated."""

    checkpoint_label: str
    underlying_index: str | None
    ce_index: str | None
    pe_index: str | None


class OutcomeTrackingView(BaseModel):
    """Phase 12 -- the dashboard's LIVE OUTCOME TRACKING section for one
    open analysis: one entry per checkpoint label, in schedule order,
    each PENDING until a real checkpoint is captured."""

    audit_id: str
    symbol: str
    checkpoints: list[CheckpointView]
    is_final: bool
    reconciliation_computed_at: datetime | None
    ce_pe: CePeComparisonView | None = None
    normalized_movement: list[NormalizedMovementPoint] = Field(default_factory=list)


_LABEL_ORDER = sorted(CheckpointLabel, key=lambda label: CHECKPOINT_ORDER[label])


def _normalize(pct_change: Decimal | None) -> str | None:
    """`100 * (current / initial) == 100 + pct_change` exactly, since
    `pct_change = (current - initial) / initial * 100` by construction
    (see every `*_pct_change` field this whole journal already computes)
    -- this is a restatement of an already-computed value, not new
    financial math."""
    if pct_change is None:
        return None
    return str(Decimal(100) + pct_change)


def _build_ce_pe_view(comparison: CePeOutcomeComparison) -> CePeComparisonView:
    return CePeComparisonView(
        reference_strike=str(comparison.reference_strike) if comparison.reference_strike is not None else None,
        ce_initial_price=str(comparison.ce_initial_price) if comparison.ce_initial_price is not None else None,
        pe_initial_price=str(comparison.pe_initial_price) if comparison.pe_initial_price is not None else None,
        underlying_initial_spot=str(comparison.underlying_initial_spot) if comparison.underlying_initial_spot is not None else None,
        ce_thesis_status=comparison.ce_thesis_status.value if comparison.ce_thesis_status is not None else None,
        pe_thesis_status=comparison.pe_thesis_status.value if comparison.pe_thesis_status is not None else None,
        checkpoints=[
            CePeCheckpointView(
                checkpoint_label=o.checkpoint_label, status=o.status,
                underlying_spot=str(o.underlying_spot) if o.underlying_spot is not None else None,
                underlying_pct_change=str(o.underlying_pct_change) if o.underlying_pct_change is not None else None,
                ce_price=str(o.ce_price) if o.ce_price is not None else None,
                ce_pct_change=str(o.ce_pct_change) if o.ce_pct_change is not None else None,
                pe_price=str(o.pe_price) if o.pe_price is not None else None,
                pe_pct_change=str(o.pe_pct_change) if o.pe_pct_change is not None else None,
            )
            for o in comparison.observations
        ],
    )


def build_normalized_movement(comparison: CePeOutcomeComparison) -> list[NormalizedMovementPoint]:
    """Sprint 6, Phase 10 -- pure transformation, no I/O, unit-testable on
    its own (see `tests/unit/orchestration/test_journal_views.py`)."""
    return [
        NormalizedMovementPoint(
            checkpoint_label=o.checkpoint_label,
            underlying_index=_normalize(o.underlying_pct_change),
            ce_index=_normalize(o.ce_pct_change),
            pe_index=_normalize(o.pe_pct_change),
        )
        for o in comparison.observations
    ]


async def build_outcome_tracking(journal: JsonlAuditJournalRepository, audit_id: str) -> OutcomeTrackingView | None:
    snapshot = await journal.get_analysis(audit_id)
    if snapshot is None:
        return None

    outcomes = await journal.get_outcomes(audit_id)
    by_label = {c.checkpoint_label: c for c in outcomes}
    views: list[CheckpointView] = []
    for label in _LABEL_ORDER:
        checkpoint = by_label.get(label)
        if checkpoint is None:
            views.append(CheckpointView(
                label=label.value, status="PENDING", captured_at=None, underlying_spot=None,
                underlying_pct_change=None, option_ltp=None, option_pct_change=None, detail=None,
            ))
            continue
        u = checkpoint.underlying
        o = checkpoint.option
        views.append(CheckpointView(
            label=label.value, status=checkpoint.status.value, captured_at=checkpoint.captured_at,
            underlying_spot=str(u.spot) if u is not None and u.spot is not None else None,
            underlying_pct_change=str(u.pct_change) if u is not None and u.pct_change is not None else None,
            option_ltp=str(o.option_ltp) if o is not None and o.option_ltp is not None else None,
            option_pct_change=str(o.option_pct_change) if o is not None and o.option_pct_change is not None else None,
            detail=checkpoint.detail,
        ))

    reconciliation = await journal.get_reconciliation(audit_id)
    # Sprint 7 bug fix -- previously this only showed the CE/PE table
    # after the FIRST reconciliation existed, hiding the "Initial LTP"
    # row (already known at analysis time, from `snapshot.contracts`)
    # for however long until the first checkpoint sweep happened to run.
    # Falls back to computing the comparison directly from the snapshot
    # + whatever checkpoints exist so far (possibly zero -- every label
    # then shows PENDING, exactly like `build_ce_pe_comparison()` already
    # handles) rather than requiring a persisted `ReconciliationResult`.
    ce_pe_comparison = reconciliation.ce_pe_comparison if reconciliation is not None else None
    if ce_pe_comparison is None:
        ce_pe_comparison = build_ce_pe_comparison(snapshot, outcomes)
    ce_pe_view = _build_ce_pe_view(ce_pe_comparison) if ce_pe_comparison is not None else None
    normalized = build_normalized_movement(ce_pe_comparison) if ce_pe_comparison is not None else []

    return OutcomeTrackingView(
        audit_id=audit_id, symbol=snapshot.identity.symbol, checkpoints=views,
        is_final=reconciliation.is_final if reconciliation is not None else False,
        reconciliation_computed_at=reconciliation.computed_at if reconciliation is not None else None,
        ce_pe=ce_pe_view, normalized_movement=normalized,
    )
