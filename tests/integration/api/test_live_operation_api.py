"""Sprint 5 — dashboard-side audit journal wiring: `/api/analyze` now
additively persists an `AnalysisSnapshot` when a journal is configured,
and two new read-only routes (`/api/status`, `/api/outcomes/{audit_id}`)
expose Phase 12/14's HISTORICAL OBSERVATIONS / LIVE OUTCOME TRACKING /
operational status views. Reuses the exact router/master/provider
fixtures from `test_dashboard_api.py` rather than duplicating them.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.data.providers.upstox_provider import UpstoxProvider
from app.persistence.jsonl_file import JsonlAuditJournalRepository
from tests.integration.api.test_dashboard_api import _MASTER, _configured_app, _provider, _router


def _app_with_journal(
    tmp_path: Path, *, provider: UpstoxProvider | None = None, instrument_master: list[dict[str, object]] | None = None
) -> FastAPI:
    app = _configured_app(tmp_path, provider=provider, instrument_master=instrument_master)
    app.state.journal = JsonlAuditJournalRepository(tmp_path / "journal")
    return app


def test_analyze_persists_a_snapshot_when_journal_is_configured(tmp_path: Path) -> None:
    app = _app_with_journal(tmp_path, provider=_provider(_router()), instrument_master=_MASTER)
    client = TestClient(app)
    resp = client.post("/api/analyze", json={"query": "RELIANCE"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["journal_status"] == "PERSISTED"
    assert body["audit_id"] is not None


def test_analyze_journal_disabled_when_no_journal_configured(tmp_path: Path) -> None:
    from tests.integration.api.test_dashboard_api import _configured_app as configured_app

    app = configured_app(tmp_path, provider=_provider(_router()), instrument_master=_MASTER)
    client = TestClient(app)
    resp = client.post("/api/analyze", json={"query": "RELIANCE"})
    body = resp.json()
    assert body["journal_status"] == "DISABLED"
    assert body["audit_id"] is None


def test_status_reports_journal_counts_with_no_secrets(tmp_path: Path) -> None:
    app = _app_with_journal(tmp_path, provider=_provider(_router()), instrument_master=_MASTER)
    client = TestClient(app)
    client.post("/api/analyze", json={"query": "RELIANCE"})
    resp = client.get("/api/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider_configured"] is True
    assert body["journal"]["analyses_count"] == 1
    assert body["journal"]["last_analysis"]["symbol"] == "RELIANCE"
    body_text = resp.text.lower()
    assert '"tok"' not in body_text
    assert "access_token" not in body_text
    assert "authorization" not in body_text


def test_status_with_no_journal_configured(tmp_path: Path) -> None:
    from tests.integration.api.test_dashboard_api import _configured_app as configured_app

    app = configured_app(tmp_path, provider=None, instrument_master=None)
    client = TestClient(app)
    resp = client.get("/api/status")
    assert resp.status_code == 200
    assert resp.json()["journal"] is None


def test_outcomes_returns_pending_checkpoints_right_after_analysis(tmp_path: Path) -> None:
    app = _app_with_journal(tmp_path, provider=_provider(_router()), instrument_master=_MASTER)
    client = TestClient(app)
    analyze_resp = client.post("/api/analyze", json={"query": "RELIANCE"})
    audit_id = analyze_resp.json()["audit_id"]

    resp = client.get(f"/api/outcomes/{audit_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "RELIANCE"
    assert len(body["checkpoints"]) == 5
    assert all(c["status"] == "PENDING" for c in body["checkpoints"])
    assert [c["label"] for c in body["checkpoints"]] == ["5m", "15m", "30m", "60m", "EOD"]


def test_watchlist_analyzes_multiple_symbols_independently(tmp_path: Path) -> None:
    """Part 4H -- each symbol keeps its own audit_id/decision/state; no
    cross-symbol contamination, no ranking."""
    app = _app_with_journal(tmp_path, provider=_provider(_router()), instrument_master=_MASTER)
    client = TestClient(app)
    resp = client.post("/api/watchlist", json={"queries": ["RELIANCE", "RELIANCE 1300 CE"]})
    assert resp.status_code == 200
    entries = resp.json()
    assert len(entries) == 2
    assert entries[0]["query"] == "RELIANCE"
    assert entries[0]["has_specific_contract"] is False
    assert entries[1]["query"] == "RELIANCE 1300 CE"
    assert entries[1]["has_specific_contract"] is True
    assert entries[1]["parsed_strike"] == "1300"
    assert entries[1]["parsed_right"] == "CE"
    # Distinct audit_ids -- two genuinely independent identities, not one shared record.
    assert entries[0]["audit_id"] != entries[1]["audit_id"]
    assert entries[0]["audit_id"] is not None and entries[1]["audit_id"] is not None


def test_watchlist_isolates_a_failing_symbol(tmp_path: Path) -> None:
    app = _app_with_journal(tmp_path, provider=_provider(_router()), instrument_master=_MASTER)
    client = TestClient(app)
    resp = client.post("/api/watchlist", json={"queries": ["NOT_A_REAL_SYMBOL", "RELIANCE"]})
    assert resp.status_code == 200
    entries = resp.json()
    assert entries[0]["error"] is not None
    assert entries[1]["error"] is None
    assert entries[1]["symbol"] == "RELIANCE"


def test_watchlist_returns_503_when_token_not_configured(tmp_path: Path) -> None:
    app = _app_with_journal(tmp_path, provider=None, instrument_master=None)
    client = TestClient(app)
    resp = client.post("/api/watchlist", json={"queries": ["RELIANCE"]})
    assert resp.status_code == 503


def test_watchlist_rejects_an_empty_query_list(tmp_path: Path) -> None:
    app = _app_with_journal(tmp_path, provider=_provider(_router()), instrument_master=_MASTER)
    client = TestClient(app)
    resp = client.post("/api/watchlist", json={"queries": []})
    assert resp.status_code == 422


def test_status_exposes_market_calendar_context(tmp_path: Path) -> None:
    """Part 4A -- `/api/status` exposes trading-day/weekend/holiday
    classification for the current IST calendar date, descriptive only."""
    app = _app_with_journal(tmp_path, provider=_provider(_router()), instrument_master=_MASTER)
    client = TestClient(app)
    resp = client.get("/api/status")
    assert resp.status_code == 200
    cal = resp.json()["market_calendar"]
    assert cal["calendar_date_ist"] is not None
    assert isinstance(cal["is_trading_day"], bool)
    assert isinstance(cal["is_weekend"], bool)
    assert isinstance(cal["is_holiday"], bool)
    assert isinstance(cal["is_special_session"], bool)
    # A trading day has no "next" (None); a non-trading day always names one.
    assert (cal["next_trading_day"] is None) == cal["is_trading_day"]


def test_outcomes_shows_ce_pe_initial_prices_before_any_checkpoint_exists(tmp_path: Path) -> None:
    """Sprint 7 bug fix -- `build_outcome_tracking()` previously only
    populated `ce_pe`/`normalized_movement` after the FIRST reconciliation
    existed, hiding the CE/PE "Initial LTP" row (already known at
    analysis time from `snapshot.contracts`) until a checkpoint sweep
    happened to run. Discovered via real-data inspection during Sprint 7's
    audit against genuinely persisted records. Regression: request a
    specific contract, immediately call `/api/outcomes/{id}` with ZERO
    checkpoints captured yet, and confirm the CE/PE table is still
    present (with every checkpoint row honestly PENDING)."""
    app = _app_with_journal(tmp_path, provider=_provider(_router()), instrument_master=_MASTER)
    client = TestClient(app)
    analyze_resp = client.post("/api/analyze", json={"query": "RELIANCE 1300 CE"})
    audit_id = analyze_resp.json()["audit_id"]
    assert audit_id is not None

    resp = client.get(f"/api/outcomes/{audit_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ce_pe"] is not None
    assert body["ce_pe"]["reference_strike"] == "1300"
    assert body["ce_pe"]["ce_initial_price"] is not None
    assert body["ce_pe"]["pe_initial_price"] is not None
    assert body["ce_pe"]["ce_thesis_status"] == "INSUFFICIENT_DATA"  # no checkpoint recorded yet -- honest, not fabricated
    assert all(c["status"] == "PENDING" for c in body["ce_pe"]["checkpoints"])
    assert len(body["normalized_movement"]) == 5
    assert all(p["ce_index"] is None for p in body["normalized_movement"])  # nothing to normalize yet


def test_outcomes_404_for_unknown_audit_id(tmp_path: Path) -> None:
    app = _app_with_journal(tmp_path, provider=_provider(_router()), instrument_master=_MASTER)
    client = TestClient(app)
    resp = client.get("/api/outcomes/does-not-exist")
    assert resp.status_code == 404


def test_outcomes_exposes_ce_pe_comparison_and_normalized_movement_after_a_checkpoint(tmp_path: Path) -> None:
    """Sprint 6, Phase 8/9/10 -- end to end through the real HTTP surface:
    request a specific CE contract, capture a real checkpoint (via the
    same `live_operation` machinery the CLI uses, against the identical
    app-state repositories/journal), then confirm `/api/outcomes/{id}`
    surfaces the backend-computed CE/PE comparison and normalized
    movement series -- never requiring the frontend to compute either."""
    from datetime import timedelta

    from app.orchestration.live_operation import LiveOperationContext, process_due_checkpoints

    app = _app_with_journal(tmp_path, provider=_provider(_router()), instrument_master=_MASTER)
    client = TestClient(app)
    analyze_resp = client.post("/api/analyze", json={"query": "RELIANCE 1300 CE"})
    audit_id = analyze_resp.json()["audit_id"]
    assert audit_id is not None

    ctx = LiveOperationContext(
        provider=app.state.provider, instrument_master=app.state.instrument_master, strategy=app.state.strategy,
        repositories=app.state.repositories, journal=app.state.journal, config=app.state.config,
    )
    snapshot = asyncio.run(app.state.journal.get_analysis(audit_id))
    # `generated_at` is the real wall-clock time (`/api/analyze` uses
    # `utc_now()`), so depending on when this test happens to run, EOD's
    # own real close-time deadline may already be due too -- both are
    # legitimately due checkpoints; this test only cares that 5m recorded.
    asyncio.run(process_due_checkpoints(audit_id, ctx=ctx, now=snapshot.identity.generated_at + timedelta(minutes=5)))

    outcomes_resp = client.get(f"/api/outcomes/{audit_id}")
    assert outcomes_resp.status_code == 200
    body = outcomes_resp.json()
    assert body["ce_pe"] is not None
    assert body["ce_pe"]["reference_strike"] == "1300"
    assert body["ce_pe"]["ce_thesis_status"] in ("CONFIRMED", "INVALIDATED", "MIXED")
    five_min = next(c for c in body["ce_pe"]["checkpoints"] if c["checkpoint_label"] == "5m")
    assert five_min["status"] == "RECORDED"
    assert len(body["normalized_movement"]) == 5
    five_min_norm = next(p for p in body["normalized_movement"] if p["checkpoint_label"] == "5m")
    assert five_min_norm["ce_index"] is not None


def test_status_exposes_ce_pe_underlying_observation_counts(tmp_path: Path) -> None:
    app = _app_with_journal(tmp_path, provider=_provider(_router()), instrument_master=_MASTER)
    client = TestClient(app)
    resp = client.get("/api/status")
    body = resp.json()
    assert body["journal"]["observations"] == {"underlying": 0, "ce": 0, "pe": 0}


def test_outcomes_503_when_no_journal_configured(tmp_path: Path) -> None:
    from tests.integration.api.test_dashboard_api import _configured_app as configured_app

    app = configured_app(tmp_path, provider=None, instrument_master=None)
    client = TestClient(app)
    resp = client.get("/api/outcomes/anything")
    assert resp.status_code == 503


def test_dashboard_html_includes_the_new_sprint5_sections() -> None:
    """Phase 12/13 -- the dashboard page itself must carry the new
    CURRENT ANALYSIS / LIVE OUTCOME TRACKING / HISTORICAL OBSERVATIONS
    section labels and the query-identity row, not just backend support."""
    import app.api.main as main_module

    static_dir = Path(main_module.__file__).resolve().parent / "static"
    html = (static_dir / "index.html").read_text(encoding="utf-8")
    assert "CURRENT ANALYSIS" in html
    assert "LIVE OUTCOME TRACKING" in html
    assert "HISTORICAL OBSERVATIONS" in html
    assert "query-identity-row" in html
    assert "renderOutcomeTracking" in html
    assert "refreshJournalStatus" in html


def test_dashboard_html_includes_the_watchlist_panel() -> None:
    import app.api.main as main_module

    static_dir = Path(main_module.__file__).resolve().parent / "static"
    html = (static_dir / "index.html").read_text(encoding="utf-8")
    assert "watchlist-query" in html
    assert "runWatchlist" in html
    assert "/api/watchlist" in html


def test_dashboard_html_includes_the_ipo_intelligence_section() -> None:
    """The IPO dashboard milestone -- a real, separate IPO section reusing
    the existing /api/ipo/* responses verbatim (no new analysis logic in
    this file)."""
    import app.api.main as main_module

    static_dir = Path(main_module.__file__).resolve().parent / "static"
    html = (static_dir / "index.html").read_text(encoding="utf-8")
    assert "ipo-query" in html
    assert "runIpoAnalyze" in html
    assert "runIpoCompare" in html
    assert "/api/ipo/analyze" in html
    assert "/api/ipo/compare" in html
    assert "/api/ipo/journal/" in html
    assert "loadIpoHistory" in html
    assert "renderHiddenOpportunity" in html
    assert "GMP is unofficial" in html or "unofficial, always caller-supplied" in html


def test_dashboard_html_includes_what_changed_watch_next_and_followup_sections() -> None:
    """Master Product Grooming Sprint, Sections 6/7/8/9/10 -- the
    deterministic follow-up box and the What Changed?/Watch Next panels,
    all backend-data-driven (no new analysis logic in this file)."""
    import app.api.main as main_module

    static_dir = Path(main_module.__file__).resolve().parent / "static"
    html = (static_dir / "index.html").read_text(encoding="utf-8")
    assert "followup-query" in html
    assert "runFollowup" in html
    assert "/api/followup" in html
    assert "panel-what-changed" in html
    assert "renderWhatChanged" in html
    assert "panel-watch-next" in html
    assert "renderWatchNext" in html
    assert "Not enough prior observations to describe a change" in html


def test_dashboard_html_final_assessment_renders_reasoning_and_invalidation_without_expanding_detail() -> None:
    """Product Effectiveness Patch, P1 #1 -- WHY/INVALIDATION are rendered
    directly inside `renderFinalAssessment()`, not only inside the
    collapsed detailed-report `<pre>`."""
    import app.api.main as main_module

    static_dir = Path(main_module.__file__).resolve().parent / "static"
    html = (static_dir / "index.html").read_text(encoding="utf-8")
    final_assessment_fn = html.split("function renderFinalAssessment(data) {", 1)[1].split("\nfunction ", 1)[0]
    assert "data.reasoning" in final_assessment_fn
    assert "data.invalidation_level" in final_assessment_fn
    assert "data.invalidation_condition" in final_assessment_fn
    assert '"DECISION REASONING"' in final_assessment_fn
    assert '"INVALIDATION"' in final_assessment_fn
    assert 'el("div", { class: "k", text: "WHY" })' in html


def test_dashboard_html_ipo_analysis_renders_hidden_opportunity_and_drops_the_stale_hint() -> None:
    """Product Effectiveness Patch, P1 #2 -- the single-IPO panel now
    renders `data.hidden_opportunity` directly and no longer claims it is
    "not computed on a single-IPO analysis" (that claim became false once
    the backend started returning it)."""
    import app.api.main as main_module

    static_dir = Path(main_module.__file__).resolve().parent / "static"
    html = (static_dir / "index.html").read_text(encoding="utf-8")
    render_ipo_fn = html.split("function renderIpoAnalysisResult(data) {", 1)[1].split("\nfunction ", 1)[0]
    assert "data.hidden_opportunity" in render_ipo_fn
    assert "renderHiddenOpportunity(data.hidden_opportunity)" in render_ipo_fn
    assert "it is not computed on a single-IPO analysis" not in html


def test_dashboard_html_usability_pass_fixes_are_present() -> None:
    """Final Pre-Live Product Usability Pass -- table overflow containment,
    data_completeness shown as a labeled percentage (never a bare decimal
    that could read as a ranking), shareholder-quota null-state clarity,
    and an example-query hint on an analysis error. All pure presentation,
    no value/ordering/analysis change."""
    import app.api.main as main_module

    static_dir = Path(main_module.__file__).resolve().parent / "static"
    html = (static_dir / "index.html").read_text(encoding="utf-8")

    assert ".panel { background: var(--panel); border: 1px solid var(--border); border-radius: 5px; padding: 14px 16px; margin-bottom: 14px; overflow-x: auto; }" in html

    assert "Data completeness: \" + (completenessPct" in html
    assert "not a quality score or ranking" in html
    assert '"  (data completeness: " + entry.comparison.data_completeness + ")"' not in html

    assert "Not evaluated -- no shareholder-quota data was available or supplied for this query." in html

    assert "This box only accepts a market symbol, optionally with a strike and CE/PE -- not a question." in html
    assert "RELIANCE 1300 PE" in html


def test_dashboard_html_progressive_disclosure_quick_view_and_expert_view() -> None:
    """Beginner/Intermediate UX Pass -- a Quick View (final assessment,
    simplified CE/PE, condensed evidence, watch-next) sits outside the
    collapsible, and the full technical panels (chart, chain, OI, IV,
    futures/global, requested contract, decay, support/resistance, full
    evidence table, adversarial, what-changed, quality, detailed report)
    are nested inside one <details id="expert-view">, reusing the exact
    same existing panel ids/renderers -- nothing deleted, nothing
    recomputed."""
    import app.api.main as main_module

    static_dir = Path(main_module.__file__).resolve().parent / "static"
    html = (static_dir / "index.html").read_text(encoding="utf-8")

    quick_view = html.split('<h2 class="section-label">QUICK VIEW', 1)[1].split('<details id="expert-view">', 1)[0]
    assert "panel-final" in quick_view
    assert "panel-quick-cepe" in quick_view
    assert "panel-quick-evidence" in quick_view
    assert "panel-watch-next" in quick_view

    assert '<details id="expert-view">' in html
    assert '<details id="expert-view" open' not in html
    assert "View Evidence" in html
    expert_view = html.split('<details id="expert-view">', 1)[1].split("</section>", 1)[0]
    for panel_id in [
        "panel-chart", "panel-chain", "panel-oi", "panel-iv", "panel-global", "panel-news",
        "panel-direction", "panel-requested", "panel-alternatives", "panel-decay", "panel-sr",
        "panel-evidence", "panel-adversarial", "panel-what-changed", "panel-quality", "panel-detail",
    ]:
        assert panel_id in expert_view, f"{panel_id} missing from expert-view -- was it deleted instead of relocated?"
    # watch-next moved OUT of expert view into Quick View -- must not be duplicated inside it.
    assert "panel-watch-next" not in expert_view

    assert "function renderQuickCePe(" in html
    assert "function renderQuickEvidence(" in html
    assert "EVIDENCE LEANS SUPPORTIVE" in html
    # Analytical Correctness Pass -- "NO CLEAR EDGE" (directional-only)
    # replaced the old "AVOID / NO CLEAR EDGE" wording, which collapsed
    # contract quality and directional edge into one misleading word.
    assert "NO CLEAR EDGE" in html
    assert "CONTRACT QUALITY" in html
    assert "ce_case" in html and "pe_case" in html
    assert "ce_assessment" in html and "pe_assessment" in html
    assert "NO_TRADE" in html and "Prefer NO TRADE over unsupported actionability" in html
    # Product-Level Analytical Audit, Phase 11 -- ACTIONABILITY,
    # CONFIRMATION REQUIRED, and DATA FRESHNESS now render directly in the
    # Quick View CE/PE panel, not only cross-referenced by name.
    assert "COMPATIBILITY DECISION: " in html
    assert "RESEARCH STATE: " in html
    assert "DATA FRESHNESS: " in html
    assert "function freshnessState(" in html
    # Final Pre-Freeze Product Analytical Pass -- ACTIONABILITY-STATE
    # taxonomy (Objective 2), "WHY NOT CONFIRMED" (Objective 3),
    # contract-specific "WHAT WOULD CHANGE THIS" (Objective 4), and the
    # closed-market "NEXT SESSION" preparation block (Objective 5) --
    # all composed from already-existing fields, no new backend state.
    assert "function classifyContractState(" in html
    assert "function buildWhyNotActionableNow(" in html
    assert "function findRelevantWatchCondition(" in html
    assert "STATE: " in html
    assert "MISSING CONFIRMATION: " in html
    assert "WHAT WOULD CHANGE THIS: " in html
    assert "SAMPLE BREADTH" in html
    assert "COMPATIBLE (NOT AN ORDER)" in html
    assert "WHY NOT CONFIRMED: " in html
    assert "NEXT SESSION -- WHAT TO RECHECK, NOT A PREDICTION" in html
    assert "POOR CONTRACT" in html
    assert "HEALTHY CONTRACT / NO DIRECTION" in html
    assert "HEALTHY CONTRACT / CONFLICTED" in html
    assert "HEALTHY CONTRACT / WATCH FOR CONFIRMATION" in html
    assert "DATA UNAVAILABLE / STALE" in html
    # Required move vs contractual breakeven now surfaced in Quick View
    # too (previously Expert View only), explicitly distinguished.
    assert "Contractual expiry breakeven (strike" in html
    assert "these are two different questions, not the same number" in html
    # Final Pre-Freeze Product Analytical Pass -- Issue 1 (closed-market
    # decision status prominence + demoted raw seconds), Issue 3 (primary
    # vs secondary evidence split), Issue 4 (underlying move to
    # contractual breakeven), Issue 5 (nearby-strike trade-offs).
    assert "CURRENT DECISION STATUS: MARKET CLOSED -- HISTORICAL SNAPSHOT ONLY" in html
    assert "function summarizeGroupDirection(" in html
    assert "PRIMARY DIRECTION (price action" in html
    assert "SECONDARY POSITIONING (options OI/PCR, IV, futures, global context)" in html
    assert "function underlyingMoveToBreakeven(" in html
    assert "Underlying move from current spot to contractual expiry breakeven" in html
    assert "pure expiry arithmetic, not a profitability forecast" in html
    assert "function dominanceVerdict(" in html
    assert "NEARBY CONTRACT TRADE-OFFS" in html
    assert "NO CLEAR DOMINANCE" in html
    # Trader Decision-Support Audit, Audit 5 (HIGH PRIORITY) -- "dominates"
    # was reworded to an explicit "BETTER ON N OF M COMPARED DIMENSIONS"
    # so a narrow (5-dimension) comparison is never misread as universal
    # superiority; the dimension set grew from 3 to 5 (added decay
    # viability and distance-to-contractual-breakeven, both real ordinal/
    # numeric fields already computed elsewhere on this page).
    assert "BETTER ON" in html and "COMPARED DIMENSIONS" in html
    assert "function dominanceVerdict(" in html
    assert "_DECAY_ORDINAL" in html
    assert "function extractSpot(" in html


def test_dashboard_html_daily_research_section_is_wired() -> None:
    """Daily Market Researcher UI -- Section 15's spec: date/status
    header, DEVELOPING SETUPS cards (or ZERO VALID DEVELOPING SETUPS),
    RESEARCH SUMMARY counts, and the "why not these?" rejection list,
    all rendered from the real `/api/research/daily` JSON shape (no
    separate ranking computed in this file)."""
    import app.api.main as main_module

    static_dir = Path(main_module.__file__).resolve().parent / "static"
    html = (static_dir / "index.html").read_text(encoding="utf-8")

    assert 'id="daily-research"' in html
    assert 'id="research-symbols"' in html
    assert 'id="research-btn"' in html
    assert "function runDailyResearch(" in html
    assert "/api/research/jobs/discover" in html
    assert "Ready to scan the market" in html
    assert "Explain simply" in html
    assert "DEVELOPING NOW" in html
    assert "EVENTS" in html
    assert "YOUR OPEN DECISIONS" in html
    assert "View Evidence" in html
    assert "function researchCardHeadlineState(" in html
    assert "Sector classification unavailable" in html or "o.sector_note" in html
    assert "Nothing compelling is developing" in html
    assert "NO HIGH-CONVICTION STRUCTURAL SHORTLIST TODAY" in html
    assert "TOP OPPORTUNITIES" not in html
    assert "RESEARCH SUMMARY" in html
    assert "Why not these?" in html
    assert "WHY SELECTED (confirming evidence)" in html
    assert "MAIN RISK (opposing evidence)" in html
    assert "WHAT WOULD CONFIRM:" in html
    assert "WHAT INVALIDATES:" in html
    assert "not a recommendation" in html
    # Research-deepening phase -- Objective 3 (both-side case), Objective
    # 7 (rejection-reason tally), Objective 8 (research strength shown
    # visibly separate from the real decision, never merged).
    assert "BULLISH vs BEARISH case (both sides, never CE-biased)" in html
    assert "EVIDENCE COMPLETENESS" in html
    assert "FINAL DECISION" in html
    assert "Rejection reasons (real, counted)" in html
    assert "Stage-1 survivors" in html
    assert "WHY THIS CONTRACT:" in html
    # Early-stage discovery phase -- Objective (early-stage state,
    # extension/room-to-move, event risk, sector-unavailable honesty),
    # all kept as visibly separate badges/lines from research strength
    # and the real decision, never merged into one.
    assert "EARLY-STAGE STATE" in html
    assert "WHAT IS DEVELOPING:" in html
    assert "WHAT HAS NOT HAPPENED YET:" in html
    assert "WHY NOT ALREADY EXTENDED:" in html
    assert "NEAREST IMPORTANT LEVEL:" in html
    assert "EVENT RISK" in html
    assert "o.sector_note" in html  # the literal unavailable text is real backend data, not hardcoded in this file
    assert "Why interesting" in html
    assert "Main blocker" in html
    assert "Confirm when" in html
    assert "Invalidated if" in html
    assert "F&O ban list: unknown" in html


def test_dashboard_html_input_and_followup_example_chips_are_wired() -> None:
    import app.api.main as main_module

    static_dir = Path(main_module.__file__).resolve().parent / "static"
    html = (static_dir / "index.html").read_text(encoding="utf-8")
    assert 'data-example-query="RELIANCE"' in html
    assert 'data-example-query="RELIANCE 1300 CE"' in html
    assert 'data-example-query="NIFTY"' in html
    assert 'data-example-query="BANKNIFTY"' in html
    assert 'data-example-followup="what about pe"' in html
    assert "querySelectorAll(\"#query-examples .chip\")" in html
    assert "querySelectorAll(\"#followup-examples .chip\")" in html
    assert "ASK ABOUT THIS ANALYSIS" in html


def test_dashboard_html_netlify_readiness_api_base() -> None:
    """Every fetch() call must go through apiUrl() -- a raw fetch("/api/...
    would silently break once this file is hosted on a different origin
    than the backend."""
    import re

    import app.api.main as main_module

    static_dir = Path(main_module.__file__).resolve().parent / "static"
    html = (static_dir / "index.html").read_text(encoding="utf-8")
    assert '<meta name="api-base" content="">' in html
    assert "function apiUrl(path)" in html
    raw_fetches = re.findall(r'fetch\("/api', html)
    assert not raw_fetches, f"found {len(raw_fetches)} fetch() call(s) bypassing apiUrl()"
    # Sprint 5 -- +1 for RESEARCH HISTORY. Early-opportunity pass -- +1
    # for POST /api/journal/personal (LOAD builds apiUrl() into a local
    # `url` then fetch(url), so it does not increment this exact count).
    # Final 95% sprint -- +1 for GET /api/research/patterns (PATTERN
    # HISTORY); this guard caught it correctly and it does route through
    # apiUrl(), which is the property actually under test here.
    # Release gate -- +1 for GET /api/research/replay-dataset (COMPARE
    # HISTORICAL OBSERVATIONS), which also routes through apiUrl().
    assert html.count('fetch(apiUrl(') == 18


def test_dashboard_html_does_not_infer_timing_or_confuse_analysis_clock() -> None:
    """Release hardening -- Symbol workspace must print API timing and
    market observation time, never invent a stage in JavaScript or label
    generated_at as LAST OBSERVED."""
    import app.api.main as main_module

    static_dir = Path(main_module.__file__).resolve().parent / "static"
    html = (static_dir / "index.html").read_text(encoding="utf-8")
    assert "function workspaceTiming(data)" in html
    assert "if (data.timing_stage) return data.timing_stage;" in html
    assert "LAST MARKET OBSERVATION" in html
    assert "data.market_observed_at" in html
    assert "Insufficient evidence to classify timing." in html
    assert "EXPLICIT · ANALYSING" in html or "SELECTED SYMBOLS" in html
    assert "F&O UNIVERSE" in html
    assert "NO LATEST MARKET SCAN" in html
    assert "Indian Market Options Intelligence" in html
    assert "What TIRE has observed" in html
    assert "surface-workspace" in html
    assert "ANALYSIS GENERATED" in html
    assert "if (analyzingQuery !== wanted)" in html
    # Per-stream glance times come from the observed-market payload.
    assert "cell.observed_at_ist" in html
    assert "cell.freshness_label" in html


def test_dashboard_html_includes_the_sprint6_ce_pe_and_chart_sections() -> None:
    """Phase 8/9/10 -- the CE-vs-PE side-by-side table and the observed-
    movement chart, both backend-data-driven."""
    import app.api.main as main_module

    static_dir = Path(main_module.__file__).resolve().parent / "static"
    html = (static_dir / "index.html").read_text(encoding="utf-8")
    assert "renderCePeTable" in html
    assert "renderNormalizedMovementChart" in html
    assert "OBSERVED MOVEMENT -- NOT A PROFITABILITY FORECAST" in html


def test_dashboard_html_market_state_labels_cover_every_real_enum_value() -> None:
    """Final Product Polish -- MARKET_STATE_LABELS must translate EVERY
    real `MarketDataState` value (never a partial table that silently
    falls back to a raw enum string for an untranslated real state)."""
    import app.api.main as main_module
    from app.domain.market.data_state import MarketDataState

    static_dir = Path(main_module.__file__).resolve().parent / "static"
    html = (static_dir / "index.html").read_text(encoding="utf-8")
    labels_block = html.split("const MARKET_STATE_LABELS = {", 1)[1].split("};", 1)[0]
    for state in MarketDataState:
        assert f"{state.value}:" in labels_block, f"{state.value} missing from MARKET_STATE_LABELS -- would silently show the raw enum"


def test_dashboard_html_prominent_price_reuses_the_existing_candle_close_only() -> None:
    """The new prominent price line must read the SAME last-candle-close
    value the rest of the panel already used -- no new field invented."""
    import app.api.main as main_module

    static_dir = Path(main_module.__file__).resolve().parent / "static"
    html = (static_dir / "index.html").read_text(encoding="utf-8")
    render_fn = html.split("function renderUnderlyingSummary(data) {", 1)[1].split("\nfunction ", 1)[0]
    assert "lastCandle ? \"₹\" + fmt(lastCandle.close)" in render_fn
    assert "data.spot" not in render_fn  # no such field exists on AnalyzeResponse -- must never be invented
    assert "data.day_change" not in render_fn


def test_dashboard_html_invalidation_unavailable_explains_why() -> None:
    import app.api.main as main_module

    static_dir = Path(main_module.__file__).resolve().parent / "static"
    html = (static_dir / "index.html").read_text(encoding="utf-8")
    assert "no actionable candidate was selected for this analysis" in html


def test_dashboard_html_followup_shows_an_explicit_resolved_query_label() -> None:
    """The resolved query must be its own labeled field, not only
    embedded inside the free-text resolution message."""
    import app.api.main as main_module

    static_dir = Path(main_module.__file__).resolve().parent / "static"
    html = (static_dir / "index.html").read_text(encoding="utf-8")
    followup_fn = html.split("async function runFollowup() {", 1)[1].split("\nfunction ", 1)[0]
    assert 'if (r.resolved_query) {' in followup_fn
    assert '"Resolved query"' in followup_fn


def test_explain_simply_is_disabled_until_research_then_falls_back() -> None:
    """GREEN-gate regression: Explain simply stays disabled on load, is
    enabled after a successful renderDashboard, and Qwen unavailability
    shows the deterministic TIRE summary instead of hanging."""
    import app.api.main as main_module

    html = (Path(main_module.__file__).resolve().parent / "static" / "index.html").read_text(encoding="utf-8")
    assert 'id="explain-simply-btn"' in html
    assert 'data-explain-ready="0"' in html
    assert "function enableExplainSimply(" in html
    assert 'btn.setAttribute("data-explain-ready", "1")' in html
    render = html.split("function renderDashboard(data) {", 1)[1].split("\nfunction ", 1)[0]
    assert render.index("enableExplainSimply()") < render.index("renderUnderlyingSummary")
    assert "requestAiExplain(data)" not in render
    explain = html.split("async function requestAiExplain(data) {", 1)[1].split("\nfunction ", 1)[0]
    # Release gate (Section 17): the two provenance labels are explicit, and
    # the deterministic fallback is never labelled as AI output.
    assert "Qwen unavailable — deterministic explanation shown" in explain
    assert "Simplified by local Qwen 2.5" in explain
    assert "no AI was used for this explanation" in explain
    assert "It did not find this setup" in explain
    assert "AI explanation based on TIRE evidence" not in explain
    assert "In simple terms" in explain
    assert "Why it matters" in explain
    assert "What's missing" in explain
    assert "What confirms it" in explain
    assert "What invalidates it" in explain
    markup, _, _script = html.partition("<script>")
    assert markup.index('id="panel-ai-explain"') < markup.index('id="report"')
    assert "futures.no_futures_reason" in html
    assert "Futures: " in html
    assert "Futures unavailable." not in html
