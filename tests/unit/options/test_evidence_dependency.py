from __future__ import annotations

from app.domain.options.evidence_dependency import (
    DEPENDENCIES,
    cash_context_may_vote,
    options_oi_row_names_are_one_group,
)
from app.domain.options.evidence_matrix import EvidenceGroup


def test_options_oi_cluster_is_one_independent_group() -> None:
    names = options_oi_row_names_are_one_group()
    assert "Volume" in names
    assert "Call/Put OI structure" in names
    oi_deps = [d for d in DEPENDENCIES if d.evidence_group == EvidenceGroup.OPTIONS_OI]
    assert len(oi_deps) == 1
    assert all(name in oi_deps[0].correlated_with for name in ("Volume", "Support", "Resistance"))


def test_cash_context_cannot_vote() -> None:
    assert cash_context_may_vote() is False
    cash = [d for d in DEPENDENCIES if d.evidence_group is None]
    assert cash and cash[0].may_vote is False


def test_iv_and_liquidity_are_non_directional() -> None:
    for group in (EvidenceGroup.OPTIONS_IV, EvidenceGroup.LIQUIDITY, EvidenceGroup.NEWS_EVENT):
        dep = next(d for d in DEPENDENCIES if d.evidence_group == group)
        assert dep.directional is False
        assert dep.may_vote is False
