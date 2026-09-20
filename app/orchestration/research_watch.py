"""Personal observation watch service — persistence + deterministic delta.

Never calls a broker. Never asks Qwen to invent T0 or a state change.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.research.watch_record import (
    ObservationSnapshot,
    WatchChange,
    WatchEvent,
    WatchEventKind,
    WatchRecord,
    compare_snapshots,
    new_watch_id,
    snapshot_from_analyze_payload,
    snapshot_from_screener_row,
)
from app.persistence.jsonl_file import JsonlWatchRecordRepository
from app.utils.time import utc_now


class DuplicateWatchError(ValueError):
    pass


class WatchUnavailableError(ValueError):
    pass


class WatchRecordView(BaseModel):
    watch_id: str
    symbol: str
    created_at: datetime
    query: str | None = None
    t0_unavailable: bool = False
    t0: ObservationSnapshot | None = None
    latest: ObservationSnapshot | None = None
    changes: list[WatchChange] = Field(default_factory=list)
    evolution_states: list[str] = Field(default_factory=list)
    outcome_checkpoint_labels: list[str] = Field(default_factory=list)


def view_from_record(record: WatchRecord, *, outcome_labels: list[str] | None = None) -> WatchRecordView:
    changes = compare_snapshots(record.t0, record.latest) if not record.t0_unavailable else []
    states: list[str] = []
    for snap in record.evolution:
        state = snap.research_state or "UNKNOWN"
        if not states or states[-1] != state:
            states.append(state)
    if record.t0 is None and record.t0_unavailable and not states:
        states = ["T0_OBSERVATION_UNAVAILABLE"]
    return WatchRecordView(
        watch_id=record.watch_id,
        symbol=record.symbol,
        created_at=record.created_at,
        query=record.query,
        t0_unavailable=record.t0_unavailable,
        t0=record.t0,
        latest=record.latest,
        changes=changes,
        evolution_states=states,
        outcome_checkpoint_labels=list(outcome_labels or []),
    )


class ResearchWatchService:
    def __init__(self, repository: JsonlWatchRecordRepository) -> None:
        self._repository = repository

    async def create(
        self,
        *,
        symbol: str,
        query: str | None,
        observation: ObservationSnapshot | None,
        t0_unavailable: bool,
        now: datetime | None = None,
    ) -> WatchRecordView:
        symbol_n = symbol.strip().upper()
        if not symbol_n:
            raise WatchUnavailableError("symbol is required")
        existing = await self._repository.active_for_symbol(symbol_n)
        if existing is not None:
            raise DuplicateWatchError(f"already watching {symbol_n}")
        if observation is None and not t0_unavailable:
            raise WatchUnavailableError("T0 OBSERVATION UNAVAILABLE")
        watch_id = new_watch_id()
        occurred = now or utc_now()
        t0 = None if t0_unavailable else observation
        event = WatchEvent(
            kind=WatchEventKind.CREATED,
            watch_id=watch_id,
            occurred_at=occurred,
            symbol=symbol_n,
            query=query,
            t0_unavailable=t0_unavailable,
            t0=t0,
            latest=t0,
        )
        await self._repository.append_event(event)
        record = await self._repository.get(watch_id)
        if record is None:
            raise WatchUnavailableError("watch was not persisted")
        return view_from_record(record)

    async def list_watches(self) -> list[WatchRecordView]:
        return [view_from_record(r) for r in await self._repository.list_active()]

    async def get(self, watch_id: str) -> WatchRecordView | None:
        record = await self._repository.get(watch_id)
        if record is None:
            return None
        return view_from_record(record)

    async def remove(self, watch_id: str, *, now: datetime | None = None) -> bool:
        record = await self._repository.get(watch_id)
        if record is None:
            return False
        await self._repository.append_event(
            WatchEvent(
                kind=WatchEventKind.REMOVED,
                watch_id=watch_id,
                occurred_at=now or utc_now(),
                symbol=record.symbol,
            )
        )
        return True

    async def update_latest(
        self,
        watch_id: str,
        observation: ObservationSnapshot,
        *,
        now: datetime | None = None,
    ) -> WatchRecordView:
        record = await self._repository.get(watch_id)
        if record is None:
            raise WatchUnavailableError("watch not found")
        t0 = record.t0
        if (
            t0 is not None
            and t0.observation_timestamp is not None
            and observation.observation_timestamp is not None
            and observation.observation_timestamp < t0.observation_timestamp
        ):
            return view_from_record(record)
        await self._repository.append_event(
            WatchEvent(
                kind=WatchEventKind.LATEST_UPDATED,
                watch_id=watch_id,
                occurred_at=now or utc_now(),
                symbol=record.symbol,
                latest=observation,
            )
        )
        updated = await self._repository.get(watch_id)
        if updated is None:
            raise WatchUnavailableError("watch not found after update")
        if t0 is not None and updated.t0 != t0:
            raise RuntimeError("T0 mutated — WatchRecord storage is corrupt")
        return view_from_record(updated)

    async def migrate_symbols(
        self,
        symbols: list[str],
        observations: dict[str, ObservationSnapshot],
        *,
        now: datetime | None = None,
    ) -> list[WatchRecordView]:
        views: list[WatchRecordView] = []
        for raw in symbols:
            symbol = raw.strip().upper()
            if not symbol:
                continue
            existing = await self._repository.active_for_symbol(symbol)
            if existing is not None:
                views.append(view_from_record(existing))
                continue
            obs = observations.get(symbol)
            views.append(
                await self.create(
                    symbol=symbol,
                    query=symbol,
                    observation=obs,
                    t0_unavailable=obs is None,
                    now=now,
                )
            )
        return views


def observation_from_client(payload: dict[str, object] | None, *, kind: str) -> ObservationSnapshot | None:
    if not payload:
        return None
    if kind == "analyze":
        return snapshot_from_analyze_payload(payload)
    if kind == "screener":
        return snapshot_from_screener_row(payload)
    try:
        return ObservationSnapshot.model_validate(payload)
    except ValueError:
        return None
