"""Real multi-instrument live market-intelligence session — the Phase-2
integration driver. Wires the already-existing, already-tested pieces:

    UpstoxLiveFeedClient.stream()          (unchanged, real WebSocket)
        -> RawTick
        -> LiveMarketIntelligenceSession.on_tick()   (new orchestration,
                                                        REST-seeded startup +
                                                        merged candle state)
        -> InstrumentSnapshot
        -> analysis_change.detect_changes()          (unchanged)
        -> printed only when something meaningful changed

REST is used at startup (seed history/status) and as a fallback whenever an
instrument's WebSocket feed goes stale (`LiveMarketIntelligenceSession.
is_stale()` / `reconcile_via_rest()`) — never silently instead of a fresher
WebSocket tick.

Never logs, prints, or persists the access token or the authorized
WebSocket URL.

Run: `python -m scripts.live_market_intelligence_session [duration_seconds]`
(default 300s / 5 minutes, matching the market-hours validation target).
"""

from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.config.settings import settings
from app.data.providers.upstox_instrument_master import fetch_instrument_master
from app.data.providers.upstox_provider import UpstoxProvider
from app.data.providers.upstox_websocket_client import UpstoxLiveFeedClient
from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy
from app.orchestration.analysis_change import ChangeEvent
from app.orchestration.live_intelligence_session import LiveMarketIntelligenceSession
from app.orchestration.watchlist_snapshot import InstrumentSnapshot
from app.utils.time import utc_now

DEFAULT_WATCHLIST = ["NIFTY50", "BANKNIFTY", "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN"]
DEFAULT_DURATION_SECONDS = 300.0
STALE_CHECK_INTERVAL_SECONDS = 10.0
_MASTER_CACHE_PATH = Path("data/reference/upstox_nse_instruments.json")


@dataclass
class SessionStats:
    initial_feed_events: int = 0
    live_ticks: int = 0
    malformed_events: int = 0
    disconnects: int = 0
    reconnects: int = 0
    rest_fallbacks: int = 0
    instruments_with_ticks: set[str] = field(default_factory=set)
    tick_to_snapshot_latencies_ms: list[float] = field(default_factory=list)
    ws_connect_latency_ms: float | None = None
    first_tick_latency_ms: float | None = None


def _print_instrument_update(snapshot: InstrumentSnapshot, changes: list[ChangeEvent]) -> None:
    a = snapshot.analysis
    lines = [f"{snapshot.requested_symbol}"]
    if snapshot.quote is not None:
        lines.append(f"  LTP: {snapshot.quote.last_price}")
    lines.append(f"  DATA: {snapshot.state.value}")
    if a is not None:
        lines.append(f"  Last completed M15: {a.last_completed_candle_timestamp}")
        lines.append(
            f"  EMA9/21/50: {a.ema9}/{a.ema21}/{a.ema50}  "
            f"alignment: {a.ema_alignment.value if a.ema_alignment else a.ema_status.value}"
        )
        lines.append(f"  VWAP: {a.vwap_value}  price vs VWAP: {a.price_vs_vwap.value if a.price_vs_vwap else '?'}")
        lines.append(f"  SETUP: {a.setup.direction if a.setup else 'NO SETUP'}")
    else:
        lines.append("  EMA/VWAP/SETUP: UNAVAILABLE (insufficient history)")
    if snapshot.data_age_seconds is not None:
        lines.append(f"  DATA AGE: {snapshot.data_age_seconds:.2f}s")
    if changes:
        lines.append(f"  CHANGE: {'; '.join(c.kind.value for c in changes)}")
    else:
        lines.append("  CHANGE: initial snapshot")
    print("\n".join(lines))
    print()


