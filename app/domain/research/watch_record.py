"""Observation-backed personal research memory.

A WatchRecord freezes T0 (what TIRE knew when the operator pinned an
observation) and keeps CURRENT separate. T0 is never overwritten. This is
not a score, not a position, and not an order.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def new_watch_id() -> str:
    return uuid4().hex


def new_event_id() -> str:
    return uuid4().hex


class WatchEventKind(str, Enum):
    CREATED = "CREATED"
    LATEST_UPDATED = "LATEST_UPDATED"
    REMOVED = "REMOVED"


class WatchChangeCategory(str, Enum):
    NO_MATERIAL_CHANGE = "NO_MATERIAL_CHANGE"
    STATE_CHANGED = "STATE_CHANGED"
    PATTERN_CHANGED = "PATTERN_CHANGED"
    DIRECTION_CHANGED = "DIRECTION_CHANGED"
    CONTRACT_CHANGED = "CONTRACT_CHANGED"
    CONTRACT_DEGRADED = "CONTRACT_DEGRADED"
    FRESHNESS_DEGRADED = "FRESHNESS_DEGRADED"
    EVIDENCE_CHANGED = "EVIDENCE_CHANGED"
    CONFIRMATION_APPEARED = "CONFIRMATION_APPEARED"
    CONFLICT_APPEARED = "CONFLICT_APPEARED"
    INVALIDATION_CONDITION_REACHED = "INVALIDATION_CONDITION_REACHED"
    EXTENDED = "EXTENDED"
    DATA_BECAME_INSUFFICIENT = "DATA_BECAME_INSUFFICIENT"
    TIMING_CHANGED = "TIMING_CHANGED"
    MISSING_CHANGED = "MISSING_CHANGED"
    CONDITION_TEXT_CHANGED = "CONDITION_TEXT_CHANGED"


class ObservationSnapshot(BaseModel):
    """Fields TIRE actually observes. Missing values stay None — never invented."""

    model_config = {"frozen": True}

    observation_id: str | None = None
    observation_timestamp: datetime | None = None
    generated_at: datetime | None = None
    query: str | None = None
    research_state: str | None = None
    timing_stage: str | None = None
    pattern: str | None = None
    direction: str | None = None
    contract_state: str | None = None
    freshness: str | None = None
    evidence_groups: list[str] = Field(default_factory=list)
    evidence_summary: str | None = None
    missing_confirmation: str | None = None
    confirmation_condition: str | None = None
    invalidation_condition: str | None = None
    underlying: str | None = None
    option_type: str | None = None
    strike: str | None = None
    expiry: str | None = None
    dte: int | None = None
    ltp: str | None = None
    bid: str | None = None
    ask: str | None = None
    volume: int | None = None
    oi: int | None = None
    delta_oi: int | None = None
    iv: str | None = None
    liquidity_state: str | None = None


class WatchChange(BaseModel):
    model_config = {"frozen": True}

    category: WatchChangeCategory
    field: str
    before: str | None
    after: str | None


class WatchEvent(BaseModel):
    """Append-only event. T0 is present only on CREATED and is never rewritten."""

    model_config = {"frozen": True}

    event_id: str = Field(default_factory=new_event_id)
    kind: WatchEventKind
    watch_id: str
    occurred_at: datetime
    symbol: str
    query: str | None = None
    t0_unavailable: bool = False
    t0: ObservationSnapshot | None = None
    latest: ObservationSnapshot | None = None


class WatchRecord(BaseModel):
    """Folded view of WatchEvents. `t0` is the CREATED snapshot only."""

    model_config = {"frozen": True}

    watch_id: str
    symbol: str
    created_at: datetime
    query: str | None = None
    t0_unavailable: bool = False
    t0: ObservationSnapshot | None = None
    latest: ObservationSnapshot | None = None
    removed: bool = False
    evolution: list[ObservationSnapshot] = Field(default_factory=list)


def _norm(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text and text.upper() not in {"NONE", "N/A", "NULL"} else None


def _dec(value: object | None) -> str | None:
    if value is None:
        return None
    return str(value)


def _dt(value: object | None) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _as_mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_sequence(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def snapshot_from_analyze_payload(payload: dict[str, Any]) -> ObservationSnapshot:
    """Build T0/current from an AnalyzeResponse JSON object (or equivalent)."""
    visual = _as_mapping(payload.get("visual"))
    development = _as_mapping(visual.get("development"))
    blockers = _as_mapping(visual.get("blockers"))
    quality = _as_mapping(visual.get("quality"))
    requested = _as_mapping(visual.get("requested_contract"))
    assessment = _as_mapping(requested.get("assessment"))
    evidence_rows = _as_sequence(visual.get("evidence"))
    groups: list[str] = []
    details: list[str] = []
    for row in evidence_rows:
        if not isinstance(row, dict):
            continue
        group = _norm(row.get("group"))
        if group and group not in groups:
            groups.append(group)
        name = _norm(row.get("name"))
        direction = _norm(row.get("direction"))
        if name:
            details.append(f"{name}:{direction or 'n/a'}")
    missing = _norm(development.get("what_is_missing")) or _norm(blockers.get("missing_confirmation"))
    confirm = _norm(development.get("confirm_if"))
    invalidate = _norm(development.get("invalidate_if")) or _norm(payload.get("invalidation_condition"))
    contract_state = None
    primary = _as_mapping(blockers.get("primary"))
    if _norm(primary.get("blocker_class")) == "CONTRACT_UNUSABLE":
        contract_state = "UNTRADEABLE"
    elif assessment:
        grade = _norm(assessment.get("liquidity_grade")) or _norm(assessment.get("structural_quality"))
        contract_state = grade
    elif requested:
        contract_state = "UNAVAILABLE" if requested.get("found") is False else "AVAILABLE"
    freshness = _norm(quality.get("data_quality")) or _norm(payload.get("data_quality"))
    obs_ts = _dt(payload.get("market_observed_at")) or _dt(payload.get("generated_at"))
    strike = assessment.get("strike") if assessment else payload.get("parsed_strike")
    right = assessment.get("right") if assessment else payload.get("parsed_right")
    return ObservationSnapshot(
        observation_id=_norm(payload.get("audit_id")),
        observation_timestamp=obs_ts,
        generated_at=_dt(payload.get("generated_at")),
        query=_norm(payload.get("query")),
        research_state=_norm(payload.get("research_state")) or _norm(visual.get("research_state")),
        timing_stage=_norm(payload.get("timing_stage")),
        pattern=_norm(development.get("pattern")),
        direction=_norm(visual.get("convergence")) or _norm(payload.get("decision")),
        contract_state=contract_state,
        freshness=freshness,
        evidence_groups=groups,
        evidence_summary="; ".join(details[:12]) or None,
        missing_confirmation=missing,
        confirmation_condition=confirm,
        invalidation_condition=invalidate,
        underlying=_norm(payload.get("symbol")),
        option_type=_norm(right),
        strike=_dec(strike),
        expiry=_norm(payload.get("parsed_expiry_hint")),
        dte=payload.get("dte") if isinstance(payload.get("dte"), int) else None,
        ltp=_dec(assessment.get("ltp")),
        bid=_dec(assessment.get("bid")),
        ask=_dec(assessment.get("ask")),
        volume=assessment.get("volume") if isinstance(assessment.get("volume"), int) else None,
        oi=assessment.get("open_interest") if isinstance(assessment.get("open_interest"), int) else None,
        delta_oi=(
            assessment.get("change_in_open_interest")
            if isinstance(assessment.get("change_in_open_interest"), int)
            else None
        ),
        iv=_dec(assessment.get("implied_volatility")),
        liquidity_state=_norm(assessment.get("liquidity_grade")),
    )


def snapshot_from_screener_row(row: dict[str, Any]) -> ObservationSnapshot:
    """Weaker T0 from a scan card — still a real observation, not a fabricated one."""
    observed = _dt(row.get("observed_at")) or _dt(row.get("generated_at"))
    return ObservationSnapshot(
        observation_id=_norm(row.get("audit_id")),
        observation_timestamp=observed,
        generated_at=observed,
        query=_norm(row.get("symbol")),
        research_state=_norm(row.get("research_state")),
        timing_stage=_norm(row.get("timing_stage")),
        pattern=_norm(row.get("developing_pattern")),
        direction=_norm(row.get("direction_hypothesis")) or _norm(row.get("research_state")),
        contract_state=_norm(row.get("contract_usability")) or _norm(row.get("liquidity_grade")),
        freshness=_norm(row.get("options_data_quality")),
        evidence_groups=[],
        evidence_summary=_norm(row.get("why_watching")) or _norm(row.get("what_is_happening")),
        missing_confirmation=_norm(row.get("what_is_missing")),
        confirmation_condition=_norm(row.get("what_would_confirm")),
        invalidation_condition=_norm(row.get("what_invalidates")),
        underlying=_norm(row.get("symbol")),
        option_type=None,
        strike=_norm(row.get("selected_strike")) or _norm(row.get("strike")),
        expiry=_norm(row.get("expiry")),
        dte=row.get("dte") if isinstance(row.get("dte"), int) else None,
        liquidity_state=_norm(row.get("liquidity_grade")),
    )


_DEGRADED_CONTRACT = {"UNTRADEABLE", "UNUSABLE", "ILLIQUID", "UNAVAILABLE"}
_DEGRADED_FRESHNESS = {"STALE", "RED", "EXPIRED"}


def compare_snapshots(t0: ObservationSnapshot | None, latest: ObservationSnapshot | None) -> list[WatchChange]:
    """Deterministic field diff. No AI, no scores, no inferred intent."""
    if t0 is None or latest is None:
        return []
    changes: list[WatchChange] = []

    def add(category: WatchChangeCategory, field: str, before: object | None, after: object | None) -> None:
        b, a = _norm(before), _norm(after)
        if b == a:
            return
        changes.append(WatchChange(category=category, field=field, before=b, after=a))

    add(WatchChangeCategory.STATE_CHANGED, "research_state", t0.research_state, latest.research_state)
    add(WatchChangeCategory.TIMING_CHANGED, "timing_stage", t0.timing_stage, latest.timing_stage)
    add(WatchChangeCategory.PATTERN_CHANGED, "pattern", t0.pattern, latest.pattern)
    add(WatchChangeCategory.DIRECTION_CHANGED, "direction", t0.direction, latest.direction)
    add(WatchChangeCategory.CONTRACT_CHANGED, "contract_state", t0.contract_state, latest.contract_state)
    add(WatchChangeCategory.CONTRACT_CHANGED, "strike", t0.strike, latest.strike)
    add(WatchChangeCategory.CONTRACT_CHANGED, "option_type", t0.option_type, latest.option_type)
    add(WatchChangeCategory.CONTRACT_CHANGED, "expiry", t0.expiry, latest.expiry)
    after_f = (latest.freshness or "").upper()
    before_f = (t0.freshness or "").upper()
    if after_f != before_f and any(token in after_f for token in _DEGRADED_FRESHNESS) and not any(
        token in before_f for token in _DEGRADED_FRESHNESS
    ):
        add(WatchChangeCategory.FRESHNESS_DEGRADED, "freshness", t0.freshness, latest.freshness)
    add(WatchChangeCategory.MISSING_CHANGED, "missing_confirmation", t0.missing_confirmation, latest.missing_confirmation)
    add(
        WatchChangeCategory.CONDITION_TEXT_CHANGED,
        "confirmation_condition",
        t0.confirmation_condition,
        latest.confirmation_condition,
    )
    add(
        WatchChangeCategory.CONDITION_TEXT_CHANGED,
        "invalidation_condition",
        t0.invalidation_condition,
        latest.invalidation_condition,
    )
    t0_groups = sorted(t0.evidence_groups)
    latest_groups = sorted(latest.evidence_groups)
    if t0_groups != latest_groups:
        changes.append(
            WatchChange(
                category=WatchChangeCategory.EVIDENCE_CHANGED,
                field="evidence_groups",
                before=",".join(t0_groups) or None,
                after=",".join(latest_groups) or None,
            )
        )

    latest_state = (latest.research_state or "").upper()
    t0_state = (t0.research_state or "").upper()
    if latest_state == "CONFIRMED_SETUP" and t0_state != "CONFIRMED_SETUP":
        changes.append(
            WatchChange(
                category=WatchChangeCategory.CONFIRMATION_APPEARED,
                field="research_state",
                before=t0.research_state,
                after=latest.research_state,
            )
        )
    if latest_state == "CONFLICT" and t0_state != "CONFLICT":
        changes.append(
            WatchChange(
                category=WatchChangeCategory.CONFLICT_APPEARED,
                field="research_state",
                before=t0.research_state,
                after=latest.research_state,
            )
        )
    if latest_state == "EXTENDED" and t0_state != "EXTENDED":
        changes.append(
            WatchChange(
                category=WatchChangeCategory.EXTENDED,
                field="research_state",
                before=t0.research_state,
                after=latest.research_state,
            )
        )
    if latest_state == "DATA_INSUFFICIENT" and t0_state != "DATA_INSUFFICIENT":
        changes.append(
            WatchChange(
                category=WatchChangeCategory.DATA_BECAME_INSUFFICIENT,
                field="research_state",
                before=t0.research_state,
                after=latest.research_state,
            )
        )

    after_c = (latest.contract_state or "").upper()
    before_c = (t0.contract_state or "").upper()
    if any(token in after_c for token in _DEGRADED_CONTRACT) and not any(
        token in before_c for token in _DEGRADED_CONTRACT
    ):
        changes.append(
            WatchChange(
                category=WatchChangeCategory.CONTRACT_DEGRADED,
                field="contract_state",
                before=t0.contract_state,
                after=latest.contract_state,
            )
        )
    if not changes:
        return [
            WatchChange(
                category=WatchChangeCategory.NO_MATERIAL_CHANGE,
                field="observation",
                before=t0.observation_id,
                after=latest.observation_id,
            )
        ]
    # Do not also emit NO_MATERIAL_CHANGE when real diffs exist.
    return [c for c in changes if c.category != WatchChangeCategory.NO_MATERIAL_CHANGE]


def fold_events(events: list[WatchEvent]) -> dict[str, WatchRecord]:
    """Replay append-only events. Later REMOVED hides the watch. T0 never mutates."""
    records: dict[str, WatchRecord] = {}
    for event in sorted(events, key=lambda e: (e.occurred_at, e.event_id)):
        if event.kind == WatchEventKind.CREATED:
            t0 = event.t0
            records[event.watch_id] = WatchRecord(
                watch_id=event.watch_id,
                symbol=event.symbol.upper(),
                created_at=event.occurred_at,
                query=event.query,
                t0_unavailable=event.t0_unavailable,
                t0=t0,
                latest=event.latest or t0,
                removed=False,
                evolution=[t0] if t0 is not None else [],
            )
            continue
        existing = records.get(event.watch_id)
        if existing is None:
            continue
        if event.kind == WatchEventKind.REMOVED:
            records[event.watch_id] = existing.model_copy(update={"removed": True})
            continue
        if event.kind == WatchEventKind.LATEST_UPDATED:
            latest = event.latest
            if latest is None:
                continue
            if not _latest_is_not_lookahead(existing.t0, latest):
                continue
            evo = list(existing.evolution)
            if not evo or evo[-1].observation_id != latest.observation_id or evo[-1].research_state != latest.research_state:
                evo.append(latest)
            records[event.watch_id] = existing.model_copy(update={"latest": latest, "evolution": evo})
    return records


def _latest_is_not_lookahead(t0: ObservationSnapshot | None, latest: ObservationSnapshot) -> bool:
    """Latest may be later than T0; it must never be used to rewrite T0.

    Reject a 'latest' whose observation timestamp is strictly *before* T0 —
    that would be a past print, not evolution, and must not appear as NOW.
    Equal timestamps are allowed (same print, re-analyze).
    """
    if t0 is None or t0.observation_timestamp is None or latest.observation_timestamp is None:
        return True
    return latest.observation_timestamp >= t0.observation_timestamp


def active_by_symbol(records: dict[str, WatchRecord]) -> dict[str, WatchRecord]:
    active: dict[str, WatchRecord] = {}
    for record in records.values():
        if record.removed:
            continue
        active[record.symbol.upper()] = record
    return active
