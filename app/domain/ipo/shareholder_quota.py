"""Shareholder-quota confirmation logic (Part 9) — structurally defaults
to NOT_CONFIRMED. This module never infers a quota from "the user owns
the parent company" or any other proxy; it becomes CONFIRMED only when
the caller explicitly asserts it is backed by the actual offer document.
"""

from __future__ import annotations

from datetime import date

from app.domain.ipo.models import ShareholderQuotaInfo, ShareholderQuotaStatus, UserEligibility

_SHAREHOLDER_CATEGORY_CODE = "SHA"


def has_real_shareholder_reservation(investor_categories: list[str]) -> bool:
    """Part 9/11 -- Upstox's real, authorized `/v2/ipos/{id}` response
    (see docs/data-sources/IPO_DATA_SOURCE_DECISION.md) lists which
    investor-reservation CATEGORY CODES an IPO genuinely has; `"SHA"`
    means a shareholder reservation category exists. This is a real,
    sourced signal that the CATEGORY exists -- it does NOT supply the
    record date, minimum holding, or reserved-share count (those remain
    only in the actual offer document). A caller with a real
    `investor_categories` list from Upstox may legitimately pass the
    result of this function as `confirmed_by_offer_document` to
    `build_shareholder_quota_info()`; a caller with no such list must
    not.
    """
    return _SHAREHOLDER_CATEGORY_CODE in investor_categories


def build_shareholder_quota_info(
    *,
    confirmed_by_offer_document: bool,
    eligibility_company: str | None = None,
    parent_company: str | None = None,
    record_date: date | None = None,
    minimum_holding_required: int | None = None,
    reserved_shares: int | None = None,
    maximum_application_shares: int | None = None,
    both_retail_and_shareholder_allowed: bool | None = None,
    source: str | None = None,
) -> ShareholderQuotaInfo:
    """`confirmed_by_offer_document` is the ONLY thing that can produce
    `CONFIRMED` -- a caller who merely suspects a quota exists (e.g.
    "Reliance shareholders might get a Jio quota") must pass `False`,
    which yields `NOT_CONFIRMED` regardless of what other fields are
    supplied. This makes over-claiming a quota structurally impossible
    from this function's own contract, not merely a documentation
    convention.
    """
    if not confirmed_by_offer_document:
        return ShareholderQuotaInfo(
            status=ShareholderQuotaStatus.NOT_CONFIRMED,
            eligibility_company=eligibility_company, parent_company=parent_company, record_date=None,
            minimum_holding_required=None, reserved_shares=None, maximum_application_shares=None,
            both_retail_and_shareholder_allowed=None, source=source,
            detail="SHAREHOLDER QUOTA: NOT CONFIRMED -- no offer-document/official-source confirmation supplied. "
                   "Owning the parent company does NOT automatically grant a reservation; do not assume one exists.",
        )
    return ShareholderQuotaInfo(
        status=ShareholderQuotaStatus.CONFIRMED,
        eligibility_company=eligibility_company, parent_company=parent_company, record_date=record_date,
        minimum_holding_required=minimum_holding_required, reserved_shares=reserved_shares,
        maximum_application_shares=maximum_application_shares,
        both_retail_and_shareholder_allowed=both_retail_and_shareholder_allowed, source=source,
        detail="SHAREHOLDER QUOTA: CONFIRMED by the supplied offer-document/official source.",
    )


def determine_user_eligibility(
    quota: ShareholderQuotaInfo, *, user_holds_shares_as_of_record_date: bool | None
) -> UserEligibility:
    """`UNKNOWN` whenever the quota itself isn't confirmed, or the
    caller doesn't know their own holding status as of the record date --
    never defaults to YES."""
    if quota.status != ShareholderQuotaStatus.CONFIRMED:
        return UserEligibility.UNKNOWN
    if user_holds_shares_as_of_record_date is None:
        return UserEligibility.UNKNOWN
    return UserEligibility.YES if user_holds_shares_as_of_record_date else UserEligibility.NO
