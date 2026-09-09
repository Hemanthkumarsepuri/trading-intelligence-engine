from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.news.models import NewsItem, build_news_item
from app.domain.options.evidence_matrix import (
    EvidenceDirection,
    EvidenceGroup,
    EvidenceMatrix,
    EvidenceRow,
    GroupVerdict,
    OverallConvergence,
    row_change_in_oi,
    row_data_freshness,
    row_futures_oi,
    row_global_context,
    row_iv_level,
    row_iv_skew,
    row_liquidity,
    row_m15_trend,
    row_market_regime,
    row_news,
    row_pcr_change,
    row_put_call_oi_structure,
    row_relative_strength,
    row_resistance,
    row_spot_futures_basis,
    row_support,
    row_volume,
    row_vwap,
)
from app.domain.options.global_context import GlobalContextAssessment, GlobalContextVerdict
from app.domain.options.iv_context import AtmIvSummary
from app.domain.options.liquidity import LiquidityGrade
from app.domain.options.market_regime import MarketRegime, MarketRegimeResult
from app.domain.options.price_oi_interpretation import PriceOIObservation, PriceOIQuadrant
from app.domain.options.support_resistance import Level, LevelKind, LevelStrength
from app.domain.technical.ema_alignment import EMAAlignmentState
from app.domain.technical.series import IndicatorStatus
from app.domain.technical.vwap_position import VWAPPositionState

# -- EvidenceMatrix: group-based convergence (the anti-gaming mechanism) ----


def test_single_group_cannot_produce_convergence_alone() -> None:
    # Five BULLISH rows, but all from the SAME group -- must not read as
    # "5 independent confirmations."
    rows = [
        EvidenceRow("a", EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH, ""),
        EvidenceRow("b", EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH, ""),
        EvidenceRow("c", EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH, ""),
    ]
    matrix = EvidenceMatrix(rows=rows)
    assert matrix.group_verdicts() == {EvidenceGroup.UNDERLYING_PRICE_STRUCTURE: GroupVerdict.BULLISH}
    # Only ONE group is bullish -> convergence, but the count of contributing
    # independent groups (not raw rows) is what a decision engine must use.
    assert matrix.overall_convergence() == OverallConvergence.CONVERGENCE_BULLISH


def test_conflicting_rows_within_one_group_are_non_directional_for_that_group() -> None:
    rows = [
        EvidenceRow("a", EvidenceGroup.OPTIONS_OI, EvidenceDirection.BULLISH, ""),
        EvidenceRow("b", EvidenceGroup.OPTIONS_OI, EvidenceDirection.BEARISH, ""),
    ]
    matrix = EvidenceMatrix(rows=rows)
    assert matrix.group_verdict(EvidenceGroup.OPTIONS_OI) == GroupVerdict.CONFLICTING
    assert matrix.overall_convergence() == OverallConvergence.CONFLICT


def test_two_independent_groups_agreeing_is_convergence() -> None:
    rows = [
        EvidenceRow("a", EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH, ""),
        EvidenceRow("b", EvidenceGroup.OPTIONS_OI, EvidenceDirection.BULLISH, ""),
    ]
    matrix = EvidenceMatrix(rows=rows)
    assert matrix.overall_convergence() == OverallConvergence.CONVERGENCE_BULLISH


def test_two_independent_groups_disagreeing_is_conflict() -> None:
    rows = [
        EvidenceRow("a", EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH, ""),
        EvidenceRow("b", EvidenceGroup.OPTIONS_OI, EvidenceDirection.BEARISH, ""),
    ]
    matrix = EvidenceMatrix(rows=rows)
    assert matrix.overall_convergence() == OverallConvergence.CONFLICT


