"""Repository interfaces for the persistence layer.

Concrete SQLAlchemy/Postgres implementations are a later milestone
(ARCHITECTURE.md §7); this milestone defines the interface contract plus an
in-memory implementation (`in_memory.py`) used by tests and by
`data.providers.historical_provider.HistoricalProvider`.

Every read method takes `as_of` as a REQUIRED keyword argument, not an
optional one — this is the enforcement point for "no future data leakage"
(Addendum A5): a future call site cannot accidentally omit it and silently
see beyond the requested instant. Implementations MUST filter out any record
with `data_timestamp > as_of`, unconditionally.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Protocol

from app.domain.audit.models import AnalysisSnapshot, OutcomeCheckpoint, ReconciliationResult
from app.domain.audit.research_models import (
    ResearchObservation,
    ResearchOutcomeCheckpoint,
    ResearchRunRecord,
)
from app.domain.market.models import Candle, OptionChainSnapshot, Quote, Timeframe
from app.domain.options.models import IvObservation


class CandleRepository(Protocol):
    async def save(self, candle: Candle) -> None: ...

    async def query(
        self,
        *,
        instrument_id: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
        as_of: datetime,
    ) -> list[Candle]:
        """Candles with `start <= data_timestamp < end` AND `data_timestamp <= as_of`,
        ordered by `data_timestamp` ascending.
        """
        ...


class QuoteRepository(Protocol):
    async def save(self, quote: Quote) -> None: ...

    async def latest(self, *, instrument_id: str, as_of: datetime) -> Quote | None:
        """Most recent quote with `data_timestamp <= as_of`, or None."""
        ...


class OptionChainRepository(Protocol):
    async def save(self, snapshot: OptionChainSnapshot) -> None: ...

    async def latest(self, *, underlying: str, expiry: date, as_of: datetime) -> OptionChainSnapshot | None:
        """Most recent snapshot with `data_timestamp <= as_of`, or None."""
        ...

    async def query_range(
        self, *, underlying: str, expiry: date, start: datetime, end: datetime, as_of: datetime
    ) -> list[OptionChainSnapshot]:
        """Every snapshot with `start <= data_timestamp <= end` AND
        `data_timestamp <= as_of`, ordered ascending by `data_timestamp` —
        the multi-snapshot query the temporal evidence engine
        (`app.domain.options.temporal_evidence`) needs to compute real
        change-over-interval (1min/5min/15min-class) evidence, as opposed
        to `latest()`'s single most-recent-snapshot answer. `end` is
        inclusive (unlike `CandleRepository.query()`'s half-open `end`)
        because option-chain snapshots are irregular, on-demand fetches,
        not fixed-period bars — there is no adjacent "next bucket" for an
        inclusive boundary to collide with.
        """
        ...


class IvObservationRepository(Protocol):
    """Persisted ATM-IV history — the prerequisite for a genuine IV rank
    (see `app.domain.options.iv_context`). Never used to answer "what is IV
    right now" (the live chain answers that); only "what has ATM IV looked
    like historically."
    """

    async def save(self, observation: IvObservation) -> None: ...

    async def query_history(
        self, *, underlying: str, as_of: datetime, lookback: timedelta
    ) -> list[IvObservation]:
        """Observations for `underlying` with
        `as_of - lookback <= data_timestamp <= as_of`, ordered ascending by
        `data_timestamp`. Never includes anything beyond `as_of`.
        """
        ...


class AuditJournalRepository(Protocol):
    """Milestone G's post-analysis audit journal (see
    `app.domain.audit.models` for the full immutability rule). `save_*`
    methods are the ONLY way to write — there is deliberately no update or
    delete method anywhere on this Protocol, which is the structural
    enforcement of "never mutate an existing audit record" (a correction
    is just another `save_analysis()` call with `corrects_audit_id` set).

    Unlike the read methods above, these query methods do NOT take
    `as_of` — this journal is backward-looking bookkeeping consumed by a
    human reviewing history, not a live read that could leak into a
    decision (the no-look-ahead property here is instead enforced at
    RECORD-CREATION time: a checkpoint can only ever be captured once real
    wall-clock time has actually reached it — see
    `app.orchestration.audit_journal`).
    """

    async def save_analysis(self, snapshot: AnalysisSnapshot) -> None: ...

    async def save_checkpoint(self, checkpoint: OutcomeCheckpoint) -> None: ...

    async def save_reconciliation(self, result: ReconciliationResult) -> None: ...

    async def get_analysis(self, audit_id: str) -> AnalysisSnapshot | None:
        """The FIRST-ever-saved record for this `audit_id` — guarantees the
        original is what comes back even if a bug somewhere ever appended
        a duplicate."""
        ...

    async def get_outcomes(self, audit_id: str) -> list[OutcomeCheckpoint]:
        """All checkpoints for this `audit_id`, ordered by
        `checkpoint_label` (5m, 15m, 30m, 60m, EOD)."""
        ...

    async def get_reconciliation(self, audit_id: str) -> ReconciliationResult | None:
        """The MOST RECENTLY computed reconciliation for this `audit_id`
        (reconciliation is legitimately recomputed and re-appended as more
        checkpoints arrive — see `ReconciliationResult`'s own docstring)."""
        ...

    async def query_by_symbol(self, symbol: str) -> list[AnalysisSnapshot]: ...

    async def query_by_date_range(self, start: datetime, end: datetime) -> list[AnalysisSnapshot]:
        """Analyses with `start <= generated_at <= end`."""
        ...

    async def query_by_decision(self, decision: str) -> list[AnalysisSnapshot]:
        """Analyses whose `decision.decision` equals this `FinalDecision`
        value (e.g. `"TRADEABLE"`)."""
        ...

    async def query_by_bias(self, bias: str) -> list[AnalysisSnapshot]:
        """Analyses whose `decision.final_bias` equals this
        `EvidenceDirection` value (e.g. `"BULLISH"`)."""
        ...

    async def query_unresolved(self) -> list[AnalysisSnapshot]:
        """Analyses with no reconciliation yet, or whose latest
        reconciliation has `is_final=False` (the EOD checkpoint has not
        yet been incorporated)."""
        ...


class ResearchRunRepository(Protocol):
    """Daily Market Researcher's own append-only journal
    (`app.domain.audit.research_models.ResearchRunRecord`) -- a separate
    Protocol from `AuditJournalRepository` because it stores a different
    record type in its own file, exactly like `IvObservationRepository`
    is separate from `QuoteRepository`. No update or delete method here
    either -- same immutability discipline."""

    async def save_run(self, record: ResearchRunRecord) -> None: ...

    async def query_by_date(self, day: date) -> list[ResearchRunRecord]:
        """Every run whose `generated_at` (IST calendar date) equals
        `day`, ordered ascending by `generated_at`."""
        ...


class ResearchOutcomeRepository(Protocol):
    """Sprint 3 -- the Daily Market Researcher's OUTCOME TRACKING journal:
    a `ResearchObservation` (immutable, written once per shortlisted
    candidate) plus zero or more later `ResearchOutcomeCheckpoint`s for
    it. Same no-update-no-delete discipline as every other repository
    here -- a `ResearchObservation` is never edited after the fact."""

    async def save_observation(self, observation: ResearchObservation) -> None: ...

    async def save_observation_once(self, observation: ResearchObservation) -> bool:
        """Sprint 3.3 -- append `observation` only if no record with the same
        `observation_id` is already persisted; returns whether it was
        written. Never overwrites: the first persisted T0 record is final,
        and a second attempt (retry, refresh, restart) is a no-op."""
        ...

    async def save_checkpoint(self, checkpoint: ResearchOutcomeCheckpoint) -> None: ...

    async def save_checkpoint_once(self, checkpoint: ResearchOutcomeCheckpoint) -> bool:
        """Sprint 3.4 -- append `checkpoint` only if none exists for the same
        (`observation_id`, `checkpoint_label`); returns whether it was written.
        Never overwrites: the first persisted checkpoint for a horizon is
        final, so a retry, restart or overlapping sweep cannot duplicate it."""
        ...

    async def query_observations_by_date(self, day: date) -> list[ResearchObservation]:
        """Every observation whose `generated_at` (IST calendar date)
        equals `day`, ordered ascending."""
        ...

    async def query_all_observations(self) -> list[ResearchObservation]:
        """Sprint 5, Objective 4 -- every real persisted observation,
        ordered ascending by `generated_at`, with no date filter. The
        history view's own lightweight symbol/date/direction/outcome-
        status filters are applied by the caller in Python over this
        list (see `app.orchestration.research_outcome.build_research_history()`)
        -- never a second repository method per filter."""
        ...

    async def get_observation(self, observation_id: str) -> ResearchObservation | None:
        """The one real observation with this id, or `None` -- never a
        guess."""
        ...

    async def count_malformed_lines(self) -> int:
        """Sprint 3.6 -- persisted lines that do not parse (e.g. a write interrupted by a process
        kill). Such lines are skipped by every read; this makes them visible."""
        ...

    async def query_all_checkpoints(self) -> list[ResearchOutcomeCheckpoint]:
        """Sprint 3.5 -- every persisted checkpoint, in file order (read-only;
        used for sweep observability, never to decide due-ness)."""
        ...

    async def query_checkpoints_for_observation(self, observation_id: str) -> list[ResearchOutcomeCheckpoint]:
        """Every checkpoint captured so far for one observation, ordered
        ascending by `captured_at`."""
        ...

    async def query_observations_due_for_sweep(self, *, as_of: datetime) -> list[ResearchObservation]:
        """Every real observation that could still have at least one
        checkpoint captured as of `as_of` -- i.e. not yet holding all
        three labels. Never a guess about which symbols "should" be
        interesting; purely "has this observation's checkpoint set
        genuinely not been completed yet"."""
        ...
