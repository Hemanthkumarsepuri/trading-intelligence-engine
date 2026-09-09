"""IPO Intelligence -- shareholder quota confirmation. The core invariant
under test: owning a parent company must never, by itself, produce a
CONFIRMED quota."""

from __future__ import annotations

from datetime import date

from app.domain.ipo.models import ShareholderQuotaStatus, UserEligibility
from app.domain.ipo.shareholder_quota import (
    build_shareholder_quota_info,
    determine_user_eligibility,
    has_real_shareholder_reservation,
)


def test_has_real_shareholder_reservation_true_when_sha_present() -> None:
    assert has_real_shareholder_reservation(["IND", "EMP", "SHA"]) is True


def test_has_real_shareholder_reservation_false_when_absent() -> None:
    assert has_real_shareholder_reservation(["IND", "EMP", "HNI"]) is False


def test_has_real_shareholder_reservation_false_for_empty_list() -> None:
    assert has_real_shareholder_reservation([]) is False


def test_defaults_to_not_confirmed_even_with_a_plausible_parent_company_named() -> None:
    """The exact scenario the milestone calls out by name: 'do Reliance
    shareholders get a Jio quota' must NOT default to CONFIRMED just
    because a parent/subsidiary relationship is named."""
    quota = build_shareholder_quota_info(
        confirmed_by_offer_document=False, eligibility_company="Jio Platforms", parent_company="Reliance Industries",
    )
    assert quota.status == ShareholderQuotaStatus.NOT_CONFIRMED
    assert quota.record_date is None
    assert quota.reserved_shares is None
    assert "NOT CONFIRMED" in quota.detail
    assert "does NOT automatically" in quota.detail


def test_confirmed_only_when_explicitly_backed_by_the_offer_document() -> None:
    quota = build_shareholder_quota_info(
        confirmed_by_offer_document=True, eligibility_company="Example Corp", parent_company="Example Holdings",
        record_date=date(2026, 9, 1), minimum_holding_required=1, reserved_shares=500000,
        maximum_application_shares=2000, both_retail_and_shareholder_allowed=True, source="Example Corp RHP, page 42",
    )
    assert quota.status == ShareholderQuotaStatus.CONFIRMED
    assert quota.record_date == date(2026, 9, 1)
    assert quota.reserved_shares == 500000
    assert quota.source == "Example Corp RHP, page 42"


def test_confirmed_flag_alone_without_other_fields_still_yields_confirmed_status() -> None:
    """`confirmed_by_offer_document=True` with no other details still
    marks CONFIRMED (the caller vouched for it) -- but every other field
    honestly stays whatever was supplied (None if omitted), never
    invented to fill the gaps."""
    quota = build_shareholder_quota_info(confirmed_by_offer_document=True)
    assert quota.status == ShareholderQuotaStatus.CONFIRMED
    assert quota.record_date is None
    assert quota.reserved_shares is None


def test_eligibility_is_unknown_when_quota_not_confirmed() -> None:
    quota = build_shareholder_quota_info(confirmed_by_offer_document=False)
    assert determine_user_eligibility(quota, user_holds_shares_as_of_record_date=True) == UserEligibility.UNKNOWN


def test_eligibility_is_unknown_when_holding_status_unknown() -> None:
    quota = build_shareholder_quota_info(confirmed_by_offer_document=True, record_date=date(2026, 9, 1))
    assert determine_user_eligibility(quota, user_holds_shares_as_of_record_date=None) == UserEligibility.UNKNOWN


def test_eligibility_yes_when_confirmed_and_user_held_shares() -> None:
    quota = build_shareholder_quota_info(confirmed_by_offer_document=True, record_date=date(2026, 9, 1))
    assert determine_user_eligibility(quota, user_holds_shares_as_of_record_date=True) == UserEligibility.YES


def test_eligibility_no_when_confirmed_but_user_did_not_hold_shares() -> None:
    quota = build_shareholder_quota_info(confirmed_by_offer_document=True, record_date=date(2026, 9, 1))
    assert determine_user_eligibility(quota, user_holds_shares_as_of_record_date=False) == UserEligibility.NO
