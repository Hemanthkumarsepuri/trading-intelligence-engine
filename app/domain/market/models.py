"""Canonical, provider-neutral market-data contracts.

Nothing in this module may import from `app.data.*`, `app.persistence.*`,
`app.llm.*`, or `app.orchestration.*` (see docs/architecture/ARCHITECTURE.md
§6 — the dependency-direction rule that keeps `domain/` pure and testable
with plain fixtures, and keeps provider identity out of everything except
`data/providers` and `data/normalization`).

These models intentionally do NOT enforce every business rule (e.g. OHLC
consistency is checked here only as a basic sanity guard against obviously
impossible objects) — the authoritative, individually-auditable checks live
in `app.data.validation.quality_gate.DataQualityGate` (Addendum A3), so a
failure shows up as a named, logged check rather than a silent construction
error.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field, model_validator

from app.domain.market.freshness import DataFreshness


class ExchangeSegment(str, Enum):
    """Provider-neutral segment identifiers. Provider adapters translate their
    own segment strings to/from this enum in `data/providers`/`data/normalization`
    — nothing else ever sees a provider-specific segment string.
    """

    NSE_EQ = "NSE_EQ"
    NSE_FNO = "NSE_FNO"
    NSE_INDEX = "NSE_INDEX"
    NSE_CURRENCY = "NSE_CURRENCY"
    BSE_EQ = "BSE_EQ"
    BSE_FNO = "BSE_FNO"
    MCX_COMM = "MCX_COMM"


class Timeframe(str, Enum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    D1 = "1d"


class OptionRight(str, Enum):
    CE = "CE"
    PE = "PE"


class Instrument(BaseModel):
    """Static reference data for one tradable security."""

    security_id: str
    symbol: str
    exchange_segment: ExchangeSegment
    lot_size: int = Field(gt=0)
    tick_size: Decimal = Field(gt=0)


class ProvenanceMixin(BaseModel):
    """Every normalized data point carries where it came from and how fresh it is."""

    provider: str
    freshness: DataFreshness


class Candle(ProvenanceMixin):
    instrument_id: str
    timeframe: Timeframe
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int = Field(ge=0)
    open_interest: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _check_ohlc_sanity(self) -> Candle:
        if self.low > self.high:
            raise ValueError("low must not exceed high")
        if not (self.low <= self.open <= self.high):
            raise ValueError("open must be within [low, high]")
        if not (self.low <= self.close <= self.high):
            raise ValueError("close must be within [low, high]")
        return self


class Quote(ProvenanceMixin):
    instrument_id: str
    last_price: Decimal = Field(gt=0)
    previous_close: Decimal | None = Field(default=None, gt=0)
    volume: int | None = Field(default=None, ge=0)
    open_interest: int | None = Field(default=None, ge=0)
    average_price: Decimal | None = Field(default=None, ge=0)


class OptionQuote(ProvenanceMixin):
    """One CE or PE leg at one strike/expiry."""

    underlying: str
    expiry: date
    strike: Decimal = Field(gt=0)
    right: OptionRight
    security_id: str | None = None
    last_price: Decimal | None = Field(default=None, ge=0)
    bid_price: Decimal | None = Field(default=None, ge=0)
    ask_price: Decimal | None = Field(default=None, ge=0)
    volume: int | None = Field(default=None, ge=0)
    open_interest: int | None = Field(default=None, ge=0)
    change_in_open_interest: int | None = None
    implied_volatility: Decimal | None = Field(default=None, ge=0)
    delta: Decimal | None = None
    theta: Decimal | None = None
    gamma: Decimal | None = None
    vega: Decimal | None = None


class OptionChainSnapshot(ProvenanceMixin):
    """A full strike window for one underlying+expiry at one point in time.

    This is the object that must be persisted at ingestion time — DhanHQ's
    option chain API is real-time only (no historical option-chain endpoint),
    so a snapshot that isn't stored here is permanently unreplayable for that
    timestamp (see ARCHITECTURE.md Addendum A1).
    """

    underlying: str
    expiry: date
    underlying_last_price: Decimal | None = Field(default=None, gt=0)
    legs: list[OptionQuote]

    @model_validator(mode="after")
    def _check_legs_match_snapshot(self) -> OptionChainSnapshot:
        for leg in self.legs:
            if leg.underlying != self.underlying or leg.expiry != self.expiry:
                raise ValueError("every leg must match the snapshot's underlying and expiry")
        return self
