from __future__ import annotations

from app.domain.options.early_opportunity import (
    MoveContext,
    ResearchBucket,
    TimingStage,
    UniverseTier,
    classify_move_context,
    classify_research_bucket,
    classify_timing_stage,
    classify_universe_tier,
    is_early_opportunity_bucket,
    is_event_to_monitor_bucket,
)


def test_data_insufficient_outranks_everything() -> None:
    a = classify_research_bucket(
        research_state="DATA_INSUFFICIENT",
        early_stage_state="EARLY_DIRECTIONAL_BUILD",
        development_pattern="OI_MIGRATION",
        pre_breakout_signal=True,
        event_risk="MAJOR_EVENT_RISK",
        liquidity_grade="EXCELLENT",
    )
    assert a.bucket == ResearchBucket.DATA_INSUFFICIENT
    assert a.already_moved is False


def test_conflict_is_not_an_early_opportunity() -> None:
    a = classify_research_bucket(
        research_state="CONFLICT",
        early_stage_state="DEVELOPING_MOMENTUM",
        development_pattern="RELATIVE_STRENGTH",
        pre_breakout_signal=True,
        event_risk="NO_KNOWN_EVENT_RISK",
        liquidity_grade="GOOD",
    )
    assert a.bucket == ResearchBucket.CONFLICT
    assert is_early_opportunity_bucket(a.bucket) is False


def test_extended_is_not_developing() -> None:
    a = classify_research_bucket(
        research_state="EXTENDED",
        early_stage_state="EXTENDED",
        development_pattern="OI_MIGRATION",
        pre_breakout_signal=False,
        event_risk="NO_KNOWN_EVENT_RISK",
        liquidity_grade="EXCELLENT",
    )
    assert a.bucket == ResearchBucket.EXTENDED
    assert a.already_moved is True
    assert a.timing == TimingStage.EXTENDED


def test_breakout_confirmation_is_already_moved() -> None:
    a = classify_research_bucket(
        research_state="WATCH",
        early_stage_state="BREAKOUT_CONFIRMATION",
        development_pattern="OI_MIGRATION",
        pre_breakout_signal=False,
        event_risk="NO_KNOWN_EVENT_RISK",
        liquidity_grade="GOOD",
    )
    assert a.bucket == ResearchBucket.ALREADY_MOVED
    assert a.already_moved is True
    assert is_early_opportunity_bucket(a.bucket) is False


def test_false_breakout_without_reclaim_is_not_interesting() -> None:
    a = classify_research_bucket(
        research_state="WATCH",
        early_stage_state="FALSE_BREAKOUT_RISK",
        development_pattern="NONE",
        pre_breakout_signal=False,
        event_risk="NO_KNOWN_EVENT_RISK",
        liquidity_grade="GOOD",
    )
    assert a.bucket == ResearchBucket.NOT_INTERESTING


def test_failed_breakdown_reclaim_can_escape_false_breakout() -> None:
    a = classify_research_bucket(
        research_state="EARLY_SETUP",
        early_stage_state="EARLY_DIRECTIONAL_BUILD",
        development_pattern="FAILED_BREAKDOWN_RECLAIM",
        pre_breakout_signal=False,
        event_risk="NO_KNOWN_EVENT_RISK",
        liquidity_grade="GOOD",
    )
    assert a.bucket == ResearchBucket.DEVELOPING
    assert is_early_opportunity_bucket(a.bucket) is True


def test_illiquid_fo_is_not_an_option_opportunity() -> None:
    a = classify_research_bucket(
        research_state="EARLY_SETUP",
        early_stage_state="EARLY_DIRECTIONAL_BUILD",
        development_pattern="OI_MIGRATION",
        pre_breakout_signal=True,
        event_risk="EVENT_RISK",
        liquidity_grade="UNTRADEABLE",
    )
    assert a.universe_tier == UniverseTier.TIER_1_FO_ILLIQUID
    assert a.bucket == ResearchBucket.NOT_INTERESTING


def test_pre_breakout_combo_is_high_quality_developing() -> None:
    a = classify_research_bucket(
        research_state="WATCH",
        early_stage_state="EARLY_DIRECTIONAL_BUILD",
        development_pattern="PRE_BREAKOUT_COMPRESSION",
        pre_breakout_signal=True,
        event_risk="NO_KNOWN_EVENT_RISK",
        liquidity_grade="MODERATE",
    )
    assert a.bucket == ResearchBucket.HIGH_QUALITY_DEVELOPING
    assert a.timing == TimingStage.EARLY


def test_early_setup_named_pattern_without_pre_breakout_is_developing() -> None:
    a = classify_research_bucket(
        research_state="EARLY_SETUP",
        early_stage_state="DEVELOPING_MOMENTUM",
        development_pattern="RELATIVE_STRENGTH",
        pre_breakout_signal=False,
        event_risk="NO_KNOWN_EVENT_RISK",
        liquidity_grade="GOOD",
    )
    assert a.bucket == ResearchBucket.DEVELOPING


def test_fresh_news_on_early_structure_is_event_driven_not_developing() -> None:
    a = classify_research_bucket(
        research_state="WATCH",
        early_stage_state="RANGE_BOUND",
        development_pattern="NONE",
        pre_breakout_signal=False,
        event_risk="MAJOR_EVENT_RISK",
        liquidity_grade="GOOD",
    )
    assert a.bucket == ResearchBucket.EVENT_DRIVEN
    assert is_early_opportunity_bucket(a.bucket) is False
    assert is_event_to_monitor_bucket(a.bucket) is True


