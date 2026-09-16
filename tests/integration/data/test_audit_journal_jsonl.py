"""`JsonlAuditJournalRepository` — append-only storage/query behavior.
Immutability is enforced structurally (see `AuditJournalRepository`'s own
docstring): there is no update/delete method on this class at all, so
these tests only ever exercise `save_*`/`get_*`/`query_*`.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.audit.models import (
    AdversarialSection,
    AnalysisSnapshot,
    CheckpointLabel,
    CheckpointStatus,
    DecisionSection,
    EvidenceRowSnapshot,
    EvidenceSection,
    FuturesSection,
    GlobalSection,
    IdentitySection,
    InvalidationTracking,
    LevelsSection,
    NewsSection,
    OptionsSection,
    OutcomeCheckpoint,
    QualitySection,
    ReconciliationResult,
    TechnicalSection,
    TemporalSection,
    ThesisStatus,
    UnderlyingOutcome,
    UnderlyingSection,
)
from app.persistence.jsonl_file import JsonlAuditJournalRepository

GENERATED_AT = datetime(2026, 8, 24, 10, 0, tzinfo=UTC)


def _quality() -> QualitySection:
    return QualitySection(
        data_quality="STRONG", evidence_quality="MODERATE", decision_quality="MODERATE", setup_quality="STRONG",
        option_quality="STRONG", liquidity_quality="STRONG", risk_quality="STRONG", supporting_evidence_count=2,
        conflicting_evidence_count=0,
    )


def _snapshot(*, audit_id: str, symbol: str = "RELIANCE", bias: str = "BULLISH", decision: str = "WATCH", generated_at: datetime = GENERATED_AT) -> AnalysisSnapshot:
    return AnalysisSnapshot(
        identity=IdentitySection(audit_id=audit_id, symbol=symbol, instrument_key="NSE_EQ|X", generated_at=generated_at, market_state="LIVE_SNAPSHOT", analysis_version="1.0.0"),
        underlying=UnderlyingSection(spot=Decimal("1300"), day_change=None, day_change_pct=None, data_age_seconds=1.0, freshness_label="LIVE"),
        technical=TechnicalSection(trend="TRENDING_BULLISH", ema_alignment="ASCENDING", vwap_position="ABOVE", rsi_value=Decimal("55"), atr_pct_of_price=Decimal("1.0"), regime_detail="trending"),
        futures=FuturesSection(futures_instrument_key=None, futures_ltp=None, futures_oi=None, futures_basis_pct=None, futures_interpretation=None),
        options=OptionsSection(expiry=None, atm_strike=Decimal("1300"), total_call_oi=100, total_put_oi=80, pcr_oi=Decimal("0.8"), atm_ce_iv=Decimal("18"), atm_pe_iv=Decimal("17"), chain_iv=Decimal("17.5"), ce_pe_skew=Decimal("1.0"), iv_trend="STABLE", oi_structure_detail=None, chain_reference=None),
        temporal=TemporalSection(), levels=LevelsSection(levels=[]), global_context=GlobalSection(inputs=[], verdict=None, detail=None), news=NewsSection(),
        evidence=EvidenceSection(rows=[EvidenceRowSnapshot(name="M15 trend", group="underlying_price_structure", direction="BULLISH", detail="x")], convergence="CONVERGENCE_BULLISH"),
        adversarial=AdversarialSection(bull_case=["a"], bear_case=[], contradictions=[], missing_data=[], key_risks=[], opposite_case_is_equally_supported=False),
        quality=_quality(), candidates=[],
        decision=DecisionSection(final_bias=bias, decision=decision, reasoning="test", candidate_selected=None, invalidation_level=Decimal("1290"), invalidation_description="x"),
    )


def _checkpoint(*, audit_id: str, label: CheckpointLabel = CheckpointLabel.FIVE_MIN) -> OutcomeCheckpoint:
    return OutcomeCheckpoint(
        audit_id=audit_id, checkpoint_label=label, scheduled_offset_seconds=300.0, captured_at=GENERATED_AT + timedelta(minutes=5),
        actual_elapsed_seconds=300.0, status=CheckpointStatus.RECORDED, underlying=UnderlyingOutcome(spot=Decimal("1305"), absolute_change=Decimal("5"), pct_change=Decimal("0.38")),
    )


def _reconciliation(*, audit_id: str, computed_at: datetime, is_final: bool, status: ThesisStatus = ThesisStatus.CONFIRMED) -> ReconciliationResult:
    return ReconciliationResult(
        audit_id=audit_id, computed_at=computed_at, checkpoints_used=["5m"], is_final=is_final,
        underlying_thesis_status=status, option_thesis_status=None, option_vs_underlying_divergence_note=None,
        invalidation=InvalidationTracking(invalidation_level=None, reached=None, reached_at_checkpoint=None, reached_at=None),
        adversarial_reconciliation=None, detail="x",
    )


def test_save_and_get_analysis_roundtrips(tmp_path: Path) -> None:
    repo = JsonlAuditJournalRepository(tmp_path)
    snapshot = _snapshot(audit_id="a1")
    asyncio.run(repo.save_analysis(snapshot))
    fetched = asyncio.run(repo.get_analysis("a1"))
    assert fetched is not None
    assert fetched == snapshot


def test_get_analysis_returns_none_when_missing(tmp_path: Path) -> None:
    repo = JsonlAuditJournalRepository(tmp_path)
    assert asyncio.run(repo.get_analysis("nope")) is None


def test_get_analysis_returns_the_first_saved_record(tmp_path: Path) -> None:
    """Structural immutability check: even if two records somehow share an
    `audit_id` (never possible via legitimate use of `new_audit_id()`, but
    defensively verified here), the FIRST-saved (the true original) is what
    comes back -- never a later one silently overwriting it, because there
    is no update method for a later save to even use to overwrite it.
    """
    repo = JsonlAuditJournalRepository(tmp_path)
    original = _snapshot(audit_id="dup", bias="BULLISH")
    later_attempt = _snapshot(audit_id="dup", bias="BEARISH")
    asyncio.run(repo.save_analysis(original))
    asyncio.run(repo.save_analysis(later_attempt))
    fetched = asyncio.run(repo.get_analysis("dup"))
    assert fetched is not None
    assert fetched.decision.final_bias == "BULLISH"


def test_no_update_or_delete_method_exists() -> None:
    public_methods = {name for name in dir(JsonlAuditJournalRepository) if not name.startswith("_")}
    assert not any(m in public_methods for m in ("update", "update_analysis", "delete", "delete_analysis"))


def test_save_and_get_outcomes_ordered_by_checkpoint_label(tmp_path: Path) -> None:
    repo = JsonlAuditJournalRepository(tmp_path)
    asyncio.run(repo.save_checkpoint(_checkpoint(audit_id="a1", label=CheckpointLabel.SIXTY_MIN)))
    asyncio.run(repo.save_checkpoint(_checkpoint(audit_id="a1", label=CheckpointLabel.FIVE_MIN)))
    asyncio.run(repo.save_checkpoint(_checkpoint(audit_id="other", label=CheckpointLabel.FIVE_MIN)))
    outcomes = asyncio.run(repo.get_outcomes("a1"))
    assert [c.checkpoint_label for c in outcomes] == [CheckpointLabel.FIVE_MIN, CheckpointLabel.SIXTY_MIN]


def test_save_and_get_reconciliation_returns_latest(tmp_path: Path) -> None:
    repo = JsonlAuditJournalRepository(tmp_path)
    asyncio.run(repo.save_reconciliation(_reconciliation(audit_id="a1", computed_at=GENERATED_AT, is_final=False)))
    asyncio.run(repo.save_reconciliation(_reconciliation(audit_id="a1", computed_at=GENERATED_AT + timedelta(hours=1), is_final=True)))
    latest = asyncio.run(repo.get_reconciliation("a1"))
    assert latest is not None
    assert latest.is_final is True


def test_query_by_symbol(tmp_path: Path) -> None:
    repo = JsonlAuditJournalRepository(tmp_path)
    asyncio.run(repo.save_analysis(_snapshot(audit_id="a1", symbol="RELIANCE")))
    asyncio.run(repo.save_analysis(_snapshot(audit_id="a2", symbol="NIFTY")))
    results = asyncio.run(repo.query_by_symbol("RELIANCE"))
    assert [s.identity.audit_id for s in results] == ["a1"]


def test_query_by_date_range(tmp_path: Path) -> None:
    repo = JsonlAuditJournalRepository(tmp_path)
    asyncio.run(repo.save_analysis(_snapshot(audit_id="a1", generated_at=GENERATED_AT)))
    asyncio.run(repo.save_analysis(_snapshot(audit_id="a2", generated_at=GENERATED_AT + timedelta(days=5))))
    results = asyncio.run(repo.query_by_date_range(GENERATED_AT - timedelta(hours=1), GENERATED_AT + timedelta(hours=1)))
    assert [s.identity.audit_id for s in results] == ["a1"]


def test_query_by_decision(tmp_path: Path) -> None:
    repo = JsonlAuditJournalRepository(tmp_path)
    asyncio.run(repo.save_analysis(_snapshot(audit_id="a1", decision="TRADEABLE")))
    asyncio.run(repo.save_analysis(_snapshot(audit_id="a2", decision="NO_TRADE")))
    results = asyncio.run(repo.query_by_decision("TRADEABLE"))
    assert [s.identity.audit_id for s in results] == ["a1"]


def test_query_by_bias(tmp_path: Path) -> None:
    repo = JsonlAuditJournalRepository(tmp_path)
    asyncio.run(repo.save_analysis(_snapshot(audit_id="a1", bias="BULLISH")))
    asyncio.run(repo.save_analysis(_snapshot(audit_id="a2", bias="BEARISH")))
    results = asyncio.run(repo.query_by_bias("BEARISH"))
    assert [s.identity.audit_id for s in results] == ["a2"]


def test_query_unresolved_includes_analyses_with_no_reconciliation(tmp_path: Path) -> None:
    repo = JsonlAuditJournalRepository(tmp_path)
    asyncio.run(repo.save_analysis(_snapshot(audit_id="a1")))
    results = asyncio.run(repo.query_unresolved())
    assert [s.identity.audit_id for s in results] == ["a1"]


def test_query_unresolved_excludes_final_reconciliations(tmp_path: Path) -> None:
    repo = JsonlAuditJournalRepository(tmp_path)
    asyncio.run(repo.save_analysis(_snapshot(audit_id="a1")))
    asyncio.run(repo.save_analysis(_snapshot(audit_id="a2")))
    asyncio.run(repo.save_reconciliation(_reconciliation(audit_id="a1", computed_at=GENERATED_AT, is_final=True)))
    asyncio.run(repo.save_reconciliation(_reconciliation(audit_id="a2", computed_at=GENERATED_AT, is_final=False)))
    results = asyncio.run(repo.query_unresolved())
    assert [s.identity.audit_id for s in results] == ["a2"]


def test_query_unresolved_uses_the_latest_reconciliation_not_an_earlier_one(tmp_path: Path) -> None:
    repo = JsonlAuditJournalRepository(tmp_path)
    asyncio.run(repo.save_analysis(_snapshot(audit_id="a1")))
    asyncio.run(repo.save_reconciliation(_reconciliation(audit_id="a1", computed_at=GENERATED_AT, is_final=False)))
    asyncio.run(repo.save_reconciliation(_reconciliation(audit_id="a1", computed_at=GENERATED_AT + timedelta(hours=1), is_final=True)))
    results = asyncio.run(repo.query_unresolved())
    assert results == []


# ---------------------------------------------------------------------------
# Release gate (Section 7) -- the pre-filtered lookups must return exactly
# what a naive full parse returns.
# ---------------------------------------------------------------------------

_REAL_JOURNAL_DIR = Path("data/persistence/audit_journal")


@pytest.mark.skipif(
    not (_REAL_JOURNAL_DIR / "analysis_snapshots.jsonl").exists(), reason="real audit journal not present locally",
)
def test_prefiltered_symbol_and_audit_id_lookups_match_a_full_parse_on_the_real_journal() -> None:
    repo = JsonlAuditJournalRepository(_REAL_JOURNAL_DIR)
    everything = repo._all_analyses()
    symbols = sorted({s.identity.symbol for s in everything})
    for symbol in [*symbols[:: max(1, len(symbols) // 8)], "NOT_A_SYMBOL"]:
        expected = [s.identity.audit_id for s in everything if s.identity.symbol == symbol]
        assert [s.identity.audit_id for s in asyncio.run(repo.query_by_symbol(symbol))] == expected
    for snapshot in everything[:: max(1, len(everything) // 25)]:
        found = asyncio.run(repo.get_analysis(snapshot.identity.audit_id))
        assert found is not None and found.identity.audit_id == snapshot.identity.audit_id
    assert asyncio.run(repo.get_analysis("0" * 32)) is None
