"""Timezone and clock utilities.

All timestamps in this system are timezone-aware. Domain and engine code
never reads wall-clock time itself — "now" is always threaded in explicitly
(as `as_of`) so that real-time and replay modes run the exact same code path
(see docs/architecture/ARCHITECTURE.md, Addendum A5). `utc_now()` exists only
for the edges: ingestion, the scheduler, and provider adapters.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def utc_now() -> datetime:
    """Current UTC time. The only sanctioned way to read 'now' in this codebase."""
    return datetime.now(UTC)


def ensure_utc(dt: datetime) -> datetime:
    """Normalize a timezone-aware datetime to UTC. Rejects naive datetimes,
    since a naive timestamp from a data provider is an ambiguity bug waiting
    to happen, not something to silently assume a timezone for.
    """
    if dt.tzinfo is None:
        raise ValueError("ensure_utc requires a timezone-aware datetime; naive datetimes are ambiguous")
    return dt.astimezone(UTC)


def to_ist(dt: datetime) -> datetime:
    """Convert a timezone-aware datetime to Asia/Kolkata, for display only."""
    if dt.tzinfo is None:
        raise ValueError("to_ist requires a timezone-aware datetime")
    return dt.astimezone(IST)


def from_epoch_seconds(epoch_seconds: int) -> datetime:
    """Provider timestamps (e.g. Dhan's chart endpoints) arrive as epoch seconds."""
    return datetime.fromtimestamp(epoch_seconds, tz=UTC)
