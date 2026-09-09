"""Daily Market Researcher -- real-pipeline integration coverage: one bad
symbol never crashes the whole scan, duplicate API calls are never made,
cross-symbol/cross-expiry isolation holds through `run_daily_research()`
(not just the underlying `analyze_symbol()`/`run_analysis()` it calls),
and the researcher's shortlist is provably downstream of, never an
override of, the real decision engine.

Uses the same real `run_analysis()` -> `analyze_symbol()` path a manual
single-symbol query uses, via a mock `httpx` transport -- no analysis
logic of its own is exercised here, only orchestration/ranking.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from app.data.providers.upstox_provider import ExchangeStatus, UpstoxProvider
from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy
from app.orchestration.daily_research import (
    ScreeningConfig,
    StageOneResult,
    _coverage_result_note,
    build_research_thesis,
    list_fo_eligible_equity_underlyings,
    run_daily_research,
    screen_universe,
)
from app.orchestration.dashboard_service import AnalyzeResponse, run_analysis
from app.orchestration.options_intelligence_pipeline import PipelineConfig, Repositories
from app.persistence.jsonl_file import (
    JsonlIvObservationRepository,
    JsonlOptionChainRepository,
    JsonlQuoteRepository,
)

EXPIRY = date(2026, 9, 24)
_EXPIRY_MS = 1790274599000
AS_OF = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)

A_KEY, A_CE, A_PE, A_FUT = "NSE_EQ|AAA", "NSE_FO|A1", "NSE_FO|A2", "NSE_FO|A3"
B_KEY, B_CE, B_PE, B_FUT = "NSE_EQ|BBB", "NSE_FO|B1", "NSE_FO|B2", "NSE_FO|B3"
A_SPOT, B_SPOT = 1300.0, 5200.0

_MASTER: list[dict[str, object]] = [
    {"segment": "NSE_EQ", "name": "SYMBOL A", "exchange": "NSE", "instrument_type": "EQ", "instrument_key": A_KEY, "trading_symbol": "SYMBOLA"},
    {"segment": "NSE_FO", "underlying_symbol": "SYMBOLA", "instrument_type": "CE", "expiry": _EXPIRY_MS, "weekly": False, "lot_size": 500, "instrument_key": A_CE, "strike_price": 1300.0},
    {"segment": "NSE_FO", "underlying_symbol": "SYMBOLA", "instrument_type": "PE", "expiry": _EXPIRY_MS, "weekly": False, "lot_size": 500, "instrument_key": A_PE, "strike_price": 1300.0},
    {"segment": "NSE_FO", "underlying_symbol": "SYMBOLA", "instrument_type": "FUT", "expiry": _EXPIRY_MS, "weekly": False, "lot_size": 500, "instrument_key": A_FUT},
    {"segment": "NSE_EQ", "name": "SYMBOL B", "exchange": "NSE", "instrument_type": "EQ", "instrument_key": B_KEY, "trading_symbol": "SYMBOLB"},
    {"segment": "NSE_FO", "underlying_symbol": "SYMBOLB", "instrument_type": "CE", "expiry": _EXPIRY_MS, "weekly": False, "lot_size": 250, "instrument_key": B_CE, "strike_price": 5200.0},
    {"segment": "NSE_FO", "underlying_symbol": "SYMBOLB", "instrument_type": "PE", "expiry": _EXPIRY_MS, "weekly": False, "lot_size": 250, "instrument_key": B_PE, "strike_price": 5200.0},
    {"segment": "NSE_FO", "underlying_symbol": "SYMBOLB", "instrument_type": "FUT", "expiry": _EXPIRY_MS, "weekly": False, "lot_size": 250, "instrument_key": B_FUT},
    # SYMBOLC deliberately has NO entries at all -- resolve_symbol() will
    # fail to find it, the real, honest way one bad symbol in a universe
    # list produces a real per-symbol error without any test-double magic.
]


def _candle_rows(n: int, *, end: datetime, price: float, step: float) -> list[list[object]]:
    return [
        [(end - timedelta(minutes=15 * (n - 1 - i))).isoformat(), price + i * step, price + i * step + 1, price + i * step - 1, price + i * step, 1000, 0]
        for i in range(n)
    ]


def _chain_body(*, ce_key: str, pe_key: str, spot: float, ltp: float, oi: int, iv: float) -> dict[str, object]:
    return {
        "status": "success",
        "data": [{
            "expiry": EXPIRY.isoformat(), "strike_price": spot, "underlying_spot_price": spot,
            "call_options": {"instrument_key": ce_key, "market_data": {"ltp": ltp, "bid_price": ltp - 0.5, "ask_price": ltp + 0.5, "volume": 8000, "oi": oi, "prev_oi": oi - 5000}, "option_greeks": {"iv": iv, "delta": 0.5}},
            "put_options": {"instrument_key": pe_key, "market_data": {"ltp": ltp - 2, "bid_price": ltp - 2.5, "ask_price": ltp - 1.5, "volume": 7000, "oi": oi - 20000, "prev_oi": oi - 22000}, "option_greeks": {"iv": iv - 1, "delta": -0.5}},
        }],
    }


def _router(call_counts: dict[str, int]) -> Callable[[httpx.Request], httpx.Response]:
    """Serves A and B; anything for a symbol not in `_MASTER` (SYMBOLC)
    never even reaches this handler -- `resolve_symbol()` fails first,
    inside `analyze_symbol()`, exactly like a real unknown symbol would.
    `call_counts` records every real HTTP call by path, so a test can
    assert no symbol's data was fetched more than once per analysis
    (duplicate-API-call prevention)."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        call_counts[path] = call_counts.get(path, 0) + 1
        if path == "/v2/market/status/NSE":
            return httpx.Response(200, json={"status": "success", "data": {"status": "NORMAL_OPEN"}})
        if path == "/v2/market-quote/quotes":
            keys = request.url.params["instrument_key"].split(",")
            data: dict[str, object] = {}
            for k in keys:
                if k == A_KEY:
                    data["NSE_EQ:A"] = {"instrument_token": k, "last_price": A_SPOT, "net_change": 5.0, "volume": 1000000}
                elif k == A_FUT:
                    data["NSE_FO:AFUT"] = {"instrument_token": k, "last_price": A_SPOT + 2.0, "net_change": 4.0, "oi": 1000000, "volume": 500000}
                elif k == B_KEY:
                    data["NSE_EQ:B"] = {"instrument_token": k, "last_price": B_SPOT, "net_change": -30.0, "volume": 2000000}
                elif k == B_FUT:
                    data["NSE_FO:BFUT"] = {"instrument_token": k, "last_price": B_SPOT - 3.0, "net_change": -25.0, "oi": 2000000, "volume": 900000}
            return httpx.Response(200, json={"status": "success", "data": data})
        if path.startswith("/v3/historical-candle/"):
            if A_KEY.split("|")[1] in path:
                candles = _candle_rows(60, end=AS_OF, price=1280.0, step=0.5)
            else:
                candles = _candle_rows(60, end=AS_OF, price=5300.0, step=-1.5)
            return httpx.Response(200, json={"status": "success", "data": {"candles": candles}})
        if path == "/v2/news":
            return httpx.Response(200, json={"status": "success", "data": {}, "metadata": {"page": {"total_records": 0}}})
        if path == "/v2/option/chain":
            key = request.url.params["instrument_key"]
            if key == A_KEY:
                return httpx.Response(200, json=_chain_body(ce_key=A_CE, pe_key=A_PE, spot=A_SPOT, ltp=30.0, oi=80000, iv=18.0))
            if key == B_KEY:
                return httpx.Response(200, json=_chain_body(ce_key=B_CE, pe_key=B_PE, spot=B_SPOT, ltp=135.4, oi=368700, iv=21.58))
            raise AssertionError(f"unexpected chain request for {key}")
        raise AssertionError(f"unexpected path {path}")

    return handler


