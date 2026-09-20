"""Product Effectiveness Patch, P1 #1 -- proves the POPULATED case: when
the underlying report genuinely has a decision, a selected candidate, and
an invalidation level, `run_analysis()` copies them into `AnalyzeResponse`
verbatim (never reworded, never recomputed). The real-provider API test
in `test_dashboard_api.py` covers the honest NOT-fabricated case (a
NO_TRADE/conflicted decision correctly yields `None` for all three) --
this file covers the other half with a controlled, minimal report."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.market.data_state import MarketDataState
from app.domain.market.models import OptionRight
from app.domain.options.candidate_engine import OptionCandidate
from app.domain.options.decision_engine import (
    DecisionResult,
    FinalDecision,
    QualityAssessment,
    QualityLevel,
)
from app.domain.options.evidence_matrix import EvidenceDirection, OverallConvergence
from app.domain.options.liquidity import LiquidityAssessment, LiquidityGrade
from app.orchestration.dashboard_service import run_analysis
from app.orchestration.options_intelligence_pipeline import PipelineConfig, Repositories
from app.orchestration.options_intelligence_report import OptionsIntelligenceReport
from app.persistence.jsonl_file import (
    JsonlIvObservationRepository,
    JsonlOptionChainRepository,
    JsonlQuoteRepository,
)

GENERATED_AT = datetime(2026, 8, 31, 4, 0, tzinfo=UTC)


def _candidate() -> OptionCandidate:
    liquidity = LiquidityAssessment(grade=LiquidityGrade.EXCELLENT, spread_fraction=Decimal("0.01"), volume=8000, open_interest=80000, is_stale=False, reasons=[])
    return OptionCandidate(
        instrument_key="NSE_FO|1", underlying="RELIANCE", strike=Decimal("1300"), right=OptionRight.CE,
        ltp=Decimal("30"), bid=Decimal("29.5"), ask=Decimal("30.5"), spread_pct=Decimal("3"),
        open_interest=80000, change_in_open_interest=5000, volume=8000, implied_volatility=Decimal("18"),
        delta=Decimal("0.5"), theta=Decimal("-2.0"), gamma=Decimal("0.01"), vega=Decimal("1.0"),
        liquidity=liquidity, is_atm=True, strikes_from_atm=0,
        invalidation_condition="thesis invalidated if the underlying closes decisively below 1290",
    )


def _decision() -> DecisionResult:
    assessment = QualityAssessment(
        market_bias=EvidenceDirection.BULLISH, convergence=OverallConvergence.CONVERGENCE_BULLISH, setup_quality=QualityLevel.STRONG,
        option_quality=QualityLevel.STRONG, liquidity_quality=QualityLevel.STRONG, data_quality=QualityLevel.STRONG,
        risk_quality=QualityLevel.MODERATE, supporting_evidence_count=4, conflicting_evidence_count=0,
    )
    return DecisionResult(decision=FinalDecision.WATCH, assessment=assessment, reasoning="M15 trend ascending, above VWAP, OI concentration supports a bullish read.")


def _fake_report() -> OptionsIntelligenceReport:
    return OptionsIntelligenceReport(
        symbol="RELIANCE", underlying_instrument_key="NSE_EQ|X", generated_at=GENERATED_AT,
        data_state=MarketDataState.LIVE_SNAPSHOT, data_age_seconds=1.0, spot=Decimal("1300"),
        decision=_decision(), candidates=[_candidate()], invalidation_level=Decimal("1290"),
    )


def test_run_analysis_exposes_reasoning_and_invalidation_when_the_report_has_them(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def _fake_analyze_symbol(*args: object, **kwargs: object) -> OptionsIntelligenceReport:
        return _fake_report()

    monkeypatch.setattr("app.orchestration.dashboard_service.analyze_symbol", _fake_analyze_symbol)

    repositories = Repositories(
        quotes=JsonlQuoteRepository(tmp_path / "quotes.jsonl"), option_chains=JsonlOptionChainRepository(tmp_path / "chains.jsonl"),
        iv_observations=JsonlIvObservationRepository(tmp_path / "iv.jsonl"),
    )
    response = asyncio.run(run_analysis(
        "RELIANCE", provider=None, instrument_master=[], strategy=None,  # type: ignore[arg-type]
        repositories=repositories, config=PipelineConfig(), as_of=GENERATED_AT,
    ))

    assert response.reasoning == "M15 trend ascending, above VWAP, OI concentration supports a bullish read."
    assert response.invalidation_level == "1290"
    assert response.invalidation_condition == "thesis invalidated if the underlying closes decisively below 1290"
    # Copied verbatim -- must also appear in the rendered text report,
    # never a second, independently-worded copy.
    assert response.detailed_report is not None
    assert response.reasoning is not None and response.reasoning in response.detailed_report
    assert response.invalidation_condition is not None and response.invalidation_condition in response.detailed_report
    assert response.tomorrow_watch is None


def test_run_analysis_attaches_tomorrow_watch_when_the_session_is_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def _fake_analyze_symbol(*args: object, **kwargs: object) -> OptionsIntelligenceReport:
        return _fake_report()

    monkeypatch.setattr("app.orchestration.dashboard_service.analyze_symbol", _fake_analyze_symbol)

    repositories = Repositories(
        quotes=JsonlQuoteRepository(tmp_path / "quotes.jsonl"), option_chains=JsonlOptionChainRepository(tmp_path / "chains.jsonl"),
        iv_observations=JsonlIvObservationRepository(tmp_path / "iv.jsonl"),
    )
    closed_sunday = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)
    response = asyncio.run(run_analysis(
        "RELIANCE 1270 PE", provider=None, instrument_master=[], strategy=None,  # type: ignore[arg-type]
        repositories=repositories, config=PipelineConfig(), as_of=closed_sunday,
    ))
    assert response.session is not None
    assert response.session.session_window == "CLOSED"
    assert response.session.observation_kind == "LAST_OBSERVED"
    assert response.tomorrow_watch is not None
    assert response.tomorrow_watch.at_open_check
    assert response.tomorrow_watch.one_line_summary
    assert "will fall" not in response.tomorrow_watch.one_line_summary.lower()
    assert any("Refresh underlying" in line for line in response.tomorrow_watch.at_open_check)
    assert any("Refresh option-chain" in line for line in response.tomorrow_watch.at_open_check)
    dumped = response.tomorrow_watch.model_dump()
    assert "score" not in dumped
    assert "probability" not in dumped
