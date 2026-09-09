"""Shared fixtures for the test suite."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest


@pytest.fixture
def fixed_now() -> datetime:
    return datetime(2026, 8, 27, 10, 0, 0, tzinfo=UTC)