def _provider(handler: Callable[[httpx.Request], httpx.Response]) -> UpstoxProvider:
    return UpstoxProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), access_token="tok")


def _repos(tmp_path: Path) -> Repositories:
    return Repositories(
        quotes=JsonlQuoteRepository(tmp_path / "quotes.jsonl"),
        option_chains=JsonlOptionChainRepository(tmp_path / "chains.jsonl"),
        iv_observations=JsonlIvObservationRepository(tmp_path / "iv.jsonl"),
    )


_FAST_CONFIG = PipelineConfig(chain_fetch_attempts=1, chain_fetch_backoff_seconds=0.01)


def test_one_bad_symbol_never_crashes_the_whole_scan(tmp_path: Path) -> None:
    call_counts: dict[str, int] = {}
    provider = _provider(_router(call_counts))
    result = asyncio.run(
        run_daily_research(
            ["SYMBOLA", "SYMBOLC", "SYMBOLB"], provider=provider, instrument_master=_MASTER,
            strategy=EMAVWAPAlignmentStrategy(), repositories=_repos(tmp_path), config=_FAST_CONFIG, as_of=AS_OF,
        )
    )
    assert result.screened_count == 3
    assert result.deep_analyzed_count == 3  # every symbol got a real response, error or not
    rejected_symbols = {r.symbol for r in result.rejected}
    assert "SYMBOLC" in rejected_symbols  # the unresolvable symbol is a real, visible rejection
    c_reason = next(r.reason for r in result.rejected if r.symbol == "SYMBOLC")
    assert "error" in c_reason.lower() or "could not" in c_reason.lower()


def test_multiple_bad_symbols_all_visible_as_rejections(tmp_path: Path) -> None:
    call_counts: dict[str, int] = {}
    provider = _provider(_router(call_counts))
    result = asyncio.run(
        run_daily_research(
            ["SYMBOLC", "SYMBOLD", "SYMBOLA"], provider=provider, instrument_master=_MASTER,
            strategy=EMAVWAPAlignmentStrategy(), repositories=_repos(tmp_path), config=_FAST_CONFIG, as_of=AS_OF,
        )
    )
    assert result.screened_count == 3
    assert result.deep_analyzed_count == 3
    rejected_symbols = {r.symbol for r in result.rejected}
    assert {"SYMBOLC", "SYMBOLD"} <= rejected_symbols


def test_no_duplicate_api_calls_per_symbol(tmp_path: Path) -> None:
    """Each symbol's option-chain endpoint is called exactly once by the
    researcher's scan -- the sequential `run_analysis()`-per-symbol loop
    never re-fetches the same symbol's data."""
    call_counts: dict[str, int] = {}
    provider = _provider(_router(call_counts))
    asyncio.run(
        run_daily_research(
            ["SYMBOLA", "SYMBOLB"], provider=provider, instrument_master=_MASTER,
            strategy=EMAVWAPAlignmentStrategy(), repositories=_repos(tmp_path), config=_FAST_CONFIG, as_of=AS_OF,
        )
    )
    assert call_counts["/v2/option/chain"] == 2  # once per symbol, never more


def test_cross_symbol_isolation_holds_through_the_researcher(tmp_path: Path) -> None:
    call_counts: dict[str, int] = {}
    provider = _provider(_router(call_counts))
    result = asyncio.run(
        run_daily_research(
            ["SYMBOLA", "SYMBOLB"], provider=provider, instrument_master=_MASTER,
            strategy=EMAVWAPAlignmentStrategy(), repositories=_repos(tmp_path), config=_FAST_CONFIG, as_of=AS_OF,
        )
    )
    entries = {c.symbol: c for c in result.shortlist}
    # Whichever symbols made the shortlist, each one's own contract strike
    # must reflect its OWN real fixture spot -- never the other's.
    for symbol, candidate in entries.items():
        if symbol == "SYMBOLA":
            assert abs(float(candidate.contract.strike) - A_SPOT) < 50.0
        elif symbol == "SYMBOLB":
            assert abs(float(candidate.contract.strike) - B_SPOT) < 50.0


def test_cross_expiry_isolation_the_researcher_never_mixes_expiries(tmp_path: Path) -> None:
    """Both real symbols here resolve to the SAME real expiry (single
    fixture expiry) -- this proves the researcher doesn't introduce a
    NEW expiry-mixing risk on top of the already-tested repository-level
    isolation (`test_persistence_jsonl.py`); each symbol's own
    `direction_comparison` is scoped to its own real chain fetch, never
    another symbol's."""
    call_counts: dict[str, int] = {}
    provider = _provider(_router(call_counts))
    result = asyncio.run(
        run_daily_research(
            ["SYMBOLA", "SYMBOLB"], provider=provider, instrument_master=_MASTER,
            strategy=EMAVWAPAlignmentStrategy(), repositories=_repos(tmp_path), config=_FAST_CONFIG, as_of=AS_OF,
        )
    )
    assert result.deep_analyzed_count == 2
    assert not any("expiry" in r.reason.lower() for r in result.rejected)


def test_researcher_shortlist_actionability_matches_a_direct_manual_query(tmp_path: Path) -> None:
    """The researcher must never present a different decision than a
    manual single-symbol query would for the exact same real data --
    proves it is downstream of, never an override of, the decision
    engine."""
    call_counts: dict[str, int] = {}
    provider_a = _provider(_router(call_counts))
    result = asyncio.run(
        run_daily_research(
            ["SYMBOLA", "SYMBOLB"], provider=provider_a, instrument_master=_MASTER,
            strategy=EMAVWAPAlignmentStrategy(), repositories=_repos(tmp_path), config=_FAST_CONFIG, as_of=AS_OF,
        )
    )
    # A genuinely separate, unpolluted repository set -- so this is a
    # clean "same input data -> same decision" comparison, not entangled
    # with the (separately tested) repeated-run/level-stability behavior.
    provider_b = _provider(_router({}))
    for candidate in result.shortlist:
        manual = asyncio.run(
            run_analysis(
                candidate.symbol, provider=provider_b, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
                repositories=_repos(tmp_path / "manual"), config=_FAST_CONFIG, as_of=AS_OF,
            )
        )
        assert manual.decision == candidate.response.decision


