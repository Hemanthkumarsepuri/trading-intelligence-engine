"""Pure result types for option-chain analysis — no provider/data imports,
per this codebase's dependency rule (`domain/*` never imports `data/*`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel

from app.domain.market.freshness import DataFreshness


class Moneyness(str, Enum):
    ITM = "ITM"
    ATM = "ATM"
    OTM = "OTM"


@dataclass(frozen=True)
class ChainTotals:
    """Chain-wide OI/volume aggregates. Ratios are `None`, never `0`, when
    the denominator itself is zero or entirely unknown — a `0` PCR would
    falsely assert "no put interest" when the truth is "cannot be computed".
    """

    total_call_oi: int
    total_put_oi: int
    total_call_volume: int
    total_put_volume: int
    put_call_ratio_oi: Decimal | None
    put_call_ratio_volume: Decimal | None
    legs_with_known_oi: int
    legs_with_missing_oi: int


@dataclass(frozen=True)
class StrikeOI:
    strike: Decimal
    open_interest: int


class ChainQualityIssueKind(str, Enum):
    STALE_SNAPSHOT = "stale_snapshot"
    NO_UNDERLYING_PRICE = "no_underlying_price"
    DEAD_CHAIN = "dead_chain"
    MISSING_GREEKS = "missing_greeks"
    ONE_SIDED_STRIKE = "one_sided_strike"
    WIDE_SPREAD = "wide_spread"
    # Sprint 6 -- bid > ask is an objectively impossible/suspicious quote
    # state (never a legitimate real order-book condition on a normal
    # snapshot), previously undetected: `spread_fraction = (ask-bid)/ask`
    # goes NEGATIVE for a crossed market, which silently never exceeds
    # `max_spread_fraction` (a positive threshold) -- see
    # `check_chain_quality()`'s own dedicated check for this, separate
    # from WIDE_SPREAD (a genuinely different, objectively-suspicious
    # condition, not just "spread was wide").
    CROSSED_MARKET = "crossed_market"
    EMPTY_CHAIN = "empty_chain"
    DUPLICATE_STRIKE_RIGHT = "duplicate_strike_right"
    EXPIRED_CONTRACT = "expired_contract"


@dataclass(frozen=True)
class ChainQualityIssue:
    kind: ChainQualityIssueKind
    detail: str


class IvObservation(BaseModel):
    """One persisted, timestamped ATM implied-volatility reading for one
    underlying+expiry — the minimum unit needed to eventually compute a
    genuine IV rank/percentile. A single observation proves nothing; IV rank
    requires a real history of these accumulated over time (see
    `app.domain.options.iv_context`). Never back-filled or fabricated —
    only ever written at the moment a real chain was actually fetched.
    """

    provider: str
    freshness: DataFreshness
    underlying: str
    expiry: date
    atm_strike: Decimal
    atm_ce_iv: Decimal | None
    atm_pe_iv: Decimal | None
    chain_iv: Decimal | None  # average of atm_ce_iv/atm_pe_iv when both present, else whichever is present
