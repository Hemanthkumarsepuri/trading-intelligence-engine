"""IPO audit journal -- immutability, persistence round-trip, no
look-ahead (a listing outcome must never rewrite the original snapshot)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.domain.ipo.hidden_opportunity import HiddenOpportunityAssessment, HiddenOpportunityLabel
from app.domain.ipo.listing_analysis import reconcile_listing_outcome
from app.domain.ipo.models import IPOIdentity, ListingRangeEstimate, UserEligibility
from app.orchestration.ipo_audit_journal import (
    build_ipo_analysis_snapshot,
    build_listing_outcome_record,
    listing_range_from_snapshot,
)
from app.orchestration.ipo_intelligence import IPOAnalysisResult
from app.persistence.jsonl_file import JsonlIPOAuditJournalRepository

_NOW = datetime(2026, 8, 30, 10, 0, tzinfo=UTC)


def _result(identity: IPOIdentity | None, *, listing_range: ListingRangeEstimate | None = None) -> IPOAnalysisResult:
    from app.domain.ipo.query_parser import IPOIntentKind, IPOQueryIntent

    return IPOAnalysisResult(
        query="Tempsens IPO", intent=IPOQueryIntent(raw_text="Tempsens IPO", company_hint="TEMPSENS", intent=IPOIntentKind.COMPANY_LOOKUP),
        identity=identity, gmp_summary=None, listing_range=listing_range, subscription=None, allotment_estimate=None,
        shareholder_quota=None, user_eligibility=UserEligibility.UNKNOWN, data_available=identity is not None,
        detail="d", analyzed_at=_NOW,
    )


def _tempsens_identity(listing_price: Decimal | None = None) -> IPOIdentity:
    return IPOIdentity(company_name="Tempsens Instruments (India) IPO", ipo_id="tempsens-instruments-india-limited-ipo", price_band_high=Decimal("300"), listing_price=listing_price, source="upstox")


def test_build_snapshot_from_a_found_identity() -> None:
    snapshot = build_ipo_analysis_snapshot(_result(_tempsens_identity()))
    assert snapshot.data_available is True
    assert snapshot.identity is not None
    assert snapshot.identity.company_name == "Tempsens Instruments (India) IPO"


def test_build_snapshot_when_nothing_was_found_is_still_a_valid_honest_snapshot() -> None:
    """Unlike the options side, "nothing found" is NOT an error here --
    it is real, honest information about what this system knew."""
    snapshot = build_ipo_analysis_snapshot(_result(None))
    assert snapshot.data_available is False
    assert snapshot.identity is None


def test_snapshot_carries_hidden_opportunity_assessment_when_supplied() -> None:
    assessment = HiddenOpportunityAssessment(label=HiddenOpportunityLabel.ATTENTION_WORTHY, reasons=["r1"], evidence_considered=["GMP"], missing_evidence=["subscription"])
    snapshot = build_ipo_analysis_snapshot(_result(_tempsens_identity()), hidden_opportunity=assessment)
    assert snapshot.hidden_opportunity is not None
    assert snapshot.hidden_opportunity.label == "ATTENTION_WORTHY"
    assert snapshot.hidden_opportunity.reasons == ["r1"]


def test_snapshot_is_immutable() -> None:
    snapshot = build_ipo_analysis_snapshot(_result(_tempsens_identity()))
    with pytest.raises(ValidationError):
        snapshot.query = "mutated"  # type: ignore[misc]


def test_persistence_round_trip(tmp_path: Path) -> None:
    repo = JsonlIPOAuditJournalRepository(tmp_path)
    snapshot = build_ipo_analysis_snapshot(_result(_tempsens_identity()))
    asyncio.run(repo.save_snapshot(snapshot))

    reloaded = asyncio.run(repo.get_snapshot(snapshot.audit_id))
    assert reloaded is not None
    assert reloaded == snapshot  # byte-for-byte-equivalent round trip


def test_persistence_survives_a_simulated_restart(tmp_path: Path) -> None:
    repo1 = JsonlIPOAuditJournalRepository(tmp_path)
    snapshot = build_ipo_analysis_snapshot(_result(_tempsens_identity()))
    asyncio.run(repo1.save_snapshot(snapshot))

    # Simulate a restart: a brand-new repository instance over the SAME directory.
    repo2 = JsonlIPOAuditJournalRepository(tmp_path)
    reloaded = asyncio.run(repo2.get_snapshot(snapshot.audit_id))
    assert reloaded is not None
    assert reloaded.identity is not None and reloaded.identity.company_name == "Tempsens Instruments (India) IPO"


def test_listing_outcome_is_a_separate_record_never_rewriting_the_snapshot(tmp_path: Path) -> None:
    """The core no-look-ahead invariant: recording a real listing outcome
    must NEVER change the original snapshot's persisted bytes."""
    repo = JsonlIPOAuditJournalRepository(tmp_path)
    original_snapshot = build_ipo_analysis_snapshot(_result(_tempsens_identity(listing_price=None)))
    asyncio.run(repo.save_snapshot(original_snapshot))

    # Later, the IPO lists -- a fresh identity WITH a real listing price is
    # fetched separately and reconciled, but the ORIGINAL snapshot is
    # never touched.
    listed_identity = _tempsens_identity(listing_price=Decimal("634"))
    reconciliation = reconcile_listing_outcome(listed_identity, gmp_implied_range=None)
    assert reconciliation is not None
    outcome_record = build_listing_outcome_record(original_snapshot, reconciliation=reconciliation, recorded_at=_NOW)
    asyncio.run(repo.save_listing_outcome(outcome_record))

    reloaded_snapshot = asyncio.run(repo.get_snapshot(original_snapshot.audit_id))
    assert reloaded_snapshot == original_snapshot  # completely unchanged
    assert reloaded_snapshot.identity is not None and reloaded_snapshot.identity.listing_price is None  # still None -- never backfilled

    reloaded_outcome = asyncio.run(repo.get_listing_outcome(original_snapshot.audit_id))
    assert reloaded_outcome is not None
    assert reloaded_outcome.listing_price == Decimal("634")


