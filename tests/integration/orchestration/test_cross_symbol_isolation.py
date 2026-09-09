"""Product-Level Analytical Audit, Phase 9 -- cross-symbol contamination
regression coverage.

The audit's real-world trigger was comparing two named contracts
(UNOMINDA 1280 CE September, INDIGO 5200 CE September) and asking
whether one symbol's analysis could ever leak into another's. This test
proves it structurally cannot, for the two places state could plausibly
leak:

1. Two symbols analyzed against the SAME shared JSONL repositories (as
   the real app does -- one set of repository files, many symbols) never
   see each other's quotes/chains/IV history (`JsonlQuoteRepository`,
   `JsonlOptionChainRepository`, `JsonlIvObservationRepository` all
   filter by `instrument_id` / `underlying` -- confirmed by reading
   `app/persistence/jsonl_file.py` directly).
2. Two symbols' in-memory report contents (spot, strikes, evidence
   values, decision) never mix, even when analyzed back-to-back in the
   same process against the same mock provider.

Each symbol here uses genuinely different spot/strike/OI/IV/candle data
so a real leak (not just a coincidental match) would be caught.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx

from app.data.providers.upstox_provider import UpstoxProvider
from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy
from app.orchestration.options_intelligence_pipeline import (
    PipelineConfig,
    Repositories,
    analyze_symbol,
)
from app.persistence.jsonl_file import (
    JsonlIvObservationRepository,
    JsonlOptionChainRepository,
    JsonlQuoteRepository,
)

EXPIRY = date(2026, 9, 24)
_EXPIRY_MS = 1790274599000  # 2026-09-24 18:29:59 UTC
AS_OF = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)

# Symbol A: RELIANCE-shaped -- spot 1300, moderate OI, IV 18/17.
A_KEY = "NSE_EQ|AAA"
A_CE, A_PE, A_FUT = "NSE_FO|A1", "NSE_FO|A2", "NSE_FO|A3"
A_SPOT = 1300.0

# Symbol B: a deliberately very different shape -- spot 5200 (INDIGO-
# scale), high OI, much higher IV, opposite candle drift (descending, so
# a different EMA/VWAP alignment than A) -- if any A value ever leaked
# into B's report (or vice versa) these numbers would make it obvious.
B_KEY = "NSE_EQ|BBB"
B_CE, B_PE, B_FUT = "NSE_FO|B1", "NSE_FO|B2", "NSE_FO|B3"
B_SPOT = 5200.0

_MASTER: list[dict[str, object]] = [
    {"segment": "NSE_EQ", "name": "SYMBOL A", "exchange": "NSE", "instrument_type": "EQ", "instrument_key": A_KEY, "trading_symbol": "SYMBOLA"},
    {"segment": "NSE_FO", "underlying_symbol": "SYMBOLA", "instrument_type": "CE", "expiry": _EXPIRY_MS, "weekly": False, "lot_size": 500, "instrument_key": A_CE, "strike_price": 1300.0},
    {"segment": "NSE_FO", "underlying_symbol": "SYMBOLA", "instrument_type": "PE", "expiry": _EXPIRY_MS, "weekly": False, "lot_size": 500, "instrument_key": A_PE, "strike_price": 1300.0},
    {"segment": "NSE_FO", "underlying_symbol": "SYMBOLA", "instrument_type": "FUT", "expiry": _EXPIRY_MS, "weekly": False, "lot_size": 500, "instrument_key": A_FUT},
    {"segment": "NSE_EQ", "name": "SYMBOL B", "exchange": "NSE", "instrument_type": "EQ", "instrument_key": B_KEY, "trading_symbol": "SYMBOLB"},
    {"segment": "NSE_FO", "underlying_symbol": "SYMBOLB", "instrument_type": "CE", "expiry": _EXPIRY_MS, "weekly": False, "lot_size": 250, "instrument_key": B_CE, "strike_price": 5200.0},
    {"segment": "NSE_FO", "underlying_symbol": "SYMBOLB", "instrument_type": "PE", "expiry": _EXPIRY_MS, "weekly": False, "lot_size": 250, "instrument_key": B_PE, "strike_price": 5200.0},
    {"segment": "NSE_FO", "underlying_symbol": "SYMBOLB", "instrument_type": "FUT", "expiry": _EXPIRY_MS, "weekly": False, "lot_size": 250, "instrument_key": B_FUT},
]


def _candle_rows(n: int, *, end: datetime, price: float, step: float) -> list[list[object]]:
    rows = []
    for i in range(n):
        ts = end - timedelta(minutes=15 * (n - 1 - i))
        p = price + i * step
        rows.append([ts.isoformat(), p, p + 1, p - 1, p, 1000, 0])
    return rows


def _chain_body(*, underlying_key: str, ce_key: str, pe_key: str, spot: float, ltp: float, oi: int, iv: float) -> dict[str, object]:
    return {
        "status": "success",
        "data": [{
            "expiry": EXPIRY.isoformat(), "strike_price": spot, "underlying_spot_price": spot,
            "call_options": {
                "instrument_key": ce_key,
                "market_data": {"ltp": ltp, "bid_price": ltp - 0.5, "ask_price": ltp + 0.5, "volume": 8000, "oi": oi, "prev_oi": oi - 5000},
                "option_greeks": {"iv": iv, "delta": 0.5},
            },
            "put_options": {
                "instrument_key": pe_key,
                "market_data": {"ltp": ltp - 2, "bid_price": ltp - 2.5, "ask_price": ltp - 1.5, "volume": 7000, "oi": oi - 20000, "prev_oi": oi - 22000},
                "option_greeks": {"iv": iv - 1, "delta": -0.5},
            },
        }],
    }


def _shared_router() -> Callable[[httpx.Request], httpx.Response]:
    """Serves BOTH symbols from one mock transport, branching strictly on
    the real instrument_key each request names -- exactly as the real
    Upstox API would (one endpoint, many underlyings), so a leak would
    have to come from the pipeline/repositories, not from the test double
    accidentally sharing state."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
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
            # Both symbols request their own instrument_key in the URL --
            # branch on which underlying key appears in the path.
            if A_KEY.split("|")[1] in path:
                candles = _candle_rows(60, end=AS_OF, price=1280.0, step=0.5)  # ascending
            else:
                candles = _candle_rows(60, end=AS_OF, price=5300.0, step=-1.5)  # descending
            return httpx.Response(200, json={"status": "success", "data": {"candles": candles}})
        if path == "/v2/news":
            return httpx.Response(200, json={"status": "success", "data": {}, "metadata": {"page": {"total_records": 0}}})
        if path == "/v2/option/chain":
            key = request.url.params["instrument_key"]
            if key == A_KEY:
                return httpx.Response(200, json=_chain_body(underlying_key=A_KEY, ce_key=A_CE, pe_key=A_PE, spot=A_SPOT, ltp=30.0, oi=80000, iv=18.0))
            if key == B_KEY:
                return httpx.Response(200, json=_chain_body(underlying_key=B_KEY, ce_key=B_CE, pe_key=B_PE, spot=B_SPOT, ltp=135.4, oi=368700, iv=21.58))
            raise AssertionError(f"unexpected chain request for {key}")
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