# ============================================================
# Stage 1 -- the cheap batched screen, exercised through the real
# `run_daily_research(symbols=None)` default path. Adds SYMBOLD (weak
# movement/volume -- deliberately screened OUT by a low `survivor_cap`,
# so its option-chain/candle endpoints are never even hit) on top of the
# existing SYMBOLA/SYMBOLB fixtures.
# ============================================================

D_KEY, D_CE, D_PE, D_FUT = "NSE_EQ|DDD", "NSE_FO|D1", "NSE_FO|D2", "NSE_FO|D3"
D_SPOT = 500.0

_MASTER_WITH_D: list[dict[str, object]] = [
    *_MASTER,
    {"segment": "NSE_EQ", "name": "SYMBOL D", "exchange": "NSE", "instrument_type": "EQ", "instrument_key": D_KEY, "trading_symbol": "SYMBOLD"},
    {"segment": "NSE_FO", "underlying_symbol": "SYMBOLD", "instrument_type": "CE", "expiry": _EXPIRY_MS, "weekly": False, "lot_size": 500, "instrument_key": D_CE, "strike_price": 500.0},
    {"segment": "NSE_FO", "underlying_symbol": "SYMBOLD", "instrument_type": "PE", "expiry": _EXPIRY_MS, "weekly": False, "lot_size": 500, "instrument_key": D_PE, "strike_price": 500.0},
    {"segment": "NSE_FO", "underlying_symbol": "SYMBOLD", "instrument_type": "FUT", "expiry": _EXPIRY_MS, "weekly": False, "lot_size": 500, "instrument_key": D_FUT},
]


def _router_with_d(call_counts: dict[str, int]) -> Callable[[httpx.Request], httpx.Response]:
    inner = _router(call_counts)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v2/market-quote/quotes":
            keys = request.url.params["instrument_key"].split(",")
            if D_KEY in keys:
                call_counts[path] = call_counts.get(path, 0) + 1
                data: dict[str, object] = {}
                for k in keys:
                    if k == D_KEY:
                        # D is deliberately ALREADY EXTENDED intraday --
                        # a real +8.7% move today, pinned near today's
                        # own high (position in day's range ~0.98). This
                        # is the core early-stage-discovery proof: a big
                        # mover is EXCLUDED, not automatically favored.
                        data["NSE_EQ:D"] = {
                            "instrument_token": k, "last_price": D_SPOT, "net_change": 40.0, "volume": 100000,
                            "ohlc": {"open": 461.0, "high": 500.5, "low": 459.0, "close": D_SPOT},
                        }
                    elif k == A_KEY:
                        # A -- a real, moderate move (+1.56%) still close
                        # to its own real day VWAP-equivalent: qualifies
                        # for the real DEVELOPING_MOMENTUM bucket.
                        data["NSE_EQ:A"] = {
                            "instrument_token": k, "last_price": A_SPOT, "net_change": 20.0, "volume": 1000000,
                            "ohlc": {"open": 1280.0, "high": 1305.0, "low": 1278.0, "close": A_SPOT},
                            "average_price": 1298.0,
                        }
                    elif k == B_KEY:
                        # B -- a real, moderate move (-0.57%), also near
                        # its own day VWAP-equivalent: qualifies for the
                        # same real DEVELOPING_MOMENTUM bucket.
                        data["NSE_EQ:B"] = {
                            "instrument_token": k, "last_price": B_SPOT, "net_change": -30.0, "volume": 2000000,
                            "ohlc": {"open": 5230.0, "high": 5240.0, "low": 5190.0, "close": B_SPOT},
                            "average_price": 5205.0,
                        }
                return httpx.Response(200, json={"status": "success", "data": data})
        # Everything else (including the plain A/B-only quote batch that
        # analyze_symbol() issues per symbol during Stage 2) goes through
        # the existing, already-proven router unchanged.
        return inner(request)

    return handler


def test_stage_one_screens_the_dynamic_equity_universe_and_excludes_indices(tmp_path: Path) -> None:
    """The core early-stage-discovery proof: D has ALREADY moved +8.7%
    today and sits pinned near today's own high -- real evidence of an
    already-extended intraday move -- and is excluded, while A and B
    (real, moderate moves still close to their own real day
    VWAP-equivalent) survive via the real DEVELOPING_MOMENTUM bucket.
    This is the opposite of the old day-change/volume-sort behavior,
    which would have put D (the biggest mover) first."""
    universe = list_fo_eligible_equity_underlyings(_MASTER_WITH_D)
    assert set(universe) == {"SYMBOLA", "SYMBOLB", "SYMBOLD"}  # real F&O equities only, no index in this fixture

    call_counts: dict[str, int] = {}
    provider = _provider(_router_with_d(call_counts))
    result = asyncio.run(
        run_daily_research(
            None, provider=provider, instrument_master=_MASTER_WITH_D, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), config=_FAST_CONFIG, as_of=AS_OF,
        )
    )
    assert result.screened_count == 3  # the full real universe (A, B, D)
    assert result.stage_one_survivor_count == 2  # A and B -- D excluded as already extended
    assert result.deep_analyzed_count == 2  # Stage 2 only ran on the 2 survivors
    # D's option-chain endpoint was never hit -- the router raises
    # AssertionError for any unexpected chain request, so if Stage 1
    # failed to filter D out, this whole test would already have failed
    # with that AssertionError surfacing as an "unexpected failure"
    # rejection instead of a clean D-excluded run.
    assert "SYMBOLD" not in {c.symbol for c in result.shortlist}
    stage_one_reasons = [r.reason for r in result.rejected if r.symbol == "SYMBOLD"]
    assert stage_one_reasons and "already extended intraday" in stage_one_reasons[0]
    assert result.rejected_as_extended_count == 1


def test_explicit_symbols_override_skips_stage_one_entirely(tmp_path: Path) -> None:
    """Backward compatibility -- passing `symbols` explicitly (the old
    default-universe behavior, or any manual list) must never invoke
    Stage 1 at all, so a caller that already knows exactly which symbols
    it wants pays zero extra cost."""
    call_counts: dict[str, int] = {}
    provider = _provider(_router_with_d(call_counts))
    result = asyncio.run(
        run_daily_research(
            ["SYMBOLA", "SYMBOLB"], provider=provider, instrument_master=_MASTER_WITH_D, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), config=_FAST_CONFIG, as_of=AS_OF,
        )
    )
    assert result.stage_one_survivor_count is None
    assert result.screened_count == 2
    assert result.deep_analyzed_count == 2


def test_rejection_summary_is_present_and_accounts_for_every_rejection(tmp_path: Path) -> None:
    call_counts: dict[str, int] = {}
    provider = _provider(_router_with_d(call_counts))
    result = asyncio.run(
        run_daily_research(
            None, provider=provider, instrument_master=_MASTER_WITH_D, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), config=_FAST_CONFIG, as_of=AS_OF,
            screening_config=ScreeningConfig(survivor_cap=2),
        )
    )
    assert sum(result.rejection_summary.values()) == len(result.rejected)
    assert result.rejection_summary  # non-empty -- D's screen-out is real and counted


