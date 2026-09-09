"""Real-time market intelligence snapshot — multi-instrument, real Upstox
data, usable whether NSE is open or closed. Ties together, all real (no
synthetic substitution):

    Upstox instrument master (real, cached)
        -> resolved instrument_key per requested symbol
        -> real market status (/v2/market/status/NSE)
        -> real batched quotes (/v2/market-quote/quotes)
        -> real M15 history (/v3/historical-candle/...)
        -> app.orchestration.watchlist_snapshot.build_watchlist_snapshot()
        -> unmodified EMAVWAPAlignmentStrategy

Never logs, prints, or persists the access token.

Run: `python -m scripts.watchlist_snapshot_demo [SYMBOL ...]`
(defaults to a fixed liquid basket if no symbols are given).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

from app.config.settings import settings
from app.data.providers.upstox_instrument_master import fetch_instrument_master
from app.data.providers.upstox_provider import UpstoxProvider
from app.domain.market.data_state import MarketDataState
from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy
from app.orchestration.watchlist_snapshot import InstrumentSnapshot, build_watchlist_snapshot
from app.utils.time import to_ist, utc_now

DEFAULT_WATCHLIST = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "NIFTY50", "INDIA VIX"]
_MASTER_CACHE_PATH = Path("data/reference/upstox_nse_instruments.json")


def _fmt(snapshot: InstrumentSnapshot) -> str:
    lines = [snapshot.requested_symbol]
    if snapshot.instrument is None:
        lines.append(f"  UNAVAILABLE - {snapshot.error}")
        return "\n".join(lines)

    lines.append(f"  Instrument key: {snapshot.instrument.instrument_key}")
    lines.append(f"  Exchange status: {snapshot.exchange_status.value if snapshot.exchange_status else 'UNKNOWN'}")
    lines.append(f"  State: {snapshot.state.value}")

    if snapshot.error is not None:
        lines.append(f"  UNAVAILABLE - {snapshot.error}")
        return "\n".join(lines)

    if snapshot.quote is not None:
        lines.append(f"  LTP: {snapshot.quote.last_price}")
        lines.append(f"  Previous close: {snapshot.previous_close}")
        if snapshot.day_change is not None and snapshot.day_change_pct is not None:
            lines.append(f"  Day change: {snapshot.day_change} ({snapshot.day_change_pct:.2f}%)")
        lines.append(f"  Exchange timestamp: {to_ist(snapshot.quote.freshness.data_timestamp).isoformat()}")
        if snapshot.data_age_seconds is not None:
            lines.append(f"  Data age: {snapshot.data_age_seconds / 3600:.2f} hours")

    if snapshot.state == MarketDataState.INSUFFICIENT_HISTORY:
        lines.append("  EMA/VWAP/SETUP: UNAVAILABLE - fewer than 50 M15 candles returned by the provider")
    elif snapshot.analysis is not None:
        a = snapshot.analysis
        lines.append(f"  Latest completed M15 candle: {a.last_completed_candle_timestamp}")
        lines.append(f"  Candles available: {a.candles_available}")
        lines.append(f"  EMA9: {a.ema9}")
        lines.append(f"  EMA21: {a.ema21}")
        lines.append(f"  EMA50: {a.ema50}")
        lines.append(f"  EMA alignment: {a.ema_alignment.value if a.ema_alignment else a.ema_status.value}")
        lines.append(f"  M15 VWAP: {a.vwap_value}")
        lines.append(f"  Price vs VWAP: {a.price_vs_vwap.value if a.price_vs_vwap else a.vwap_status.value}")
        setup_label = a.setup.direction if a.setup else "NO SETUP"
        reason = "" if a.setup else " (EMA alignment and price-vs-VWAP conditions were not both satisfied)"
        lines.append(f"  SETUP: {setup_label}{reason}")
        lines.append(f"  Strategy version: {a.strategy_version}")

    return "\n".join(lines)


async def run(symbols: list[str]) -> None:
    if not settings.upstox_access_token:
        print("WAITING: UPSTOX_ACCESS_TOKEN is not set. See docs/data-sources/PROVIDER_DECISION.md.")
        return

    as_of = utc_now()
    async with httpx.AsyncClient() as client:
        master = await fetch_instrument_master(client, cache_path=_MASTER_CACHE_PATH)
        provider = UpstoxProvider(client=client, access_token=settings.upstox_access_token, base_url=settings.upstox_base_url)
        strategy = EMAVWAPAlignmentStrategy()

        results = await build_watchlist_snapshot(
            provider=provider, instrument_master=master, symbols=symbols, strategy=strategy, as_of=as_of
        )

    print("=" * 60)
    print("REAL-TIME MARKET INTELLIGENCE")
    print("=" * 60)
    print()
    print(f"Analysis time: {to_ist(as_of).strftime('%Y-%m-%d %H:%M:%S')} IST")
    print("Provider: Upstox")
    print(f"Instruments requested: {len(symbols)}")
    print()
    for snapshot in results:
        print(_fmt(snapshot))
        print()
    print("=" * 60)
    print("READ-ONLY. NO ORDER/EXECUTION ENDPOINT WAS EVER CALLED.")
    print("=" * 60)


def main() -> None:
    symbols = sys.argv[1:] or DEFAULT_WATCHLIST
    asyncio.run(run(symbols))


if __name__ == "__main__":
    main()
