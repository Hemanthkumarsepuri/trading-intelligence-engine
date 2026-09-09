from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.data.normalization.base import DefaultNormalizer
from app.data.providers.base import RawOptionChain, RawOptionLeg
from app.domain.market.models import OptionChainSnapshot, OptionRight
from app.domain.options.contract_analysis import (
    ContractPreference,
    ContractStructuralQuality,
    assess_contract,
    classify_contract_structural_quality,
    compare_contract_pair,
    compare_contracts,
    summarize_contract_preference,
)
from app.domain.options.support_resistance import Level, LevelKind, LevelStrength

UNDERLYING = "NSE_EQ|KAYNES"
EXPIRY = date(2026, 9, 24)
RECEIVED_AT = datetime(2026, 8, 29, 10, 0, tzinfo=UTC)
AS_OF = RECEIVED_AT


def _leg(
    *, right: OptionRight, ltp: float | None = 30.0, bid: float | None = 29.5, ask: float | None = 30.5,
    oi: int | None = 50000, volume: int | None = 5000, iv: float | None = 18.0, delta: float | None = 0.5,
    theta: float | None = -2.0, gamma: float | None = 0.01, vega: float | None = 1.0,
) -> RawOptionLeg:
    return RawOptionLeg(
        right=right, last_price=ltp, bid_price=bid, ask_price=ask, volume=volume, open_interest=oi,
        previous_open_interest=None, implied_volatility=iv, delta=delta, theta=theta, gamma=gamma, vega=vega,
    )


def _snapshot(strikes: dict[float, list[RawOptionLeg]], *, spot: float = 4000.0) -> OptionChainSnapshot:
    raw = RawOptionChain(underlying=UNDERLYING, expiry=EXPIRY, underlying_last_price=spot, strikes=strikes)
    normalizer = DefaultNormalizer(provider_name="test")
    return normalizer.normalize_option_chain(raw, received_at=RECEIVED_AT)


_STANDARD = {
    3900.0: [_leg(right=OptionRight.CE, ltp=90.0), _leg(right=OptionRight.PE, ltp=5.0)],
    4000.0: [_leg(right=OptionRight.CE, ltp=30.0), _leg(right=OptionRight.PE, ltp=25.0)],
    4100.0: [_leg(right=OptionRight.CE, ltp=8.0), _leg(right=OptionRight.PE, ltp=60.0)],
    4200.0: [_leg(right=OptionRight.CE, ltp=2.0, oi=200, volume=10), _leg(right=OptionRight.PE, ltp=140.0)],
}

_DECAY_KWARGS = {
    "decay_viability_holding_horizon_hours": Decimal("6.25"), "decay_viability_iv_scenario_points": Decimal("1.0"),
    "decay_viability_favorable_min_ratio": Decimal("3.0"), "decay_viability_acceptable_min_ratio": Decimal("1.5"),
    "decay_viability_headwind_min_ratio": Decimal("0.75"),
}


# -- assess_contract ----------------------------------------------------


def test_assess_contract_returns_none_when_strike_not_in_chain() -> None:
    snapshot = _snapshot(_STANDARD)
    result = assess_contract(
        snapshot, strike=Decimal("9999"), right=OptionRight.CE, expiry=EXPIRY, as_of=AS_OF, max_quote_age=timedelta(minutes=5),
        support_levels=[], resistance_levels=[], **_DECAY_KWARGS,
    )
    assert result is None


def test_assess_contract_returns_real_leg_fields() -> None:
    snapshot = _snapshot(_STANDARD)
    result = assess_contract(
        snapshot, strike=Decimal("4000"), right=OptionRight.CE, expiry=EXPIRY, as_of=AS_OF, max_quote_age=timedelta(minutes=5),
        support_levels=[], resistance_levels=[], **_DECAY_KWARGS,
    )
    assert result is not None
    assert result.ltp == Decimal("30.0")
    assert result.strike == Decimal("4000")
    assert result.right == OptionRight.CE
    assert result.decay_viability is not None
    assert result.liquidity is not None


def test_assess_contract_moneyness_classified() -> None:
    snapshot = _snapshot(_STANDARD)  # spot=4000, ATM strike=4000
    otm_call = assess_contract(
        snapshot, strike=Decimal("4100"), right=OptionRight.CE, expiry=EXPIRY, as_of=AS_OF, max_quote_age=timedelta(minutes=5),
        support_levels=[], resistance_levels=[], **_DECAY_KWARGS,
    )
    assert otm_call is not None
    assert otm_call.moneyness is not None and otm_call.moneyness.value == "OTM"


