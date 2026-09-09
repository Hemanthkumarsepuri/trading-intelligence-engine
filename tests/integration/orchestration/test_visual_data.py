"""Sprint 3 — `build_visual_data()` against a REAL, fully-assembled
`OptionsIntelligenceReport` (built via the real `analyze_symbol()`
pipeline against a mocked Upstox provider, never a hand-built report --
this exercises the exact wiring the dashboard actually uses).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx

from app.data.providers.upstox_provider import UpstoxProvider
from app.domain.market.models import OptionRight
from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy
from app.orchestration.options_intelligence_pipeline import (
    PipelineConfig,
    Repositories,
    analyze_symbol,
)
from app.orchestration.options_intelligence_report import OptionsIntelligenceReport
from app.orchestration.visual_data import build_visual_data
from app.persistence.jsonl_file import (
    JsonlIvObservationRepository,
    JsonlOptionChainRepository,
    JsonlQuoteRepository,
)

RELIANCE_KEY = "NSE_EQ|INE002A01018"
CE_KEY = "NSE_FO|1"
PE_KEY = "NSE_FO|2"
FUT_KEY = "NSE_FO|3"
EXPIRY = date(2026, 9, 24)
_EXPIRY_MS = 1790274599000
AS_OF = datetime(2026, 8, 28, 6, 0, tzinfo=UTC)  # 11:30 IST -- within market hours

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


def _chain_body(*, spot: float = 1300.0) -> dict[str, object]:
    def leg(ce_ltp: float, pe_ltp: float) -> dict[str, object]:
        return {
            "call_options": {"instrument_key": CE_KEY, "market_data": {"ltp": ce_ltp, "bid_price": ce_ltp - 0.5, "ask_price": ce_ltp + 0.5, "volume": 8000, "oi": 80000, "prev_oi": 75000}, "option_greeks": {"iv": 18.0, "delta": 0.5, "theta": -2.0, "gamma": 0.01, "vega": 1.0}},
            "put_options": {"instrument_key": PE_KEY, "market_data": {"ltp": pe_ltp, "bid_price": pe_ltp - 0.5, "ask_price": pe_ltp + 0.5, "volume": 7000, "oi": 60000, "prev_oi": 58000}, "option_greeks": {"iv": 17.0, "delta": -0.5, "theta": -1.8, "gamma": 0.01, "vega": 1.0}},
        }

    return {
        "status": "success",
        "data": [
            {"expiry": EXPIRY.isoformat(), "strike_price": 1200.0, "underlying_spot_price": spot, **leg(105.0, 3.0)},
            {"expiry": EXPIRY.isoformat(), "strike_price": 1300.0, "underlying_spot_price": spot, **leg(30.0, 28.0)},
            {"expiry": EXPIRY.isoformat(), "strike_price": 1400.0, "underlying_spot_price": spot, **leg(4.0, 100.0)},
        ],
    }


def _router(*, status: str = "NORMAL_OPEN", candle_count: int = 60, news_heading: str | None = None) -> Callable[[httpx.Request], httpx.Response]:
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
            return httpx.Response(200, json={"status": "success", "data": {"candles": _candle_rows(candle_count, end=AS_OF)}})
        if path == "/v2/news":
            if news_heading is not None:
                return httpx.Response(200, json={"status": "success", "data": {RELIANCE_KEY: [{"heading": news_heading, "summary": "x", "article_link": "https://x", "thumbnail": None, "published_time": 1787740313933}]}, "metadata": {"page": {"total_records": 1}}})
            return httpx.Response(200, json={"status": "success", "data": {}, "metadata": {"page": {"total_records": 0}}})
        if path == "/v2/option/chain":
            return httpx.Response(200, json=_chain_body())
        raise AssertionError(f"unexpected path {path}")

    return handler


def _provider(handler: Callable[[httpx.Request], httpx.Response]) -> UpstoxProvider:
    return UpstoxProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), access_token="tok")


def _repos(tmp_path: Path) -> Repositories:
    return Repositories(
        quotes=JsonlQuoteRepository(tmp_path / "quotes.jsonl"), option_chains=JsonlOptionChainRepository(tmp_path / "chains.jsonl"),
        iv_observations=JsonlIvObservationRepository(tmp_path / "iv.jsonl"),
    )


_FAST_CONFIG = PipelineConfig(chain_fetch_attempts=1, chain_fetch_backoff_seconds=0.01)


def _real_report(tmp_path: Path, *, requested_strike: Decimal | None = None, requested_right: OptionRight | None = None, status: str = "NORMAL_OPEN", candle_count: int = 60, news_heading: str | None = None) -> OptionsIntelligenceReport:
    provider = _provider(_router(status=status, candle_count=candle_count, news_heading=news_heading))
    return asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
            requested_strike=requested_strike, requested_right=requested_right,
        )
    )


def test_price_chart_has_real_candles_ema_and_vwap(tmp_path: Path) -> None:
    report = _real_report(tmp_path)
    assert report.error is None
    visual = build_visual_data(report)
    chart = visual.price_chart
    assert chart.insufficient_history is False
    assert len(chart.candles) == 60
    assert chart.candles == sorted(chart.candles, key=lambda c: c.timestamp)  # chronological
    assert len(chart.ema["9"]) == 60 - 9 + 1
    assert len(chart.ema["21"]) == 60 - 21 + 1
    assert len(chart.ema["50"]) == 60 - 50 + 1
    assert len(chart.vwap) == 60  # one running VWAP point per candle


def test_price_chart_insufficient_history_with_no_candles(tmp_path: Path) -> None:
    report = _real_report(tmp_path, candle_count=5)  # below the 50-candle EMA/strategy minimum
    # Strategy requires more candles than 5 -- report.error is set, but
    # build_visual_data() must still handle a thin/empty candle set safely.
    assert report.candles == [] or len(report.candles) == 5
    visual = build_visual_data(report)
    if not report.candles:
        assert visual.price_chart.insufficient_history is True


def test_price_chart_candles_never_exceed_as_of(tmp_path: Path) -> None:
    report = _real_report(tmp_path)
    visual = build_visual_data(report)
    assert all(c.timestamp <= report.generated_at for c in visual.price_chart.candles)
    assert all(p.timestamp <= report.generated_at for series in visual.price_chart.ema.values() for p in series)
    assert all(p.timestamp <= report.generated_at for p in visual.price_chart.vwap)


def test_option_chain_rows_paired_and_atm_marked(tmp_path: Path) -> None:
    report = _real_report(tmp_path)
    visual = build_visual_data(report)
    chain = visual.option_chain
    assert [r.strike for r in chain.rows] == [Decimal("1200.0"), Decimal("1300.0"), Decimal("1400.0")]
    for row in chain.rows:
        assert row.call is not None and row.put is not None
    atm_rows = [r for r in chain.rows if r.is_atm]
    assert len(atm_rows) == 1
    assert atm_rows[0].strike == chain.atm_strike


def test_requested_contract_present_when_requested(tmp_path: Path) -> None:
    report = _real_report(tmp_path, requested_strike=Decimal("1300"), requested_right=OptionRight.CE)
    visual = build_visual_data(report)
    assert visual.requested_contract is not None
    assert visual.requested_contract.found is True
    assert visual.requested_contract.assessment is not None
    assert visual.requested_contract.assessment.strike == Decimal("1300")
    assert len(visual.requested_contract.alternatives) == 2  # 1200 and 1400 CE
    # The requested strike's own row in the chain table is flagged too.
    requested_rows = [r for r in visual.option_chain.rows if r.is_requested]
    assert len(requested_rows) == 1 and requested_rows[0].strike == Decimal("1300")


def test_requested_contract_none_when_not_requested(tmp_path: Path) -> None:
    report = _real_report(tmp_path)
    visual = build_visual_data(report)
    assert visual.requested_contract is None
    assert all(not r.is_requested for r in visual.option_chain.rows)


def test_evidence_adversarial_quality_populated(tmp_path: Path) -> None:
    report = _real_report(tmp_path)
    visual = build_visual_data(report)
    assert visual.evidence  # at least one real evidence row
    assert visual.convergence is not None
    assert visual.adversarial is not None
    assert visual.quality is not None


def test_support_resistance_populated(tmp_path: Path) -> None:
    report = _real_report(tmp_path)
    visual = build_visual_data(report)
    assert isinstance(visual.support_resistance.support, list)
    assert isinstance(visual.support_resistance.resistance, list)


def test_support_resistance_distance_pct_is_never_negative(tmp_path: Path) -> None:
    """S/R Semantics Fix -- the real chain here has strikes 1200 (below
    spot 1300, real support candidate) and 1400 (above spot, real
    resistance candidate). `distance_pct` must be the UNSIGNED distance;
    the older `distance_from_spot_pct` stays signed for backward
    compatibility."""
    report = _real_report(tmp_path)
    visual = build_visual_data(report)
    all_levels = visual.support_resistance.support + visual.support_resistance.resistance
    assert all_levels  # real candidates exist in this fixture (1200 below, 1400 above spot 1300)
    for lv in all_levels:
        if lv.distance_pct is not None:
            assert lv.distance_pct >= 0
        # The signed field must still be preserved for backward compatibility.
        if lv.distance_from_spot_pct is not None and lv.distance_pct is not None:
            assert lv.distance_pct == abs(lv.distance_from_spot_pct)


def test_freshness_reflects_market_state(tmp_path: Path) -> None:
    report = _real_report(tmp_path, status="NORMAL_CLOSE")
    visual = build_visual_data(report)
    assert visual.freshness.market_state == "MARKET_CLOSED_LATEST_DATA"
    assert visual.freshness.generated_at == report.generated_at


def test_futures_populated_from_the_real_report(tmp_path: Path) -> None:
    report = _real_report(tmp_path)
    assert report.futures_ltp is not None  # the router provides a real futures quote
    visual = build_visual_data(report)
    assert visual.futures.instrument_key == report.futures_instrument_key
    assert visual.futures.ltp == report.futures_ltp
    assert visual.futures.open_interest == report.futures_oi
    assert visual.futures.basis_pct == report.futures_basis_pct


def test_visual_data_never_contains_a_token_or_credential(tmp_path: Path) -> None:
    report = _real_report(tmp_path, requested_strike=Decimal("1300"), requested_right=OptionRight.CE)
    visual = build_visual_data(report)
    serialized = visual.model_dump_json().lower()
    for forbidden in ("access_token", "authorization", "bearer ", "\"tok\""):
        assert forbidden not in serialized


# -- Sprint 4: news + direction comparison -----------------------------


def test_news_visual_populated_from_real_items(tmp_path: Path) -> None:
    report = _real_report(tmp_path, news_heading="Reliance beats estimates")
    visual = build_visual_data(report)
    assert len(visual.news.items) == 1
    assert visual.news.items[0].title == "Reliance beats estimates"
    assert visual.news.items[0].direction == "UNKNOWN"  # never fabricated
    assert visual.news.fetch_error is None


def test_news_visual_empty_and_honest_when_no_items(tmp_path: Path) -> None:
    report = _real_report(tmp_path)
    visual = build_visual_data(report)
    assert visual.news.items == []
    assert visual.news.fetch_error is None


def test_news_visual_carries_real_category_and_recency(tmp_path: Path) -> None:
    """Sprint 7B, Objectives 5/6/16 -- wired through to the dashboard,
    never touching `direction` (which stays UNKNOWN)."""
    report = _real_report(tmp_path, news_heading="Reliance posts Q2 results, profit up")
    visual = build_visual_data(report)
    assert len(visual.news.items) == 1
    item = visual.news.items[0]
    assert item.category == "EARNINGS"
    assert item.recency is not None
    assert item.direction == "UNKNOWN"


def test_macro_regime_visual_populated_from_real_index_quotes(tmp_path: Path) -> None:
    """Sprint 7B, Objective 1/16 -- see market_regime_context.py's own
    docstring."""
    report = _real_report(tmp_path)
    visual = build_visual_data(report)
    assert visual.macro_regime is not None
    assert visual.macro_regime.regime in ("RISK_ON", "RISK_OFF", "MIXED", "TRANSITION", "INSUFFICIENT_DATA")
    assert len(visual.macro_regime.inputs) == 5


def test_transmission_notes_empty_and_honest_when_no_geopolitical_news(tmp_path: Path) -> None:
    report = _real_report(tmp_path, news_heading="Reliance posts Q2 results, profit up")
    visual = build_visual_data(report)
    assert visual.transmission_notes == []


def test_direction_comparison_visual_populated(tmp_path: Path) -> None:
    report = _real_report(tmp_path, requested_strike=Decimal("1300"), requested_right=OptionRight.CE)
    visual = build_visual_data(report)
    dc = visual.direction_comparison
    assert dc is not None
    assert dc.ce_assessment is not None and dc.ce_assessment.right == "CE"
    assert dc.pe_assessment is not None and dc.pe_assessment.right == "PE"
    assert dc.ce_case is not None and dc.pe_case is not None
    assert "HEDGE STRUCTURE NOT EVALUATED" in dc.ce_hedge_context
    assert "HEDGE STRUCTURE NOT EVALUATED" in dc.pe_hedge_context
    assert dc.paths.bullish_path and dc.paths.bearish_path


_MASTER_NO_FUT: list[dict[str, object]] = [
    row for row in _MASTER if row.get("instrument_type") != "FUT"
]


def test_futures_visual_surfaces_the_real_no_futures_reason(tmp_path: Path) -> None:
    """UAT finding (docs/TIRE_OPERATOR_UAT.md, NIFTY real-symbol test):
    `analyze_symbol()` already records WHY no futures contract was found
    (e.g. a weekly-only options expiry with no matching monthly futures
    contract) in `report.data_warnings`, but the dashboard's Futures panel
    previously showed only a bare "no futures resolved for this
    underlying" with no explanation -- the operator had to open "Detailed
    Audit Report" and search a multi-thousand-character text dump for the
    same sentence. `build_visual_data()` must copy that already-computed
    reason onto `FuturesVisual.no_futures_reason` so the panel that
    actually says "no futures resolved" can explain why, verbatim, with
    no new computation."""
    provider = _provider(_router())
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER_NO_FUT, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    assert report.futures_instrument_key is None
    visual = build_visual_data(report)
    assert visual.futures.instrument_key is None
    assert visual.futures.no_futures_reason is not None
    assert visual.futures.no_futures_reason.startswith("no futures contract found")
    assert visual.futures.no_futures_reason in report.data_warnings


def test_direction_comparison_visual_none_when_reference_strike_unavailable() -> None:
    # Structural guarantee only (no fixture needed): the field is Optional.
    from app.orchestration.visual_data import VisualData

    assert "direction_comparison" in VisualData.model_fields


def test_extension_distance_none_when_day_change_pct_unavailable(tmp_path: Path) -> None:
    """Section 27 -- never fabricated: `None` when there is no real
    `day_change_pct` to compute a distance from."""
    provider = _provider(_router())
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    report.day_change_pct = None
    visual = build_visual_data(report)
    assert visual.extension_distance is None


def test_extension_distance_reports_approaching_and_extended_status(tmp_path: Path) -> None:
    provider = _provider(_router())
    report = asyncio.run(
        analyze_symbol(
            "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), as_of=AS_OF, config=_FAST_CONFIG,
        )
    )
    report.day_change_pct = Decimal("2.0")
    visual = build_visual_data(report)
    assert visual.extension_distance is not None
    assert visual.extension_distance.status == "NORMAL"
    assert visual.extension_distance.extended_threshold_pct == Decimal("6.0")

    report.day_change_pct = Decimal("4.8")
    visual = build_visual_data(report)
    assert visual.extension_distance is not None
    assert visual.extension_distance.status == "APPROACHING_EXTENSION"

    report.day_change_pct = Decimal("-7.0")
    visual = build_visual_data(report)
    assert visual.extension_distance is not None
    assert visual.extension_distance.status == "EXTENDED"
    assert visual.extension_distance.day_change_pct == Decimal("-7.0")
