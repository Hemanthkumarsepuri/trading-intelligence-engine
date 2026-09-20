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

import json
import re
import threading
from collections.abc import Iterator
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

    def iter_lines_containing(self, *required: str) -> Iterator[str]:
        """Stream this file's lines, yielding only those containing EVERY
        `required` raw substring.

        Final release gate (Section 7) -- a measured fix, not a guess.
        `JsonlOptionChainRepository.latest()` was taking **12.08 s per
        call** on the real 103 MB `option_chains.jsonl` (3019 snapshots):
        1.8 s to read the file and ~10.3 s to `model_validate_json()`
        every stored chain -- hundreds of option legs each -- purely to
        discard almost all of them on the very next comparison. The
        options pipeline calls `latest()` AND `query_range()` once per
        symbol, so a 30-symbol Stage 2 was paying ~24 s of pure
        repository cost per symbol. That, not the analysis itself, was
        the scan's whole runtime: one full analysis measured ~1 s in
        isolation and 20 symbols ran in 7.8 s wall against in-memory
        repositories.

        This CANNOT change any query's result. The substrings callers
        pass are the exact JSON-serialised header fields (e.g.
        `"underlying":"NSE_EQ|INE002A01018"`), so a line lacking one
        provably cannot satisfy the caller's own predicate. A substring
        that happens to also occur inside a record's payload only causes
        a FALSE POSITIVE -- that line is still fully validated and still
        re-checked by the caller's original, unchanged predicate. Only
        provably-irrelevant lines are skipped; nothing is ever accepted
        on the strength of a substring alone.

        Streaming (rather than `read_lines()`'s list) also keeps peak
        memory at one line instead of materialising the whole 103 MB
        file on every call.
        """
        if not self.path.exists():
            return
        with self.path.open(encoding="utf-8") as f:
            for raw in f:
                stripped = raw.strip()
                if stripped and all(token in stripped for token in required):
                    yield stripped


class _FieldTokenIndex:
    """Append-aware byte-offset index of ONE store's lines by every
    serialised `"<field>":"<json string>"` token each line contains.

    Release gate (Section 7) -- measured with cProfile on a real live scan:
    after the substring pre-filter, `JsonlOptionChainRepository.latest()`
    and `query_range()` still streamed the whole 110 MB
    `option_chains.jsonl` (~0.85 s CPU each) once per Stage-2 symbol --
    ~136 s of an 80-symbol scan, all of it synchronous and so stalling
    every concurrent provider call on the same event loop.

    Equivalence: a line is registered under EVERY distinct token of the
    field it contains (option legs repeat `"underlying"`, so this is a
    superset, never a subset). A candidate line is then re-read from disk
    by offset, must still contain every required substring, and is still
    fully validated and re-checked by the caller's ORIGINAL predicate. A
    line that lacks the token cannot contain it as a substring, so nothing
    a full scan would return can be missed.

    Freshness: the store is append-only. Each lookup stats the file; bytes
    appended since the last lookup (by this process or any other) are
    indexed before answering, and only complete newline-terminated lines
    are indexed, so a concurrently half-written line is picked up on the
    next call rather than mis-indexed. A file that shrank or whose leading
    bytes changed is re-indexed from scratch.
    """

    _HEAD_BYTES = 256

    def __init__(self, path: Path, field: str) -> None:
        self._path = path
        self._pattern = re.compile(rb'"' + re.escape(field.encode()) + rb'":("(?:[^"\\]|\\.)*")')
        self._offsets: dict[bytes, list[tuple[int, int]]] = {}
        self._indexed_to = 0
        self._head = b""
        self._lock = threading.Lock()

    def _reset(self) -> None:
        self._offsets = {}
        self._indexed_to = 0
        self._head = b""

    def _refresh(self) -> None:
        if not self._path.exists():
            self._reset()
            return
        size = self._path.stat().st_size
        with self._path.open("rb") as f:
            head = f.read(self._HEAD_BYTES)
            if size < self._indexed_to or head[: len(self._head)] != self._head:
                self._reset()
            if size == self._indexed_to:
                return
            f.seek(self._indexed_to)
            offset = self._indexed_to
            for raw in f:
                if not raw.endswith(b"\n"):
                    break  # a line still being written -- index it next call
                for token in set(self._pattern.findall(raw)):
                    self._offsets.setdefault(token, []).append((offset, len(raw)))
                offset += len(raw)
            self._indexed_to = offset
            self._head = head[: min(len(head), offset)]

    def lines_with(self, value: str, *required: str) -> Iterator[str]:
        token = json.dumps(value, ensure_ascii=False).encode("utf-8")
        with self._lock:
            self._refresh()
            spans = list(self._offsets.get(token, ()))
        if not spans:
            return
        with self._path.open("rb") as f:
            for offset, length in spans:
                f.seek(offset)
                stripped = f.read(length).decode("utf-8").strip()
                if stripped and all(part in stripped for part in required):
                    yield stripped


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
        found = await self.latest_for_ids(frozenset({instrument_id}), as_of=as_of)
        return found.get(instrument_id)

    async def latest_for_ids(self, instrument_ids: frozenset[str], *, as_of: datetime) -> dict[str, Quote]:
        """One pass over the quote file. Presentation-only helper — does not
        invent a quote that `latest()` would not also return."""
        if not instrument_ids:
            return {}
        latest: dict[str, Quote] = {}
        for line in self._store.read_lines():
            if not any(instrument_id in line for instrument_id in instrument_ids):
                continue
            quote = Quote.model_validate_json(line)
            if quote.instrument_id not in instrument_ids:
                continue
            if quote.freshness.data_timestamp > as_of:
                continue
            previous = latest.get(quote.instrument_id)
            if previous is None or quote.freshness.data_timestamp > previous.freshness.data_timestamp:
                latest[quote.instrument_id] = quote
        return latest