def test_assess_contract_distance_to_levels() -> None:
    snapshot = _snapshot(_STANDARD)
    support = Level(kind=LevelKind.SUPPORT, strike=Decimal("3900"), open_interest=1000, strength=LevelStrength.STRONG, distance_from_spot=Decimal("-100"), distance_from_spot_pct=Decimal("-2.5"), evidence="x")
    result = assess_contract(
        snapshot, strike=Decimal("4000"), right=OptionRight.CE, expiry=EXPIRY, as_of=AS_OF, max_quote_age=timedelta(minutes=5),
        support_levels=[support], resistance_levels=[], **_DECAY_KWARGS,
    )
    assert result is not None
    assert result.distance_to_support_pct == Decimal("-2.5")
    assert result.distance_to_resistance_pct is None


def test_assess_contract_handles_missing_ltp_without_crashing() -> None:
    snapshot = _snapshot({4000.0: [_leg(right=OptionRight.CE, ltp=None, bid=None, ask=None), _leg(right=OptionRight.PE)]})
    result = assess_contract(
        snapshot, strike=Decimal("4000"), right=OptionRight.CE, expiry=EXPIRY, as_of=AS_OF, max_quote_age=timedelta(minutes=5),
        support_levels=[], resistance_levels=[], **_DECAY_KWARGS,
    )
    assert result is not None
    assert result.decay_viability is None
    assert any("decay viability unavailable" in n for n in result.data_quality_notes)


# -- Sprint 7A, Objective 9: intrinsic/time-value decomposition -----------


def test_intrinsic_and_time_value_for_an_atm_call() -> None:
    # spot=4000, strike=4000 -> intrinsic 0, all real premium is time value.
    snapshot = _snapshot(_STANDARD)
    result = assess_contract(
        snapshot, strike=Decimal("4000"), right=OptionRight.CE, expiry=EXPIRY, as_of=AS_OF, max_quote_age=timedelta(minutes=5),
        support_levels=[], resistance_levels=[], **_DECAY_KWARGS,
    )
    assert result is not None
    assert result.intrinsic_value == Decimal("0")
    assert result.time_value == Decimal("30.0")


def test_intrinsic_and_time_value_for_an_itm_call() -> None:
    # spot=4000, strike=4100 PE is ITM (strike above spot for a put);
    # use a real ITM CALL instead: strike below spot.
    snapshot = _snapshot(_STANDARD)
    result = assess_contract(
        snapshot, strike=Decimal("3900"), right=OptionRight.CE, expiry=EXPIRY, as_of=AS_OF, max_quote_age=timedelta(minutes=5),
        support_levels=[], resistance_levels=[], **_DECAY_KWARGS,
    )
    assert result is not None
    assert result.intrinsic_value == Decimal("100")  # 4000 - 3900
    assert result.time_value == Decimal("-10.0")  # ltp 90 - intrinsic 100 -- a real, honestly-flagged suspicious state
    assert any("time value is negative" in n for n in result.data_quality_notes)


def test_intrinsic_and_time_value_for_an_itm_put() -> None:
    # spot=4000, strike=4100 PE -> ITM put: intrinsic = strike - spot = 100.
    snapshot = _snapshot(_STANDARD)
    result = assess_contract(
        snapshot, strike=Decimal("4100"), right=OptionRight.PE, expiry=EXPIRY, as_of=AS_OF, max_quote_age=timedelta(minutes=5),
        support_levels=[], resistance_levels=[], **_DECAY_KWARGS,
    )
    assert result is not None
    assert result.intrinsic_value == Decimal("100")
    assert result.time_value == Decimal("-40.0")  # ltp 60 - intrinsic 100 (same real fixture quirk)


def test_intrinsic_and_time_value_none_without_a_real_spot_or_ltp() -> None:
    snapshot = _snapshot({4000.0: [_leg(right=OptionRight.CE, ltp=None, bid=None, ask=None), _leg(right=OptionRight.PE)]})
    result = assess_contract(
        snapshot, strike=Decimal("4000"), right=OptionRight.CE, expiry=EXPIRY, as_of=AS_OF, max_quote_age=timedelta(minutes=5),
        support_levels=[], resistance_levels=[], **_DECAY_KWARGS,
    )
    assert result is not None
    assert result.intrinsic_value is None
    assert result.time_value is None


# -- compare_contracts (independent of any bias) -----------------------------


