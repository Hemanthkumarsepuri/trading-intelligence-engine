"""Sprint 3.2 -- Historical Validation Contract: pure unit tests for
`assess_evidence_availability()` and the `EvidenceAvailability` model."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from app.domain.options.evidence_availability import (
    AvailabilityState,
    EvidenceAvailability,
    EvidenceClass,
    assess_evidence_availability,
)
from app.domain.options.evidence_matrix import (
    VOTING_GROUPS,
    EvidenceDirection,
    EvidenceGroup,
    EvidenceMatrix,
    EvidenceRow,
    row_iv_skew,
)
from app.domain.options.iv_context import AtmIvSummary
from app.domain.options.liquidity import LiquidityGrade

_LIVE: dict[str, Any] = {
    "price_history_sufficient": True, "candles_present": True, "candles_are_current": True,
    "market_context_present": True,
    "chain_present": True, "chain_is_current": True, "provider_has_chain_history": True,
    "futures_present": True, "futures_are_current": True, "provider_has_futures_history": True,
    "news_fetch_failed": False, "provider_has_news_history": True,
    "matrix": None,
}

_PRICE_ONLY_REPLAY: dict[str, Any] = {
    **_LIVE,
    "market_context_present": False,
    "chain_present": False, "provider_has_chain_history": False,
    "futures_present": False, "provider_has_futures_history": False,
    "news_fetch_failed": True, "provider_has_news_history": False,
}


def _assess(**overrides: Any) -> EvidenceAvailability:
    return assess_evidence_availability(**{**_LIVE, **overrides})


def _row(group: EvidenceGroup, direction: EvidenceDirection) -> EvidenceRow:
    return EvidenceRow(f"{group.value}-row", group, direction, "")


def test_price_only_replay_records_price_available_and_everything_else_unavailable() -> None:
    a = assess_evidence_availability(**_PRICE_ONLY_REPLAY)
    assert a.as_mapping() == {
        "PRICE": "AVAILABLE", "MARKET_CONTEXT": "UNAVAILABLE", "OPTIONS_CHAIN": "UNAVAILABLE",
        "FUTURES": "UNAVAILABLE", "NEWS": "UNAVAILABLE",
    }


def test_one_entry_per_evidence_class_in_enum_order() -> None:
    a = assess_evidence_availability(**_PRICE_ONLY_REPLAY)
    assert [e.evidence_class for e in a.classes] == list(EvidenceClass)


def test_market_context_can_be_available_in_replay() -> None:
    a = assess_evidence_availability(**{**_PRICE_ONLY_REPLAY, "market_context_present": True})
    assert a.state_of(EvidenceClass.MARKET_CONTEXT) == AvailabilityState.AVAILABLE
    assert a.state_of(EvidenceClass.OPTIONS_CHAIN) == AvailabilityState.UNAVAILABLE


def test_stale_and_insufficient_are_distinct_from_unavailable() -> None:
    stale = _assess(candles_are_current=False, chain_is_current=False, futures_are_current=False)
    assert stale.state_of(EvidenceClass.PRICE) == AvailabilityState.STALE
    assert stale.state_of(EvidenceClass.OPTIONS_CHAIN) == AvailabilityState.STALE
    assert stale.state_of(EvidenceClass.FUTURES) == AvailabilityState.STALE
    insufficient = _assess(price_history_sufficient=False)
    assert insufficient.state_of(EvidenceClass.PRICE) == AvailabilityState.INSUFFICIENT


def test_news_fetched_but_empty_is_available_not_unavailable() -> None:
    """"Evaluated, nothing found" is a fact about the evidence; only a
    failed/absent fetch is UNAVAILABLE."""
    assert _assess().state_of(EvidenceClass.NEWS) == AvailabilityState.AVAILABLE
    assert _assess(news_fetch_failed=True).state_of(EvidenceClass.NEWS) == AvailabilityState.UNAVAILABLE


def test_reason_distinguishes_structural_absence_from_a_missing_live_snapshot() -> None:
    structural = assess_evidence_availability(**_PRICE_ONLY_REPLAY)
    live_gap = _assess(chain_present=False)
    chain_reason = {e.evidence_class: e.reason for e in structural.classes}[EvidenceClass.OPTIONS_CHAIN]
    live_reason = {e.evidence_class: e.reason for e in live_gap.classes}[EvidenceClass.OPTIONS_CHAIN]
    assert "provider has no option-chain history" in chain_reason
    assert "provider has no option-chain history" not in live_reason


def test_conflicting_only_when_available_evidence_disagrees_within_its_group() -> None:
    conflicting = EvidenceMatrix(rows=[
        _row(EvidenceGroup.OPTIONS_OI, EvidenceDirection.BULLISH),
        _row(EvidenceGroup.OPTIONS_OI, EvidenceDirection.BEARISH),
    ])
    a = _assess(matrix=conflicting)
    assert a.state_of(EvidenceClass.OPTIONS_CHAIN) == AvailabilityState.CONFLICTING
    assert a.state_of(EvidenceClass.PRICE) == AvailabilityState.AVAILABLE
    # A neutral verdict is still AVAILABLE evidence, not a conflict.
    neutral = EvidenceMatrix(rows=[_row(EvidenceGroup.OPTIONS_OI, EvidenceDirection.NEUTRAL)])
    assert _assess(matrix=neutral).state_of(EvidenceClass.OPTIONS_CHAIN) == AvailabilityState.AVAILABLE


def test_unavailable_class_is_never_upgraded_to_conflicting_by_the_matrix() -> None:
    conflicting = EvidenceMatrix(rows=[
        _row(EvidenceGroup.OPTIONS_OI, EvidenceDirection.BULLISH),
        _row(EvidenceGroup.OPTIONS_OI, EvidenceDirection.BEARISH),
    ])
    a = assess_evidence_availability(**{**_PRICE_ONLY_REPLAY, "matrix": conflicting})
    assert a.state_of(EvidenceClass.OPTIONS_CHAIN) == AvailabilityState.UNAVAILABLE
    assert a.contradicted_by(conflicting) == [EvidenceClass.OPTIONS_CHAIN]


def test_sprint_3_1_iv_skew_cannot_make_the_chain_conflicting() -> None:
    """M-1 stays fixed: OPTIONS_IV never votes, so an IV-skew lean opposing
    OI evidence must not flip OPTIONS_CHAIN to CONFLICTING."""
    skew = row_iv_skew(
        AtmIvSummary(
            atm_strike=Decimal("24800"), atm_ce_iv=Decimal("12"), atm_pe_iv=Decimal("16"),
            chain_iv=Decimal("14"), ce_pe_skew=Decimal("-4"),
        ),
        meaningful_skew=Decimal("1.0"),
        ce_liquidity_grade=LiquidityGrade.EXCELLENT, pe_liquidity_grade=LiquidityGrade.GOOD,
    )
    assert skew.group == EvidenceGroup.OPTIONS_IV and skew.direction == EvidenceDirection.BEARISH
    matrix = EvidenceMatrix(rows=[_row(EvidenceGroup.OPTIONS_OI, EvidenceDirection.BULLISH), skew])
    assert _assess(matrix=matrix).state_of(EvidenceClass.OPTIONS_CHAIN) == AvailabilityState.AVAILABLE


def test_unavailable_evidence_is_not_a_voting_group_and_never_directional() -> None:
    """An evidence class is not an EvidenceGroup, and an availability
    record contributes nothing to the matrix: a matrix built only from the
    rows an unavailable stream produces (UNKNOWN) has no directional vote
    and no invented NEUTRAL."""
    # The Sprint 3.1 voting policy is untouched: availability adds no voting group.
    assert VOTING_GROUPS == {
        EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceGroup.FUTURES, EvidenceGroup.OPTIONS_OI,
        EvidenceGroup.GLOBAL, EvidenceGroup.RELATIVE_STRENGTH,
    }
    assert EvidenceGroup.OPTIONS_IV not in VOTING_GROUPS
    matrix = EvidenceMatrix(rows=[
        _row(EvidenceGroup.OPTIONS_OI, EvidenceDirection.UNKNOWN),
        _row(EvidenceGroup.FUTURES, EvidenceDirection.UNKNOWN),
        _row(EvidenceGroup.NEWS_EVENT, EvidenceDirection.UNKNOWN),
    ])
    a = assess_evidence_availability(**{**_PRICE_ONLY_REPLAY, "matrix": matrix})
    assert a.contradicted_by(matrix) == []
    assert matrix.supporting_group_count(EvidenceDirection.BULLISH) == 0
    assert matrix.supporting_group_count(EvidenceDirection.BEARISH) == 0
    assert all(r.direction == EvidenceDirection.UNKNOWN for r in matrix.rows)
    assert a.state_of(EvidenceClass.OPTIONS_CHAIN) not in (AvailabilityState.AVAILABLE, AvailabilityState.CONFLICTING)


def test_assessment_is_deterministic() -> None:
    assert assess_evidence_availability(**_PRICE_ONLY_REPLAY) == assess_evidence_availability(**_PRICE_ONLY_REPLAY)
    assert (
        assess_evidence_availability(**_PRICE_ONLY_REPLAY).model_dump_json()
        == assess_evidence_availability(**_PRICE_ONLY_REPLAY).model_dump_json()
    )


def test_contract_is_immutable() -> None:
    a = assess_evidence_availability(**_PRICE_ONLY_REPLAY)
    with pytest.raises(ValidationError):
        a.classes = ()  # type: ignore[misc]
    with pytest.raises(ValidationError):
        a.classes[0].state = AvailabilityState.UNAVAILABLE  # type: ignore[misc]


def test_serialization_round_trip_preserves_the_contract() -> None:
    a = assess_evidence_availability(**_PRICE_ONLY_REPLAY)
    assert EvidenceAvailability.model_validate_json(a.model_dump_json()) == a
    assert EvidenceAvailability.model_validate(a.model_dump(mode="json")) == a
