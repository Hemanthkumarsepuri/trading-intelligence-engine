"""IPO Intelligence -- live-lookup orchestration against a mocked
Upstox transport (same `httpx.MockTransport` pattern as
`test_upstox_provider.py`). No real network calls in this file."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

import httpx

from app.data.providers.upstox_provider import UpstoxProvider
from app.domain.ipo.models import ShareholderQuotaStatus
from app.domain.ipo.query_parser import IPOIntentKind
from app.orchestration.ipo_intelligence import (
    analyze_ipo_query_live,
    discover_ipos,
    fetch_ipo_detail,
    find_ipo_by_hint,
)

_NOW = datetime(2026, 8, 30, 10, 0, tzinfo=UTC)

_LISTING_ROW = {
    "id": "tempsens-instruments-india-limited-ipo", "symbol": "TEMPSENS", "name": "Tempsens Instruments (India) IPO",
    "status": "listed", "isin": "INE1KZI01025", "issue_type": "regular", "issue_size": 650.0, "industry": "Engineering",
    "minimum_price": 285.0, "maximum_price": 300.0, "bidding_start_date": "2026-08-20", "bidding_end_date": "2026-08-24",
    "total_subscription": "213.92",
}

_DETAIL_BODY = {
    **_LISTING_ROW, "face_value": 4.0, "tick_size": None, "lot_size": 50, "minimum_quantity": 50,
    "cut_off_price": 300.0, "listing_price": 634.0, "listing_exchange": "BSE,NSE",
    "rhp_url": "https://example.com/rhp.pdf", "drhp_url": None,
    "timeline": {"listing_date": "2026-08-28", "allotment_date": "2026-08-27"},
    "registrar_info": {"registrar": "K FINTECH"},
    "investors": [{"category": "IND", "description": None}, {"category": "SHA", "description": None}],
}


def _provider(handler: Callable[[httpx.Request], httpx.Response]) -> UpstoxProvider:
    return UpstoxProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), access_token="tok")


def _fake_ipos_handler(*, list_status_to_rows: dict[str, list[dict[str, object]]]) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/ipos":
            status = request.url.params.get("status", "open")
            rows = list_status_to_rows.get(status, [])
            return httpx.Response(200, json={
                "status": "success", "data": rows,
                "meta_data": {"page": {"page_number": 1, "total_pages": 1, "records": len(rows), "total_records": len(rows)}},
            })
        if request.url.path.startswith("/v2/ipos/"):
            return httpx.Response(200, json={"status": "success", "data": _DETAIL_BODY})
        raise AssertionError(f"unexpected path {request.url.path}")

    return handler


def test_discover_ipos_returns_real_shaped_identities() -> None:
    provider = _provider(_fake_ipos_handler(list_status_to_rows={"open": [_LISTING_ROW]}))
    identities, page = asyncio.run(discover_ipos(provider=provider, status="open"))
    assert len(identities) == 1
    assert identities[0].company_name == "Tempsens Instruments (India) IPO"
    assert page.total_records == 1


def test_fetch_ipo_detail_includes_investor_categories_and_listing_price() -> None:
    provider = _provider(_fake_ipos_handler(list_status_to_rows={}))
    identity = asyncio.run(fetch_ipo_detail(provider=provider, ipo_id="tempsens-instruments-india-limited-ipo"))
    assert identity.listing_price is not None
    assert "SHA" in identity.investor_categories


def test_find_ipo_by_hint_matches_symbol() -> None:
    provider = _provider(_fake_ipos_handler(list_status_to_rows={"open": [_LISTING_ROW]}))
    found = asyncio.run(find_ipo_by_hint(provider=provider, hint="TEMPSENS"))
    assert found is not None
    assert found.symbol == "TEMPSENS"


def _fake_paginated_ipos_handler(*, pages: dict[str, list[list[dict[str, object]]]]) -> Callable[[httpx.Request], httpx.Response]:
    """Part 14 regression fixture -- `pages[status]` is a list of PAGES
    (each a list of rows), so a hint matching only page 2+ can be tested
    without duplicating `ipo_universe`'s own pagination logic here."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/ipos":
            status = request.url.params.get("status", "open")
            page_number = int(request.url.params.get("page_number", "1"))
            status_pages = pages.get(status, [[]])
            total_pages = len(status_pages)
            rows = status_pages[page_number - 1] if 1 <= page_number <= total_pages else []
            total_records = sum(len(p) for p in status_pages)
            return httpx.Response(200, json={
                "status": "success", "data": rows,
                "meta_data": {"page": {"page_number": page_number, "total_pages": total_pages, "records": len(rows), "total_records": total_records}},
            })
        if request.url.path.startswith("/v2/ipos/"):
            return httpx.Response(200, json={"status": "success", "data": _DETAIL_BODY})
        raise AssertionError(f"unexpected path {request.url.path}")

    return handler


