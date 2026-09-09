"""Named evolution of research maturity across snapshots.

Compares two already-computed research states (and optional timing
buckets). Never infers WHY the change happened. Never uses future data.
"""

from __future__ import annotations

from enum import Enum


class DevelopmentEvolution(str, Enum):
    EMERGING = "EMERGING"
    STRENGTHENING = "STRENGTHENING"
    WEAKENING = "WEAKENING"
    CONFIRMING = "CONFIRMING"
    INVALIDATED = "INVALIDATED"
    BECOMING_EXTENDED = "BECOMING_EXTENDED"
    UNCHANGED = "UNCHANGED"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"


_MATURITY_ORDER = {
    "DATA_INSUFFICIENT": 0,
    "UNKNOWN": 0,
    "NO_TRADE": 1,
    "WATCH": 2,
    "EARLY_SETUP": 3,
    "CONFIRMATION_PENDING": 3,
    "CONFIRMED_SETUP": 4,
    "EXTENDED": 5,
    "CONFLICT": -1,
}


def classify_development_evolution(*, previous_state: str | None, current_state: str | None) -> DevelopmentEvolution:
    if previous_state is None or current_state is None:
        return DevelopmentEvolution.INSUFFICIENT_HISTORY
    if previous_state == current_state:
        return DevelopmentEvolution.UNCHANGED
    if current_state == "CONFLICT":
        return DevelopmentEvolution.INVALIDATED
    if current_state == "EXTENDED" and previous_state != "EXTENDED":
        return DevelopmentEvolution.BECOMING_EXTENDED
    if current_state == "CONFIRMED_SETUP" and previous_state in ("WATCH", "EARLY_SETUP", "CONFIRMATION_PENDING"):
        return DevelopmentEvolution.CONFIRMING
    if current_state == "EARLY_SETUP" and previous_state in ("WATCH", "NO_TRADE", "DATA_INSUFFICIENT", "UNKNOWN"):
        return DevelopmentEvolution.EMERGING
    prev_rank = _MATURITY_ORDER.get(previous_state)
    curr_rank = _MATURITY_ORDER.get(current_state)
    if prev_rank is None or curr_rank is None:
        return DevelopmentEvolution.UNCHANGED
    if curr_rank < 0 or prev_rank < 0:
        return DevelopmentEvolution.WEAKENING if current_state != previous_state else DevelopmentEvolution.UNCHANGED
    if curr_rank > prev_rank:
        return DevelopmentEvolution.STRENGTHENING
    if curr_rank < prev_rank:
        return DevelopmentEvolution.WEAKENING
    return DevelopmentEvolution.UNCHANGED
