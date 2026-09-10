from __future__ import annotations

from decimal import Decimal

from app.domain.options.early_opportunity import (
    MoveContext,
    classify_move_context,
    classify_research_bucket,
    is_early_opportunity_bucket,
)
from app.domain.options.stage1_discovery import (
    FAILED_BREAKDOWN_RECLAIM_CANDIDATE,
    PRE_BREAKOUT_COMPRESSION_CANDIDATE,
    RELATIVE_STRENGTH_VS_INDEX,
    assert_no_stage1_option_claims,
    promotion_reason,
)
from app.orchestration.daily_research import ScreeningConfig, _discovery_buckets, _quote_metrics
from tests.unit.orchestration.test_daily_research_ranking import _raw_quote

_CFG = ScreeningConfig()


def test_stage1_never_emits_option_chain_claims() -> None:
    m = _quote_metrics(_raw_quote(
        last_price=100.4, previous_close=100.0, ohlc_high=100.5, ohlc_low=100.1, average_price=100.2,
        buy_qty=1_300_000, sell_qty=700_000,
    ))
    buckets = _discovery_buckets(m, _CFG, index_day_change_pct=Decimal("0.0"))
    assert_no_stage1_option_claims(buckets)
    assert "OI_MIGRATION" not in buckets
    assert "FUTURES_STRUCTURE" not in buckets


def test_compression_near_session_high_is_pre_breakout_candidate_not_a_stage2_pattern() -> None:
    m = _quote_metrics(_raw_quote(
        last_price=100.4, previous_close=100.0, ohlc_high=100.5, ohlc_low=99.8,
    ))
    buckets = _discovery_buckets(m, _CFG)
    assert PRE_BREAKOUT_COMPRESSION_CANDIDATE in buckets
    assert "PRE_BREAKOUT_COMPRESSION" not in buckets


def test_quiet_midrange_compression_is_not_promoted() -> None:
    m = _quote_metrics(_raw_quote(
        last_price=100.25, previous_close=100.0, ohlc_high=100.5, ohlc_low=100.0,
    ))
    assert PRE_BREAKOUT_COMPRESSION_CANDIDATE not in _discovery_buckets(m, _CFG)


def test_reclaim_observation_is_named_without_claiming_failed_breakdown_pattern() -> None:
    m = _quote_metrics(_raw_quote(last_price=99.0, previous_close=100.0, ohlc_high=99.5, ohlc_low=95.0))
    buckets = _discovery_buckets(m, _CFG)
    assert FAILED_BREAKDOWN_RECLAIM_CANDIDATE in buckets
    assert "EARLY_REVERSAL" in buckets


def test_relative_strength_vs_index_requires_real_index_change() -> None:
    m = _quote_metrics(_raw_quote(last_price=102.0, previous_close=100.0, average_price=101.5, ohlc_high=102.2, ohlc_low=99.8))
    assert RELATIVE_STRENGTH_VS_INDEX not in _discovery_buckets(m, _CFG)
    assert RELATIVE_STRENGTH_VS_INDEX in _discovery_buckets(m, _CFG, index_day_change_pct=Decimal("0.1"))


def test_promotion_reason_is_named_observations_not_a_score() -> None:
    text = promotion_reason((PRE_BREAKOUT_COMPRESSION_CANDIDATE, RELATIVE_STRENGTH_VS_INDEX))
    assert "Score" not in text
    assert "compression candidate (not multi-day proof)" in text
    assert "multi-day compression" not in text.lower()
    assert "PRE_BREAKOUT_COMPRESSION_CANDIDATE" not in text
    assert text.startswith("Promoted because:")


def test_session_range_can_mark_already_moved_without_six_percent_day_change() -> None:
    a = classify_research_bucket(
        research_state="EARLY_SETUP",
        early_stage_state="DEVELOPING_MOMENTUM",
        development_pattern="OI_MIGRATION",
        pre_breakout_signal=False,
        event_risk="NO_KNOWN_EVENT",
        liquidity_grade="GOOD",
        day_change_pct=Decimal("2.0"),
        session_range_pct=Decimal("5.5"),
    )
    assert a.move_context == MoveContext.MATURE
    assert is_early_opportunity_bucket(a.bucket) is False


def test_classify_move_context_does_not_use_day_change_alone_when_compressing() -> None:
    ctx = classify_move_context(
        day_change_pct=Decimal("4.5"),
        structural_context="RANGE_COMPRESSION",
        session_range_pct=Decimal("1.0"),
    )
    assert ctx == MoveContext.BUILDING