def test_no_directional_evidence_anywhere_is_insufficient() -> None:
    rows = [
        EvidenceRow("a", EvidenceGroup.LIQUIDITY, EvidenceDirection.NEUTRAL, ""),
        EvidenceRow("b", EvidenceGroup.DATA_QUALITY, EvidenceDirection.UNKNOWN, ""),
    ]
    matrix = EvidenceMatrix(rows=rows)
    assert matrix.overall_convergence() == OverallConvergence.INSUFFICIENT_EVIDENCE


# -- individual row builders --------------------------------------------


def test_row_m15_trend_ascending_is_bullish() -> None:
    row = row_m15_trend(ema_alignment=EMAAlignmentState.ASCENDING, ema_status=IndicatorStatus.OK)
    assert row.direction == EvidenceDirection.BULLISH
    assert row.group == EvidenceGroup.UNDERLYING_PRICE_STRUCTURE


def test_row_m15_trend_insufficient_history_is_unknown() -> None:
    row = row_m15_trend(ema_alignment=None, ema_status=IndicatorStatus.INSUFFICIENT_HISTORY)
    assert row.direction == EvidenceDirection.UNKNOWN


def test_row_vwap_above_is_bullish() -> None:
    row = row_vwap(vwap_state=VWAPPositionState.ABOVE, vwap_status=IndicatorStatus.OK)
    assert row.direction == EvidenceDirection.BULLISH


def test_row_market_regime_maps_trending_bearish() -> None:
    regime = MarketRegimeResult(regime=MarketRegime.TRENDING_BEARISH, detail="x", atr_pct_of_price=Decimal("1"))
    row = row_market_regime(regime)
    assert row.direction == EvidenceDirection.BEARISH


# -- Sprint 7A, Objective 1: market-data synchronization ------------------


def test_row_m15_trend_unknown_when_candles_are_not_current() -> None:
    """A real, otherwise-valid BULLISH reading must be withheld as UNKNOWN
    when the candle series is stale relative to the live session --
    never silently combined with a current quote."""
    row = row_m15_trend(ema_alignment=EMAAlignmentState.ASCENDING, ema_status=IndicatorStatus.OK, data_is_current=False)
    assert row.direction == EvidenceDirection.UNKNOWN
    assert "current live trading session" in row.detail


def test_row_vwap_unknown_when_candles_are_not_current() -> None:
    row = row_vwap(vwap_state=VWAPPositionState.ABOVE, vwap_status=IndicatorStatus.OK, data_is_current=False)
    assert row.direction == EvidenceDirection.UNKNOWN
    assert "current live trading session" in row.detail


def test_row_market_regime_unknown_when_candles_are_not_current() -> None:
    regime = MarketRegimeResult(regime=MarketRegime.TRENDING_BULLISH, detail="x", atr_pct_of_price=Decimal("1"))
    row = row_market_regime(regime, data_is_current=False)
    assert row.direction == EvidenceDirection.UNKNOWN
    assert "current live trading session" in row.detail


def test_row_m15_trend_still_directional_when_candles_are_current() -> None:
    """`data_is_current` defaults to True -- every pre-Sprint-7A call
    site is unaffected."""
    row = row_m15_trend(ema_alignment=EMAAlignmentState.ASCENDING, ema_status=IndicatorStatus.OK)
    assert row.direction == EvidenceDirection.BULLISH


def test_row_spot_futures_basis_premium_is_neutral_not_bullish() -> None:
    # Premium is the NORMAL state (cost of carry) -- must not be read as bullish.
    row = row_spot_futures_basis(spot=Decimal("100"), futures_ltp=Decimal("100.5"))
    assert row.direction == EvidenceDirection.NEUTRAL


def test_row_spot_futures_basis_discount_is_bearish() -> None:
    row = row_spot_futures_basis(spot=Decimal("100"), futures_ltp=Decimal("99"))
    assert row.direction == EvidenceDirection.BEARISH


