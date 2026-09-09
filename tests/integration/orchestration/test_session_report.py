from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from app.domain.audit.models import (
    AdversarialSection,
    AnalysisSnapshot,
    CheckpointLabel,
    CheckpointStatus,
    ContractObservationSection,
    DecisionSection,
    EvidenceSection,
    FuturesSection,
    GlobalSection,
    IdentitySection,
    LevelsSection,
    NewsSection,
    OptionOutcome,
    OptionsSection,
    OutcomeCheckpoint,
    QualitySection,
    TechnicalSection,
    TemporalSection,
    UnderlyingOutcome,
    UnderlyingSection,
)
from app.domain.market.models import OptionRight
from app.orchestration.session_report import build_session_report, render_session_report
from app.persistence.jsonl_file import JsonlAuditJournalRepository

GENERATED_AT = datetime(2026, 8, 31, 3, 45, 0, tzinfo=UTC)  # 09:15 IST -- real market open instant


def _quality() -> QualitySection:
    return QualitySection(
        data_quality="STRONG", evidence_quality="MODERATE", decision_quality="MODERATE", setup_quality="STRONG",
        option_quality="STRONG", liquidity_quality="STRONG", risk_quality="STRONG", supporting_evidence_count=2,
        conflicting_evidence_count=0,
    )


def _snapshot(*, audit_id: str, symbol: str = "RELIANCE", generated_at: datetime = GENERATED_AT, reference_strike: Decimal | None = None) -> AnalysisSnapshot:
    return AnalysisSnapshot(
        identity=IdentitySection(audit_id=audit_id, symbol=symbol, instrument_key="NSE_EQ|X", generated_at=generated_at, market_state="LIVE_SNAPSHOT", analysis_version="1.0.0"),
        underlying=UnderlyingSection(spot=Decimal("1300"), day_change=None, day_change_pct=None, data_age_seconds=1.0, freshness_label="LIVE"),
        technical=TechnicalSection(trend="TRENDING_BULLISH", ema_alignment="ASCENDING", vwap_position="ABOVE", rsi_value=Decimal("55"), atr_pct_of_price=Decimal("1.0"), regime_detail="trending"),
        futures=FuturesSection(futures_instrument_key=None, futures_ltp=None, futures_oi=None, futures_basis_pct=None, futures_interpretation=None),
        options=OptionsSection(expiry=None, atm_strike=None, total_call_oi=None, total_put_oi=None, pcr_oi=None, atm_ce_iv=None, atm_pe_iv=None, chain_iv=None, ce_pe_skew=None, iv_trend=None, oi_structure_detail=None, chain_reference=None),
        temporal=TemporalSection(), global_context=GlobalSection(inputs=[], verdict=None, detail=None), news=NewsSection(),
        evidence=EvidenceSection(rows=[], convergence="CONVERGENCE_BULLISH"),
        adversarial=AdversarialSection(bull_case=[], bear_case=[], contradictions=[], missing_data=[], key_risks=[], opposite_case_is_equally_supported=False),
        quality=_quality(), candidates=[], levels=LevelsSection(levels=[]),
        decision=DecisionSection(final_bias="BULLISH", decision="WATCH", reasoning="test", candidate_selected=None, invalidation_level=None, invalidation_description=None),
        contracts=ContractObservationSection(reference_strike=reference_strike, requested_right=OptionRight.CE if reference_strike else None),
    )


def _option_outcome() -> OptionOutcome:
    return OptionOutcome(
        option_ltp=Decimal("32"), option_pct_change=Decimal("6.6"), implied_volatility=Decimal("18"), iv_change=Decimal("0"),
        spread_pct=Decimal("3"), liquidity_grade="excellent", intrinsic_value=Decimal("0"), intrinsic_value_change=Decimal("0"),
        time_remaining_days=29, decay_attribution=None,
    )


def _checkpoint(*, audit_id: str, label: CheckpointLabel, status: CheckpointStatus, offset_seconds: float | None, captured_at: datetime, detail: str = "", with_ce_pe: bool = False) -> OutcomeCheckpoint:
    underlying = UnderlyingOutcome(spot=Decimal("1310"), absolute_change=Decimal("10"), pct_change=Decimal("0.77")) if status == CheckpointStatus.RECORDED else None
    return OutcomeCheckpoint(
        audit_id=audit_id, checkpoint_label=label, scheduled_offset_seconds=offset_seconds, captured_at=captured_at,
        actual_elapsed_seconds=(captured_at - GENERATED_AT).total_seconds(), status=status, underlying=underlying,
        ce_option=_option_outcome() if with_ce_pe else None, pe_option=_option_outcome() if with_ce_pe else None,
        detail=detail,
    )


