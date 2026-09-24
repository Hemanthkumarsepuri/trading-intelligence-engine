"""Sprint 3.2 -- the Historical Validation Contract: a machine-readable
record of which EVIDENCE CLASSES were actually available when an analysis
(and so any research observation built from it) was generated.

This is a description of AVAILABILITY, never of direction. It exists so a
replayed observation can say "the option chain was never available at this
instant" instead of leaving that to be inferred from an UNKNOWN row, a
`derivatives_evidence_available=False` flag, or free-text warnings -- and so
an absent stream can never be mistaken for one that was evaluated:

    UNAVAILABLE   the stream produced nothing for this instant (provider has
                  no such history, fetch failed, no contract). NOT neutral,
                  NOT "no change", NOT bullish/bearish, NOT a confirmation
                  or invalidation.
    INSUFFICIENT  data exists but too little to evaluate (e.g. too few M15
                  bars for EMA/VWAP).
    STALE         data exists but failed its freshness requirement and is
                  withheld from voting (same gates the evidence matrix
                  already applies -- this reads them, never re-derives them).
    AVAILABLE     current, evaluated evidence. A NEUTRAL verdict is still
                  AVAILABLE: "evaluated and showed no direction" is a fact
                  about the evidence, not about its availability.
    CONFLICTING   current and evaluated, but the class's own voting group
                  disagrees internally (see `EvidenceMatrix.group_verdict`).

Not a second source of truth: every state is derived, per analysis, from the
facts the pipeline already computed (candle currency, chain snapshot,
futures quote, news fetch outcome, the matrix's group verdicts). Nothing
here reads a clock, a live provider, or any data later than the analysis
`as_of`; identical inputs always yield an identical contract. An evidence
class is NOT an `EvidenceGroup` and never votes -- in particular an
UNAVAILABLE class is not a voting group. `OPTIONS_CHAIN` maps to the
`OPTIONS_OI` voting group only; `OPTIONS_IV` stays non-voting (Sprint 3.1).
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from app.domain.options.evidence_matrix import EvidenceGroup, EvidenceMatrix, GroupVerdict


class EvidenceClass(str, Enum):
    PRICE = "PRICE"  # underlying M15 candles/quote -> EMA, VWAP, regime
    MARKET_CONTEXT = "MARKET_CONTEXT"  # NIFTY / index / VIX day-change context
    OPTIONS_CHAIN = "OPTIONS_CHAIN"  # option-chain OI/volume/IV snapshot
    FUTURES = "FUTURES"  # futures quote / OI / basis
    NEWS = "NEWS"  # instrument-scoped headlines


class AvailabilityState(str, Enum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    INSUFFICIENT = "INSUFFICIENT"
    STALE = "STALE"
    CONFLICTING = "CONFLICTING"


class EvidenceClassAvailability(BaseModel):
    model_config = {"frozen": True}

    evidence_class: EvidenceClass
    state: AvailabilityState
    # Short, deterministic explanation (never an embedded exception text or
    # timestamp, so identical inputs serialize identically).
    reason: str


class EvidenceAvailability(BaseModel):
    """Immutable, nested provenance object carried by a research
    observation. One entry per `EvidenceClass`, in enum order."""

    model_config = {"frozen": True}

    classes: tuple[EvidenceClassAvailability, ...]

    def state_of(self, evidence_class: EvidenceClass) -> AvailabilityState:
        for entry in self.classes:
            if entry.evidence_class == evidence_class:
                return entry.state
        raise KeyError(evidence_class)

    def as_mapping(self) -> dict[str, str]:
        return {e.evidence_class.value: e.state.value for e in self.classes}

    def contradicted_by(self, matrix: EvidenceMatrix) -> list[EvidenceClass]:
        """Classes whose availability says no directional vote is possible
        while the matrix nevertheless shows a directional verdict for the
        class's group -- an invariant violation, expected to be empty."""
        offenders: list[EvidenceClass] = []
        for entry in self.classes:
            group = _VOTING_GROUP.get(entry.evidence_class)
            if group is None or entry.state in (AvailabilityState.AVAILABLE, AvailabilityState.CONFLICTING):
                continue
            if matrix.group_verdict(group) in (GroupVerdict.BULLISH, GroupVerdict.BEARISH, GroupVerdict.CONFLICTING):
                offenders.append(entry.evidence_class)
        return offenders