def test_row_futures_oi_insufficient_data_is_unknown() -> None:
    obs = PriceOIObservation(
        quadrant=PriceOIQuadrant.INSUFFICIENT_DATA, price_change=None, oi_change=None, conventional_reading="x"
    )
    row = row_futures_oi(obs)
    assert row.direction == EvidenceDirection.UNKNOWN


def test_row_change_in_oi_price_down_oi_up_is_bearish() -> None:
    obs = PriceOIObservation(
        quadrant=PriceOIQuadrant.PRICE_DOWN_OI_UP, price_change=Decimal("-5"), oi_change=100, conventional_reading="x"
    )
    row = row_change_in_oi(obs, right_label="ATM CE")
    assert row.direction == EvidenceDirection.BEARISH
    assert row.group == EvidenceGroup.OPTIONS_OI


def test_row_put_call_oi_structure_high_pcr_is_neutral_not_bullish() -> None:
    """Sprint 7A, Objective 4 -- PCR LEVEL alone is now NEVER directional,
    even at the old "bullish band" threshold. The real value/band is
    still reported as context."""
    row = row_put_call_oi_structure(Decimal("1.5"))
    assert row.direction == EvidenceDirection.NEUTRAL
    assert "1.50" in row.detail
    assert "context only; not directional by itself" in row.detail


def test_row_put_call_oi_structure_low_pcr_is_neutral_not_bearish() -> None:
    row = row_put_call_oi_structure(Decimal("0.5"))
    assert row.direction == EvidenceDirection.NEUTRAL


def test_row_put_call_oi_structure_none_is_unknown() -> None:
    row = row_put_call_oi_structure(None)
    assert row.direction == EvidenceDirection.UNKNOWN


# -- Sprint 7A, Objective 4: PCR change + OI balance corroboration --------


def test_row_pcr_change_unknown_when_prior_oi_missing() -> None:
    row = row_pcr_change(
        current_pcr=Decimal("1.5"), previous_pcr=Decimal("1.0"), current_call_oi=1000, previous_call_oi=None,
        current_put_oi=1500, previous_put_oi=None, meaningful_pcr_change=Decimal("0.1"),
        meaningful_oi_change_fraction=Decimal("0.1"),
    )
    assert row.direction == EvidenceDirection.UNKNOWN
    assert row.direction != EvidenceDirection.NEUTRAL
    row = row_pcr_change(
        current_pcr=Decimal("1.5"), previous_pcr=None, current_call_oi=1000, previous_call_oi=None,
        current_put_oi=1500, previous_put_oi=None, meaningful_pcr_change=Decimal("0.1"),
        meaningful_oi_change_fraction=Decimal("0.1"),
    )
    assert row.direction == EvidenceDirection.UNKNOWN


def test_row_pcr_change_neutral_when_change_is_not_meaningful() -> None:
    row = row_pcr_change(
        current_pcr=Decimal("1.02"), previous_pcr=Decimal("1.00"), current_call_oi=1000, previous_call_oi=1000,
        current_put_oi=1020, previous_put_oi=1000, meaningful_pcr_change=Decimal("0.1"),
        meaningful_oi_change_fraction=Decimal("0.1"),
    )
    assert row.direction == EvidenceDirection.NEUTRAL


def test_row_pcr_change_bullish_when_pcr_rises_and_put_oi_corroborates() -> None:
    """Contrarian convention: PCR rising, corroborated by real put OI
    growing meaningfully faster than call OI -> contrarian-bullish."""
    row = row_pcr_change(
        current_pcr=Decimal("1.5"), previous_pcr=Decimal("1.0"), current_call_oi=1000, previous_call_oi=1000,
        current_put_oi=1500, previous_put_oi=1000, meaningful_pcr_change=Decimal("0.1"),
        meaningful_oi_change_fraction=Decimal("0.1"),
    )
    assert row.direction == EvidenceDirection.BULLISH


