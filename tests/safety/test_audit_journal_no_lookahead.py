"""Phase 19 — dedicated look-ahead-bias tests for the post-analysis audit
journal (Milestone G). Mirrors `tests/safety/test_no_lookahead.py`'s own
rationale: this is its own test category because preventing look-ahead
leakage is a safety property this project cannot regress on silently.

Proves, explicitly:
    - future outcomes never modify the original `AnalysisSnapshot`
    - future option prices/IV/OI never enter the original snapshot
    - future support/resistance never enters the original snapshot
    - `capture_outcome_checkpoint()` refuses to run before its checkpoint
      is genuinely due (same-timestamp, 1-second-after, and boundary
      conditions right at the 5-minute mark)
    - a market-closed capture (including an "overnight"/next-trading-day
      capture) never fabricates an outcome
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from app.data.providers.upstox_provider import UpstoxProvider
from app.domain.audit.models import CheckpointLabel, CheckpointStatus
from app.domain.audit.reconciliation import build_reconciliation
from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy
from app.orchestration.audit_journal import (
    build_analysis_snapshot,
    capture_outcome_checkpoint,
    due_checkpoints,
)
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

RELIANCE_KEY = "NSE_EQ|INE002A01018"
CE_KEY = "NSE_FO|1"
PE_KEY = "NSE_FO|2"
FUT_KEY = "NSE_FO|3"
EXPIRY = date(2026, 9, 24)
_EXPIRY_MS = 1790274599000
GENERATED_AT = datetime(2026, 8, 28, 6, 0, tzinfo=UTC)  # 11:30 IST -- within market hours

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


def _chain_body(*, spot: float, ce_ltp: float) -> dict[str, object]:
    return {
        "status": "success",
        "data": [
            {
                "expiry": EXPIRY.isoformat(), "strike_price": 1300.0, "underlying_spot_price": spot,
                "call_options": {
                    "instrument_key": CE_KEY,
                    "market_data": {"ltp": ce_ltp, "bid_price": ce_ltp - 0.5, "ask_price": ce_ltp + 0.5, "volume": 8000, "oi": 80000, "prev_oi": 75000},
                    "option_greeks": {"iv": 18.0, "delta": 0.5, "theta": -2.0, "gamma": 0.01, "vega": 1.0},
                },
                "put_options": {
                    "instrument_key": PE_KEY,
                    "market_data": {"ltp": 28.0, "bid_price": 27.5, "ask_price": 28.5, "volume": 7000, "oi": 60000, "prev_oi": 58000},
                    "option_greeks": {"iv": 17.0, "delta": -0.5, "theta": -1.8, "gamma": 0.01, "vega": 1.0},
                },
            }
        ],
    }


def _router(*, status: str = "NORMAL_OPEN", checkpoint_spot: float = 1400.0, checkpoint_ce_ltp: float = 60.0) -> Callable[[httpx.Request], httpx.Response]:
    """FUTURE data (a much larger spot/premium jump than any real 5-minute
    move) is only ever returned from the SECOND call onward -- if it ever
    leaked into the FIRST (`generated_at`) analysis, these tests would
    catch it directly on the original snapshot's own fields.
    """
    calls = {"quote": 0, "chain": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v2/market/status/NSE":
            return httpx.Response(200, json={"status": "success", "data": {"status": status}})
        if path == "/v2/market-quote/quotes":
            keys = request.url.params["instrument_key"].split(",")
            data: dict[str, object] = {}
            for k in keys:
                if k == RELIANCE_KEY:
                    calls["quote"] += 1
                    spot = 1300.0 if calls["quote"] == 1 else checkpoint_spot
                    data["NSE_EQ:RELIANCE"] = {"instrument_token": k, "last_price": spot, "net_change": 5.0, "volume": 1000000}
                elif k == FUT_KEY:
                    spot = 1300.0 if calls["quote"] <= 1 else checkpoint_spot
                    data["NSE_FO:FUT"] = {"instrument_token": k, "last_price": spot + 2.0, "net_change": 4.0, "oi": 1000000, "volume": 500000}
            return httpx.Response(200, json={"status": "success", "data": data})
        if path.startswith("/v3/historical-candle/"):
            return httpx.Response(200, json={"status": "success", "data": {"candles": _candle_rows(60, end=GENERATED_AT)}})
        if path == "/v2/news":
            return httpx.Response(200, json={"status": "success", "data": {}, "metadata": {"page": {"total_records": 0}}})
        if path == "/v2/option/chain":
            calls["chain"] += 1
            if calls["chain"] == 1:
                return httpx.Response(200, json=_chain_body(spot=1300.0, ce_ltp=30.0))
            return httpx.Response(200, json=_chain_body(spot=checkpoint_spot, ce_ltp=checkpoint_ce_ltp))
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


# -- immutability: a snapshot cannot be mutated at all -----------------------


def test_analysis_snapshot_is_frozen(tmp_path: Path) -> None:
    provider = _provider(_router())
    report = asyncio.run(analyze_symbol("RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(), repositories=_repos(tmp_path), as_of=GENERATED_AT, config=_FAST_CONFIG))
    snapshot = build_analysis_snapshot(report)
    with pytest.raises(ValidationError):
        snapshot.underlying.spot = snapshot.underlying.spot + 1 if snapshot.underlying.spot else None  # type: ignore[misc]


# -- future data never leaks into the original snapshot -----------------


def test_future_option_price_iv_and_oi_never_enter_the_original_snapshot(tmp_path: Path) -> None:
    provider = _provider(_router(checkpoint_spot=1400.0, checkpoint_ce_ltp=60.0))
    repos = _repos(tmp_path)
    report = asyncio.run(analyze_symbol("RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(), repositories=repos, as_of=GENERATED_AT, config=_FAST_CONFIG))
    assert report.error is None
    snapshot = build_analysis_snapshot(report)

    # Record the ORIGINAL numbers before capturing anything.
    original_spot = snapshot.underlying.spot
    original_ce_iv = snapshot.options.atm_ce_iv
    original_call_oi = snapshot.options.total_call_oi
    original_candidate_ltp = snapshot.decision.candidate_selected.ltp if snapshot.decision.candidate_selected else None

    checkpoint = asyncio.run(
        capture_outcome_checkpoint(
            CheckpointLabel.FIVE_MIN, snapshot, now=GENERATED_AT + timedelta(minutes=5), provider=provider,
            instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(), repositories=repos, config=_FAST_CONFIG,
        )
    )
    # The checkpoint legitimately sees the "future" (later) values...
    assert checkpoint.underlying is not None and checkpoint.underlying.spot == 1400.0

    # ...but the ORIGINAL snapshot -- built and returned BEFORE the
    # checkpoint ever existed -- is completely unaffected by it.
    assert snapshot.underlying.spot == original_spot == 1300.0
    assert snapshot.options.atm_ce_iv == original_ce_iv
    assert snapshot.options.total_call_oi == original_call_oi
    if snapshot.decision.candidate_selected is not None:
        assert snapshot.decision.candidate_selected.ltp == original_candidate_ltp


def test_future_support_resistance_never_enters_the_original_snapshot(tmp_path: Path) -> None:
    provider = _provider(_router(checkpoint_spot=1400.0))
    repos = _repos(tmp_path)
    report = asyncio.run(analyze_symbol("RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(), repositories=repos, as_of=GENERATED_AT, config=_FAST_CONFIG))
    snapshot = build_analysis_snapshot(report)
    original_levels = [(lv.kind, lv.strike, lv.distance_from_spot_pct) for lv in snapshot.levels.levels]

    asyncio.run(
        capture_outcome_checkpoint(
            CheckpointLabel.FIVE_MIN, snapshot, now=GENERATED_AT + timedelta(minutes=5), provider=provider,
            instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(), repositories=repos, config=_FAST_CONFIG,
        )
    )
    assert [(lv.kind, lv.strike, lv.distance_from_spot_pct) for lv in snapshot.levels.levels] == original_levels


def test_reconciliation_never_mutates_the_original_snapshot_it_reads(tmp_path: Path) -> None:
    provider = _provider(_router(checkpoint_spot=1400.0, checkpoint_ce_ltp=60.0))
    repos = _repos(tmp_path)
    report = asyncio.run(analyze_symbol("RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(), repositories=repos, as_of=GENERATED_AT, config=_FAST_CONFIG))
    snapshot = build_analysis_snapshot(report)
    before = snapshot.model_dump_json()

    checkpoint = asyncio.run(
        capture_outcome_checkpoint(
            CheckpointLabel.FIVE_MIN, snapshot, now=GENERATED_AT + timedelta(minutes=5), provider=provider,
            instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(), repositories=repos, config=_FAST_CONFIG,
        )
    )
    build_reconciliation(snapshot, [checkpoint], now=GENERATED_AT + timedelta(minutes=6), near_level_pct_threshold=_FAST_CONFIG.near_level_pct_threshold)

    after = snapshot.model_dump_json()
    assert before == after


# -- checkpoint timestamp boundary conditions -------------------------------


def test_checkpoint_refused_at_the_same_timestamp(tmp_path: Path) -> None:
    provider = _provider(_router())
    repos = _repos(tmp_path)
    report = asyncio.run(analyze_symbol("RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(), repositories=repos, as_of=GENERATED_AT, config=_FAST_CONFIG))
    snapshot = build_analysis_snapshot(report)
    with pytest.raises(ValueError, match="not due yet"):
        asyncio.run(
            capture_outcome_checkpoint(
                CheckpointLabel.FIVE_MIN, snapshot, now=GENERATED_AT, provider=provider, instrument_master=_MASTER,
                strategy=EMAVWAPAlignmentStrategy(), repositories=repos, config=_FAST_CONFIG,
            )
        )


def test_checkpoint_refused_one_second_after_generation(tmp_path: Path) -> None:
    provider = _provider(_router())
    repos = _repos(tmp_path)
    report = asyncio.run(analyze_symbol("RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(), repositories=repos, as_of=GENERATED_AT, config=_FAST_CONFIG))
    snapshot = build_analysis_snapshot(report)
    with pytest.raises(ValueError, match="not due yet"):
        asyncio.run(
            capture_outcome_checkpoint(
                CheckpointLabel.FIVE_MIN, snapshot, now=GENERATED_AT + timedelta(seconds=1), provider=provider,
                instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(), repositories=repos, config=_FAST_CONFIG,
            )
        )


def test_checkpoint_allowed_at_exactly_five_minutes(tmp_path: Path) -> None:
    provider = _provider(_router())
    repos = _repos(tmp_path)
    report = asyncio.run(analyze_symbol("RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(), repositories=repos, as_of=GENERATED_AT, config=_FAST_CONFIG))
    snapshot = build_analysis_snapshot(report)
    checkpoint = asyncio.run(
        capture_outcome_checkpoint(
            CheckpointLabel.FIVE_MIN, snapshot, now=GENERATED_AT + timedelta(minutes=5), provider=provider,
            instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(), repositories=repos, config=_FAST_CONFIG,
        )
    )
    assert checkpoint.status in (CheckpointStatus.RECORDED, CheckpointStatus.MARKET_CLOSED, CheckpointStatus.INSUFFICIENT_DATA)


def test_missing_checkpoint_is_simply_never_due_not_fabricated() -> None:
    # Nothing captured, and not enough time has passed for anything to be
    # due -- `due_checkpoints` must return an empty list, never a guessed
    # placeholder.
    assert due_checkpoints(GENERATED_AT, set(), now=GENERATED_AT + timedelta(minutes=2)) == []


# -- market close / overnight / next trading day -----------------------


def test_market_closed_capture_never_fabricates_an_outcome(tmp_path: Path) -> None:
    provider = _provider(_router(status="NORMAL_CLOSE"))
    repos = _repos(tmp_path)
    report = asyncio.run(analyze_symbol("RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(), repositories=repos, as_of=GENERATED_AT, config=_FAST_CONFIG))
    snapshot = build_analysis_snapshot(report)
    checkpoint = asyncio.run(
        capture_outcome_checkpoint(
            CheckpointLabel.SIXTY_MIN, snapshot, now=GENERATED_AT + timedelta(minutes=60), provider=provider,
            instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(), repositories=repos, config=_FAST_CONFIG,
        )
    )
    assert checkpoint.status == CheckpointStatus.MARKET_CLOSED
    assert checkpoint.underlying is None and checkpoint.option is None


def test_overnight_next_trading_day_capture_never_fabricates_an_outcome(tmp_path: Path) -> None:
    """An EOD checkpoint requested the NEXT calendar day (simulating an
    operator who only gets around to capturing it well after close) must
    still never fabricate a number -- the market really was closed for the
    entire intervening period.
    """
    provider = _provider(_router(status="NORMAL_CLOSE"))
    repos = _repos(tmp_path)
    report = asyncio.run(analyze_symbol("RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(), repositories=repos, as_of=GENERATED_AT, config=_FAST_CONFIG))
    snapshot = build_analysis_snapshot(report)

    next_day = GENERATED_AT + timedelta(days=1)
    assert CheckpointLabel.EOD in due_checkpoints(GENERATED_AT, set(), now=next_day)

    checkpoint = asyncio.run(
        capture_outcome_checkpoint(
            CheckpointLabel.EOD, snapshot, now=next_day, provider=provider, instrument_master=_MASTER,
            strategy=EMAVWAPAlignmentStrategy(), repositories=repos, config=_FAST_CONFIG,
        )
    )
    assert checkpoint.status == CheckpointStatus.MARKET_CLOSED
    assert checkpoint.underlying is None
