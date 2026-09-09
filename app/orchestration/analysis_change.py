""""What changed?" — deterministic comparison between two
`InstrumentSnapshot`s for the same instrument, taken at different times.

Lives in `orchestration/`, not `domain/`, because it compares
`InstrumentSnapshot` — an orchestration-level type combining quote + market
status + strategy analysis — and `domain/*` may not import from
`orchestration/*` (ARCHITECTURE.md §6). This module adds no new facts; it
only names specific, pre-defined transitions between two already-computed
snapshots. A bare number changing (LTP ticking by a paisa, a VWAP value
shifting slightly) is deliberately NOT reported — only the named,
structural transitions below are, so "what changed" never degrades into
noise or an invented "signal."
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.orchestration.watchlist_snapshot import InstrumentSnapshot


class ChangeKind(str, Enum):
    MARKET_STATUS_CHANGED = "MARKET_STATUS_CHANGED"
    DATA_STATE_CHANGED = "DATA_STATE_CHANGED"
    EMA_ALIGNMENT_CHANGED = "EMA_ALIGNMENT_CHANGED"
    PRICE_VWAP_RELATION_CHANGED = "PRICE_VWAP_RELATION_CHANGED"
    SETUP_APPEARED = "SETUP_APPEARED"
    SETUP_DISAPPEARED = "SETUP_DISAPPEARED"
    SETUP_DIRECTION_CHANGED = "SETUP_DIRECTION_CHANGED"
    NEW_COMPLETED_CANDLE = "NEW_COMPLETED_CANDLE"


@dataclass
class ChangeEvent:
    kind: ChangeKind
    detail: str


def detect_changes(*, previous: InstrumentSnapshot | None, current: InstrumentSnapshot) -> list[ChangeEvent]:
    """Returns `[]` (never a fabricated event) when there is nothing to
    compare against yet (`previous is None`) or nothing named above
    actually differs.
    """
    if previous is None:
        return []

    events: list[ChangeEvent] = []

    if previous.exchange_status != current.exchange_status:
        events.append(
            ChangeEvent(
                ChangeKind.MARKET_STATUS_CHANGED,
                f"{_label(previous.exchange_status)} -> {_label(current.exchange_status)}",
            )
        )

    if previous.state != current.state:
        events.append(ChangeEvent(ChangeKind.DATA_STATE_CHANGED, f"{previous.state.value} -> {current.state.value}"))

    prev_analysis = previous.analysis
    curr_analysis = current.analysis
    if prev_analysis is not None and curr_analysis is not None:
        if prev_analysis.ema_alignment != curr_analysis.ema_alignment:
            events.append(
                ChangeEvent(
                    ChangeKind.EMA_ALIGNMENT_CHANGED,
                    f"{_label(prev_analysis.ema_alignment)} -> {_label(curr_analysis.ema_alignment)}",
                )
            )

        if (
            prev_analysis.price_vs_vwap is not None
            and curr_analysis.price_vs_vwap is not None
            and prev_analysis.price_vs_vwap != curr_analysis.price_vs_vwap
        ):
            events.append(
                ChangeEvent(
                    ChangeKind.PRICE_VWAP_RELATION_CHANGED,
                    f"{prev_analysis.price_vs_vwap.value} -> {curr_analysis.price_vs_vwap.value}",
                )
            )

        prev_setup, curr_setup = prev_analysis.setup, curr_analysis.setup
        if prev_setup is None and curr_setup is not None:
            events.append(ChangeEvent(ChangeKind.SETUP_APPEARED, curr_setup.direction))
        elif prev_setup is not None and curr_setup is None:
            events.append(ChangeEvent(ChangeKind.SETUP_DISAPPEARED, prev_setup.direction))
        elif prev_setup is not None and curr_setup is not None and prev_setup.direction != curr_setup.direction:
            events.append(
                ChangeEvent(ChangeKind.SETUP_DIRECTION_CHANGED, f"{prev_setup.direction} -> {curr_setup.direction}")
            )

        if (
            curr_analysis.last_completed_candle_timestamp is not None
            and prev_analysis.last_completed_candle_timestamp != curr_analysis.last_completed_candle_timestamp
        ):
            events.append(
                ChangeEvent(ChangeKind.NEW_COMPLETED_CANDLE, str(curr_analysis.last_completed_candle_timestamp))
            )

    return events


def _label(value: object) -> str:
    return value.value if hasattr(value, "value") else str(value)
