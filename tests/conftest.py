"""Shared fixtures for the test suite."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from app.data.providers.request_budget import UPSTOX_REQUEST_LEDGER


@pytest.fixture(autouse=True)
def _isolate_upstox_request_ledger() -> Iterator[None]:
    """Mock-transport tests drive the real `UpstoxProvider`, which records
    every request in the process-wide rate-budget ledger. Without this,
    request counts would accumulate across the whole suite and a late test
    could see a budget-deferred scan that depends on test ordering."""
    UPSTOX_REQUEST_LEDGER.reset()
    yield
    UPSTOX_REQUEST_LEDGER.reset()


@pytest.fixture
def fixed_now() -> datetime:
    return datetime(2026, 8, 27, 10, 0, 0, tzinfo=UTC)


@pytest.fixture
def pinned_api_clock(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> datetime:
    """Opt-in (never autouse) pin of the clock the HTTP API tests depend on.

    The API routes read `as_of` from `app.api.main.utc_now`, and the shared
    mock router in `tests/integration/api/test_dashboard_api.py` stamps its
    synthetic candles from that module's own `utc_now`. Both are pinned to
    `PINNED_API_NOW`, the instant the fixture expiry is derived from, plus the
    requesting test module's own `utc_now` import when it has one. Nothing else
    is touched: `app.utils.time.utc_now` itself, and every other module's
    reference to it, keep the real clock. Tests that need a different instant
    still override with their own `monkeypatch.setattr` after this runs."""
    from app.utils import time as app_time
    from tests.integration.api import test_dashboard_api

    pinned = test_dashboard_api.PINNED_API_NOW
    monkeypatch.setattr("app.api.main.utc_now", lambda: pinned)
    monkeypatch.setattr(test_dashboard_api, "utc_now", lambda: pinned)
    if getattr(request.module, "utc_now", None) is app_time.utc_now:
        monkeypatch.setattr(request.module, "utc_now", lambda: pinned)
    return pinned