def test_ongc_uat_watch_none_news_is_events_to_monitor_not_developing_setup() -> None:
    """Regression: ONGC UAT card (EVENT_DRIVEN, pattern NONE, WATCH, EARLY)."""
    a = classify_research_bucket(
        research_state="WATCH",
        early_stage_state="EARLY_DIRECTIONAL_BUILD",
        development_pattern="NONE",
        pre_breakout_signal=False,
        event_risk="MAJOR_EVENT_RISK",
        liquidity_grade="GOOD",
    )
    assert a.bucket == ResearchBucket.EVENT_DRIVEN
    assert is_early_opportunity_bucket(a.bucket) is False
    assert is_event_to_monitor_bucket(a.bucket) is True


def test_confirmed_setup_is_not_early() -> None:
    a = classify_research_bucket(
        research_state="CONFIRMED_SETUP",
        early_stage_state="DEVELOPING_MOMENTUM",
        development_pattern="OI_MIGRATION",
        pre_breakout_signal=True,
        event_risk="NO_KNOWN_EVENT_RISK",
        liquidity_grade="EXCELLENT",
    )
    assert a.bucket == ResearchBucket.CONFIRMED
    assert is_early_opportunity_bucket(a.bucket) is False


def test_no_trade_with_pre_breakout_is_not_high_quality() -> None:
    a = classify_research_bucket(
        research_state="NO_TRADE",
        early_stage_state="EARLY_DIRECTIONAL_BUILD",
        development_pattern="NONE",
        pre_breakout_signal=True,
        event_risk="NO_KNOWN_EVENT_RISK",
        liquidity_grade="GOOD",
    )
    assert a.bucket == ResearchBucket.NOT_INTERESTING


def test_watch_without_named_pattern_is_never_a_developing_setup() -> None:
    a = classify_research_bucket(
        research_state="WATCH",
        early_stage_state="EARLY_DIRECTIONAL_BUILD",
        development_pattern="NONE",
        pre_breakout_signal=False,
        event_risk="NO_KNOWN_EVENT",
        liquidity_grade="GOOD",
    )
    assert a.bucket == ResearchBucket.NOT_INTERESTING
    assert is_early_opportunity_bucket(a.bucket) is False


def test_timing_and_tier_helpers() -> None:
    assert classify_timing_stage(research_state="EARLY_SETUP", early_stage_state="RANGE_BOUND") == TimingStage.VERY_EARLY
    assert classify_universe_tier(fo_eligible=True, liquidity_grade="GOOD") == UniverseTier.TIER_1_FO_LIQUID
    assert classify_universe_tier(fo_eligible=False, liquidity_grade="GOOD") == UniverseTier.UNKNOWN
    assert classify_universe_tier(fo_eligible=True, liquidity_grade="GOOD", fo_restricted=True) == UniverseTier.TIER_5_FO_RESTRICTED


def test_move_context_late_is_not_developing_even_with_named_pattern() -> None:
    from decimal import Decimal
    a = classify_research_bucket(
        research_state="EARLY_SETUP",
        early_stage_state="DEVELOPING_MOMENTUM",
        development_pattern="OI_MIGRATION",
        pre_breakout_signal=False,
        event_risk="NO_KNOWN_EVENT",
        liquidity_grade="GOOD",
        day_change_pct=Decimal("5.5"),
    )
    assert a.move_context == MoveContext.MATURE
    assert a.timing == TimingStage.MATURE
    assert a.bucket != ResearchBucket.DEVELOPING
    assert is_early_opportunity_bucket(a.bucket) is False


def test_watch_plus_developing_momentum_without_pattern_is_not_developing_timing() -> None:
    a = classify_research_bucket(
        research_state="WATCH",
        early_stage_state="DEVELOPING_MOMENTUM",
        development_pattern="NONE",
        pre_breakout_signal=False,
        event_risk="NO_KNOWN_EVENT",
        liquidity_grade="GOOD",
    )
    assert a.bucket == ResearchBucket.NOT_INTERESTING
    assert a.timing != TimingStage.DEVELOPING
    assert is_early_opportunity_bucket(a.bucket) is False


def test_compression_keeps_five_percent_move_from_being_called_late() -> None:
    from decimal import Decimal
    ctx = classify_move_context(day_change_pct=Decimal("5.0"), structural_context="RANGE_COMPRESSION")
    assert ctx == MoveContext.BUILDING


def test_intraday_range_can_mark_move_mature_below_six_percent_day_change() -> None:
    from decimal import Decimal
    ctx = classify_move_context(
        day_change_pct=Decimal("2.0"), session_range_pct=Decimal("5.5"),
    )
    assert ctx == MoveContext.MATURE


def test_six_percent_day_change_still_extended() -> None:
    from decimal import Decimal
    ctx = classify_move_context(day_change_pct=Decimal("6.0"))
    assert ctx == MoveContext.EXTENDED


def test_atr_multiple_marks_extended_below_six_percent() -> None:
    from decimal import Decimal
    ctx = classify_move_context(day_change_pct=Decimal("3.0"), atr_pct=Decimal("1.0"))
    assert ctx == MoveContext.EXTENDED


def test_missing_atr_does_not_invent_extension() -> None:
    from decimal import Decimal
    ctx = classify_move_context(day_change_pct=Decimal("3.0"))
    assert ctx == MoveContext.BUILDING

