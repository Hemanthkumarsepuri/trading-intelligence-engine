from __future__ import annotations

from decimal import Decimal

from app.domain.market.models import OptionRight
from app.domain.options.development import DevelopmentPattern, classify_development
from app.domain.options.evidence_matrix import EvidenceDirection, OverallConvergence
from app.domain.options.oi_migration import OIMigrationDirection, OIMigrationResult
from app.domain.options.price_oi_interpretation import BasisChangeDirection, BasisChangeObservation
from app.domain.options.temporal_evidence import EvidenceQuality


def _mig(direction: OIMigrationDirection) -> OIMigrationResult:
    return OIMigrationResult(
        right=OptionRight.CE,
        entries=[],
        direction=direction,
        quality=EvidenceQuality.HIGH,
        interpretation="test",
    )


def test_oi_migration_pattern_when_chain_current() -> None:
    n = classify_development(
        convergence=OverallConvergence.CONVERGENCE_BULLISH,
        chain_is_current=True,
        candles_are_current=False,
        futures_are_current=True,
        quote_is_current=True,
        ce_migration=_mig(OIMigrationDirection.MIGRATING_HIGHER),
        pe_migration=None,
        rs_direction=EvidenceDirection.NEUTRAL,
        m15_direction=EvidenceDirection.UNKNOWN,
        basis_change=None,
    )
    assert n.pattern == DevelopmentPattern.OI_MIGRATION
    assert n.confirm_if
    assert n.invalidate_if


def test_conflict_is_not_early_development() -> None:
    n = classify_development(
        convergence=OverallConvergence.CONFLICT,
        chain_is_current=True,
        candles_are_current=True,
        futures_are_current=True,
        quote_is_current=True,
        ce_migration=_mig(OIMigrationDirection.MIGRATING_HIGHER),
        pe_migration=None,
        rs_direction=EvidenceDirection.BULLISH,
        m15_direction=EvidenceDirection.BULLISH,
        basis_change=None,
    )
    assert n.pattern == DevelopmentPattern.NONE


def test_relative_strength_rotation_when_sector_leads_with_stock() -> None:
    n = classify_development(
        **_base(  # type: ignore[arg-type]
            rs_direction=EvidenceDirection.BULLISH,
            m15_direction=EvidenceDirection.NEUTRAL,
            sector_rs_tier="STOCK_LEADING",
        )
    )
    assert n.pattern == DevelopmentPattern.RELATIVE_STRENGTH_ROTATION
    n = classify_development(
        convergence=OverallConvergence.INSUFFICIENT_EVIDENCE,
        chain_is_current=True,
        candles_are_current=True,
        futures_are_current=False,
        quote_is_current=True,
        ce_migration=None,
        pe_migration=None,
        rs_direction=EvidenceDirection.BULLISH,
        m15_direction=EvidenceDirection.NEUTRAL,
        basis_change=None,
    )
    assert n.pattern == DevelopmentPattern.RELATIVE_STRENGTH


def test_single_basis_print_is_not_futures_structure() -> None:
    n = classify_development(
        convergence=OverallConvergence.INSUFFICIENT_EVIDENCE,
        chain_is_current=True,
        candles_are_current=True,
        futures_are_current=True,
        quote_is_current=True,
        ce_migration=None,
        pe_migration=None,
        rs_direction=EvidenceDirection.NEUTRAL,
        m15_direction=EvidenceDirection.NEUTRAL,
        basis_change=BasisChangeObservation(
            direction=BasisChangeDirection.INSUFFICIENT_DATA,
            current_basis_pct=Decimal("0.5"),
            previous_basis_pct=None,
            change_pct_points=None,
            interpretation="no prior",
        ),
    )
    assert n.pattern == DevelopmentPattern.NONE


def test_comparable_basis_change_is_futures_structure() -> None:
    n = classify_development(
        convergence=OverallConvergence.INSUFFICIENT_EVIDENCE,
        chain_is_current=True,
        candles_are_current=True,
        futures_are_current=True,
        quote_is_current=True,
        ce_migration=None,
        pe_migration=None,
        rs_direction=EvidenceDirection.NEUTRAL,
        m15_direction=EvidenceDirection.NEUTRAL,
        basis_change=BasisChangeObservation(
            direction=BasisChangeDirection.WIDENING_PREMIUM,
            current_basis_pct=Decimal("0.8"),
            previous_basis_pct=Decimal("0.2"),
            change_pct_points=Decimal("0.6"),
            interpretation="widening",
        ),
    )
    assert n.pattern == DevelopmentPattern.FUTURES_STRUCTURE


def _base(**overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "convergence": OverallConvergence.INSUFFICIENT_EVIDENCE,
        "chain_is_current": True,
        "candles_are_current": True,
        "futures_are_current": True,
        "quote_is_current": True,
        "ce_migration": None,
        "pe_migration": None,
        "rs_direction": EvidenceDirection.NEUTRAL,
        "m15_direction": EvidenceDirection.NEUTRAL,
        "basis_change": None,
    }
    kwargs.update(overrides)
    return kwargs


def test_pre_breakout_compression_when_flags_and_streams_current() -> None:
    n = classify_development(**_base(pre_breakout_compression=True))  # type: ignore[arg-type]
    assert n.pattern == DevelopmentPattern.PRE_BREAKOUT_COMPRESSION
    assert "opposing level" in n.what_is_developing.lower() or "compress" in n.what_is_developing.lower()


def test_pre_breakout_does_not_fire_on_stale_candles() -> None:
    n = classify_development(**_base(pre_breakout_compression=True, candles_are_current=False))  # type: ignore[arg-type]
    assert n.pattern != DevelopmentPattern.PRE_BREAKOUT_COMPRESSION


def test_conflict_outranks_pre_breakout() -> None:
    n = classify_development(**_base(convergence=OverallConvergence.CONFLICT, pre_breakout_compression=True))  # type: ignore[arg-type]
    assert n.pattern == DevelopmentPattern.NONE


def test_failed_breakdown_reclaim_when_streams_current() -> None:
    n = classify_development(**_base(failed_breakdown_reclaim=True))  # type: ignore[arg-type]
    assert n.pattern == DevelopmentPattern.FAILED_BREAKDOWN_RECLAIM
