"""Multi-instrument real-time market intelligence demo — real Upstox data
only. Polls the configurable watchlist twice (real REST calls, a real delay
between them) to demonstrate the "what changed" engine against real,
independently-fetched data — not a synthetic diff.

Never logs, prints, or persists the access token.

Run: `python -m scripts.market_intelligence_demo [--poll-delay SECONDS] [SYMBOL ...]`
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import httpx

from app.config.settings import settings
from app.data.providers.upstox_instrument_master import fetch_instrument_master
from app.data.providers.upstox_provider import UpstoxProvider
from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy
from app.orchestration.analysis_change import ChangeEvent, detect_changes
from app.orchestration.watchlist_snapshot import InstrumentSnapshot, build_watchlist_snapshot
from app.utils.time import to_ist, utc_now

DEFAULT_WATCHLIST = ["NIFTY50", "BANKNIFTY", "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN"]
_MASTER_CACHE_PATH = Path("data/reference/upstox_nse_instruments.json")
DEFAULT_POLL_DELAY_SECONDS = 15.0


def _instrument_block(snapshot: InstrumentSnapshot, changes: list[ChangeEvent]) -> str:
    lines = [snapshot.requested_symbol]
    if snapshot.instrument is None or snapshot.error is not None:
        lines.append(f"  state: {snapshot.state.value}")
        lines.append(f"  UNAVAILABLE - {snapshot.error}")
        return "\n".join(lines)

    lines.append(f"  state: {snapshot.state.value}")
    if changes:
        lines.append(f"  change: {'; '.join(f'{c.kind.value} ({c.detail})' for c in changes)}")
    else:
        lines.append("  change: none since previous poll")

    if snapshot.quote is not None:
        lines.append(f"  LTP: {snapshot.quote.last_price}  (data age: {(snapshot.data_age_seconds or 0) / 3600:.2f}h)")

    a = snapshot.analysis
    if a is None:
        lines.append("  technical state: UNAVAILABLE (insufficient history)")
    else:
        lines.append(
            f"  EMA9/21/50: {a.ema9}/{a.ema21}/{a.ema50}  alignment: "
            f"{a.ema_alignment.value if a.ema_alignment else a.ema_status.value}"
        )
        if a.vwap_value is not None:
            lines.append(f"  VWAP: {a.vwap_value}  price vs VWAP: {a.price_vs_vwap.value if a.price_vs_vwap else '?'}")
        else:
            lines.append(f"  VWAP: UNAVAILABLE ({a.vwap_status.value} -- e.g. zero-volume index)")
        lines.append(f"  setup: {a.setup.direction if a.setup else 'NO SETUP'}")

    return "\n".join(lines)


def _attention_section(results: list[InstrumentSnapshot], changes_by_symbol: dict[str, list[ChangeEvent]]) -> str:
    entries: list[str] = []
    for r in results:
        reasons = []
        if r.analysis is not None and r.analysis.setup is not None:
            reasons.append(f"active {r.analysis.setup.direction} setup")
        changes = changes_by_symbol.get(r.requested_symbol, [])
        if changes:
            reasons.append(f"{len(changes)} change(s) since previous poll")
        if reasons:
            evidence = r.analysis.setup.structural_basis if (r.analysis and r.analysis.setup) else "n/a"
            entries.append(
                f"{len(entries) + 1}. {r.requested_symbol}\n"
                f"   Reason: {', '.join(reasons)}\n"
                f"   Evidence: {evidence}\n"
                f"   Data state: {r.state.value}"
            )

    if not entries:
        return "NO ACTIONABLE SETUP DETECTED"
    return "ATTENTION REQUIRED\n\n" + "\n\n".join(entries)


async def run(symbols: list[str], *, poll_delay_seconds: float) -> None:
    if not settings.upstox_access_token:
        print("WAITING: UPSTOX_ACCESS_TOKEN is not set. See docs/data-sources/PROVIDER_DECISION.md.")
        return

    async with httpx.AsyncClient() as client:
        master = await fetch_instrument_master(client, cache_path=_MASTER_CACHE_PATH)
        provider = UpstoxProvider(client=client, access_token=settings.upstox_access_token, base_url=settings.upstox_base_url)
        strategy = EMAVWAPAlignmentStrategy()

        first_started = time.perf_counter()
        as_of_1 = utc_now()
        first_results = await build_watchlist_snapshot(
            provider=provider, instrument_master=master, symbols=symbols, strategy=strategy, as_of=as_of_1
        )
        first_latency_s = time.perf_counter() - first_started

        print(f"(first poll: {len(symbols)} instruments in {first_latency_s:.2f}s real REST time; "
              f"waiting {poll_delay_seconds:.0f}s for the second poll...)")
        await asyncio.sleep(poll_delay_seconds)

        second_started = time.perf_counter()
        as_of_2 = utc_now()
        second_results = await build_watchlist_snapshot(
            provider=provider, instrument_master=master, symbols=symbols, strategy=strategy, as_of=as_of_2
        )
        second_latency_s = time.perf_counter() - second_started

    previous_by_symbol = {r.requested_symbol: r for r in first_results}
    changes_by_symbol = {
        r.requested_symbol: detect_changes(previous=previous_by_symbol.get(r.requested_symbol), current=r)
        for r in second_results
    }

    print()
    print("=" * 60)
    print("MARKET SNAPSHOT")
    print("=" * 60)
    print(f"Timestamp: {to_ist(as_of_2).strftime('%Y-%m-%d %H:%M:%S')} IST")
    statuses = {r.exchange_status.value for r in second_results if r.exchange_status is not None}
    print(f"Market status: {', '.join(statuses) if statuses else 'UNKNOWN'}")
    print(f"Second-poll REST time: {second_latency_s:.2f}s for {len(symbols)} instruments (real, measured)")
    print()

    for r in second_results:
        print(_instrument_block(r, changes_by_symbol.get(r.requested_symbol, [])))
        print()

    print("=" * 60)
    print(_attention_section(second_results, changes_by_symbol))
    print("=" * 60)
    print()
    print("READ-ONLY. NO ORDER/EXECUTION ENDPOINT WAS EVER CALLED.")


def main() -> None:
    args = sys.argv[1:]
    poll_delay = DEFAULT_POLL_DELAY_SECONDS
    if args and args[0] == "--poll-delay":
        poll_delay = float(args[1])
        args = args[2:]
    symbols = args or DEFAULT_WATCHLIST
    asyncio.run(run(symbols, poll_delay_seconds=poll_delay))


if __name__ == "__main__":
    main()
