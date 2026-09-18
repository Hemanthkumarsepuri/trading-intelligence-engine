from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx

from app.data.providers.upstox_provider import UpstoxProvider
from app.domain.market.models import OptionRight
from app.domain.market.price_consistency import DataConsistency, PriceSource
from app.domain.options.decision_engine import FinalDecision, QualityLevel, ResearchState
from app.domain.options.evidence_matrix import EvidenceDirection, EvidenceRow
from app.domain.options.oi_migration import OIMigrationDirection
from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy
from app.orchestration.options_intelligence_pipeline import (
    PipelineConfig,
    Repositories,
    analyze_symbol,
)
from app.orchestration.options_intelligence_report import OptionsIntelligenceReport
from app.persistence.jsonl_file import (
    JsonlIvObservationRepository,
    JsonlOptionChainRepository,
    JsonlQuoteRepository,
)

RELIANCE_KEY = "NSE_EQ|INE002A01018"
CE_KEY = "NSE_FO|1"
PE_KEY = "NSE_FO|2"
FUT_KEY = "NSE_FO|3"
NIFTY_KEY = "NSE_INDEX|Nifty 50"
BANKNIFTY_KEY = "NSE_INDEX|Nifty Bank"
VIX_KEY = "NSE_INDEX|India VIX"
USDINR_KEY = "NCD_FO|1"
CRUDE_KEY = "MCX_FO|1"
EXPIRY = date(2026, 9, 24)
_EXPIRY_MS = 1790274599000  # 2026-09-24 18:29:59 UTC
AS_OF = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)

_MASTER: list[dict[str, object]] = [
    {
        "segment": "NSE_EQ", "name": "RELIANCE INDUSTRIES LTD", "exchange": "NSE", "instrument_type": "EQ",
        "instrument_key": RELIANCE_KEY, "trading_symbol": "RELIANCE",
    },
    {
        "segment": "NSE_FO", "underlying_symbol": "RELIANCE", "instrument_type": "CE", "expiry": _EXPIRY_MS,
        "weekly": False, "lot_size": 500, "instrument_key": CE_KEY, "strike_price": 1300.0,
    },
    {
        "segment": "NSE_FO", "underlying_symbol": "RELIANCE", "instrument_type": "PE", "expiry": _EXPIRY_MS,
        "weekly": False, "lot_size": 500, "instrument_key": PE_KEY, "strike_price": 1300.0,
    },
    {
        "segment": "NSE_FO", "underlying_symbol": "RELIANCE", "instrument_type": "FUT", "expiry": _EXPIRY_MS,
        "weekly": False, "lot_size": 500, "instrument_key": FUT_KEY,
    },
    {
        "segment": "NSE_INDEX", "name": "Nifty 50", "exchange": "NSE", "instrument_type": "INDEX",
        "instrument_key": NIFTY_KEY, "trading_symbol": "NIFTY",
    },
    {
        "segment": "NSE_INDEX", "name": "Nifty Bank", "exchange": "NSE", "instrument_type": "INDEX",
        "instrument_key": BANKNIFTY_KEY, "trading_symbol": "BANKNIFTY",
    },
    {
        "segment": "NSE_INDEX", "name": "India VIX", "exchange": "NSE", "instrument_type": "INDEX",
        "instrument_key": VIX_KEY, "trading_symbol": "INDIA VIX",
    },
    {
        "segment": "NCD_FO", "underlying_symbol": "USDINR", "instrument_type": "FUT",
        "expiry": 1790447399000, "instrument_key": USDINR_KEY,  # 2026-09-26
    },
]

_MCX_MASTER: list[dict[str, object]] = [
    {
        "segment": "MCX_FO", "underlying_symbol": "CRUDEOIL", "instrument_type": "FUT",
        "expiry": 1790447399000, "instrument_key": CRUDE_KEY,  # 2026-09-26
    },
]


def _candle_rows(n: int, *, end: datetime, price: float = 1280.0) -> list[list[object]]:
    rows = []
    for i in range(n):
        ts = end - timedelta(minutes=15 * (n - 1 - i))
        p = price + i * 0.5  # gently rising -> ASCENDING EMA alignment, price above VWAP
        rows.append([ts.isoformat(), p, p + 1, p - 1, p, 1000, 0])
    return rows


def _chain_body(*, spot: float = 1300.0, extra_strikes: tuple[float, ...] = ()) -> dict[str, object]:
    rows: list[dict[str, object]] = [
        {
            "expiry": EXPIRY.isoformat(), "strike_price": 1300.0, "underlying_spot_price": spot,
            "call_options": {
                "instrument_key": CE_KEY,
                "market_data": {"ltp": 30.0, "bid_price": 29.5, "ask_price": 30.5, "volume": 8000, "oi": 80000, "prev_oi": 75000},
                "option_greeks": {"iv": 18.0, "delta": 0.5},
            },
            "put_options": {
                "instrument_key": PE_KEY,
                "market_data": {"ltp": 28.0, "bid_price": 27.5, "ask_price": 28.5, "volume": 7000, "oi": 60000, "prev_oi": 58000},
                "option_greeks": {"iv": 17.0, "delta": -0.5},
            },
        }
    ]
    # `extra_strikes` (S/R geometry fix regression coverage) -- additional
    # legs at caller-chosen strikes so a test can exercise a chain with
    # real strikes genuinely on both sides of spot; no instrument_key
    # reuse (support_resistance_levels() never dereferences it), same
    # static OI/greeks shape every call so two successive fetches compare
    # as a real, stable level.
    for strike in extra_strikes:
        rows.append({
            "expiry": EXPIRY.isoformat(), "strike_price": strike, "underlying_spot_price": spot,
            "call_options": {
                "instrument_key": f"NSE_FO|extra-ce-{strike}",
                "market_data": {"ltp": 5.0, "bid_price": 4.8, "ask_price": 5.2, "volume": 1000, "oi": 20000, "prev_oi": 19000},
                "option_greeks": {"iv": 18.0, "delta": 0.3},
            },
            "put_options": {
                "instrument_key": f"NSE_FO|extra-pe-{strike}",
                "market_data": {"ltp": 5.0, "bid_price": 4.8, "ask_price": 5.2, "volume": 1000, "oi": 20000, "prev_oi": 19000},
                "option_greeks": {"iv": 17.0, "delta": -0.3},
            },
        })
    return {"status": "success", "data": rows}


