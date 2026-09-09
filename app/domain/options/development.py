"""Evidence-specific early development -- never a numeric score.

WATCH vs EARLY_SETUP is decided by named, testable patterns (OI migration,
relative-strength emergence, futures basis change), not by counting
supporting evidence groups.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.domain.options.evidence_matrix import EvidenceDirection, OverallConvergence
from app.domain.options.oi_migration import OIMigrationDirection, OIMigrationResult
from app.domain.options.price_oi_interpretation import BasisChangeDirection, BasisChangeObservation
from app.domain.options.temporal_evidence import EvidenceQuality


class DevelopmentPattern(str, Enum):
    NONE = "NONE"
    OI_MIGRATION = "OI_MIGRATION"
    RELATIVE_STRENGTH = "RELATIVE_STRENGTH"
    FUTURES_STRUCTURE = "FUTURES_STRUCTURE"
    PRE_BREAKOUT_COMPRESSION = "PRE_BREAKOUT_COMPRESSION"
    FAILED_BREAKDOWN_RECLAIM = "FAILED_BREAKDOWN_RECLAIM"
    RELATIVE_STRENGTH_ROTATION = "RELATIVE_STRENGTH_ROTATION"


@dataclass(frozen=True)
class DevelopmentNarrative:
    pattern: DevelopmentPattern
    what_is_developing: str
    why_it_matters: str
    what_is_missing: str
    confirm_if: str
    invalidate_if: str
    freshness_note: str


def _none(*, missing: str, freshness_note: str) -> DevelopmentNarrative:
    return DevelopmentNarrative(
        pattern=DevelopmentPattern.NONE,
        what_is_developing="No structured early development identified from independent observations.",
        why_it_matters="Absence of a named pattern is not a directional conclusion.",
        what_is_missing=missing,
        confirm_if="A named pattern appears with current required streams and no independent conflict.",
        invalidate_if="Independent evidence groups enter CONFLICT, or required streams become unusable.",
        freshness_note=freshness_note,
    )


def _migration_is_meaningful(result: OIMigrationResult | None) -> bool:
    if result is None:
        return False
    if result.direction not in (OIMigrationDirection.MIGRATING_HIGHER, OIMigrationDirection.MIGRATING_LOWER):
        return False
    return result.quality in (EvidenceQuality.HIGH, EvidenceQuality.MODERATE)


def classify_development(
    *,
    convergence: OverallConvergence,
    chain_is_current: bool,
    candles_are_current: bool,
    futures_are_current: bool,
    quote_is_current: bool,
    ce_migration: OIMigrationResult | None,
    pe_migration: OIMigrationResult | None,
    rs_direction: EvidenceDirection,
    m15_direction: EvidenceDirection,
    basis_change: BasisChangeObservation | None,
    pre_breakout_compression: bool = False,
    failed_breakdown_reclaim: bool = False,
    sector_rs_tier: str | None = None,
) -> DevelopmentNarrative:
    freshness = (
        f"quote={'current' if quote_is_current else 'not current'}; "
        f"chain={'current' if chain_is_current else 'not current'}; "
        f"M15={'current' if candles_are_current else 'not current'}; "
        f"futures={'current' if futures_are_current else 'not current'}"
    )
    if convergence == OverallConvergence.CONFLICT:
        return _none(missing="Independent evidence groups disagree -- not an early setup.", freshness_note=freshness)

    # Pre-breakout is checked before OI migration: it is the earlier,
    # cheaper structural signature (compression + proximity to a real
    # level). It is only True when the caller already verified the
    # documented combo -- this function does not recompute it.
    if pre_breakout_compression and quote_is_current and candles_are_current:
        return DevelopmentNarrative(
            pattern=DevelopmentPattern.PRE_BREAKOUT_COMPRESSION,
            what_is_developing="Price is compressing near a real opposing level while structure has not yet broken.",
            why_it_matters="Range contraction plus proximity to structure is an observable preparation state, not a prediction that a breakout will occur.",
            what_is_missing="A held break of the nearby level, plus independent chain/futures confirmation.",
            confirm_if="Price holds beyond the nearby opposing level on current M15 without independent groups entering CONFLICT.",
            invalidate_if="Compression expands without a break, the nearby level rejects, or independent groups CONFLICT.",
            freshness_note=freshness,
        )

    if failed_breakdown_reclaim and quote_is_current and candles_are_current:
        return DevelopmentNarrative(
            pattern=DevelopmentPattern.FAILED_BREAKDOWN_RECLAIM,
            what_is_developing="A recent break of swing structure failed to hold and price has reclaimed.",
            why_it_matters="A failed break that is then reclaimed is an observable structural event -- not a claim of trapped traders.",
            what_is_missing="Independent confirmation from relative strength, chain positioning, or futures structure.",
            confirm_if="The reclaimed level continues to hold on current M15 and independent groups do not CONFLICT.",
            invalidate_if="Price loses the reclaimed level again, or independent groups enter CONFLICT.",
            freshness_note=freshness,
        )

    if chain_is_current and (_migration_is_meaningful(ce_migration) or _migration_is_meaningful(pe_migration)):
        pieces = []
        if _migration_is_meaningful(ce_migration) and ce_migration is not None:
            pieces.append(f"CE OI {ce_migration.direction.value}")
        if _migration_is_meaningful(pe_migration) and pe_migration is not None:
            pieces.append(f"PE OI {pe_migration.direction.value}")
        return DevelopmentNarrative(
            pattern=DevelopmentPattern.OI_MIGRATION,
            what_is_developing="Open interest is shifting across strikes.",
            why_it_matters=(
                "This is observable positioning change, not a claim of trader intent. "
                "Technical: " + "; ".join(pieces) + "."
            ),
            what_is_missing=(
                "Current M15 structure has not independently confirmed the same direction"
                if not candles_are_current or m15_direction in (EvidenceDirection.UNKNOWN, EvidenceDirection.NEUTRAL)
                else "A second independent group (price structure or futures) has not fully confirmed."
            ),
            confirm_if=(
                "Price structure on current M15 holds with the positioning change still present, "
                "and independent groups do not conflict."
            ),
            invalidate_if="OI-weighted strike migration reverses, or independent groups enter CONFLICT.",
            freshness_note=freshness,
        )

    opposite = (
        (rs_direction == EvidenceDirection.BULLISH and m15_direction == EvidenceDirection.BEARISH)
        or (rs_direction == EvidenceDirection.BEARISH and m15_direction == EvidenceDirection.BULLISH)
    )
    if (
        quote_is_current
        and candles_are_current
        and rs_direction in (EvidenceDirection.BULLISH, EvidenceDirection.BEARISH)
        and not opposite
        and m15_direction in (rs_direction, EvidenceDirection.NEUTRAL)
    ):
        rotation = sector_rs_tier in ("STOCK_LEADING", "SECTOR_LEADING_STOCK_INLINE")
        return DevelopmentNarrative(
            pattern=(
                DevelopmentPattern.RELATIVE_STRENGTH_ROTATION if rotation else DevelopmentPattern.RELATIVE_STRENGTH
            ),
            what_is_developing=(
                "Stock is strengthening versus Nifty with official sector context also supporting, while price structure is not opposing."
                if rotation
                else "Stock day-change is diverging from Nifty while price structure is not opposing."
            ),
            why_it_matters=(
                f"Relative strength {rs_direction.value}; M15 {m15_direction.value}"
                + (f"; sector RS {sector_rs_tier}" if rotation else "")
                + ". This is stock-vs-index comparison, not a second copy of the Nifty GLOBAL row."
            ),
            what_is_missing="Option-chain positioning and/or futures structure have not independently confirmed.",
            confirm_if="Current chain positioning and price structure agree with the relative-strength side without CONFLICT.",
            invalidate_if="Relative strength collapses against Nifty, or M15 turns opposite, or CONFLICT appears.",
            freshness_note=freshness,
        )

    if (
        futures_are_current
        and basis_change is not None
        and basis_change.direction not in (BasisChangeDirection.INSUFFICIENT_DATA, BasisChangeDirection.STABLE)
    ):
        return DevelopmentNarrative(
            pattern=DevelopmentPattern.FUTURES_STRUCTURE,
            what_is_developing="Futures basis vs spot changed vs a comparable prior observation.",
            why_it_matters=basis_change.interpretation,
            what_is_missing="Price structure and/or option-chain confirmation are incomplete.",
            confirm_if="Current M15 structure and chain positioning remain compatible with the basis change.",
            invalidate_if="Basis change reverses on a comparable observation, or independent groups CONFLICT.",
            freshness_note=freshness,
        )

    return _none(
        missing="No pre-breakout compression, failed-breakdown reclaim, OI-migration, relative-strength, relative-strength rotation, or comparable futures-basis pattern met the documented tests.",
        freshness_note=freshness,
    )
