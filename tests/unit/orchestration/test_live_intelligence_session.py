from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx

from app.data.providers.base import RawTick
from app.data.providers.upstox_provider import UpstoxProvider
from app.domain.market.data_state import MarketDataState
from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy
from app.orchestration.live_intelligence_session import LiveMarketIntelligenceSession

RELIANCE_KEY = "NSE_EQ|INE002A01018"
AS_OF = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)

_MASTER: list[dict[str, object]] = [
    {
        "segment": "NSE_EQ",
        "name": "RELIANCE INDUSTRIES LTD",
        "exchange": "NSE",
        "instrument_type": "EQ",
        "instrument_key": RELIANCE_KEY,
        "trading_symbol": "RELIANCE",
    }
]


def _candle_rows(n: int, *, end: datetime) -> list[list[object]]:
    rows = []
    for i in range(n):
        ts = end - timedelta(minutes=15 * (n - 1 - i))
        price = 1280.0 + i * 0.1
        rows.append([ts.isoformat(), price, price + 1, price - 1, price, 1000, 0])
    return rows


def _router(
    *, status: str = "NORMAL_OPEN", candle_count: int = 60, fail_history: bool = False, fail_quote: bool = False
) -> object:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v2/market/status/NSE":
            return httpx.Response(200, json={"status": "success", "data": {"status": status}})
        if path == "/v2/market-quote/quotes":
            if fail_quote:
                return httpx.Response(500, text="down")
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": {
                        "NSE_EQ:RELIANCE": {
                            "instrument_token": RELIANCE_KEY,
                            "last_price": 1287.0,
                            "net_change": 4.8,
                            "last_trade_time": str(int(AS_OF.timestamp() * 1000)),
                        }
                    },
                },
            )
        if path.startswith("/v3/historical-candle/"):
            if fail_history:
                return httpx.Response(500, text="down")
            return httpx.Response(200, json={"status": "success", "data": {"candles": _candle_rows(candle_count, end=AS_OF)}})
        raise AssertionError(f"unexpected path {path}")

    return handler


def _provider(handler: object) -> UpstoxProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
    return UpstoxProvider(client=client, access_token="tok")


def _start(handler: object, *, symbols: list[str] | None = None) -> LiveMarketIntelligenceSession:
    provider = _provider(handler)
    strategy = EMAVWAPAlignmentStrategy()
    return asyncio.run(
        LiveMarketIntelligenceSession.start(
            provider=provider, instrument_master=_MASTER, symbols=symbols or ["RELIANCE"], strategy=strategy, as_of=AS_OF
        )
    )


def _tick(
    *, price: float, timestamp: datetime, quantity: int = 100, key: str = RELIANCE_KEY, is_initial_snapshot: bool = False
) -> RawTick:
    return RawTick(
        instrument_key=key, price=price, quantity=quantity, timestamp=timestamp, is_initial_snapshot=is_initial_snapshot
    )


# -- startup ---------------------------------------------------------------


def test_start_seeds_real_history_and_resolves_instrument() -> None:
    session = _start(_router(candle_count=60))

    refs = session.instruments()
    assert len(refs) == 1 and refs[0].instrument_key == RELIANCE_KEY
    assert len(session.merged_completed_candles(RELIANCE_KEY)) == 60


def test_start_skips_unresolvable_symbols_without_crashing() -> None:
    session = _start(_router(), symbols=["RELIANCE", "NOT_A_REAL_SYMBOL"])
    assert len(session.instruments()) == 1


def test_start_history_failure_seeds_empty_not_fabricated() -> None:
    session = _start(_router(fail_history=True))
    assert session.merged_completed_candles(RELIANCE_KEY) == []


# -- on_tick -----------------------------------------------------------


def test_tick_for_unknown_instrument_is_ignored_not_an_error() -> None:
    session = _start(_router())
    result = session.on_tick(_tick(price=100.0, timestamp=AS_OF, key="SOME_OTHER_KEY"))
    assert result is None