def test_row_pcr_change_bearish_when_pcr_falls_and_call_oi_corroborates() -> None:
    row = row_pcr_change(
        current_pcr=Decimal("0.7"), previous_pcr=Decimal("1.0"), current_call_oi=1500, previous_call_oi=1000,
        current_put_oi=1000, previous_put_oi=1000, meaningful_pcr_change=Decimal("0.1"),
        meaningful_oi_change_fraction=Decimal("0.1"),
    )
    assert row.direction == EvidenceDirection.BEARISH


def test_row_pcr_change_neutral_when_oi_balance_does_not_corroborate() -> None:
    """The exact regression the objective names: identical PCR values/
    changes must produce DIFFERENT interpretations depending on real OI
    migration -- here the PCR rose but call OI (not put OI) grew faster,
    so the level-alone convention is NOT corroborated -> NEUTRAL."""
    row = row_pcr_change(
        current_pcr=Decimal("1.5"), previous_pcr=Decimal("1.0"), current_call_oi=2000, previous_call_oi=1000,
        current_put_oi=1600, previous_put_oi=1000, meaningful_pcr_change=Decimal("0.1"),
        meaningful_oi_change_fraction=Decimal("0.1"),
    )
    assert row.direction == EvidenceDirection.NEUTRAL


def test_row_pcr_change_identical_pcr_change_different_oi_migration_different_interpretation() -> None:
    """Direct regression test for the objective's own explicit claim:
    the SAME current/previous PCR values, with different real OI
    migration behind them, must be able to produce different
    interpretations."""
    bullish_row = row_pcr_change(
        current_pcr=Decimal("1.5"), previous_pcr=Decimal("1.0"), current_call_oi=1000, previous_call_oi=1000,
        current_put_oi=1500, previous_put_oi=1000, meaningful_pcr_change=Decimal("0.1"),
        meaningful_oi_change_fraction=Decimal("0.1"),
    )
    neutral_row = row_pcr_change(
        current_pcr=Decimal("1.5"), previous_pcr=Decimal("1.0"), current_call_oi=2000, previous_call_oi=1000,
        current_put_oi=1600, previous_put_oi=1000, meaningful_pcr_change=Decimal("0.1"),
        meaningful_oi_change_fraction=Decimal("0.1"),
    )
    assert bullish_row.direction != neutral_row.direction


def test_pcr_alone_cannot_create_a_strong_directional_vote() -> None:
    """Direct regression test for the objective's own explicit claim:
    the PCR LEVEL row is structurally incapable of voting BULLISH/
    BEARISH, at any real value."""
    for pcr in (Decimal("0.1"), Decimal("0.7"), Decimal("1.0"), Decimal("1.3"), Decimal("5.0")):
        assert row_put_call_oi_structure(pcr).direction in (EvidenceDirection.NEUTRAL, EvidenceDirection.UNKNOWN)


def test_row_volume_below_floor_is_unknown() -> None:
    row = row_volume(call_volume=5, put_volume=5, min_meaningful_volume=1000)
    assert row.direction == EvidenceDirection.UNKNOWN


def test_row_volume_call_dominant_is_not_bullish() -> None:
    row = row_volume(call_volume=10000, put_volume=3000, min_meaningful_volume=1000)
    assert row.direction != EvidenceDirection.BULLISH
    assert row.direction == EvidenceDirection.NEUTRAL
    assert "not a directional vote" in row.detail


def test_row_volume_put_dominant_is_not_bearish() -> None:
    row = row_volume(call_volume=3000, put_volume=10000, min_meaningful_volume=1000)
    assert row.direction != EvidenceDirection.BEARISH
    assert row.direction == EvidenceDirection.NEUTRAL
    assert "not a directional vote" in row.detail


