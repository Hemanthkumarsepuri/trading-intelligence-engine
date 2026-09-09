"""End-to-end tests for Milestone G's audit journal orchestration layer:
`build_analysis_snapshot()` (Phase 1, from a real `analyze_symbol()` run),
`capture_outcome_checkpoint()` (Phases 2/3, a real second `analyze_symbol()`
re-run at a later `as_of`), and the scheduling helpers `due_checkpoints()`/
`eod_deadline()`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx

from app.data.providers.upstox_provider import UpstoxProvider
from app.domain.audit.models import CheckpointLabel, CheckpointStatus
from app.domain.audit.reconciliation import build_reconciliation
from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy
from app.orchestration.audit_journal import (
    build_analysis_snapshot,
    capture_outcome_checkpoint,
    due_checkpoints,
    eod_deadline,
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
_EXPIRY_MS = 1790274599000  # 2026-09-24 18:29:59 UTC
GENERATED_AT = datetime(2026, 8, 28, 6, 0, tzinfo=UTC)  # 11:30 IST -- well within NSE market hours

_MASTER: list[dict[str, object]] = [
    {"segment": "NSE_EQ", "name": "RELIANCE INDUSTRIES LTD", "exchange": "NSE", "instrument_type": "EQ", "instrument_key": RELIANCE_KEY, "trading_symbol": "RELIANCE"},
    {"segment": "NSE_FO", "underlying_symbol": "RELIANCE", "instrument_type": "CE", "expiry": _EXPIRY_MS, "weekly": False, "lot_size": 500, "instrument_key": CE_KEY, "strike_price": 1300.0},
    {"segment": "NSE_FO", "underlying_symbol": "RELIANCE", "instrument_type": "PE", "expiry": _EXPIRY_MS, "weekly": False, "lot_size": 500, "instrument_key": PE_KEY, "strike_price": 1300.0},
    {"segment": "NSE_FO", "underlying_symbol": "RELIANCE", "instrument_type": "FUT", "expiry": _EXPIRY_MS, "weekly": False, "lot_size": 500, "instrument_key": FUT_KEY},
]


def _candle_rows(n: int, *, end: datetime, price: float = 1280.0) -> list[list[object]]:
    rows = []
    for i in range(n):
        ts = end - timedelta(minutes=15 * (n - 1 - i))
        p = price + i * 0.5  # gently rising -> ASCENDING EMA alignment, price above VWAP
        rows.append([ts.isoformat(), p, p + 1, p - 1, p, 1000, 0])
    return rows


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


def _router(
    *, status: str = "NORMAL_OPEN", checkpoint_spot: float = 1310.0, checkpoint_ce_ltp: float = 32.0,
) -> Callable[[httpx.Request], httpx.Response]:
    """Call-count-aware: the FIRST underlying-quote/chain fetch answers
    with the "generation-time" values; every subsequent one answers with
    the "checkpoint-time" values -- simulates real price movement between
    the original analysis and a later checkpoint capture without needing
    the mock to inspect `as_of` (which never appears on the wire; Upstox's
    real REST API has no such parameter either).
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
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return UpstoxProvider(client=client, access_token="tok")


def _repos(tmp_path: Path) -> Repositories:
    return Repositories(
        quotes=JsonlQuoteRepository(tmp_path / "quotes.jsonl"), option_chains=JsonlOptionChainRepository(tmp_path / "chains.jsonl"),
        iv_observations=JsonlIvObservationRepository(tmp_path / "iv.jsonl"),
    )


_FAST_CONFIG = PipelineConfig(chain_fetch_attempts=1, chain_fetch_backoff_seconds=0.01)


# -- build_analysis_snapshot ----------------------------------------------


