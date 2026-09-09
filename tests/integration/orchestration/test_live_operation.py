"""Sprint 5 — tests for the thin live-operation orchestration layer
(`app.orchestration.live_operation`): analyze -> persist snapshot -> due
checkpoints -> capture -> reconcile, plus idempotency/restart-safety and
CE/PE reference-contract tracking (Phase 8).

Reuses the exact mock-provider fixtures from `test_audit_journal.py`
(same router, same instrument master, same fake RELIANCE chain) rather
than duplicating ~140 lines of Upstox response mocking.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.data.providers.upstox_provider import UpstoxProvider
from app.domain.audit.models import CheckpointLabel, CheckpointStatus
from app.domain.market.models import OptionRight
from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy
from app.orchestration.live_operation import (
    LiveAnalysisStatus,
    LiveOperationContext,
    process_due_checkpoints,
    run_controlled_observation_set,
    run_live_analysis,
    sweep_due_checkpoints,
)
from app.orchestration.options_intelligence_pipeline import PipelineConfig, analyze_symbol
from app.persistence.jsonl_file import JsonlAuditJournalRepository
from tests.integration.orchestration.test_audit_journal import (
    _FAST_CONFIG,
    _MASTER,
    GENERATED_AT,
    _provider,
    _repos,
    _router,
)


def _ctx(
    tmp_path: Path, *, journal_dir: Path | None = None, config: PipelineConfig = _FAST_CONFIG, provider: UpstoxProvider | None = None
) -> LiveOperationContext:
    return LiveOperationContext(
        provider=provider or _provider(_router()), instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
        repositories=_repos(tmp_path), journal=JsonlAuditJournalRepository(journal_dir or (tmp_path / "journal")), config=config,
    )


# -- run_live_analysis -------------------------------------------------------


def test_run_live_analysis_persists_a_snapshot(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    outcome = asyncio.run(run_live_analysis("RELIANCE", ctx=ctx, now=GENERATED_AT))
    assert outcome.status == LiveAnalysisStatus.PERSISTED
    assert outcome.symbol == "RELIANCE"
    assert outcome.audit_id is not None
    stored = asyncio.run(ctx.journal.get_analysis(outcome.audit_id))
    assert stored is not None
    assert stored.identity.audit_id == outcome.audit_id


def test_run_live_analysis_invalid_query_never_calls_the_provider(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    outcome = asyncio.run(run_live_analysis("   ", ctx=ctx, now=GENERATED_AT))  # empty query -> no symbol recognized
    assert outcome.status == LiveAnalysisStatus.INVALID_QUERY
    assert outcome.audit_id is None
    assert outcome.report is None


def test_run_live_analysis_unresolvable_strike_right_pair_is_invalid_query(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    outcome = asyncio.run(run_live_analysis("RELIANCE 1300", ctx=ctx, now=GENERATED_AT))  # strike without CE/PE
    assert outcome.status == LiveAnalysisStatus.INVALID_QUERY
    assert outcome.audit_id is None


def test_run_live_analysis_skips_persistence_when_no_decision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A report with `error=None` but `decision=None` (an honest empty
    case `build_analysis_snapshot()` itself rejects) must be surfaced as
    SKIPPED_NO_DECISION, never persisted -- exercised directly since
    triggering this real path via the live pipeline requires a narrow
    market-data gap that is not this test's concern."""
    from app.domain.market.data_state import MarketDataState
    from app.orchestration.options_intelligence_report import OptionsIntelligenceReport

    async def _fake_analyze(*args: object, **kwargs: object) -> OptionsIntelligenceReport:
        return OptionsIntelligenceReport(
            symbol="RELIANCE", underlying_instrument_key=None, generated_at=GENERATED_AT,
            data_state=MarketDataState.INSUFFICIENT_HISTORY, data_age_seconds=None,
        )

    monkeypatch.setattr("app.orchestration.live_operation.analyze_symbol", _fake_analyze)
    ctx = _ctx(tmp_path)
    outcome = asyncio.run(run_live_analysis("RELIANCE", ctx=ctx, now=GENERATED_AT))
    assert outcome.status == LiveAnalysisStatus.SKIPPED_NO_DECISION
    assert outcome.audit_id is None
    unresolved = asyncio.run(ctx.journal.query_unresolved())
    assert unresolved == []


# -- Phase 8: CE/PE reference-contract tracking -------------------------------