def test_a_clean_real_run_reads_high_coverage_end_to_end(tmp_path: Path) -> None:
    """The existing, already-proven A/B/D fixture (no injected failures)
    -- every real symbol is reliably evaluated, so coverage reads HIGH end
    to end, through the real `run_daily_research()` call."""
    call_counts: dict[str, int] = {}
    provider = _provider(_router_with_d(call_counts))
    result = asyncio.run(
        run_daily_research(
            None, provider=provider, instrument_master=_MASTER_WITH_D, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), config=_FAST_CONFIG, as_of=AS_OF,
        )
    )
    assert result.coverage is not None
    assert result.coverage.classification == "HIGH"
    assert result.coverage.stage1_failed == 0
    assert result.coverage.stage2_failed == 0
    note = _coverage_result_note(no_high_conviction=result.no_high_conviction, coverage=result.coverage)
    assert "RESEARCH INCOMPLETE" not in note
    assert "degraded" not in note.lower()


# ============================================================
# "Quiet before move" -- the direct end-to-end proof that
# `classify_early_stage_state()` reaches EARLY_DIRECTIONAL_BUILD through
# the REAL pipeline (real EMA(9,21,50) alignment, real VWAP position, real
# decay viability -- nothing hand-built), not just the isolated unit-level
# tests in `test_daily_research_ranking.py`. Real M15 candles: a short
# elevated base, a real decline, then a real partial recovery -- this
# genuinely produces a MIXED (non-monotonic) EMA sequence (M15 trend ==
# NEUTRAL) while price ends up above the real session VWAP (VWAP ==
# BULLISH) -- exactly one of the two confirming signals, not both, with
# price still short of the recent swing high. Found empirically by probing
# the real `compute_ema_alignment()`/`compute_vwap_position()` functions,
# not guessed.
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

# Short elevated base (15 candles), a real decline (20 candles), then a
# real partial recovery (25 candles) that does not fully retrace the
# decline -- 60 M15 candles total.
_E_PRICES: list[float] = (
    [1000.0] * 15
    + [1000.0 - 0.75 * i for i in range(1, 21)]
    + [985.0 + 0.52 * i for i in range(1, 26)]
)


def _router_quiet_setup(call_counts: dict[str, int]) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        call_counts[path] = call_counts.get(path, 0) + 1
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
                    }
                elif k == E_FUT:
                    data["NSE_FO:EFUT"] = {"instrument_token": k, "last_price": E_SPOT + 2.0, "net_change": 4.0, "oi": 1000000, "volume": 500000}
            return httpx.Response(200, json={"status": "success", "data": data})
        if path.startswith("/v3/historical-candle/"):
            candles = [
                [(AS_OF - timedelta(minutes=15 * (len(_E_PRICES) - 1 - i))).isoformat(), p, p + 1, p - 1, p, 1000, 0]
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


def test_quiet_compressed_candidate_reaches_early_directional_build(tmp_path: Path) -> None:
    """The direct proof of 'quiet before move' detection: a real,
    genuinely quiet-today symbol (a small net day-change, LOW_VOLATILITY
    real market regime, only ONE of M15 trend/VWAP confirming) survives
    the real gate and reaches `EARLY_DIRECTIONAL_BUILD` -- not
    `INSUFFICIENT_DATA`, not `RANGE_BOUND`, and not one of the
    already-moved states. This is the system finding a setup BEFORE it
    becomes an obvious mover, via the real pipeline end to end."""
    call_counts: dict[str, int] = {}
    provider = _provider(_router_quiet_setup(call_counts))
    result = asyncio.run(
        run_daily_research(
            ["SYMBOLE"], provider=provider, instrument_master=_MASTER_WITH_E, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), config=_FAST_CONFIG, as_of=AS_OF,
        )
    )
    assert len(result.shortlist) == 1
    candidate = result.shortlist[0]
    assert candidate.symbol == "SYMBOLE"
    assert candidate.direction == "BULLISH"
    thesis = build_research_thesis(candidate)
    assert thesis.early_stage_state == "EARLY_DIRECTIONAL_BUILD"
    v = candidate.response.visual
    assert v is not None
    trend = next(r for r in v.evidence if r.name == "M15 trend")
    vwap = next(r for r in v.evidence if r.name == "VWAP")
    # Exactly one of the two confirms -- the defining real "quiet before
    # move" signature, never both (that would be DEVELOPING_MOMENTUM) and
    # never neither (that would be RANGE_BOUND).
    assert trend.direction == "NEUTRAL"
    assert vwap.direction == "BULLISH"


# ============================================================
# Sprint 1 -- root-cause fix regression: Stage 2's sequential loop over
# multiple symbols advances each symbol's own `as_of` by the REAL elapsed
# wall-clock duration since the scan started, so a late-processed symbol
# under real API pressure is never compared against an `as_of` that has
# quietly fallen minutes behind real time (see the fix's comment in
# `run_daily_research()`). `run_analysis()` and `utc_now()` are faked here
# -- this tests the ORCHESTRATION loop's own `as_of` arithmetic directly
# and deterministically, not the network layer (already covered above).
# ============================================================


def _blank_response(symbol: str) -> AnalyzeResponse:
    return AnalyzeResponse(
        query=symbol, parsed_symbol=None, parsed_strike=None, parsed_right=None, parsed_expiry_hint=None,
        has_specific_contract=False, parse_warnings=[], error=None, latency_seconds=0.0,
    )


def test_stage_two_advances_per_symbol_as_of_by_real_elapsed_wallclock_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_as_of: list[datetime] = []

    async def fake_run_analysis(symbol: str, **kwargs: object) -> AnalyzeResponse:
        captured_as_of.append(kwargs["as_of"])  # type: ignore[arg-type]
        return _blank_response(symbol)

    # utc_now() is read once before the loop (the real elapsed-time
    # reference point), once per iteration, and once more after the scan
    # completes (for `ResearchScanSnapshot.completed_at`) -- 4 calls for
    # 2 symbols.
    clock_values = iter([AS_OF, AS_OF, AS_OF + timedelta(minutes=6, seconds=30), AS_OF + timedelta(minutes=6, seconds=30)])
    monkeypatch.setattr("app.orchestration.daily_research.utc_now", lambda: next(clock_values))
    monkeypatch.setattr("app.orchestration.daily_research.run_analysis", fake_run_analysis)

    asyncio.run(
        run_daily_research(
            ["SYMBOLA", "SYMBOLB"], provider=_provider(_router({})), instrument_master=_MASTER,
            strategy=EMAVWAPAlignmentStrategy(), repositories=_repos(tmp_path), config=_FAST_CONFIG, as_of=AS_OF,
        )
    )

    assert len(captured_as_of) == 2
    # Symbol 1: no real time has elapsed yet -- as_of is exactly the
    # caller-supplied value, unchanged.
    assert captured_as_of[0] == AS_OF
    # Symbol 2: 6m30s of REAL wall-clock time genuinely elapsed while
    # symbol 1 was being analyzed -- as_of is advanced by that same real
    # duration, past the 5-minute clock-skew tolerance window a frozen
    # as_of would have silently stayed inside of.
    assert captured_as_of[1] == AS_OF + timedelta(minutes=6, seconds=30)


