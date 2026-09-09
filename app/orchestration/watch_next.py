""""What should I watch next?" (Master Product Grooming Sprint, Section
8/Part 8) — converts evidence ALREADY computed for this analysis into a
small set of deterministic monitoring conditions. This is explicitly NOT
a recommendation engine: every condition is phrased as something to
OBSERVE ("underlying remains above X"), never an instruction to act
("buy CE if X"). No new domain computation happens here — every WATCH/
WHY/CURRENT value is copied from an already-computed field on the
`OptionsIntelligenceReport` (`invalidation_level`, `support_levels`,
`resistance_levels`, `direction_comparison`, `term_structure`); this
module only selects, formats, and evaluates whether each named condition
currently holds.

`status` is one of HOLDS / DOES_NOT_HOLD / CANNOT_BE_EVALUATED.
CANNOT_BE_EVALUATED covers two honest cases: the inputs needed to judge
the condition are missing, or the condition is inherently informational
(e.g. "CE/PE evidence currently diverges") rather than a single
above/below threshold — never guessed as HOLDS/DOES_NOT_HOLD.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from app.domain.options.evidence_matrix import EvidenceDirection
from app.domain.options.support_resistance import Level
from app.orchestration.options_intelligence_report import OptionsIntelligenceReport


class WatchStatus(str, Enum):
    HOLDS = "HOLDS"
    DOES_NOT_HOLD = "DOES_NOT_HOLD"
    CANNOT_BE_EVALUATED = "CANNOT_BE_EVALUATED"


@dataclass(frozen=True)
class WatchCondition:
    watch: str
    why: str
    current: str
    status: WatchStatus


def build_watch_conditions(report: OptionsIntelligenceReport) -> list[WatchCondition]:
    """Never raises, never fabricates a condition from missing data --
    each condition below is added only when its underlying evidence is
    actually present on `report`."""
    conditions: list[WatchCondition] = []

    conditions.extend(_invalidation_condition(report))
    conditions.extend(_level_conditions(report))
    conditions.extend(_ce_pe_divergence_condition(report))
    conditions.extend(_expiry_proximity_condition(report))
    conditions.extend(_development_conditions(report))

    return conditions


def _development_conditions(report: OptionsIntelligenceReport) -> list[WatchCondition]:
    d = report.development
    if d is None:
        return []
    return [
        WatchCondition(
            watch=f"CONFIRM IF: {d.confirm_if}",
            why=d.what_is_missing,
            current=d.freshness_note,
            status=WatchStatus.CANNOT_BE_EVALUATED,
        ),
        WatchCondition(
            watch=f"INVALIDATE IF: {d.invalidate_if}",
            why=d.why_it_matters,
            current=d.pattern.value,
            status=WatchStatus.CANNOT_BE_EVALUATED,
        ),
    ]


def _invalidation_condition(report: OptionsIntelligenceReport) -> list[WatchCondition]:
    level = report.invalidation_level
    spot = report.spot
    if level is None:
        return []

    why = (
        report.candidates[0].invalidation_condition
        if report.candidates
        else f"the current thesis's documented invalidation level is {level}"
    )
    current = f"Spot is {spot}" if spot is not None else "Spot is unavailable"

    bias = report.decision.assessment.market_bias if report.decision is not None else EvidenceDirection.UNKNOWN
    if spot is None or bias not in (EvidenceDirection.BULLISH, EvidenceDirection.BEARISH):
        status = WatchStatus.CANNOT_BE_EVALUATED
    elif bias == EvidenceDirection.BULLISH:
        status = WatchStatus.HOLDS if spot > level else WatchStatus.DOES_NOT_HOLD
    else:  # BEARISH
        status = WatchStatus.HOLDS if spot < level else WatchStatus.DOES_NOT_HOLD

    return [WatchCondition(watch=f"Underlying stays on the side of {level} where the current thesis holds", why=why, current=current, status=status)]


def _level_conditions(report: OptionsIntelligenceReport) -> list[WatchCondition]:
    conditions: list[WatchCondition] = []
    spot = report.spot

    def _nearest(levels: list[Level]) -> Level | None:
        with_distance = [lv for lv in levels if lv.distance_from_spot is not None]
        if not with_distance:
            return levels[0] if levels else None
        return min(with_distance, key=lambda lv: abs(lv.distance_from_spot or Decimal(0)))

    nearest_support = _nearest(list(report.support_levels))
    if nearest_support is not None:
        current = f"Spot is {spot}" if spot is not None else "Spot is unavailable"
        status = (
            WatchStatus.CANNOT_BE_EVALUATED if spot is None
            else (WatchStatus.HOLDS if spot > nearest_support.strike else WatchStatus.DOES_NOT_HOLD)
        )
        conditions.append(WatchCondition(
            watch=f"Underlying remains above the nearest support candidate at {nearest_support.strike} ({nearest_support.strength.value})",
            why=nearest_support.evidence, current=current, status=status,
        ))

    nearest_resistance = _nearest(list(report.resistance_levels))
    if nearest_resistance is not None:
        current = f"Spot is {spot}" if spot is not None else "Spot is unavailable"
        status = (
            WatchStatus.CANNOT_BE_EVALUATED if spot is None
            else (WatchStatus.HOLDS if spot < nearest_resistance.strike else WatchStatus.DOES_NOT_HOLD)
        )
        conditions.append(WatchCondition(
            watch=f"Underlying remains below the nearest resistance candidate at {nearest_resistance.strike} ({nearest_resistance.strength.value})",
            why=nearest_resistance.evidence, current=current, status=status,
        ))

    return conditions


def _ce_pe_divergence_condition(report: OptionsIntelligenceReport) -> list[WatchCondition]:
    dc = report.direction_comparison
    if dc is None:
        return []
    return [WatchCondition(
        watch="Whether CE and PE evidence continues to point the same way",
        why=dc.verdict_detail,
        current=dc.verdict.value,
        status=WatchStatus.CANNOT_BE_EVALUATED,  # informational classification, not a threshold -- never guessed as holding/not holding
    )]


def _expiry_proximity_condition(report: OptionsIntelligenceReport) -> list[WatchCondition]:
    ts = report.term_structure
    if ts is None or not ts.expiries:
        return []
    nearest = min(ts.expiries, key=lambda e: e.days_remaining)
    return [WatchCondition(
        watch=f"Time remaining to the nearest analyzed expiry ({nearest.expiry.isoformat()})",
        why="Premium decay (theta) accelerates as an option's expiry approaches -- a standard, well-known options mechanic, not a system-specific prediction.",
        current=f"{nearest.days_remaining} day(s) remaining",
        status=WatchStatus.CANNOT_BE_EVALUATED,  # informational -- there is no above/below threshold for time
    )]
