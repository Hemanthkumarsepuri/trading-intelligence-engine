from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.domain.market.models import OptionRight
from app.domain.options.decay_viability import (
    DecayViabilityAssessment,
    DecayViabilityVerdict,
    assess_decay_viability,
)

AS_OF = datetime(2026, 8, 29, 10, 0, tzinfo=UTC)
_HORIZON = Decimal("6.25")  # one NSE trading session
_IV_STEP = Decimal("1.0")
_FAVORABLE = Decimal("3.0")
_ACCEPTABLE = Decimal("1.5")
_HEADWIND = Decimal("0.75")


def _assess(
    *, spot: Decimal | None = Decimal("100"), iv: Decimal | None = Decimal("20"), delta: Decimal | None = Decimal("0.5"),
    gamma: Decimal | None = Decimal("0.02"), vega: Decimal | None = Decimal("0.15"), theta: Decimal | None = Decimal("-1.0"),
    ltp: Decimal | None = Decimal("9.75"), bid: Decimal | None = Decimal("9.5"), ask: Decimal | None = Decimal("10.0"),
    expiry: date = date(2026, 9, 29), horizon: Decimal = _HORIZON,
) -> DecayViabilityAssessment:
    return assess_decay_viability(
        strike=Decimal("100"), right=OptionRight.CE, spot=spot, expiry=expiry, as_of=AS_OF,
        implied_volatility=iv, delta=delta, gamma=gamma, vega=vega, theta=theta, ltp=ltp, bid=bid, ask=ask,
        holding_horizon_hours=horizon, iv_scenario_points=_IV_STEP, favorable_min_ratio=_FAVORABLE,
        acceptable_min_ratio=_ACCEPTABLE, headwind_min_ratio=_HEADWIND,
    )


# -- insufficient-data propagation -------------------------------------------


def test_insufficient_data_when_iv_missing() -> None:
    assert _assess(iv=None).verdict == DecayViabilityVerdict.INSUFFICIENT_DATA


def test_insufficient_data_when_spot_missing() -> None:
    assert _assess(spot=None).verdict == DecayViabilityVerdict.INSUFFICIENT_DATA


def test_insufficient_data_when_delta_missing() -> None:
    assert _assess(delta=None).verdict == DecayViabilityVerdict.INSUFFICIENT_DATA


def test_insufficient_data_when_bid_ask_missing() -> None:
    assert _assess(bid=None).verdict == DecayViabilityVerdict.INSUFFICIENT_DATA
    assert _assess(ask=None).verdict == DecayViabilityVerdict.INSUFFICIENT_DATA


def test_gamma_and_vega_are_optional_not_required() -> None:
    # Missing gamma/vega must still allow a computed (non-INSUFFICIENT_DATA)
    # result -- only spot/IV/delta/theta/bid/ask are load-bearing.
    result = _assess(gamma=None, vega=None)
    assert result.verdict != DecayViabilityVerdict.INSUFFICIENT_DATA
    assert result.iv_up_scenario_premium_effect is None
    assert result.iv_down_scenario_premium_effect is None


# -- zero/negative/invalid values --------------------------------------------


def test_dte_never_negative_for_an_expired_contract() -> None:
    result = _assess(expiry=date(2026, 8, 1))  # before AS_OF's date
    assert result.dte_days == Decimal(0)


def test_zero_dte_still_has_a_real_nonzero_horizon_move() -> None:
    # A 0-DTE (expiring-today) option still has real remaining trading
    # time within the horizon -- DTE is not the same thing as "time left
    # in the holding window," so expected_move_over_horizon (which
    # depends only on IV/spot/horizon, not DTE -- that's the whole point
    # of the fix) must NOT collapse to zero just because DTE is zero.
    result = _assess(expiry=date(2026, 8, 29))  # same date as AS_OF
    assert result.dte_days == Decimal(0)
    assert result.expected_move_over_horizon is not None and result.expected_move_over_horizon > 0