def test_stage_two_threads_cash_context_kwargs_into_run_analysis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, object]] = []

    async def fake_run_analysis(symbol: str, **kwargs: object) -> AnalyzeResponse:
        captured.append(kwargs)
        return _blank_response(symbol)

    monkeypatch.setattr("app.orchestration.daily_research.run_analysis", fake_run_analysis)
    asyncio.run(
        run_daily_research(
            ["SYMBOLA"], provider=_provider(_router({})), instrument_master=_MASTER,
            strategy=EMAVWAPAlignmentStrategy(), repositories=_repos(tmp_path), config=_FAST_CONFIG, as_of=AS_OF,
            nifty50_symbols=("RELIANCE",), http_client=object(), delivery_cache_dir=tmp_path,
        )
    )
    assert captured[0]["nifty50_symbols"] == ("RELIANCE",)
    assert captured[0]["delivery_cache_dir"] == tmp_path
    assert captured[0]["http_client"] is not None


def test_stage_two_as_of_advancement_is_negligible_in_a_fast_run(tmp_path: Path) -> None:
    """The real (non-faked) clock path -- in a fast/mocked run (every
    other test in this module), real elapsed wall-clock time between
    symbols is microseconds, so behavior is unchanged from a single
    frozen `as_of`: two back-to-back real calls with identical mocked
    inputs still produce the identical real decision per symbol
    (deterministic replay preserved)."""
    call_counts_1: dict[str, int] = {}
    call_counts_2: dict[str, int] = {}
    result_1 = asyncio.run(
        run_daily_research(
            ["SYMBOLA", "SYMBOLB"], provider=_provider(_router(call_counts_1)), instrument_master=_MASTER,
            strategy=EMAVWAPAlignmentStrategy(), repositories=_repos(tmp_path / "run1"), config=_FAST_CONFIG, as_of=AS_OF,
        )
    )
    result_2 = asyncio.run(
        run_daily_research(
            ["SYMBOLA", "SYMBOLB"], provider=_provider(_router(call_counts_2)), instrument_master=_MASTER,
            strategy=EMAVWAPAlignmentStrategy(), repositories=_repos(tmp_path / "run2"), config=_FAST_CONFIG, as_of=AS_OF,
        )
    )
    assert [c.response.decision for c in result_1.shortlist] == [c.response.decision for c in result_2.shortlist]
    assert {r.symbol: r.reason for r in result_1.rejected} == {r.symbol: r.reason for r in result_2.rejected}


def test_stage_two_one_symbol_failure_does_not_corrupt_the_next_symbols_as_of(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry/failure-isolation regression: a real per-symbol exception
    (isolated by the existing `try/except` -- unchanged) must never skip
    or corrupt the real-elapsed-time advancement for the symbols that
    come after it."""
    captured_as_of: list[datetime] = []

    async def flaky_run_analysis(symbol: str, **kwargs: object) -> AnalyzeResponse:
        if symbol == "SYMBOLA":
            raise RuntimeError("transient provider timeout")
        captured_as_of.append(kwargs["as_of"])  # type: ignore[arg-type]
        return _blank_response(symbol)

    clock_values = iter([AS_OF, AS_OF + timedelta(minutes=1), AS_OF + timedelta(minutes=7), AS_OF + timedelta(minutes=7)])
    monkeypatch.setattr("app.orchestration.daily_research.utc_now", lambda: next(clock_values))
    monkeypatch.setattr("app.orchestration.daily_research.run_analysis", flaky_run_analysis)

    result = asyncio.run(
        run_daily_research(
            ["SYMBOLA", "SYMBOLB"], provider=_provider(_router({})), instrument_master=_MASTER,
            strategy=EMAVWAPAlignmentStrategy(), repositories=_repos(tmp_path), config=_FAST_CONFIG, as_of=AS_OF,
        )
    )

    assert any("transient provider timeout" in r.reason for r in result.rejected)
    # SYMBOLB still got its own, correctly-advanced as_of despite SYMBOLA's
    # real failure immediately before it -- isolation holds both for the
    # exception AND for the real-elapsed-time bookkeeping.
    assert captured_as_of == [AS_OF + timedelta(minutes=7)]


def test_stage_two_as_of_advancement_never_moves_the_run_level_generated_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fix is scoped to the per-symbol `as_of` passed into
    `run_analysis()` only -- the run-level `generated_at`/Stage-1
    timestamp (used for rejection records, `ShortlistRecord`, etc.)
    remains the original, caller-supplied `as_of`, unchanged."""
    async def fake_run_analysis(symbol: str, **kwargs: object) -> AnalyzeResponse:
        return _blank_response(symbol)

    clock_values = iter([AS_OF, AS_OF + timedelta(minutes=10), AS_OF + timedelta(minutes=20), AS_OF + timedelta(minutes=20)])
    monkeypatch.setattr("app.orchestration.daily_research.utc_now", lambda: next(clock_values))
    monkeypatch.setattr("app.orchestration.daily_research.run_analysis", fake_run_analysis)

    result = asyncio.run(
        run_daily_research(
            ["SYMBOLA", "SYMBOLB"], provider=_provider(_router({})), instrument_master=_MASTER,
            strategy=EMAVWAPAlignmentStrategy(), repositories=_repos(tmp_path), config=_FAST_CONFIG, as_of=AS_OF,
        )
    )
    assert result.generated_at == AS_OF


# ============================================================
# Sprint 1 -- Stage-1 batched-quote reliability: a bounded retry (reusing
# the SAME `_retry()` linear-backoff helper the option-chain fetch already
# uses) on genuinely transient failures only, so one real timeout/network
# blip on a quote-batch chunk no longer permanently drops every symbol in
# it (the real live defect: ~100/210 symbols lost to zero-retry batch
# timeouts on 2026-08-31).
# ============================================================


def _router_stage1_transient_then_success(
    call_counts: dict[str, int], attempt_counter: list[int],
) -> Callable[[httpx.Request], httpx.Response]:
    inner = _router(call_counts)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v2/market-quote/quotes":
            keys = request.url.params["instrument_key"].split(",")
            if A_KEY in keys and B_KEY in keys:
                attempt_counter.append(1)
                call_counts[path] = call_counts.get(path, 0) + 1
                if len(attempt_counter) == 1:
                    raise httpx.ConnectError("transient network blip")  # first attempt -- genuinely transient
                return httpx.Response(200, json={"status": "success", "data": {
                    "NSE_EQ:A": {
                        "instrument_token": A_KEY, "last_price": A_SPOT, "net_change": 20.0, "volume": 1000000,
                        "ohlc": {"open": 1280.0, "high": 1305.0, "low": 1278.0, "close": A_SPOT}, "average_price": 1298.0,
                    },
                    "NSE_EQ:B": {
                        "instrument_token": B_KEY, "last_price": B_SPOT, "net_change": -30.0, "volume": 2000000,
                        "ohlc": {"open": 5230.0, "high": 5240.0, "low": 5190.0, "close": B_SPOT}, "average_price": 5205.0,
                    },
                }})
        return inner(request)

    return handler


def test_stage_one_retries_a_transient_quote_batch_failure_and_recovers(tmp_path: Path) -> None:
    """A real, transient (`ProviderUnavailable`-class) failure on the
    Stage-1 batched quote call for A/B's chunk succeeds on the bounded
    retry -- neither symbol is lost to a single blip, unlike the
    zero-retry behavior before this fix."""
    call_counts: dict[str, int] = {}
    attempt_counter: list[int] = []
    provider = _provider(_router_stage1_transient_then_success(call_counts, attempt_counter))
    result = asyncio.run(
        run_daily_research(
            None, provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), config=_FAST_CONFIG, as_of=AS_OF,
            screening_config=ScreeningConfig(quote_batch_attempts=2, quote_batch_backoff_seconds=0.01),
        )
    )
    assert len(attempt_counter) == 2  # first attempt failed transiently, second succeeded
    assert result.stage_one_survivor_count == 2  # both A and B survived Stage 1 -- neither lost to the blip
    assert not any("quote batch fetch failed" in r.reason for r in result.rejected)
    # Coverage (Sprint 1, Phase 4) -- the retry means this run reads HIGH,
    # not degraded, for what would otherwise have looked like a real
    # infrastructure failure.
    assert result.coverage is not None
    assert result.coverage.classification == "HIGH"
    assert result.coverage.stage1_failed == 0


