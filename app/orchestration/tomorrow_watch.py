"""Closed/pre-market Tomorrow Watch — monitoring checklist, not a forecast.

Built only from already-computed analyze visual/session fields. Never
creates BUY/SELL, probability, or a predicted next-session direction.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.domain.options.plain_language import research_state_plain_english
from app.orchestration.visual_data import EvidenceRowView, VisualData

_GROUP_LABELS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("PRICE STRUCTURE", ("underlying_price_structure",)),
    ("OPTION STRUCTURE", ("options_oi",)),
    ("VOLATILITY", ("options_iv",)),
    ("FUTURES", ("futures",)),
    ("LIQUIDITY", ("liquidity",)),
    ("RELATIVE STRENGTH", ("relative_strength",)),
    ("MARKET CONTEXT", ("global",)),
    ("NEWS", ("news_event",)),
    ("DATA QUALITY", ("data_quality",)),
)

_STREAM_FOR_ENGINE_GROUP = {
    "underlying_price_structure": "candles_m15",
    "relative_strength": "underlying_quote",
    "options_oi": "option_chain",
    "options_iv": "option_chain",
    "liquidity": "option_chain",
    "futures": "futures",
    "global": "underlying_quote",
    "news_event": "news",
    "data_quality": "underlying_quote",
}


class EvidenceGroupBrief(BaseModel):
    label: str
    status: str
    evidence: list[str] = Field(default_factory=list)
    freshness: str | None = None
    missing: str | None = None


class TomorrowWatchView(BaseModel):
    """Pre-open research brief. Absent (`None` on AnalyzeResponse) while LIVE."""

    observation_kind: str
    research_session_mode: str
    one_line_summary: str
    last_observed_at: datetime | None = None
    next_session_open_ist_label: str | None = None
    why: list[str] = Field(default_factory=list)
    evidence_groups: list[EvidenceGroupBrief] = Field(default_factory=list)
    missing: str | None = None
    confirm_if: str | None = None
    invalidate_if: str | None = None
    at_open_check: list[str] = Field(default_factory=list)
    contract_dte: int | None = None


def one_line_research_summary(research_state: str | None) -> str:
    state = (research_state or "UNKNOWN").strip() or "UNKNOWN"
    if state == "CONFLICT":
        return "Latest verified observation shows conflicting evidence; confirmation remains pending."
    if state == "EARLY_SETUP":
        return "Latest verified observation shows an early setup; confirmation remains pending."
    if state == "WATCH" or state == "CONFIRMATION_PENDING":
        return "Latest verified observation needs confirmation; confirmation remains pending."
    if state == "CONFIRMED_SETUP":
        return "Latest verified observation shows a confirmed setup — not a buy instruction. Re-evaluate when live data is available."
    if state == "EXTENDED":
        return "Latest verified observation shows an extended move. This is not a live print."
    if state == "DATA_INSUFFICIENT":
        return "Latest verified observation does not have enough reliable data."
    if state == "NO_TRADE":
        return "Latest verified observation does not show a compelling developing setup."
    return f"Latest verified observation: {research_state_plain_english(state)}."


def _visual_attr(visual: VisualData | None, name: str) -> Any:
    if visual is None:
        return None
    return getattr(visual, name, None)


def _blank_to_none(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text or text.upper() in {"N/A", "NA", "NONE", "UNKNOWN"}:
        return None
    return text


def _stream_status(visual: VisualData | None, engine_group: str) -> str | None:
    freshness = _visual_attr(visual, "freshness")
    if freshness is None:
        return None
    wanted = _STREAM_FOR_ENGINE_GROUP.get(engine_group)
    for stream in getattr(freshness, "streams", None) or []:
        if stream.stream == wanted:
            # Closed last-prints stay MARKET_CLOSED. Only STALE isolates a group.
            label = getattr(stream, "label", None)
            return str(label) if label is not None else None
    return None


def _group_status(rows: list[EvidenceRowView], stream_label: str | None) -> str:
    if stream_label == "STALE":
        return "STALE"
    if not rows:
        return "MISSING"
    directions = {row.direction for row in rows}
    directional = directions & {"BULLISH", "BEARISH"}
    if len(directional) > 1:
        return "CONFLICTING"
    if directional:
        return "SUPPORTED"
    if directions <= {"INSUFFICIENT", "UNKNOWN"} or "INSUFFICIENT" in directions:
        return "INSUFFICIENT"
    if directions <= {"NEUTRAL"}:
        return "NOT APPLICABLE"
    return "INSUFFICIENT"


def _dte_for_observed_expiry(visual: VisualData | None) -> int | None:
    chain = _visual_attr(visual, "option_chain")
    requested = _visual_attr(visual, "requested_contract")
    term = _visual_attr(visual, "term_structure")
    expiry = None
    if chain is not None and getattr(chain, "expiry", None) is not None:
        expiry = chain.expiry
    elif requested is not None and getattr(requested, "expiry", None) is not None:
        expiry = requested.expiry
    if expiry is None or term is None:
        return None
    wanted = expiry.isoformat() if hasattr(expiry, "isoformat") else str(expiry)
    for row in getattr(term, "expiries", None) or []:
        row_exp = row.expiry.isoformat() if hasattr(row.expiry, "isoformat") else str(row.expiry)
        if row_exp == wanted:
            days = getattr(row, "days_remaining", None)
            return int(days) if days is not None else None
    return None


def _at_open_check(
    *,
    has_specific_contract: bool,
    confirm_if: str | None,
    invalidate_if: str | None,
    missing: str | None,
    contract_unverified: bool,
) -> list[str]:
    checks = [
        "Refresh underlying price observation",
        "Recalculate freshness for each stream independently",
        "Re-evaluate evidence groups",
        "Re-evaluate research state",
    ]
    if has_specific_contract:
        checks.extend(
            [
                "Refresh option-chain observation",
                "Verify strike and option type against the live chain",
                "Verify requested expiry against the observed chain",
                "Verify bid/ask when a quote is current",
                "Verify liquidity / contract usability",
            ]
        )
    if contract_unverified:
        checks.append("Requested contract was unverified on the last observation — do not treat it as a live contract until the chain confirms it")
    if confirm_if:
        checks.append(f"Re-check confirmation condition: {confirm_if}")
    if invalidate_if:
        checks.append(f"Re-check invalidation condition: {invalidate_if}")
    if missing:
        checks.append(f"Re-check missing evidence: {missing}")
    return checks


def build_tomorrow_watch(
    *,
    session_window: str,
    observation_kind: str,
    research_session_mode: str,
    next_session_open_ist_label: str | None,
    research_state: str | None,
    visual: VisualData | None,
    has_specific_contract: bool,
    invalidation_condition: str | None,
    market_observed_at: datetime | None,
) -> TomorrowWatchView | None:
    if session_window == "OPEN":
        return None

    development = _visual_attr(visual, "development")
    confirm_if = _blank_to_none(development.confirm_if if development is not None else None)
    invalidate_if = _blank_to_none(
        (development.invalidate_if if development is not None else None) or invalidation_condition
    )
    missing = _blank_to_none(development.what_is_missing if development is not None else None)
    blockers = _visual_attr(visual, "blockers")
    if missing is None and blockers is not None:
        missing = _blank_to_none(getattr(blockers, "missing_confirmation", None))

    rows = list(_visual_attr(visual, "evidence") or [])
    groups: list[EvidenceGroupBrief] = []
    why: list[str] = []
    for label, engine_ids in _GROUP_LABELS:
        grouped = [row for row in rows if row.group in engine_ids]
        if not grouped:
            continue
        stream_label = _stream_status(visual, engine_ids[0])
        status = _group_status(grouped, stream_label)
        evidence = [f"{row.name}: {row.direction}" for row in grouped]
        groups.append(
            EvidenceGroupBrief(
                label=label,
                status=status,
                evidence=evidence,
                freshness=stream_label,
                missing=None if status not in {"MISSING", "INSUFFICIENT"} else (missing or "group did not produce a usable vote"),
            )
        )
        why.append(f"{label}: {status}")

    contract_unverified = False
    requested = _visual_attr(visual, "requested_contract")
    if requested is not None and has_specific_contract:
        contract_unverified = requested.found is False

    return TomorrowWatchView(
        observation_kind=observation_kind,
        research_session_mode=research_session_mode,
        one_line_summary=one_line_research_summary(research_state),
        last_observed_at=market_observed_at,
        next_session_open_ist_label=next_session_open_ist_label,
        why=why,
        evidence_groups=groups,
        missing=missing,
        confirm_if=confirm_if,
        invalidate_if=invalidate_if,
        at_open_check=_at_open_check(
            has_specific_contract=has_specific_contract,
            confirm_if=confirm_if,
            invalidate_if=invalidate_if,
            missing=missing,
            contract_unverified=contract_unverified,
        ),
        contract_dte=_dte_for_observed_expiry(visual),
    )


def factual_since_t0_lines(changes: list[Any]) -> list[str]:
    """Objective field diffs only. Never infers momentum or trader intent."""
    labels = {
        "ltp": "Option LTP changed",
        "oi": "Option open interest changed",
        "iv": "Option IV changed",
        "underlying_last": "Underlying last observed price changed",
        "research_state": "Research state changed",
        "pattern": "Named pattern changed",
        "contract_state": "Contract usability changed",
        "expiry": "Observed expiry changed",
        "strike": "Observed strike changed",
        "option_type": "Observed option type changed",
        "instrument_type": "Observation scope changed",
        "freshness": "Freshness label changed",
    }
    lines: list[str] = []
    for change in changes:
        category = getattr(change, "category", None)
        field = getattr(change, "field", None)
        before = getattr(change, "before", None)
        after = getattr(change, "after", None)
        if str(getattr(category, "value", category) or "") == "NO_MATERIAL_CHANGE":
            continue
        if str(getattr(category, "value", category) or "") == "OBSERVATION_SCOPE_CHANGED":
            lines.append(
                f"Observation scope changed from {before or 'n/a'} to {after or 'n/a'}."
            )
            continue
        stem = labels.get(str(field or ""), None)
        if stem is None:
            continue
        if before is None or after is None:
            continue
        lines.append(f"{stem} from {before} to {after}.")
    return lines