# -- the corrected time-horizon-consistency fix ------------------------------


def test_expected_move_uses_the_holding_horizon_not_full_dte() -> None:
    # Same IV/spot, wildly different DTE (3d vs 90d) -- if the bug were
    # still present, these would differ (move scales with sqrt(DTE)).
    # With the fix, the expected move is computed over the SAME horizon
    # for both, so it must be identical regardless of DTE.
    near = _assess(expiry=date(2026, 9, 1))  # 3 DTE
    far = _assess(expiry=date(2026, 11, 27))  # ~90 DTE
    assert near.expected_move_over_horizon == far.expected_move_over_horizon


def test_ratio_does_not_scale_with_dte_alone() -> None:
    # This is the exact real-world case that exposed the original bug:
    # RELIANCE-like (31 DTE) vs NIFTY-like (3 DTE) with otherwise-similar
    # inputs must no longer show an order-of-magnitude ratio gap driven
    # purely by DTE.
    near = _assess(expiry=date(2026, 9, 1))
    far = _assess(expiry=date(2026, 9, 29))
    assert near.viability_ratio is not None and far.viability_ratio is not None
    assert near.viability_ratio == far.viability_ratio  # theta/spread/IV/delta identical -> ratio identical


# -- premium-relative metrics (Phase 7) --------------------------------------


def test_theta_to_premium_pct_computed_when_premium_available() -> None:
    result = _assess(theta=Decimal("-2.0"), ltp=Decimal("10.0"))
    assert result.theta_to_premium_pct_per_hour is not None
    assert result.theta_to_premium_pct_per_hour > 0


def test_premium_reference_falls_back_to_bid_ask_mid_when_ltp_unavailable() -> None:
    result = _assess(ltp=None, bid=Decimal("9.0"), ask=Decimal("11.0"))
    assert result.premium_reference == Decimal("10.0")


def test_low_premium_yields_high_theta_to_premium_pct() -> None:
    cheap = _assess(theta=Decimal("-1.0"), ltp=Decimal("1.0"))
    expensive = _assess(theta=Decimal("-1.0"), ltp=Decimal("100.0"))
    assert cheap.theta_to_premium_pct_per_hour is not None and expensive.theta_to_premium_pct_per_hour is not None
    assert cheap.theta_to_premium_pct_per_hour > expensive.theta_to_premium_pct_per_hour


# -- required underlying move (Phase 6, renamed -- see Final Correctness
# Pass: this was previously misnamed "breakeven_underlying_move", which
# implied the standard contractual expiry breakeven when it is actually a
# model-estimated cost-coverage figure) --------------------------------


def test_required_move_is_cost_over_delta() -> None:
    result = _assess()
    assert result.total_modeled_cost is not None and result.required_underlying_move is not None
    assert result.required_underlying_move == result.total_modeled_cost / abs(Decimal("0.5"))


def test_wide_spread_increases_required_move() -> None:
    tight = _assess(bid=Decimal("9.9"), ask=Decimal("10.0"))
    wide = _assess(bid=Decimal("8.0"), ask=Decimal("12.0"))
    assert tight.required_underlying_move is not None and wide.required_underlying_move is not None
    assert wide.required_underlying_move > tight.required_underlying_move


def test_high_theta_increases_required_move() -> None:
    small_theta = _assess(theta=Decimal("-0.1"))
    large_theta = _assess(theta=Decimal("-5.0"))
    assert small_theta.required_underlying_move is not None and large_theta.required_underlying_move is not None
    assert large_theta.required_underlying_move > small_theta.required_underlying_move


# -- genuine contractual expiry breakeven (Final Correctness Pass) -----
# Real GAIL UAT case that exposed the naming defect:
#   CE 170, LTP 4.54, delta 0.602 -> old code reported "breakeven=0.25 pts"
#   PE 170, LTP 2.47, delta -0.398 -> old code reported "breakeven=0.23 pts"
# Neither is close to the real contractual breakeven (174.54 / 167.53) --
# confirming those were never breakeven values at all, just mislabeled.


