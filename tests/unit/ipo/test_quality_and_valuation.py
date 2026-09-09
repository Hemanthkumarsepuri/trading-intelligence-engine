"""Sprint 7B, Objectives 9/10 -- pure unit tests for IPO issue quality
and valuation."""

from __future__ import annotations

from decimal import Decimal

from app.domain.ipo.models import IPOIdentity
from app.domain.ipo.quality_and_valuation import (
    IPOFundamentals,
    build_issue_quality_assessment,
    build_valuation_assessment,
)


def _identity(**overrides: object) -> IPOIdentity:
    base: dict[str, object] = {"company_name": "Example Ltd"}
    base.update(overrides)
    return IPOIdentity(**base)  # type: ignore[arg-type]


def test_issue_quality_computes_fresh_and_ofs_pct_from_real_fields() -> None:
    identity = _identity(issue_size_crore=Decimal("1000"), fresh_issue_crore=Decimal("600"), offer_for_sale_crore=Decimal("400"))
    result = build_issue_quality_assessment(identity)
    assert result.fresh_issue_pct == Decimal("60")
    assert result.offer_for_sale_pct == Decimal("40")


def test_issue_quality_none_when_issue_size_missing() -> None:
    identity = _identity(fresh_issue_crore=Decimal("600"))
    result = build_issue_quality_assessment(identity)
    assert result.fresh_issue_pct is None
    assert result.offer_for_sale_pct is None


def test_issue_quality_debt_capex_working_capital_always_data_not_available() -> None:
    identity = _identity(objects_of_issue=["Repayment of borrowings", "General corporate purposes"])
    result = build_issue_quality_assessment(identity)
    assert result.debt_reduction_note == "DATA_NOT_AVAILABLE"
    assert result.capex_note == "DATA_NOT_AVAILABLE"
    assert result.working_capital_note == "DATA_NOT_AVAILABLE"
    assert result.objects_of_issue == ["Repayment of borrowings", "General corporate purposes"]


def test_issue_quality_objects_of_issue_empty_is_honest() -> None:
    result = build_issue_quality_assessment(_identity())
    assert result.objects_of_issue == []
    assert "DATA_NOT_AVAILABLE" in result.detail


def test_valuation_data_not_available_when_no_fundamentals_supplied() -> None:
    result = build_valuation_assessment(_identity(), None)
    assert result.availability == "DATA_NOT_AVAILABLE"
    assert result.detail.startswith("VALUATION ANALYSIS: DATA_NOT_AVAILABLE")
    assert result.pe_ratio is None


def test_valuation_data_not_available_when_fundamentals_object_is_empty() -> None:
    result = build_valuation_assessment(_identity(), IPOFundamentals())
    assert result.availability == "DATA_NOT_AVAILABLE"


def test_valuation_computes_pe_ratio_from_real_price_band_and_eps() -> None:
    identity = _identity(price_band_high=Decimal("100"))
    fundamentals = IPOFundamentals(eps=Decimal("5"), revenue_crore=Decimal("500"), source="caller")
    result = build_valuation_assessment(identity, fundamentals)
    assert result.availability == "PARTIALLY_SUPPORTED"
    assert result.pe_ratio == Decimal("20")
    assert result.revenue_crore == Decimal("500")


def test_valuation_never_fabricates_ps_or_ev_ebitda() -> None:
    identity = _identity(price_band_high=Decimal("100"))
    fundamentals = IPOFundamentals(eps=Decimal("5"), revenue_crore=Decimal("500"))
    result = build_valuation_assessment(identity, fundamentals)
    assert result.ps_ratio is None
    assert result.ev_ebitda is None
    assert any("shares outstanding" in n or "market capitalisation" in n for n in result.notes)


def test_valuation_pe_none_when_eps_present_but_no_price_band() -> None:
    fundamentals = IPOFundamentals(eps=Decimal("5"), revenue_crore=Decimal("500"))
    result = build_valuation_assessment(_identity(), fundamentals)
    assert result.pe_ratio is None