def test_ce_query_still_persists_both_ce_and_pe_reference_contracts(tmp_path: Path) -> None:
    """Requesting the CE side must never suppress the PE side's data --
    the direct enforcement point for Phase 7/8's symmetry requirement at
    the persistence layer."""
    ctx = _ctx(tmp_path)
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=ctx.provider, instrument_master=_MASTER, strategy=ctx.strategy,
            repositories=ctx.repositories, as_of=GENERATED_AT, config=_FAST_CONFIG,
            requested_strike=Decimal("1300"), requested_right=OptionRight.CE,
        )
    )
    assert report.error is None
    from app.orchestration.audit_journal import build_analysis_snapshot

    snapshot = build_analysis_snapshot(report)
    assert snapshot.contracts.requested_right == OptionRight.CE
    assert snapshot.contracts.reference_strike == Decimal("1300")
    assert snapshot.contracts.ce_requested is not None
    assert snapshot.contracts.ce_requested.right == OptionRight.CE
    # The PE side at the SAME strike is present too, even though the user
    # asked for the CE -- never silently dropped because of the requested side.
    assert snapshot.contracts.pe_requested is not None
    assert snapshot.contracts.pe_requested.right == OptionRight.PE


def test_checkpoint_tracks_both_ce_and_pe_reference_legs_regardless_of_requested_side(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, provider=_provider(_router(checkpoint_spot=1310.0, checkpoint_ce_ltp=35.0)))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=ctx.provider, instrument_master=_MASTER, strategy=ctx.strategy,
            repositories=ctx.repositories, as_of=GENERATED_AT, config=_FAST_CONFIG,
            requested_strike=Decimal("1300"), requested_right=OptionRight.CE,
        )
    )
    from app.orchestration.audit_journal import build_analysis_snapshot, capture_outcome_checkpoint

    snapshot = build_analysis_snapshot(report)
    checkpoint = asyncio.run(
        capture_outcome_checkpoint(
            CheckpointLabel.FIVE_MIN, snapshot, now=GENERATED_AT + timedelta(minutes=5), provider=ctx.provider,
            instrument_master=_MASTER, strategy=ctx.strategy, repositories=ctx.repositories, config=_FAST_CONFIG,
        )
    )
    assert checkpoint.status == CheckpointStatus.RECORDED
    assert checkpoint.ce_option is not None
    assert checkpoint.ce_option.option_ltp == Decimal("35.0")
    # PE outcome also captured even though the requested contract was the CE.
    assert checkpoint.pe_option is not None


# -- process_due_checkpoints: idempotency / restart-safety --------------------


