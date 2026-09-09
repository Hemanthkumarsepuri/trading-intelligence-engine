"""IPO issue quality + valuation (Sprint 7B, Objectives 9/10).

Same discipline as every other IPO module (`app.domain.ipo.models`,
`app.domain.ipo.comparison`): pure, no I/O, every FACT is caller-supplied
or derived from a caller-supplied `IPOIdentity`/`IPOFundamentals`, never
fetched or invented by this codebase. No authorized fundamentals provider
is wired anywhere in this system (Upstox's real, authorized IPO endpoint
carries no Revenue/EBITDA/PAT/EPS/ROE/ROCE/Debt fields -- see
docs/data-sources/IPO_DATA_SOURCE_DECISION.md) -- `IPOFundamentals` exists
purely as an optional, caller-supplied input for the day such a source
becomes authorized; until then `build_valuation_assessment()` honestly
reports DATA_NOT_AVAILABLE.

=== Objective 9: issue quality ===
`fresh_issue_pct`/`offer_for_sale_pct` are METRICs -- deterministic
percentages of the already-real `IPOIdentity.fresh_issue_crore`/
`offer_for_sale_crore`/`issue_size_crore` fields. `objects_of_issue` is
shown verbatim (the caller-supplied free text) as the use-of-proceeds
evidence; this module performs no keyword categorization of that text
into "debt reduction"/"capex"/"working capital" buckets -- `IPOIdentity`
carries no structured per-category proceeds breakdown, and guessing a
category from free text would be exactly the kind of fabrication this
product forbids. Those three fields are therefore always
DATA_NOT_AVAILABLE today, honestly, rather than approximated.

=== Objective 10: valuation ===
Only `pe_ratio` is computed (`price_band_high / eps`) -- the one ratio
this system's real fields can support without inventing a market-cap
denominator. `ps_ratio`/`ev_ebitda` require post-issue shares outstanding,
which no model in this system carries; they stay `None` with an honest
`detail` explaining why, never approximated from issue size or price band
alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.domain.ipo.models import IPOIdentity

_DATA_NOT_AVAILABLE = "DATA_NOT_AVAILABLE"


@dataclass(frozen=True)
class IssueQualityAssessment:
    """Part 9 -- METRIC section (percentages) + verbatim FACT pass-through
    (objects_of_issue). `debt_reduction_note`/`capex_note`/
    `working_capital_note` are always `DATA_NOT_AVAILABLE` today -- see
    module docstring."""

    fresh_issue_pct: Decimal | None
    offer_for_sale_pct: Decimal | None
    objects_of_issue: list[str]
    debt_reduction_note: str = _DATA_NOT_AVAILABLE
    capex_note: str = _DATA_NOT_AVAILABLE
    working_capital_note: str = _DATA_NOT_AVAILABLE
    detail: str = ""


def build_issue_quality_assessment(identity: IPOIdentity) -> IssueQualityAssessment:
    fresh_pct: Decimal | None = None
    ofs_pct: Decimal | None = None
    total = identity.issue_size_crore
    if total is not None and total != 0:
        if identity.fresh_issue_crore is not None:
            fresh_pct = (identity.fresh_issue_crore / total) * Decimal(100)
        if identity.offer_for_sale_crore is not None:
            ofs_pct = (identity.offer_for_sale_crore / total) * Decimal(100)

    if not identity.objects_of_issue:
        detail = "No objects-of-issue / use-of-proceeds text was supplied for this IPO -- DATA_NOT_AVAILABLE."
    else:
        detail = (
            "Use-of-proceeds shown is the caller-supplied objects_of_issue text verbatim; this system performs "
            "no keyword categorization of that text into debt-reduction/capex/working-capital buckets."
        )
    return IssueQualityAssessment(
        fresh_issue_pct=fresh_pct, offer_for_sale_pct=ofs_pct, objects_of_issue=list(identity.objects_of_issue), detail=detail,
    )


@dataclass(frozen=True)
class IPOFundamentals:
    """FACT section -- every field is caller-supplied only. No authorized
    fundamentals provider exists in this system; a caller with no
    fundamentals data simply omits this entirely, and
    `build_valuation_assessment()` reports DATA_NOT_AVAILABLE."""

    revenue_crore: Decimal | None = None
    ebitda_crore: Decimal | None = None
    pat_crore: Decimal | None = None
    eps: Decimal | None = None
    roe_pct: Decimal | None = None
    roce_pct: Decimal | None = None
    total_debt_crore: Decimal | None = None
    source: str | None = None
    as_of: date | None = None


@dataclass(frozen=True)
class IPOValuationAssessment:
    """Part 10. `availability` is `"DATA_NOT_AVAILABLE"` whenever no
    fundamentals were supplied at all; otherwise the individual FACT/
    METRIC fields below are each independently `None` when their own
    inputs are missing -- never backfilled from one another."""

    availability: str
    revenue_crore: Decimal | None
    ebitda_crore: Decimal | None
    pat_crore: Decimal | None
    eps: Decimal | None
    pe_ratio: Decimal | None
    ps_ratio: Decimal | None
    ev_ebitda: Decimal | None
    roe_pct: Decimal | None
    roce_pct: Decimal | None
    total_debt_crore: Decimal | None
    source: str | None
    detail: str
    notes: list[str] = field(default_factory=list)


def build_valuation_assessment(identity: IPOIdentity, fundamentals: IPOFundamentals | None) -> IPOValuationAssessment:
    if fundamentals is None or all(
        v is None for v in (fundamentals.revenue_crore, fundamentals.ebitda_crore, fundamentals.pat_crore, fundamentals.eps)
    ):
        return IPOValuationAssessment(
            availability=_DATA_NOT_AVAILABLE, revenue_crore=None, ebitda_crore=None, pat_crore=None, eps=None,
            pe_ratio=None, ps_ratio=None, ev_ebitda=None, roe_pct=None, roce_pct=None, total_debt_crore=None, source=None,
            detail="VALUATION ANALYSIS: DATA_NOT_AVAILABLE -- no authorized fundamentals source is wired in this system, and no fundamentals were supplied for this IPO.",
        )

    notes: list[str] = []
    pe_ratio: Decimal | None = None
    price = identity.price_band_high
    if price is not None and fundamentals.eps is not None and fundamentals.eps != 0:
        pe_ratio = price / fundamentals.eps
    else:
        notes.append("P/E not computed -- requires both a price band and a real EPS.")

    notes.append("P/S not computed -- requires post-issue shares outstanding, which no model in this system carries.")
    notes.append("EV/EBITDA not computed -- requires market capitalisation, which no model in this system carries.")

    return IPOValuationAssessment(
        availability="PARTIALLY_SUPPORTED", revenue_crore=fundamentals.revenue_crore, ebitda_crore=fundamentals.ebitda_crore,
        pat_crore=fundamentals.pat_crore, eps=fundamentals.eps, pe_ratio=pe_ratio, ps_ratio=None, ev_ebitda=None,
        roe_pct=fundamentals.roe_pct, roce_pct=fundamentals.roce_pct, total_debt_crore=fundamentals.total_debt_crore,
        source=fundamentals.source,
        detail="Valuation figures are caller-supplied fundamentals, not fetched by this system. Ratios requiring shares outstanding/market cap are honestly omitted rather than approximated.",
        notes=notes,
    )
