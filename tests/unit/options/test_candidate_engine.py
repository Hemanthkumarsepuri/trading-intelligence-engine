from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.data.normalization.base import DefaultNormalizer
from app.data.providers.base import RawOptionChain, RawOptionLeg
from app.domain.market.models import OptionChainSnapshot, OptionRight
from app.domain.options.candidate_engine import generate_candidates
from app.domain.options.evidence_matrix import EvidenceDirection
from app.domain.options.liquidity import LiquidityGrade

UNDERLYING = "NIFTY"
EXPIRY = date(2026, 9, 24)
RECEIVED_AT = datetime(2026, 8, 29, 10, 0, tzinfo=UTC)
AS_OF = RECEIVED_AT
_MAX_AGE = timedelta(seconds=60)


def _leg(*, right: OptionRight, bid: float, ask: float, volume: int, oi: int) -> RawOptionLeg:
    return RawOptionLeg(right=right, last_price=(bid + ask) / 2, bid_price=bid, ask_price=ask, volume=volume, open_interest=oi)


def _snapshot(strikes: dict[float, list[RawOptionLeg]], *, spot: float = 24800.0) -> OptionChainSnapshot:
    raw = RawOptionChain(underlying=UNDERLYING, expiry=EXPIRY, underlying_last_price=spot, strikes=strikes)
    return DefaultNormalizer(provider_name="test").normalize_option_chain(raw, received_at=RECEIVED_AT)


_LIQUID_STRIKES = {
    24700.0: [
        _leg(right=OptionRight.CE, bid=150, ask=153, volume=6000, oi=60000),
        _leg(right=OptionRight.PE, bid=40, ask=41, volume=6000, oi=60000),
    ],
    24800.0: [
        _leg(right=OptionRight.CE, bid=100, ask=102, volume=8000, oi=80000),
        _leg(right=OptionRight.PE, bid=90, ask=92, volume=8000, oi=80000),
    ],
    24900.0: [
        _leg(right=OptionRight.CE, bid=60, ask=62, volume=7000, oi=70000),
        _leg(right=OptionRight.PE, bid=140, ask=143, volume=7000, oi=70000),
    ],
}


def test_neutral_bias_produces_no_candidates() -> None:
    result = generate_candidates(
        _snapshot(_LIQUID_STRIKES), bias=EvidenceDirection.NEUTRAL, supporting_evidence=[], contradicting_evidence=[],
        strikes_each_side=1, as_of=AS_OF, max_quote_age=_MAX_AGE, min_liquidity_grade=LiquidityGrade.GOOD,
        nearest_support_strike=None, nearest_resistance_strike=None,
    )
    assert result == []


def test_unknown_bias_produces_no_candidates() -> None:
    result = generate_candidates(
        _snapshot(_LIQUID_STRIKES), bias=EvidenceDirection.UNKNOWN, supporting_evidence=[], contradicting_evidence=[],
        strikes_each_side=1, as_of=AS_OF, max_quote_age=_MAX_AGE, min_liquidity_grade=LiquidityGrade.GOOD,
        nearest_support_strike=None, nearest_resistance_strike=None,
    )
    assert result == []


def test_bullish_bias_produces_only_ce_candidates() -> None:
    result = generate_candidates(
        _snapshot(_LIQUID_STRIKES), bias=EvidenceDirection.BULLISH, supporting_evidence=["M15 trend bullish"],
        contradicting_evidence=[], strikes_each_side=1, as_of=AS_OF, max_quote_age=_MAX_AGE,
        min_liquidity_grade=LiquidityGrade.GOOD, nearest_support_strike=Decimal("24700"), nearest_resistance_strike=None,
    )
    assert result
    assert all(c.right == OptionRight.CE for c in result)


def test_bearish_bias_produces_only_pe_candidates() -> None:
    result = generate_candidates(
        _snapshot(_LIQUID_STRIKES), bias=EvidenceDirection.BEARISH, supporting_evidence=[], contradicting_evidence=[],
        strikes_each_side=1, as_of=AS_OF, max_quote_age=_MAX_AGE, min_liquidity_grade=LiquidityGrade.GOOD,
        nearest_support_strike=None, nearest_resistance_strike=Decimal("24900"),
    )
    assert result
    assert all(c.right == OptionRight.PE for c in result)


def test_illiquid_strike_is_excluded_not_merely_downranked() -> None:
    strikes = {
        24800.0: [
            _leg(right=OptionRight.CE, bid=100, ask=102, volume=8000, oi=80000),  # good
            _leg(right=OptionRight.PE, bid=90, ask=92, volume=8000, oi=80000),
        ],
        24900.0: [
            _leg(right=OptionRight.CE, bid=1, ask=5, volume=2, oi=10),  # wide spread, illiquid
            _leg(right=OptionRight.PE, bid=140, ask=143, volume=7000, oi=70000),
        ],
    }
    result = generate_candidates(
        _snapshot(strikes), bias=EvidenceDirection.BULLISH, supporting_evidence=[], contradicting_evidence=[],
        strikes_each_side=1, as_of=AS_OF, max_quote_age=_MAX_AGE, min_liquidity_grade=LiquidityGrade.GOOD,
        nearest_support_strike=None, nearest_resistance_strike=None,
    )
    assert all(c.strike != Decimal("24900.0") for c in result)


def test_no_candidates_when_everything_illiquid() -> None:
    strikes = {24800.0: [_leg(right=OptionRight.CE, bid=1, ask=5, volume=1, oi=5)]}
    result = generate_candidates(
        _snapshot(strikes), bias=EvidenceDirection.BULLISH, supporting_evidence=[], contradicting_evidence=[],
        strikes_each_side=1, as_of=AS_OF, max_quote_age=_MAX_AGE, min_liquidity_grade=LiquidityGrade.GOOD,
        nearest_support_strike=None, nearest_resistance_strike=None,
    )
    assert result == []


def test_candidates_ranked_by_liquidity_then_distance_from_atm() -> None:
    result = generate_candidates(
        _snapshot(_LIQUID_STRIKES, spot=24800.0), bias=EvidenceDirection.BULLISH, supporting_evidence=[],
        contradicting_evidence=[], strikes_each_side=1, as_of=AS_OF, max_quote_age=_MAX_AGE,
        min_liquidity_grade=LiquidityGrade.POOR, nearest_support_strike=None, nearest_resistance_strike=None,
    )
    assert result[0].is_atm  # ATM strike (24800) should rank at or near the top given similar liquidity


def test_invalidation_condition_present_for_every_candidate() -> None:
    result = generate_candidates(
        _snapshot(_LIQUID_STRIKES), bias=EvidenceDirection.BULLISH, supporting_evidence=[], contradicting_evidence=[],
        strikes_each_side=1, as_of=AS_OF, max_quote_age=_MAX_AGE, min_liquidity_grade=LiquidityGrade.GOOD,
        nearest_support_strike=Decimal("24700"), nearest_resistance_strike=None,
    )
    assert all(c.invalidation_condition for c in result)
    assert all("24700" in c.invalidation_condition for c in result)
