"""IPO universe (real pagination across ALL pages of a status bucket)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx

from app.data.providers.upstox_provider import UpstoxProvider
from app.orchestration.ipo_universe import fetch_ipo_universe


def _provider(handler: Callable[[httpx.Request], httpx.Response]) -> UpstoxProvider:
    return UpstoxProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), access_token="tok")


def _row(ipo_id: str, name: str) -> dict[str, object]:
    return {
        "id": ipo_id, "symbol": None, "name": name, "status": "open", "isin": None, "issue_type": "regular",
        "issue_size": None, "industry": None, "minimum_price": None, "maximum_price": None,
        "bidding_start_date": None, "bidding_end_date": None, "total_subscription": None,
    }


def test_fetches_every_page_not_just_page_1() -> None:
    pages = {
        1: [_row("a", "A"), _row("b", "B")],
        2: [_row("c", "C")],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        page_number = int(request.url.params.get("page_number", "1"))
        rows = pages.get(page_number, [])
        return httpx.Response(200, json={
            "status": "success", "data": rows,
            "meta_data": {"page": {"page_number": page_number, "total_pages": 2, "records": len(rows), "total_records": 3}},
        })

    provider = _provider(handler)
    outcome = asyncio.run(fetch_ipo_universe(provider=provider, statuses=("open",)))
    assert len(outcome.entries) == 3
    assert outcome.pages_fetched["open"] == 2
    assert outcome.total_records["open"] == 3
    assert outcome.errors == []


def test_deduplicates_by_ipo_id_keeping_first_occurrence() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        status = request.url.params["status"]
        rows = [_row("dup-id", f"{status.upper()} NAME")]
        return httpx.Response(200, json={
            "status": "success", "data": rows,
            "meta_data": {"page": {"page_number": 1, "total_pages": 1, "records": 1, "total_records": 1}},
        })

    provider = _provider(handler)
    outcome = asyncio.run(fetch_ipo_universe(provider=provider, statuses=("listed", "closed")))
    assert len(outcome.entries) == 1  # not 2 -- same real ipo_id deduplicated
    assert outcome.entries[0].status == "listed"  # first status in the order given wins


def test_per_status_error_isolated_other_statuses_still_fetched() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        status = request.url.params["status"]
        if status == "upcoming":
            return httpx.Response(500, json={"status": "error"})
        rows = [_row("ok-id", "OK")]
        return httpx.Response(200, json={
            "status": "success", "data": rows,
            "meta_data": {"page": {"page_number": 1, "total_pages": 1, "records": 1, "total_records": 1}},
        })

    provider = _provider(handler)
    outcome = asyncio.run(fetch_ipo_universe(provider=provider, statuses=("upcoming", "open")))
    assert len(outcome.entries) == 1
    assert outcome.entries[0].status == "open"
    assert any("upcoming" in e for e in outcome.errors)


def test_max_pages_cap_is_reported_never_silently_truncated() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        page_number = int(request.url.params.get("page_number", "1"))
        return httpx.Response(200, json={
            "status": "success", "data": [_row(f"id-{page_number}", f"N{page_number}")],
            "meta_data": {"page": {"page_number": page_number, "total_pages": 50, "records": 1, "total_records": 50}},
        })

    provider = _provider(handler)
    outcome = asyncio.run(fetch_ipo_universe(provider=provider, statuses=("open",), max_pages_per_status=3))
    assert len(outcome.entries) == 3
    assert any("max_pages_per_status" in e for e in outcome.errors)
