"""End-to-end tests for the Sprint 1 localhost dashboard API — exercises
the REAL FastAPI routing/JSON contract against a mocked Upstox provider
(never real network), reusing the exact router-mocking pattern already
established in `tests/integration/orchestration/test_options_intelligence_pipeline.py`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.data.providers.upstox_provider import UpstoxProvider
from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy
from app.orchestration.options_intelligence_pipeline import PipelineConfig, Repositories
from app.orchestration.query_context import ContextState
from app.persistence.jsonl_file import (
    JsonlIvObservationRepository,
    JsonlOptionChainRepository,
    JsonlQuoteRepository,
)
from app.utils.time import utc_now

RELIANCE_KEY = "NSE_EQ|INE002A01018"
CE_KEY = "NSE_FO|1"
PE_KEY = "NSE_FO|2"
FUT_KEY = "NSE_FO|3"
EXPIRY = date(2026, 9, 24)
_EXPIRY_MS = 1790274599000
AS_OF = datetime(2026, 8, 28, 6, 0, tzinfo=UTC)

_MASTER: list[dict[str, object]] = [
    {"segment": "NSE_EQ", "name": "RELIANCE INDUSTRIES LTD", "exchange": "NSE", "instrument_type": "EQ", "instrument_key": RELIANCE_KEY, "trading_symbol": "RELIANCE"},
    {"segment": "NSE_FO", "underlying_symbol": "RELIANCE", "instrument_type": "CE", "expiry": _EXPIRY_MS, "weekly": False, "lot_size": 500, "instrument_key": CE_KEY, "strike_price": 1300.0},
    {"segment": "NSE_FO", "underlying_symbol": "RELIANCE", "instrument_type": "PE", "expiry": _EXPIRY_MS, "weekly": False, "lot_size": 500, "instrument_key": PE_KEY, "strike_price": 1300.0},
    {"segment": "NSE_FO", "underlying_symbol": "RELIANCE", "instrument_type": "FUT", "expiry": _EXPIRY_MS, "weekly": False, "lot_size": 500, "instrument_key": FUT_KEY},
]


def _candle_rows(n: int, *, end: datetime, price: float = 1280.0) -> list[list[object]]:
    return [
        [(end - timedelta(minutes=15 * (n - 1 - i))).isoformat(), price + i * 0.5, price + i * 0.5 + 1, price + i * 0.5 - 1, price + i * 0.5, 1000, 0]
        for i in range(n)
    ]


def _router(*, status: str = "NORMAL_OPEN") -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v2/market/status/NSE":
            return httpx.Response(200, json={"status": "success", "data": {"status": status}})
        if path == "/v2/market-quote/quotes":
            keys = request.url.params["instrument_key"].split(",")
            data: dict[str, object] = {}
            for k in keys:
                if k == RELIANCE_KEY:
                    # Daily Researcher Stage 1 (early-stage discovery) --
                    # `total_buy_quantity`/`total_sell_quantity` are real,
                    # already-returned Upstox quote fields, added here so
                    # RELIANCE genuinely qualifies for the real
                    # ORDER_FLOW_PARTICIPATION discovery bucket (a real
                    # imbalance) in tests that exercise the default
                    # (no `?symbols=`) Stage-1 path. Purely additive --
                    # `normalize_quote()`/the single-symbol pipeline never
                    # reads these, so every other test using this router
                    # is unaffected.
                    data["NSE_EQ:RELIANCE"] = {
                        "instrument_token": k, "last_price": 1300.0, "net_change": 5.0, "volume": 1000000,
                        "total_buy_quantity": 1300000.0, "total_sell_quantity": 700000.0,
                    }
                elif k == FUT_KEY:
                    data["NSE_FO:FUT"] = {"instrument_token": k, "last_price": 1302.0, "net_change": 4.0, "oi": 1000000, "volume": 500000}
            return httpx.Response(200, json={"status": "success", "data": data})
        if path.startswith("/v3/historical-candle/"):
            # Sprint 7A -- `/api/analyze` always calls the pipeline with a
            # real `as_of=utc_now()` (never this file's own fixed `AS_OF`,
            # which only anchors the mock chain's own expiry/strike
            # bookkeeping) -- candles must end at a real-current instant
            # too, or the new market-data-synchronization check
            # (Objective 1) correctly, honestly reads them as a stale
            # previous-session series and withholds M15/VWAP/regime
            # evidence, which is real, intended behavior, not a defect.
            # `- 1 minute` avoids a real no-look-ahead race: the pipeline's
            # own `as_of=utc_now()` is captured microseconds before this
            # mock's own `utc_now()` call runs, so an `end` exactly at
            # `utc_now()` here can land AFTER that `as_of` and get the
            # last candle correctly filtered out by the provider's own
            # `timestamp > as_of` check -- a real, correct no-look-ahead
            # behavior, just not what this fixture intends to test.
            return httpx.Response(200, json={"status": "success", "data": {"candles": _candle_rows(60, end=utc_now() - timedelta(minutes=1))}})
        if path == "/v2/news":
            return httpx.Response(200, json={"status": "success", "data": {}, "metadata": {"page": {"total_records": 0}}})
        if path == "/v2/option/chain":
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": [
                        {
                            "expiry": EXPIRY.isoformat(), "strike_price": 1200.0, "underlying_spot_price": 1300.0,
                            "call_options": {"instrument_key": CE_KEY, "market_data": {"ltp": 105.0, "bid_price": 104.5, "ask_price": 105.5, "volume": 4000, "oi": 40000, "prev_oi": 39000}, "option_greeks": {"iv": 19.0, "delta": 0.8, "theta": -1.5, "gamma": 0.005, "vega": 0.8}},
                            "put_options": {"instrument_key": PE_KEY, "market_data": {"ltp": 3.0, "bid_price": 2.5, "ask_price": 3.5, "volume": 3000, "oi": 30000, "prev_oi": 29000}, "option_greeks": {"iv": 20.0, "delta": -0.2, "theta": -1.0, "gamma": 0.003, "vega": 0.5}},
                        },
                        {
                            "expiry": EXPIRY.isoformat(), "strike_price": 1300.0, "underlying_spot_price": 1300.0,
                            "call_options": {"instrument_key": CE_KEY, "market_data": {"ltp": 30.0, "bid_price": 29.5, "ask_price": 30.5, "volume": 8000, "oi": 80000, "prev_oi": 75000}, "option_greeks": {"iv": 18.0, "delta": 0.5, "theta": -2.0, "gamma": 0.01, "vega": 1.0}},
                            "put_options": {"instrument_key": PE_KEY, "market_data": {"ltp": 28.0, "bid_price": 27.5, "ask_price": 28.5, "volume": 7000, "oi": 60000, "prev_oi": 58000}, "option_greeks": {"iv": 17.0, "delta": -0.5, "theta": -1.8, "gamma": 0.01, "vega": 1.0}},
                        },
                    ],
                },
            )
        if path == "/v2/ipos":
            return httpx.Response(200, json={
                "status": "success", "data": [],
                "meta_data": {"page": {"page_number": 1, "total_pages": 1, "records": 0, "total_records": 0}},
            })
        raise AssertionError(f"unexpected path {path}")

    return handler


def _provider(handler: Callable[[httpx.Request], httpx.Response]) -> UpstoxProvider:
    return UpstoxProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), access_token="tok")


@asynccontextmanager
async def _noop_lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Deliberately does nothing -- real wiring (real HTTP client, real
    # instrument-master fetch) must NEVER run during a test.
    yield


def _configured_app(tmp_path: Path, *, provider: UpstoxProvider | None, instrument_master: list[dict[str, object]] | None) -> FastAPI:
    app = create_app(lifespan=_noop_lifespan)
    app.state.provider = provider
    app.state.instrument_master = instrument_master
    app.state.mcx_instrument_master = None
    app.state.strategy = EMAVWAPAlignmentStrategy()
    app.state.config = PipelineConfig(chain_fetch_attempts=1, chain_fetch_backoff_seconds=0.01)
    app.state.repositories = Repositories(
        quotes=JsonlQuoteRepository(tmp_path / "quotes.jsonl"), option_chains=JsonlOptionChainRepository(tmp_path / "chains.jsonl"),
        iv_observations=JsonlIvObservationRepository(tmp_path / "iv.jsonl"),
    )
    return app


def test_index_serves_the_dashboard_html(tmp_path: Path) -> None:
    app = _configured_app(tmp_path, provider=None, instrument_master=None)
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Indian Market Intelligence" in resp.text
    assert "<script>" in resp.text  # real page, not a stub


def test_health_reports_token_configured_state(tmp_path: Path) -> None:
    app = _configured_app(tmp_path, provider=_provider(_router()), instrument_master=_MASTER)
    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_configured"] is True
    assert body["broker_execution"] == "impossible"
    names = {s["name"]: s["state"] for s in body["streams"]}
    assert names["Option Chain"] != "GREEN"
    assert names["5paisa"] == "UNKNOWN"
    assert body["session_window"]["session_window"] in ("OPEN", "PRE_OPEN", "CLOSED")
    assert "Not a live exchange ping" in body["session_window"]["basis"]


def test_market_observed_never_fabricates_an_index_print(tmp_path: Path) -> None:
    app = _configured_app(tmp_path, provider=_provider(_router()), instrument_master=_MASTER)
    client = TestClient(app)
    resp = client.get("/api/market/observed")
    assert resp.status_code == 200
    body = resp.json()
    assert body["indices"]["nifty"]["last_price"] is None
    assert body["indices"]["nifty"]["observation_kind"] == "UNAVAILABLE"
    assert body["breadth"]["observation_kind"] == "UNAVAILABLE"


def test_analyze_returns_503_when_token_not_configured(tmp_path: Path) -> None:
    app = _configured_app(tmp_path, provider=None, instrument_master=None)
    client = TestClient(app)
    resp = client.post("/api/analyze", json={"query": "RELIANCE"})
    assert resp.status_code == 503
    assert "UPSTOX_ACCESS_TOKEN" in resp.json()["detail"]


def test_analyze_full_real_pipeline_via_http(tmp_path: Path) -> None:
    app = _configured_app(tmp_path, provider=_provider(_router()), instrument_master=_MASTER)
    client = TestClient(app)
    resp = client.post("/api/analyze", json={"query": "RELIANCE"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "RELIANCE"
    assert body["error"] is None
    assert "OPTIONS INTELLIGENCE" in body["compact_report"]
    assert body["market_state"] is not None
    assert body["latency_seconds"] > 0
    assert body["timing_stage"] in {
        "VERY_EARLY", "EARLY", "DEVELOPING", "CONFIRMING", "CONFIRMED",
        "MATURE", "ALREADY_MOVED", "EXTENDED", "UNKNOWN",
    }
    if body["timing_stage"] == "UNKNOWN":
        assert body["timing_reason"] == "Insufficient evidence to classify timing."
    assert "market_observed_at" in body


def test_analyze_surfaces_parsed_strike_and_right(tmp_path: Path) -> None:
    app = _configured_app(tmp_path, provider=_provider(_router()), instrument_master=_MASTER)
    client = TestClient(app)
    resp = client.post("/api/analyze", json={"query": "RELIANCE 1300 CE"})
    body = resp.json()
    assert body["parsed_symbol"] == "RELIANCE"
    assert body["parsed_strike"] == "1300"
    assert body["parsed_right"] == "CE"
    assert body["has_specific_contract"] is True
    assert body["parse_warnings"] == []
    # Sprint 1 scope: the request is recognized but not yet acted on as a
    # specific-contract analysis (that is Sprint 2) -- it still analyzes
    # the underlying symbol via the existing pipeline.
    assert body["symbol"] == "RELIANCE"


def test_analyze_surfaces_parse_warnings_without_crashing(tmp_path: Path) -> None:
    app = _configured_app(tmp_path, provider=_provider(_router()), instrument_master=_MASTER)
    client = TestClient(app)
    resp = client.post("/api/analyze", json={"query": "RELIANCE 1300"})  # strike without CE/PE
    assert resp.status_code == 200
    body = resp.json()
    assert any("without CE/PE" in w for w in body["parse_warnings"])
    assert body["symbol"] == "RELIANCE"  # still analyzes the underlying honestly


def test_analyze_unresolvable_symbol_returns_error_not_a_crash(tmp_path: Path) -> None:
    app = _configured_app(tmp_path, provider=_provider(_router()), instrument_master=_MASTER)
    client = TestClient(app)
    resp = client.post("/api/analyze", json={"query": "NOT_A_REAL_SYMBOL"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] is not None
    assert body["compact_report"] is None


def test_analyze_empty_query_returns_a_clean_error(tmp_path: Path) -> None:
    app = _configured_app(tmp_path, provider=_provider(_router()), instrument_master=_MASTER)
    client = TestClient(app)
    resp = client.post("/api/analyze", json={"query": "   "})
    assert resp.status_code == 422 or resp.json()["error"] is not None


def test_analyze_market_closed_never_fabricates_a_decision(tmp_path: Path) -> None:
    app = _configured_app(tmp_path, provider=_provider(_router(status="NORMAL_CLOSE")), instrument_master=_MASTER)
    client = TestClient(app)
    resp = client.post("/api/analyze", json={"query": "RELIANCE"})
    body = resp.json()
    assert body["market_state"] == "MARKET_CLOSED_LATEST_DATA"


def test_analyze_requested_contract_appears_in_the_rendered_reports(tmp_path: Path) -> None:
    """Sprint 2 end-to-end via real HTTP: "RELIANCE 1300 CE" surfaces the
    REQUESTED CONTRACT/CONTRACT ALTERNATIVES sections in both rendered
    reports -- the dashboard never needs its own copy of this logic (no
    duplicated calculation in the frontend; the backend renders it)."""
    app = _configured_app(tmp_path, provider=_provider(_router()), instrument_master=_MASTER)
    client = TestClient(app)
    resp = client.post("/api/analyze", json={"query": "RELIANCE 1300 CE"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_specific_contract"] is True
    assert "REQUESTED CONTRACT" in body["compact_report"]
    assert "CONTRACT ALTERNATIVES" in body["compact_report"]
    assert "REQUESTED CONTRACT" in body["detailed_report"]
    assert "1300" in body["compact_report"]


def test_analyze_response_includes_a_structured_visual_payload(tmp_path: Path) -> None:
    """Sprint 3: the `/api/analyze` JSON response carries a structured
    `visual` object -- charts/tables render from THIS, never by parsing
    `compact_report`/`detailed_report` text (which would force the
    frontend to re-derive numbers from prose)."""
    app = _configured_app(tmp_path, provider=_provider(_router()), instrument_master=_MASTER)
    client = TestClient(app)
    resp = client.post("/api/analyze", json={"query": "RELIANCE 1300 CE"})
    assert resp.status_code == 200
    visual = resp.json()["visual"]
    assert visual is not None
    assert visual["price_chart"]["insufficient_history"] is False
    assert len(visual["price_chart"]["candles"]) == 60
    assert "9" in visual["price_chart"]["ema"]
    assert len(visual["option_chain"]["rows"]) == 2  # 1200 CE/PE and 1300 CE/PE (single-strike router)
    assert visual["requested_contract"] is not None
    assert visual["requested_contract"]["found"] is True
    assert visual["evidence"]
    assert visual["quality"] is not None
    assert visual["freshness"]["market_state"] is not None


def test_analyze_visual_payload_absent_when_analysis_failed(tmp_path: Path) -> None:
    app = _configured_app(tmp_path, provider=_provider(_router()), instrument_master=_MASTER)
    client = TestClient(app)
    resp = client.post("/api/analyze", json={"query": "NOT_A_REAL_SYMBOL"})
    assert resp.json()["visual"] is None


def test_analyze_visual_payload_never_leaks_the_access_token(tmp_path: Path) -> None:
    app = _configured_app(tmp_path, provider=_provider(_router()), instrument_master=_MASTER)
    client = TestClient(app)
    resp = client.post("/api/analyze", json={"query": "RELIANCE 1300 CE"})
    body_text = resp.text.lower()
    assert '"tok"' not in body_text
    assert "access_token" not in body_text
    assert "authorization" not in body_text


# -- Section 6: deterministic follow-up context ------------------------------


def test_followup_with_no_prior_analysis_reports_no_context(tmp_path: Path) -> None:
    app = _configured_app(tmp_path, provider=_provider(_router()), instrument_master=_MASTER)
    app.state.query_context = ContextState()
    client = TestClient(app)
    resp = client.post("/api/followup", json={"text": "what about pe"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["resolution"]["kind"] == "NO_CONTEXT"
    assert body["analysis"] is None


def test_followup_what_about_pe_reanalyzes_the_swapped_contract(tmp_path: Path) -> None:
    app = _configured_app(tmp_path, provider=_provider(_router()), instrument_master=_MASTER)
    app.state.query_context = ContextState()
    client = TestClient(app)
    first = client.post("/api/analyze", json={"query": "RELIANCE 1300 CE"})
    assert first.json()["parsed_right"] == "CE"

    resp = client.post("/api/followup", json={"text": "what about PE?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["resolution"]["kind"] == "RESOLVED_QUERY"
    assert body["resolution"]["resolved_query"] == "RELIANCE 1300 PE"
    assert body["analysis"] is not None
    assert body["analysis"]["parsed_right"] == "PE"
    assert body["analysis"]["symbol"] == "RELIANCE"


def test_followup_which_expiry_answers_from_context_without_reanalyzing(tmp_path: Path) -> None:
    app = _configured_app(tmp_path, provider=_provider(_router()), instrument_master=_MASTER)
    app.state.query_context = ContextState()
    client = TestClient(app)
    client.post("/api/analyze", json={"query": "RELIANCE"})

    resp = client.post("/api/followup", json={"text": "which expiry?"})
    body = resp.json()
    assert body["resolution"]["kind"] == "ANSWERED_FROM_CONTEXT"
    assert body["analysis"] is None
    assert "TERM STRUCTURE" in body["resolution"]["message"]


def test_followup_unrecognized_phrase_needs_clarification(tmp_path: Path) -> None:
    app = _configured_app(tmp_path, provider=_provider(_router()), instrument_master=_MASTER)
    app.state.query_context = ContextState()
    client = TestClient(app)
    client.post("/api/analyze", json={"query": "RELIANCE"})

    resp = client.post("/api/followup", json={"text": "what do you think?"})
    body = resp.json()
    assert body["resolution"]["kind"] == "NEEDS_CLARIFICATION"
    assert body["analysis"] is None


def test_a_failed_analyze_query_never_overwrites_a_valid_prior_context(tmp_path: Path) -> None:
    app = _configured_app(tmp_path, provider=_provider(_router()), instrument_master=_MASTER)
    app.state.query_context = ContextState()
    client = TestClient(app)
    client.post("/api/analyze", json={"query": "RELIANCE 1300 CE"})
    client.post("/api/analyze", json={"query": "NOT_A_REAL_SYMBOL"})  # must not clobber the valid context above

    resp = client.post("/api/followup", json={"text": "what about pe"})
    body = resp.json()
    assert body["resolution"]["kind"] == "RESOLVED_QUERY"
    assert body["resolution"]["resolved_query"] == "RELIANCE 1300 PE"


# -- Product Effectiveness Patch, P1 #1: reasoning/invalidation exposure ----


def test_analyze_exposes_real_reasoning_and_never_fabricates_invalidation_without_a_thesis(tmp_path: Path) -> None:
    """Invalidation is either a REAL level from a real thesis, or absent --
    never a number produced to make the response look complete.

    Updated 18 Sep 2026: against this file's mocked chain the evidence used
    to land on CONFLICT -> NO_TRADE, and this test pinned the resulting
    empty invalidation fields. That CONFLICT was an artifact of the
    inverted `row_m15_trend` mapping (a rising series was reported as
    BEARISH, permanently contradicting the VWAP row from the same candle
    series). With the mapping corrected the same mocked data converges
    bullish, so the fixture now exercises the POPULATED side. The invariant
    under test is unchanged and is asserted directly rather than through
    one fixture's incidental outcome: a level appears if and only if a
    condition explaining it does, and the level must be a real level from
    this analysis, not an invented number. The error path's empty case is
    covered by the next test."""
    app = _configured_app(tmp_path, provider=_provider(_router()), instrument_master=_MASTER)
    client = TestClient(app)
    resp = client.post("/api/analyze", json={"query": "RELIANCE 1300 CE"})
    body = resp.json()
    assert body["error"] is None
    assert body["decision"] == "WATCH"
    assert body["reasoning"]
    assert body["reasoning"] in body["detailed_report"]

    level, condition = body["invalidation_level"], body["invalidation_condition"]
    assert (level is None) == (condition is None), "a level and its explanation must appear together or not at all"
    if level is not None:
        assert str(level) in condition, "the stated condition must name the level it was derived from"
        real_levels = {
            lv["strike"] for lv in body["visual"]["support_resistance"]["support"]
        } | {lv["strike"] for lv in body["visual"]["support_resistance"]["resistance"]}
        assert level in real_levels, "the invalidation level must be one of this analysis's own real S/R levels"


def test_analyze_reasoning_fields_are_none_not_fabricated_when_analysis_failed(tmp_path: Path) -> None:
    app = _configured_app(tmp_path, provider=_provider(_router()), instrument_master=_MASTER)
    client = TestClient(app)
    resp = client.post("/api/analyze", json={"query": "NOT_A_REAL_SYMBOL"})
    body = resp.json()
    assert body["error"] is not None
    assert body["reasoning"] is None
    assert body["invalidation_level"] is None
    assert body["invalidation_condition"] is None


def test_analyze_existing_response_fields_unchanged_by_the_patch(tmp_path: Path) -> None:
    """The patch is purely additive -- every field that existed before
    must still behave identically."""
    app = _configured_app(tmp_path, provider=_provider(_router()), instrument_master=_MASTER)
    client = TestClient(app)
    resp = client.post("/api/analyze", json={"query": "RELIANCE"})
    body = resp.json()
    assert body["symbol"] == "RELIANCE"
    assert body["error"] is None
    assert "OPTIONS INTELLIGENCE" in body["compact_report"]
    assert body["market_state"] is not None
    assert body["latency_seconds"] > 0
    assert body["watch_next"] is not None  # unaffected by this patch