def _router(
    *, status: str = "NORMAL_OPEN", candle_count: int = 60, chain_fails: int = 0, spot: float = 1300.0,
    nifty_change: float = 0.0, banknifty_change: float = 0.0, vix_change: float = 0.0,
    usdinr_change: float = 0.0, crude_change: float = 0.0, fail_derivative_quotes: bool = False,
    extra_chain_strikes: tuple[float, ...] = (), fut_last_trade_time_ahead_seconds: float | None = None,
    underlying_quote_fails: int = 0, futures_quote_fails: int = 0, candle_price: float = 1280.0,
    candle_end: datetime | None = None, fut_price: float | None = None,
    fut_last_trade_time: datetime | None = None, chain_spot: float | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    calls = {"chain": 0, "quote": 0, "fut_quote": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v2/market/status/NSE":
            return httpx.Response(200, json={"status": "success", "data": {"status": status}})
        if path == "/v2/market-quote/quotes":
            keys = request.url.params["instrument_key"].split(",")
            if RELIANCE_KEY in keys:
                # Sprint 2 -- the real underlying-quote fetch (step 3) is
                # the FIRST batched-quote call this pipeline makes -- fail
                # it `underlying_quote_fails` times (a real transient
                # `ProviderUnavailable`) before letting it succeed, to
                # prove the new bounded retry recovers it.
                calls["quote"] += 1
                if calls["quote"] <= underlying_quote_fails:
                    return httpx.Response(503, text="simulated transient underlying-quote failure")
            if keys == [FUT_KEY]:
                # Sprint 3, Priority 4 -- the real futures-quote fetch is
                # its own separate single-key `get_quotes()` call.
                calls["fut_quote"] += 1
                if calls["fut_quote"] <= futures_quote_fails:
                    return httpx.Response(503, text="simulated transient futures-quote failure")
            if fail_derivative_quotes and (USDINR_KEY in keys or CRUDE_KEY in keys):
                # Reproduces the real bug found live 2026-08-29: Upstox
                # returning a malformed `last_trade_time` for these newer
                # derivative keys -- this must never take down a request
                # that ALSO contains healthy NSE_INDEX keys sharing the
                # same batch (the real fix: never share a batch at all).
                return httpx.Response(503, text="simulated real last_trade_time failure")
            data: dict[str, object] = {}
            for k in keys:
                if k == RELIANCE_KEY:
                    data["NSE_EQ:RELIANCE"] = {"instrument_token": k, "last_price": spot, "net_change": 5.0, "volume": 1000000}
                elif k == FUT_KEY:
                    fut_entry: dict[str, object] = {"instrument_token": k, "last_price": fut_price if fut_price is not None else spot + 2.0, "net_change": 4.0, "oi": 1000000, "volume": 500000}
                    if fut_last_trade_time is not None:
                        fut_entry["last_trade_time"] = str(int(fut_last_trade_time.timestamp() * 1000))
                    elif fut_last_trade_time_ahead_seconds is not None:
                        # Reproduces the real bug found live 2026-08-31
                        # (market open, INDIGO): the futures leg's own
                        # `last_trade_time` arriving after this request's
                        # frozen `as_of` -- `DataFreshness` correctly
                        # rejects that; the pipeline must degrade this one
                        # section, not crash the whole analysis.
                        ahead = AS_OF + timedelta(seconds=fut_last_trade_time_ahead_seconds)
                        fut_entry["last_trade_time"] = str(int(ahead.timestamp() * 1000))
                    data["NSE_FO:FUT"] = fut_entry
                elif k == NIFTY_KEY:
                    data["NSE_INDEX:NIFTY"] = {"instrument_token": k, "last_price": 24000.0, "net_change": nifty_change}
                elif k == BANKNIFTY_KEY:
                    data["NSE_INDEX:BANKNIFTY"] = {"instrument_token": k, "last_price": 51000.0, "net_change": banknifty_change}
                elif k == VIX_KEY:
                    data["NSE_INDEX:VIX"] = {"instrument_token": k, "last_price": 13.0, "net_change": vix_change}
                elif k == USDINR_KEY:
                    data["NCD_FO:USDINR"] = {"instrument_token": k, "last_price": 88.0, "net_change": usdinr_change}
                elif k == CRUDE_KEY:
                    data["MCX_FO:CRUDEOIL"] = {"instrument_token": k, "last_price": 6000.0, "net_change": crude_change}
            return httpx.Response(200, json={"status": "success", "data": data})
        if path.startswith("/v3/historical-candle/"):
            return httpx.Response(200, json={"status": "success", "data": {"candles": _candle_rows(candle_count, end=candle_end or AS_OF, price=candle_price)}})
        if path == "/v2/news":
            return httpx.Response(200, json={"status": "success", "data": {}, "metadata": {"page": {"total_records": 0}}})
        if path == "/v2/option/chain":
            calls["chain"] += 1
            if calls["chain"] <= chain_fails:
                return httpx.Response(503, text="temporarily unavailable")
            return httpx.Response(200, json=_chain_body(spot=chain_spot if chain_spot is not None else spot, extra_strikes=extra_chain_strikes))
        raise AssertionError(f"unexpected path {path}")

    return handler


def _provider(handler: Callable[[httpx.Request], httpx.Response]) -> UpstoxProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return UpstoxProvider(client=client, access_token="tok")


def _repos(tmp_path: Path) -> Repositories:
    return Repositories(
        quotes=JsonlQuoteRepository(tmp_path / "quotes.jsonl"),
        option_chains=JsonlOptionChainRepository(tmp_path / "chains.jsonl"),
        iv_observations=JsonlIvObservationRepository(tmp_path / "iv.jsonl"),
    )


_FAST_CONFIG = PipelineConfig(chain_fetch_attempts=3, chain_fetch_backoff_seconds=0.01)


def test_full_pipeline_produces_a_decision_with_real_shape_data(tmp_path: Path) -> None:
    provider = _provider(_router(candle_count=60))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    assert report.error is None
    assert report.spot is not None
    assert report.atm_strike is not None
    assert report.matrix is not None
    assert report.decision is not None
    assert report.decision.decision in (FinalDecision.TRADEABLE, FinalDecision.WATCH, FinalDecision.NO_TRADE, FinalDecision.DATA_INSUFFICIENT)
    assert report.total_latency_seconds > 0
    stage_names = {sl.stage for sl in report.stage_latencies}
    assert {"instrument_resolution", "quote", "historical_candles", "option_chain", "futures", "decision"} <= stage_names
    # Renders without raising -- the exact requested report format.
    text = report.render_text()
    assert "OPTIONS INTELLIGENCE" in text
    assert "RESEARCH STATE" in text
    assert "COMPATIBILITY DECISION" in text


def test_compact_report_renders_all_nine_sections(tmp_path: Path) -> None:
    provider = _provider(_router(candle_count=60))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    assert report.error is None
    text = report.render_compact()
    for heading in (
        "MARKET DIRECTION", "SUPPORT / RESISTANCE", "OPTIONS CHAIN", "IV / VOLATILITY", "DECAY",
        "GLOBAL CONTEXT", "NEWS / EVENTS", "EVIDENCE CONVERGENCE", "RESEARCH STATE / COMPATIBILITY DECISION",
    ):
        assert heading in text
    assert "NOT AVAILABLE FROM CURRENT AUTHORIZED DATA SOURCES" in text  # news honestly never fabricated
    assert "CE:" in text and "PE:" in text


def test_futures_leg_clock_skewed_timestamp_degrades_gracefully_not_a_crash(tmp_path: Path) -> None:
    """Final Pre-Freeze Trader Decision-Support Audit -- a real, live-
    reproducing P0 defect (found 2026-08-31, real market open, INDIGO):
    the futures leg's own `last_trade_time` arriving a few seconds after
    this request's frozen `as_of` raised an uncaught
    `pydantic.ValidationError` inside `DataFreshness`'s real, correct
    no-look-ahead guard, crashing the ENTIRE analysis -- not just the
    optional futures section.

    A small skew (5s, well within `DefaultNormalizer.normalize_quote()`'s
    bounded clock-skew tolerance) is now fixed at the ROOT (floored, not
    skipped) since that same root cause turned out to also hit the
    MANDATORY underlying-quote leg live (RELIANCE/NIFTY/BANKNIFTY/
    UNOMINDA all failed in one real Daily Researcher run 2026-08-31) --
    this test now proves the futures data is genuinely preserved, not
    discarded. `test_futures_leg_severely_bad_timestamp_still_degrades_gracefully`
    below covers the beyond-tolerance case, where the futures leg's own
    `ValidationError` handling (this section is optional/supplementary,
    unlike the underlying quote) is still the real, needed safety net."""
    provider = _provider(_router(candle_count=60, fut_last_trade_time_ahead_seconds=5.0))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    assert report.error is None
    assert report.decision is not None
    assert report.futures_ltp is not None  # root-caused fix: floored and preserved, not discarded
    assert report.futures_instrument_key is not None
    assert not any("futures quote timestamp invalid" in w for w in report.data_warnings)


def test_futures_leg_severely_bad_timestamp_still_degrades_gracefully(tmp_path: Path) -> None:
    """Beyond the normalizer's bounded clock-skew tolerance (here: 1
    hour ahead), the futures leg's own `ValidationError` handling is
    still the real safety net -- this is far more likely a genuine
    provider defect (like the real 2026-08-28 malformed-`last_trade_time`
    bug) than ordinary pipeline latency, and must still degrade this one
    optional section rather than being silently floored."""
    provider = _provider(_router(candle_count=60, fut_last_trade_time_ahead_seconds=3600.0))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    assert report.error is None
    assert report.decision is not None
    assert report.futures_ltp is None
    assert report.futures_instrument_key is None
    assert any("futures quote timestamp invalid" in w for w in report.data_warnings)


def test_compact_report_avoid_line_carries_the_contracts_own_structural_quality(tmp_path: Path) -> None:
    """Final Pre-Freeze Product Analytical Pass, Issue 2 -- a real, traced
    defect: `render_compact()`'s FINAL DECISION section printed a bare
    "AVOID" for the non-favored side even when that side's own contract
    (via `direction_comparison`, already computed independently of bias)
    was structurally ACCEPTABLE -- reading identically to a genuinely
    poor contract. The AVOID line must now carry that already-computed
    quality so a reader never conflates "not currently favored" with
    "this contract itself is bad"."""
    provider = _provider(_router(candle_count=60))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    assert report.decision is not None
    assert report.direction_comparison is not None
    text = report.render_compact()
    ce_line = next(line for line in text.splitlines() if line.startswith("CE:"))
    pe_line = next(line for line in text.splitlines() if line.startswith("PE:"))
    avoid_line = ce_line if ce_line.split()[1] == "AVOID" else pe_line
    assert "contract structural quality:" in avoid_line
    assert "not a contract-quality rejection" in avoid_line


def test_compact_report_never_shows_ce_and_pe_both_actionable(tmp_path: Path) -> None:
    # A directional bias can only ever make ONE side (CE or PE) actionable
    # -- the other must always read AVOID, by construction of the
    # existing bias-gated candidate engine.
    provider = _provider(_router(candle_count=60))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    assert report.decision is not None
    text = report.render_compact()
    ce_line = next(line for line in text.splitlines() if line.startswith("CE:"))
    pe_line = next(line for line in text.splitlines() if line.startswith("PE:"))
    # The verdict token is the first word after "CE:"/"PE:" -- an
    # explanatory parenthetical (contract structural quality) may follow
    # "AVOID" on the same line (Final Pre-Freeze Audit, Issue 2), so this
    # checks the verdict token itself, not the last word on the line.
    ce_verdict = ce_line.split()[1]
    pe_verdict = pe_line.split()[1]
    assert not (ce_verdict != "AVOID" and pe_verdict != "AVOID")


def test_pipeline_populates_adversarial_analysis_from_the_same_matrix(tmp_path: Path) -> None:
    provider = _provider(_router(candle_count=60))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    assert report.error is None
    assert report.matrix is not None
    assert report.adversarial_analysis is not None
    # Every bull/bear-case line traces back to a real row in `report.matrix`
    # -- adversarial analysis is pure synthesis, never a new fact.
    matrix_names = {r.name for r in report.matrix.rows}
    for line in report.adversarial_analysis.bull_case + report.adversarial_analysis.bear_case:
        assert line.split(":", 1)[0] in matrix_names
    text = report.render_compact()
    assert "ADVERSARIAL ANALYSIS" in text
    assert "BULL CASE:" in text and "BEAR CASE:" in text
    assert "CONTRADICTIONS:" in text and "MISSING DATA:" in text and "KEY RISKS:" in text


def test_pipeline_populates_quality_tiers_consistent_with_decision(tmp_path: Path) -> None:
    provider = _provider(_router(candle_count=60))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    assert report.error is None
    assert report.decision is not None
    assert report.quality_tiers is not None
    assert report.quality_tiers.data_quality == report.decision.assessment.data_quality
    text = report.render_compact()
    assert "QUALITY SUMMARY" in text
    assert "DATA QUALITY:" in text and "EVIDENCE QUALITY:" in text and "DECISION QUALITY:" in text


def test_unresolvable_symbol_returns_error_report_not_a_crash(tmp_path: Path) -> None:
    provider = _provider(_router())
    report = asyncio.run(
        analyze_symbol(
            "NOT_A_REAL_SYMBOL", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    assert report.error is not None
    assert report.decision is None


def _multi_strike_chain_body(*, spot: float = 1300.0) -> dict[str, object]:
    def leg(ce_ltp: float, pe_ltp: float, oi: int = 50000, volume: int = 5000) -> dict[str, object]:
        return {
            "call_options": {"instrument_key": CE_KEY, "market_data": {"ltp": ce_ltp, "bid_price": ce_ltp - 0.5, "ask_price": ce_ltp + 0.5, "volume": volume, "oi": oi, "prev_oi": oi - 1000}, "option_greeks": {"iv": 18.0, "delta": 0.5, "theta": -2.0, "gamma": 0.01, "vega": 1.0}},
            "put_options": {"instrument_key": PE_KEY, "market_data": {"ltp": pe_ltp, "bid_price": pe_ltp - 0.5, "ask_price": pe_ltp + 0.5, "volume": volume, "oi": oi, "prev_oi": oi - 1000}, "option_greeks": {"iv": 17.0, "delta": -0.5, "theta": -1.8, "gamma": 0.01, "vega": 1.0}},
        }

    return {
        "status": "success",
        "data": [
            {"expiry": EXPIRY.isoformat(), "strike_price": 1200.0, "underlying_spot_price": spot, **leg(105.0, 3.0)},
            {"expiry": EXPIRY.isoformat(), "strike_price": 1300.0, "underlying_spot_price": spot, **leg(30.0, 28.0)},
            {"expiry": EXPIRY.isoformat(), "strike_price": 1400.0, "underlying_spot_price": spot, **leg(4.0, 100.0)},
        ],
    }


def _multi_strike_router(
    *, status: str = "NORMAL_OPEN", news_fail: bool = False, news_heading: str | None = None,
    news_published_ms: int | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v2/market/status/NSE":
            return httpx.Response(200, json={"status": "success", "data": {"status": status}})
        if path == "/v2/market-quote/quotes":
            keys = request.url.params["instrument_key"].split(",")
            data: dict[str, object] = {}
            for k in keys:
                if k == RELIANCE_KEY:
                    data["NSE_EQ:RELIANCE"] = {"instrument_token": k, "last_price": 1300.0, "net_change": 5.0, "volume": 1000000}
                elif k == FUT_KEY:
                    data["NSE_FO:FUT"] = {"instrument_token": k, "last_price": 1302.0, "net_change": 4.0, "oi": 1000000, "volume": 500000}
            return httpx.Response(200, json={"status": "success", "data": data})
        if path.startswith("/v3/historical-candle/"):
            return httpx.Response(200, json={"status": "success", "data": {"candles": _candle_rows(60, end=AS_OF)}})
        if path == "/v2/news":
            if news_fail:
                return httpx.Response(503, text="simulated news provider outage")
            if news_heading is not None:
                return httpx.Response(
                    200,
                    json={
                        "status": "success",
                        "data": {RELIANCE_KEY: [{"heading": news_heading, "summary": "x", "article_link": "https://x", "thumbnail": None, "published_time": news_published_ms or 1787740313933}]},
                        "metadata": {"page": {"total_records": 1}},
                    },
                )
            return httpx.Response(200, json={"status": "success", "data": {}, "metadata": {"page": {"total_records": 0}}})
        if path == "/v2/option/chain":
            return httpx.Response(200, json=_multi_strike_chain_body())
        raise AssertionError(f"unexpected path {path}")

    return handler


def test_requested_contract_analyzed_independent_of_bias(tmp_path: Path) -> None:
    provider = _provider(_multi_strike_router())
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
            requested_strike=Decimal("1300"), requested_right=OptionRight.CE,
        )
    )
    assert report.error is None
    assert report.requested_contract is not None
    assert report.requested_contract.requested is not None
    assert report.requested_contract.requested.strike == Decimal("1300")
    assert report.requested_contract.requested.right == OptionRight.CE
    # Independent of whatever bias/decision the evidence matrix reached --
    # a requested contract is analyzed regardless (master directive rule).
    assert report.decision is not None


def test_requested_contract_alternatives_are_same_right_and_exclude_requested(tmp_path: Path) -> None:
    provider = _provider(_multi_strike_router())
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
            requested_strike=Decimal("1300"), requested_right=OptionRight.CE,
        )
    )
    assert report.requested_contract is not None
    alternatives = report.requested_contract.alternatives
    assert alternatives  # 1200 and 1400 CE both exist
    assert all(alt.right == OptionRight.CE for alt in alternatives)
    assert Decimal("1300") not in [alt.strike for alt in alternatives]


def test_requested_contract_not_in_chain_reported_honestly(tmp_path: Path) -> None:
    provider = _provider(_multi_strike_router())
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
            requested_strike=Decimal("9999"), requested_right=OptionRight.CE,
        )
    )
    assert report.requested_contract is not None
    assert report.requested_contract.requested is None
    assert report.requested_contract.requested_not_in_chain_detail is not None


