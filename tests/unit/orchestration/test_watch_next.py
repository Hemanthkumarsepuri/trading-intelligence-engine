from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from app.domain.market.data_state import MarketDataState
from app.domain.options.contract_analysis import ContractComparison
from app.domain.options.decision_engine import (
    DecisionResult,
    FinalDecision,
    QualityAssessment,
    QualityLevel,
)
from app.domain.options.direction_analysis import (
    ContractCase,
    DirectionalPaths,
    DirectionComparison,
    DirectionComparisonVerdict,
)
from app.domain.options.evidence_matrix import EvidenceDirection, OverallConvergence
from app.domain.options.iv_context import AtmIvSummary
from app.domain.options.models import ChainTotals
from app.domain.options.support_resistance import Level, LevelKind, LevelStrength
from app.domain.options.term_structure import ExpirySummary, TermStructure
from app.orchestration.options_intelligence_report import OptionsIntelligenceReport
from app.orchestration.watch_next import WatchStatus, build_watch_conditions

GENERATED_AT = datetime(2026, 8, 24, 10, 0, tzinfo=UTC)


def _report(**overrides: object) -> OptionsIntelligenceReport:
    defaults: dict[str, object] = {
        "symbol": "RELIANCE", "underlying_instrument_key": None, "generated_at": GENERATED_AT,
        "data_state": MarketDataState.LIVE_SNAPSHOT, "data_age_seconds": 1.0,
    }
    defaults.update(overrides)
    return OptionsIntelligenceReport(**defaults)  # type: ignore[arg-type]


def _decision(bias: EvidenceDirection) -> DecisionResult:
    assessment = QualityAssessment(
        market_bias=bias, convergence=OverallConvergence.CONFLICT, setup_quality=QualityLevel.MODERATE,
        option_quality=QualityLevel.MODERATE, liquidity_quality=QualityLevel.MODERATE, data_quality=QualityLevel.STRONG,
        risk_quality=QualityLevel.MODERATE, supporting_evidence_count=2, conflicting_evidence_count=1,
    )
    return DecisionResult(decision=FinalDecision.WATCH, assessment=assessment, reasoning="test")


def _level(kind: LevelKind, strike: Decimal, distance: Decimal | None) -> Level:
    return Level(
        kind=kind, strike=strike, open_interest=50000, strength=LevelStrength.MODERATE,
        distance_from_spot=distance, distance_from_spot_pct=None, evidence="top OI concentration",
    )


def test_empty_report_returns_no_conditions() -> None:
    report = _report()
    assert build_watch_conditions(report) == []


def test_bullish_invalidation_holds_when_spot_above_level() -> None:
    report = _report(spot=Decimal("1300"), invalidation_level=Decimal("1290"), decision=_decision(EvidenceDirection.BULLISH))
    conditions = build_watch_conditions(report)
    invalidation = next(c for c in conditions if "1290" in c.watch)
    assert invalidation.status == WatchStatus.HOLDS


def test_bullish_invalidation_does_not_hold_when_spot_at_or_below_level() -> None:
    report = _report(spot=Decimal("1280"), invalidation_level=Decimal("1290"), decision=_decision(EvidenceDirection.BULLISH))
    conditions = build_watch_conditions(report)
    invalidation = next(c for c in conditions if "1290" in c.watch)
    assert invalidation.status == WatchStatus.DOES_NOT_HOLD


def test_bearish_invalidation_direction_is_inverted() -> None:
    report = _report(spot=Decimal("1280"), invalidation_level=Decimal("1290"), decision=_decision(EvidenceDirection.BEARISH))
    conditions = build_watch_conditions(report)
    invalidation = next(c for c in conditions if "1290" in c.watch)
    assert invalidation.status == WatchStatus.HOLDS


def test_neutral_bias_invalidation_cannot_be_evaluated() -> None:
    report = _report(spot=Decimal("1280"), invalidation_level=Decimal("1290"), decision=_decision(EvidenceDirection.NEUTRAL))
    conditions = build_watch_conditions(report)
    invalidation = next(c for c in conditions if "1290" in c.watch)
    assert invalidation.status == WatchStatus.CANNOT_BE_EVALUATED


def test_no_invalidation_level_adds_no_condition() -> None:
    report = _report(spot=Decimal("1300"))
    assert build_watch_conditions(report) == []


