"""SAFETY_LOCK.txt invariant -- every broker HTTP path a provider adapter
can call is a READ-ONLY market-data path.

`test_broker_execution_lock.py` proves no order ROUTE exists in this app.
This proves the other side: no provider adapter builds a request to a
broker order, portfolio, funds or account-mutation endpoint. It reads the
adapters' source (AST), so a newly added path literal fails here until it
is consciously added to the allowlist below.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_PROVIDER_DIR = Path(__file__).resolve().parents[2] / "app" / "data" / "providers"

# Every broker path prefix a provider may request. Market data only.
_ALLOWED_PREFIXES = (
    # Upstox
    "/v2/market-quote/",
    "/v2/market/status/",
    "/v2/option/chain",
    "/v2/option/contract",
    "/v3/historical-candle/",
    "/v2/news",
    "/v2/ipos",
    # DhanHQ v2 (adapter present, not constructed by the dashboard)
    "/v2/marketfeed/",
    "/optionchain",
    "/charts/historical",
    "/charts/intraday",
)
_FORBIDDEN_FRAGMENTS = ("order", "portfolio", "fund", "margin", "gtt", "position", "holding", "trade", "charges")


def _leading_text(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values and isinstance(node.values[0], ast.Constant):
        return str(node.values[0].value)
    return None


def _request_path_literals(source: str) -> list[str]:
    """Leading constant text of the path argument of every `_get(...)` /
    `_post(...)` call. A path passed as a local variable is resolved to
    EVERY string assigned to that name in the enclosing function (branches
    included); anything else is reported as unresolvable and fails."""
    paths: list[str] = []
    for function in ast.walk(ast.parse(source)):
        if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        assigned: dict[str, list[str | None]] = {}
        for node in ast.walk(function):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assigned.setdefault(target.id, []).append(_leading_text(node.value))
        for node in ast.walk(function):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"_get", "_post"}):
                continue
            if not node.args:
                continue
            first = node.args[0]
            candidates = assigned.get(first.id, [None]) if isinstance(first, ast.Name) else [_leading_text(first)]
            paths.extend(c if c is not None else f"<unresolvable path at line {node.lineno}>" for c in candidates)
    return sorted(set(paths))


def _all_paths() -> list[tuple[str, str]]:
    return [
        (module.name, path)
        for module in sorted(_PROVIDER_DIR.glob("*.py"))
        for path in _request_path_literals(module.read_text(encoding="utf-8"))
    ]


def test_provider_adapters_request_at_least_the_known_market_data_paths() -> None:
    # Guards the guard: an AST walk that silently found nothing would pass
    # every assertion below vacuously.
    modules = {module for module, _ in _all_paths()}
    assert {"upstox_provider.py", "dhan_provider.py"} <= modules


@pytest.mark.parametrize(("module", "path"), _all_paths())
def test_every_provider_request_path_is_an_allowlisted_read_only_path(module: str, path: str) -> None:
    assert path.startswith(_ALLOWED_PREFIXES), f"{module}: {path!r} is not an allowlisted market-data path"
    lowered = path.lower()
    assert not any(fragment in lowered for fragment in _FORBIDDEN_FRAGMENTS), f"{module}: {path!r}"