def test_no_requested_contract_field_when_none_asked(tmp_path: Path) -> None:
    provider = _provider(_multi_strike_router())
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    assert report.requested_contract is None


def test_requested_contract_section_appears_in_both_report_formats(tmp_path: Path) -> None:
    provider = _provider(_multi_strike_router())
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
            requested_strike=Decimal("1300"), requested_right=OptionRight.CE,
        )
    )
    compact = report.render_compact()
    detailed = report.render_text()
    assert "REQUESTED CONTRACT" in compact and "CONTRACT ALTERNATIVES" in compact
    assert "REQUESTED CONTRACT" in detailed and "CONTRACT ALTERNATIVES" in detailed
    assert "PREFERENCE:" in compact


# -- Sprint 4: news ----------------------------------------------------------


def test_news_items_populated_from_real_provider_response(tmp_path: Path) -> None:
    provider = _provider(_multi_strike_router(news_heading="Reliance beats estimates"))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    assert report.error is None
    assert len(report.news_items) == 1
    assert report.news_items[0].title == "Reliance beats estimates"
    assert report.news_fetch_error is None
    news_row = next(r for r in report.matrix.rows if r.name == "News/Events")  # type: ignore[union-attr]
    assert news_row.direction.value == "NEUTRAL"


def test_news_fetch_failure_does_not_break_the_rest_of_the_analysis(tmp_path: Path) -> None:
    provider = _provider(_multi_strike_router(news_fail=True))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    assert report.error is None  # the rest of the analysis still succeeds
    assert report.news_items == []
    assert report.news_fetch_error is not None
    news_row = next(r for r in report.matrix.rows if r.name == "News/Events")  # type: ignore[union-attr]
    assert news_row.direction.value == "UNKNOWN"
    assert report.decision is not None  # decision still computed despite the news failure


def test_news_never_directional_even_with_many_real_items(tmp_path: Path) -> None:
    provider = _provider(_multi_strike_router(news_heading="Some real headline"))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    assert report.matrix is not None
    assert report.matrix.overall_convergence().value != "CONVERGENCE_BULLISH" or any(
        r.group.value != "news_event" for r in report.matrix.rows if r.direction.value == "BULLISH"
    )
    # Structural: the News/Events row itself is never BULLISH/BEARISH.
    news_row = next(r for r in report.matrix.rows if r.name == "News/Events")
    assert news_row.direction.value in ("NEUTRAL", "UNKNOWN")


def test_news_does_not_mutate_support_resistance(tmp_path: Path) -> None:
    provider_without_news = _provider(_multi_strike_router())
    provider_with_news = _provider(_multi_strike_router(news_heading="Big positive news near resistance"))
    report_a = asyncio.run(
        analyze_symbol("RELIANCE", provider=provider_without_news, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(), repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG)
    )
    report_b = asyncio.run(
        analyze_symbol("RELIANCE", provider=provider_with_news, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(), repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG)
    )
    assert [lv.strike for lv in report_a.support_levels] == [lv.strike for lv in report_b.support_levels]
    assert [lv.strike for lv in report_a.resistance_levels] == [lv.strike for lv in report_b.resistance_levels]


# -- Sprint 4: direction-neutral CE/PE comparison ----------------------------


def test_direction_comparison_populated_with_both_ce_and_pe(tmp_path: Path) -> None:
    provider = _provider(_multi_strike_router())
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    assert report.direction_comparison is not None
    assert report.direction_comparison.ce_assessment is not None
    assert report.direction_comparison.pe_assessment is not None
    assert report.direction_comparison.ce_assessment.right == OptionRight.CE
    assert report.direction_comparison.pe_assessment.right == OptionRight.PE


def test_direction_comparison_uses_atm_when_no_contract_requested(tmp_path: Path) -> None:
    provider = _provider(_multi_strike_router())
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    assert report.direction_comparison is not None
    assert report.direction_comparison.reference_strike == report.atm_strike


def test_direction_comparison_reference_strike_is_requested_strike_when_given(tmp_path: Path) -> None:
    provider = _provider(_multi_strike_router())
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
            requested_strike=Decimal("1200"), requested_right=OptionRight.PE,
        )
    )
    assert report.direction_comparison is not None
    assert report.direction_comparison.reference_strike == Decimal("1200")
    # The requested RIGHT (PE) must not privilege PE -- CE is still fully analyzed at the same strike.
    assert report.direction_comparison.ce_assessment is not None
    assert report.direction_comparison.ce_assessment.strike == Decimal("1200")


def test_fo_ineligible_underlying_returns_explicit_error(tmp_path: Path) -> None:
    master = [_MASTER[0]]  # only the equity entry, no NSE_FO segment at all
    provider = _provider(_router(candle_count=60))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=master, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    assert report.error is not None
    assert "F&O" in report.error


def test_insufficient_candle_history_stops_before_options_stages(tmp_path: Path) -> None:
    provider = _provider(_router(candle_count=10))  # below the strategy's 50-candle minimum
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    assert report.error is not None
    assert report.matrix is None
    assert report.candidates == []


def test_chain_fetch_retries_and_recovers_from_transient_failure(tmp_path: Path) -> None:
    provider = _provider(_router(candle_count=60, chain_fails=1))  # fails once, succeeds on retry
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    assert report.error is None
    assert report.atm_strike is not None


def test_chain_fetch_fails_after_exhausting_retries(tmp_path: Path) -> None:
    provider = _provider(_router(candle_count=60, chain_fails=99))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    assert report.error is not None
    assert "option chain" in report.error.lower() or "chain" in report.error.lower()


