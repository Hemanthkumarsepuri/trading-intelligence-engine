"""Sprint 6, Phase 10 — pure unit tests for
`app.orchestration.journal_views.build_normalized_movement()`: the
"100 + pct_change" restatement used for the dashboard's observed-movement
chart. No I/O, no journal, no pipeline -- exercises the transform in
isolation from real `CePeOutcomeComparison`/`CePeCheckpointObservation`
objects built by hand.
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.audit.models import CePeCheckpointObservation, CePeOutcomeComparison, ThesisStatus
from app.orchestration.journal_views import build_normalized_movement


def _comparison(observations: list[CePeCheckpointObservation]) -> CePeOutcomeComparison:
    return CePeOutcomeComparison(
        reference_strike=Decimal("1300"), ce_initial_price=Decimal("30.0"), pe_initial_price=Decimal("28.0"),
        underlying_initial_spot=Decimal("1300.0"), ce_thesis_status=ThesisStatus.CONFIRMED, pe_thesis_status=ThesisStatus.MIXED,
        observations=observations,
    )


def test_normalized_movement_indexes_recorded_values_to_100() -> None:
    comparison = _comparison([
        CePeCheckpointObservation(
            checkpoint_label="5m", status="RECORDED", underlying_spot=Decimal("1310.0"), underlying_pct_change=Decimal("0.77"),
            ce_price=Decimal("35.0"), ce_pct_change=Decimal("16.67"), pe_price=Decimal("28.0"), pe_pct_change=Decimal("0"),
        ),
    ])
    points = build_normalized_movement(comparison)
    assert len(points) == 1
    p = points[0]
    assert p.checkpoint_label == "5m"
    assert p.underlying_index == "100.77"
    assert p.ce_index == "116.67"
    assert p.pe_index == "100"


def test_normalized_movement_is_none_for_pending_checkpoints_never_fabricated() -> None:
    comparison = _comparison([
        CePeCheckpointObservation(
            checkpoint_label="15m", status="PENDING", underlying_spot=None, underlying_pct_change=None,
            ce_price=None, ce_pct_change=None, pe_price=None, pe_pct_change=None,
        ),
    ])
    points = build_normalized_movement(comparison)
    assert points[0].underlying_index is None
    assert points[0].ce_index is None
    assert points[0].pe_index is None


def test_normalized_movement_preserves_checkpoint_order_and_count() -> None:
    comparison = _comparison([
        CePeCheckpointObservation(checkpoint_label=label, status="PENDING", underlying_spot=None, underlying_pct_change=None, ce_price=None, ce_pct_change=None, pe_price=None, pe_pct_change=None)
        for label in ("5m", "15m", "30m", "60m", "EOD")
    ])
    points = build_normalized_movement(comparison)
    assert [p.checkpoint_label for p in points] == ["5m", "15m", "30m", "60m", "EOD"]
