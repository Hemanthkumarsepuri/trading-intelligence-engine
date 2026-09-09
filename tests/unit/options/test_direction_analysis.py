from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.data.normalization.base import DefaultNormalizer
from app.data.providers.base import RawOptionChain, RawOptionLeg
from app.domain.market.models import OptionChainSnapshot, OptionRight
from app.domain.options.adversarial_analysis import AdversarialAnalysis
from app.domain.options.contract_analysis import assess_contract
from app.domain.options.direction_analysis import (
    DirectionComparisonVerdict,
    MoveCoverage,
    build_contract_case,
    build_decay_interpretation,
    build_direction_comparison,
    build_directional_paths,
    build_hedge_context,
    classify_direction_comparison,
)
from app.domain.options.evidence_matrix import (
    EvidenceDirection,
    EvidenceGroup,
    EvidenceMatrix,
    EvidenceRow,
)
from app.domain.options.support_resistance import (
    Level,
    LevelKind,
    LevelStability,
    LevelStrength,
    StabilityState,
)

EXPIRY = date(2026, 9, 24)
RECEIVED_AT = datetime(2026, 8, 29, 10, 0, tzinfo=UTC)
AS_OF = RECEIVED_AT

_DECAY_KWARGS = {
    "decay_viability_holding_horizon_hours": Decimal("6.25"), "decay_viability_iv_scenario_points": Decimal("1.0"),
    "decay_viability_favorable_min_ratio": Decimal("3.0"), "decay_viability_acceptable_min_ratio": Decimal("1.5"),
    "decay_viability_headwind_min_ratio": Decimal("0.75"),
}


def _leg(*, right: OptionRight, ltp: float | None = 30.0, bid: float | None = 29.5, ask: float | None = 30.5, oi: int | None = 50000, volume: int | None = 5000, iv: float | None = 18.0, delta: float | None = 0.5, theta: float | None = -2.0, gamma: float | None = 0.01, vega: float | None = 1.0) -> RawOptionLeg:
    return RawOptionLeg(right=right, last_price=ltp, bid_price=bid, ask_price=ask, volume=volume, open_interest=oi, previous_open_interest=None, implied_volatility=iv, delta=delta, theta=theta, gamma=gamma, vega=vega)


def _snapshot(strikes: dict[float, list[RawOptionLeg]], *, spot: float = 4000.0) -> OptionChainSnapshot:
    raw = RawOptionChain(underlying="NSE_EQ|X", expiry=EXPIRY, underlying_last_price=spot, strikes=strikes)
    return DefaultNormalizer(provider_name="test").normalize_option_chain(raw, received_at=RECEIVED_AT)


_STANDARD = {
    3900.0: [_leg(right=OptionRight.CE, ltp=90.0), _leg(right=OptionRight.PE, ltp=5.0)],
    4000.0: [_leg(right=OptionRight.CE, ltp=30.0), _leg(right=OptionRight.PE, ltp=25.0)],
    4100.0: [_leg(right=OptionRight.CE, ltp=8.0), _leg(right=OptionRight.PE, ltp=60.0)],
    4200.0: [_leg(right=OptionRight.CE, ltp=2.0), _leg(right=OptionRight.PE, ltp=140.0)],
}


def _matrix(directions: list[tuple[EvidenceGroup, EvidenceDirection]]) -> EvidenceMatrix:
    return EvidenceMatrix(rows=[EvidenceRow(f"row{i}", g, d, "detail") for i, (g, d) in enumerate(directions)])


_NO_ADVERSARIAL = AdversarialAnalysis(bull_case=[], bear_case=[], contradictions=[], missing_data=[], key_risks=[], opposite_case_is_equally_supported=False)


# -- classify_direction_comparison (Part E, item 10/11/12) --------------


def test_bullish_convergence_maps_to_bullish_side_better_supported() -> None:
    matrix = _matrix([(EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH), (EvidenceGroup.OPTIONS_OI, EvidenceDirection.BULLISH)])
    assert classify_direction_comparison(matrix, min_supporting_rows_for_strong=2) == DirectionComparisonVerdict.BULLISH_SIDE_BETTER_SUPPORTED


def test_bearish_convergence_maps_to_bearish_side_better_supported() -> None:
    matrix = _matrix([(EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BEARISH)])
    assert classify_direction_comparison(matrix, min_supporting_rows_for_strong=2) == DirectionComparisonVerdict.BEARISH_SIDE_BETTER_SUPPORTED


def test_no_directional_evidence_is_insufficient_data() -> None:
    matrix = _matrix([(EvidenceGroup.LIQUIDITY, EvidenceDirection.NEUTRAL)])
    assert classify_direction_comparison(matrix, min_supporting_rows_for_strong=2) == DirectionComparisonVerdict.INSUFFICIENT_DATA