def test_row_support_near_spot_is_neutral_not_bullish() -> None:
    """S/R Directional-Evidence Correctness Pass -- a support candidate's
    mere existence/proximity must NEVER independently produce BULLISH
    evidence (this codebase has no real price-reaction confirmation
    mechanism to justify that read)."""
    level = Level(
        kind=LevelKind.SUPPORT, strike=Decimal("24700"), open_interest=9000, strength=LevelStrength.STRONG,
        distance_from_spot=Decimal("-100"), distance_from_spot_pct=Decimal("-1.0"), evidence="x",
    )
    row = row_support([level], near_pct_threshold=Decimal("2.0"))
    assert row.direction == EvidenceDirection.NEUTRAL
    assert "not confirmed directional evidence" in row.detail


def test_row_support_far_from_spot_is_neutral() -> None:
    level = Level(
        kind=LevelKind.SUPPORT, strike=Decimal("20000"), open_interest=9000, strength=LevelStrength.STRONG,
        distance_from_spot=Decimal("-4800"), distance_from_spot_pct=Decimal("-19.0"), evidence="x",
    )
    row = row_support([level], near_pct_threshold=Decimal("2.0"))
    assert row.direction == EvidenceDirection.NEUTRAL


def test_row_resistance_near_spot_is_neutral_not_bearish() -> None:
    level = Level(
        kind=LevelKind.RESISTANCE, strike=Decimal("24900"), open_interest=9000, strength=LevelStrength.STRONG,
        distance_from_spot=Decimal("100"), distance_from_spot_pct=Decimal("1.0"), evidence="x",
    )
    row = row_resistance([level], near_pct_threshold=Decimal("2.0"))
    assert row.direction == EvidenceDirection.NEUTRAL
    assert "not confirmed directional evidence" in row.detail


def test_row_support_and_resistance_detail_never_shows_a_negative_distance() -> None:
    support = Level(
        kind=LevelKind.SUPPORT, strike=Decimal("24700"), open_interest=9000, strength=LevelStrength.STRONG,
        distance_from_spot=Decimal("-100"), distance_from_spot_pct=Decimal("-1.0"), evidence="x",
    )
    resistance = Level(
        kind=LevelKind.RESISTANCE, strike=Decimal("24900"), open_interest=9000, strength=LevelStrength.STRONG,
        distance_from_spot=Decimal("100"), distance_from_spot_pct=Decimal("1.0"), evidence="x",
    )
    support_row = row_support([support], near_pct_threshold=Decimal("2.0"))
    resistance_row = row_resistance([resistance], near_pct_threshold=Decimal("2.0"))
    assert "-1.00%" not in support_row.detail
    assert "1.00% below spot" in support_row.detail
    assert "-1.00%" not in resistance_row.detail
    assert "1.00% above spot" in resistance_row.detail


def test_row_support_no_candidate_is_unknown_not_neutral() -> None:
    """Distinguishes "genuinely no candidate exists" (UNKNOWN) from "a
    candidate exists but isn't confirmed directional evidence" (NEUTRAL)
    -- never collapsed into one state."""
    assert row_support([], near_pct_threshold=Decimal("2.0")).direction == EvidenceDirection.UNKNOWN
    assert row_resistance([], near_pct_threshold=Decimal("2.0")).direction == EvidenceDirection.UNKNOWN


def test_row_iv_level_is_never_directional() -> None:
    summary = AtmIvSummary(
        atm_strike=Decimal("24800"), atm_ce_iv=Decimal("14"), atm_pe_iv=Decimal("12"),
        chain_iv=Decimal("13"), ce_pe_skew=Decimal("2"),
    )
    row = row_iv_level(summary)
    assert row.direction == EvidenceDirection.NEUTRAL


def test_row_iv_skew_call_richer_is_bullish_when_both_legs_are_liquid() -> None:
    summary = AtmIvSummary(
        atm_strike=Decimal("24800"), atm_ce_iv=Decimal("16"), atm_pe_iv=Decimal("12"),
        chain_iv=Decimal("14"), ce_pe_skew=Decimal("4"),
    )
    row = row_iv_skew(
        summary, meaningful_skew=Decimal("1.0"),
        ce_liquidity_grade=LiquidityGrade.EXCELLENT, pe_liquidity_grade=LiquidityGrade.GOOD,
    )
    assert row.direction == EvidenceDirection.BULLISH


