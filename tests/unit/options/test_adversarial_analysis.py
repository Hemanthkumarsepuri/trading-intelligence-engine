from __future__ import annotations

from app.domain.options.adversarial_analysis import build_adversarial_analysis
from app.domain.options.evidence_matrix import (
    EvidenceDirection,
    EvidenceGroup,
    EvidenceMatrix,
    EvidenceRow,
)


def _row(name: str, group: EvidenceGroup, direction: EvidenceDirection, detail: str = "detail") -> EvidenceRow:
    return EvidenceRow(name, group, direction, detail)


def test_bull_and_bear_cases_collect_the_matching_rows() -> None:
    matrix = EvidenceMatrix(
        rows=[
            _row("M15 trend", EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH, "ema ascending"),
            _row("Resistance", EvidenceGroup.OPTIONS_OI, EvidenceDirection.BEARISH, "heavy CE OI overhead"),
            _row("IV", EvidenceGroup.OPTIONS_IV, EvidenceDirection.NEUTRAL, "not directional"),
        ]
    )
    result = build_adversarial_analysis(matrix)
    assert result.bull_case == ["M15 trend: ema ascending"]
    assert result.bear_case == ["Resistance: heavy CE OI overhead"]


def test_missing_data_collects_unknown_rows() -> None:
    matrix = EvidenceMatrix(rows=[_row("VWAP", EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.UNKNOWN, "insufficient history")])
    result = build_adversarial_analysis(matrix)
    assert result.missing_data == ["VWAP: insufficient history"]


def test_contradiction_detected_when_a_group_disagrees_internally() -> None:
    matrix = EvidenceMatrix(
        rows=[
            _row("M15 trend", EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH),
            _row("VWAP", EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BEARISH),
        ]
    )
    result = build_adversarial_analysis(matrix)
    assert any("underlying_price_structure" in c for c in result.contradictions)
    assert "M15 trend" in result.contradictions[0] and "VWAP" in result.contradictions[0]


def test_no_contradiction_when_groups_are_internally_consistent() -> None:
    matrix = EvidenceMatrix(
        rows=[
            _row("M15 trend", EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH),
            _row("VWAP", EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH),
        ]
    )
    result = build_adversarial_analysis(matrix)
    assert result.contradictions == []
    assert result.opposite_case_is_equally_supported is False


def test_opposite_case_equally_supported_when_independent_groups_conflict() -> None:
    matrix = EvidenceMatrix(
        rows=[
            _row("M15 trend", EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH),
            _row("Resistance", EvidenceGroup.OPTIONS_OI, EvidenceDirection.BEARISH),
        ]
    )
    result = build_adversarial_analysis(matrix)
    assert result.opposite_case_is_equally_supported is True
    assert any("CONFLICT" in c for c in result.contradictions)


def test_key_risk_flagged_when_both_directions_have_evidence() -> None:
    matrix = EvidenceMatrix(
        rows=[
            _row("M15 trend", EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH),
            _row("Resistance", EvidenceGroup.OPTIONS_OI, EvidenceDirection.BEARISH),
        ]
    )
    result = build_adversarial_analysis(matrix)
    assert any("both directions" in r for r in result.key_risks)


def test_key_risk_flagged_when_neither_direction_has_evidence() -> None:
    matrix = EvidenceMatrix(rows=[_row("IV", EvidenceGroup.OPTIONS_IV, EvidenceDirection.NEUTRAL)])
    result = build_adversarial_analysis(matrix)
    assert any("neither direction" in r for r in result.key_risks)


def test_key_risk_flagged_for_untradeable_liquidity() -> None:
    matrix = EvidenceMatrix(rows=[_row("Liquidity", EvidenceGroup.LIQUIDITY, EvidenceDirection.NEUTRAL, "best candidate liquidity grade: untradeable")])
    result = build_adversarial_analysis(matrix)
    assert any("untradeable" in r for r in result.key_risks)


def test_key_risk_flagged_for_degraded_freshness() -> None:
    matrix = EvidenceMatrix(rows=[_row("Data freshness", EvidenceGroup.DATA_QUALITY, EvidenceDirection.UNKNOWN, "stale data")])
    result = build_adversarial_analysis(matrix)
    assert any("degraded" in r for r in result.key_risks)


def test_never_fabricates_evidence_beyond_the_matrix() -> None:
    # Empty matrix -> everything empty, never a guessed case.
    result = build_adversarial_analysis(EvidenceMatrix(rows=[]))
    assert result.bull_case == []
    assert result.bear_case == []
    assert result.missing_data == []
