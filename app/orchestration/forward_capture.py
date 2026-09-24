"""Sprint 3.3 -- FORWARD F&O OBSERVATION CAPTURE.

Turns a validated live analysis into an immutable, provenance-backed
`ResearchObservation` recording what TIRE genuinely knew at T0, ready for the
existing +1/+3/+5 session outcome checkpoints. This is a RECORDING step, not an
analysis step:

    live analysis (run_analysis -> AnalyzeResponse)      <- source of truth
        -> shortlisted candidate + `build_research_observation()`
        -> `assess_forward_capture()`   eligibility + identity + provenance
        -> `capture_forward_observation()`   persist once, deduplicated

There is no capture-specific decision, evidence, pattern or contract logic here:
everything recorded is copied from the analysis that already exists, and
nothing is fetched to make a record look richer.

T0 is the analysis's own authoritative timestamp (`AnalyzeResponse.generated_at`
== the `as_of` the analysis ran at == `ResearchObservation.generated_at`). It is
never the persistence time (`ForwardCaptureProvenance.persisted_at`, kept
separately), never a later evaluation time, and source data timestamps stay
per stream. The option chain's stream timestamp is an HTTP receipt time, not an
exchange matching-engine time, and is recorded as such.

Eligibility is DATA INTEGRITY only -- session, identity, T0 causality,
expiry sanity and Sprint 3.2 evidence availability. It never reads a score,
confidence, probability or research state value: a WATCH / EARLY_SETUP /
CONFIRMATION_PENDING observation is captured exactly like a CONFIRMED_SETUP one.
Capture is downstream of the existing shortlist gate (which supplies a
direction and a selected contract); this module adds no second gate on top of
the research vocabulary.

Every rule lives in `verify_forward_observation()` and is applied both before
persistence (as the gate) and later (as the integrity check behind the
inspection endpoint), so there is exactly one rule set. Capture failures are
infrastructure outcomes: they are returned/logged and never alter the analysis
result or any research state.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import TYPE_CHECKING

from pydantic import BaseModel

from app.domain.audit.research_models import (
    ForwardCaptureProvenance,
    ResearchObservation,
    StreamProvenance,
)
from app.domain.market.data_state import MarketDataState
from app.domain.market.trading_calendar import classify_session_window
from app.domain.options.evidence_availability import (
    AvailabilityState,
    EvidenceAvailability,
    EvidenceClass,
)
from app.utils.time import to_ist

if TYPE_CHECKING:
    from app.orchestration.daily_research import RankedCandidate
    from app.persistence.interfaces import ResearchOutcomeRepository

_LOG = logging.getLogger(__name__)

CAPTURE_POLICY_VERSION = "1"

# Observation kinds, derived (never stored) from `source` + `forward_capture`.
FORWARD_LIVE_CAPTURE = "FORWARD_LIVE_CAPTURE"
HISTORICAL_REPLAY = "HISTORICAL_REPLAY"
LEGACY_LIVE_UNVERIFIED = "LEGACY_LIVE_UNVERIFIED"

_LIVE_MARKET_STATES = frozenset({MarketDataState.LIVE_STREAMING.value, MarketDataState.LIVE_SNAPSHOT.value})
_CAPTURED_STREAMS = ("underlying_quote", "candles_m15", "option_chain", "futures", "macro", "news")
# The evidence states in which the chain a forward F&O observation is about was
# genuinely present and current at T0.
_CHAIN_USABLE = frozenset({AvailabilityState.AVAILABLE, AvailabilityState.CONFLICTING})


def observation_capture_kind(observation: ResearchObservation) -> str:
    """Distinguishes forward captures from replay and from pre-Sprint-3.3 live
    records without a second stored field."""
    if observation.forward_capture is not None:
        return FORWARD_LIVE_CAPTURE
    if observation.source == "REPLAY":
        return HISTORICAL_REPLAY
    return LEGACY_LIVE_UNVERIFIED


def _normalized_strike(strike: str) -> str:
    return format(Decimal(strike).normalize(), "f")


def forward_observation_identity(
    *, symbol: str, underlying_instrument_key: str | None, right: str, strike: str, expiry: date,
    contract_instrument_key: str | None, t0: datetime,
) -> str:
    """Deterministic observation id: the same contract observed at the same T0
    always yields the same id (so a retry, refresh or restart cannot mint a
    second record), while the same contract at a different T0 yields a
    different one. 32 hex chars, matching the format of the random ids used
    elsewhere. `persisted_at` is deliberately NOT part of the identity."""
    material = "|".join((
        "TIRE-FWD-V1", symbol, underlying_instrument_key or "", right, _normalized_strike(strike), expiry.isoformat(),
        contract_instrument_key or "", t0.astimezone(UTC).isoformat(),
    ))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def verify_forward_observation(observation: ResearchObservation) -> tuple[str, ...]:
    """The single rule set. Returns rejection/integrity reason codes; empty
    means the observation is a valid forward live capture."""
    capture = observation.forward_capture
    if capture is None:
        return ("NOT_A_FORWARD_CAPTURE",)
    reasons: list[str] = []
    t0 = observation.generated_at
    if t0.tzinfo is None:
        reasons.append("T0_NOT_TIMEZONE_AWARE")
    else:
        if t0 > capture.persisted_at:
            reasons.append("T0_AFTER_PERSISTED_AT")
        window = classify_session_window(t0)
        if window.session_window != "OPEN" or window.research_session_mode != "LIVE":
            reasons.append("T0_NOT_IN_LIVE_SESSION")
        if capture.session_window != window.session_window or capture.research_session_mode != window.research_session_mode:
            reasons.append("SESSION_RECORD_MISMATCH")
        if capture.observed_expiry < to_ist(t0).date():
            reasons.append("EXPIRY_BEFORE_T0")
    if capture.market_state not in _LIVE_MARKET_STATES:
        reasons.append("MARKET_STATE_NOT_LIVE")
    if observation.source != "LIVE":
        reasons.append("SOURCE_NOT_LIVE")
    if not observation.symbol:
        reasons.append("SYMBOL_MISSING")
    if not capture.underlying_instrument_key:
        reasons.append("UNDERLYING_IDENTITY_MISSING")
    if observation.direction not in ("BULLISH", "BEARISH"):
        reasons.append("DIRECTION_INVALID")
    if not observation.early_stage_state:
        reasons.append("RESEARCH_STATE_MISSING")

    strike: str | None = None
    try:
        if observation.selected_strike is None or Decimal(observation.selected_strike) <= 0:
            raise InvalidOperation
        strike = observation.selected_strike
    except InvalidOperation:
        reasons.append("OPTION_STRIKE_INVALID")
    if observation.selected_right not in ("CE", "PE"):
        reasons.append("OPTION_RIGHT_INVALID")

    availability = observation.evidence_availability
    if availability is None:
        reasons.append("EVIDENCE_AVAILABILITY_MISSING")
    elif availability.state_of(EvidenceClass.OPTIONS_CHAIN) not in _CHAIN_USABLE:
        reasons.append("OPTION_CHAIN_NOT_CURRENT_AT_T0")

    if strike is not None and observation.selected_right in ("CE", "PE") and t0.tzinfo is not None:
        expected = forward_observation_identity(
            symbol=observation.symbol, underlying_instrument_key=capture.underlying_instrument_key,
            right=observation.selected_right, strike=strike, expiry=capture.observed_expiry,
            contract_instrument_key=capture.contract_instrument_key, t0=t0,
        )
        if observation.observation_id != expected:
            reasons.append("IDENTITY_MISMATCH")
    return tuple(reasons)


def assess_forward_capture(
    candidate: RankedCandidate, observation: ResearchObservation, *, persisted_at: datetime,
) -> tuple[ResearchObservation | None, tuple[str, ...]]:
    """Fail closed. `observation` is the one `build_research_observation()`
    already produced from `candidate`; this only adds the forward-capture
    identity/provenance, copied from the same analysis response."""
    response = candidate.response
    visual = response.visual
    pre: list[str] = []
    if response.error is not None:
        pre.append("ANALYSIS_FAILED")
    if visual is None:
        pre.append("ANALYSIS_HAS_NO_VISUAL")
    if response.generated_at is None:
        pre.append("T0_MISSING")
    if response.symbol is not None and response.symbol != observation.symbol:
        pre.append("SCOPE_INCONSISTENT")
    if visual is None or response.generated_at is None or pre:
        return None, tuple(pre)

    chain = visual.option_chain
    expiry = chain.expiry
    if expiry is None:
        pre.append("OBSERVED_EXPIRY_MISSING")
    contract_key: str | None = None
    row = next((r for r in chain.rows if r.strike == candidate.contract.strike), None)
    leg = None if row is None else (row.call if candidate.contract.right == "CE" else row.put)
    if leg is None:
        pre.append("CONTRACT_NOT_IN_OBSERVED_CHAIN")
    else:
        contract_key = leg.instrument_key
    if expiry is None or pre:
        return None, tuple(pre)

    window = classify_session_window(observation.generated_at)
    provenance = ForwardCaptureProvenance(
        capture_policy_version=CAPTURE_POLICY_VERSION,
        session_window=window.session_window,
        research_session_mode=window.research_session_mode,
        market_state=response.market_state or "UNKNOWN",
        persisted_at=persisted_at,
        underlying_instrument_key=chain.underlying_instrument_key,
        contract_instrument_key=contract_key,
        futures_instrument_key=visual.futures.instrument_key,
        observed_expiry=expiry,
        streams=tuple(
            StreamProvenance(
                stream=s.stream, label=s.label, data_timestamp=s.data_timestamp, retrieved_at=s.retrieved_at, source=s.source,
            )
            for s in visual.freshness.streams if s.stream in _CAPTURED_STREAMS
        ),
    )
    try:
        identity = forward_observation_identity(
            symbol=observation.symbol, underlying_instrument_key=provenance.underlying_instrument_key,
            right=candidate.contract.right, strike=str(candidate.contract.strike), expiry=expiry,
            contract_instrument_key=contract_key, t0=observation.generated_at,
        )
        forward = ResearchObservation.model_validate({
            **observation.model_dump(), "observation_id": identity, "forward_capture": provenance.model_dump(),
        })
    except (ValueError, InvalidOperation) as exc:
        return None, (f"OBSERVATION_INVALID: {type(exc).__name__}",)
    problems = verify_forward_observation(forward)
    if problems:
        return None, problems
    return forward, ()


class CaptureStatus(str, Enum):
    CAPTURED = "CAPTURED"
    DUPLICATE = "DUPLICATE"  # the same T0 identity was already persisted; nothing written
    INELIGIBLE = "INELIGIBLE"  # failed closed; nothing written
    ERROR = "ERROR"  # infrastructure failure; nothing changes about the analysis


@dataclass(frozen=True)
class CaptureOutcome:
    symbol: str
    status: CaptureStatus
    observation_id: str | None
    reasons: tuple[str, ...] = ()


async def capture_forward_observation(
    repository: ResearchOutcomeRepository, candidate: RankedCandidate, observation: ResearchObservation, *,
    persisted_at: datetime,
) -> CaptureOutcome:
    """Assess, then persist exactly once. Never raises: an infrastructure error
    is reported as `ERROR` and leaves the analysis result untouched."""
    try:
        forward, reasons = assess_forward_capture(candidate, observation, persisted_at=persisted_at)
        if forward is None:
            return CaptureOutcome(candidate.symbol, CaptureStatus.INELIGIBLE, None, reasons)
        written = await repository.save_observation_once(forward)
        return CaptureOutcome(
            candidate.symbol, CaptureStatus.CAPTURED if written else CaptureStatus.DUPLICATE, forward.observation_id,
        )
    except Exception as exc:  # noqa: BLE001 -- capture is infrastructure; it must never break or alter the research result
        _LOG.warning("forward capture failed for %s: %s", candidate.symbol, exc)
        return CaptureOutcome(candidate.symbol, CaptureStatus.ERROR, None, (f"{type(exc).__name__}",))


class ForwardCaptureView(BaseModel):
    """Read-only inspection of one persisted observation's capture record and
    its integrity verdict. Purely a view: nothing here is written back."""

    observation_id: str
    capture_kind: str
    t0: datetime
    verified: bool
    problems: list[str]
    provenance: ForwardCaptureProvenance | None
    evidence_availability: EvidenceAvailability | None


def build_forward_capture_view(observation: ResearchObservation) -> ForwardCaptureView:
    problems = verify_forward_observation(observation)
    return ForwardCaptureView(
        observation_id=observation.observation_id, capture_kind=observation_capture_kind(observation),
        t0=observation.generated_at, verified=not problems, problems=list(problems),
        provenance=observation.forward_capture, evidence_availability=observation.evidence_availability,
    )