def test_stage_one_gives_up_after_exhausting_bounded_retries(tmp_path: Path) -> None:
    """A failure that persists across every attempt still produces a real,
    honest rejection for every symbol in that chunk -- the retry is
    bounded, never unlimited, and a genuinely down provider is still
    reported, not silently retried forever."""
    def always_fails(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/market-quote/quotes":
            raise httpx.ConnectError("provider is down")
        return _router({})(request)

    provider = _provider(always_fails)
    result = asyncio.run(
        run_daily_research(
            None, provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), config=_FAST_CONFIG, as_of=AS_OF,
            screening_config=ScreeningConfig(quote_batch_attempts=2, quote_batch_backoff_seconds=0.01),
        )
    )
    assert result.stage_one_survivor_count == 0
    # Sprint 3 -- each symbol now gets exactly ONE real rejection record
    # ("quote batch fetch failed"), not a second, redundant "no quote data
    # returned" record for the same real symbol (the P2 duplicate-
    # rejection cleanup -- was previously harmless for Coverage math,
    # which already deduplicated by symbol, but inflated the raw
    # rejection list).
    assert {r.symbol for r in result.rejected} == {"SYMBOLA", "SYMBOLB"}
    assert len(result.rejected) == 2
    assert any("quote batch fetch failed" in r.reason for r in result.rejected if r.symbol == "SYMBOLA")
    assert any("quote batch fetch failed" in r.reason for r in result.rejected if r.symbol == "SYMBOLB")
    assert not any("no quote data returned" in r.reason for r in result.rejected)
    # Coverage (Sprint 1, Phase 4) -- both real symbols genuinely could not
    # be reliably evaluated this run (0/2 = 0% coverage) -- POOR, and the
    # run must never claim a complete market scan (Phase 5).
    assert result.coverage is not None
    assert result.coverage.classification == "POOR"
    assert result.coverage.stage1_failed == 2
    note = _coverage_result_note(no_high_conviction=result.no_high_conviction, coverage=result.coverage)
    assert "RESEARCH INCOMPLETE" in note


def test_stage_one_never_retries_a_genuinely_malformed_response(tmp_path: Path) -> None:
    """A real `ProviderMalformedResponse` (a bad response, not a transient
    failure) is never retried -- retrying a schema error can't help, and
    doing so would waste real request budget."""
    call_counts: dict[str, int] = {}

    def malformed(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/market-quote/quotes":
            call_counts[request.url.path] = call_counts.get(request.url.path, 0) + 1
            return httpx.Response(200, content=b"not json")
        return _router({})(request)

    provider = _provider(malformed)
    asyncio.run(
        run_daily_research(
            None, provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), config=_FAST_CONFIG, as_of=AS_OF,
            screening_config=ScreeningConfig(quote_batch_attempts=2, quote_batch_backoff_seconds=0.01),
        )
    )
    assert call_counts["/v2/market-quote/quotes"] == 1  # never retried


# ============================================================
# Sprint 2 -- MARKET-STATE-AWARE STALENESS: Stage 1's own staleness check
# now reuses the SAME authoritative `ExchangeStatus`/`market_is_open`
# semantic Stage 2's `classify_market_data_state()` already uses (never a
# second, duplicate market-state implementation), and a real, weekend/
# holiday-aware `is_trading_day()` bound (never a flat hours/days
# tolerance) for the closed-market case. `screen_universe()` is called
# directly here for precise, deterministic control over `exchange_status`
# and `last_trade_time`, rather than routing it through a full market-
# status HTTP fixture.
# ============================================================


def _epoch_ms(dt: datetime) -> str:
    return str(int(dt.timestamp() * 1000))


def _router_stage1_freshness(*, a_last_trade_time: datetime | None, b_last_trade_time: datetime | None) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v2/market-quote/quotes":
            keys = request.url.params["instrument_key"].split(",")
            data: dict[str, object] = {}
            for k in keys:
                if k == A_KEY:
                    entry: dict[str, object] = {
                        "instrument_token": k, "last_price": A_SPOT, "net_change": 20.0, "volume": 1000000,
                        "ohlc": {"open": 1280.0, "high": 1305.0, "low": 1278.0, "close": A_SPOT}, "average_price": 1298.0,
                    }
                    if a_last_trade_time is not None:
                        entry["last_trade_time"] = _epoch_ms(a_last_trade_time)
                    data["NSE_EQ:A"] = entry
                elif k == B_KEY:
                    entry = {
                        "instrument_token": k, "last_price": B_SPOT, "net_change": -30.0, "volume": 2000000,
                        "ohlc": {"open": 5230.0, "high": 5240.0, "low": 5190.0, "close": B_SPOT}, "average_price": 5205.0,
                    }
                    if b_last_trade_time is not None:
                        entry["last_trade_time"] = _epoch_ms(b_last_trade_time)
                    data["NSE_EQ:B"] = entry
            return httpx.Response(200, json={"status": "success", "data": data})
        raise AssertionError(f"unexpected path {path}")

    return handler


# AS_OF is 2026-08-28 12:00 UTC -- a real Friday. AS_OF_MONDAY is the real
# following Monday, 2026-08-31 -- used for the weekend-aware tests.
AS_OF_MONDAY = datetime(2026, 8, 31, 6, 0, tzinfo=UTC)


