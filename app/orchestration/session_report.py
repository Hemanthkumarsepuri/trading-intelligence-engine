"""Real-NSE-session validation harness (Master Grooming Sprint, Phase 3).

READ-ONLY over the already-existing, already-persisted audit journal --
this module writes nothing, schedules nothing, and captures no data of
its own. It exists only to answer, in one human-readable report, the
question a reviewer needs answered after a real trading session:
"what did the system actually capture, when, and how does that compare
to what was scheduled?"

Every field is either copied verbatim from a persisted `AnalysisSnapshot`/
`OutcomeCheckpoint`, or a deterministic recomputation of an EXISTING
scheduling rule (`CHECKPOINT_OFFSETS`, `eod_deadline()`) already used
elsewhere in this codebase -- no new scheduling logic, no new persistence
format, no fabricated checkpoint. A checkpoint this module has never seen
persisted is always reported PENDING, never guessed as RECORDED.

Two things this module deliberately does NOT attempt to verify from
journal data alone, because they cannot honestly be inferred from it:
- RESTART SAFETY -- proving this requires an operator to actually
  restart the live-operation process mid-session (see the procedure in
  this module's `RESTART_VALIDATION_PROCEDURE`) and observe that no
  duplicate/lost checkpoint results. A clean journal is consistent with
  restart safety but does not prove it happened.
- NO-LOOK-AHEAD -- already covered by `tests/safety/test_no_lookahead.py`
  and `test_snapshot_structure_no_lookahead.py`; this is a structural
  code guarantee, not something a session report can newly demonstrate.
Both are named explicitly in the rendered report as "see manual
procedure" / "see test suite", never silently marked verified.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from app.domain.audit.models import CHECKPOINT_OFFSETS, CheckpointLabel, CheckpointStatus
from app.orchestration.audit_journal import eod_deadline
from app.persistence.jsonl_file import JsonlAuditJournalRepository
from app.utils.time import to_ist

RESTART_VALIDATION_PROCEDURE = (
    "1. Start `python -m scripts.run_live_analysis --repeat 300 <symbols>` before market open. "
    "2. Let it capture at least one real checkpoint (confirm via this report). "
    "3. Stop the process (Ctrl+C) after a checkpoint is captured but before the next one is due. "
    "4. Restart the exact same command. "
    "5. Re-run this report: the audit_id/snapshot from step 2 must be byte-identical (compare "
    "`analysis_snapshots.jsonl` before/after by hash), and the next checkpoint capture must not "
    "duplicate the one from step 2 (see `duplicate_checkpoint_labels` below, which must stay empty)."
)


@dataclass(frozen=True)
class CheckpointRow:
    label: str
    expected_time: datetime | None
    actual_time: datetime | None
    status: str  # CheckpointStatus.value, or "PENDING" if never captured
    data_source: str  # "Upstox (live)" when RECORDED, else "n/a"
    error: str | None


@dataclass(frozen=True)
class SymbolSession:
    symbol: str
    audit_id: str
    generated_at: datetime
    market_state: str
    checkpoints: list[CheckpointRow] = field(default_factory=list)
    # Labels appearing MORE THAN ONCE in the real persisted checkpoints for
    # this audit_id -- always expected empty; a non-empty list here is a
    # genuine duplicate-write defect, not a formatting artifact.
    duplicate_checkpoint_labels: list[str] = field(default_factory=list)
    ce_pe_symmetry_holds: bool | None = None  # None when reference_strike was never established (bare underlying query)


@dataclass(frozen=True)
class SessionValidationReport:
    session_date: date
    is_trading_day: bool
    symbols: list[SymbolSession] = field(default_factory=list)
    report_generated_at: datetime | None = None


_CHECKPOINT_ORDER = [CheckpointLabel.FIVE_MIN, CheckpointLabel.FIFTEEN_MIN, CheckpointLabel.THIRTY_MIN, CheckpointLabel.SIXTY_MIN, CheckpointLabel.EOD]


async def build_session_report(
    *, journal: JsonlAuditJournalRepository, symbols: list[str], session_date: date, is_trading_day: bool, now: datetime
) -> SessionValidationReport:
    """Reads real data for every `symbol` whose `AnalysisSnapshot.identity.
    generated_at`, converted to IST, falls on `session_date` -- never a
    different day's snapshot, and never more than one real report per
    symbol per day is silently merged (`SymbolSession` is per audit_id;
    a symbol analyzed twice in one day yields two `SymbolSession`
    entries, both real)."""
    symbol_sessions: list[SymbolSession] = []
    for symbol in symbols:
        snapshots = await journal.query_by_symbol(symbol)
        day_snapshots = [s for s in snapshots if to_ist(s.identity.generated_at).date() == session_date]
        for snapshot in sorted(day_snapshots, key=lambda s: s.identity.generated_at):
            outcomes = await journal.get_outcomes(snapshot.identity.audit_id)
            labels_seen = [o.checkpoint_label for o in outcomes]
            duplicate_labels = sorted({label.value for label in labels_seen if labels_seen.count(label) > 1})

            rows: list[CheckpointRow] = []
            for label in _CHECKPOINT_ORDER:
                offset = CHECKPOINT_OFFSETS.get(label)
                expected = snapshot.identity.generated_at + offset if offset is not None else eod_deadline(snapshot.identity.generated_at)
                matching = [o for o in outcomes if o.checkpoint_label == label]
                if not matching:
                    rows.append(CheckpointRow(label=label.value, expected_time=expected, actual_time=None, status="PENDING", data_source="n/a", error=None))
                    continue
                outcome = matching[0]  # first real capture; duplicates already surfaced via duplicate_checkpoint_labels
                rows.append(CheckpointRow(
                    label=label.value, expected_time=expected, actual_time=outcome.captured_at,
                    status=outcome.status.value,
                    data_source="Upstox (live)" if outcome.status == CheckpointStatus.RECORDED else "n/a",
                    error=(outcome.detail or None) if outcome.status != CheckpointStatus.RECORDED else None,
                ))

            symmetry: bool | None = None
            if snapshot.contracts.reference_strike is not None:
                recorded = [o for o in outcomes if o.status == CheckpointStatus.RECORDED]
                if recorded:
                    symmetry = all((o.ce_option is not None) == (o.pe_option is not None) for o in recorded)

            symbol_sessions.append(SymbolSession(
                symbol=symbol, audit_id=snapshot.identity.audit_id, generated_at=snapshot.identity.generated_at,
                market_state=snapshot.identity.market_state, checkpoints=rows,
                duplicate_checkpoint_labels=duplicate_labels, ce_pe_symmetry_holds=symmetry,
            ))

    return SessionValidationReport(session_date=session_date, is_trading_day=is_trading_day, symbols=symbol_sessions, report_generated_at=now)


def render_session_report(report: SessionValidationReport) -> str:
    lines: list[str] = []
    add = lines.append
    add("REAL NSE SESSION VALIDATION")
    add("-" * 28)
    add(f"Date: {report.session_date.isoformat()}")
    add(f"Trading day: {report.is_trading_day}")
    add(f"Report generated at (UTC): {report.report_generated_at.isoformat() if report.report_generated_at else 'n/a'}")
    add(f"Symbols with a real analysis this day: {[s.symbol for s in report.symbols]}")
    add("")

    if not report.symbols:
        add("No real analysis was persisted for any requested symbol on this date -- nothing to validate yet.")
        add("Overall session status: NO DATA")
        return "\n".join(lines)

    total_recorded = total_pending = total_market_closed = total_insufficient = 0
    total_duplicates = 0

    for s in report.symbols:
        add(f"== {s.symbol}  (audit_id={s.audit_id}, generated_at={s.generated_at.isoformat()}, market_state={s.market_state}) ==")
        for row in s.checkpoints:
            expected = row.expected_time.isoformat() if row.expected_time else "n/a"
            actual = row.actual_time.isoformat() if row.actual_time else "n/a"
            add(f"  {row.label:>4}: expected={expected}  actual={actual}  status={row.status}  source={row.data_source}" + (f"  error={row.error}" if row.error else ""))
            if row.status == CheckpointStatus.RECORDED.value:
                total_recorded += 1
            elif row.status == "PENDING":
                total_pending += 1
            elif row.status == CheckpointStatus.MARKET_CLOSED.value:
                total_market_closed += 1
            else:
                total_insufficient += 1
        if s.duplicate_checkpoint_labels:
            add(f"  DUPLICATE CHECKPOINTS DETECTED: {s.duplicate_checkpoint_labels}  <-- genuine defect, investigate")
            total_duplicates += len(s.duplicate_checkpoint_labels)
        add(f"  CE/PE symmetry holds: {s.ce_pe_symmetry_holds if s.ce_pe_symmetry_holds is not None else 'n/a (no reference strike / no recorded checkpoint yet)'}")
        add("")

    total_checkpoints = sum(len(s.checkpoints) for s in report.symbols)

    add(f"Successful checkpoints (RECORDED): {total_recorded}")
    add(f"Pending checkpoints: {total_pending}")
    add(f"Market-closed checkpoints: {total_market_closed}")
    add(f"Insufficient-data checkpoints: {total_insufficient}")
    add(f"Duplicate checkpoints detected: {total_duplicates}")
    add(f"Persistence verified: {'YES -- real snapshots exist in the journal' if report.symbols else 'NO'}")
    add(f"Restart verified: NOT DERIVABLE FROM JOURNAL DATA ALONE -- see manual procedure:\n    {RESTART_VALIDATION_PROCEDURE}")
    add("No-look-ahead verified: see tests/safety/test_no_lookahead.py and test_snapshot_structure_no_lookahead.py (structural, not re-verified by this report)")
    add("")
    # Classification (Master Grooming Sprint, Phase 6): a session with the
    # process running but ZERO genuine RECORDED checkpoints is never
    # "successful" evidence, and a session with only some checkpoints
    # RECORDED is PARTIAL, never inflated to the same label as a session
    # where every scheduled checkpoint for every symbol was genuinely
    # captured. FULL/PARTIAL/FAILED are only ever assigned once every
    # checkpoint has been resolved (nothing PENDING) -- a still-running
    # session is honestly INCOMPLETE, not prematurely judged.
    if total_duplicates > 0:
        classification = "DEFECT DETECTED (duplicate checkpoints) -- investigate before trusting this session's data"
    elif total_pending > 0:
        classification = "INCOMPLETE -- session still has pending checkpoints not yet due"
    elif total_checkpoints == 0:
        classification = "NO DATA"
    elif total_market_closed == total_checkpoints:
        classification = "MARKET_CLOSED -- entire session's market was closed, no live checkpoints possible, not a defect"
    elif total_recorded == total_checkpoints:
        classification = "FULL -- every scheduled checkpoint for every symbol was genuinely RECORDED"
    elif total_recorded > 0:
        classification = f"PARTIAL -- {total_recorded}/{total_checkpoints} checkpoints genuinely RECORDED, the rest MARKET_CLOSED/INSUFFICIENT_DATA -- do not treat as full evidence"
    else:
        classification = "FAILED -- session ran, checkpoints resolved, but zero genuine RECORDED observations (see insufficient-data/error detail above)"
    add(f"Overall session status: {classification}")

    return "\n".join(lines)