def test_market_closed_is_not_treated_as_a_technical_failure(tmp_path: Path) -> None:
    provider = _provider(_router(status="CLOSING_END", candle_count=60))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    assert report.error is None
    assert report.market_status_label == "MARKET_CLOSED"
    assert report.decision is not None  # still produces a full analysis, per Phase 6's explicit instruction


def test_pre_open_auction_reads_pre_market_not_generic_market_closed(tmp_path: Path) -> None:
    """Final Hardening Pass, Phase 2 -- wired end to end: the real NSE
    pre-open call-auction exchange status produces the distinct PRE_MARKET
    label, not the generic MARKET_CLOSED_LATEST_DATA."""
    provider = _provider(_router(status="PRE_OPEN_START", candle_count=60))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    assert report.error is None
    assert report.data_state.value == "PRE_MARKET"
    assert report.market_status_label == "PRE_MARKET"
    assert report.decision is not None  # still produces a full analysis, same as plain market-closed


def test_second_run_gets_real_change_in_oi_from_persisted_first_run(tmp_path: Path) -> None:
    repos = _repos(tmp_path)
    provider1 = _provider(_router(candle_count=60, spot=1300.0))
    first = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider1, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=repos, as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    assert first.error is None
    assert any("no prior persisted" in w for w in first.data_warnings)  # honest on the first run

    provider2 = _provider(_router(candle_count=60, spot=1305.0))
    second = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider2, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=repos, as_of=AS_OF + timedelta(minutes=15), config=_FAST_CONFIG,
        )
    )
    assert second.error is None
    assert not any("no prior persisted" in w for w in second.data_warnings)  # second run has real history now
    assert second.matrix is not None
    change_oi_rows = [r for r in second.matrix.rows if r.name.startswith("Change in OI")]
    assert change_oi_rows  # real evidence rows now present, not INSUFFICIENT_DATA-only


def test_level_stability_unconfirmed_on_first_run_then_real_on_second(tmp_path: Path) -> None:
    # S/R Geometry Fix regression note: the shared single-strike fixture
    # (strike 1300 == spot 1300) can no longer produce ANY support/
    # resistance candidate on its own -- a strike exactly at spot is
    # neither (see support_resistance.py's geometry fix) -- so this test
    # now supplies real extra strikes genuinely below/above spot.
    extra_strikes = (1250.0, 1350.0)
    repos = _repos(tmp_path)
    provider1 = _provider(_router(candle_count=60, spot=1300.0, extra_chain_strikes=extra_strikes))
    first = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider1, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=repos, as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    assert first.level_stability
    assert all(ls.state.value == "UNCONFIRMED" for ls in first.level_stability)

    provider2 = _provider(_router(candle_count=60, spot=1300.0, extra_chain_strikes=extra_strikes))
    second = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider2, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=repos, as_of=AS_OF + timedelta(minutes=15), config=_FAST_CONFIG,
        )
    )
    assert second.level_stability
    # Same OI both runs -> real, non-UNCONFIRMED stability read against the real prior snapshot.
    assert any(ls.state.value != "UNCONFIRMED" for ls in second.level_stability)


def test_level_classifications_include_vwap_and_ema_technical_only_entries(tmp_path: Path) -> None:
    """Sprint 7A, Objective 11 -- VWAP/EMA levels are wired end to end
    into `report.level_classifications`, either standalone (TECHNICAL_PRICE)
    or upgraded to CONFLUENCE/STRONGER_CONFLUENCE when a real OI level
    lands nearby -- never silently dropped."""
    provider = _provider(_router(candle_count=60, spot=1300.0))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    sources = {lc.source for lc in report.level_classifications}
    # At minimum, VWAP/EMA levels were computed and classified into SOME
    # real bucket (OI_STRUCTURAL/TECHNICAL_PRICE/CONFLUENCE/STRONGER_CONFLUENCE) --
    # never silently absent.
    assert sources  # real classifications were produced this run
    assert sources <= {"OI_STRUCTURAL", "TECHNICAL_PRICE", "CONFLUENCE", "STRONGER_CONFLUENCE"}


def test_pcr_change_row_is_unknown_on_first_run_then_real_on_second(tmp_path: Path) -> None:
    """Sprint 7A, Objective 4 -- end to end: `row_pcr_change()` genuinely
    needs a real prior persisted snapshot (never available on a symbol's
    very first analysis), then becomes real (never UNKNOWN) once one
    exists."""
    repos = _repos(tmp_path)
    provider1 = _provider(_router(candle_count=60, spot=1300.0))
    first = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider1, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=repos, as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    assert first.matrix is not None
    pcr_change_row_1 = next(r for r in first.matrix.rows if r.name == "PCR change")
    assert pcr_change_row_1.direction == EvidenceDirection.UNKNOWN

    provider2 = _provider(_router(candle_count=60, spot=1300.0))
    second = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider2, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=repos, as_of=AS_OF + timedelta(minutes=15), config=_FAST_CONFIG,
        )
    )
    assert second.matrix is not None
    pcr_change_row_2 = next(r for r in second.matrix.rows if r.name == "PCR change")
    assert pcr_change_row_2.direction != EvidenceDirection.UNKNOWN  # real prior snapshot now exists


def test_basis_change_insufficient_on_first_run_then_real_on_second(tmp_path: Path) -> None:
    """Final Hardening Pass, Phase 6 -- real prior basis, recovered with
    zero new fetch (both the underlying's and the futures leg's own
    quotes are already persisted to the same `repositories.quotes` store
    every run)."""
    repos = _repos(tmp_path)
    provider1 = _provider(_router(candle_count=60, spot=1300.0, fut_price=1302.0))
    first = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider1, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=repos, as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    assert first.error is None
    assert first.futures_basis_change is not None
    assert first.futures_basis_change.direction.value == "insufficient_data"

    # Second run: same spot, futures LTP moved further above spot -- a
    # real, meaningful basis widening.
    provider2 = _provider(_router(candle_count=60, spot=1300.0, fut_price=1310.0))
    second = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider2, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=repos, as_of=AS_OF + timedelta(minutes=15), config=_FAST_CONFIG,
        )
    )
    assert second.error is None
    assert second.futures_basis_change is not None
    assert second.futures_basis_change.direction.value == "widening_premium"
    assert second.futures_basis_change.previous_basis_pct is not None

    # Never fed into the evidence matrix -- informational only.
    assert second.matrix is not None
    assert not any("basis change" in r.name.lower() or "basis_change" in r.name.lower() for r in second.matrix.rows)


def test_oi_migration_wired_end_to_end_and_insufficient_without_prior_snapshot(tmp_path: Path) -> None:
    """Sprint 7A, Objective 3 -- end to end: both CE and PE migration are
    computed and attached to the real report; genuinely INSUFFICIENT_DATA
    on a symbol's first-ever analysis (no real prior snapshot yet)."""
    provider = _provider(_router(candle_count=60, spot=1300.0))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    assert report.ce_oi_migration is not None
    assert report.pe_oi_migration is not None
    assert report.ce_oi_migration.direction == OIMigrationDirection.INSUFFICIENT_DATA
    assert report.ce_oi_migration.entries  # real current-OI entries still populated


def test_relative_strength_outperforming_wired_end_to_end(tmp_path: Path) -> None:
    """Sprint 7A, Objective 6 -- RELIANCE's own real (small, positive)
    day-change vs a real, sharply negative NIFTY day-change -> real
    OUTPERFORMING evidence, zero new fetch."""
    provider = _provider(_router(candle_count=60, spot=1300.0, nifty_change=-500.0))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    assert report.matrix is not None
    rs_row = next(r for r in report.matrix.rows if r.name == "Relative strength")
    assert rs_row.direction == EvidenceDirection.BULLISH
    assert "OUTPERFORMING" in rs_row.detail


def test_realized_volatility_wired_end_to_end(tmp_path: Path) -> None:
    """Sprint 7A, Objective 7 -- real windows computed from the real M15
    candle series; with only ~15 real trading hours of history in this
    fixture (60 M15 candles), 5D is likely OK while 10D/20D honestly read
    INSUFFICIENT_DATA -- both are legitimate, real outcomes."""
    provider = _provider(_router(candle_count=60, spot=1300.0))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    assert report.realized_vol_5d is not None
    assert report.realized_vol_10d is not None
    assert report.realized_vol_20d is not None
    assert report.iv_vs_realized_state is not None
    assert report.iv_vs_realized_detail is not None
    # 60 M15 candles at 15-min spacing span well under 20 real trading
    # days -- 20D must never be silently fabricated from insufficient history.
    assert report.realized_vol_20d.status == "INSUFFICIENT_DATA"


def test_freshness_label_is_populated(tmp_path: Path) -> None:
    provider = _provider(_router(candle_count=60))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    assert report.freshness_label is not None


def test_decay_attribution_honestly_insufficient_without_full_greeks_in_the_fixture(tmp_path: Path) -> None:
    # The shared test fixture's option_greeks only carries iv/delta -- no
    # gamma/vega/theta -- so a real decomposition cannot be computed; this
    # asserts the pipeline reports that honestly rather than guessing.
    repos = _repos(tmp_path)
    provider1 = _provider(_router(candle_count=60, spot=1300.0))
    asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider1, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=repos, as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    provider2 = _provider(_router(candle_count=60, spot=1305.0))
    second = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider2, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=repos, as_of=AS_OF + timedelta(minutes=15), config=_FAST_CONFIG,
        )
    )
    assert second.decay_attributions
    assert all(da.confidence.value == "INSUFFICIENT_DATA" for da in second.decay_attributions)


def test_anomalies_report_insufficient_history_with_thin_real_sample(tmp_path: Path) -> None:
    # Only 1-2 real prior snapshots exist -- far below the default
    # min_sample_size of 5 -- so every anomaly check must honestly read
    # INSUFFICIENT_HISTORY rather than fabricate a baseline.
    repos = _repos(tmp_path)
    provider1 = _provider(_router(candle_count=60, spot=1300.0))
    asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider1, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=repos, as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    provider2 = _provider(_router(candle_count=60, spot=1305.0))
    second = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider2, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=repos, as_of=AS_OF + timedelta(minutes=15), config=_FAST_CONFIG,
        )
    )
    assert second.anomalies
    assert all(a.verdict.value == "INSUFFICIENT_HISTORY" for a in second.anomalies)


