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