def test_compare_contracts_never_reads_a_bias_and_always_uses_requested_right() -> None:
    snapshot = _snapshot(_STANDARD)
    comparison = compare_contracts(
        snapshot, requested_strike=Decimal("4000"), requested_right=OptionRight.CE, strikes_each_side=2,
        expiry=EXPIRY, as_of=AS_OF, max_quote_age=timedelta(minutes=5), support_levels=[], resistance_levels=[], **_DECAY_KWARGS,
    )
    assert comparison.requested is not None
    assert comparison.requested.right == OptionRight.CE
    assert all(alt.right == OptionRight.CE for alt in comparison.alternatives)  # never PE presented as an "alternative"
    assert comparison.requested_strike not in [alt.strike for alt in comparison.alternatives]


def test_compare_contracts_reports_when_requested_not_in_chain() -> None:
    snapshot = _snapshot(_STANDARD)
    comparison = compare_contracts(
        snapshot, requested_strike=Decimal("9999"), requested_right=OptionRight.CE, strikes_each_side=2,
        expiry=EXPIRY, as_of=AS_OF, max_quote_age=timedelta(minutes=5), support_levels=[], resistance_levels=[], **_DECAY_KWARGS,
    )
    assert comparison.requested is None
    assert comparison.alternatives == []
    assert comparison.requested_not_in_chain_detail is not None and "not found" in comparison.requested_not_in_chain_detail


# -- compare_contract_pair / summarize_contract_preference (deterministic dominance) ---


def test_compare_contract_pair_requested_preferable_when_it_dominates() -> None:
    snapshot = _snapshot(_STANDARD)
    requested = assess_contract(snapshot, strike=Decimal("4000"), right=OptionRight.CE, expiry=EXPIRY, as_of=AS_OF, max_quote_age=timedelta(minutes=5), support_levels=[], resistance_levels=[], **_DECAY_KWARGS)
    illiquid_alt = assess_contract(snapshot, strike=Decimal("4200"), right=OptionRight.CE, expiry=EXPIRY, as_of=AS_OF, max_quote_age=timedelta(minutes=5), support_levels=[], resistance_levels=[], **_DECAY_KWARGS)
    assert requested is not None and illiquid_alt is not None
    result = compare_contract_pair(requested, illiquid_alt)
    # 4200 has oi=200/volume=10 -- much less liquid than 4000's oi=50000/volume=5000,
    # and both compute the same DECAY_FAVORABLE verdict here, so liquidity alone
    # decides: requested strictly dominates.
    assert result.preference == ContractPreference.REQUESTED_PREFERABLE


def test_compare_contract_pair_tie_is_no_defensible_preference() -> None:
    snapshot = _snapshot({4000.0: [_leg(right=OptionRight.CE), _leg(right=OptionRight.PE)], 4100.0: [_leg(right=OptionRight.CE), _leg(right=OptionRight.PE)]})
    a = assess_contract(snapshot, strike=Decimal("4000"), right=OptionRight.CE, expiry=EXPIRY, as_of=AS_OF, max_quote_age=timedelta(minutes=5), support_levels=[], resistance_levels=[], **_DECAY_KWARGS)
    b = assess_contract(snapshot, strike=Decimal("4100"), right=OptionRight.CE, expiry=EXPIRY, as_of=AS_OF, max_quote_age=timedelta(minutes=5), support_levels=[], resistance_levels=[], **_DECAY_KWARGS)
    assert a is not None and b is not None
    result = compare_contract_pair(a, b)
    assert result.preference == ContractPreference.NO_DEFENSIBLE_PREFERENCE


def test_summarize_contract_preference_insufficient_data_when_requested_missing() -> None:
    snapshot = _snapshot(_STANDARD)
    comparison = compare_contracts(
        snapshot, requested_strike=Decimal("9999"), requested_right=OptionRight.CE, strikes_each_side=2,
        expiry=EXPIRY, as_of=AS_OF, max_quote_age=timedelta(minutes=5), support_levels=[], resistance_levels=[], **_DECAY_KWARGS,
    )
    preference, detail = summarize_contract_preference(comparison)
    assert preference == ContractPreference.INSUFFICIENT_DATA
    assert detail


def test_summarize_contract_preference_insufficient_data_with_no_alternatives() -> None:
    snapshot = _snapshot({4000.0: [_leg(right=OptionRight.CE), _leg(right=OptionRight.PE)]})
    comparison = compare_contracts(
        snapshot, requested_strike=Decimal("4000"), requested_right=OptionRight.CE, strikes_each_side=2,
        expiry=EXPIRY, as_of=AS_OF, max_quote_age=timedelta(minutes=5), support_levels=[], resistance_levels=[], **_DECAY_KWARGS,
    )
    assert comparison.alternatives == []
    preference, _detail = summarize_contract_preference(comparison)
    assert preference == ContractPreference.INSUFFICIENT_DATA