def test_no_snapshots_for_the_day_yields_no_data_report(tmp_path: Path) -> None:
    journal = JsonlAuditJournalRepository(tmp_path)
    report = asyncio.run(build_session_report(journal=journal, symbols=["RELIANCE"], session_date=GENERATED_AT.date(), is_trading_day=True, now=GENERATED_AT))
    assert report.symbols == []
    rendered = render_session_report(report)
    assert "NO DATA" in rendered


def test_a_real_snapshot_with_no_checkpoints_yet_shows_all_pending(tmp_path: Path) -> None:
    journal = JsonlAuditJournalRepository(tmp_path)
    asyncio.run(journal.save_analysis(_snapshot(audit_id="audit-1")))

    report = asyncio.run(build_session_report(journal=journal, symbols=["RELIANCE"], session_date=GENERATED_AT.date(), is_trading_day=True, now=GENERATED_AT + timedelta(hours=1)))

    assert len(report.symbols) == 1
    s = report.symbols[0]
    assert s.audit_id == "audit-1"
    assert all(row.status == "PENDING" for row in s.checkpoints)
    assert [row.label for row in s.checkpoints] == ["5m", "15m", "30m", "60m", "EOD"]


def test_a_genuinely_recorded_checkpoint_shows_recorded_with_real_source(tmp_path: Path) -> None:
    journal = JsonlAuditJournalRepository(tmp_path)
    asyncio.run(journal.save_analysis(_snapshot(audit_id="audit-1")))
    asyncio.run(journal.save_checkpoint(_checkpoint(
        audit_id="audit-1", label=CheckpointLabel.FIVE_MIN, status=CheckpointStatus.RECORDED,
        offset_seconds=300.0, captured_at=GENERATED_AT + timedelta(minutes=5),
    )))

    report = asyncio.run(build_session_report(journal=journal, symbols=["RELIANCE"], session_date=GENERATED_AT.date(), is_trading_day=True, now=GENERATED_AT + timedelta(hours=1)))
    row = report.symbols[0].checkpoints[0]
    assert row.status == "RECORDED"
    assert row.data_source == "Upstox (live)"
    assert row.actual_time == GENERATED_AT + timedelta(minutes=5)

    rendered = render_session_report(report)
    # Only 1 of 5 checkpoints for this symbol was ever captured -- the
    # other 4 are genuinely still PENDING (not yet due at `now`), so the
    # session is honestly INCOMPLETE, never prematurely called FULL/PARTIAL.
    assert "INCOMPLETE" in rendered


def test_market_closed_checkpoint_never_reported_as_recorded(tmp_path: Path) -> None:
    journal = JsonlAuditJournalRepository(tmp_path)
    asyncio.run(journal.save_analysis(_snapshot(audit_id="audit-1")))
    asyncio.run(journal.save_checkpoint(_checkpoint(
        audit_id="audit-1", label=CheckpointLabel.FIVE_MIN, status=CheckpointStatus.MARKET_CLOSED,
        offset_seconds=300.0, captured_at=GENERATED_AT + timedelta(minutes=5), detail="market closed",
    )))

    report = asyncio.run(build_session_report(journal=journal, symbols=["RELIANCE"], session_date=GENERATED_AT.date(), is_trading_day=True, now=GENERATED_AT + timedelta(hours=1)))
    row = report.symbols[0].checkpoints[0]
    assert row.status == "MARKET_CLOSED"
    assert row.data_source == "n/a"
    assert row.error == "market closed"


def test_duplicate_checkpoint_for_the_same_label_is_flagged_as_a_defect(tmp_path: Path) -> None:
    journal = JsonlAuditJournalRepository(tmp_path)
    asyncio.run(journal.save_analysis(_snapshot(audit_id="audit-1")))
    asyncio.run(journal.save_checkpoint(_checkpoint(audit_id="audit-1", label=CheckpointLabel.FIVE_MIN, status=CheckpointStatus.RECORDED, offset_seconds=300.0, captured_at=GENERATED_AT + timedelta(minutes=5))))
    asyncio.run(journal.save_checkpoint(_checkpoint(audit_id="audit-1", label=CheckpointLabel.FIVE_MIN, status=CheckpointStatus.RECORDED, offset_seconds=300.0, captured_at=GENERATED_AT + timedelta(minutes=6))))

    report = asyncio.run(build_session_report(journal=journal, symbols=["RELIANCE"], session_date=GENERATED_AT.date(), is_trading_day=True, now=GENERATED_AT + timedelta(hours=1)))
    assert report.symbols[0].duplicate_checkpoint_labels == ["5m"]
    rendered = render_session_report(report)
    assert "DEFECT DETECTED" in rendered