def test_process_due_checkpoints_is_idempotent_when_run_twice(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    outcome = asyncio.run(run_live_analysis("RELIANCE", ctx=ctx, now=GENERATED_AT))
    assert outcome.audit_id is not None

    later = GENERATED_AT + timedelta(minutes=5)
    first = asyncio.run(process_due_checkpoints(outcome.audit_id, ctx=ctx, now=later))
    assert len(first.captured) == 1
    assert first.captured[0].checkpoint_label == CheckpointLabel.FIVE_MIN

    second = asyncio.run(process_due_checkpoints(outcome.audit_id, ctx=ctx, now=later))
    assert second.captured == []  # nothing new -- already captured, not duplicated

    stored = asyncio.run(ctx.journal.get_outcomes(outcome.audit_id))
    assert len(stored) == 1  # exactly one persisted 5m checkpoint, never two


def test_process_due_checkpoints_never_captures_before_due(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    outcome = asyncio.run(run_live_analysis("RELIANCE", ctx=ctx, now=GENERATED_AT))
    assert outcome.audit_id is not None

    result = asyncio.run(process_due_checkpoints(outcome.audit_id, ctx=ctx, now=GENERATED_AT + timedelta(seconds=1)))
    assert result.captured == []
    assert result.reconciliation is None


def test_process_due_checkpoints_rejects_t0_exactly(tmp_path: Path) -> None:
    """Phase 5 -- `now == generated_at` (T+0) must never be treated as
    due; only a real elapsed instant at or past a checkpoint's own offset
    is capturable."""
    ctx = _ctx(tmp_path)
    outcome = asyncio.run(run_live_analysis("RELIANCE", ctx=ctx, now=GENERATED_AT))
    assert outcome.audit_id is not None

    result = asyncio.run(process_due_checkpoints(outcome.audit_id, ctx=ctx, now=GENERATED_AT))
    assert result.captured == []


def test_analysis_snapshot_is_immutable(tmp_path: Path) -> None:
    """Phase 5 -- a checkpoint capture must never be able to mutate the
    original snapshot (the frozen-model enforcement Milestone G already
    established); re-confirmed here at the orchestration-layer boundary
    this sprint actually exercises."""
    ctx = _ctx(tmp_path)
    outcome = asyncio.run(run_live_analysis("RELIANCE", ctx=ctx, now=GENERATED_AT))
    assert outcome.snapshot is not None
    with pytest.raises(ValidationError):
        outcome.snapshot.underlying.spot = Decimal("9999")  # type: ignore[misc]


def test_process_due_checkpoints_survives_a_simulated_restart(tmp_path: Path) -> None:
    """A fresh `LiveOperationContext` (new lock registry, new in-process
    state) pointed at the SAME persisted journal directory must resume
    exactly where the prior process left off -- no duplicate checkpoints,
    no lost history."""
    journal_dir = tmp_path / "journal"
    ctx1 = _ctx(tmp_path, journal_dir=journal_dir)
    outcome = asyncio.run(run_live_analysis("RELIANCE", ctx=ctx1, now=GENERATED_AT))
    assert outcome.audit_id is not None
    asyncio.run(process_due_checkpoints(outcome.audit_id, ctx=ctx1, now=GENERATED_AT + timedelta(minutes=5)))

    # Simulate a restart: brand-new context/provider/lock registry, same journal directory.
    ctx2 = _ctx(tmp_path, journal_dir=journal_dir)
    result = asyncio.run(process_due_checkpoints(outcome.audit_id, ctx=ctx2, now=GENERATED_AT + timedelta(minutes=15)))
    assert result.captured and result.captured[0].checkpoint_label == CheckpointLabel.FIFTEEN_MIN

    stored = asyncio.run(ctx2.journal.get_outcomes(outcome.audit_id))
    labels = {c.checkpoint_label for c in stored}
    assert labels == {CheckpointLabel.FIVE_MIN, CheckpointLabel.FIFTEEN_MIN}  # prior history preserved, not lost or duplicated


def test_process_due_checkpoints_reports_error_for_unknown_audit_id(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    result = asyncio.run(process_due_checkpoints("does-not-exist", ctx=ctx, now=GENERATED_AT))
    assert result.error is not None
    assert result.captured == []


# -- sweep_due_checkpoints ----------------------------------------------------


def test_ce_pe_comparison_still_present_for_a_bare_underlying_query(tmp_path: Path) -> None:
    """A bare underlying query ("RELIANCE") still resolves an ATM
    reference strike (Sprint 4's `direction_analysis` anchor behavior,
    unmodified) -- so it still gets a real CE/PE comparison. This is
    correct, existing behavior; it is NOT the user requesting a contract."""
    ctx = _ctx(tmp_path)
    report = asyncio.run(
        analyze_symbol("RELIANCE", provider=ctx.provider, instrument_master=_MASTER, strategy=ctx.strategy, repositories=ctx.repositories, as_of=GENERATED_AT, config=_FAST_CONFIG)
    )
    from app.domain.audit.reconciliation import build_reconciliation
    from app.orchestration.audit_journal import build_analysis_snapshot

    snapshot = build_analysis_snapshot(report)
    assert snapshot.contracts.reference_strike is not None  # ATM fallback, not a user-requested contract
    assert snapshot.contracts.requested_right is None  # the user never asked for a side
    reconciliation = build_reconciliation(snapshot, [], now=GENERATED_AT, near_level_pct_threshold=_FAST_CONFIG.near_level_pct_threshold)
    assert reconciliation.ce_pe_comparison is not None


def test_ce_pe_comparison_is_none_when_no_reference_contract_was_resolved(tmp_path: Path) -> None:
    """The genuine "nothing to compare" case: a snapshot whose `contracts`
    section is empty (e.g. no option chain was available at all this
    run) -- exercised directly at the pure `build_ce_pe_comparison()`
    boundary rather than needing a real pipeline path that produces no
    chain."""
    from app.domain.audit.reconciliation import build_ce_pe_comparison

    ctx = _ctx(tmp_path)
    report = asyncio.run(
        analyze_symbol("RELIANCE", provider=ctx.provider, instrument_master=_MASTER, strategy=ctx.strategy, repositories=ctx.repositories, as_of=GENERATED_AT, config=_FAST_CONFIG)
    )
    from app.orchestration.audit_journal import build_analysis_snapshot

    snapshot = build_analysis_snapshot(report)
    empty_contracts_snapshot = snapshot.model_copy(update={"contracts": snapshot.contracts.model_copy(update={"reference_strike": None})})
    assert build_ce_pe_comparison(empty_contracts_snapshot, []) is None


def test_ce_pe_comparison_reflects_real_divergent_price_movement(tmp_path: Path) -> None:
    """Sprint 6, Phase 8 -- CE gains value (CONFIRMED) while PE stays flat
    (MIXED) in the fixture's own real chain movement; the comparison must
    report exactly that, per side, from real persisted checkpoint data."""
    from app.domain.audit.models import ThesisStatus
    from app.domain.audit.reconciliation import build_reconciliation

    ctx = _ctx(tmp_path, provider=_provider(_router(checkpoint_spot=1310.0, checkpoint_ce_ltp=35.0)))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=ctx.provider, instrument_master=_MASTER, strategy=ctx.strategy, repositories=ctx.repositories,
            as_of=GENERATED_AT, config=_FAST_CONFIG, requested_strike=Decimal("1300"), requested_right=OptionRight.CE,
        )
    )
    from app.orchestration.audit_journal import build_analysis_snapshot, capture_outcome_checkpoint

    snapshot = build_analysis_snapshot(report)
    checkpoint = asyncio.run(
        capture_outcome_checkpoint(
            CheckpointLabel.FIVE_MIN, snapshot, now=GENERATED_AT + timedelta(minutes=5), provider=ctx.provider,
            instrument_master=_MASTER, strategy=ctx.strategy, repositories=ctx.repositories, config=_FAST_CONFIG,
        )
    )
    reconciliation = build_reconciliation(snapshot, [checkpoint], now=GENERATED_AT + timedelta(minutes=6), near_level_pct_threshold=_FAST_CONFIG.near_level_pct_threshold)

    comparison = reconciliation.ce_pe_comparison
    assert comparison is not None
    assert comparison.reference_strike == Decimal("1300")
    assert comparison.ce_initial_price == Decimal("30.0")
    assert comparison.pe_initial_price == Decimal("28.0")
    assert comparison.ce_thesis_status == ThesisStatus.CONFIRMED  # 30.0 -> 35.0, gained value
    assert comparison.pe_thesis_status == ThesisStatus.MIXED  # 28.0 -> 28.0 unchanged in this fixture

    five_min = next(o for o in comparison.observations if o.checkpoint_label == "5m")
    assert five_min.status == "RECORDED"
    assert five_min.ce_price == Decimal("35.0")
    assert five_min.pe_price == Decimal("28.0")
    assert five_min.underlying_spot == Decimal("1310.0")

    still_pending = next(o for o in comparison.observations if o.checkpoint_label == "15m")
    assert still_pending.status == "PENDING"
    assert still_pending.ce_price is None


def test_run_controlled_observation_set_resolves_live_atm_strike(tmp_path: Path) -> None:
    """Sprint 6, Phase 3 -- a bare underlying query expands into
    [underlying, CE, PE] using the REAL ATM strike from the live chain
    (1300 in this fixture, since spot == the fixture's own single strike)
    -- never a hardcoded guess."""
    ctx = _ctx(tmp_path)
    outcomes = asyncio.run(run_controlled_observation_set("RELIANCE", ctx=ctx, now=GENERATED_AT))
    assert len(outcomes) == 3
    assert outcomes[0].query == "RELIANCE"
    assert outcomes[1].query == "RELIANCE 1300.0 CE"
    assert outcomes[2].query == "RELIANCE 1300.0 PE"
    assert all(o.status == LiveAnalysisStatus.PERSISTED for o in outcomes)
    assert all(o.symbol == "RELIANCE" for o in outcomes)


def test_run_controlled_observation_set_leaves_a_specific_contract_query_unexpanded(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    outcomes = asyncio.run(run_controlled_observation_set("RELIANCE 1300 CE", ctx=ctx, now=GENERATED_AT))
    assert len(outcomes) == 1
    assert outcomes[0].query == "RELIANCE 1300 CE"


def test_sweep_due_checkpoints_is_scoped_to_its_own_symbol(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    outcome = asyncio.run(run_live_analysis("RELIANCE", ctx=ctx, now=GENERATED_AT))
    assert outcome.audit_id is not None

    runs = asyncio.run(sweep_due_checkpoints("RELIANCE", ctx=ctx, now=GENERATED_AT + timedelta(minutes=5)))
    assert len(runs) == 1
    assert runs[0].audit_id == outcome.audit_id
    assert runs[0].captured

    # A different symbol with no analysis on record sweeps to nothing.
    empty = asyncio.run(sweep_due_checkpoints("TCS", ctx=ctx, now=GENERATED_AT + timedelta(minutes=5)))
    assert empty == []