def test_iv_observation_is_persisted_across_runs(tmp_path: Path) -> None:
    repos = _repos(tmp_path)
    provider = _provider(_router(candle_count=60))
    asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=repos, as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    history = asyncio.run(repos.iv_observations.query_history(underlying=RELIANCE_KEY, as_of=AS_OF, lookback=timedelta(days=1)))
    assert len(history) == 1


# -- multi-expiry term structure (Phase 4) ----------------------------------

_NEXT_EXPIRY = date(2026, 10, 29)
_NEXT_EXPIRY_MS = 1793298599000  # 2026-10-29 18:29:59 UTC
_NEXT_CE_KEY = "NSE_FO|11"
_NEXT_PE_KEY = "NSE_FO|12"

_MULTI_EXPIRY_MASTER: list[dict[str, object]] = [
    *_MASTER,
    {
        "segment": "NSE_FO", "underlying_symbol": "RELIANCE", "instrument_type": "CE", "expiry": _NEXT_EXPIRY_MS,
        "weekly": False, "lot_size": 500, "instrument_key": _NEXT_CE_KEY, "strike_price": 1300.0,
    },
    {
        "segment": "NSE_FO", "underlying_symbol": "RELIANCE", "instrument_type": "PE", "expiry": _NEXT_EXPIRY_MS,
        "weekly": False, "lot_size": 500, "instrument_key": _NEXT_PE_KEY, "strike_price": 1300.0,
    },
]


def _multi_expiry_router(*, candle_count: int = 60, spot: float = 1300.0) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v2/market/status/NSE":
            return httpx.Response(200, json={"status": "success", "data": {"status": "NORMAL_OPEN"}})
        if path == "/v2/market-quote/quotes":
            keys = request.url.params["instrument_key"].split(",")
            data: dict[str, object] = {}
            for k in keys:
                if k == RELIANCE_KEY:
                    data["NSE_EQ:RELIANCE"] = {"instrument_token": k, "last_price": spot, "net_change": 5.0, "volume": 1000000}
                elif k == FUT_KEY:
                    data["NSE_FO:FUT"] = {"instrument_token": k, "last_price": spot + 2.0, "net_change": 4.0, "oi": 1000000, "volume": 500000}
            return httpx.Response(200, json={"status": "success", "data": data})
        if path.startswith("/v3/historical-candle/"):
            return httpx.Response(200, json={"status": "success", "data": {"candles": _candle_rows(candle_count, end=AS_OF)}})
        if path == "/v2/news":
            return httpx.Response(200, json={"status": "success", "data": {}, "metadata": {"page": {"total_records": 0}}})
        if path == "/v2/option/chain":
            requested_expiry = request.url.params["expiry_date"]
            # Different real IV per expiry -- proves each expiry's chain was
            # really fetched separately, not reused from the primary one.
            if requested_expiry == _NEXT_EXPIRY.isoformat():
                body = _chain_body(spot=spot)
                body["data"][0]["expiry"] = _NEXT_EXPIRY.isoformat()  # type: ignore[index]
                body["data"][0]["call_options"]["option_greeks"]["iv"] = 25.0  # type: ignore[index]
                body["data"][0]["call_options"]["instrument_key"] = _NEXT_CE_KEY  # type: ignore[index]
                body["data"][0]["put_options"]["instrument_key"] = _NEXT_PE_KEY  # type: ignore[index]
                return httpx.Response(200, json=body)
            return httpx.Response(200, json=_chain_body(spot=spot))
        raise AssertionError(f"unexpected path {path}")

    return handler


def test_multi_expiry_term_structure_fetches_and_compares_real_second_expiry(tmp_path: Path) -> None:
    provider = _provider(_multi_expiry_router())
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MULTI_EXPIRY_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    assert report.error is None
    assert report.term_structure is not None
    assert [e.expiry for e in report.term_structure.expiries] == [EXPIRY, _NEXT_EXPIRY]
    # The second expiry's real (different) ATM IV was actually fetched, not reused:
    assert report.term_structure.expiries[1].iv.atm_ce_iv == Decimal("25.0")
    slope = report.term_structure.iv_slope()
    assert slope is not None  # both expiries have known IV -> a real slope is computable
    assert "term_structure" in {sl.stage for sl in report.stage_latencies}


def test_single_expiry_underlying_term_structure_has_exactly_one_entry(tmp_path: Path) -> None:
    provider = _provider(_router(candle_count=60))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    assert report.term_structure is not None
    assert len(report.term_structure.expiries) == 1  # RELIANCE has only one real expiry in this fixture -- never padded to 3


# -- temporal evidence (Phase 6) ---------------------------------------------


def test_temporal_observations_populated_on_a_later_run_against_real_persisted_history(tmp_path: Path) -> None:
    repos = _repos(tmp_path)
    provider1 = _provider(_router(candle_count=60, spot=1300.0))
    first = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider1, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=repos, as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    assert first.temporal_observations == []  # no prior snapshot exists yet -- honestly empty, not fabricated

    provider2 = _provider(_router(candle_count=60, spot=1310.0))
    second = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider2, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=repos, as_of=AS_OF + timedelta(minutes=5), config=_FAST_CONFIG,
        )
    )
    assert second.temporal_observations  # real prior snapshot found -> real observations
    obs = second.temporal_observations[0]
    assert obs.interval == timedelta(minutes=5)  # the REAL elapsed time between the two runs, not a fabricated bucket
    assert obs.t1 - obs.t0 == obs.interval


# -- global / market-wide context (Phase 12) --------------------------------


def test_global_context_tailwind_from_real_index_quotes(tmp_path: Path) -> None:
    provider = _provider(_router(candle_count=60, nifty_change=200.0, banknifty_change=400.0, vix_change=-1.0))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    assert report.error is None
    assert report.global_context is not None
    assert report.global_context.verdict.value == "GLOBAL_TAILWIND"
    assert report.matrix is not None
    global_rows = [r for r in report.matrix.rows if r.name == "Global context"]
    assert global_rows and global_rows[0].direction.value == "BULLISH"


def test_market_regime_context_wired_end_to_end_from_the_same_real_index_quotes(tmp_path: Path) -> None:
    """Sprint 7B, Objective 1 -- reuses the SAME real NIFTY/BANKNIFTY/VIX
    quotes `global_context` already consumes, zero new fetch. RISK_ON
    given a real risk-on-shaped move (NIFTY up, BANKNIFTY up, VIX down)."""
    provider = _provider(_router(candle_count=60, nifty_change=200.0, banknifty_change=400.0, vix_change=-1.0))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    assert report.market_regime_context is not None
    assert report.market_regime_context.regime.value == "RISK_ON"


def test_news_event_and_transmission_notes_wired_end_to_end(tmp_path: Path) -> None:
    """Sprint 7B, Objectives 2/5/6 -- a real EARNINGS-shaped headline
    classifies as EARNINGS with a real recency bucket; a real
    GEOPOLITICAL-shaped headline produces a real transmission note over
    the SAME real crude/VIX/USDINR moves already fetched for this run."""
    published_earnings = int((AS_OF - timedelta(hours=1)).timestamp() * 1000)
    published_geo = int((AS_OF - timedelta(days=2)).timestamp() * 1000)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v2/market/status/NSE":
            return httpx.Response(200, json={"status": "success", "data": {"status": "NORMAL_OPEN"}})
        if path == "/v2/market-quote/quotes":
            keys = request.url.params["instrument_key"].split(",")
            data: dict[str, object] = {}
            for k in keys:
                if k == RELIANCE_KEY:
                    data["NSE_EQ:RELIANCE"] = {"instrument_token": k, "last_price": 1300.0, "net_change": 5.0, "volume": 1000000}
                elif k == FUT_KEY:
                    data["NSE_FO:FUT"] = {"instrument_token": k, "last_price": 1302.0, "net_change": 4.0, "oi": 1000000, "volume": 500000}
                elif k == CRUDE_KEY:
                    data["MCX_FO:CRUDEOIL"] = {"instrument_token": k, "last_price": 6180.0, "net_change": 180.0}
            return httpx.Response(200, json={"status": "success", "data": data})
        if path.startswith("/v3/historical-candle/"):
            return httpx.Response(200, json={"status": "success", "data": {"candles": _candle_rows(60, end=AS_OF)}})
        if path == "/v2/news":
            return httpx.Response(200, json={"status": "success", "data": {RELIANCE_KEY: [
                {"heading": "RELIANCE posts Q2 results, net profit up", "summary": None, "article_link": None, "thumbnail": None, "published_time": published_earnings},
                {"heading": "Middle East tension escalates, crude supply risk rises", "summary": None, "article_link": None, "thumbnail": None, "published_time": published_geo},
            ]}})
        if path == "/v2/option/chain":
            return httpx.Response(200, json=_chain_body(spot=1300.0))
        raise AssertionError(f"unexpected path {path}")

    provider = _provider(handler)
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG, mcx_instrument_master=_MCX_MASTER,
        )
    )
    assert len(report.news_event_notes) == 2
    earnings_note = next(n for n in report.news_event_notes if "Q2 results" in n.item.title)
    assert earnings_note.category.value == "EARNINGS"
    assert earnings_note.recency.value == "INTRADAY"
    geo_note = next(n for n in report.news_event_notes if "Middle East" in n.item.title)
    assert geo_note.category.value == "GEOPOLITICAL"
    assert geo_note.recency.value == "3D"

    assert len(report.transmission_notes) == 1  # only the GEOPOLITICAL item produces a transmission note
    transmission = report.transmission_notes[0]
    # `mcx_instrument_master` is supplied here, so the real MCX Crude Oil
    # day-change (last_price=6180.0, net_change=180.0 -> up) is genuinely
    # available and drives direction -- never fabricated from the
    # headline's own tone.
    assert transmission.affected_market_variable == "MCX Crude Oil"
    assert transmission.market_variable_day_change_pct is not None
    assert transmission.direction == "POSITIVE"
    assert transmission.sector_exposure == "UNKNOWN"
    assert transmission.company_exposure == "UNKNOWN"


