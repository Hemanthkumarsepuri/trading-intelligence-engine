"""Sprint 3.1 -- M-1: evidence-group voting policy.

`OPTIONS_IV` is derived from the SAME option-chain snapshot as `OPTIONS_OI`;
the dependency map declares it `may_vote=False`. Before this sprint the
matrix ignored that declaration, so a directional IV-skew row became a second,
"independent" directional group next to OI evidence.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.options.adversarial_analysis import build_adversarial_analysis
from app.domain.options.decision_engine import build_quality_assessment
from app.domain.options.evidence_dependency import DEPENDENCIES
from app.domain.options.evidence_matrix import (
    EvidenceDirection,
    EvidenceGroup,
    EvidenceMatrix,
    EvidenceRow,
    GroupVerdict,
    OverallConvergence,
    row_iv_skew,
    row_pcr_change,
    withhold_stale_stream_rows,
)
from app.domain.options.iv_context import AtmIvSummary, IvRankResult, IvRankStatus
from app.domain.options.liquidity import LiquidityGrade
from app.domain.options.market_regime import MarketRegime

_BULL = EvidenceDirection.BULLISH
_BEAR = EvidenceDirection.BEARISH


def _oi_bullish_row() -> EvidenceRow:
    """A real directional OI row: PCR rose and put OI grew faster than call OI."""
    return row_pcr_change(
        current_pcr=Decimal("1.30"), previous_pcr=Decimal("1.00"),
        current_call_oi=1_000, previous_call_oi=1_000,
        current_put_oi=1_300, previous_put_oi=1_000,
        meaningful_pcr_change=Decimal("0.05"), meaningful_oi_change_fraction=Decimal("0.02"),
    )


def _iv_skew_row(direction: EvidenceDirection) -> EvidenceRow:
    """A real, fully-qualified directional IV-skew row (both legs tradeable)."""
    skew = Decimal("4") if direction == _BULL else Decimal("-4")
    summary = AtmIvSummary(
        atm_strike=Decimal("24800"), atm_ce_iv=Decimal("16"), atm_pe_iv=Decimal("12"),
        chain_iv=Decimal("14"), ce_pe_skew=skew,
    )
    return row_iv_skew(
        summary, meaningful_skew=Decimal("1.0"),
        ce_liquidity_grade=LiquidityGrade.EXCELLENT, pe_liquidity_grade=LiquidityGrade.GOOD,
    )


def _row(group: EvidenceGroup, direction: EvidenceDirection) -> EvidenceRow:
    return EvidenceRow(f"{group.value}-row", group, direction, "")


# -- the exact M-1 defect -----------------------------------------------------


def test_same_snapshot_oi_and_iv_skew_are_not_two_independent_supporting_groups() -> None:
    oi = _oi_bullish_row()
    iv = _iv_skew_row(_BULL)
    # Preconditions: both real row builders genuinely emit a directional vote.
    assert oi.group == EvidenceGroup.OPTIONS_OI and oi.direction == _BULL
    assert iv.group == EvidenceGroup.OPTIONS_IV and iv.direction == _BULL

    matrix = EvidenceMatrix(rows=[oi, iv])

    assert matrix.supporting_group_count(_BULL) == 1
    assert matrix.group_verdict(EvidenceGroup.OPTIONS_IV) == GroupVerdict.NON_DIRECTIONAL
    assert matrix.group_verdict(EvidenceGroup.OPTIONS_OI) == GroupVerdict.BULLISH


def test_same_snapshot_oi_and_iv_skew_do_not_inflate_decision_engine_evidence_count() -> None:
    matrix = EvidenceMatrix(rows=[_oi_bullish_row(), _iv_skew_row(_BULL)])
    assessment = build_quality_assessment(
        matrix=matrix, regime=MarketRegime.TRENDING_BULLISH, candidates=[], chain_issues=[],
        underlying_data_state_is_ok=True,
        iv_rank=IvRankResult(status=IvRankStatus.UNAVAILABLE, value=None, observations_used=0, detail=""),
    )
    assert assessment.supporting_evidence_count == 1


def test_iv_skew_opposing_oi_does_not_manufacture_a_conflict() -> None:
    matrix = EvidenceMatrix(rows=[_oi_bullish_row(), _iv_skew_row(_BEAR)])
    assert matrix.overall_convergence() == OverallConvergence.CONVERGENCE_BULLISH
    assert matrix.supporting_group_count(_BEAR) == 0


def test_iv_skew_alone_cannot_produce_directional_convergence() -> None:
    matrix = EvidenceMatrix(rows=[_iv_skew_row(_BULL)])
    assert matrix.overall_convergence() == OverallConvergence.INSUFFICIENT_EVIDENCE
    assert matrix.supporting_group_count(_BULL) == 0


def test_non_voting_rows_are_excluded_from_raw_supporting_count_and_adversarial_cases() -> None:
    matrix = EvidenceMatrix(rows=[_oi_bullish_row(), _iv_skew_row(_BEAR)])
    assert matrix.supporting_count(_BEAR) == 0
    analysis = build_adversarial_analysis(matrix)
    assert analysis.bear_case == []
    assert len(analysis.bull_case) == 1


# -- legitimate behavior that must keep working ------------------------------


@pytest.mark.parametrize(
    "voting_group",
    [
        EvidenceGroup.UNDERLYING_PRICE_STRUCTURE,
        EvidenceGroup.FUTURES,
        EvidenceGroup.GLOBAL,
        EvidenceGroup.RELATIVE_STRENGTH,
    ],
)
def test_genuinely_independent_groups_still_count_as_independent_with_oi(voting_group: EvidenceGroup) -> None:
    matrix = EvidenceMatrix(rows=[_oi_bullish_row(), _row(voting_group, _BULL)])
    assert matrix.supporting_group_count(_BULL) == 2
    assert matrix.overall_convergence() == OverallConvergence.CONVERGENCE_BULLISH


def test_independent_group_disagreeing_with_oi_is_still_a_conflict() -> None:
    matrix = EvidenceMatrix(rows=[_oi_bullish_row(), _row(EvidenceGroup.FUTURES, _BEAR)])
    assert matrix.overall_convergence() == OverallConvergence.CONFLICT


def test_oi_rows_conflicting_within_their_own_group_still_conflict() -> None:
    matrix = EvidenceMatrix(rows=[_row(EvidenceGroup.OPTIONS_OI, _BULL), _row(EvidenceGroup.OPTIONS_OI, _BEAR)])
    assert matrix.group_verdict(EvidenceGroup.OPTIONS_OI) == GroupVerdict.CONFLICTING
    assert matrix.overall_convergence() == OverallConvergence.CONFLICT


def test_iv_skew_row_itself_still_reports_its_observation() -> None:
    """The observation stays visible; only its VOTE is removed."""
    assert _iv_skew_row(_BULL).direction == _BULL
    assert _iv_skew_row(_BEAR).direction == _BEAR


def test_unknown_and_stale_chain_semantics_are_preserved() -> None:
    rows = withhold_stale_stream_rows(
        [_oi_bullish_row(), _iv_skew_row(_BULL)],
        chain_is_current=False, futures_are_current=True, quote_is_current=True,
    )
    assert all(r.direction == EvidenceDirection.UNKNOWN for r in rows)
    matrix = EvidenceMatrix(rows=rows)
    assert matrix.overall_convergence() == OverallConvergence.INSUFFICIENT_EVIDENCE
    assert matrix.group_verdict(EvidenceGroup.OPTIONS_IV) == GroupVerdict.NON_DIRECTIONAL


# -- one source of truth ------------------------------------------------------


@pytest.mark.parametrize("group", list(EvidenceGroup))
def test_matrix_enforces_the_dependency_map_may_vote_flag_for_every_group(group: EvidenceGroup) -> None:
    from app.domain.options.evidence_matrix import group_may_vote

    dependency = next(d for d in DEPENDENCIES if d.evidence_group == group)
    assert dependency.may_vote is group_may_vote(group)

    verdict = EvidenceMatrix(rows=[_row(group, _BULL)]).group_verdict(group)
    assert verdict == (GroupVerdict.BULLISH if dependency.may_vote else GroupVerdict.NON_DIRECTIONAL)


def test_every_evidence_group_has_exactly_one_dependency_entry() -> None:
    declared = [d.evidence_group for d in DEPENDENCIES if d.evidence_group is not None]
    assert sorted(g.value for g in declared) == sorted(g.value for g in EvidenceGroup)