def test_query_without_listing_outcome_excludes_reconciled_snapshots(tmp_path: Path) -> None:
    repo = JsonlIPOAuditJournalRepository(tmp_path)
    snap1 = build_ipo_analysis_snapshot(_result(_tempsens_identity()))
    snap2 = build_ipo_analysis_snapshot(_result(IPOIdentity(company_name="Other IPO", ipo_id="other-ipo")))
    asyncio.run(repo.save_snapshot(snap1))
    asyncio.run(repo.save_snapshot(snap2))

    reconciliation = reconcile_listing_outcome(_tempsens_identity(listing_price=Decimal("634")), gmp_implied_range=None)
    assert reconciliation is not None
    asyncio.run(repo.save_listing_outcome(build_listing_outcome_record(snap1, reconciliation=reconciliation, recorded_at=_NOW)))

    unresolved = asyncio.run(repo.query_without_listing_outcome())
    assert [s.audit_id for s in unresolved] == [snap2.audit_id]


def test_query_by_ipo_id_finds_all_snapshots_for_the_same_real_ipo(tmp_path: Path) -> None:
    repo = JsonlIPOAuditJournalRepository(tmp_path)
    snap1 = build_ipo_analysis_snapshot(_result(_tempsens_identity()))
    snap2 = build_ipo_analysis_snapshot(_result(_tempsens_identity()))  # a second, later analysis of the same real IPO
    asyncio.run(repo.save_snapshot(snap1))
    asyncio.run(repo.save_snapshot(snap2))

    matches = asyncio.run(repo.query_by_ipo_id("tempsens-instruments-india-limited-ipo"))
    assert len(matches) == 2
    assert snap1.audit_id != snap2.audit_id  # two genuinely distinct audit_ids, neither overwrites the other


def test_listing_range_from_snapshot_round_trips_the_two_fields_reconciliation_actually_uses() -> None:
    result = _result(
        _tempsens_identity(),
        listing_range=ListingRangeEstimate(
            issue_price=Decimal("300"), gmp_low=Decimal("50"), gmp_high=Decimal("80"), gmp_median=Decimal("65"),
            indicative_low=Decimal("350"), indicative_mid=Decimal("365"), indicative_high=Decimal("380"),
            indicative_premium_pct_low=None, indicative_premium_pct_high=None,
        ),
    )
    snapshot = build_ipo_analysis_snapshot(result)
    reconstructed = listing_range_from_snapshot(snapshot)
    assert reconstructed is not None
    assert reconstructed.indicative_low == Decimal("350")
    assert reconstructed.indicative_high == Decimal("380")