def test_market_regime_and_transmission_never_appear_in_the_evidence_matrix(tmp_path: Path) -> None:
    """Sprint 7B, Objective 17 -- geopolitical/macro context cannot
    override company-specific evidence: `market_regime_context`/
    `transmission_notes`/`news_event_notes` are real, wired fields on the
    report, but structurally never feed `EvidenceMatrix`/`decide()` --
    proven end-to-end, not just by source inspection."""
    published_geo = int((AS_OF - timedelta(hours=1)).timestamp() * 1000)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v2/market/status/NSE":
            return httpx.Response(200, json={"status": "success", "data": {"status": "NORMAL_OPEN"}})
        if path == "/v2/market-quote/quotes":
            keys = request.url.params["instrument_key"].split(",")
            data: dict[str, object] = {}
            for k in keys:
                if k == RELIANCE_KEY:
                    data["NSE_EQ:RELIANCE"] = {"instrument_token": k, "last_price": 1300.0, "net_change": 5.0, "volume": 1000000}
                elif k == FUT_KEY:
                    data["NSE_FO:FUT"] = {"instrument_token": k, "last_price": 1302.0, "net_change": 4.0, "oi": 1000000, "volume": 500000}
                elif k == CRUDE_KEY:
                    data["MCX_FO:CRUDEOIL"] = {"instrument_token": k, "last_price": 6180.0, "net_change": 180.0}
            return httpx.Response(200, json={"status": "success", "data": data})
        if path.startswith("/v3/historical-candle/"):
            return httpx.Response(200, json={"status": "success", "data": {"candles": _candle_rows(60, end=AS_OF)}})
        if path == "/v2/news":
            return httpx.Response(200, json={"status": "success", "data": {RELIANCE_KEY: [
                {"heading": "Middle East tension escalates, crude supply risk rises", "summary": None, "article_link": None, "thumbnail": None, "published_time": published_geo},
            ]}})
        if path == "/v2/option/chain":
            return httpx.Response(200, json=_chain_body(spot=1300.0))
        raise AssertionError(f"unexpected path {path}")

    provider = _provider(handler)
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG, mcx_instrument_master=_MCX_MASTER,
        )
    )
    # Real, non-trivial values exist this run -- this is a genuine test of
    # exclusion, not an accident of empty fields.
    assert report.market_regime_context is not None
    assert len(report.transmission_notes) == 1
    assert report.transmission_notes[0].direction == "POSITIVE"

    assert report.matrix is not None
    row_names = {r.name for r in report.matrix.rows}
    row_groups = {r.group.value for r in report.matrix.rows}
    for forbidden in ("Macro regime", "Transmission", "Geopolitical"):
        assert forbidden not in row_names
    assert "macro_regime" not in row_groups
    assert "transmission" not in row_groups
    assert "geopolitical" not in row_groups

    # Decisive proof, run 2: the geopolitical headline removed, so there
    # is no transmission note and no news-event note at all -- every OTHER
    # input, including the MCX master and the crude quote it resolves,
    # held identical.
    #
    # Corrected 18 Sep 2026. This run previously dropped the CRUDE quote
    # too, which is a confound rather than a sharper test: crude feeds the
    # macro regime AND the genuinely-matrix-resident `Global context` row
    # (`assess_global_context(crude_oil_day_change_pct=...)`), so removing
    # it changed real evidence, not just excluded context. The comparison
    # only appeared to hold because an unrelated defect (the inverted
    # `row_m15_trend` mapping, fixed the same day) forced BOTH runs into
    # CONFLICT, which hid the difference behind an identical NO_TRADE.
    def handler_no_geopolitics(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/news":
            return httpx.Response(200, json={"status": "success", "data": {RELIANCE_KEY: []}})
        return handler(request)

    report2 = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=_provider(handler_no_geopolitics), instrument_master=_MASTER,
            strategy=EMAVWAPAlignmentStrategy(), repositories=_repos(tmp_path), as_of=AS_OF,
            config=_FAST_CONFIG, mcx_instrument_master=_MCX_MASTER,
        )
    )
    assert len(report2.transmission_notes) == 0
    assert len(report2.news_event_notes) == 0
    assert report2.decision is not None and report.decision is not None
    assert report2.decision.decision == report.decision.decision

    # Decisive proof, run 3: the MACRO REGIME itself changed, using the one
    # input that feeds it and is explicitly NOT part of the global-context
    # verdict (`contributes_to_verdict=False` for USD/INR -- see
    # `app.domain.options.global_context`). Real macro context therefore
    # differs while every matrix-resident evidence row is untouched.
    def handler_macro_shifted(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v2/market-quote/quotes":
            response = handler(request)
            body = response.json()
            keys = request.url.params["instrument_key"].split(",")
            if USDINR_KEY in keys:
                body["data"]["NCD_FO:USDINR"] = {"instrument_token": USDINR_KEY, "last_price": 88.0, "net_change": -1.2}
            return httpx.Response(200, json=body)
        return handler(request)

    report3 = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=_provider(handler_macro_shifted), instrument_master=_MASTER,
            strategy=EMAVWAPAlignmentStrategy(), repositories=_repos(tmp_path), as_of=AS_OF,
            config=_FAST_CONFIG, mcx_instrument_master=_MCX_MASTER,
        )
    )
    assert report3.market_regime_context is not None
    assert report3.market_regime_context.regime != report.market_regime_context.regime, (
        "this run is only a proof of exclusion if the macro regime genuinely differs"
    )
    assert report3.matrix is not None and report.matrix is not None
    assert [(r.name, r.direction) for r in report3.matrix.rows] == [
        (r.name, r.direction) for r in report.matrix.rows
    ], "a macro-regime change must leave every evidence row's direction untouched"
    assert report3.decision is not None
    assert report3.decision.decision == report.decision.decision