def test_tick_for_known_instrument_produces_a_live_snapshot() -> None:
    session = _start(_router(status="NORMAL_OPEN", candle_count=60))
    tick_time = AS_OF + timedelta(minutes=1)

    snapshot = session.on_tick(_tick(price=1290.0, timestamp=tick_time))

    assert snapshot is not None
    assert snapshot.quote is not None and snapshot.quote.last_price == Decimal("1290.0")
    assert snapshot.quote.freshness.data_timestamp == tick_time
    assert snapshot.analysis is not None  # 60 seeded candles already satisfy the minimum


def test_initial_snapshot_tick_is_not_classified_as_live_streaming() -> None:
    """An `initial_feed`-sourced tick is real data but not continuous
    streaming -- with the market closed (this router's default), it must
    classify as MARKET_CLOSED_LATEST_DATA, never LIVE_STREAMING.
    """
    session = _start(_router(status="CLOSING_END", candle_count=60))
    snapshot = session.on_tick(_tick(price=1290.0, timestamp=AS_OF + timedelta(minutes=1), is_initial_snapshot=True))
    assert snapshot is not None
    assert snapshot.state == MarketDataState.MARKET_CLOSED_LATEST_DATA


def test_live_feed_tick_during_open_market_classifies_as_live_streaming() -> None:
    session = _start(_router(status="NORMAL_OPEN", candle_count=60))
    tick_time = AS_OF + timedelta(minutes=1)
    snapshot = session.on_tick(_tick(price=1290.0, timestamp=tick_time, is_initial_snapshot=False))
    assert snapshot is not None
    assert snapshot.state == MarketDataState.LIVE_STREAMING


def test_insufficient_seeded_history_reports_insufficient_history_state() -> None:
    session = _start(_router(candle_count=10))
    snapshot = session.on_tick(_tick(price=1290.0, timestamp=AS_OF + timedelta(minutes=1)))
    assert snapshot is not None
    assert snapshot.state == MarketDataState.INSUFFICIENT_HISTORY
    assert snapshot.analysis is None


# -- merged_completed_candles (boundary correctness) ------------------------


def test_merged_candles_deduplicate_at_the_seeded_live_boundary() -> None:
    session = _start(_router(candle_count=60))
    seeded_last_ts = session.merged_completed_candles(RELIANCE_KEY)[-1].freshness.data_timestamp

    # Feed a tick inside the seeded history's own last bucket -- must not
    # create a second, duplicate candle for a period already seeded.
    session.on_tick(_tick(price=1290.0, timestamp=seeded_last_ts + timedelta(minutes=5)))
    # Roll into the NEXT bucket to force that in-seeded-range partial to finalize.
    session.on_tick(_tick(price=1291.0, timestamp=seeded_last_ts + timedelta(minutes=16)))

    merged = session.merged_completed_candles(RELIANCE_KEY)
    timestamps = [c.freshness.data_timestamp for c in merged]
    assert len(timestamps) == len(set(timestamps))  # no duplicate bucket


def test_merged_candles_append_genuinely_new_live_completed_candles() -> None:
    session = _start(_router(candle_count=60))
    before = len(session.merged_completed_candles(RELIANCE_KEY))
    seeded_last_ts = session.merged_completed_candles(RELIANCE_KEY)[-1].freshness.data_timestamp

    session.on_tick(_tick(price=1290.0, timestamp=seeded_last_ts + timedelta(minutes=20)))
    session.on_tick(_tick(price=1291.0, timestamp=seeded_last_ts + timedelta(minutes=36)))  # rolls the new bucket over

    after = len(session.merged_completed_candles(RELIANCE_KEY))
    assert after == before + 1


# -- staleness ---------------------------------------------------------------


def test_is_stale_true_before_any_tick() -> None:
    session = _start(_router())
    assert session.is_stale(RELIANCE_KEY, as_of=AS_OF) is True


def test_is_stale_false_shortly_after_a_tick() -> None:
    session = _start(_router(), )
    tick_time = AS_OF
    session.on_tick(_tick(price=1290.0, timestamp=tick_time))
    assert session.is_stale(RELIANCE_KEY, as_of=tick_time + timedelta(seconds=5)) is False