def test_conflicting_evidence_both_sides_strong() -> None:
    matrix = _matrix([
        (EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH), (EvidenceGroup.OPTIONS_IV, EvidenceDirection.BULLISH),
        (EvidenceGroup.OPTIONS_OI, EvidenceDirection.BEARISH), (EvidenceGroup.FUTURES, EvidenceDirection.BEARISH),
    ])
    assert classify_direction_comparison(matrix, min_supporting_rows_for_strong=2) == DirectionComparisonVerdict.BOTH_SIDES_STRONG


def test_conflicting_evidence_both_sides_weak() -> None:
    matrix = _matrix([(EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH), (EvidenceGroup.OPTIONS_OI, EvidenceDirection.BEARISH)])
    assert classify_direction_comparison(matrix, min_supporting_rows_for_strong=5) == DirectionComparisonVerdict.BOTH_SIDES_WEAK


def test_conflicting_evidence_asymmetric_magnitude_is_conflicted() -> None:
    matrix = _matrix([
        (EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH), (EvidenceGroup.OPTIONS_IV, EvidenceDirection.BULLISH),
        (EvidenceGroup.OPTIONS_OI, EvidenceDirection.BEARISH),
    ])
    assert classify_direction_comparison(matrix, min_supporting_rows_for_strong=2) == DirectionComparisonVerdict.CONFLICTED


# -- build_directional_paths (Part H, item 9) --------------------------


def _level(kind: LevelKind, strike: Decimal, distance_pct: Decimal) -> Level:
    return Level(kind=kind, strike=strike, open_interest=1000, strength=LevelStrength.STRONG, distance_from_spot=None, distance_from_spot_pct=distance_pct, evidence="x")


def test_directional_paths_both_directions_built() -> None:
    resistances = [_level(LevelKind.RESISTANCE, Decimal("4100"), Decimal("2.5")), _level(LevelKind.RESISTANCE, Decimal("4200"), Decimal("5.0"))]
    supports = [_level(LevelKind.SUPPORT, Decimal("3900"), Decimal("-2.5")), _level(LevelKind.SUPPORT, Decimal("3800"), Decimal("-5.0"))]
    stability = [LevelStability(level=resistances[0], state=StabilityState.STABLE, detail="x")]
    paths = build_directional_paths(spot=Decimal("4000"), resistance_levels=resistances, support_levels=supports, level_stability=stability)
    assert paths.bullish_path[0].label == "current"
    assert [s.price for s in paths.bullish_path[1:]] == [Decimal("4100"), Decimal("4200")]
    assert paths.bullish_path[1].stability == "STABLE"
    assert [s.price for s in paths.bearish_path[1:]] == [Decimal("3900"), Decimal("3800")]


def test_directional_paths_empty_when_no_levels() -> None:
    paths = build_directional_paths(spot=Decimal("4000"), resistance_levels=[], support_levels=[], level_stability=[])
    assert len(paths.bullish_path) == 1  # only "current"
    assert len(paths.bearish_path) == 1


# -- build_decay_interpretation (Part F/G, items 4/5/8) ------------------


def test_decay_interpretation_never_says_positive_for_either_side() -> None:
    snapshot = _snapshot(_STANDARD)

    ce = assess_contract(snapshot, strike=Decimal("4000"), right=OptionRight.CE, expiry=EXPIRY, as_of=AS_OF, max_quote_age=timedelta(minutes=5), support_levels=[], resistance_levels=[], **_DECAY_KWARGS)
    pe = assess_contract(snapshot, strike=Decimal("4000"), right=OptionRight.PE, expiry=EXPIRY, as_of=AS_OF, max_quote_age=timedelta(minutes=5), support_levels=[], resistance_levels=[], **_DECAY_KWARGS)
    ce_interp = build_decay_interpretation(ce)
    pe_interp = build_decay_interpretation(pe)
    assert ce_interp is not None and pe_interp is not None
    # The forbidden CLAIM ("decay is positive") must never appear; the
    # explicit NEGATION of it ("does not become positive") is correct and
    # exactly what the statement is required to say.
    for forbidden in ("decay is positive", "theta is positive", "becomes positive because"):
        assert forbidden not in ce_interp.theta_cost_statement.lower()
        assert forbidden not in pe_interp.theta_cost_statement.lower()
    assert "does not become positive" in ce_interp.theta_cost_statement
    assert "does not become positive" in pe_interp.theta_cost_statement
    assert "remains a cost" in ce_interp.theta_cost_statement
    assert "remains a cost" in pe_interp.theta_cost_statement
    assert "bullish" in ce_interp.theta_cost_statement
    assert "bearish" in pe_interp.theta_cost_statement


