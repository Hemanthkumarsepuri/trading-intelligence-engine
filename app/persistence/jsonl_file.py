"""File-backed (JSON-Lines), durable implementations of
`persistence.interfaces` — the pragmatic middle ground between
`in_memory` (fast, but nothing survives a process restart) and the
eventual SQLAlchemy/Postgres backend (`interfaces.py`'s own docstring
calls that "a later milestone"). No Postgres server is actually reachable
in this environment (confirmed: `localhost:5432` refused a connection,
2026-08-29) and this milestone's IV-rank/OI-history requirements need real
observations that accumulate ACROSS separate process runs — something
`in_memory` structurally cannot provide. An append-only JSONL file per
repository is real, durable, inspectable with plain text tools, and
implements the exact same Protocols as `in_memory` — swapping this for
Postgres later is exactly the "infrastructure change behind the same
Protocol" `interfaces.py` already promises; no caller outside
`persistence/` needs to change.

Not designed for high write volume or concurrent multi-process writers —
each repository loads its whole file into memory on every read and appends
one line per `save()`. Adequate for this product's real write rate (one
option-chain snapshot per instrument per analysis run, not a tick-by-tick
firehose); revisit if that changes.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from app.domain.audit.models import (
    CHECKPOINT_ORDER,
    AnalysisSnapshot,
    OutcomeCheckpoint,
    ReconciliationResult,
)
from app.domain.audit.research_models import (
    RESEARCH_CHECKPOINT_SESSIONS_AHEAD,
    ResearchObservation,
    ResearchOutcomeCheckpoint,
    ResearchRunRecord,
)
from app.domain.ipo.audit_models import IPOAnalysisSnapshot, IPOListingOutcomeRecord
from app.domain.journal.personal_journal import PersonalJournalEntry, PersonalJournalOutcome
from app.domain.market.models import Candle, OptionChainSnapshot, Quote, Timeframe
from app.domain.options.models import IvObservation
from app.utils.time import to_ist


class _JsonlStore:
    """Shared append/read-all machinery for one JSONL file. Not a
    Protocol implementation itself — each repository below wraps one of
    these for its own record type.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append_line(self, line: str) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line)
            f.write("\n")

    def read_lines(self) -> list[str]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as f:
            return [stripped for raw in f if (stripped := raw.strip())]


class JsonlCandleRepository:
    def __init__(self, path: Path) -> None:
        self._store = _JsonlStore(path)

    async def save(self, candle: Candle) -> None:
        self._store.append_line(candle.model_dump_json())

    async def query(
        self, *, instrument_id: str, timeframe: Timeframe, start: datetime, end: datetime, as_of: datetime
    ) -> list[Candle]:
        results = []
        for line in self._store.read_lines():
            candle = Candle.model_validate_json(line)
            if (
                candle.instrument_id == instrument_id
                and candle.timeframe == timeframe
                and start <= candle.freshness.data_timestamp < end
                and candle.freshness.data_timestamp <= as_of
            ):
                results.append(candle)
        return sorted(results, key=lambda c: c.freshness.data_timestamp)


class JsonlQuoteRepository:
    def __init__(self, path: Path) -> None:
        self._store = _JsonlStore(path)

    async def save(self, quote: Quote) -> None:
        self._store.append_line(quote.model_dump_json())

    async def latest(self, *, instrument_id: str, as_of: datetime) -> Quote | None:
        candidates = [
            q
            for line in self._store.read_lines()
            if (q := Quote.model_validate_json(line)).instrument_id == instrument_id
            and q.freshness.data_timestamp <= as_of
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda q: q.freshness.data_timestamp)


