"""The Options Intelligence Agent CLI — the actual product. Given one or
more symbols, runs the complete real pipeline
(`app.orchestration.options_intelligence_pipeline.analyze_symbol`) against
live Upstox data and prints the full report for each.

Real data only. No mocked responses. No fabricated fields. Persists real
option-chain/quote/IV snapshots to `data/persistence/` on every run, so IV
rank and change-in-OI evidence genuinely accumulate across separate runs of
this script over time — the first run of a new day's expiry will honestly
show `INSUFFICIENT_DATA` for those, not a fabricated value.

Never calls, and this codebase contains no code path capable of calling,
any order placement/modification/cancellation endpoint.

Prints the compact 9-section report by default; pass `--detailed` first to
print the full audit-grade report instead (same underlying data either way
— `render_compact()` vs `render_text()` on the same `OptionsIntelligenceReport`).

Run: `python -m scripts.options_intelligence_report [--detailed] [SYMBOL ...]`
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
from app.orchestration.options_intelligence_pipeline import Repositories, analyze_symbol
from app.persistence.jsonl_file import (
    JsonlIvObservationRepository,
    JsonlOptionChainRepository,
    JsonlQuoteRepository,
)
from app.utils.time import utc_now

DEFAULT_SYMBOLS = ["NIFTY50", "BANKNIFTY", "RELIANCE", "TCS", "SBIN", "INFY"]
_MASTER_CACHE_PATH = Path("data/reference/upstox_nse_instruments.json")
_MCX_MASTER_CACHE_PATH = Path("data/reference/upstox_mcx_instruments.json")
_MCX_MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/MCX.json.gz"
_PERSISTENCE_DIR = Path("data/persistence")


async def run(symbols: list[str], *, detailed: bool) -> None:
    if not settings.upstox_access_token:
        print("WAITING: UPSTOX_ACCESS_TOKEN is not set. See docs/data-sources/PROVIDER_DECISION.md.")
        return

    async with httpx.AsyncClient() as client:
        master = await fetch_instrument_master(client, cache_path=_MASTER_CACHE_PATH)
        # MCX Crude Oil (real, confirmed-available global-context input) --
        # a failed MCX fetch must never block the whole run: fall back to
        # omitting crude oil (honestly reported as unavailable downstream),
        # never a guessed proxy.
        try:
            mcx_master = await fetch_instrument_master(client, cache_path=_MCX_MASTER_CACHE_PATH, master_url=_MCX_MASTER_URL)
        except Exception as exc:  # noqa: BLE001 -- genuinely any failure here must degrade gracefully, not abort the run
            print(f"(MCX instrument master unavailable this run, crude oil context omitted: {exc})")
            mcx_master = None
        provider = UpstoxProvider(client=client, access_token=settings.upstox_access_token, base_url=settings.upstox_base_url)
        strategy = EMAVWAPAlignmentStrategy()
        repositories = Repositories(
            quotes=JsonlQuoteRepository(_PERSISTENCE_DIR / "quotes.jsonl"),
            option_chains=JsonlOptionChainRepository(_PERSISTENCE_DIR / "option_chains.jsonl"),
            iv_observations=JsonlIvObservationRepository(_PERSISTENCE_DIR / "iv_observations.jsonl"),
        )

        for symbol in symbols:
            started = time.perf_counter()
            report = await analyze_symbol(
                symbol, provider=provider, instrument_master=master, strategy=strategy,
                repositories=repositories, as_of=utc_now(), mcx_instrument_master=mcx_master,
            )
            wall_clock = time.perf_counter() - started
            print(report.render_text() if detailed else report.render_compact())
            print(f"(wall-clock for this symbol, including this script's own overhead: {wall_clock:.2f}s)")
            print()


def main() -> None:
    args = sys.argv[1:]
    detailed = False
    if args and args[0] == "--detailed":
        detailed = True
        args = args[1:]
    symbols = args or DEFAULT_SYMBOLS
    asyncio.run(run(symbols, detailed=detailed))


if __name__ == "__main__":
    main()
