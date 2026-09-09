"""EOD / previous-session delivery context — informational, never a vote.

Delivery percentage is only meaningful as an official end-of-day figure.
During a live session this is always PREVIOUS_SESSION, never LIVE.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum


class DeliveryFreshness(str, Enum):
    EOD = "EOD"
    PREVIOUS_SESSION = "PREVIOUS_SESSION"
    UNKNOWN = "UNKNOWN"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class DeliveryObservation:
    symbol: str
    trade_date: date | None
    traded_quantity: int | None
    deliverable_quantity: int | None
    delivery_pct: Decimal | None
    series: str | None
    source: str
    retrieved_at: datetime
    session: DeliveryFreshness
    detail: str


def unknown_delivery(*, symbol: str, retrieved_at: datetime, reason: str) -> DeliveryObservation:
    return DeliveryObservation(
        symbol=symbol, trade_date=None, traded_quantity=None, deliverable_quantity=None,
        delivery_pct=None, series=None, source="nse_sec_bhavdata_full", retrieved_at=retrieved_at,
        session=DeliveryFreshness.UNKNOWN, detail=reason,
    )
