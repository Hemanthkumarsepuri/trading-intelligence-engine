"""Guards for the test-only `pinned_api_clock` fixture (tests/conftest.py).

Nineteen API tests once passed only until the fixture expiry in
test_dashboard_api.py (2026-09-24) went by on the wall clock. These tests keep
that class of failure from returning without hiding real behavior: the pin
must be a real open NSE session before the fixture expiry, it must reach only
the clocks it names, and a test module that pairs a fixed expiry with a live
`utc_now()` call must opt in to the pin.
"""

from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path

import pytest

import app.api.main as api_main
from app.domain.market.trading_calendar import classify_session_window
from app.utils import time as app_time
from tests.integration.api import test_dashboard_api
from tests.integration.api.test_dashboard_api import EXPIRY, PINNED_API_NOW

_TESTS_ROOT = Path(__file__).resolve().parents[2]


def test_pinned_instant_is_an_open_session_strictly_before_the_fixture_expiry() -> None:
    window = classify_session_window(PINNED_API_NOW)
    assert window.session_window == "OPEN"
    assert window.research_session_mode == "LIVE"
    assert PINNED_API_NOW.date() < EXPIRY


def test_pin_reaches_only_the_route_clock_and_the_mock_router_clock(pinned_api_clock: datetime) -> None:
    assert api_main.utc_now() == pinned_api_clock
    assert test_dashboard_api.utc_now() == pinned_api_clock
    # The application's own time source is never replaced.
    assert app_time.utc_now.__module__ == "app.utils.time"
    assert app_time.utc_now.__name__ == "utc_now"
    assert app_time.utc_now() != pinned_api_clock


def test_pin_is_undone_after_the_test() -> None:
    assert api_main.utc_now is app_time.utc_now
    assert test_dashboard_api.utc_now is app_time.utc_now


def _expiry_and_live_clock(tree: ast.Module) -> bool:
    fixed_expiry = False
    live_clock = False
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in {"EXPIRY", "_EXPIRY_MS"} for t in node.targets)
        ) or (isinstance(node, ast.ImportFrom) and any(a.name in {"EXPIRY", "_EXPIRY_MS"} for a in node.names)):
            fixed_expiry = True
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "utc_now":
            live_clock = True
    return fixed_expiry and live_clock


def _modules_pairing_fixed_expiry_with_live_clock() -> list[Path]:
    return [
        path for path in sorted(_TESTS_ROOT.rglob("test_*.py"))
        if _expiry_and_live_clock(ast.parse(path.read_text(encoding="utf-8")))
    ]


def test_the_guard_still_finds_the_modules_it_was_written_for() -> None:
    names = {p.name for p in _modules_pairing_fixed_expiry_with_live_clock()}
    assert {"test_dashboard_api.py", "test_daily_research_api.py"} <= names


def _opts_in_to_the_pin(tree: ast.Module) -> bool:
    """A real opt-in in code -- `usefixtures("pinned_api_clock")` or a test
    parameter of that name. Comments and docstring prose do not count."""
    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and node.arg == "pinned_api_clock":
            return True
        if (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "usefixtures"
            and any(isinstance(a, ast.Constant) and a.value == "pinned_api_clock" for a in node.args)
        ):
            return True
    return False


@pytest.mark.parametrize("path", _modules_pairing_fixed_expiry_with_live_clock(), ids=lambda p: p.name)
def test_a_module_pairing_a_fixed_expiry_with_a_live_clock_opts_in_to_the_pin(path: Path) -> None:
    assert _opts_in_to_the_pin(ast.parse(path.read_text(encoding="utf-8"))), (
        f"{path.name} combines a hard-coded expiry with a live utc_now() call; "
        "use the `pinned_api_clock` fixture so the scenario does not expire with the calendar"
    )