def test_decay_interpretation_move_coverage_is_first_order_language_only() -> None:
    snapshot = _snapshot(_STANDARD)

    ce = assess_contract(snapshot, strike=Decimal("4000"), right=OptionRight.CE, expiry=EXPIRY, as_of=AS_OF, max_quote_age=timedelta(minutes=5), support_levels=[], resistance_levels=[], **_DECAY_KWARGS)
    interp = build_decay_interpretation(ce)
    assert interp is not None
    assert interp.move_coverage in (MoveCoverage.PRESENT, MoveCoverage.ABSENT, MoveCoverage.INSUFFICIENT_DATA)
    assert "FIRST-ORDER MOVE COVERAGE" in interp.move_coverage_label
    for forbidden in ("profitable", "guaranteed", "probability"):
        assert forbidden not in interp.move_coverage_label.lower()
        assert forbidden not in interp.move_coverage_detail.lower()


def test_decay_interpretation_none_when_assessment_missing() -> None:
    assert build_decay_interpretation(None) is None


def test_decay_interpretation_move_coverage_explicitly_disclaims_profitability() -> None:
    """Sprint 7A, Objective 10 -- 'coverage' must never visually/verbally
    imply a good trade; the detail text must explicitly say so."""
    snapshot = _snapshot(_STANDARD)
    ce = assess_contract(snapshot, strike=Decimal("4000"), right=OptionRight.CE, expiry=EXPIRY, as_of=AS_OF, max_quote_age=timedelta(minutes=5), support_levels=[], resistance_levels=[], **_DECAY_KWARGS)
    interp = build_decay_interpretation(ce)
    assert interp is not None
    assert interp.move_coverage in (MoveCoverage.PRESENT, MoveCoverage.ABSENT)
    assert "does NOT establish positive expectancy or profitability" in interp.move_coverage_detail


def test_decay_interpretation_insufficient_data_when_no_ltp() -> None:
    snapshot = _snapshot({4000.0: [_leg(right=OptionRight.CE, ltp=None, bid=None, ask=None), _leg(right=OptionRight.PE)]})

    ce = assess_contract(snapshot, strike=Decimal("4000"), right=OptionRight.CE, expiry=EXPIRY, as_of=AS_OF, max_quote_age=timedelta(minutes=5), support_levels=[], resistance_levels=[], **_DECAY_KWARGS)
    interp = build_decay_interpretation(ce)
    assert interp is not None
    assert interp.move_coverage == MoveCoverage.INSUFFICIENT_DATA


# -- build_hedge_context (Part I, item not numbered but explicit) -------


def test_hedge_context_never_recommends_a_structure() -> None:
    ce_ctx = build_hedge_context(OptionRight.CE)
    pe_ctx = build_hedge_context(OptionRight.PE)
    assert "HEDGE STRUCTURE NOT EVALUATED" in ce_ctx
    assert "HEDGE STRUCTURE NOT EVALUATED" in pe_ctx
    assert "downside" in ce_ctx and "upside" in pe_ctx


# -- build_contract_case (Part J, adversarial CE/PE) ---------------------


def test_contract_case_ce_could_work_is_bull_case() -> None:
    adversarial = AdversarialAnalysis(bull_case=["trend bullish"], bear_case=["resistance nearby"], contradictions=[], missing_data=[], key_risks=[], opposite_case_is_equally_supported=False)
    case = build_contract_case(right=OptionRight.CE, adversarial=adversarial, assessment=None)
    assert case.could_work == ["trend bullish"]
    assert case.could_fail == ["resistance nearby"]


def test_contract_case_pe_could_work_is_bear_case() -> None:
    adversarial = AdversarialAnalysis(bull_case=["trend bullish"], bear_case=["resistance nearby"], contradictions=[], missing_data=[], key_risks=[], opposite_case_is_equally_supported=False)
    case = build_contract_case(right=OptionRight.PE, adversarial=adversarial, assessment=None)
    assert case.could_work == ["resistance nearby"]
    assert case.could_fail == ["trend bullish"]


def test_contract_case_adds_structural_risk_for_illiquid_contract() -> None:
    snapshot = _snapshot({4200.0: [_leg(right=OptionRight.CE, ltp=2.0, bid=1.0, ask=3.0, oi=200, volume=10), _leg(right=OptionRight.PE, ltp=140.0)]})

    ce = assess_contract(snapshot, strike=Decimal("4200"), right=OptionRight.CE, expiry=EXPIRY, as_of=AS_OF, max_quote_age=timedelta(minutes=5), support_levels=[], resistance_levels=[], **_DECAY_KWARGS)
    assert ce is not None
    case = build_contract_case(right=OptionRight.CE, adversarial=_NO_ADVERSARIAL, assessment=ce)
    if ce.liquidity.grade.value in ("poor", "untradeable"):
        assert any("liquidity" in note.lower() for note in case.could_fail)