class JsonlOptionChainRepository:
    def __init__(self, path: Path) -> None:
        self._store = _JsonlStore(path)

    async def save(self, snapshot: OptionChainSnapshot) -> None:
        self._store.append_line(snapshot.model_dump_json())

    async def latest(self, *, underlying: str, expiry: date, as_of: datetime) -> OptionChainSnapshot | None:
        candidates = [
            s
            for line in self._store.read_lines()
            if (s := OptionChainSnapshot.model_validate_json(line)).underlying == underlying
            and s.expiry == expiry
            and s.freshness.data_timestamp <= as_of
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda s: s.freshness.data_timestamp)

    async def query_range(
        self, *, underlying: str, expiry: date, start: datetime, end: datetime, as_of: datetime
    ) -> list[OptionChainSnapshot]:
        results = [
            s
            for line in self._store.read_lines()
            if (s := OptionChainSnapshot.model_validate_json(line)).underlying == underlying
            and s.expiry == expiry
            and start <= s.freshness.data_timestamp <= end
            and s.freshness.data_timestamp <= as_of
        ]
        return sorted(results, key=lambda s: s.freshness.data_timestamp)


class JsonlIvObservationRepository:
    def __init__(self, path: Path) -> None:
        self._store = _JsonlStore(path)

    async def save(self, observation: IvObservation) -> None:
        self._store.append_line(observation.model_dump_json())

    async def query_history(self, *, underlying: str, as_of: datetime, lookback: timedelta) -> list[IvObservation]:
        start = as_of - lookback
        results = [
            obs
            for line in self._store.read_lines()
            if (obs := IvObservation.model_validate_json(line)).underlying == underlying
            and start <= obs.freshness.data_timestamp <= as_of
        ]
        return sorted(results, key=lambda o: o.freshness.data_timestamp)


class JsonlAuditJournalRepository:
    """Milestone G's audit journal — three separate append-only JSONL
    files under one directory (`analysis_snapshots.jsonl`,
    `outcome_checkpoints.jsonl`, `reconciliation_results.jsonl`), per
    `interfaces.AuditJournalRepository`'s docstring: no update or delete
    method exists anywhere on this class.
    """

    def __init__(self, directory: Path) -> None:
        self._analyses = _JsonlStore(directory / "analysis_snapshots.jsonl")
        self._checkpoints = _JsonlStore(directory / "outcome_checkpoints.jsonl")
        self._reconciliations = _JsonlStore(directory / "reconciliation_results.jsonl")

    async def save_analysis(self, snapshot: AnalysisSnapshot) -> None:
        self._analyses.append_line(snapshot.model_dump_json())

    async def save_checkpoint(self, checkpoint: OutcomeCheckpoint) -> None:
        self._checkpoints.append_line(checkpoint.model_dump_json())

    async def save_reconciliation(self, result: ReconciliationResult) -> None:
        self._reconciliations.append_line(result.model_dump_json())

    def _all_analyses(self) -> list[AnalysisSnapshot]:
        return [AnalysisSnapshot.model_validate_json(line) for line in self._analyses.read_lines()]

    def _all_checkpoints(self) -> list[OutcomeCheckpoint]:
        return [OutcomeCheckpoint.model_validate_json(line) for line in self._checkpoints.read_lines()]

    def _all_reconciliations(self) -> list[ReconciliationResult]:
        return [ReconciliationResult.model_validate_json(line) for line in self._reconciliations.read_lines()]

    async def get_analysis(self, audit_id: str) -> AnalysisSnapshot | None:
        return next((s for s in self._all_analyses() if s.identity.audit_id == audit_id), None)

    async def get_outcomes(self, audit_id: str) -> list[OutcomeCheckpoint]:
        matches = [c for c in self._all_checkpoints() if c.audit_id == audit_id]
        return sorted(matches, key=lambda c: CHECKPOINT_ORDER[c.checkpoint_label])

    async def get_reconciliation(self, audit_id: str) -> ReconciliationResult | None:
        matches = [r for r in self._all_reconciliations() if r.audit_id == audit_id]
        if not matches:
            return None
        return max(matches, key=lambda r: r.computed_at)

    async def query_by_symbol(self, symbol: str) -> list[AnalysisSnapshot]:
        return [s for s in self._all_analyses() if s.identity.symbol == symbol]

    async def query_by_date_range(self, start: datetime, end: datetime) -> list[AnalysisSnapshot]:
        return [s for s in self._all_analyses() if start <= s.identity.generated_at <= end]

    async def query_by_decision(self, decision: str) -> list[AnalysisSnapshot]:
        return [s for s in self._all_analyses() if s.decision.decision == decision]

    async def query_by_bias(self, bias: str) -> list[AnalysisSnapshot]:
        return [s for s in self._all_analyses() if s.decision.final_bias == bias]

    async def query_unresolved(self) -> list[AnalysisSnapshot]:
        latest_by_audit_id: dict[str, ReconciliationResult] = {}
        for r in self._all_reconciliations():
            existing = latest_by_audit_id.get(r.audit_id)
            if existing is None or r.computed_at > existing.computed_at:
                latest_by_audit_id[r.audit_id] = r
        return [
            s
            for s in self._all_analyses()
            if (latest := latest_by_audit_id.get(s.identity.audit_id)) is None or not latest.is_final
        ]