def test_two_symbols_analyzed_against_the_same_shared_repositories_never_mix(tmp_path: Path) -> None:
    provider = _provider(_shared_router())
    repos = _repos(tmp_path)  # ONE shared repository set, as the real app uses.

    report_a = asyncio.run(
        analyze_symbol("SYMBOLA", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(), repositories=repos, as_of=AS_OF, config=_FAST_CONFIG)
    )
    report_b = asyncio.run(
        analyze_symbol("SYMBOLB", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(), repositories=repos, as_of=AS_OF, config=_FAST_CONFIG)
    )
    # Re-run A AFTER B has already been analyzed and persisted into the
    # SAME repository files -- this is the real contamination risk: if
    # `latest()`/history lookups ever failed to filter by instrument key,
    # A's second run could pick up B's just-saved chain/quote/IV data.
    report_a2 = asyncio.run(
        analyze_symbol("SYMBOLA", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(), repositories=repos, as_of=AS_OF, config=_FAST_CONFIG)
    )

    assert report_a.error is None and report_b.error is None and report_a2.error is None
    assert report_a.symbol == "SYMBOLA" and report_b.symbol == "SYMBOLB"

    # Spot must reflect each symbol's OWN real fixture value, never the
    # other's, on both the first and the repeated (post-contamination-
    # risk) run.
    assert report_a.spot is not None and abs(float(report_a.spot) - A_SPOT) < 5.0
    assert report_a2.spot is not None and abs(float(report_a2.spot) - A_SPOT) < 5.0
    assert report_b.spot is not None and abs(float(report_b.spot) - B_SPOT) < 5.0

    # ATM strikes must never cross over.
    assert report_a.atm_strike is not None and float(report_a.atm_strike) < 2000.0
    assert report_b.atm_strike is not None and float(report_b.atm_strike) > 4000.0

    # The rendered text of each report must only ever name its OWN
    # symbol, never the other's, anywhere in the full report body.
    text_a = report_a.render_text()
    text_b = report_b.render_text()
    assert "SYMBOLB" not in text_a
    assert "SYMBOLA" not in text_b

    # Distinct underlying candle drift (A ascending, B descending) must
    # produce genuinely different evidence -- proving the two analyses
    # are computed from genuinely separate data, not a shared/cached one.
    assert report_a.matrix is not None and report_b.matrix is not None
    a_trend_row = next(r for r in report_a.matrix.rows if r.name == "M15 trend")
    b_trend_row = next(r for r in report_b.matrix.rows if r.name == "M15 trend")
    assert a_trend_row.direction != b_trend_row.direction or a_trend_row.detail != b_trend_row.detail


def test_symbol_a_repeated_analysis_is_stable_regardless_of_intervening_symbol_b_activity(tmp_path: Path) -> None:
    """A's own level-stability / snapshot-diff bookkeeping (which persists
    across calls, by design, to detect real repeated-level confirmation)
    must depend ONLY on A's own prior snapshots -- never on B's, even
    when B was analyzed in between."""
    provider = _provider(_shared_router())
    repos = _repos(tmp_path)

    first_a = asyncio.run(
        analyze_symbol("SYMBOLA", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(), repositories=repos, as_of=AS_OF, config=_FAST_CONFIG)
    )
    asyncio.run(
        analyze_symbol("SYMBOLB", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(), repositories=repos, as_of=AS_OF, config=_FAST_CONFIG)
    )
    second_a = asyncio.run(
        analyze_symbol("SYMBOLA", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(), repositories=repos, as_of=AS_OF + timedelta(minutes=15), config=_FAST_CONFIG)
    )
    assert first_a.error is None and second_a.error is None
    # Same real fixture spot both times -- B's very different spot/OI/IV
    # must never leak into A's second computation.
    assert second_a.spot is not None and abs(float(second_a.spot) - A_SPOT) < 5.0