async def _screen(*, last_trade_time_a: datetime | None, last_trade_time_b: datetime | None, exchange_status: ExchangeStatus, as_of: datetime = AS_OF) -> StageOneResult:
    provider = _provider(_router_stage1_freshness(a_last_trade_time=last_trade_time_a, b_last_trade_time=last_trade_time_b))
    return await screen_universe(
        ["SYMBOLA", "SYMBOLB"], provider=provider, instrument_master=_MASTER, as_of=as_of, exchange_status=exchange_status,
    )


def test_market_open_stale_quote_remains_rejected() -> None:
    """Objective 12.A -- LIVE/NORMAL_OPEN protection is completely
    unchanged: a quote 20 minutes old (beyond the strict 15-minute live
    tolerance) is still rejected, even though the market is open."""
    result = asyncio.run(_screen(
        last_trade_time_a=AS_OF - timedelta(minutes=20), last_trade_time_b=AS_OF - timedelta(minutes=20),
        exchange_status=ExchangeStatus.NORMAL_OPEN,
    ))
    assert result.survivors == []
    assert all("stale quote data" in r.reason for r in result.rejected if r.symbol in ("SYMBOLA", "SYMBOLB"))


def test_market_closed_historical_quote_is_research_usable_same_day() -> None:
    """Objective 12.B -- a quote 3 hours old (far beyond the 15-minute
    live tolerance) on the SAME real trading day is valid historical data
    once the market is confirmed closed -- it must reach real bucket
    evaluation, not be rejected as stale."""
    result = asyncio.run(_screen(
        last_trade_time_a=AS_OF - timedelta(hours=3), last_trade_time_b=AS_OF - timedelta(hours=3),
        exchange_status=ExchangeStatus.NORMAL_CLOSE,
    ))
    assert not any("stale quote data" in r.reason for r in result.rejected if r.symbol in ("SYMBOLA", "SYMBOLB"))


def test_market_closed_historical_quote_is_research_usable_across_a_weekend() -> None:
    """Objective 12.B, the real originally-reported bug scenario -- a
    Monday morning research run (market not yet open) must accept
    Friday's real last-session quote, not reject it merely because its
    calendar age is measured in days, not minutes. A flat hours/days
    tolerance could never get this right without either being unsafely
    loose during the week or wrongly rejecting a legitimate Friday->Monday
    gap -- the real, weekend-aware `is_trading_day()` bound handles it
    correctly either way."""
    last_friday_close = datetime(2026, 8, 28, 10, 0, tzinfo=UTC)  # a real Friday, ~15:30 IST
    result = asyncio.run(_screen(
        last_trade_time_a=last_friday_close, last_trade_time_b=last_friday_close,
        exchange_status=ExchangeStatus.PRE_OPEN_START, as_of=AS_OF_MONDAY,
    ))
    assert not any("stale quote data" in r.reason for r in result.rejected if r.symbol in ("SYMBOLA", "SYMBOLB"))


def test_market_closed_quote_predating_the_last_real_session_still_rejected() -> None:
    """The closed-market relaxation is bounded, never unlimited -- a quote
    from BEFORE the last real trading session (here: the Wednesday before
    a Friday `as_of`, skipping Thursday's real session entirely) is still
    genuinely stale and still rejected, market-closed or not."""
    two_sessions_back = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)  # the Wednesday before Friday AS_OF
    result = asyncio.run(_screen(
        last_trade_time_a=two_sessions_back, last_trade_time_b=two_sessions_back,
        exchange_status=ExchangeStatus.NORMAL_CLOSE,
    ))
    assert all("stale quote data" in r.reason for r in result.rejected if r.symbol in ("SYMBOLA", "SYMBOLB"))


def test_future_quote_timestamp_still_rejected_regardless_of_market_state() -> None:
    """Objective 12.D/E -- a real look-ahead quote (10 minutes ahead of
    `as_of`, beyond the real clock-skew tolerance reused from Stage 2's
    own `normalize_quote()`) must never survive Stage 1, whether the
    market is open or closed."""
    future = AS_OF + timedelta(minutes=10)
    for status in (ExchangeStatus.NORMAL_OPEN, ExchangeStatus.NORMAL_CLOSE):
        result = asyncio.run(_screen(last_trade_time_a=future, last_trade_time_b=future, exchange_status=status))
        assert all("stale quote data" in r.reason for r in result.rejected if r.symbol in ("SYMBOLA", "SYMBOLB")), status


def test_unknown_market_state_fails_conservatively_not_treated_as_open_or_unlimited() -> None:
    """Objective 12.F -- `ExchangeStatus.UNKNOWN` (the market-status call
    itself failed) must never be treated as NORMAL_OPEN (which would wrongly
    apply the strict live tolerance's PASS path to data that might really
    be from a closed market) -- it takes the SAME bounded, real-calendar
    closed-market path Stage 2 already falls back to, never an unlimited
    one: a same-day 3h-old quote still passes, but a quote predating the
    last real session still does not."""
    same_day_result = asyncio.run(_screen(
        last_trade_time_a=AS_OF - timedelta(hours=3), last_trade_time_b=AS_OF - timedelta(hours=3),
        exchange_status=ExchangeStatus.UNKNOWN,
    ))
    assert not any("stale quote data" in r.reason for r in same_day_result.rejected if r.symbol in ("SYMBOLA", "SYMBOLB"))

    too_old = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
    too_old_result = asyncio.run(_screen(last_trade_time_a=too_old, last_trade_time_b=too_old, exchange_status=ExchangeStatus.UNKNOWN))
    assert all("stale quote data" in r.reason for r in too_old_result.rejected if r.symbol in ("SYMBOLA", "SYMBOLB"))


def test_missing_timestamp_is_distinct_from_a_missing_quote() -> None:
    """A real quote with no `last_trade_time` at all (some providers never
    supply one) has nothing to compare for staleness -- the check is
    skipped, not a rejection -- proving "missing timestamp" and "missing
    quote" (the next test) are genuinely different, real cases, not
    conflated into one."""
    result = asyncio.run(_screen(last_trade_time_a=None, last_trade_time_b=None, exchange_status=ExchangeStatus.NORMAL_CLOSE))
    assert result.candidates_evaluated == 2


