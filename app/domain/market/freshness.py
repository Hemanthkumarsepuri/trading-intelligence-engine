"""Data freshness classification and the age/staleness model.

See docs/architecture/ARCHITECTURE.md, Addendum A2. Every normalized data
point in this system — candles, quotes, option chain legs, news — carries a
`DataFreshness` so staleness can be evaluated per freshness class rather than
against one global "5 minutes" assumption. This module is pure `domain`: no
I/O, no config, no clock reads — thresholds and "now" are always supplied by
the caller (typically `data/validation`).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum

from pydantic import BaseModel, model_validator


class FreshnessClass(str, Enum):
    """See ARCHITECTURE.md Addendum A2 for the full description of each class."""

    REAL_TIME = "REAL_TIME"
    SHORT_INTERVAL = "SHORT_INTERVAL"
    ANALYSIS_INTERVAL = "ANALYSIS_INTERVAL"
    SLOW_REFRESH = "SLOW_REFRESH"
    EVENT_DRIVEN = "EVENT_DRIVEN"


class DataFreshness(BaseModel):
    """When a piece of data is *of*, when this process received it, and (once
    a cycle consumes it) when that analysis happened.
    """

    data_timestamp: datetime
    received_timestamp: datetime
    analysis_timestamp: datetime | None = None

    @model_validator(mode="after")
    def _require_timezone_aware(self) -> DataFreshness:
        for field_name in ("data_timestamp", "received_timestamp", "analysis_timestamp"):
            value = getattr(self, field_name)
            if value is not None and value.tzinfo is None:
                raise ValueError(f"{field_name} must be timezone-aware")
        return self

    @model_validator(mode="after")
    def _require_non_negative_age(self) -> DataFreshness:
        # received_timestamp before data_timestamp would mean we received data
        # from the future — always a bug (clock skew or a provider echoing a
        # bad timestamp), never a valid state.
        if self.received_timestamp < self.data_timestamp:
            raise ValueError("received_timestamp must not be before data_timestamp")
        return self

    @property
    def data_age(self) -> timedelta:
        """Age of the data as of when it was received."""
        return self.received_timestamp - self.data_timestamp

    def is_stale(self, max_age: timedelta, *, as_of: datetime | None = None) -> bool:
        """Whether this data should be treated as stale.

        Always checks staleness at receipt time. If `as_of` is given (e.g. an
        analysis cycle consuming data that was received a while ago), also
        checks staleness at that later instant — data that was fresh when
        received can still be stale by the time a cycle gets to it.
        """
        if self.data_age > max_age:
            return True
        if as_of is not None:
            if as_of.tzinfo is None:
                raise ValueError("as_of must be timezone-aware")
            if as_of - self.data_timestamp > max_age:
                return True
        return False