def test_context_day_change_rejects_a_zero_last_price_as_impossible(tmp_path: Path) -> None:
    """Real live UAT defect (2026-09-02): a real USD/INR NCD_FO quote came
    back with `last_price == 0` (impossible for a live currency future),
    which previously produced a nonsensical exact -100.00% day-change
    silently treated as real, contributing macro-regime evidence. Same
    'impossible price' discipline as `quality_gate._check_impossible_price_single()`."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v2/market/status/NSE":
            return httpx.Response(200, json={"status": "success", "data": {"status": "NORMAL_OPEN"}})
        if path == "/v2/market-quote/quotes":
            keys = request.url.params["instrument_key"].split(",")
            data: dict[str, object] = {}
            for k in keys:
                if k == RELIANCE_KEY:
                    data["NSE_EQ:RELIANCE"] = {"instrument_token": k, "last_price": 1300.0, "net_change": 5.0, "volume": 1000000}
                elif k == FUT_KEY:
                    data["NSE_FO:FUT"] = {"instrument_token": k, "last_price": 1302.0, "net_change": 4.0, "oi": 1000000, "volume": 500000}
                elif k == USDINR_KEY:
                    # The real, live-observed defect shape: last_price=0.
                    data["NCD_FO:USDINR"] = {"instrument_token": k, "last_price": 0.0, "net_change": -88.0}
            return httpx.Response(200, json={"status": "success", "data": data})
        if path.startswith("/v3/historical-candle/"):
            return httpx.Response(200, json={"status": "success", "data": {"candles": _candle_rows(60, end=AS_OF)}})
        if path == "/v2/news":
            return httpx.Response(200, json={"status": "success", "data": {}})
        if path == "/v2/option/chain":
            return httpx.Response(200, json=_chain_body(spot=1300.0))
        raise AssertionError(f"unexpected path {path}")

    provider = _provider(handler)
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    assert report.error is None
    # The impossible USD/INR quote must never surface as a real -100%
    # macro-regime input -- it is honestly excluded, not silently used.
    assert report.market_regime_context is not None
    usdinr_input = next(i for i in report.market_regime_context.inputs if i.label == "USD/INR")
    assert usdinr_input.day_change_pct is None
    assert usdinr_input.contributes is False


def test_global_context_insufficient_data_when_index_entries_absent(tmp_path: Path) -> None:
    master_without_indices = [e for e in _MASTER if e.get("segment") != "NSE_INDEX"]
    provider = _provider(_router(candle_count=60))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=master_without_indices, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    assert report.error is None
    assert report.global_context is not None
    assert report.global_context.verdict.value == "INSUFFICIENT_DATA"


def test_global_context_wires_real_usdinr_and_crude_when_mcx_master_supplied(tmp_path: Path) -> None:
    provider = _provider(_router(candle_count=60, usdinr_change=0.5, crude_change=-90.0))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG, mcx_instrument_master=_MCX_MASTER,
        )
    )
    assert report.error is None
    assert report.global_context is not None
    labels = {i.label: i for i in report.global_context.inputs}
    assert "USD/INR" in labels and labels["USD/INR"].day_change_pct is not None
    assert "MCX Crude Oil" in labels and labels["MCX Crude Oil"].day_change_pct is not None
    assert labels["USD/INR"].contributes_to_verdict is False  # informational only, per global_context.py's own rule


def test_derivative_quote_failure_does_not_take_down_index_quotes(tmp_path: Path) -> None:
    # Real regression, found live 2026-08-29: batching NIFTY/BANKNIFTY/VIX
    # together with USD/INR/crude in one request meant a real parse
    # failure on the newer derivative keys silently zeroed out the
    # previously-working index data too. Must now be isolated.
    provider = _provider(_router(candle_count=60, nifty_change=200.0, fail_derivative_quotes=True))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG, mcx_instrument_master=_MCX_MASTER,
        )
    )
    assert report.error is None
    assert report.global_context is not None
    labels = {i.label: i for i in report.global_context.inputs}
    assert labels["NIFTY 50"].day_change_pct is not None  # index data survives the derivative-side failure
    assert labels["USD/INR"].day_change_pct is None  # the failed side is honestly reported as unavailable
    assert labels["MCX Crude Oil"].day_change_pct is None
    assert any("USD/INR or crude oil quote fetch failed" in w for w in report.data_warnings)


def test_global_context_omits_crude_when_no_mcx_master_supplied(tmp_path: Path) -> None:
    provider = _provider(_router(candle_count=60))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,  # no mcx_instrument_master
        )
    )
    assert report.error is None
    assert report.global_context is not None
    crude_input = next(i for i in report.global_context.inputs if i.label == "MCX Crude Oil")
    assert crude_input.day_change_pct is None  # honestly omitted, never guessed from a proxy


# -- decay viability (Stage 8) -----------------------------------------------


def test_decay_viability_has_exactly_one_entry_per_candidate(tmp_path: Path) -> None:
    provider = _provider(_router(candle_count=60))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    assert report.error is None
    assert len(report.decay_viability) == len(report.candidates)
    assert {(dv.strike, dv.right) for dv in report.decay_viability} == {(c.strike, c.right) for c in report.candidates}


def test_decay_viability_reports_insufficient_data_when_greeks_incomplete(tmp_path: Path) -> None:
    # The shared test fixture's option_greeks carries iv/delta but no
    # gamma/vega/theta -- decay viability needs theta, so every real
    # candidate this fixture can produce must honestly read
    # INSUFFICIENT_DATA rather than a fabricated ratio.
    provider = _provider(_router(candle_count=60))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    assert all(dv.verdict.value == "INSUFFICIENT_DATA" for dv in report.decay_viability)


def test_no_order_execution_capability_anywhere_in_the_pipeline_module() -> None:
    import app.orchestration.options_intelligence_pipeline as module

    source = Path(module.__file__).read_text(encoding="utf-8").lower()
    for forbidden in ("place_order", "modify_order", "cancel_order"):
        assert forbidden not in source


# ============================================================
# Sprint 2, Objective 10 (P2 -- "Stage-2 per-symbol calls have no retry
# behavior") -- classified as a genuine reliability defect (real live
# failures: UNOMINDA/BANKNIFTY quote-fetch timeouts) and fixed with the
# smallest bounded retry, reusing the EXACT SAME `_retry()` helper/
# exception split the option-chain fetch already uses -- never a second
# retry mechanism, never unbounded, never retrying a genuinely malformed
# response.
# ============================================================


def test_underlying_quote_fetch_retries_a_transient_failure_and_recovers(tmp_path: Path) -> None:
    provider = _provider(_router(underlying_quote_fails=1))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF,
            config=PipelineConfig(chain_fetch_attempts=1, chain_fetch_backoff_seconds=0.01, underlying_quote_fetch_attempts=2, underlying_quote_fetch_backoff_seconds=0.01),
        )
    )
    assert report.error is None  # the transient first attempt never surfaces as a symbol-wide failure
    assert report.spot is not None


def test_underlying_quote_retry_never_duplicates_persisted_quote(tmp_path: Path) -> None:
    """Sprint 5, Objective 7 -- audit gap closed with a test, not a code
    change: `_retry()` wraps only the RAW fetch; `repositories.quotes.save()`
    is called exactly once, AFTER `_retry()` already returned a single
    successful result -- a failed-then-recovered attempt must never
    persist more than one real quote record for the same analysis."""
    provider = _provider(_router(underlying_quote_fails=1))
    quotes_path = tmp_path / "quotes.jsonl"
    asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=Repositories(
                quotes=JsonlQuoteRepository(quotes_path), option_chains=JsonlOptionChainRepository(tmp_path / "chains.jsonl"),
                iv_observations=JsonlIvObservationRepository(tmp_path / "iv.jsonl"),
            ),
            as_of=AS_OF,
            config=PipelineConfig(chain_fetch_attempts=1, chain_fetch_backoff_seconds=0.01, underlying_quote_fetch_attempts=2, underlying_quote_fetch_backoff_seconds=0.01),
        )
    )
    lines = [line for line in quotes_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    reliance_lines = [line for line in lines if RELIANCE_KEY in line]
    assert len(reliance_lines) == 1  # never duplicated by the retry that preceded the single successful save


def test_underlying_quote_fetch_gives_up_after_exhausting_bounded_retries(tmp_path: Path) -> None:
    provider = _provider(_router(underlying_quote_fails=99))  # persists across every attempt
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF,
            config=PipelineConfig(chain_fetch_attempts=1, chain_fetch_backoff_seconds=0.01, underlying_quote_fetch_attempts=2, underlying_quote_fetch_backoff_seconds=0.01),
        )
    )
    assert report.error is not None
    assert "real quote fetch failed" in report.error


def test_futures_quote_fetch_retries_a_transient_failure_and_recovers(tmp_path: Path) -> None:
    """Sprint 3, Priority 4 -- the optional/supplementary futures-quote
    fetch now also gets the same bounded retry, reusing the underlying-
    quote fetch's own config values."""
    provider = _provider(_router(futures_quote_fails=1, nifty_change=50.0))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF,
            config=PipelineConfig(chain_fetch_attempts=1, chain_fetch_backoff_seconds=0.01, underlying_quote_fetch_attempts=2, underlying_quote_fetch_backoff_seconds=0.01),
        )
    )
    assert report.error is None
    # The transient first attempt never surfaces as a lost futures section
    # -- real futures context is present despite the blip.
    assert report.futures_instrument_key is not None
    assert not any("futures quote fetch failed" in w for w in report.data_warnings)


# ============================================================
# Sprint 7A, Objective 1 -- hard market-data synchronization: real M15
# trend/VWAP/regime evidence must never be computed from a stale
# (previous-session) candle series while a fresh quote exists.
# ============================================================


def _m15_trend_row(report: OptionsIntelligenceReport) -> EvidenceRow:
    assert report.matrix is not None
    return next(r for r in report.matrix.rows if r.name == "M15 trend")


def test_market_data_sync_current_quote_plus_stale_candle_withholds_trend_evidence(tmp_path: Path) -> None:
    """The core Objective 1 scenario: market is open, quote is fresh, but
    the candle series is a full day stale (a real previous-session
    series) -- M15 trend/VWAP/regime must read UNKNOWN, never a
    silently-contaminated direction."""
    stale_end = AS_OF - timedelta(days=1)
    provider = _provider(_router(candle_end=stale_end))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF,
            config=PipelineConfig(chain_fetch_attempts=1, chain_fetch_backoff_seconds=0.01),
        )
    )
    assert report.candles_are_current is False
    assert report.matrix is not None
    trend_row = _m15_trend_row(report)
    vwap_row = next(r for r in report.matrix.rows if r.name == "VWAP")
    regime_row = next(r for r in report.matrix.rows if r.name == "Market regime")
    assert trend_row.direction == EvidenceDirection.UNKNOWN
    assert vwap_row.direction == EvidenceDirection.UNKNOWN
    assert regime_row.direction == EvidenceDirection.UNKNOWN
    assert "current live trading session" in trend_row.detail
    # The real candles themselves are still preserved for charting/history.
    assert len(report.candles) > 0


def test_market_data_sync_current_quote_plus_current_candle_allows_evidence(tmp_path: Path) -> None:
    provider = _provider(_router())  # default: candles end at AS_OF, same as quote
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF,
            config=PipelineConfig(chain_fetch_attempts=1, chain_fetch_backoff_seconds=0.01),
        )
    )
    assert report.candles_are_current is True
    trend_row = _m15_trend_row(report)
    assert trend_row.direction != EvidenceDirection.UNKNOWN


def test_market_data_sync_market_closed_previous_session_candle_is_not_flagged_stale(tmp_path: Path) -> None:
    """When the market is genuinely CLOSED, the latest candle legitimately
    being from the most recent real session is expected, not stale --
    mirrors the same market-state-aware rule already established for
    quote freshness (Sprint 2/3)."""
    provider = _provider(_router(status="CLOSED"))  # candles end at AS_OF -- the real last session
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF,
            config=PipelineConfig(chain_fetch_attempts=1, chain_fetch_backoff_seconds=0.01),
        )
    )
    assert report.candles_are_current is True


def test_market_data_sync_missing_candles_is_handled_by_the_existing_insufficient_history_gate(tmp_path: Path) -> None:
    """Zero real candles must never crash the new sync check. The PRE-
    EXISTING `INSUFFICIENT_HISTORY` precedence rule (candles_available <
    minimum_candles always wins) already short-circuits the whole
    analysis before evidence-matrix construction is even reached --
    `candles_are_current` defaults harmlessly to True since nothing
    downstream reads it on this path."""
    provider = _provider(_router(candle_count=0))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF,
            config=PipelineConfig(chain_fetch_attempts=1, chain_fetch_backoff_seconds=0.01),
        )
    )
    assert report.candles_are_current is True
    assert report.matrix is None
    assert report.error is not None and "INSUFFICIENT_HISTORY" in report.error


def test_market_data_sync_mismatched_timestamps_within_tolerance_still_allows_evidence(tmp_path: Path) -> None:
    """A candle series ending a few minutes before `as_of` (normal --
    an M15 bar only closes every 15 minutes) must NOT be flagged stale;
    only a genuinely stale (previous-session-scale) gap should be."""
    provider = _provider(_router(candle_end=AS_OF - timedelta(minutes=10)))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF,
            config=PipelineConfig(chain_fetch_attempts=1, chain_fetch_backoff_seconds=0.01),
        )
    )
    assert report.candles_are_current is True


# ============================================================
# Sprint 6 -- underlying-price consistency (the real, traced KAYNES root
# cause: three separately-fetched real prices for one underlying, with
# no prior cross-check at all). See app.domain.market.price_consistency's
# own docstring for the full trace.
# ============================================================