def test_ce_pe_symmetry_holds_when_both_legs_recorded_together(tmp_path: Path) -> None:
    journal = JsonlAuditJournalRepository(tmp_path)
    asyncio.run(journal.save_analysis(_snapshot(audit_id="audit-1", reference_strike=Decimal("1300"))))
    asyncio.run(journal.save_checkpoint(_checkpoint(audit_id="audit-1", label=CheckpointLabel.FIVE_MIN, status=CheckpointStatus.RECORDED, offset_seconds=300.0, captured_at=GENERATED_AT + timedelta(minutes=5), with_ce_pe=True)))

    report = asyncio.run(build_session_report(journal=journal, symbols=["RELIANCE"], session_date=GENERATED_AT.date(), is_trading_day=True, now=GENERATED_AT + timedelta(hours=1)))
    assert report.symbols[0].ce_pe_symmetry_holds is True


def test_bare_underlying_query_has_no_symmetry_claim(tmp_path: Path) -> None:
    journal = JsonlAuditJournalRepository(tmp_path)
    asyncio.run(journal.save_analysis(_snapshot(audit_id="audit-1", reference_strike=None)))

    report = asyncio.run(build_session_report(journal=journal, symbols=["RELIANCE"], session_date=GENERATED_AT.date(), is_trading_day=True, now=GENERATED_AT + timedelta(hours=1)))
    assert report.symbols[0].ce_pe_symmetry_holds is None


def test_snapshot_from_a_different_calendar_day_is_excluded(tmp_path: Path) -> None:
    journal = JsonlAuditJournalRepository(tmp_path)
    asyncio.run(journal.save_analysis(_snapshot(audit_id="audit-1", generated_at=GENERATED_AT - timedelta(days=1))))

    report = asyncio.run(build_session_report(journal=journal, symbols=["RELIANCE"], session_date=GENERATED_AT.date(), is_trading_day=True, now=GENERATED_AT + timedelta(hours=1)))
    assert report.symbols == []


def test_restart_and_no_look_ahead_are_never_silently_marked_verified(tmp_path: Path) -> None:
    """This report must never claim to have proven restart safety or
    no-look-ahead from journal data alone -- both require a real
    procedure or the existing test suite, not this read-only report."""
    journal = JsonlAuditJournalRepository(tmp_path)
    asyncio.run(journal.save_analysis(_snapshot(audit_id="audit-1")))
    report = asyncio.run(build_session_report(journal=journal, symbols=["RELIANCE"], session_date=GENERATED_AT.date(), is_trading_day=True, now=GENERATED_AT + timedelta(hours=1)))
    rendered = render_session_report(report)
    assert "NOT DERIVABLE FROM JOURNAL DATA ALONE" in rendered
    assert "see tests/safety/test_no_lookahead.py" in rendered


# -- Phase 6/7: FULL / PARTIAL / FAILED classification -----------------


def _save_all_five(journal: JsonlAuditJournalRepository, *, audit_id: str, statuses: dict[CheckpointLabel, CheckpointStatus]) -> None:
    offsets = {CheckpointLabel.FIVE_MIN: 300.0, CheckpointLabel.FIFTEEN_MIN: 900.0, CheckpointLabel.THIRTY_MIN: 1800.0, CheckpointLabel.SIXTY_MIN: 3600.0, CheckpointLabel.EOD: None}
    for label, status in statuses.items():
        offset = offsets[label]
        captured_at = GENERATED_AT + timedelta(seconds=offset) if offset is not None else GENERATED_AT + timedelta(hours=6)
        asyncio.run(journal.save_checkpoint(_checkpoint(audit_id=audit_id, label=label, status=status, offset_seconds=offset, captured_at=captured_at, detail="" if status == CheckpointStatus.RECORDED else "resolved")))


_ALL_LABELS = [CheckpointLabel.FIVE_MIN, CheckpointLabel.FIFTEEN_MIN, CheckpointLabel.THIRTY_MIN, CheckpointLabel.SIXTY_MIN, CheckpointLabel.EOD]