# The voting group each evidence class is judged against for CONFLICTING.
# NEWS is never directional, so it has no group to conflict within.
_VOTING_GROUP: dict[EvidenceClass, EvidenceGroup] = {
    EvidenceClass.PRICE: EvidenceGroup.UNDERLYING_PRICE_STRUCTURE,
    EvidenceClass.MARKET_CONTEXT: EvidenceGroup.GLOBAL,
    EvidenceClass.OPTIONS_CHAIN: EvidenceGroup.OPTIONS_OI,
    EvidenceClass.FUTURES: EvidenceGroup.FUTURES,
}


def _entry(
    evidence_class: EvidenceClass, state: AvailabilityState, reason: str, matrix: EvidenceMatrix | None,
) -> EvidenceClassAvailability:
    if state == AvailabilityState.AVAILABLE and matrix is not None:
        group = _VOTING_GROUP.get(evidence_class)
        if group is not None and matrix.group_verdict(group) == GroupVerdict.CONFLICTING:
            return EvidenceClassAvailability(
                evidence_class=evidence_class, state=AvailabilityState.CONFLICTING,
                reason="current evidence whose own rows disagree",
            )
    return EvidenceClassAvailability(evidence_class=evidence_class, state=state, reason=reason)


def assess_evidence_availability(
    *,
    price_history_sufficient: bool,
    candles_present: bool,
    candles_are_current: bool,
    market_context_present: bool,
    chain_present: bool,
    chain_is_current: bool,
    provider_has_chain_history: bool,
    futures_present: bool,
    futures_are_current: bool,
    provider_has_futures_history: bool,
    news_fetch_failed: bool,
    provider_has_news_history: bool,
    matrix: EvidenceMatrix | None,
) -> EvidenceAvailability:
    """Pure. Inputs are facts the analysis already established at `as_of`;
    the `provider_has_*` flags are the provider's own declared capabilities
    (`ProviderCapabilities`), used only to word the reason, never to
    override an observed fact."""
    if not candles_present or not price_history_sufficient:
        price = _entry(
            EvidenceClass.PRICE, AvailabilityState.INSUFFICIENT,
            "too little M15 history at this instant to evaluate EMA trend evidence", matrix,
        )
    elif not candles_are_current:
        price = _entry(
            EvidenceClass.PRICE, AvailabilityState.STALE,
            "latest M15 candle predates the current session -- withheld from voting", matrix,
        )
    else:
        price = _entry(EvidenceClass.PRICE, AvailabilityState.AVAILABLE, "current M15 price evidence", matrix)

    if market_context_present:
        context = _entry(EvidenceClass.MARKET_CONTEXT, AvailabilityState.AVAILABLE, "index context quotes available", matrix)
    else:
        context = _entry(
            EvidenceClass.MARKET_CONTEXT, AvailabilityState.UNAVAILABLE, "no index context quotes for this instant", matrix,
        )

    if not chain_present:
        chain = _entry(
            EvidenceClass.OPTIONS_CHAIN, AvailabilityState.UNAVAILABLE,
            "provider has no option-chain history for this instant" if not provider_has_chain_history
            else "no option-chain snapshot for this instant",
            matrix,
        )
    elif not chain_is_current:
        chain = _entry(
            EvidenceClass.OPTIONS_CHAIN, AvailabilityState.STALE,
            "option-chain snapshot failed freshness -- chain evidence withheld from voting", matrix,
        )
    else:
        chain = _entry(EvidenceClass.OPTIONS_CHAIN, AvailabilityState.AVAILABLE, "current option-chain snapshot", matrix)

    if not futures_present:
        futures = _entry(
            EvidenceClass.FUTURES, AvailabilityState.UNAVAILABLE,
            "provider has no futures history for this instant" if not provider_has_futures_history
            else "no futures quote for this instant",
            matrix,
        )
    elif not futures_are_current:
        futures = _entry(
            EvidenceClass.FUTURES, AvailabilityState.STALE,
            "futures quote failed freshness -- futures evidence withheld from voting", matrix,
        )
    else:
        futures = _entry(EvidenceClass.FUTURES, AvailabilityState.AVAILABLE, "current futures quote", matrix)

    if news_fetch_failed:
        news = _entry(
            EvidenceClass.NEWS, AvailabilityState.UNAVAILABLE,
            "provider has no news history for this instant" if not provider_has_news_history else "news fetch failed",
            matrix,
        )
    else:
        news = _entry(EvidenceClass.NEWS, AvailabilityState.AVAILABLE, "news fetched (possibly none found)", matrix)

    return EvidenceAvailability(classes=(price, context, chain, futures, news))