def test_summarize_contract_preference_never_forces_a_side_on_identical_alternatives() -> None:
    snapshot = _snapshot({
        3900.0: [_leg(right=OptionRight.CE), _leg(right=OptionRight.PE)],
        4000.0: [_leg(right=OptionRight.CE), _leg(right=OptionRight.PE)],
        4100.0: [_leg(right=OptionRight.CE), _leg(right=OptionRight.PE)],
    })
    comparison = compare_contracts(
        snapshot, requested_strike=Decimal("4000"), requested_right=OptionRight.CE, strikes_each_side=1,
        expiry=EXPIRY, as_of=AS_OF, max_quote_age=timedelta(minutes=5), support_levels=[], resistance_levels=[], **_DECAY_KWARGS,
    )
    preference, detail = summarize_contract_preference(comparison)
    assert preference == ContractPreference.NO_DEFENSIBLE_PREFERENCE
    assert "trade-off" in detail


# -- classify_contract_structural_quality ------------------------------------


def test_structural_quality_high_risk_when_illiquid_and_decay_unfavorable() -> None:
    # Deep OTM, tiny premium, wide relative spread, thin OI/volume, and a
    # large theta relative to the tiny premium -- verified directly (not
    # assumed) to compute POOR liquidity + DECAY_UNFAVORABLE.
    snapshot = _snapshot({
        4200.0: [
            _leg(right=OptionRight.CE, ltp=2.0, bid=1.0, ask=3.0, oi=200, volume=10, delta=0.05, theta=-5.0, gamma=0.001, vega=0.2),
            _leg(right=OptionRight.PE, ltp=140.0, bid=139.0, ask=141.0),
        ]
    })
    assessment = assess_contract(
        snapshot, strike=Decimal("4200"), right=OptionRight.CE, expiry=EXPIRY, as_of=AS_OF, max_quote_age=timedelta(minutes=5),
        support_levels=[], resistance_levels=[], **_DECAY_KWARGS,
    )
    assert assessment is not None
    assert assessment.liquidity.grade.value == "poor"
    assert assessment.decay_viability is not None and assessment.decay_viability.verdict.value == "DECAY_UNFAVORABLE"
    assert classify_contract_structural_quality(assessment) == ContractStructuralQuality.HIGH_RISK_STRUCTURE


def test_structural_quality_insufficient_data_when_decay_verdict_itself_is_insufficient() -> None:
    # A real `DecayViabilityAssessment` object whose OWN verdict is
    # INSUFFICIENT_DATA (missing bid/ask) must not be misread as "checked
    # and acceptable" -- this is the exact bug this test guards against.
    snapshot = _snapshot({4200.0: [_leg(right=OptionRight.CE, ltp=2.0, bid=None, ask=None, oi=200, volume=10), _leg(right=OptionRight.PE)]})
    assessment = assess_contract(
        snapshot, strike=Decimal("4200"), right=OptionRight.CE, expiry=EXPIRY, as_of=AS_OF, max_quote_age=timedelta(minutes=5),
        support_levels=[], resistance_levels=[], **_DECAY_KWARGS,
    )
    assert assessment is not None
    assert assessment.decay_viability is not None  # a real object, not None...
    assert assessment.decay_viability.verdict.value == "INSUFFICIENT_DATA"  # ...but with an insufficient-data verdict
    assert classify_contract_structural_quality(assessment) == ContractStructuralQuality.INSUFFICIENT_DATA


def test_structural_quality_insufficient_data_without_decay_viability() -> None:
    snapshot = _snapshot({4000.0: [_leg(right=OptionRight.CE, ltp=None, bid=None, ask=None), _leg(right=OptionRight.PE)]})
    assessment = assess_contract(
        snapshot, strike=Decimal("4000"), right=OptionRight.CE, expiry=EXPIRY, as_of=AS_OF, max_quote_age=timedelta(minutes=5),
        support_levels=[], resistance_levels=[], **_DECAY_KWARGS,
    )
    assert assessment is not None
    assert classify_contract_structural_quality(assessment) == ContractStructuralQuality.INSUFFICIENT_DATA


def test_structural_quality_never_reads_a_probability_field() -> None:
    # Structural guarantee: the enum vocabulary itself contains no
    # probability/percentage-flavored member.
    assert {m.value for m in ContractStructuralQuality} == {"ACCEPTABLE", "HIGH_RISK_STRUCTURE", "INSUFFICIENT_DATA"}