async def run(symbols: list[str], *, duration_seconds: float) -> None:
    if not settings.upstox_access_token:
        print("WAITING: UPSTOX_ACCESS_TOKEN is not set. See docs/data-sources/PROVIDER_DECISION.md.")
        return

    stats = SessionStats()
    strategy = EMAVWAPAlignmentStrategy()

    async with httpx.AsyncClient() as client:
        master = await fetch_instrument_master(client, cache_path=_MASTER_CACHE_PATH)
        provider = UpstoxProvider(client=client, access_token=settings.upstox_access_token, base_url=settings.upstox_base_url)

        as_of = utc_now()
        session = await LiveMarketIntelligenceSession.start(
            provider=provider, instrument_master=master, symbols=symbols, strategy=strategy, as_of=as_of
        )
        refs = session.instruments()
        instrument_keys = [r.instrument_key for r in refs]

        print(f"Startup: resolved {len(refs)}/{len(symbols)} symbols; market status: {session.exchange_status.value}")
        print(f"Watchlist: {', '.join(r.requested_symbol for r in refs)}")
        print()

        feed_client = UpstoxLiveFeedClient(client=client, access_token=settings.upstox_access_token, base_url=settings.upstox_base_url)

        connect_started = time.perf_counter()
        first_event_seen = False
        first_tick_seen = False
        deadline = time.monotonic() + duration_seconds
        last_stale_check = time.monotonic()

        agen = feed_client.stream(instrument_keys, mode="ltpc")
        try:
            async for event in agen:
                now_monotonic = time.monotonic()
                if not first_event_seen:
                    stats.ws_connect_latency_ms = (time.perf_counter() - connect_started) * 1000
                    first_event_seen = True

                if event.kind == "connected":
                    print("WebSocket: connected and subscribed to the full watchlist.")
                elif event.kind == "disconnected":
                    stats.disconnects += 1
                    print(f"WebSocket: disconnected ({event.detail})")
                elif event.kind == "reconnecting":
                    stats.reconnects += 1
                    print(f"WebSocket: reconnecting (attempt {event.attempt})...")
                elif event.kind == "stopped":
                    print(f"WebSocket: stopped ({event.detail})")
                    break
                elif event.kind == "malformed":
                    stats.malformed_events += 1
                elif event.kind == "tick" and event.tick is not None:
                    tick_started = time.perf_counter()
                    if not first_tick_seen:
                        stats.first_tick_latency_ms = (time.perf_counter() - connect_started) * 1000
                        first_tick_seen = True

                    snapshot = session.on_tick(event.tick)
                    stats.tick_to_snapshot_latencies_ms.append((time.perf_counter() - tick_started) * 1000)
                    stats.live_ticks += 1
                    stats.instruments_with_ticks.add(event.tick.instrument_key)

                    if snapshot is not None:
                        _print_instrument_update(snapshot, session.changes_for(event.tick.instrument_key))

                # Periodic REST-fallback staleness sweep.
                if now_monotonic - last_stale_check >= STALE_CHECK_INTERVAL_SECONDS:
                    last_stale_check = now_monotonic
                    poll_as_of = utc_now()
                    for key in instrument_keys:
                        if session.is_stale(key, as_of=poll_as_of):
                            stats.rest_fallbacks += 1
                            fallback_snapshot = await session.reconcile_via_rest(key, provider=provider, as_of=poll_as_of)
                            if fallback_snapshot is not None:
                                changes = session.changes_for(key)
                                if changes:
                                    _print_instrument_update(fallback_snapshot, changes)

                if time.monotonic() >= deadline:
                    print("Duration elapsed; ending session.")
                    break
        finally:
            await agen.aclose()

    print()
    print("=" * 60)
    print("SESSION SUMMARY")
    print("=" * 60)
    print(f"WS connect latency: {stats.ws_connect_latency_ms:.1f} ms" if stats.ws_connect_latency_ms else "WS connect latency: n/a")
    print(f"First tick latency: {stats.first_tick_latency_ms:.1f} ms" if stats.first_tick_latency_ms else "First tick latency: n/a (no live_feed tick observed)")
    print(f"Live ticks received: {stats.live_ticks}")
    print(f"Instruments with at least one tick: {len(stats.instruments_with_ticks)}/{len(instrument_keys)}")
    if stats.tick_to_snapshot_latencies_ms:
        s = sorted(stats.tick_to_snapshot_latencies_ms)
        print(f"Tick -> snapshot application latency: min={s[0]:.3f}ms avg={sum(s)/len(s):.3f}ms max={s[-1]:.3f}ms")
    print(f"Disconnects: {stats.disconnects}  Reconnects: {stats.reconnects}  Malformed: {stats.malformed_events}")
    print(f"REST fallback invocations: {stats.rest_fallbacks}")
    print("=" * 60)
    print("READ-ONLY. NO ORDER/EXECUTION ENDPOINT WAS EVER CALLED.")


def main() -> None:
    duration = float(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DURATION_SECONDS
    asyncio.run(run(DEFAULT_WATCHLIST, duration_seconds=duration))


if __name__ == "__main__":
    main()