def test_find_ipo_by_hint_matches_a_later_page() -> None:
    """Part 14 fix: `find_ipo_by_hint` previously only checked page 1 of
    each status bucket. A hint matching only page 2 must now still be
    found -- via the same full-pagination `fetch_ipo_universe()` the
    IPO-universe/comparison scan already uses, not a second pagination
    implementation."""
    other_row = {**_LISTING_ROW, "id": "other-co-ipo", "symbol": "OTHERCO", "name": "Other Co IPO"}
    provider = _provider(_fake_paginated_ipos_handler(
        pages={"open": [[other_row], [_LISTING_ROW]], "upcoming": [[]], "closed": [[]], "listed": [[]]}
    ))
    found = asyncio.run(find_ipo_by_hint(provider=provider, hint="TEMPSENS"))
    assert found is not None
    assert found.symbol == "TEMPSENS"


def test_find_ipo_by_hint_status_filtering_and_dedup_across_statuses() -> None:
    """The same real `ipo_id` legitimately appearing in more than one
    status bucket must resolve once, via the first bucket order checks
    (upcoming, open, closed, listed) -- never a duplicate/ambiguous
    result."""
    provider = _provider(_fake_paginated_ipos_handler(
        pages={"upcoming": [[_LISTING_ROW]], "open": [[_LISTING_ROW]], "closed": [[]], "listed": [[]]}
    ))
    found = asyncio.run(find_ipo_by_hint(provider=provider, hint="TEMPSENS"))
    assert found is not None
    assert found.symbol == "TEMPSENS"


def test_find_ipo_by_hint_returns_none_for_no_real_match() -> None:
    """The exact real-world case confirmed this session: no Jio/Reliance
    IPO exists in the real feed -- this must return None, never a
    fabricated match."""
    provider = _provider(_fake_ipos_handler(list_status_to_rows={"open": [], "upcoming": [], "closed": [], "listed": []}))
    found = asyncio.run(find_ipo_by_hint(provider=provider, hint="JIO"))
    assert found is None


def test_analyze_ipo_query_live_resolves_real_identity_from_hint() -> None:
    provider = _provider(_fake_ipos_handler(list_status_to_rows={"open": [_LISTING_ROW]}))
    result = asyncio.run(analyze_ipo_query_live("Tempsens IPO", as_of=_NOW, provider=provider))
    assert result.data_available is True
    assert result.identity is not None
    assert result.identity.company_name == "Tempsens Instruments (India) IPO"
    assert result.intent.intent == IPOIntentKind.COMPANY_LOOKUP


def test_analyze_ipo_query_live_derives_shareholder_quota_from_real_sha_category() -> None:
    """A real "SHA" investor category confirms the RESERVATION EXISTS --
    this must set CONFIRMED, but must never fabricate record_date/
    reserved_shares (not present in this endpoint)."""
    provider = _provider(_fake_ipos_handler(list_status_to_rows={"open": [_LISTING_ROW]}))
    result = asyncio.run(analyze_ipo_query_live("Tempsens IPO", as_of=_NOW, provider=provider))
    assert result.shareholder_quota is not None
    assert result.shareholder_quota.status == ShareholderQuotaStatus.CONFIRMED
    assert result.shareholder_quota.record_date is None  # genuinely not available from this source
    assert result.shareholder_quota.reserved_shares is None


def test_analyze_ipo_query_live_no_match_falls_back_to_honest_not_available() -> None:
    provider = _provider(_fake_ipos_handler(list_status_to_rows={"open": [], "upcoming": [], "closed": [], "listed": []}))
    result = asyncio.run(analyze_ipo_query_live("Nonexistent Company IPO", as_of=_NOW, provider=provider))
    assert result.data_available is False
    assert result.identity is None


def test_analyze_ipo_query_live_no_match_detail_says_a_real_search_was_attempted() -> None:
    """Regression: previously the detail message said "this function
    performs no lookup itself" even when `analyze_ipo_query_live()` HAD
    genuinely searched the real feed and found nothing -- misleading
    about whether a search actually happened. Discovered via real-data
    validation against the live Upstox API (a real "Jio IPO" query)."""
    provider = _provider(_fake_ipos_handler(list_status_to_rows={"open": [], "upcoming": [], "closed": [], "listed": []}))
    result = asyncio.run(analyze_ipo_query_live("Jio IPO", as_of=_NOW, provider=provider))
    assert result.data_available is False
    assert "searched" in result.detail.lower()
    assert "'JIO'" in result.detail
    assert "performs no lookup itself" not in result.detail


def test_analyze_ipo_query_live_no_provider_keeps_the_original_generic_detail() -> None:
    """When no lookup was even attempted (no provider), the original
    "performs no lookup itself" message remains accurate and unchanged."""
    result = asyncio.run(analyze_ipo_query_live("Jio IPO", as_of=_NOW, provider=None))
    assert "performs no lookup itself" in result.detail


def test_analyze_ipo_query_live_with_no_provider_behaves_like_the_pure_function() -> None:
    result = asyncio.run(analyze_ipo_query_live("KAYNES IPO", as_of=_NOW, provider=None))
    assert result.data_available is False


def test_caller_supplied_identity_always_wins_over_live_lookup() -> None:
    from app.domain.ipo.models import IPOIdentity

    provider = _provider(_fake_ipos_handler(list_status_to_rows={"open": [_LISTING_ROW]}))
    manual_identity = IPOIdentity(company_name="Manually Entered Corp")
    result = asyncio.run(analyze_ipo_query_live("Tempsens IPO", as_of=_NOW, provider=provider, identity=manual_identity))
    assert result.identity is not None
    assert result.identity.company_name == "Manually Entered Corp"
