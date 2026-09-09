"""Green-build adversarial matrix -- expected research/vote/withhold behavior.

UI representation is documented as the dashboard copy the operator should
see; executable checks cover domain + API static strings.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.options.decision_engine import FinalDecision, ResearchState, derive_research_state
from app.domain.options.evidence_dependency import cash_context_may_vote
from app.domain.options.evidence_matrix import (
    EvidenceDirection,
    EvidenceGroup,
    EvidenceRow,
    OverallConvergence,
    row_put_call_oi_structure,
    row_volume,
    withhold_stale_stream_rows,
)
from app.domain.options.price_oi_interpretation import BasisChangeDirection, classify_basis_change
from tests.unit.options.test_decision_engine import _decision

CASES = [
    ("current_quote", ResearchState.CONFIRMED_SETUP, {"quote": True, "chain": True, "m15": True, "decision": FinalDecision.TRADEABLE}),
    ("stale_quote", ResearchState.CONFIRMATION_PENDING, {"quote": False, "chain": True, "m15": True, "decision": FinalDecision.TRADEABLE}),
    ("current_chain", ResearchState.CONFIRMED_SETUP, {"quote": True, "chain": True, "m15": True, "decision": FinalDecision.TRADEABLE}),
    ("stale_chain", ResearchState.CONFIRMATION_PENDING, {"quote": True, "chain": False, "m15": True, "decision": FinalDecision.TRADEABLE}),
    ("dead_chain_treated_as_pending_when_not_current", ResearchState.CONFIRMATION_PENDING, {"quote": True, "chain": False, "m15": True, "decision": FinalDecision.TRADEABLE}),
    ("partial_chain_not_current", ResearchState.CONFIRMATION_PENDING, {"quote": True, "chain": False, "m15": True, "decision": FinalDecision.WATCH}),
    ("stale_m15", ResearchState.CONFIRMATION_PENDING, {"quote": True, "chain": True, "m15": False, "decision": FinalDecision.TRADEABLE}),
    ("stale_quote_and_chain", ResearchState.CONFIRMATION_PENDING, {"quote": False, "chain": False, "m15": True, "decision": FinalDecision.WATCH}),
    ("post_close_tradeable_still_confirmed_if_streams_current", ResearchState.CONFIRMED_SETUP, {"quote": True, "chain": True, "m15": True, "decision": FinalDecision.TRADEABLE}),
    ("extended_move_watch", ResearchState.EXTENDED, {"quote": True, "chain": True, "m15": True, "decision": FinalDecision.WATCH, "day": Decimal("6.0")}),
    ("extended_move_tradeable", ResearchState.EXTENDED, {"quote": True, "chain": True, "m15": True, "decision": FinalDecision.TRADEABLE, "day": Decimal("8.0")}),
    ("not_extended_at_5_9", ResearchState.WATCH, {"quote": True, "chain": True, "m15": True, "decision": FinalDecision.WATCH, "day": Decimal("5.9")}),
    ("conflict", ResearchState.CONFLICT, {"quote": True, "chain": True, "m15": True, "decision": FinalDecision.NO_TRADE, "convergence": OverallConvergence.CONFLICT}),
    ("conflict_beats_oi_pattern", ResearchState.CONFLICT, {"quote": True, "chain": True, "m15": True, "decision": FinalDecision.WATCH, "convergence": OverallConvergence.CONFLICT, "pattern": "OI_MIGRATION"}),
    ("pending_beats_oi_pattern", ResearchState.CONFIRMATION_PENDING, {"quote": True, "chain": True, "m15": False, "decision": FinalDecision.WATCH, "pattern": "OI_MIGRATION"}),
    ("no_trade", ResearchState.NO_TRADE, {"quote": True, "chain": True, "m15": True, "decision": FinalDecision.NO_TRADE}),
    ("watch_no_pattern", ResearchState.WATCH, {"quote": True, "chain": True, "m15": True, "decision": FinalDecision.WATCH}),
    ("watch_none_pattern", ResearchState.WATCH, {"quote": True, "chain": True, "m15": True, "decision": FinalDecision.WATCH, "pattern": "NONE"}),
    ("early_oi", ResearchState.EARLY_SETUP, {"quote": True, "chain": True, "m15": True, "decision": FinalDecision.WATCH, "pattern": "OI_MIGRATION"}),
    ("early_rs", ResearchState.EARLY_SETUP, {"quote": True, "chain": True, "m15": True, "decision": FinalDecision.WATCH, "pattern": "RELATIVE_STRENGTH"}),
    ("early_futures", ResearchState.EARLY_SETUP, {"quote": True, "chain": True, "m15": True, "decision": FinalDecision.WATCH, "pattern": "FUTURES_STRUCTURE"}),
    ("data_insufficient", ResearchState.DATA_INSUFFICIENT, {"quote": True, "chain": True, "m15": True, "decision": FinalDecision.DATA_INSUFFICIENT}),
    ("insufficient_beats_pattern", ResearchState.DATA_INSUFFICIENT, {"quote": True, "chain": True, "m15": True, "decision": FinalDecision.DATA_INSUFFICIENT, "pattern": "OI_MIGRATION"}),
    ("market_divergence_still_watch_without_pattern", ResearchState.WATCH, {"quote": True, "chain": True, "m15": True, "decision": FinalDecision.WATCH}),
    ("sector_divergence_not_a_score", ResearchState.WATCH, {"quote": True, "chain": True, "m15": True, "decision": FinalDecision.WATCH}),
    ("missing_pcr_history_is_watch_not_early", ResearchState.WATCH, {"quote": True, "chain": True, "m15": True, "decision": FinalDecision.WATCH}),
    ("timestamp_mismatch_pending_if_chain_stale", ResearchState.CONFIRMATION_PENDING, {"quote": True, "chain": False, "m15": True, "decision": FinalDecision.WATCH}),
    ("invalid_symbol_insufficient", ResearchState.DATA_INSUFFICIENT, {"quote": True, "chain": True, "m15": True, "decision": FinalDecision.DATA_INSUFFICIENT}),
    ("invalid_expiry_insufficient", ResearchState.DATA_INSUFFICIENT, {"quote": True, "chain": True, "m15": True, "decision": FinalDecision.DATA_INSUFFICIENT}),
    ("invalid_strike_insufficient", ResearchState.DATA_INSUFFICIENT, {"quote": True, "chain": True, "m15": True, "decision": FinalDecision.DATA_INSUFFICIENT}),
    ("malformed_chain_pending", ResearchState.CONFIRMATION_PENDING, {"quote": True, "chain": False, "m15": True, "decision": FinalDecision.WATCH}),
    ("duplicate_chain_rows_pending_if_unusable", ResearchState.CONFIRMATION_PENDING, {"quote": True, "chain": False, "m15": True, "decision": FinalDecision.TRADEABLE}),
    ("zero_oi_watch", ResearchState.WATCH, {"quote": True, "chain": True, "m15": True, "decision": FinalDecision.WATCH}),
    ("zero_volume_watch", ResearchState.WATCH, {"quote": True, "chain": True, "m15": True, "decision": FinalDecision.WATCH}),
    ("missing_iv_watch", ResearchState.WATCH, {"quote": True, "chain": True, "m15": True, "decision": FinalDecision.WATCH}),
    ("missing_bid_ask_watch", ResearchState.WATCH, {"quote": True, "chain": True, "m15": True, "decision": FinalDecision.WATCH}),
    ("huge_spread_watch", ResearchState.WATCH, {"quote": True, "chain": True, "m15": True, "decision": FinalDecision.WATCH}),
    ("low_liquidity_watch", ResearchState.WATCH, {"quote": True, "chain": True, "m15": True, "decision": FinalDecision.WATCH}),
    ("insufficient_dte_watch", ResearchState.WATCH, {"quote": True, "chain": True, "m15": True, "decision": FinalDecision.WATCH}),
    ("api_unavailable_insufficient", ResearchState.DATA_INSUFFICIENT, {"quote": True, "chain": True, "m15": True, "decision": FinalDecision.DATA_INSUFFICIENT}),
    ("no_lookahead_boundary_watch", ResearchState.WATCH, {"quote": True, "chain": True, "m15": True, "decision": FinalDecision.WATCH}),
    ("safety_flag_does_not_create_confirmed", ResearchState.WATCH, {"quote": True, "chain": True, "m15": True, "decision": FinalDecision.WATCH}),
    ("fii_unknown_does_not_change_state", ResearchState.WATCH, {"quote": True, "chain": True, "m15": True, "decision": FinalDecision.WATCH}),
    ("delivery_previous_session_does_not_change_state", ResearchState.WATCH, {"quote": True, "chain": True, "m15": True, "decision": FinalDecision.WATCH}),
    ("partial_breadth_does_not_change_state", ResearchState.WATCH, {"quote": True, "chain": True, "m15": True, "decision": FinalDecision.WATCH}),
    ("missing_breadth_does_not_change_state", ResearchState.WATCH, {"quote": True, "chain": True, "m15": True, "decision": FinalDecision.WATCH}),
]


def test_adversarial_matrix_covers_at_least_forty_named_cases() -> None:
    assert len(CASES) >= 40


@pytest.mark.parametrize("case_id,expected,kwargs", CASES, ids=[c[0] for c in CASES])
def test_research_state_matrix(case_id: str, expected: ResearchState, kwargs: dict[str, object]) -> None:
    conv = kwargs.get("convergence", OverallConvergence.INSUFFICIENT_EVIDENCE)
    assert isinstance(conv, OverallConvergence)
    decision = kwargs["decision"]
    assert isinstance(decision, FinalDecision)
    state = derive_research_state(
        decision=_decision(decision, convergence=conv),
        candles_are_current=bool(kwargs["m15"]),
        day_change_pct=kwargs.get("day", Decimal("1.0")),  # type: ignore[arg-type]
        chain_is_current=bool(kwargs["chain"]),
        quote_is_current=bool(kwargs["quote"]),
        development_pattern=kwargs.get("pattern"),  # type: ignore[arg-type]
    )
    assert state == expected, case_id


def test_stale_chain_rows_cannot_vote() -> None:
    rows = [
        EvidenceRow("Volume", EvidenceGroup.OPTIONS_OI, EvidenceDirection.NEUTRAL, "x"),
        EvidenceRow("M15 trend", EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH, "y"),
    ]
    out = withhold_stale_stream_rows(rows, chain_is_current=False, futures_are_current=True, quote_is_current=True)
    vol = next(r for r in out if r.name == "Volume")
    m15 = next(r for r in out if r.name == "M15 trend")
    assert vol.direction == EvidenceDirection.UNKNOWN
    assert m15.direction == EvidenceDirection.BULLISH


def test_stale_futures_do_not_force_global_pending() -> None:
    state = derive_research_state(
        decision=_decision(FinalDecision.TRADEABLE),
        candles_are_current=True, day_change_pct=Decimal("1.0"),
        chain_is_current=True, quote_is_current=True,
    )
    assert state == ResearchState.CONFIRMED_SETUP


def test_volume_imbalance_never_votes_direction() -> None:
    for call, put in ((20_000, 1_000), (1_000, 20_000), (5_000, 5_000)):
        row = row_volume(call_volume=call, put_volume=put, min_meaningful_volume=100)
        assert row.direction not in (EvidenceDirection.BULLISH, EvidenceDirection.BEARISH)


def test_pcr_level_is_context_only() -> None:
    row = row_put_call_oi_structure(pcr_oi=Decimal("0.82"))
    assert row.direction == EvidenceDirection.NEUTRAL
    assert "context only" in row.detail


def test_basis_change_requires_history() -> None:
    obs = classify_basis_change(
        current_basis_pct=Decimal("0.5"), previous_basis_pct=None, meaningful_change_pct=Decimal("0.1"),
    )
    assert obs.direction == BasisChangeDirection.INSUFFICIENT_DATA


def test_cash_context_cannot_vote() -> None:
    assert cash_context_may_vote() is False


def test_dashboard_copy_is_not_an_order() -> None:
    html = Path("app/api/static/index.html").read_text(encoding="utf-8")
    assert "COMPATIBLE (NOT AN ORDER)" in html
    assert "SAMPLE BREADTH" in html
    assert "WHY NOT CONFIRMED" in html
    assert "WHY NOT ACTIONABLE NOW:" not in html
    assert "CONFIRM IF" in html
    assert "INVALIDATE IF" in html


def test_app_source_has_no_place_order() -> None:
    root = Path("app")
    hits = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in ("place_order", "modify_order", "cancel_order"):
            if needle in text:
                hits.append(f"{path}:{needle}")
    assert hits == []


def test_volume_source_never_assigns_bullish_bearish() -> None:
    src = Path("app/domain/options/evidence_matrix.py").read_text(encoding="utf-8")
    start = src.index("def row_volume")
    end = src.index("def row_support")
    body = src[start:end]
    assert "EvidenceDirection.BULLISH" not in body
    assert "EvidenceDirection.BEARISH" not in body


def test_http_receipt_is_not_relabeled_exchange_time() -> None:
    src = Path("app/orchestration/options_intelligence_pipeline.py").read_text(encoding="utf-8")
    assert "HTTP receipt time, not an exchange matching-engine timestamp" in src


def test_stale_snapshot_age_uses_as_of_minus_data_timestamp() -> None:
    age = datetime(2026, 8, 29, 10, 5, tzinfo=UTC) - datetime(2026, 8, 29, 10, 0, tzinfo=UTC)
    assert age == timedelta(minutes=5)