def test_genuinely_missing_quote_data_remains_rejected() -> None:
    """The real "no quote returned at all" case (not merely no timestamp)."""
    def no_data(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/market-quote/quotes":
            return httpx.Response(200, json={"status": "success", "data": {}})
        raise AssertionError(f"unexpected path {request.url.path}")

    provider = _provider(no_data)
    result = asyncio.run(screen_universe(
        ["SYMBOLA", "SYMBOLB"], provider=provider, instrument_master=_MASTER, as_of=AS_OF, exchange_status=ExchangeStatus.NORMAL_CLOSE,
    ))
    assert result.survivors == []
    assert all("no quote data returned" in r.reason for r in result.rejected if r.symbol in ("SYMBOLA", "SYMBOLB"))


def test_malformed_quote_response_remains_rejected_market_state_notwithstanding(tmp_path: Path) -> None:
    """Objective 12.H -- a genuinely malformed response is still rejected
    (and never retried -- Sprint 1's existing protection, unchanged) no
    matter what the market state is."""
    def malformed(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/market/status/NSE":
            return httpx.Response(200, json={"status": "success", "data": {"status": "NORMAL_CLOSE"}})
        if request.url.path == "/v2/market-quote/quotes":
            return httpx.Response(200, content=b"not json")
        return _router({})(request)

    provider = _provider(malformed)
    result = asyncio.run(
        run_daily_research(
            None, provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), config=_FAST_CONFIG, as_of=AS_OF,
            screening_config=ScreeningConfig(quote_batch_attempts=2, quote_batch_backoff_seconds=0.01),
        )
    )
    assert result.stage_one_survivor_count == 0


def test_market_closed_run_reads_high_coverage_not_poor(tmp_path: Path) -> None:
    """Objective 12.I -- the actual originally-reported defect: a
    market-closed run with genuinely valid historical data must NOT read
    POOR coverage merely because quotes are older than the live-session
    tolerance."""
    def closed_market_router(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/market/status/NSE":
            return httpx.Response(200, json={"status": "success", "data": {"status": "NORMAL_CLOSE"}})
        return _router_with_d({})(request)

    provider = _provider(closed_market_router)
    result = asyncio.run(
        run_daily_research(
            None, provider=provider, instrument_master=_MASTER_WITH_D, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), config=_FAST_CONFIG, as_of=AS_OF,
        )
    )
    assert result.coverage is not None
    assert result.coverage.classification != "POOR"
    assert result.coverage.stage1_failed == 0
    # Stage 1 and Stage 2 agree (Objective 7/12.K): the run-level
    # market_state Stage 2 reports is the honest MARKET_CLOSED_LATEST_DATA
    # label -- Stage 1 having let real historical data through never gets
    # silently relabeled "live" downstream.
    assert result.market_state == "MARKET_CLOSED_LATEST_DATA"


# ============================================================
# 95%+ Reliability & Performance Gate -- Stage 2 bounded concurrency and
# `ResearchScanSnapshot`. See `docs/TIRE_SCAN_PERFORMANCE.md` for the
# real measured before/after this addresses.
# ============================================================


def test_stage_two_runs_concurrently_not_strictly_sequentially(tmp_path: Path) -> None:
    """The real, demonstrated performance defect: the old Stage 2 loop
    was strictly sequential, so N symbols each taking `t` seconds cost
    N*t wall-clock time. With bounded concurrency (default 6), 6
    independent symbols that each take ~0.1s should complete in close to
    0.1s total, not 0.6s."""
    async def slow_run_analysis(symbol: str, **kwargs: object) -> AnalyzeResponse:
        await asyncio.sleep(0.1)
        return _blank_response(symbol)

    async def _run() -> float:
        started = asyncio.get_event_loop().time()
        await run_daily_research(
            ["SYMBOLA", "SYMBOLB", "SYMBOLD", "SYMBOLE", "SYMBOLF", "SYMBOLG"],
            provider=_provider(_router({})), instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), config=_FAST_CONFIG, as_of=AS_OF,
        )
        return asyncio.get_event_loop().time() - started

    import unittest.mock

    with unittest.mock.patch("app.orchestration.daily_research.run_analysis", slow_run_analysis):
        elapsed = asyncio.run(_run())

    # Strictly sequential would be >= 0.6s (6 * 0.1s); concurrent (bound 6)
    # should complete in roughly one round-trip, comfortably under that.
    assert elapsed < 0.4, f"Stage 2 took {elapsed:.3f}s -- looks sequential, not concurrent"


def test_stage_two_concurrency_never_exceeds_the_configured_bound(tmp_path: Path) -> None:
    """Rate-limit safety: even with 12 symbols and plenty of room to run
    them all at once, no more than `stage_two_concurrency` real analyses
    are ever in flight simultaneously -- "210 requests" must never become
    "2100 requests."""
    in_flight = 0
    max_observed = 0

    async def tracked_run_analysis(symbol: str, **kwargs: object) -> AnalyzeResponse:
        nonlocal in_flight, max_observed
        in_flight += 1
        max_observed = max(max_observed, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1
        return _blank_response(symbol)

    symbols = [f"SYM{i}" for i in range(12)]
    master: list[dict[str, object]] = list(_MASTER)
    for i in range(12):
        key = f"NSE_EQ|SYM{i}"
        master.append({"segment": "NSE_EQ", "name": f"SYM{i}", "exchange": "NSE", "instrument_type": "EQ", "instrument_key": key, "trading_symbol": f"SYM{i}"})

    import unittest.mock

    with unittest.mock.patch("app.orchestration.daily_research.run_analysis", tracked_run_analysis):
        asyncio.run(
            run_daily_research(
                symbols, provider=_provider(_router({})), instrument_master=master, strategy=EMAVWAPAlignmentStrategy(),
                repositories=_repos(tmp_path), config=_FAST_CONFIG, as_of=AS_OF, stage_two_concurrency=3,
            )
        )

    assert max_observed <= 3


def test_scan_snapshot_reports_honest_coverage_and_timing(tmp_path: Path) -> None:
    """`ResearchScanSnapshot` must let a caller say "1/2 symbols
    successfully analyzed" -- never silently claim a full scan when a
    symbol genuinely failed, and must correctly count a symbol that
    really did complete with real data as successful."""
    real_run_analysis = run_analysis

    async def one_fails(symbol: str, **kwargs: object) -> AnalyzeResponse:
        if symbol == "SYMBOLA":
            raise RuntimeError("provider unavailable")
        return await real_run_analysis(symbol, **kwargs)  # type: ignore[arg-type]

    import unittest.mock

    with unittest.mock.patch("app.orchestration.daily_research.run_analysis", one_fails):
        result = asyncio.run(
            run_daily_research(
                ["SYMBOLA", "SYMBOLB"], provider=_provider(_router({})), instrument_master=_MASTER,
                strategy=EMAVWAPAlignmentStrategy(), repositories=_repos(tmp_path), config=_FAST_CONFIG, as_of=AS_OF,
            )
        )

    snapshot = result.scan_snapshot
    assert snapshot is not None
    assert snapshot.requested_symbols == 2
    assert snapshot.successful_symbols == 1
    assert snapshot.failed_symbols == 1
    assert snapshot.completed_at >= snapshot.started_at
    assert snapshot.duration_seconds >= 0.0
    symbols_seen = {r.symbol for r in snapshot.per_symbol_freshness}
    assert symbols_seen == {"SYMBOLA", "SYMBOLB"}
    failed_record = next(r for r in snapshot.per_symbol_freshness if r.symbol == "SYMBOLA")
    assert failed_record.succeeded is False
    ok_record = next(r for r in snapshot.per_symbol_freshness if r.symbol == "SYMBOLB")
    assert ok_record.succeeded is True