# -- build_direction_comparison (Part D/E/K, items 1/2/3/21/22/23) -------


def test_direction_comparison_builds_both_ce_and_pe_regardless_of_bias() -> None:
    matrix = _matrix([(EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BEARISH)])  # bearish bias
    snapshot = _snapshot(_STANDARD)
    comparison = build_direction_comparison(
        snapshot, matrix=matrix, adversarial=_NO_ADVERSARIAL, reference_strike=Decimal("4000"), expiry=EXPIRY, as_of=AS_OF,
        max_quote_age=timedelta(minutes=5), strikes_each_side=2, support_levels=[], resistance_levels=[], level_stability=[],
        min_supporting_rows_for_strong=2, **_DECAY_KWARGS,
    )
    # Even with a bearish bias, the CE side is still fully analyzed -- never skipped.
    assert comparison.ce_assessment is not None
    assert comparison.ce_assessment.right == OptionRight.CE
    assert comparison.pe_assessment is not None
    assert comparison.pe_assessment.right == OptionRight.PE


def test_requested_contract_cannot_force_preference_ce() -> None:
    # Requesting a CE strike must not make CE "preferred" merely because
    # it was requested -- preference follows the EVIDENCE, not the request.
    matrix = _matrix([(EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BEARISH)])
    snapshot = _snapshot(_STANDARD)
    comparison = build_direction_comparison(
        snapshot, matrix=matrix, adversarial=_NO_ADVERSARIAL, reference_strike=Decimal("4000"), expiry=EXPIRY, as_of=AS_OF,
        max_quote_age=timedelta(minutes=5), strikes_each_side=2, support_levels=[], resistance_levels=[], level_stability=[],
        min_supporting_rows_for_strong=2, **_DECAY_KWARGS,
    )
    assert comparison.verdict == DirectionComparisonVerdict.BEARISH_SIDE_BETTER_SUPPORTED
    assert comparison.preferred_direction == EvidenceDirection.BEARISH
    if comparison.preferred_contract is not None:
        assert comparison.preferred_contract.right == OptionRight.PE  # not CE, despite CE being the reference


def test_no_defensible_direction_when_insufficient_data() -> None:
    matrix = _matrix([(EvidenceGroup.LIQUIDITY, EvidenceDirection.NEUTRAL)])
    snapshot = _snapshot(_STANDARD)
    comparison = build_direction_comparison(
        snapshot, matrix=matrix, adversarial=_NO_ADVERSARIAL, reference_strike=Decimal("4000"), expiry=EXPIRY, as_of=AS_OF,
        max_quote_age=timedelta(minutes=5), strikes_each_side=2, support_levels=[], resistance_levels=[], level_stability=[],
        min_supporting_rows_for_strong=2, **_DECAY_KWARGS,
    )
    assert comparison.no_defensible_direction is True
    assert comparison.preferred_contract is None


def test_conflicted_evidence_yields_no_defensible_direction() -> None:
    matrix = _matrix([
        (EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH), (EvidenceGroup.OPTIONS_IV, EvidenceDirection.BULLISH),
        (EvidenceGroup.OPTIONS_OI, EvidenceDirection.BEARISH), (EvidenceGroup.FUTURES, EvidenceDirection.BEARISH),
    ])
    snapshot = _snapshot(_STANDARD)
    comparison = build_direction_comparison(
        snapshot, matrix=matrix, adversarial=_NO_ADVERSARIAL, reference_strike=Decimal("4000"), expiry=EXPIRY, as_of=AS_OF,
        max_quote_age=timedelta(minutes=5), strikes_each_side=2, support_levels=[], resistance_levels=[], level_stability=[],
        min_supporting_rows_for_strong=2, **_DECAY_KWARGS,
    )
    assert comparison.verdict == DirectionComparisonVerdict.BOTH_SIDES_STRONG
    assert comparison.no_defensible_direction is True


def test_ce_and_pe_alternatives_both_built_symmetrically() -> None:
    matrix = _matrix([(EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH)])
    snapshot = _snapshot(_STANDARD)
    comparison = build_direction_comparison(
        snapshot, matrix=matrix, adversarial=_NO_ADVERSARIAL, reference_strike=Decimal("4000"), expiry=EXPIRY, as_of=AS_OF,
        max_quote_age=timedelta(minutes=5), strikes_each_side=2, support_levels=[], resistance_levels=[], level_stability=[],
        min_supporting_rows_for_strong=2, **_DECAY_KWARGS,
    )
    assert len(comparison.ce_alternatives.alternatives) == len(comparison.pe_alternatives.alternatives)
    assert all(a.right == OptionRight.CE for a in comparison.ce_alternatives.alternatives)
    assert all(a.right == OptionRight.PE for a in comparison.pe_alternatives.alternatives)
