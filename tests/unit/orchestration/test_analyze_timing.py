from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from app.domain.options.development import DevelopmentPattern
from app.domain.options.early_opportunity import TimingStage
from app.domain.options.freshness_label import DataStream
from app.orchestration.dashboard_service import market_observed_at_from_report, timing_from_report


def test_timing_from_report_uses_classify_research_bucket_for_early_setup() -> None:
    report = SimpleNamespace(
        research_state=SimpleNamespace(value="EARLY_SETUP"),
        development=SimpleNamespace(pattern=DevelopmentPattern.FAILED_BREAKDOWN_RECLAIM),
        candidates=[],
        day_change_pct=None,
        stream_freshness=[],
    )
    timing, reason = timing_from_report(report)
    assert timing == TimingStage.EARLY.value
    assert reason is None


def test_timing_from_report_is_unknown_with_reason_when_evidence_is_insufficient() -> None:
    report = SimpleNamespace(
        research_state=SimpleNamespace(value="CONFLICT"),
        development=SimpleNamespace(pattern=DevelopmentPattern.NONE),
        candidates=[],
        day_change_pct=None,
        stream_freshness=[],
    )
    timing, reason = timing_from_report(report)
    assert timing == TimingStage.UNKNOWN.value
    assert reason == "Insufficient evidence to classify timing."


def test_market_observed_at_reads_underlying_quote_stream_not_generated_at() -> None:
    quote_ts = datetime(2026, 9, 18, 10, 30, tzinfo=UTC)
    generated = datetime(2026, 9, 20, 7, 17, tzinfo=UTC)
    report = SimpleNamespace(
        generated_at=generated,
        stream_freshness=[
            SimpleNamespace(stream=DataStream.UNDERLYING_QUOTE, data_timestamp=quote_ts),
        ],
    )
    assert market_observed_at_from_report(report) == quote_ts
    assert market_observed_at_from_report(report) != generated