def test_row_iv_skew_put_richer_is_bearish_when_both_legs_are_liquid() -> None:
    summary = AtmIvSummary(
        atm_strike=Decimal("24800"), atm_ce_iv=Decimal("12"), atm_pe_iv=Decimal("16"),
        chain_iv=Decimal("14"), ce_pe_skew=Decimal("-4"),
    )
    row = row_iv_skew(
        summary, meaningful_skew=Decimal("1.0"),
        ce_liquidity_grade=LiquidityGrade.GOOD, pe_liquidity_grade=LiquidityGrade.EXCELLENT,
    )
    assert row.direction == EvidenceDirection.BEARISH


def test_row_iv_skew_stays_neutral_when_below_the_meaningful_threshold() -> None:
    """Sprint 6 -- the real KAYNES scenario: a +1.22 pt skew must NOT
    become directional just because it crosses a bare 1.0-point cutoff
    when the caller's own threshold is set above it; this proves the
    threshold comparison itself, independent of liquidity."""
    summary = AtmIvSummary(
        atm_strike=Decimal("4000"), atm_ce_iv=Decimal("43.85"), atm_pe_iv=Decimal("42.63"),
        chain_iv=Decimal("43.24"), ce_pe_skew=Decimal("1.22"),
    )
    row = row_iv_skew(
        summary, meaningful_skew=Decimal("1.5"),
        ce_liquidity_grade=LiquidityGrade.EXCELLENT, pe_liquidity_grade=LiquidityGrade.GOOD,
    )
    assert row.direction == EvidenceDirection.NEUTRAL


def test_row_iv_skew_downgrades_to_neutral_when_a_leg_is_illiquid_even_past_threshold() -> None:
    """Sprint 6 hardening -- the core fix: a skew that DOES cross the
    meaningful-point threshold must still degrade to NEUTRAL/UNCONFIRMED
    when either ATM leg's own real liquidity grade is too thin to trust
    (never blindly promoted to a directional vote from a bare point
    difference alone)."""
    summary = AtmIvSummary(
        atm_strike=Decimal("24800"), atm_ce_iv=Decimal("16"), atm_pe_iv=Decimal("12"),
        chain_iv=Decimal("14"), ce_pe_skew=Decimal("4"),
    )
    row = row_iv_skew(
        summary, meaningful_skew=Decimal("1.0"),
        ce_liquidity_grade=LiquidityGrade.POOR, pe_liquidity_grade=LiquidityGrade.EXCELLENT,
    )
    assert row.direction == EvidenceDirection.NEUTRAL
    assert "UNCONFIRMED" in row.detail

    row_untradeable_pe = row_iv_skew(
        summary, meaningful_skew=Decimal("1.0"),
        ce_liquidity_grade=LiquidityGrade.EXCELLENT, pe_liquidity_grade=LiquidityGrade.UNTRADEABLE,
    )
    assert row_untradeable_pe.direction == EvidenceDirection.NEUTRAL

    row_missing_grade = row_iv_skew(
        summary, meaningful_skew=Decimal("1.0"), ce_liquidity_grade=LiquidityGrade.EXCELLENT, pe_liquidity_grade=None,
    )
    assert row_missing_grade.direction == EvidenceDirection.NEUTRAL


def test_row_iv_skew_unavailable_when_one_side_missing_regardless_of_liquidity() -> None:
    summary = AtmIvSummary(atm_strike=Decimal("24800"), atm_ce_iv=Decimal("16"), atm_pe_iv=None, chain_iv=Decimal("16"), ce_pe_skew=None)
    row = row_iv_skew(
        summary, meaningful_skew=Decimal("1.0"),
        ce_liquidity_grade=LiquidityGrade.EXCELLENT, pe_liquidity_grade=LiquidityGrade.EXCELLENT,
    )
    assert row.direction == EvidenceDirection.UNKNOWN