def test_is_stale_true_after_threshold_elapses() -> None:
    session = _start(_router())
    tick_time = AS_OF
    session.on_tick(_tick(price=1290.0, timestamp=tick_time))
    assert session.is_stale(RELIANCE_KEY, as_of=tick_time + timedelta(seconds=30)) is True


# -- REST reconciliation ------------------------------------------------


def test_reconcile_via_rest_updates_state_when_no_ws_tick_yet() -> None:
    handler = _router(candle_count=60)
    session = _start(handler)
    provider = _provider(handler)

    snapshot = asyncio.run(session.reconcile_via_rest(RELIANCE_KEY, provider=provider, as_of=AS_OF + timedelta(minutes=1)))

    assert snapshot is not None
    assert snapshot.quote is not None and snapshot.quote.last_price == Decimal("1287.0")


def test_reconcile_via_rest_never_overwrites_a_newer_ws_tick() -> None:
    handler = _router(candle_count=60)
    session = _start(handler)
    provider = _provider(handler)

    newer_tick_time = AS_OF + timedelta(hours=1)  # newer than the REST quote's last_trade_time (== AS_OF)
    session.on_tick(_tick(price=9999.0, timestamp=newer_tick_time))

    result = asyncio.run(session.reconcile_via_rest(RELIANCE_KEY, provider=provider, as_of=newer_tick_time + timedelta(seconds=1)))

    assert result is not None
    assert result.quote is not None and result.quote.last_price == Decimal("9999.0")  # unchanged by the older REST quote


def test_reconcile_via_rest_provider_failure_returns_none_not_a_crash() -> None:
    handler = _router(fail_quote=True)
    session = _start(handler)
    provider = _provider(handler)

    result = asyncio.run(session.reconcile_via_rest(RELIANCE_KEY, provider=provider, as_of=AS_OF))
    assert result is None


def test_reconcile_via_rest_unknown_instrument_returns_none() -> None:
    handler = _router()
    session = _start(handler)
    provider = _provider(handler)

    result = asyncio.run(session.reconcile_via_rest("SOME_OTHER_KEY", provider=provider, as_of=AS_OF))
    assert result is None


# -- change detection integration --------------------------------------


def test_changes_for_reflects_transition_from_insufficient_to_live() -> None:
    session = _start(_router(candle_count=10))  # insufficient
    assert session.changes_for(RELIANCE_KEY) == []  # nothing yet (no snapshot at all)

    session.on_tick(_tick(price=1290.0, timestamp=AS_OF + timedelta(minutes=1)))
    changes_after_first = session.changes_for(RELIANCE_KEY)
    assert changes_after_first == []  # first snapshot ever -> nothing to compare against


# -- determinism / no-look-ahead ------------------------------------------


def test_deterministic_given_the_same_tick_sequence() -> None:
    def run_sequence() -> Decimal | None:
        session = _start(_router(candle_count=60))
        session.on_tick(_tick(price=1290.0, timestamp=AS_OF + timedelta(minutes=1)))
        snapshot = session.on_tick(_tick(price=1295.0, timestamp=AS_OF + timedelta(minutes=2)))
        assert snapshot is not None and snapshot.analysis is not None
        return snapshot.analysis.ema9

    assert run_sequence() == run_sequence()


def test_future_tick_does_not_affect_an_earlier_snapshots_analysis() -> None:
    session = _start(_router(candle_count=60))
    early = session.on_tick(_tick(price=1290.0, timestamp=AS_OF + timedelta(minutes=1)))
    assert early is not None and early.analysis is not None
    early_ema9 = early.analysis.ema9

    # A later tick must not retroactively change what the earlier snapshot
    # already reported (the earlier InstrumentSnapshot object is immutable).
    session.on_tick(_tick(price=50000.0, timestamp=AS_OF + timedelta(minutes=20)))

    assert early.analysis.ema9 == early_ema9
