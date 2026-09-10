"""Daily Market Researcher -- HTTP wiring for `GET /api/research/daily`.
Reuses the exact same router/master/provider fixtures `test_dashboard_api.py`
already validates the single-symbol `/api/analyze` endpoint against,
rather than duplicating them (same pattern `test_live_operation_api.py`
already uses).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.utils.time import utc_now
from tests.integration.api.test_dashboard_api import (
    _EXPIRY_MS,
    _MASTER,
    EXPIRY,
    _configured_app,
    _provider,
    _router,
)


def test_research_daily_returns_503_when_token_not_configured(tmp_path: Path) -> None:
    app = _configured_app(tmp_path, provider=None, instrument_master=None)
    client = TestClient(app)
    resp = client.get("/api/research/daily")
    assert resp.status_code == 503
    assert "UPSTOX_ACCESS_TOKEN" in resp.json()["detail"]


def test_research_daily_explicit_symbols_override_via_http(tmp_path: Path) -> None:
    """`_router()`'s fixture only knows RELIANCE-shaped data by
    instrument key -- exercising the `?symbols=` override (Stage 1
    correctly skipped) proves the full HTTP path end to end."""
    app = _configured_app(tmp_path, provider=_provider(_router()), instrument_master=_MASTER)
    client = TestClient(app)
    resp = client.get("/api/research/daily", params={"symbols": "RELIANCE"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["universe"] == ["RELIANCE"]
    assert body["screened_count"] == 1
    assert body["stage_one_survivor_count"] is None  # override -- Stage 1 skipped
    assert body["fno_ban_status"] == "FNO_BAN_STATUS_UNKNOWN"
    assert body["universe_source"] == "explicit_symbols_override"
    assert isinstance(body["confirmation_pending"], list)
    assert isinstance(body["data_issues"], list)
    assert body["deep_analyzed_count"] == 1
    assert "generated_at" in body and "market_state" in body
    assert isinstance(body["opportunities"], list)
    assert isinstance(body["rejected"], list)
    assert isinstance(body["rejection_summary"], dict)
    assert isinstance(body["rejected_as_extended_count"], int)
    assert isinstance(body["rejected_insufficient_evidence_count"], int)
    assert body["shortlisted_count"] == len(body["opportunities"])
    assert body["no_high_conviction"] == (body["shortlisted_count"] == 0)
    assert isinstance(body["events_to_monitor"], list)
    assert isinstance(body["scan_mode"], str)
    assert isinstance(body["already_moved"], list)
    assert isinstance(body["zero_valid_early_opportunities"], bool)
    assert body["zero_valid_early_opportunities"] == (len(body["developing_now"]) == 0)
    for card in body["opportunities"] + body["developing_now"] + body["already_moved"]:
        assert "research_bucket" in card
        assert "why_investigate_now" in card
        assert "developing_pattern" in card
        assert "BUY" not in card["why_investigate_now"]
        assert "SELL" not in card["why_investigate_now"]


def test_research_daily_default_universe_via_http_runs_stage_one(tmp_path: Path) -> None:
    """No `?symbols=` -- the real default path: Stage 1 derives the real
    F&O equity universe from the fixture master (RELIANCE only, in this
    small fixture) and screens it via the real batched quote endpoint
    before Stage 2 runs."""
    app = _configured_app(tmp_path, provider=_provider(_router()), instrument_master=_MASTER)
    client = TestClient(app)
    resp = client.get("/api/research/daily")
    assert resp.status_code == 200
    body = resp.json()
    assert body["universe"] == ["RELIANCE"]
    assert body["screened_count"] == 1
    assert body["stage_one_survivor_count"] == 1  # Stage 1 ran and RELIANCE survived (only candidate)
    assert body["deep_analyzed_count"] == 1


def test_research_daily_symbols_param_is_parsed_and_normalized(tmp_path: Path) -> None:
    app = _configured_app(tmp_path, provider=_provider(_router()), instrument_master=_MASTER)
    client = TestClient(app)
    resp = client.get("/api/research/daily", params={"symbols": " reliance , reliance "})
    assert resp.status_code == 200
    # normalized to uppercase; a repeated symbol is not silently de-duped
    # by this endpoint (that would hide a real caller mistake) -- both
    # entries are analyzed exactly as requested.
    assert resp.json()["universe"] == ["RELIANCE", "RELIANCE"]


def test_research_daily_no_forbidden_language_or_credential_leak(tmp_path: Path) -> None:
    app = _configured_app(tmp_path, provider=_provider(_router()), instrument_master=_MASTER)
    client = TestClient(app)
    resp = client.get("/api/research/daily", params={"symbols": "RELIANCE"})
    text = resp.text.upper()
    for forbidden in ["BUY NOW", "SELL NOW", "GUARANTEED", "100% ACCURACY", "WIN RATE", "TARGET PROFIT"]:
        assert forbidden not in text
    assert "BEARER " not in text
    assert "ACCESS_TOKEN" not in text


# ============================================================
# Early-move discovery phase -- the new `market_context`/`room_to_breakeven`/
# `participation_note`/`early_stage_state` fields are genuinely reachable
# through the real HTTP JSON response, not just internal Python objects.
# Same real "quiet before move" candle shape
# (`tests/integration/orchestration/test_daily_research.py`'s
# `_router_quiet_setup`) rebuilt here for the HTTP layer.
# ============================================================

E_KEY, E_CE, E_PE, E_FUT = "NSE_EQ|EEE", "NSE_FO|E1", "NSE_FO|E2", "NSE_FO|E3"
E_SPOT = 998.0

_MASTER_WITH_E: list[dict[str, object]] = [
    *_MASTER,
    {"segment": "NSE_EQ", "name": "SYMBOL E", "exchange": "NSE", "instrument_type": "EQ", "instrument_key": E_KEY, "trading_symbol": "SYMBOLE"},
    {"segment": "NSE_FO", "underlying_symbol": "SYMBOLE", "instrument_type": "CE", "expiry": _EXPIRY_MS, "weekly": False, "lot_size": 500, "instrument_key": E_CE, "strike_price": 1000.0},
    {"segment": "NSE_FO", "underlying_symbol": "SYMBOLE", "instrument_type": "PE", "expiry": _EXPIRY_MS, "weekly": False, "lot_size": 500, "instrument_key": E_PE, "strike_price": 1000.0},
    {"segment": "NSE_FO", "underlying_symbol": "SYMBOLE", "instrument_type": "FUT", "expiry": _EXPIRY_MS, "weekly": False, "lot_size": 500, "instrument_key": E_FUT},
]

_E_PRICES: list[float] = (
    [1000.0] * 15
    + [1000.0 - 0.75 * i for i in range(1, 21)]
    + [985.0 + 0.52 * i for i in range(1, 26)]
)


def _router_quiet_setup() -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v2/market/status/NSE":
            return httpx.Response(200, json={"status": "success", "data": {"status": "NORMAL_OPEN"}})
        if path == "/v2/market-quote/quotes":
            keys = request.url.params["instrument_key"].split(",")
            data: dict[str, object] = {}
            for k in keys:
                if k == E_KEY:
                    data["NSE_EQ:E"] = {
                        "instrument_token": k, "last_price": E_SPOT, "net_change": 8.0, "volume": 500000,
                        "ohlc": {"open": 990.0, "high": 999.0, "low": 984.0, "close": E_SPOT}, "average_price": 994.0,
                        "total_buy_quantity": 150000, "total_sell_quantity": 100000,
                    }
                elif k == E_FUT:
                    data["NSE_FO:EFUT"] = {"instrument_token": k, "last_price": E_SPOT + 2.0, "net_change": 4.0, "oi": 1000000, "volume": 500000}
            return httpx.Response(200, json={"status": "success", "data": data})
        if path.startswith("/v3/historical-candle/"):
            # Sprint 7A -- `/api/research/daily` always calls the pipeline
            # with a real `as_of=utc_now()` (never a fixed historical
            # date) -- candles must end near a real-current instant too,
            # or the new market-data-synchronization check
            # (Objective 1) correctly, honestly reads this "quiet before
            # move" series as a stale previous-session series and
            # withholds M15/VWAP/regime evidence, changing the real
            # early-stage read -- a `- 1 minute` buffer avoids a real
            # no-look-ahead race against the pipeline's own `as_of`.
            end = utc_now() - timedelta(minutes=1)
            candles = [
                [(end - timedelta(minutes=15 * (len(_E_PRICES) - 1 - i))).isoformat(), p, p + 1, p - 1, p, 1000, 0]
                for i, p in enumerate(_E_PRICES)
            ]
            return httpx.Response(200, json={"status": "success", "data": {"candles": candles}})
        if path == "/v2/news":
            return httpx.Response(200, json={"status": "success", "data": {}, "metadata": {"page": {"total_records": 0}}})
        if path == "/v2/option/chain":
            key = request.url.params["instrument_key"]
            if key == E_KEY:
                return httpx.Response(200, json={
                    "status": "success",
                    "data": [{
                        "expiry": EXPIRY.isoformat(), "strike_price": E_SPOT, "underlying_spot_price": E_SPOT,
                        "call_options": {
                            "instrument_key": E_CE, "market_data": {"ltp": 25.0, "bid_price": 24.5, "ask_price": 25.5, "volume": 8000, "oi": 80000, "prev_oi": 75000},
                            "option_greeks": {"iv": 17.5, "delta": 0.5, "theta": -0.8, "gamma": 0.01, "vega": 0.5},
                        },
                        "put_options": {
                            "instrument_key": E_PE, "market_data": {"ltp": 23.0, "bid_price": 22.5, "ask_price": 23.5, "volume": 7000, "oi": 60000, "prev_oi": 58000},
                            "option_greeks": {"iv": 16.5, "delta": -0.5, "theta": -0.7, "gamma": 0.01, "vega": 0.5},
                        },
                    }],
                })
            raise AssertionError(f"unexpected chain request for {key}")
        raise AssertionError(f"unexpected path {path}")

    return handler


def test_research_daily_early_move_discovery_fields_appear_in_the_real_json_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # HTTP daily research always passes `as_of=utc_now()`. After the cash
    # session, synthesizing M15 candles at wall-clock "now" is read as
    # post-session / unsynchronized and the quiet-setup evidence is
    # honestly withheld (BOTH_SIDES_WEAK). Pin the clock to a live NSE
    # session so this JSON-contract test is not a market-hours flake.
    session_now = datetime(2026, 9, 9, 5, 0, tzinfo=UTC)  # 10:30 IST
    monkeypatch.setattr("app.api.main.utc_now", lambda: session_now)
    monkeypatch.setattr("app.orchestration.daily_research.utc_now", lambda: session_now)
    monkeypatch.setattr("app.data.providers.upstox_provider.utc_now", lambda: session_now)
    monkeypatch.setattr("tests.integration.api.test_daily_research_api.utc_now", lambda: session_now)
    app = _configured_app(tmp_path, provider=_provider(_router_quiet_setup()), instrument_master=_MASTER_WITH_E)
    client = TestClient(app)
    resp = client.get("/api/research/daily", params={"symbols": "SYMBOLE"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["opportunities"]) == 1
    opp = body["opportunities"][0]
    assert opp["symbol"] == "SYMBOLE"
    assert opp["early_stage_state"] == "EARLY_DIRECTIONAL_BUILD"
    assert opp["market_context"] in ("SUPPORTIVE", "OPPOSING", "NEUTRAL", "UNKNOWN")
    assert "market_context_detail" in opp
    assert "room_to_breakeven" in opp
    assert "participation_note" in opp
    # `?symbols=` is an explicit override -- Stage 1 is correctly skipped
    # (see `test_research_daily_explicit_symbols_override_via_http`), so
    # `participation_note` must honestly say no Stage-1 signal ran, never
    # fabricate one from the quote's real buy/sell quantity fields.
    assert opp["participation_note"] == (
        "no real Stage-1 order-flow signal available this run (either Stage 1 was skipped for an explicit "
        "symbol query, or no real buy/sell quantity data was present on the quote)."
    )
    # Sprint 1, Phase 4/6 -- coverage metadata reaches the real JSON
    # response too, alongside the candidate fields.
    assert body["coverage"] is not None
    assert body["coverage"]["classification"] in ("HIGH", "GOOD", "DEGRADED", "POOR")
    assert body["coverage"]["stage1_attempted"] is None  # explicit ?symbols= -- Stage 1 skipped
    assert body.get("coverage_note")
    text = resp.text.upper()
    for forbidden in ["BUY NOW", "SELL NOW", "GUARANTEED", "100% ACCURACY", "WIN RATE", "TARGET PROFIT"]:
        assert forbidden not in text


def test_research_discover_is_an_alias_of_daily(tmp_path: Path) -> None:
    app = _configured_app(tmp_path, provider=_provider(_router()), instrument_master=_MASTER)
    client = TestClient(app)
    daily = client.get("/api/research/daily", params={"symbols": "RELIANCE"})
    discover = client.get("/api/research/discover", params={"symbols": "RELIANCE"})
    assert daily.status_code == 200
    assert discover.status_code == 200
    assert discover.json()["universe"] == daily.json()["universe"]
    assert discover.json()["scan_mode"] == "EXPLICIT_SYMBOL_QUERY"
    snap = discover.json()["scan_snapshot"]
    assert snap is not None
    assert "stage1_seconds" in snap
    assert "stage2_seconds" in snap
    assert snap["fno_ban_status"] == "FNO_BAN_STATUS_UNKNOWN"