class JsonlOptionChainRepository:
    def __init__(self, path: Path) -> None:
        self._store = _JsonlStore(path)
        self._underlying_index = _FieldTokenIndex(path, "underlying")

    async def save(self, snapshot: OptionChainSnapshot) -> None:
        self._store.append_line(snapshot.model_dump_json())

    @staticmethod
    def _header_tokens(underlying: str, expiry: date) -> tuple[str, str]:
        """The exact JSON-serialised header fields every matching record
        must contain, used only to SKIP provably-irrelevant lines before
        the expensive full validation -- see
        `_JsonlStore.iter_lines_containing()` for why this cannot change
        a result. `json.dumps` (not an f-string) so any character needing
        JSON escaping is encoded exactly as `model_dump_json()` wrote
        it."""
        return f'"underlying":{json.dumps(underlying, ensure_ascii=False)}', f'"expiry":"{expiry.isoformat()}"'

    async def latest(self, *, underlying: str, expiry: date, as_of: datetime) -> OptionChainSnapshot | None:
        # Predicate below is UNCHANGED -- `iter_lines_containing()` only
        # skips lines that provably cannot satisfy it.
        candidates = [
            s
            for line in self._underlying_index.lines_with(underlying, *self._header_tokens(underlying, expiry))
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
            for line in self._underlying_index.lines_with(underlying, *self._header_tokens(underlying, expiry))
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
        self._analysis_indexes = {
            field: _FieldTokenIndex(directory / "analysis_snapshots.jsonl", field) for field in ("symbol", "audit_id")
        }

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

    def _analyses_containing(self, field: str, value: str) -> Iterator[AnalysisSnapshot]:
        """Validate only the snapshot lines that can possibly match
        `field == value`.

        Release gate (Section 7) -- measured on a real 80-symbol scan:
        `query_by_symbol()` cost **2.05 s per call, 164 s of a 297 s
        scan**, because `run_analysis()` calls it once per Stage-2 symbol
        (to find the prior snapshot for "what changed") and it validated
        all ~1,500 snapshots in the 29 MB journal every time. The work is
        synchronous, so it also stalled the event loop that every
        concurrent provider call shares.

        Same equivalence argument as `_JsonlStore.iter_lines_containing()`:
        the token is the exact serialised form (`json.dumps` with
        `ensure_ascii=False`, matching `model_dump_json()`'s raw UTF-8
        output), a line lacking it cannot match, and every candidate is
        still fully validated and re-checked by the caller's unchanged
        predicate -- a false positive costs time, never correctness."""
        # The substring pre-filter alone still streamed the whole journal
        # (0.40 s/call re-measured); the offset index reads only lines that
        # contain the serialised token -- see `_FieldTokenIndex`.
        token = f'"{field}":{json.dumps(value, ensure_ascii=False)}'
        for line in self._analysis_indexes[field].lines_with(value, token):
            yield AnalysisSnapshot.model_validate_json(line)

    async def get_analysis(self, audit_id: str) -> AnalysisSnapshot | None:
        return next((s for s in self._analyses_containing("audit_id", audit_id) if s.identity.audit_id == audit_id), None)

    async def get_outcomes(self, audit_id: str) -> list[OutcomeCheckpoint]:
        matches = [c for c in self._all_checkpoints() if c.audit_id == audit_id]
        return sorted(matches, key=lambda c: CHECKPOINT_ORDER[c.checkpoint_label])

    async def get_reconciliation(self, audit_id: str) -> ReconciliationResult | None:
        matches = [r for r in self._all_reconciliations() if r.audit_id == audit_id]
        if not matches:
            return None
        return max(matches, key=lambda r: r.computed_at)

    async def query_by_symbol(self, symbol: str) -> list[AnalysisSnapshot]:
        return [s for s in self._analyses_containing("symbol", symbol) if s.identity.symbol == symbol]

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