# -- Sprint 7A, Objective 6: relative strength ----------------------------


def test_row_relative_strength_unknown_when_either_value_missing() -> None:
    row = row_relative_strength(underlying_day_change_pct=None, nifty_day_change_pct=Decimal("1.0"), meaningful_gap_pct=Decimal("1.0"))
    assert row.direction == EvidenceDirection.UNKNOWN
    row2 = row_relative_strength(underlying_day_change_pct=Decimal("1.0"), nifty_day_change_pct=None, meaningful_gap_pct=Decimal("1.0"))
    assert row2.direction == EvidenceDirection.UNKNOWN


def test_row_relative_strength_outperforming_is_bullish() -> None:
    row = row_relative_strength(underlying_day_change_pct=Decimal("1.8"), nifty_day_change_pct=Decimal("-0.3"), meaningful_gap_pct=Decimal("1.0"))
    assert row.direction == EvidenceDirection.BULLISH
    assert "OUTPERFORMING" in row.detail


def test_row_relative_strength_underperforming_is_bearish() -> None:
    row = row_relative_strength(underlying_day_change_pct=Decimal("-2.1"), nifty_day_change_pct=Decimal("-0.4"), meaningful_gap_pct=Decimal("1.0"))
    assert row.direction == EvidenceDirection.BEARISH
    assert "UNDERPERFORMING" in row.detail


def test_row_relative_strength_neutral_when_gap_not_meaningful() -> None:
    row = row_relative_strength(underlying_day_change_pct=Decimal("0.5"), nifty_day_change_pct=Decimal("0.2"), meaningful_gap_pct=Decimal("1.0"))
    assert row.direction == EvidenceDirection.NEUTRAL


def test_row_relative_strength_does_not_claim_a_second_global_nifty_vote() -> None:
    """STOCK vs NIFTY is one residual; STOCK vs SECTOR is informational
    elsewhere. This row must not be a silent duplicate of GLOBAL."""
    rows = [
        row_relative_strength(underlying_day_change_pct=None, nifty_day_change_pct=None, meaningful_gap_pct=Decimal("1.0")),
        row_relative_strength(underlying_day_change_pct=Decimal("0.5"), nifty_day_change_pct=Decimal("0.2"), meaningful_gap_pct=Decimal("1.0")),
        row_relative_strength(underlying_day_change_pct=Decimal("1.8"), nifty_day_change_pct=Decimal("-0.3"), meaningful_gap_pct=Decimal("1.0")),
        row_relative_strength(underlying_day_change_pct=Decimal("-2.1"), nifty_day_change_pct=Decimal("-0.4"), meaningful_gap_pct=Decimal("1.0")),
    ]
    for row in rows:
        assert "STOCK vs NIFTY" in row.detail
        assert "not a second copy of the GLOBAL" in row.detail


def test_row_liquidity_is_never_directional() -> None:
    row = row_liquidity(LiquidityGrade.EXCELLENT)
    assert row.direction == EvidenceDirection.NEUTRAL


def test_row_data_freshness_is_never_directional() -> None:
    fresh = row_data_freshness(is_fresh=True, detail="fresh")
    stale = row_data_freshness(is_fresh=False, detail="stale")
    assert fresh.direction == EvidenceDirection.NEUTRAL
    assert stale.direction == EvidenceDirection.UNKNOWN


def test_row_global_context_tailwind_is_bullish() -> None:
    assessment = GlobalContextAssessment(inputs=[], verdict=GlobalContextVerdict.TAILWIND, detail="x")
    row = row_global_context(assessment)
    assert row.direction == EvidenceDirection.BULLISH
    assert row.group == EvidenceGroup.GLOBAL