def test_price_consistency_detected_end_to_end_reproduces_the_real_kaynes_gap(tmp_path: Path) -> None:
    """The real, live-observed KAYNES scenario: the M15 candle close and
    the option-chain's own reported underlying price differ materially.
    End to end through the real pipeline: classified INCONSISTENT, and
    the existing DATA_INSUFFICIENT gate (`decide()`) withholds the
    decision rather than silently combining the two prices into anything
    downstream. `_candle_rows()` builds the last (60th, i=59) candle's
    close as `candle_price + 59 * 0.5` -- solved backwards so it lands
    exactly on the real observed 3943.10.
    """
    provider = _provider(_router(spot=3685.0, candle_price=3943.10 - 59 * 0.5))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF,
            config=PipelineConfig(chain_fetch_attempts=1, chain_fetch_backoff_seconds=0.01),
        )
    )
    assert report.price_consistency is not None
    assert report.price_consistency.classification == DataConsistency.INCONSISTENT
    labeled = {p.source: p.price for p in report.price_consistency.prices}
    assert labeled[PriceSource.LATEST_QUOTE] == Decimal("3685.0")
    assert labeled[PriceSource.OPTION_CHAIN_REFERENCE] == Decimal("3685.0")
    candle_price = labeled[PriceSource.LATEST_CANDLE]
    assert candle_price is not None
    assert abs(candle_price - Decimal("3943.10")) < Decimal("0.01")
    assert report.decision is not None
    assert report.decision.decision == FinalDecision.DATA_INSUFFICIENT
    assert report.decision.assessment.data_quality == QualityLevel.INSUFFICIENT


def test_price_consistency_passes_when_all_real_sources_agree(tmp_path: Path) -> None:
    """A genuinely consistent-enough snapshot (quote/candle/chain all
    close together, using this fixture module's own existing default
    candle shape) must classify CONSISTENT or PARTIALLY_ALIGNED -- never
    INCONSISTENT -- and must not, by itself, force DATA_INSUFFICIENT."""
    provider = _provider(_router(spot=1300.0))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF,
            config=PipelineConfig(chain_fetch_attempts=1, chain_fetch_backoff_seconds=0.01),
        )
    )
    assert report.price_consistency is not None
    assert report.price_consistency.classification in (DataConsistency.CONSISTENT, DataConsistency.PARTIALLY_ALIGNED)
    # Never fabricates a unified spot -- every real source is still labeled individually.
    labeled_sources = {p.source for p in report.price_consistency.prices}
    assert labeled_sources == {PriceSource.LATEST_QUOTE, PriceSource.LATEST_CANDLE, PriceSource.OPTION_CHAIN_REFERENCE, PriceSource.FUTURES}


def test_live_quote_and_chain_with_stale_m15_does_not_collapse_to_data_insufficient(tmp_path: Path) -> None:
    """Case A -- live quote/chain/futures + previous-session M15 that
    diverges from LTP must NOT become global DATA_INSUFFICIENT solely
    because the candle is stale. Technical votes are withheld; options
    evidence remains."""
    stale_end = AS_OF - timedelta(days=1)
    provider = _provider(_router(spot=365.0, candle_price=320.5, candle_end=stale_end, fut_price=366.0))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF,
            config=PipelineConfig(chain_fetch_attempts=1, chain_fetch_backoff_seconds=0.01),
        )
    )
    assert report.candles_are_current is False
    assert report.price_consistency is not None
    assert report.price_consistency.candle_excluded_as_stale is True
    assert report.price_consistency.classification != DataConsistency.INCONSISTENT
    assert report.decision is not None
    assert report.decision.decision != FinalDecision.DATA_INSUFFICIENT
    assert report.research_state is not None
    assert report.research_state == ResearchState.CONFIRMATION_PENDING
    m15 = next(s for s in report.stream_freshness if s.stream.value == "candles_m15")
    assert m15.label.value == "STALE"
    assert "m15_trend" in m15.withheld_calculations
    assert report.matrix is not None
    assert any(r.name == "Volume" for r in report.matrix.rows)
    text = report.render_text()
    assert "M15 = STALE" in text or "WITHHELD (M15 = STALE" in text


def test_live_quote_vs_live_chain_disagreement_collapses_consistency(tmp_path: Path) -> None:
    """Case A -- live quote 365 vs live chain 350 vs current M15 360 is a
    live-vs-live contradiction, not a stale-candle artifact."""
    provider = _provider(_router(spot=365.0, chain_spot=350.0, candle_price=360.0, fut_price=366.0))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF,
            config=PipelineConfig(chain_fetch_attempts=1, chain_fetch_backoff_seconds=0.01),
        )
    )
    assert report.candles_are_current is True
    assert report.price_consistency is not None
    assert report.price_consistency.classification == DataConsistency.INCONSISTENT
    assert report.decision is not None
    assert report.decision.decision == FinalDecision.DATA_INSUFFICIENT


def test_stale_chain_withholds_option_rows_not_m15(tmp_path: Path) -> None:
    """Case D -- receipt-age STALE_SNAPSHOT must not vote; M15 may still vote.
    Negative max_chain_age is the only way to mark receipt-time `as_of`
    as stale when the producer has no exchange matching timestamp.
    """
    provider = _provider(_router(spot=1300.0))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF,
            config=PipelineConfig(
                chain_fetch_attempts=1, chain_fetch_backoff_seconds=0.01,
                max_chain_age=timedelta(microseconds=-1),
            ),
        )
    )
    assert report.matrix is not None
    volume = next(r for r in report.matrix.rows if r.name == "Volume")
    assert volume.direction == EvidenceDirection.UNKNOWN
    assert "withheld" in volume.detail
    m15 = next(r for r in report.matrix.rows if r.name == "M15 trend")
    assert "option-chain snapshot is not current" not in m15.detail
    assert report.research_state != ResearchState.CONFIRMED_SETUP
    assert report.research_state in (ResearchState.CONFIRMATION_PENDING, ResearchState.CONFLICT)
    chain = next(s for s in report.stream_freshness if s.stream.value == "option_chain")
    assert chain.label.value == "STALE"
    assert "pcr" in chain.withheld_calculations


def test_stale_futures_withholds_futures_rows_not_options(tmp_path: Path) -> None:
    """Case E -- stale futures LTT must not vote; chain/M15 remain usable."""
    provider = _provider(_router(spot=1300.0, fut_last_trade_time=AS_OF - timedelta(hours=2)))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF,
            config=PipelineConfig(chain_fetch_attempts=1, chain_fetch_backoff_seconds=0.01),
        )
    )
    assert report.matrix is not None
    basis = next(r for r in report.matrix.rows if r.name == "Spot/Futures")
    assert basis.direction == EvidenceDirection.UNKNOWN
    volume = next(r for r in report.matrix.rows if r.name == "Volume")
    assert "option-chain snapshot is not current" not in volume.detail
    fut = next(s for s in report.stream_freshness if s.stream.value == "futures")
    assert fut.label.value == "STALE"
    assert report.research_state != ResearchState.CONFIRMATION_PENDING



# ============================================================
# Sprint 8, Objective P1 -- sector classification + 3-level relative strength
# ============================================================

_SECTOR_INDEX_KEY = "NSE_INDEX|Nifty Oil And Gas"
_SECTOR_MASTER: list[dict[str, object]] = [*_MASTER, {
    "segment": "NSE_INDEX", "name": "Nifty Oil And Gas", "exchange": "NSE", "instrument_type": "INDEX",
    "instrument_key": _SECTOR_INDEX_KEY, "trading_symbol": "NIFTY OIL AND GAS",
}]


def _sector_router(*, sector_change: float = 0.0, **kwargs: object) -> Callable[[httpx.Request], httpx.Response]:
    base_handler = _router(**kwargs)  # type: ignore[arg-type]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v2/market-quote/quotes":
            keys = request.url.params["instrument_key"].split(",")
            if _SECTOR_INDEX_KEY in keys:
                data: dict[str, object] = {}
                for k in keys:
                    if k == _SECTOR_INDEX_KEY:
                        data["NSE_INDEX:NIFTYOILANDGAS"] = {"instrument_token": k, "last_price": 100.0, "net_change": sector_change}
                base_response = base_handler(request)
                if base_response.status_code == 200:
                    body = json.loads(base_response.content)
                    body.get("data", {}).update(data)
                    return httpx.Response(200, json=body)
        return base_handler(request)

    return handler


def test_sector_classification_wired_end_to_end_from_real_nse_constituent_map(tmp_path: Path) -> None:
    """RELIANCE's real NSE Nifty 500 industry is 'Oil Gas & Consumable
    Fuels' -- honest, never inferred from the symbol's own name (the
    sector_map here is exactly the shape `nse_sector_index.
    fetch_sector_index()` returns)."""
    provider = _provider(_sector_router(candle_count=60, spot=1300.0, sector_change=2.0))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_SECTOR_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
            sector_map={"RELIANCE": "Oil Gas & Consumable Fuels"},
        )
    )
    assert report.error is None
    assert report.sector_info is not None
    assert report.sector_info.classification.value == "KNOWN"
    assert report.sector_info.industry == "Oil Gas & Consumable Fuels"
    assert report.sector_relative_strength is not None
    assert report.sector_relative_strength.sector_day_change_pct is not None

    # Never appears as a directional evidence-matrix row -- structural
    # context only, per this sprint's own explicit rule.
    assert report.matrix is not None
    assert not any("sector" in r.name.lower() for r in report.matrix.rows)


def test_sector_honestly_unknown_when_symbol_not_in_the_real_constituent_map(tmp_path: Path) -> None:
    provider = _provider(_router(candle_count=60, spot=1300.0))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
            sector_map={"SOMEOTHERSYMBOL": "Information Technology"},  # RELIANCE deliberately absent
        )
    )
    assert report.error is None
    assert report.sector_info is not None
    assert report.sector_info.classification.value == "UNKNOWN"
    # Still a real object (not None) -- but honestly UNKNOWN, never a
    # comparison forced through against an unclassified sector.
    assert report.sector_relative_strength is not None
    assert report.sector_relative_strength.tier.value == "UNKNOWN"
    assert report.sector_relative_strength.sector_day_change_pct is None


def test_sector_none_when_no_sector_map_supplied(tmp_path: Path) -> None:
    """Omitting `sector_map` entirely (the default) leaves sector fields
    honestly None -- never a silent guess."""
    provider = _provider(_router(candle_count=60, spot=1300.0))
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    assert report.error is None
    assert report.sector_info is None
    assert report.sector_relative_strength is None