class JsonlIPOAuditJournalRepository:
    """IPO audit journal (Phase 8) -- the exact same append-only,
    no-update-no-delete discipline as `JsonlAuditJournalRepository`, in
    its own directory/files so the two domains' persisted data never
    mixes."""

    def __init__(self, directory: Path) -> None:
        self._snapshots = _JsonlStore(directory / "ipo_analysis_snapshots.jsonl")
        self._outcomes = _JsonlStore(directory / "ipo_listing_outcomes.jsonl")

    async def save_snapshot(self, snapshot: IPOAnalysisSnapshot) -> None:
        self._snapshots.append_line(snapshot.model_dump_json())

    async def save_listing_outcome(self, outcome: IPOListingOutcomeRecord) -> None:
        self._outcomes.append_line(outcome.model_dump_json())

    def _all_snapshots(self) -> list[IPOAnalysisSnapshot]:
        return [IPOAnalysisSnapshot.model_validate_json(line) for line in self._snapshots.read_lines()]

    def _all_listing_outcomes(self) -> list[IPOListingOutcomeRecord]:
        return [IPOListingOutcomeRecord.model_validate_json(line) for line in self._outcomes.read_lines()]

    async def get_snapshot(self, audit_id: str) -> IPOAnalysisSnapshot | None:
        return next((s for s in self._all_snapshots() if s.audit_id == audit_id), None)

    async def get_listing_outcome(self, audit_id: str) -> IPOListingOutcomeRecord | None:
        matches = [o for o in self._all_listing_outcomes() if o.audit_id == audit_id]
        return max(matches, key=lambda o: o.recorded_at) if matches else None

    async def query_by_ipo_id(self, ipo_id: str) -> list[IPOAnalysisSnapshot]:
        return [s for s in self._all_snapshots() if s.identity is not None and s.identity.ipo_id == ipo_id]

    async def query_all_snapshots(self) -> list[IPOAnalysisSnapshot]:
        return self._all_snapshots()

    async def query_without_listing_outcome(self) -> list[IPOAnalysisSnapshot]:
        """Snapshots that could still be reconciled -- either the IPO
        genuinely hasn't listed yet, or it has but this journal hasn't
        recorded the outcome yet."""
        recorded_ids = {o.audit_id for o in self._all_listing_outcomes()}
        return [s for s in self._all_snapshots() if s.audit_id not in recorded_ids]


class JsonlResearchRunRepository:
    """Daily Market Researcher's own append-only journal (one
    `research_runs.jsonl` file) -- same no-update-no-delete discipline as
    every other repository in this file. Each `ResearchRunRecord` already
    cross-references the shortlisted symbols' own `audit_id`s in the
    normal `JsonlAuditJournalRepository` rather than duplicating their
    content, so this file stays small."""

    def __init__(self, directory: Path) -> None:
        self._store = _JsonlStore(directory / "research_runs.jsonl")

    async def save_run(self, record: ResearchRunRecord) -> None:
        self._store.append_line(record.model_dump_json())

    async def query_by_date(self, day: date) -> list[ResearchRunRecord]:
        matches = [
            r
            for line in self._store.read_lines()
            if to_ist((r := ResearchRunRecord.model_validate_json(line)).generated_at).date() == day
        ]
        return sorted(matches, key=lambda r: r.generated_at)


