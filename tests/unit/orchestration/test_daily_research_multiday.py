"""Sprint 4 -- pure unit tests for the multi-day early-move context
classifiers (`classify_structural_context`/`classify_participation_depth`/
`classify_relative_strength`/`_pre_breakout_signal`/`_multi_day_windows`)
in `app.orchestration.daily_research`. Same conventions as
`test_daily_research_ranking.py`: real Pydantic view objects hand-built
with only the fields each function actually reads set to meaningful
values, no I/O, no real pipeline fetch. Helpers are deliberately
duplicated here rather than imported from that file, matching this
project's established per-test-file convention.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.orchestration.daily_research import (
    MULTI_DAY_BASELINE_MIN_CANDLES,
    MULTI_DAY_RECENT_CANDLES,
    _GatedCandidate,
    _multi_day_windows,
    _pre_breakout_signal,
    classify_participation_depth,
    classify_relative_strength,
    classify_structural_context,
)
from app.orchestration.dashboard_service import AnalyzeResponse
from app.orchestration.visual_data import (
    CandlePoint,
    ContractAssessmentView,
    ContractCaseView,
    DirectionalPathsView,
    DirectionComparisonVisual,
    FreshnessVisual,
    FuturesVisual,
    GlobalContextInputView,
    GlobalContextVisual,
    LevelView,
    NewsVisual,
    OptionChainVisual,
    PriceChartData,
    SupportResistanceVisual,
    VisualData,
)


def _flat_candles(n: int, *, high: str, low: str, volume: int = 1000) -> list[CandlePoint]:
    return [
        CandlePoint(
            timestamp=datetime(2026, 8, 31, 9, 15, tzinfo=UTC), open=Decimal(low), high=Decimal(high),
            low=Decimal(low), close=Decimal(low), volume=volume,
        )
        for _ in range(n)
    ]


def _closing_move_candles(n: int, *, start_close: str, end_close: str, volume: int = 1000) -> list[CandlePoint]:
    """A minimal real M15 series where only the FIRST and LAST candle's
    close differ (everything in between flat at `start_close`) -- exactly
    what `classify_relative_strength()` reads (`recent[0].close`/
    `recent[-1].close`), nothing more."""
    candles = _flat_candles(n, high=start_close, low=start_close, volume=volume)
    candles[-1] = CandlePoint(
        timestamp=datetime(2026, 8, 31, 10, 0, tzinfo=UTC), open=Decimal(start_close), high=Decimal(end_close),
        low=Decimal(start_close), close=Decimal(end_close), volume=volume,
    )
    return candles


def _dc(*, preferred_direction: str, preferred_contract: ContractAssessmentView | None) -> DirectionComparisonVisual:
    return DirectionComparisonVisual(
        reference_strike=Decimal("1000"), verdict="BULLISH_SIDE_BETTER_SUPPORTED", verdict_detail="test",
        ce_assessment=None, pe_assessment=None, ce_alternatives=[], pe_alternatives=[], ce_decay=None, pe_decay=None,
        ce_case=ContractCaseView(), pe_case=ContractCaseView(), ce_hedge_context="", pe_hedge_context="", paths=DirectionalPathsView(spot=Decimal("1000")),
        bullish_supporting_groups=1, bearish_supporting_groups=0, conflicting_groups=0,
        preferred_direction=preferred_direction, preferred_contract=preferred_contract, no_defensible_direction=False,
    )


def _visual(
    *, candles: list[CandlePoint] | None = None, global_context: GlobalContextVisual | None = None,
    resistance: list[LevelView] | None = None, support: list[LevelView] | None = None,
) -> VisualData:
    return VisualData(
        price_chart=PriceChartData(timeframe="M15", insufficient_history=False, detail="", candles=candles or []),
        option_chain=OptionChainVisual(), requested_contract=None, direction_comparison=None,
        news=NewsVisual(items=[]), evidence=[], convergence=None, adversarial=None, quality=None,
        futures=FuturesVisual(instrument_key=None, ltp=None, open_interest=None, basis_pct=None, oi_interpretation=None),
        global_context=global_context,
        support_resistance=SupportResistanceVisual(support=support or [], resistance=resistance or []),
        term_structure=None,
        freshness=FreshnessVisual(market_state="LIVE_SNAPSHOT", freshness_label=None, data_age_seconds=1.0, generated_at=datetime(2026, 8, 31, tzinfo=UTC)),
    )


def _response(visual: VisualData) -> AnalyzeResponse:
    return AnalyzeResponse(
        query="X", parsed_symbol="X", parsed_strike=None, parsed_right=None, parsed_expiry_hint=None,
        has_specific_contract=False, parse_warnings=[], symbol="X", market_state="LIVE_SNAPSHOT",
        decision="WATCH", error=None, visual=visual, watch_next=[], latency_seconds=0.01,
        audit_id="audit-X", generated_at=datetime(2026, 8, 31, 10, 0, tzinfo=UTC),
    )


def _gated(*, direction: str = "BULLISH", visual: VisualData) -> _GatedCandidate:
    return _GatedCandidate(
        symbol="X", response=_response(visual), dc=_dc(preferred_direction=direction, preferred_contract=None),
        contract=ContractAssessmentView(
            strike=Decimal("1000"), right="CE", moneyness=None, ltp=Decimal("10"), bid=None, ask=None,
            spread_pct=Decimal("1.0"), open_interest=100000, change_in_open_interest=1000, volume=50000,
            implied_volatility=Decimal("20"), delta=Decimal("0.5"), gamma=None, theta=Decimal("-1.0"), vega=None,
            liquidity_grade="excellent", distance_to_support_pct=None, distance_to_resistance_pct=None,
            decay_verdict="DECAY_FAVORABLE", decay_viability_ratio=None, required_underlying_move=None,
            required_underlying_move_pct=Decimal("1.0"), contractual_expiry_breakeven=None,
            expected_move_over_horizon=None, expected_move_over_horizon_pct=None, scenarios=[], scope_note=None,
            structural_quality="ACCEPTABLE", data_quality_notes=[],
        ),
        direction=direction,
    )


SUFFICIENT = MULTI_DAY_RECENT_CANDLES + MULTI_DAY_BASELINE_MIN_CANDLES


# ---- _multi_day_windows ----------------------------------------------

def test_multi_day_windows_none_when_no_visual() -> None:
    gated = _gated(visual=_visual())
    gated_no_visual = _GatedCandidate(symbol="X", response=AnalyzeResponse(
        query="X", parsed_symbol="X", parsed_strike=None, parsed_right=None, parsed_expiry_hint=None,
        has_specific_contract=False, parse_warnings=[], symbol="X", market_state=None, decision=None,
        error=None, visual=None, watch_next=[], latency_seconds=0.01, audit_id=None, generated_at=None,
    ), dc=gated.dc, contract=gated.contract, direction="BULLISH")
    assert _multi_day_windows(gated_no_visual.response) is None


def test_multi_day_windows_none_when_insufficient_candles() -> None:
    candles = _flat_candles(SUFFICIENT - 1, high="1010", low="990")
    gated = _gated(visual=_visual(candles=candles))
    assert _multi_day_windows(gated.response) is None


def test_multi_day_windows_splits_recent_and_baseline() -> None:
    candles = _flat_candles(SUFFICIENT, high="1010", low="990")
    gated = _gated(visual=_visual(candles=candles))
    windows = _multi_day_windows(gated.response)
    assert windows is not None
    recent, baseline = windows
    assert len(recent) == MULTI_DAY_RECENT_CANDLES
    assert len(baseline) == MULTI_DAY_BASELINE_MIN_CANDLES


# ---- classify_structural_context --------------------------------------

def test_structural_context_insufficient_data() -> None:
    gated = _gated(visual=_visual(candles=_flat_candles(10, high="1010", low="990")))
    state, detail = classify_structural_context(gated)
    assert state == "INSUFFICIENT_DATA"
    assert "not enough real" in detail


def test_structural_context_insufficient_data_when_baseline_flat() -> None:
    baseline = _flat_candles(MULTI_DAY_BASELINE_MIN_CANDLES, high="1000", low="1000")
    recent = _flat_candles(MULTI_DAY_RECENT_CANDLES, high="1010", low="990")
    gated = _gated(visual=_visual(candles=baseline + recent))
    state, detail = classify_structural_context(gated)
    assert state == "INSUFFICIENT_DATA"
    assert "no real range" in detail


def test_structural_context_range_compression() -> None:
    baseline = _flat_candles(MULTI_DAY_BASELINE_MIN_CANDLES, high="1050", low="950")  # range 100
    recent = _flat_candles(MULTI_DAY_RECENT_CANDLES, high="1010", low="990")  # range 20 -> ratio 0.2
    gated = _gated(visual=_visual(candles=baseline + recent))
    state, detail = classify_structural_context(gated)
    assert state == "RANGE_COMPRESSION"
    assert "0.20x" in detail


def test_structural_context_normal_range() -> None:
    baseline = _flat_candles(MULTI_DAY_BASELINE_MIN_CANDLES, high="1050", low="950")  # range 100
    recent = _flat_candles(MULTI_DAY_RECENT_CANDLES, high="1040", low="960")  # range 80 -> ratio 0.8
    gated = _gated(visual=_visual(candles=baseline + recent))
    state, _detail = classify_structural_context(gated)
    assert state == "NORMAL_RANGE"


# ---- classify_participation_depth --------------------------------------

def test_participation_depth_unavailable_insufficient_candles() -> None:
    gated = _gated(visual=_visual(candles=_flat_candles(10, high="1010", low="990")))
    state, _detail = classify_participation_depth(gated)
    assert state == "PARTICIPATION_UNAVAILABLE"


def test_participation_depth_unavailable_zero_baseline_volume() -> None:
    baseline = _flat_candles(MULTI_DAY_BASELINE_MIN_CANDLES, high="1010", low="990", volume=0)
    recent = _flat_candles(MULTI_DAY_RECENT_CANDLES, high="1010", low="990", volume=1000)
    gated = _gated(visual=_visual(candles=baseline + recent))
    state, detail = classify_participation_depth(gated)
    assert state == "PARTICIPATION_UNAVAILABLE"
    assert "no real volume" in detail


def test_participation_depth_build() -> None:
    baseline = _flat_candles(MULTI_DAY_BASELINE_MIN_CANDLES, high="1010", low="990", volume=1000)
    recent = _flat_candles(MULTI_DAY_RECENT_CANDLES, high="1010", low="990", volume=2000)  # ratio 2.0
    gated = _gated(visual=_visual(candles=baseline + recent))
    state, _detail = classify_participation_depth(gated)
    assert state == "PARTICIPATION_BUILD"


def test_participation_depth_weak() -> None:
    baseline = _flat_candles(MULTI_DAY_BASELINE_MIN_CANDLES, high="1010", low="990", volume=1000)
    recent = _flat_candles(MULTI_DAY_RECENT_CANDLES, high="1010", low="990", volume=500)  # ratio 0.5
    gated = _gated(visual=_visual(candles=baseline + recent))
    state, _detail = classify_participation_depth(gated)
    assert state == "PARTICIPATION_WEAK"


def test_participation_depth_confirming() -> None:
    baseline = _flat_candles(MULTI_DAY_BASELINE_MIN_CANDLES, high="1010", low="990", volume=1000)
    recent = _flat_candles(MULTI_DAY_RECENT_CANDLES, high="1010", low="990", volume=1050)  # ratio 1.05
    gated = _gated(visual=_visual(candles=baseline + recent))
    state, _detail = classify_participation_depth(gated)
    assert state == "PARTICIPATION_CONFIRMING"


# ---- classify_relative_strength -----------------------------------------

def test_relative_strength_unavailable_no_global_context() -> None:
    candles = _closing_move_candles(SUFFICIENT, start_close="1000", end_close="1050")
    gated = _gated(visual=_visual(candles=candles, global_context=None))
    state, _detail = classify_relative_strength(gated)
    assert state == "RELATIVE_STRENGTH_DATA_UNAVAILABLE"


def test_relative_strength_unavailable_no_nifty_input() -> None:
    candles = _closing_move_candles(SUFFICIENT, start_close="1000", end_close="1050")
    gc = GlobalContextVisual(inputs=[], verdict="MIXED", detail="no nifty")
    gated = _gated(visual=_visual(candles=candles, global_context=gc))
    state, _detail = classify_relative_strength(gated)
    assert state == "RELATIVE_STRENGTH_DATA_UNAVAILABLE"


def test_relative_strength_unavailable_insufficient_candles() -> None:
    gc = GlobalContextVisual(
        inputs=[GlobalContextInputView(label="NIFTY 50", day_change_pct=Decimal("1.0"), contributes_to_verdict=True, detail="d")],
        verdict="GLOBAL_TAILWIND", detail="d",
    )
    gated = _gated(visual=_visual(candles=_flat_candles(10, high="1010", low="990"), global_context=gc))
    state, _detail = classify_relative_strength(gated)
    assert state == "RELATIVE_STRENGTH_DATA_UNAVAILABLE"


def test_relative_strength_index_flat() -> None:
    candles = _closing_move_candles(SUFFICIENT, start_close="1000", end_close="1050")
    gc = GlobalContextVisual(
        inputs=[GlobalContextInputView(label="NIFTY 50", day_change_pct=Decimal("0.1"), contributes_to_verdict=True, detail="d")],
        verdict="MIXED", detail="d",
    )
    gated = _gated(visual=_visual(candles=candles, global_context=gc))
    state, _detail = classify_relative_strength(gated)
    assert state == "RELATIVE_STRENGTH_INDEX_FLAT"


def test_relative_strength_aligned() -> None:
    candles = _closing_move_candles(SUFFICIENT, start_close="1000", end_close="1050")  # +5%
    gc = GlobalContextVisual(
        inputs=[GlobalContextInputView(label="NIFTY 50", day_change_pct=Decimal("1.0"), contributes_to_verdict=True, detail="d")],
        verdict="GLOBAL_TAILWIND", detail="d",
    )
    gated = _gated(visual=_visual(candles=candles, global_context=gc))
    state, _detail = classify_relative_strength(gated)
    assert state == "RELATIVE_STRENGTH_ALIGNED"


def test_relative_strength_diverging() -> None:
    candles = _closing_move_candles(SUFFICIENT, start_close="1000", end_close="950")  # -5%
    gc = GlobalContextVisual(
        inputs=[GlobalContextInputView(label="NIFTY 50", day_change_pct=Decimal("1.0"), contributes_to_verdict=True, detail="d")],
        verdict="GLOBAL_TAILWIND", detail="d",
    )
    gated = _gated(visual=_visual(candles=candles, global_context=gc))
    state, _detail = classify_relative_strength(gated)
    assert state == "RELATIVE_STRENGTH_DIVERGING"


# ---- _pre_breakout_signal -----------------------------------------------

def test_pre_breakout_true_early_directional_build() -> None:
    level = LevelView(kind="resistance", strike=Decimal("1020"), strength="moderate", stability="STABLE", distance_from_spot_pct=Decimal("2.0"), distance_pct=Decimal("2.0"), evidence="test", stability_detail="test")
    gated = _gated(direction="BULLISH", visual=_visual(resistance=[level]))
    signal, detail = _pre_breakout_signal(gated, early_stage_state="EARLY_DIRECTIONAL_BUILD", structural_context="RANGE_COMPRESSION")
    assert signal is True
    assert detail is not None
    assert "2.00%" in detail


def test_pre_breakout_true_developing_momentum() -> None:
    level = LevelView(kind="resistance", strike=Decimal("1020"), strength="moderate", stability="STABLE", distance_from_spot_pct=Decimal("1.0"), distance_pct=Decimal("1.0"), evidence="test", stability_detail="test")
    gated = _gated(direction="BULLISH", visual=_visual(resistance=[level]))
    signal, _ = _pre_breakout_signal(gated, early_stage_state="DEVELOPING_MOMENTUM", structural_context="RANGE_COMPRESSION")
    assert signal is True


def test_pre_breakout_false_wrong_early_stage() -> None:
    level = LevelView(kind="resistance", strike=Decimal("1020"), strength="moderate", stability="STABLE", distance_from_spot_pct=Decimal("2.0"), distance_pct=Decimal("2.0"), evidence="test", stability_detail="test")
    gated = _gated(direction="BULLISH", visual=_visual(resistance=[level]))
    signal, detail = _pre_breakout_signal(gated, early_stage_state="RANGE_BOUND", structural_context="RANGE_COMPRESSION")
    assert signal is False
    assert detail is None


def test_pre_breakout_false_wrong_structural_context() -> None:
    level = LevelView(kind="resistance", strike=Decimal("1020"), strength="moderate", stability="STABLE", distance_from_spot_pct=Decimal("2.0"), distance_pct=Decimal("2.0"), evidence="test", stability_detail="test")
    gated = _gated(direction="BULLISH", visual=_visual(resistance=[level]))
    signal, detail = _pre_breakout_signal(gated, early_stage_state="EARLY_DIRECTIONAL_BUILD", structural_context="NORMAL_RANGE")
    assert signal is False
    assert detail is None


def test_pre_breakout_false_headroom_too_large() -> None:
    level = LevelView(kind="resistance", strike=Decimal("1050"), strength="moderate", stability="STABLE", distance_from_spot_pct=Decimal("5.0"), distance_pct=Decimal("5.0"), evidence="test", stability_detail="test")
    gated = _gated(direction="BULLISH", visual=_visual(resistance=[level]))
    signal, detail = _pre_breakout_signal(gated, early_stage_state="EARLY_DIRECTIONAL_BUILD", structural_context="RANGE_COMPRESSION")
    assert signal is False
    assert detail is None


def test_pre_breakout_false_no_headroom_data() -> None:
    gated = _gated(direction="BULLISH", visual=_visual(resistance=[]))
    signal, detail = _pre_breakout_signal(gated, early_stage_state="EARLY_DIRECTIONAL_BUILD", structural_context="RANGE_COMPRESSION")
    assert signal is False
    assert detail is None