def test_build_analysis_snapshot_from_a_real_report(tmp_path: Path) -> None:
    provider = _provider(_router())
    report = asyncio.run(
        analyze_symbol("RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(), repositories=_repos(tmp_path), as_of=GENERATED_AT, config=_FAST_CONFIG)
    )
    assert report.error is None
    snapshot = build_analysis_snapshot(report)
    assert snapshot.identity.symbol == "RELIANCE"
    assert snapshot.identity.generated_at == GENERATED_AT
    assert snapshot.underlying.spot == report.spot
    assert snapshot.decision.decision == report.decision.decision.value  # type: ignore[union-attr]
    assert snapshot.evidence.rows  # matrix rows carried over
    # Sprint 4: real news is now a legitimate, first-party source (Upstox
    # /v2/news) -- a successful fetch with zero items is an honest empty
    # result, not the old "no source exists" stub.
    assert snapshot.news.provider == "upstox"
    assert snapshot.news.fetch_error is None
    assert snapshot.news.unavailable_reason is None


def test_build_analysis_snapshot_raises_on_a_failed_report(tmp_path: Path) -> None:
    provider = _provider(_router())
    report = asyncio.run(
        analyze_symbol("NOT_A_REAL_SYMBOL", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(), repositories=_repos(tmp_path), as_of=GENERATED_AT, config=_FAST_CONFIG)
    )
    assert report.error is not None
    try:
        build_analysis_snapshot(report)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_build_analysis_snapshot_candidate_matches_top_report_candidate(tmp_path: Path) -> None:
    provider = _provider(_router())
    report = asyncio.run(
        analyze_symbol("RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(), repositories=_repos(tmp_path), as_of=GENERATED_AT, config=_FAST_CONFIG)
    )
    assert report.error is None
    snapshot = build_analysis_snapshot(report)
    if report.candidates:
        assert snapshot.decision.candidate_selected is not None
        assert snapshot.decision.candidate_selected.strike == report.candidates[0].strike


# -- scheduling helpers ----------------------------------------------------


def test_due_checkpoints_never_returns_a_future_checkpoint() -> None:
    due = due_checkpoints(GENERATED_AT, set(), now=GENERATED_AT + timedelta(minutes=1))
    assert due == []


def test_due_checkpoints_returns_five_min_once_elapsed() -> None:
    due = due_checkpoints(GENERATED_AT, set(), now=GENERATED_AT + timedelta(minutes=5))
    assert CheckpointLabel.FIVE_MIN in due
    assert CheckpointLabel.FIFTEEN_MIN not in due


def test_due_checkpoints_excludes_already_captured() -> None:
    due = due_checkpoints(GENERATED_AT, {CheckpointLabel.FIVE_MIN}, now=GENERATED_AT + timedelta(minutes=20))
    assert CheckpointLabel.FIVE_MIN not in due
    assert CheckpointLabel.FIFTEEN_MIN in due


def test_eod_deadline_is_same_calendar_day_market_close() -> None:
    from app.utils.time import to_ist

    deadline = eod_deadline(GENERATED_AT)
    assert to_ist(deadline).date() == to_ist(GENERATED_AT).date()
    assert to_ist(deadline).hour == 15 and to_ist(deadline).minute == 30
    assert deadline > GENERATED_AT


def test_eod_deadline_rolls_forward_from_a_saturday_generated_snapshot() -> None:
    """Part 4A -- a snapshot generated on a non-trading day must never
    get a same-day "EOD deadline" that can never correspond to a real
    market close (the exact edge case Sprint 7 documented as a known
    limitation)."""
    from app.utils.time import to_ist

    saturday_generated = datetime(2026, 8, 29, 6, 0, tzinfo=UTC)  # 11:30 IST Saturday
    deadline = eod_deadline(saturday_generated)
    ist_deadline = to_ist(deadline)
    assert ist_deadline.date() == date(2026, 8, 31)  # rolls forward to Monday
    assert ist_deadline.hour == 15 and ist_deadline.minute == 30


def test_eod_deadline_rolls_forward_from_a_sunday_generated_snapshot() -> None:
    from app.utils.time import to_ist

    sunday_generated = datetime(2026, 8, 30, 20, 0, tzinfo=UTC)  # 01:30 IST Monday-adjacent Sunday night
    deadline = eod_deadline(sunday_generated)
    ist_deadline = to_ist(deadline)
    assert ist_deadline.date() == date(2026, 8, 31)  # rolls forward to Monday
    assert ist_deadline.weekday() == 0  # Monday


def test_eod_deadline_weekday_generated_after_market_close_is_unaffected() -> None:
    """A weekday snapshot generated AFTER 15:30 IST still gets that SAME
    day's 15:30 deadline (already in the past relative to `generated_at`
    -- unchanged existing behavior, not rolled forward to the next day)."""
    from app.utils.time import to_ist

    after_close = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)  # 17:30 IST Friday, after close
    deadline = eod_deadline(after_close)
    assert to_ist(deadline).date() == date(2026, 8, 28)  # same Friday, not rolled to Monday
    assert deadline < after_close  # already in the past -- EOD immediately due


def test_eod_deadline_weekday_generated_before_market_open_is_unaffected() -> None:
    """A weekday snapshot generated before market open (pre-market) still
    gets that SAME day's 15:30 deadline, still in the future."""
    from app.utils.time import to_ist

    pre_market = datetime(2026, 8, 28, 2, 30, tzinfo=UTC)  # 08:00 IST Friday, before open
    deadline = eod_deadline(pre_market)
    assert to_ist(deadline).date() == date(2026, 8, 28)
    assert deadline > pre_market


# -- capture_outcome_checkpoint ---------------------------------------------


def test_capture_outcome_checkpoint_raises_when_not_due(tmp_path: Path) -> None:
    provider = _provider(_router())
    repos = _repos(tmp_path)
    report = asyncio.run(analyze_symbol("RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(), repositories=repos, as_of=GENERATED_AT, config=_FAST_CONFIG))
    snapshot = build_analysis_snapshot(report)
    try:
        asyncio.run(
            capture_outcome_checkpoint(
                CheckpointLabel.FIVE_MIN, snapshot, now=GENERATED_AT + timedelta(minutes=1), provider=provider,
                instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(), repositories=repos, config=_FAST_CONFIG,
            )
        )
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_capture_outcome_checkpoint_records_real_underlying_and_option_movement(tmp_path: Path) -> None:
    provider = _provider(_router(checkpoint_spot=1310.0, checkpoint_ce_ltp=35.0))
    repos = _repos(tmp_path)
    report = asyncio.run(analyze_symbol("RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(), repositories=repos, as_of=GENERATED_AT, config=_FAST_CONFIG))
    assert report.error is None
    snapshot = build_analysis_snapshot(report)

    checkpoint_time = GENERATED_AT + timedelta(minutes=5)
    checkpoint = asyncio.run(
        capture_outcome_checkpoint(
            CheckpointLabel.FIVE_MIN, snapshot, now=checkpoint_time, provider=provider, instrument_master=_MASTER,
            strategy=EMAVWAPAlignmentStrategy(), repositories=repos, config=_FAST_CONFIG,
        )
    )
    assert checkpoint.status == CheckpointStatus.RECORDED
    assert checkpoint.checkpoint_label == CheckpointLabel.FIVE_MIN
    assert checkpoint.actual_elapsed_seconds == 300.0
    assert checkpoint.underlying is not None
    assert checkpoint.underlying.spot == 1310.0
    assert checkpoint.underlying.pct_change is not None and checkpoint.underlying.pct_change > 0

    if snapshot.decision.candidate_selected is not None and snapshot.decision.candidate_selected.right.value == "CE":
        assert checkpoint.option is not None
        assert checkpoint.option.option_ltp == 35.0


def test_capture_outcome_checkpoint_market_closed_never_fabricates_an_outcome(tmp_path: Path) -> None:
    provider = _provider(_router(status="NORMAL_CLOSE"))
    repos = _repos(tmp_path)
    report = asyncio.run(analyze_symbol("RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(), repositories=repos, as_of=GENERATED_AT, config=_FAST_CONFIG))
    snapshot = build_analysis_snapshot(report)

    checkpoint = asyncio.run(
        capture_outcome_checkpoint(
            CheckpointLabel.FIVE_MIN, snapshot, now=GENERATED_AT + timedelta(minutes=5), provider=provider,
            instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(), repositories=repos, config=_FAST_CONFIG,
        )
    )
    assert checkpoint.status == CheckpointStatus.MARKET_CLOSED
    assert checkpoint.underlying is None
    assert checkpoint.option is None


def test_end_to_end_snapshot_checkpoint_and_reconciliation(tmp_path: Path) -> None:
    """The full loop: generate -> capture a checkpoint -> reconcile,
    proving the three layers (pipeline, orchestration, pure domain
    reconciliation) actually compose.
    """
    provider = _provider(_router(checkpoint_spot=1320.0, checkpoint_ce_ltp=40.0))
    repos = _repos(tmp_path)
    report = asyncio.run(analyze_symbol("RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(), repositories=repos, as_of=GENERATED_AT, config=_FAST_CONFIG))
    assert report.error is None
    snapshot = build_analysis_snapshot(report)

    checkpoint = asyncio.run(
        capture_outcome_checkpoint(
            CheckpointLabel.FIVE_MIN, snapshot, now=GENERATED_AT + timedelta(minutes=5), provider=provider,
            instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(), repositories=repos, config=_FAST_CONFIG,
        )
    )
    reconciliation = build_reconciliation(snapshot, [checkpoint], now=GENERATED_AT + timedelta(minutes=6), near_level_pct_threshold=_FAST_CONFIG.near_level_pct_threshold)
    assert reconciliation.audit_id == snapshot.identity.audit_id
    assert reconciliation.is_final is False
    assert len(reconciliation.excursion) == 1