def test_contractual_breakeven_for_a_call_is_strike_plus_premium() -> None:
    result = _assess(ltp=Decimal("4.54"))  # strike is fixed at 100 in _assess()
    assert result.contractual_expiry_breakeven == Decimal("100") + Decimal("4.54")


def test_contractual_breakeven_for_a_put_is_strike_minus_premium() -> None:
    result = assess_decay_viability(
        strike=Decimal("170"), right=OptionRight.PE, spot=Decimal("171.10"), expiry=date(2026, 9, 29), as_of=AS_OF,
        implied_volatility=Decimal("20"), delta=Decimal("-0.398"), gamma=Decimal("0.02"), vega=Decimal("0.15"),
        theta=Decimal("-1.0"), ltp=Decimal("2.47"), bid=Decimal("2.40"), ask=Decimal("2.55"),
        holding_horizon_hours=_HORIZON, iv_scenario_points=_IV_STEP, favorable_min_ratio=_FAVORABLE,
        acceptable_min_ratio=_ACCEPTABLE, headwind_min_ratio=_HEADWIND,
    )
    assert result.contractual_expiry_breakeven == Decimal("170") - Decimal("2.47")


def test_real_gail_ce_shaped_case_required_move_is_not_the_contractual_breakeven() -> None:
    """The exact real GAIL CE 170 shape (LTP 4.54, delta 0.602) -- proves
    `required_underlying_move` (the old, misleadingly-named field) and
    `contractual_expiry_breakeven` are genuinely different numbers, never
    to be confused."""
    result = assess_decay_viability(
        strike=Decimal("170"), right=OptionRight.CE, spot=Decimal("171.10"), expiry=date(2026, 9, 29), as_of=AS_OF,
        implied_volatility=Decimal("20"), delta=Decimal("0.602"), gamma=Decimal("0.02"), vega=Decimal("0.15"),
        theta=Decimal("-1.0"), ltp=Decimal("4.54"), bid=Decimal("4.50"), ask=Decimal("4.60"),
        holding_horizon_hours=_HORIZON, iv_scenario_points=_IV_STEP, favorable_min_ratio=_FAVORABLE,
        acceptable_min_ratio=_ACCEPTABLE, headwind_min_ratio=_HEADWIND,
    )
    assert result.contractual_expiry_breakeven == Decimal("170") + Decimal("4.54")
    assert result.required_underlying_move is not None
    # The two concepts must never coincidentally be asserted equal here --
    # they measure genuinely different things (contractual arithmetic vs.
    # a modeled cost-coverage estimate).
    assert result.required_underlying_move != result.contractual_expiry_breakeven


def test_contractual_breakeven_is_independent_of_the_cost_model() -> None:
    """Changing theta/spread (which move `required_underlying_move`) must
    NEVER move `contractual_expiry_breakeven` -- it is pure strike+premium
    arithmetic, computed independently of the decay/cost model."""
    low_cost = _assess(theta=Decimal("-0.1"), bid=Decimal("9.9"), ask=Decimal("10.0"))
    high_cost = _assess(theta=Decimal("-5.0"), bid=Decimal("8.0"), ask=Decimal("12.0"))
    assert low_cost.contractual_expiry_breakeven == high_cost.contractual_expiry_breakeven
    assert low_cost.required_underlying_move != high_cost.required_underlying_move


def test_contractual_breakeven_none_when_insufficient_data() -> None:
    assert _assess(iv=None).contractual_expiry_breakeven is None


# -- gamma contribution -------------------------------------------------------


def test_gamma_increases_expected_premium_response_over_delta_only() -> None:
    without_gamma = _assess(gamma=None)
    with_gamma = _assess(gamma=Decimal("0.5"))  # large gamma to make the effect visible
    assert without_gamma.expected_premium_response is not None and with_gamma.expected_premium_response is not None
    assert with_gamma.expected_premium_response > without_gamma.expected_premium_response


