"""Personal decision journal -- what the operator thought, not a trade.

Append-only. No execution. Outcome classification is recorded later by
the operator (or by a future comparison against `research_outcome`
checkpoints) -- this module never infers emotion, never scores the
decision, and never feeds ranking.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field


def new_journal_id() -> str:
    return uuid4().hex


class MistakeClass(str, Enum):
    DIRECTION_ERROR = "DIRECTION_ERROR"
    TIMING_ERROR = "TIMING_ERROR"
    CONTRACT_SELECTION_ERROR = "CONTRACT_SELECTION_ERROR"
    IV_ERROR = "IV_ERROR"
    THETA_ERROR = "THETA_ERROR"
    LIQUIDITY_ERROR = "LIQUIDITY_ERROR"
    OVEREXTENSION_ERROR = "OVEREXTENSION_ERROR"
    MARKET_REGIME_ERROR = "MARKET_REGIME_ERROR"
    SECTOR_ERROR = "SECTOR_ERROR"
    NEWS_ERROR = "NEWS_ERROR"
    NO_CONFIRMATION = "NO_CONFIRMATION"
    REVERSAL = "REVERSAL"
    DATA_ERROR = "DATA_ERROR"
    EMOTIONAL_DECISION = "EMOTIONAL_DECISION"
    UNKNOWN = "UNKNOWN"
    NONE = "NONE"


class PersonalJournalEntry(BaseModel):
    model_config = {"frozen": True}

    journal_id: str = Field(default_factory=new_journal_id)
    recorded_at: datetime
    symbol: str
    market_regime: str | None = None
    sector: str | None = None
    research_state: str | None = None
    research_bucket: str | None = None
    timing_stage: str | None = None
    direction_hypothesis: str | None = None
    underlying_price: str | None = None
    option_contract: str | None = None
    strike: str | None = None
    expiry: str | None = None
    dte: int | None = None
    iv: str | None = None
    spread: str | None = None
    liquidity: str | None = None
    supporting_evidence: str | None = None
    missing_evidence: str | None = None
    confirmation: str | None = None
    invalidation: str | None = None
    development_pattern: str | None = None
    user_reasoning: str
    audit_id: str | None = None


class PersonalJournalOutcome(BaseModel):
    model_config = {"frozen": True}

    outcome_id: str = Field(default_factory=new_journal_id)
    journal_id: str
    captured_at: datetime
    what_i_thought: str
    what_tire_observed: str | None = None
    what_actually_happened: str
    mistake_class: MistakeClass = MistakeClass.UNKNOWN
    notes: str | None = None