def test_full_classification_when_every_checkpoint_for_every_symbol_is_recorded(tmp_path: Path) -> None:
    journal = JsonlAuditJournalRepository(tmp_path)
    asyncio.run(journal.save_analysis(_snapshot(audit_id="audit-1")))
    _save_all_five(journal, audit_id="audit-1", statuses={label: CheckpointStatus.RECORDED for label in _ALL_LABELS})

    report = asyncio.run(build_session_report(journal=journal, symbols=["RELIANCE"], session_date=GENERATED_AT.date(), is_trading_day=True, now=GENERATED_AT + timedelta(hours=8)))
    rendered = render_session_report(report)
    assert "Overall session status: FULL" in rendered


def test_partial_classification_when_some_but_not_all_checkpoints_are_recorded(tmp_path: Path) -> None:
    journal = JsonlAuditJournalRepository(tmp_path)
    asyncio.run(journal.save_analysis(_snapshot(audit_id="audit-1")))
    statuses = {label: CheckpointStatus.RECORDED for label in _ALL_LABELS}
    statuses[CheckpointLabel.SIXTY_MIN] = CheckpointStatus.MARKET_CLOSED
    statuses[CheckpointLabel.EOD] = CheckpointStatus.INSUFFICIENT_DATA
    _save_all_five(journal, audit_id="audit-1", statuses=statuses)

    report = asyncio.run(build_session_report(journal=journal, symbols=["RELIANCE"], session_date=GENERATED_AT.date(), is_trading_day=True, now=GENERATED_AT + timedelta(hours=8)))
    rendered = render_session_report(report)
    assert "Overall session status: PARTIAL" in rendered
    assert "3/5" in rendered


def test_failed_classification_when_zero_checkpoints_recorded_but_not_all_market_closed(tmp_path: Path) -> None:
    """Distinguishes a genuine operational failure (provider errors during
    what should have been live hours) from an honest closed-market
    session -- these must never be reported identically."""
    journal = JsonlAuditJournalRepository(tmp_path)
    asyncio.run(journal.save_analysis(_snapshot(audit_id="audit-1")))
    statuses = {label: CheckpointStatus.INSUFFICIENT_DATA for label in _ALL_LABELS}
    statuses[CheckpointLabel.FIVE_MIN] = CheckpointStatus.MARKET_CLOSED
    _save_all_five(journal, audit_id="audit-1", statuses=statuses)

    report = asyncio.run(build_session_report(journal=journal, symbols=["RELIANCE"], session_date=GENERATED_AT.date(), is_trading_day=True, now=GENERATED_AT + timedelta(hours=8)))
    rendered = render_session_report(report)
    assert "Overall session status: FAILED" in rendered


def test_market_closed_classification_when_every_checkpoint_is_market_closed(tmp_path: Path) -> None:
    journal = JsonlAuditJournalRepository(tmp_path)
    asyncio.run(journal.save_analysis(_snapshot(audit_id="audit-1")))
    _save_all_five(journal, audit_id="audit-1", statuses={label: CheckpointStatus.MARKET_CLOSED for label in _ALL_LABELS})

    report = asyncio.run(build_session_report(journal=journal, symbols=["RELIANCE"], session_date=GENERATED_AT.date(), is_trading_day=False, now=GENERATED_AT + timedelta(hours=8)))
    rendered = render_session_report(report)
    assert "Overall session status: MARKET_CLOSED" in rendered


def test_still_incomplete_session_is_never_prematurely_classified_full_or_partial(tmp_path: Path) -> None:
    journal = JsonlAuditJournalRepository(tmp_path)
    asyncio.run(journal.save_analysis(_snapshot(audit_id="audit-1")))
    asyncio.run(journal.save_checkpoint(_checkpoint(audit_id="audit-1", label=CheckpointLabel.FIVE_MIN, status=CheckpointStatus.RECORDED, offset_seconds=300.0, captured_at=GENERATED_AT + timedelta(minutes=5))))

    report = asyncio.run(build_session_report(journal=journal, symbols=["RELIANCE"], session_date=GENERATED_AT.date(), is_trading_day=True, now=GENERATED_AT + timedelta(minutes=10)))
    rendered = render_session_report(report)
    assert "Overall session status: INCOMPLETE" in rendered
    assert "FULL" not in rendered.split("Overall session status:")[1]
    assert "PARTIAL" not in rendered.split("Overall session status:")[1]