class JsonlResearchOutcomeRepository:
    """Sprint 3 -- the Daily Market Researcher's OUTCOME TRACKING journal:
    two separate append-only files (`research_observations.jsonl`,
    `research_outcome_checkpoints.jsonl`) under one directory, exactly the
    same "immutable record + separate later-observation record" split
    `JsonlAuditJournalRepository` already uses for the single-symbol
    live-operation checkpoint system -- never a second design."""

    def __init__(self, directory: Path) -> None:
        self._observations = _JsonlStore(directory / "research_observations.jsonl")
        self._checkpoints = _JsonlStore(directory / "research_outcome_checkpoints.jsonl")

    async def save_observation(self, observation: ResearchObservation) -> None:
        self._observations.append_line(observation.model_dump_json())

    async def save_checkpoint(self, checkpoint: ResearchOutcomeCheckpoint) -> None:
        self._checkpoints.append_line(checkpoint.model_dump_json())

    def _all_observations(self) -> list[ResearchObservation]:
        return [ResearchObservation.model_validate_json(line) for line in self._observations.read_lines()]

    def _all_checkpoints(self) -> list[ResearchOutcomeCheckpoint]:
        return [ResearchOutcomeCheckpoint.model_validate_json(line) for line in self._checkpoints.read_lines()]

    async def query_observations_by_date(self, day: date) -> list[ResearchObservation]:
        matches = [o for o in self._all_observations() if to_ist(o.generated_at).date() == day]
        return sorted(matches, key=lambda o: o.generated_at)

    async def query_all_observations(self) -> list[ResearchObservation]:
        return sorted(self._all_observations(), key=lambda o: o.generated_at)

    async def get_observation(self, observation_id: str) -> ResearchObservation | None:
        return next((o for o in self._all_observations() if o.observation_id == observation_id), None)

    async def query_checkpoints_for_observation(self, observation_id: str) -> list[ResearchOutcomeCheckpoint]:
        matches = [c for c in self._all_checkpoints() if c.observation_id == observation_id]
        return sorted(matches, key=lambda c: c.captured_at)

    async def query_observations_due_for_sweep(self, *, as_of: datetime) -> list[ResearchObservation]:
        captured_by_observation: dict[str, set[str]] = {}
        for c in self._all_checkpoints():
            captured_by_observation.setdefault(c.observation_id, set()).add(c.checkpoint_label.value)
        all_labels = {label.value for label in RESEARCH_CHECKPOINT_SESSIONS_AHEAD}
        return [
            o
            for o in self._all_observations()
            if o.generated_at <= as_of and captured_by_observation.get(o.observation_id, set()) != all_labels
        ]


class JsonlPersonalJournalRepository:
    """Operator research journal -- append-only entries and later
    outcomes. Never updates an entry in place. Never executes anything."""

    def __init__(self, directory: Path) -> None:
        self._entries = _JsonlStore(directory / "personal_journal_entries.jsonl")
        self._outcomes = _JsonlStore(directory / "personal_journal_outcomes.jsonl")

    async def save_entry(self, entry: PersonalJournalEntry) -> None:
        self._entries.append_line(entry.model_dump_json())

    async def save_outcome(self, outcome: PersonalJournalOutcome) -> None:
        self._outcomes.append_line(outcome.model_dump_json())

    async def query_entries(self, *, symbol: str | None = None) -> list[PersonalJournalEntry]:
        entries = [PersonalJournalEntry.model_validate_json(line) for line in self._entries.read_lines()]
        if symbol is not None:
            wanted = symbol.upper()
            entries = [e for e in entries if e.symbol.upper() == wanted]
        return sorted(entries, key=lambda e: e.recorded_at)

    async def query_outcomes(self, journal_id: str) -> list[PersonalJournalOutcome]:
        matches = [
            o
            for line in self._outcomes.read_lines()
            if (o := PersonalJournalOutcome.model_validate_json(line)).journal_id == journal_id
        ]
        return sorted(matches, key=lambda o: o.captured_at)
