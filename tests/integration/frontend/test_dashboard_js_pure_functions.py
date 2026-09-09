"""Final Pre-Freeze Product Analytical Pass, Objective 9 -- executable
regression coverage for the dashboard's pure presentation-logic
functions (`humanDuration`, `freshnessState`, `classifyContractState`).

These functions live in a single-file vanilla-JS dashboard with no build
step (`app/api/static/index.html`) and, until now, were only checked for
SYNTAX (`node --check`) and for STRING PRESENCE in
`test_live_operation_api.py` -- neither actually executes them against
known inputs. This file extracts the real function source (by
brace-balanced parsing, not a fragile regex) and runs it under a real
Node subprocess, so a behavioral regression (e.g. an off-by-one in a
threshold, or a flipped side comparison) is caught by pytest, not just
manual UAT.

This is deliberately NOT a general JS test harness: it targets exactly
the three pure, DOM-independent functions this audit added/relies on.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

_INDEX_HTML = Path(__file__).resolve().parents[3] / "app" / "api" / "static" / "index.html"


def _extract_function(source: str, name: str) -> str:
    """Brace-balanced extraction of `function <name>(...) { ... }` --
    safe against nested braces (object literals, arrow functions) inside
    the function body, unlike a naive non-greedy regex."""
    marker = f"function {name}("
    start = source.index(marker)
    brace_start = source.index("{", start)
    depth = 0
    i = brace_start
    while i < len(source):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
        i += 1
    raise AssertionError(f"unbalanced braces extracting function {name}")


def _extract_const(source: str, name: str) -> str:
    match = re.search(rf"^const {re.escape(name)}\s*=.*?;", source, re.MULTILINE)
    if match is None:
        raise AssertionError(f"could not find const {name}")
    return match.group(0)


@pytest.fixture(scope="module")
def js_source() -> str:
    return _INDEX_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def node_available() -> bool:
    return shutil.which("node") is not None


def _run_node_harness(js_source: str, calls: list[tuple[str, list[object]]]) -> list[Any]:
    """Extracts the real functions from the real dashboard source, then
    calls each of `calls` (function name, args) and returns the real
    results as parsed JSON -- no reimplementation of the logic under
    test."""
    pieces = [
        _extract_function(js_source, "num"),
        _extract_const(js_source, "_LIVE_FRESH_MAX_SECONDS"),
        _extract_const(js_source, "_LIVE_AGING_MAX_SECONDS"),
        _extract_function(js_source, "freshnessState"),
        _extract_function(js_source, "humanDuration"),
        _extract_function(js_source, "classifyContractState"),
        _extract_const(js_source, "_PRIMARY_EVIDENCE_GROUPS"),
        _extract_const(js_source, "_SECONDARY_EVIDENCE_GROUPS"),
        _extract_function(js_source, "summarizeGroupDirection"),
        _extract_function(js_source, "underlyingMoveToBreakeven"),
        _extract_const(js_source, "_LIQUIDITY_ORDINAL"),
        _extract_const(js_source, "_DECAY_ORDINAL"),
        _extract_function(js_source, "_compareDimension"),
        _extract_function(js_source, "dominanceVerdict"),
        _extract_function(js_source, "researchCardHeadlineState"),
    ]
    call_lines = "\n".join(
        f"results.push({name}({', '.join(json.dumps(a) for a in args)}));" for name, args in calls
    )
    script = "\n".join(pieces) + "\nconst results = [];\n" + call_lines + "\nconsole.log(JSON.stringify(results));\n"
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, check=False)
    assert proc.returncode == 0, f"node harness failed: {proc.stderr}"
    parsed: list[Any] = json.loads(proc.stdout)
    return parsed


def test_human_duration_converts_seconds_to_real_human_readable_strings(js_source: str, node_available: bool) -> None:
    if not node_available:
        pytest.skip("node not available in this environment")
    results = _run_node_harness(js_source, [
        ("humanDuration", [45]),
        ("humanDuration", [90]),
        ("humanDuration", [3661]),
        ("humanDuration", [90061]),
        ("humanDuration", [0]),
        ("humanDuration", [None]),
    ])
    assert results == ["45s", "1m 30s", "1h 1m", "1d 1h 1m", "0s", "n/a"]


def test_freshness_state_market_closed_always_wins_over_elapsed_seconds(js_source: str, node_available: bool) -> None:
    """Real defect this guards against: age-based thresholds silently
    overriding the real MARKET_CLOSED_LATEST_DATA fact and mislabeling a
    weekend/holiday as a stale live feed."""
    if not node_available:
        pytest.skip("node not available in this environment")
    results = _run_node_harness(js_source, [
        ("freshnessState", ["MARKET_CLOSED_LATEST_DATA", 30]),
        ("freshnessState", ["MARKET_CLOSED_LATEST_DATA", 900000]),
    ])
    assert [r["state"] for r in results] == ["MARKET_CLOSED", "MARKET_CLOSED"]


def test_freshness_state_live_thresholds(js_source: str, node_available: bool) -> None:
    if not node_available:
        pytest.skip("node not available in this environment")
    results = _run_node_harness(js_source, [
        ("freshnessState", ["LIVE_SNAPSHOT", 30]),
        ("freshnessState", ["LIVE_SNAPSHOT", 120]),
        ("freshnessState", ["LIVE_SNAPSHOT", 400]),
        ("freshnessState", ["STALE_DATA", 30]),
        ("freshnessState", ["PROVIDER_UNAVAILABLE", None]),
    ])
    assert [r["state"] for r in results] == ["FRESH", "AGING", "STALE", "STALE", "UNAVAILABLE"]


def test_classify_contract_state_poor_contract_overrides_everything(js_source: str, node_available: bool) -> None:
    """Objective 9, item 1/2 -- a HIGH_RISK_STRUCTURE contract is always
    POOR CONTRACT, regardless of directional verdict or decision -- and a
    structurally healthy contract with insufficient/conflicted direction
    is never labeled POOR."""
    if not node_available:
        pytest.skip("node not available in this environment")
    results = _run_node_harness(js_source, [
        ("classifyContractState", ["HIGH_RISK_STRUCTURE", "BULLISH_SIDE_BETTER_SUPPORTED", "ce", "TRADEABLE", "FRESH"]),
        ("classifyContractState", ["ACCEPTABLE", "BOTH_SIDES_WEAK", "ce", "NO_TRADE", "FRESH"]),
        ("classifyContractState", ["ACCEPTABLE", "CONFLICTED", "ce", "NO_TRADE", "FRESH"]),
    ])
    assert results[0]["label"] == "POOR CONTRACT"
    assert results[1]["label"] == "HEALTHY CONTRACT / NO DIRECTION"
    assert results[2]["label"] == "HEALTHY CONTRACT / CONFLICTED"
    assert results[1]["label"] != results[2]["label"]  # no-direction and conflicted must be visibly distinct


def test_classify_contract_state_watch_and_actionable(js_source: str, node_available: bool) -> None:
    """Objective 9, item 3/4 -- a leaning-but-unconfirmed side reads WATCH
    FOR CONFIRMATION; TRADEABLE is compatibility-only, never ACTIONABLE."""
    if not node_available:
        pytest.skip("node not available in this environment")
    results = _run_node_harness(js_source, [
        ("classifyContractState", ["ACCEPTABLE", "BULLISH_SIDE_BETTER_SUPPORTED", "ce", "WATCH", "FRESH"]),
        ("classifyContractState", ["ACCEPTABLE", "BULLISH_SIDE_BETTER_SUPPORTED", "pe", "WATCH", "FRESH"]),
        ("classifyContractState", ["ACCEPTABLE", "BULLISH_SIDE_BETTER_SUPPORTED", "ce", "TRADEABLE", "FRESH"]),
    ])
    assert results[0]["label"] == "HEALTHY CONTRACT / WATCH FOR CONFIRMATION"  # CE, evidence leans CE
    assert results[1]["label"] == "HEALTHY CONTRACT / NO DIRECTION"  # PE, evidence leans the OTHER side
    assert results[2]["label"] == "COMPATIBLE (NOT AN ORDER)"


def test_classify_contract_state_stale_data_overrides_a_favorable_verdict(js_source: str, node_available: bool) -> None:
    """Objective 9, item 5 -- stale/unavailable data must never be
    presented as an actionable or watchable opportunity."""
    if not node_available:
        pytest.skip("node not available in this environment")
    results = _run_node_harness(js_source, [
        ("classifyContractState", ["ACCEPTABLE", "BULLISH_SIDE_BETTER_SUPPORTED", "ce", "TRADEABLE", "STALE"]),
    ])
    assert results[0]["label"] == "DATA UNAVAILABLE / STALE"


def test_summarize_group_direction_distinguishes_primary_from_secondary(js_source: str, node_available: bool) -> None:
    """Final Pre-Freeze Product Analytical Pass, Issue 3 -- price-action
    rows (M15 trend, VWAP, market regime) and options-positioning rows
    (PCR/OI, IV, futures, global) are summarized SEPARATELY, using only
    real `EvidenceRowView.group`/`.direction` values -- proving a case
    where primary evidence is bullish while secondary evidence disagrees,
    as INDIGO's real live data does (M15 ascending, PCR contrarian-bearish
    convention)."""
    if not node_available:
        pytest.skip("node not available in this environment")
    rows = [
        {"name": "M15 trend", "group": "underlying_price_structure", "direction": "BULLISH"},
        {"name": "VWAP", "group": "underlying_price_structure", "direction": "BULLISH"},
        {"name": "Market regime", "group": "underlying_price_structure", "direction": "BULLISH"},
        {"name": "Call/Put OI structure", "group": "options_oi", "direction": "BEARISH"},
        {"name": "IV", "group": "options_iv", "direction": "NEUTRAL"},
    ]
    results = _run_node_harness(js_source, [
        ("summarizeGroupDirection", [rows, ["underlying_price_structure"]]),
        ("summarizeGroupDirection", [rows, ["options_oi", "options_iv", "futures", "global"]]),
    ])
    assert results[0]["label"] == "Bullish"
    assert results[1]["label"] == "Bearish"


def test_underlying_move_to_breakeven_sign_and_magnitude_for_ce_and_pe(js_source: str, node_available: bool) -> None:
    """Issue 4 -- pure expiry arithmetic, verified against the exact real
    INDIGO 5200 CE numbers from the live UAT (spot 5166, contractual BE
    5335.4 -> +169.40 points, +3.28%)."""
    if not node_available:
        pytest.skip("node not available in this environment")
    results = _run_node_harness(js_source, [
        ("underlyingMoveToBreakeven", [5335.4, 5166, "ce"]),
        ("underlyingMoveToBreakeven", [5075.75, 5166, "pe"]),
        ("underlyingMoveToBreakeven", [None, 5166, "ce"]),
    ])
    assert round(results[0]["points"], 2) == 169.4
    assert round(results[0]["pct"], 2) == 3.28
    assert round(results[1]["points"], 2) == 90.25  # PE: spot - BE
    assert results[1]["points"] > 0
    assert results[2] is None


def test_dominance_verdict_never_calls_a_worse_alternative_dominant(js_source: str, node_available: bool) -> None:
    """Final Pre-Freeze Trader Decision-Support Audit, Audit 5 (HIGH
    PRIORITY) -- a real, explicitly-scoped comparison over 5 analytically
    valid dimensions (spread, required move, liquidity, decay viability,
    distance to contractual breakeven). The verdict text must always
    name how many of how many dimensions were compared (never a bare
    "dominates"), and an alternative worse on ANY known dimension must
    never be reported as fully dominant."""
    if not node_available:
        pytest.skip("node not available in this environment")
    spot = 100.0
    requested = {"right": "CE", "spread_pct": 2.0, "required_underlying_move_pct": 3.0, "liquidity_grade": "good", "decay_verdict": "DECAY_ACCEPTABLE", "contractual_expiry_breakeven": 110.0}
    strictly_better = {"right": "CE", "spread_pct": 1.0, "required_underlying_move_pct": 2.0, "liquidity_grade": "excellent", "decay_verdict": "DECAY_FAVORABLE", "contractual_expiry_breakeven": 105.0}
    strictly_worse = {"right": "CE", "spread_pct": 4.0, "required_underlying_move_pct": 5.0, "liquidity_grade": "poor", "decay_verdict": "DECAY_HEADWIND", "contractual_expiry_breakeven": 115.0}
    mixed = {"right": "CE", "spread_pct": 1.0, "required_underlying_move_pct": 5.0, "liquidity_grade": "good", "decay_verdict": "DECAY_ACCEPTABLE", "contractual_expiry_breakeven": 110.0}
    results = _run_node_harness(js_source, [
        ("dominanceVerdict", [strictly_better, requested, spot]),
        ("dominanceVerdict", [strictly_worse, requested, spot]),
        ("dominanceVerdict", [mixed, requested, spot]),
    ])
    assert results[0]["worse"] == 0 and results[0]["better"] > 0
    assert "BETTER ON" in results[0]["verdict"] and f"OF {results[0]['total']}" in results[0]["verdict"]
    assert results[1]["better"] == 0 and results[1]["worse"] > 0
    assert "WORSE ON" in results[1]["verdict"]
    assert results[2]["better"] > 0 and results[2]["worse"] > 0
    assert "NO CLEAR DOMINANCE" in results[2]["verdict"]
    for r in results:
        assert "OF " + str(r["total"]) in r["verdict"] or r["total"] == 0


def test_research_card_headline_watch_and_event_cannot_display_as_developing(js_source: str, node_available: bool) -> None:
    if not node_available:
        pytest.skip("node not available in this environment")
    results = _run_node_harness(js_source, [
        ("researchCardHeadlineState", [{"research_bucket": "EVENT_DRIVEN", "developing_pattern": "NONE"}]),
        ("researchCardHeadlineState", [{"research_bucket": "NOT_INTERESTING", "developing_pattern": "NONE"}]),
        ("researchCardHeadlineState", [{"research_bucket": "DEVELOPING", "developing_pattern": "NONE"}]),
        ("researchCardHeadlineState", [{"research_bucket": "DEVELOPING", "developing_pattern": "OI_MIGRATION"}]),
        ("researchCardHeadlineState", [{"research_bucket": "ALREADY_MOVED", "developing_pattern": "OI_MIGRATION"}]),
    ])
    assert results == ["EVENT", "WATCH", "WATCH", "DEVELOPING", "ALREADY_MOVED"]
