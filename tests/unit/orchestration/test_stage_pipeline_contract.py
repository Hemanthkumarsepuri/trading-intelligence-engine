"""Release gate (Sections 5/6/8) -- the explicit Stage-1 -> Stage-2
contract: provider-budget-bound capacity, movement-stage prioritisation,
and exactly one pipeline record per universe symbol."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from app.data.providers.base import RawOHLC, RawQuote
from app.data.providers.request_budget import RequestLedger, api_family
from app.data.providers.upstox_provider import ExchangeStatus
from app.domain.audit.research_models import RejectionRecord
from app.orchestration.daily_research import (
    MOVEMENT_STAGE_ADVANCED,
    MOVEMENT_STAGE_DEVELOPING,
    STAGE_1_ANALYZED,
    STAGE_1_CANDIDATE,
    STAGE_1_FAILED,
    STAGE_1_REJECTED,
    STAGE_2_ANALYZED,
    STAGE_2_DEFERRED_RATE_BUDGET,
    STAGE_2_FAILED,
    STAGE_2_LIMIT_CONFIGURED_CAP,
    STAGE_2_LIMIT_NONE,
    STAGE_2_LIMIT_RATE_BUDGET,
    STAGE_2_NOT_REACHED,
    STAGE_2_SKIPPED_CAPACITY,
    ScreeningConfig,
    build_symbol_pipeline,
    categorize_rejection,
    compute_research_coverage,
    screen_universe,
)
from app.orchestration.dashboard_service import AnalyzeResponse

AS_OF = datetime(2026, 9, 16, 6, 0, tzinfo=UTC)
_EXPIRY_MS = 1790274599000  # 2026-09-24


def _master(symbols: list[str]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for symbol in symbols:
        rows.append({"segment": "NSE_EQ", "trading_symbol": symbol, "instrument_key": f"NSE_EQ|{symbol}"})
        for right in ("CE", "PE", "FUT"):
            rows.append({
                "segment": "NSE_FO", "underlying_symbol": symbol, "instrument_type": right, "expiry": _EXPIRY_MS,
                "weekly": False, "lot_size": 1, "instrument_key": f"NSE_FO|{symbol}{right}",
            })
    return rows


def _quote(key: str, *, last: float, avg: float, low: float, high: float, buy: int | None = None, sell: int | None = None) -> RawQuote:
    return RawQuote(
        security_id=key, last_price=last, previous_close=100.0, average_price=avg,
        ohlc=RawOHLC(open=100.0, high=high, low=low, close=last), total_buy_quantity=buy, total_sell_quantity=sell,
    )


class _FakeBatch:
    def __init__(self, quotes: dict[str, RawQuote]) -> None:
        self._quotes = quotes

    async def get_quotes(self, security_ids: list[str]) -> dict[str, RawQuote]:
        return {k: q for k, q in self._quotes.items() if k in security_ids}


# DEV: +1.0% on the day, 0.2% from VWAP -> DEVELOPING, one dimension (momentum).
# ADV: +5.0% on the day, 1.9% from VWAP, faded to 25% of the range, 2:1 book
#      imbalance -> ADVANCED, two dimensions (order flow + reversal).
# Under the previous ordering ADV's extra dimension ranked it first.
_QUOTES = {
    "NSE_EQ|DEV": _quote("NSE_EQ|DEV", last=101.0, avg=100.8, low=100.0, high=102.0),
    "NSE_EQ|ADV": _quote("NSE_EQ|ADV", last=105.0, avg=103.0, low=104.0, high=108.0, buy=200, sell=100),
}


def _screen(**kwargs: object) -> object:
    return asyncio.run(screen_universe(
        ["ADV", "DEV"], provider=_FakeBatch(_QUOTES), instrument_master=_master(["ADV", "DEV"]), as_of=AS_OF,
        exchange_status=ExchangeStatus.NORMAL_CLOSE, **kwargs,  # type: ignore[arg-type]
    ))


def test_developing_candidate_takes_scarce_capacity_before_an_advanced_mover() -> None:
    result = _screen(config=ScreeningConfig(survivor_cap=1))
    assert result.survivors == ["DEV"]  # type: ignore[attr-defined]
    assert result.candidate_movement_stage == {  # type: ignore[attr-defined]
        "DEV": MOVEMENT_STAGE_DEVELOPING, "ADV": MOVEMENT_STAGE_ADVANCED,
    }
    assert result.stage_two_limit_reason == STAGE_2_LIMIT_CONFIGURED_CAP  # type: ignore[attr-defined]


def test_rate_budget_defers_visibly_with_its_own_reason_and_category() -> None:
    result = _screen(config=ScreeningConfig(survivor_cap=1000), stage_two_budget_capacity=1)
    assert result.survivors == ["DEV"]  # type: ignore[attr-defined]
    assert result.stage_two_limit_reason == STAGE_2_LIMIT_RATE_BUDGET  # type: ignore[attr-defined]
    [deferred] = [r for r in result.rejected if r.symbol == "ADV"]  # type: ignore[attr-defined]
    assert deferred.reason.startswith("STAGE_2_DEFERRED_RATE_BUDGET")
    assert categorize_rejection(deferred.reason).startswith("STAGE_2_DEFERRED_RATE_BUDGET")


def test_ample_budget_analyzes_every_candidate_and_reports_no_limit() -> None:
    result = _screen(config=ScreeningConfig(survivor_cap=1000), stage_two_budget_capacity=500)
    assert sorted(result.survivors) == ["ADV", "DEV"]  # type: ignore[attr-defined]
    assert result.truncated_count == 0  # type: ignore[attr-defined]
    assert result.stage_two_limit_reason == STAGE_2_LIMIT_NONE  # type: ignore[attr-defined]


def test_budget_deferral_is_never_counted_as_a_coverage_failure() -> None:
    rejected = [RejectionRecord(symbol="ADV", reason="STAGE_2_DEFERRED_RATE_BUDGET: matched x")]
    coverage = compute_research_coverage(
        universe_count=2, stage_one_attempted=2, stage_one_rejected=rejected, stage_two_attempted=1, stage_two_rejected=[],
    )
    assert coverage.stage1_failed == 0


def _response(symbol: str, *, error: str | None = None, research_state: str | None = "WATCH") -> AnalyzeResponse:
    return AnalyzeResponse(
        query=symbol, parsed_symbol=symbol, parsed_strike=None, parsed_right=None, parsed_expiry_hint=None,
        has_specific_contract=False, parse_warnings=[], symbol=symbol, error=error, research_state=research_state,
        latency_seconds=0.1,
    )


def test_every_universe_symbol_gets_exactly_one_pipeline_record_with_the_right_states() -> None:
    universe = ["SHORT", "WATCHED", "BROKEN", "CAPPED", "BUDGET", "EXTENDED", "NOQUOTE"]
    records = build_symbol_pipeline(
        universe=universe, stage_one_ran=True,
        stage_one_rejected=[
            RejectionRecord(symbol="CAPPED", reason="STAGE_2_SKIPPED_CAPACITY: matched y"),
            RejectionRecord(symbol="BUDGET", reason="STAGE_2_DEFERRED_RATE_BUDGET: matched z"),
            RejectionRecord(symbol="EXTENDED", reason="already extended intraday: +7%"),
            RejectionRecord(symbol="NOQUOTE", reason="no quote data returned for this symbol"),
        ],
        stage_one_movement_stage={"SHORT": MOVEMENT_STAGE_DEVELOPING},
        stage_one_candidate_observations={"SHORT": ("DEVELOPING_MOMENTUM",)},
        stage_two_universe=["SHORT", "WATCHED", "BROKEN"],
        responses={"SHORT": _response("SHORT"), "WATCHED": _response("WATCHED"), "BROKEN": _response("BROKEN", error="boom")},
        stage_two_rejected=[
            RejectionRecord(symbol="WATCHED", reason="no defensible direction (verdict: NONE)"),
            RejectionRecord(symbol="BROKEN", reason="analysis error: boom"),
        ],
        shortlisted=["SHORT"],
    )
    assert [r.symbol for r in records] == universe
    by = {r.symbol: r for r in records}
    assert (by["SHORT"].stage_1, by["SHORT"].stage_1_result, by["SHORT"].stage_2, by["SHORT"].outcome) == (
        STAGE_1_ANALYZED, STAGE_1_CANDIDATE, STAGE_2_ANALYZED, "SHORTLISTED",
    )
    assert by["SHORT"].stage_1_observations == ["DEVELOPING_MOMENTUM"]
    assert (by["WATCHED"].stage_2, by["WATCHED"].outcome) == (STAGE_2_ANALYZED, "NO_DIRECTIONAL_CONVERGENCE")
    assert by["BROKEN"].stage_2 == STAGE_2_FAILED
    assert (by["CAPPED"].stage_1_result, by["CAPPED"].stage_2) == (STAGE_1_CANDIDATE, STAGE_2_SKIPPED_CAPACITY)
    assert (by["BUDGET"].stage_1_result, by["BUDGET"].stage_2) == (STAGE_1_CANDIDATE, STAGE_2_DEFERRED_RATE_BUDGET)
    assert (by["EXTENDED"].stage_1, by["EXTENDED"].stage_1_result, by["EXTENDED"].stage_2) == (
        STAGE_1_ANALYZED, STAGE_1_REJECTED, STAGE_2_NOT_REACHED,
    )
    assert (by["NOQUOTE"].stage_1, by["NOQUOTE"].stage_2) == (STAGE_1_FAILED, STAGE_2_NOT_REACHED)


def test_api_family_strips_keys_dates_and_parameters() -> None:
    assert api_family("/v2/market-quote/quotes") == "/v2/market-quote/quotes"
    assert api_family("/v3/historical-candle/NSE_EQ|INE002A01018/minutes/15/2026-09-16/2026-09-01") == "/v3/historical-candle"
    assert api_family("/v3/historical-candle/NSE_EQ%7CINE002A01018/days/1/2026-09-16/2025-09-01") == "/v3/historical-candle"
    assert api_family("/v3/historical-candle/intraday/NSE_EQ%7CINE002A01018/minutes/15") == "/v3/historical-candle"
    assert api_family("/v2/option/chain") == "/v2/option/chain"


def test_ledger_capacity_is_the_tightest_api_and_the_window_rolls() -> None:
    now = [0.0]
    ledger = RequestLedger(clock=lambda: now[0], window_seconds=1800)
    cost = {"/v2/market-quote/quotes": 3, "/v2/option/chain": 2}
    assert ledger.stage_two_capacity(limit=2000, reserve=300, cost_per_symbol=cost) == 566
    for _ in range(1400):
        ledger.record("/v2/market-quote/quotes")
    assert ledger.stage_two_capacity(limit=2000, reserve=300, cost_per_symbol=cost) == 100  # (2000-300-1400)//3
    for _ in range(1700):
        ledger.record("/v2/option/chain")
    assert ledger.stage_two_capacity(limit=2000, reserve=300, cost_per_symbol=cost) == 0
    now[0] = 1801.0
    assert ledger.stage_two_capacity(limit=2000, reserve=300, cost_per_symbol=cost) == 566
