"""Sprint 2 research-truth: discovery categories stay unmixed."""

from __future__ import annotations

from app.domain.options.early_opportunity import (
    ResearchBucket,
    classify_research_bucket,
    is_early_opportunity_bucket,
    is_event_to_monitor_bucket,
)
from app.domain.options.stage1_discovery import assert_no_stage1_option_claims, promotion_reason


def test_watch_without_pattern_is_never_developing() -> None:
    a = classify_research_bucket(
        research_state="WATCH",
        early_stage_state="EARLY_DIRECTIONAL_BUILD",
        development_pattern="NONE",
        pre_breakout_signal=False,
        event_risk="NO_KNOWN_EVENT",
        liquidity_grade="GOOD",
    )
    assert a.bucket != ResearchBucket.DEVELOPING
    assert is_early_opportunity_bucket(a.bucket) is False


def test_event_driven_is_never_a_developing_setup() -> None:
    a = classify_research_bucket(
        research_state="WATCH",
        early_stage_state="RANGE_BOUND",
        development_pattern="NONE",
        pre_breakout_signal=False,
        event_risk="MAJOR_EVENT_RISK",
        liquidity_grade="EXCELLENT",
    )
    assert a.bucket == ResearchBucket.EVENT_DRIVEN
    assert is_event_to_monitor_bucket(a.bucket) is True
    assert is_early_opportunity_bucket(a.bucket) is False


def test_promotion_reason_has_no_numeric_score() -> None:
    text = promotion_reason(("INTRADAY_COMPRESSION", "RELATIVE_STRENGTH_VS_INDEX"))
    assert "7.8" not in text
    assert "score" not in text.lower()
    assert_no_stage1_option_claims(("INTRADAY_COMPRESSION", "RELATIVE_STRENGTH_VS_INDEX"))