def test_support_level_holds_when_spot_above_it() -> None:
    report = _report(spot=Decimal("1300"), support_levels=[_level(LevelKind.SUPPORT, Decimal("1280"), Decimal("20"))])
    conditions = build_watch_conditions(report)
    support = next(c for c in conditions if "support" in c.watch)
    assert support.status == WatchStatus.HOLDS
    assert "1280" in support.watch


def test_resistance_level_does_not_hold_when_spot_above_it() -> None:
    report = _report(spot=Decimal("1320"), resistance_levels=[_level(LevelKind.RESISTANCE, Decimal("1310"), Decimal("10"))])
    conditions = build_watch_conditions(report)
    resistance = next(c for c in conditions if "resistance" in c.watch)
    assert resistance.status == WatchStatus.DOES_NOT_HOLD


def test_nearest_support_by_distance_is_selected_not_the_first_in_list() -> None:
    far = _level(LevelKind.SUPPORT, Decimal("1250"), Decimal("50"))
    near = _level(LevelKind.SUPPORT, Decimal("1290"), Decimal("10"))
    report = _report(spot=Decimal("1300"), support_levels=[far, near])
    conditions = build_watch_conditions(report)
    support = next(c for c in conditions if "support" in c.watch)
    assert "1290" in support.watch


def test_no_spot_makes_level_conditions_unevaluable() -> None:
    report = _report(support_levels=[_level(LevelKind.SUPPORT, Decimal("1280"), Decimal("20"))])
    conditions = build_watch_conditions(report)
    support = next(c for c in conditions if "support" in c.watch)
    assert support.status == WatchStatus.CANNOT_BE_EVALUATED


def test_ce_pe_divergence_condition_is_informational_only() -> None:
    ce_alt = ContractComparison(requested_strike=Decimal("1300"), requested_right=None, requested=None)  # type: ignore[arg-type]
    pe_alt = ContractComparison(requested_strike=Decimal("1300"), requested_right=None, requested=None)  # type: ignore[arg-type]
    dc = DirectionComparison(
        reference_strike=Decimal("1300"), verdict=DirectionComparisonVerdict.CONFLICTED, verdict_detail="CE and PE evidence conflict",
        ce_alternatives=ce_alt, pe_alternatives=pe_alt, ce_case=ContractCase(), pe_case=ContractCase(),
        ce_decay=None, pe_decay=None, ce_hedge_context="n/a", pe_hedge_context="n/a",
        paths=DirectionalPaths(spot=Decimal("1300")), bullish_supporting_groups=1, bearish_supporting_groups=1,
        conflicting_groups=1, preferred_direction=None, preferred_contract=None, no_defensible_direction=True,
    )
    report = _report(direction_comparison=dc)
    conditions = build_watch_conditions(report)
    divergence = next(c for c in conditions if "CE and PE" in c.watch)
    assert divergence.status == WatchStatus.CANNOT_BE_EVALUATED
    assert divergence.current == "CONFLICTED"


def test_expiry_proximity_condition_reports_nearest_expiry() -> None:
    iv = AtmIvSummary(atm_strike=Decimal("1300"), atm_ce_iv=Decimal("18"), atm_pe_iv=Decimal("17"), chain_iv=Decimal("17.5"), ce_pe_skew=Decimal("1"))
    totals = ChainTotals(
        total_call_oi=100, total_put_oi=80, total_call_volume=10, total_put_volume=8, put_call_ratio_oi=Decimal("0.8"),
        put_call_ratio_volume=Decimal("1.25"), legs_with_known_oi=2, legs_with_missing_oi=0,
    )
    near = ExpirySummary(expiry=date(2026, 8, 28), is_weekly=True, days_remaining=4, atm_strike=Decimal("1300"), iv=iv, totals=totals, top_call_oi=[], top_put_oi=[], chain_quality_issues=[])
    far = ExpirySummary(expiry=date(2026, 9, 25), is_weekly=False, days_remaining=32, atm_strike=Decimal("1300"), iv=iv, totals=totals, top_call_oi=[], top_put_oi=[], chain_quality_issues=[])
    report = _report(term_structure=TermStructure(expiries=[near, far]))
    conditions = build_watch_conditions(report)
    proximity = next(c for c in conditions if "expiry" in c.watch)
    assert "4 day" in proximity.current
    assert proximity.status == WatchStatus.CANNOT_BE_EVALUATED