# -- IV scenario (vega, Phase 8) ---------------------------------------------


def test_iv_up_and_down_scenarios_are_symmetric_for_linear_vega() -> None:
    result = _assess(vega=Decimal("0.2"))
    assert result.iv_up_scenario_premium_effect == Decimal("0.2")
    assert result.iv_down_scenario_premium_effect == Decimal("-0.2")


# -- scenario table + monotonicity -------------------------------------------


def test_scenario_table_has_five_rows_in_descending_favorability() -> None:
    result = _assess()
    assert len(result.scenarios) == 5
    assert [s.move_multiple for s in result.scenarios] == [Decimal(2), Decimal(1), Decimal(0), Decimal(-1), Decimal(-2)]


def test_scenario_premium_response_is_monotonic_in_move_direction() -> None:
    result = _assess(gamma=None)  # isolate delta-only monotonicity (gamma is symmetric, not monotonic)
    responses = [s.estimated_premium_response for s in result.scenarios]
    assert responses == sorted(responses, reverse=True)  # strictly descending as scenarios go from favorable to adverse


def test_flat_scenario_response_is_zero_without_gamma() -> None:
    result = _assess(gamma=None)
    flat = next(s for s in result.scenarios if s.move_multiple == Decimal(0))
    assert flat.estimated_premium_response == Decimal("0")


def test_gamma_makes_flat_scenario_still_zero() -> None:
    # Gamma term is proportional to move^2; at move=0 it must vanish too.
    result = _assess(gamma=Decimal("0.5"))
    flat = next(s for s in result.scenarios if s.move_multiple == Decimal(0))
    assert flat.estimated_premium_response == Decimal("0")


def test_net_after_costs_subtracts_the_same_total_cost_in_every_scenario() -> None:
    result = _assess(gamma=None)
    assert result.total_modeled_cost is not None
    for s in result.scenarios:
        assert s.net_after_modeled_costs == s.estimated_premium_response - result.total_modeled_cost


# -- verdict tiers + scope discipline -----------------------------------------


def test_verdict_tiers_are_reachable() -> None:
    favorable = _assess(iv=Decimal("80"), theta=Decimal("-0.05"), bid=Decimal("9.95"), ask=Decimal("10.0"))
    unfavorable = _assess(iv=Decimal("5"), theta=Decimal("-50.0"), bid=Decimal("5.0"), ask=Decimal("15.0"))
    assert favorable.verdict == DecayViabilityVerdict.DECAY_FAVORABLE
    assert unfavorable.verdict == DecayViabilityVerdict.DECAY_UNFAVORABLE


def test_every_result_carries_the_scope_note() -> None:
    for result in (_assess(), _assess(iv=None)):
        assert "not an overall trade recommendation" in result.scope_note.lower()
        assert "not a probability of profit" in result.scope_note.lower()


def test_detail_never_claims_a_guarantee() -> None:
    result = _assess()
    for phrase in ("probability", "will rise", "will fall", "guaranteed"):
        assert phrase not in result.detail.lower()


# -- no-look-ahead ------------------------------------------------------------


def test_as_of_after_expiry_never_produces_negative_dte_or_crashes() -> None:
    result = _assess(expiry=date(2020, 1, 1))
    assert result.dte_days == Decimal(0)  # clamped, never negative
    assert result.verdict != DecayViabilityVerdict.INSUFFICIENT_DATA  # still computes; DTE=0 is not itself a data gap


@pytest.mark.parametrize("bad_iv", [Decimal("0"), Decimal("-5")])
def test_non_positive_iv_is_insufficient_data(bad_iv: Decimal) -> None:
    assert _assess(iv=bad_iv).verdict == DecayViabilityVerdict.INSUFFICIENT_DATA
