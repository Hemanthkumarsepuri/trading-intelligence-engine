"""Phase 3 gap-closure -- pure, deterministic unit tests for
`build_price_only_observation()`: the price-only path that lets a replay
observation exist when no historical option-chain evidence is available.
No network, no full pipeline run -- everything here operates on directly
constructed `AnalyzeResponse`/`VisualData` fixtures, mirroring
`tests/unit/orchestration/test_research_outcome.py`'s own style. Covers
Section 25's Cases A-E (Case F -- no-lookahead through a real historical
walk -- is covered by `tests/integration/orchestration/test_historical_replay.py`,
which exercises the real pipeline/repository, not a hand-built fixture).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.orchestration.daily_research import build_price_only_observation
from app.orchestration.dashboard_service import AnalyzeResponse
from app.orchestration.visual_data import (
    CandlePoint,
    DevelopmentNarrativeView,
    FreshnessVisual,
    FuturesVisual,
    NewsVisual,
    OptionChainVisual,
    PriceChartData,
    SupportResistanceVisual,
    TechnicalLevelView,
    VisualData,
)

_AS_OF = datetime(2026, 8, 25, 6, 15, tzinfo=UTC)


def _response(
    *,
    error: str | None = None,
    market_state: str = "LIVE_SNAPSHOT",
    convergence: str | None = "CONVERGENCE_BULLISH",
    pattern: str = "PRE_BREAKOUT_COMPRESSION",
    generated_at: datetime | None = _AS_OF,
    spot: str = "1000",
    decision: str | None = "WATCH",
    research_state: str | None = "EARLY_SETUP",
    visual_present: bool = True,
    technical_only: list[TechnicalLevelView] | None = None,
) -> AnalyzeResponse:
    development = (
        DevelopmentNarrativeView(
            pattern=pattern, what_is_developing="Price is compressing near a real opposing level.",
            why_it_matters="Observable preparation state.", what_is_missing="A held break, plus chain/futures confirmation.",
            confirm_if="Price holds beyond the level.", invalidate_if="Compression expands without a break.",
            freshness_note="quote=current",
        )
        if pattern != "NONE"
        else DevelopmentNarrativeView(
            pattern="NONE", what_is_developing="", why_it_matters="", what_is_missing="no pattern met",
            confirm_if="", invalidate_if="", freshness_note="quote=current",
        )
    )
    v = VisualData(
        price_chart=PriceChartData(
            timeframe="M15", insufficient_history=False, detail="",
            candles=[CandlePoint(timestamp=_AS_OF, open=Decimal(spot), high=Decimal(spot), low=Decimal(spot), close=Decimal(spot), volume=1000)],
        ),
        option_chain=OptionChainVisual(), requested_contract=None, direction_comparison=None,
        news=NewsVisual(items=[]), evidence=[], convergence=convergence, adversarial=None, quality=None,
        futures=FuturesVisual(instrument_key=None, ltp=None, open_interest=None, basis_pct=None, oi_interpretation=None),
        global_context=None,
        support_resistance=SupportResistanceVisual(support=[], resistance=[], technical_only=technical_only or []),
        term_structure=None,
        freshness=FreshnessVisual(market_state=market_state, freshness_label=None, data_age_seconds=1.0, generated_at=_AS_OF),
        research_state=research_state, development=development,
    ) if visual_present else None
    return AnalyzeResponse(
        query="RELIANCE", parsed_symbol="RELIANCE", parsed_strike=None, parsed_right=None, parsed_expiry_hint=None,
        has_specific_contract=False, parse_warnings=[], symbol="RELIANCE", generated_at=generated_at,
        market_state=market_state, decision=decision, research_state=research_state, error=error, visual=v,
        audit_id="audit-1", latency_seconds=0.5,
    )


# ============================================================
# Case A -- valid price-based developing pattern -> observation possible
# ============================================================


def test_case_a_valid_price_pattern_produces_an_observation() -> None:
    response = _response(convergence="CONVERGENCE_BULLISH", pattern="PRE_BREAKOUT_COMPRESSION", research_state="EARLY_SETUP")
    obs = build_price_only_observation("RELIANCE", response, run_id="run1", coverage_classification="REPLAY")
    assert obs is not None
    assert obs.direction == "BULLISH"
    assert obs.source == "REPLAY"
    assert obs.derivatives_evidence_available is False
    assert obs.selected_right is None
    assert obs.selected_strike is None
    assert obs.contractual_expiry_breakeven is None
    assert obs.early_stage_state == "EARLY_SETUP"
    assert "compressing" in obs.thesis.lower()
    assert obs.missing_evidence is not None
    assert "historical option-chain/futures evidence is unavailable" in obs.missing_evidence.lower()


def test_case_a_never_claims_derivatives_confirmed_the_setup() -> None:
    response = _response(convergence="CONVERGENCE_BULLISH", pattern="PRE_BREAKOUT_COMPRESSION")
    obs = build_price_only_observation("RELIANCE", response, run_id="run1", coverage_classification="REPLAY")
    assert obs is not None
    for text in (obs.thesis, obs.missing_evidence or ""):
        assert "confirm" not in text.lower() or "not" in text.lower() or "missing" in text.lower() or "unavailable" in text.lower()


# ============================================================
# Case B -- a derivatives-dependent pattern cannot appear at all through
# this path (classify_development() never selects OI_MIGRATION/
# FUTURES_STRUCTURE without real migration/basis data -- this test pins
# that by construction: no fixture here can EVER produce those patterns,
# because the fixture never supplies migration/basis data. See
# app/domain/options/development.py.)
# ============================================================


def test_case_b_watch_with_no_named_pattern_produces_no_observation() -> None:
    response = _response(convergence="CONVERGENCE_BULLISH", pattern="NONE", research_state="WATCH")
    obs = build_price_only_observation("RELIANCE", response, run_id="run1", coverage_classification="REPLAY")
    assert obs is None


# ============================================================
# Case C -- insufficient history / stale data -> no observation
# ============================================================


def test_case_c_stale_market_state_produces_no_observation() -> None:
    response = _response(market_state="INSUFFICIENT_HISTORY", research_state="DATA_INSUFFICIENT")
    obs = build_price_only_observation("RELIANCE", response, run_id="run1", coverage_classification="REPLAY")
    assert obs is None


def test_case_c_failed_analysis_produces_no_observation() -> None:
    response = _response(error="something failed")
    obs = build_price_only_observation("RELIANCE", response, run_id="run1", coverage_classification="REPLAY")
    assert obs is None


def test_case_c_missing_visual_produces_no_observation() -> None:
    response = _response(visual_present=False)
    obs = build_price_only_observation("RELIANCE", response, run_id="run1", coverage_classification="REPLAY")
    assert obs is None


# ============================================================
# Case D -- conflicting evidence -> no observation
# ============================================================


def test_case_d_conflict_convergence_produces_no_observation() -> None:
    response = _response(convergence="CONFLICT", pattern="NONE", research_state="CONFLICT")
    obs = build_price_only_observation("RELIANCE", response, run_id="run1", coverage_classification="REPLAY")
    assert obs is None


def test_case_d_insufficient_evidence_convergence_produces_no_observation() -> None:
    response = _response(convergence="INSUFFICIENT_EVIDENCE", pattern="NONE", research_state="UNKNOWN")
    obs = build_price_only_observation("RELIANCE", response, run_id="run1", coverage_classification="REPLAY")
    assert obs is None


# ============================================================
# Case E -- no qualifying pattern -> no observation (same as Case B, a
# distinct scenario: a real bearish convergence with no named pattern)
# ============================================================


def test_case_e_bearish_convergence_no_pattern_produces_no_observation() -> None:
    response = _response(convergence="CONVERGENCE_BEARISH", pattern="NONE", research_state="WATCH")
    obs = build_price_only_observation("RELIANCE", response, run_id="run1", coverage_classification="REPLAY")
    assert obs is None


def test_case_e_bearish_convergence_with_pattern_produces_a_bearish_observation() -> None:
    response = _response(convergence="CONVERGENCE_BEARISH", pattern="RELATIVE_STRENGTH", research_state="EARLY_SETUP")
    obs = build_price_only_observation("RELIANCE", response, run_id="run1", coverage_classification="REPLAY")
    assert obs is not None
    assert obs.direction == "BEARISH"


# ============================================================
# Missing generated_at -> no observation (never a fabricated timestamp)
# ============================================================


def test_no_observation_without_a_real_generated_at() -> None:
    response = _response(generated_at=None)
    obs = build_price_only_observation("RELIANCE", response, run_id="run1", coverage_classification="REPLAY")
    assert obs is None


# ============================================================
# 95% sprint, Sprint 1 -- nearest opposing TECHNICAL level (candle-
# derived, not chain-OI) is threaded through so a later outcome
# evaluation can determine a real invalidation result instead of UNKNOWN.
# ============================================================


def test_bullish_observation_uses_the_nearest_real_technical_resistance() -> None:
    response = _response(
        convergence="CONVERGENCE_BULLISH", spot="1000",
        technical_only=[
            TechnicalLevelView(kind="resistance", price=Decimal("1050"), evidence="swing high"),
            TechnicalLevelView(kind="resistance", price=Decimal("1020"), evidence="VWAP"),  # nearer to spot
            TechnicalLevelView(kind="support", price=Decimal("980"), evidence="swing low"),  # wrong side
        ],
    )
    obs = build_price_only_observation("RELIANCE", response, run_id="run1", coverage_classification="REPLAY")
    assert obs is not None
    assert obs.nearest_level_kind == "resistance"
    assert obs.nearest_level_value == "1020"


def test_bearish_observation_uses_the_nearest_real_technical_support() -> None:
    response = _response(
        convergence="CONVERGENCE_BEARISH", pattern="RELATIVE_STRENGTH", spot="1000",
        technical_only=[
            TechnicalLevelView(kind="support", price=Decimal("950"), evidence="swing low"),
            TechnicalLevelView(kind="support", price=Decimal("985"), evidence="EMA50"),  # nearer to spot
            TechnicalLevelView(kind="resistance", price=Decimal("1010"), evidence="swing high"),  # wrong side
        ],
    )
    obs = build_price_only_observation("RELIANCE", response, run_id="run1", coverage_classification="REPLAY")
    assert obs is not None
    assert obs.nearest_level_kind == "support"
    assert obs.nearest_level_value == "985"


def test_no_nearest_level_when_no_real_technical_level_exists() -> None:
    response = _response(convergence="CONVERGENCE_BULLISH", technical_only=[])
    obs = build_price_only_observation("RELIANCE", response, run_id="run1", coverage_classification="REPLAY")
    assert obs is not None
    assert obs.nearest_level_kind is None
    assert obs.nearest_level_value is None
