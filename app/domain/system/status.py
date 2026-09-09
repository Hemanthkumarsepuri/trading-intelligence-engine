"""Factual capability/status classification -- never a fake green light.

GREEN requires configured + reachable + authenticated + a valid response.
A module that merely exists in the repo is not GREEN.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class StreamHealthState(str, Enum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class StreamStatus:
    name: str
    state: StreamHealthState
    configured: bool
    reachable: bool
    authenticated: bool
    valid_response: bool
    actually_used: bool
    last_successful_call: datetime | None
    detail: str


def classify_stream_status(
    *,
    configured: bool,
    reachable: bool,
    authenticated: bool,
    valid_response: bool,
    optional: bool = False,
) -> StreamHealthState:
    """Deterministic traffic light. Optional streams (Qwen, unused
    adapters) are UNKNOWN when not configured rather than RED."""
    if not configured:
        return StreamHealthState.UNKNOWN if optional else StreamHealthState.RED
    if not authenticated:
        return StreamHealthState.RED
    if not reachable:
        return StreamHealthState.YELLOW if optional else StreamHealthState.RED
    if valid_response:
        return StreamHealthState.GREEN
    return StreamHealthState.YELLOW