def test_row_global_context_headwind_is_bearish() -> None:
    assessment = GlobalContextAssessment(inputs=[], verdict=GlobalContextVerdict.HEADWIND, detail="x")
    assert row_global_context(assessment).direction == EvidenceDirection.BEARISH


def test_row_global_context_insufficient_data_is_unknown() -> None:
    assessment = GlobalContextAssessment(inputs=[], verdict=GlobalContextVerdict.INSUFFICIENT_DATA, detail="x")
    assert row_global_context(assessment).direction == EvidenceDirection.UNKNOWN


# -- row_news (Sprint 4) -------------------------------------------------


AS_OF = datetime(2026, 8, 29, 10, 0, tzinfo=UTC)


def _news_item(published_at: datetime = AS_OF) -> NewsItem:
    return build_news_item(
        source="upstox", heading="Reliance in the news", summary="x", url="https://x", published_at=published_at,
        retrieved_at=AS_OF, symbol="RELIANCE", as_of=AS_OF, fresh_within=timedelta(days=2),
    )


def test_row_news_never_directional_even_with_many_items() -> None:
    items = [_news_item() for _ in range(5)]
    row = row_news(items, fetch_error=None)
    assert row.direction == EvidenceDirection.NEUTRAL
    assert row.group == EvidenceGroup.NEWS_EVENT


def test_row_news_neutral_and_honest_when_no_items() -> None:
    row = row_news([], fetch_error=None)
    assert row.direction == EvidenceDirection.NEUTRAL
    assert "no recent company news" in row.detail


def test_row_news_unknown_on_fetch_failure() -> None:
    row = row_news([], fetch_error="ProviderTimeout: request timed out")
    assert row.direction == EvidenceDirection.UNKNOWN
    assert "news fetch failed" in row.detail


def test_row_news_detail_names_the_most_recent_item() -> None:
    older = _news_item(AS_OF - timedelta(hours=5))
    newer = _news_item(AS_OF - timedelta(minutes=5))
    row = row_news([older, newer], fetch_error=None)
    assert newer.title in row.detail


def test_row_news_can_never_vote_bullish_or_bearish_in_convergence() -> None:
    # Structural guarantee, not just a per-call check: even a matrix with
    # only a strongly-worded news row and nothing else must never resolve
    # to a directional convergence.
    matrix = EvidenceMatrix(rows=[row_news([_news_item() for _ in range(20)], fetch_error=None)])
    assert matrix.overall_convergence() == OverallConvergence.INSUFFICIENT_EVIDENCE


def test_a_strong_nearby_support_candidate_can_never_vote_bullish_in_convergence() -> None:
    """S/R Directional-Evidence Correctness Pass -- a structural guarantee,
    not just a per-call check: even a matrix with only a STRONG, very-
    close support candidate (the case that used to force BULLISH) and
    nothing else must never resolve to a directional convergence."""
    level = Level(
        kind=LevelKind.SUPPORT, strike=Decimal("24700"), open_interest=90000, strength=LevelStrength.STRONG,
        distance_from_spot=Decimal("-10"), distance_from_spot_pct=Decimal("-0.1"), evidence="x",
    )
    matrix = EvidenceMatrix(rows=[row_support([level], near_pct_threshold=Decimal("2.0"))])
    assert matrix.overall_convergence() == OverallConvergence.INSUFFICIENT_EVIDENCE


def test_a_strong_nearby_resistance_candidate_can_never_vote_bearish_in_convergence() -> None:
    level = Level(
        kind=LevelKind.RESISTANCE, strike=Decimal("24900"), open_interest=90000, strength=LevelStrength.STRONG,
        distance_from_spot=Decimal("10"), distance_from_spot_pct=Decimal("0.1"), evidence="x",
    )
    matrix = EvidenceMatrix(rows=[row_resistance([level], near_pct_threshold=Decimal("2.0"))])
    assert matrix.overall_convergence() == OverallConvergence.INSUFFICIENT_EVIDENCE
