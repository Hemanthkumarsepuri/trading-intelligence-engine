"""Sprint 5, Phase 2 — the safe operational entrypoint for live market
operation. For each requested query, runs the real pipeline, persists an
immutable `AnalysisSnapshot` to the audit journal, then catches up on any
outcome checkpoints (T+5m/15m/30m/60m/EOD) that have genuinely come due
for THIS symbol's prior unresolved observations -- reusing
`app.orchestration.live_operation` end to end. No new analysis engine, no
new persistence format, no order execution capability of any kind.

Real data only -- no mocked responses, no fabricated fields, no fabricated
checkpoints. When the market is closed, this honestly reports
MARKET_CLOSED_LATEST_DATA / SKIPPED_NO_DECISION rather than inventing a
live observation (Phase 3).

Examples:
    python -m scripts.run_live_analysis KAYNES
    python -m scripts.run_live_analysis "KAYNES 4000 CE" "KAYNES 4000 PE"
    python -m scripts.run_live_analysis KAYNES JIOFIN "JIOFIN 145 CE" "JIOFIN 145 PE" RELIANCE "RELIANCE 1400 CE" "RELIANCE 1400 PE" "NIFTY 25000 CE" "NIFTY 25000 PE" BANKNIFTY
    python -m scripts.run_live_analysis --repeat 300 KAYNES   # re-run every 300s until Ctrl+C

Sprint 6, Phase 3 -- `--auto-atm` treats each query as a bare underlying
symbol and expands it into [SYMBOL, "SYMBOL <live ATM> CE", "SYMBOL <live
ATM> PE"] using the REAL strike resolved from that run's own option chain
(`app.orchestration.live_operation.run_controlled_observation_set`) --
never a hardcoded strike that may not exist in the current chain:
    python -m scripts.run_live_analysis --auto-atm KAYNES JIOFIN RELIANCE NIFTY BANKNIFTY

Per-symbol failures are isolated -- one query erroring never aborts the
rest of the batch (Phase 2/18).
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

from app.config.settings import settings
from app.data.providers.upstox_instrument_master import fetch_instrument_master
from app.data.providers.upstox_provider import UpstoxProvider
from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy
from app.orchestration.live_operation import (
    AnalysisOutcome,
    LiveAnalysisStatus,
    LiveOperationContext,
    run_controlled_observation_set,
    run_live_analysis,
    sweep_due_checkpoints,
)
from app.orchestration.options_intelligence_pipeline import PipelineConfig, Repositories
from app.persistence.jsonl_file import (
    JsonlAuditJournalRepository,
    JsonlIvObservationRepository,
    JsonlOptionChainRepository,
    JsonlQuoteRepository,
)
from app.utils.time import utc_now

DEFAULT_QUERIES = ["NIFTY", "BANKNIFTY", "RELIANCE", "TCS", "SBIN", "INFY"]
_MASTER_CACHE_PATH = Path("data/reference/upstox_nse_instruments.json")
_MCX_MASTER_CACHE_PATH = Path("data/reference/upstox_mcx_instruments.json")
_MCX_MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/MCX.json.gz"
_PERSISTENCE_DIR = Path("data/persistence")
_JOURNAL_DIR = Path("data/persistence/audit_journal")


def _print_outcome(query: str, outcome: AnalysisOutcome, *, now: datetime) -> None:
    ts = now.isoformat()
    if outcome.status == LiveAnalysisStatus.PERSISTED:
        print(f"[{ts}] {query!r} -> PERSISTED  symbol={outcome.symbol}  market_state={outcome.market_state}  audit_id={outcome.audit_id}")
    elif outcome.status == LiveAnalysisStatus.SKIPPED_NO_DECISION:
        print(f"[{ts}] {query!r} -> SKIPPED_NO_DECISION  symbol={outcome.symbol}  market_state={outcome.market_state}  (nothing decision-worthy this run -- not persisted)")
    elif outcome.status == LiveAnalysisStatus.INVALID_QUERY:
        print(f"[{ts}] {query!r} -> INVALID_QUERY  {outcome.error}")
    else:
        print(f"[{ts}] {query!r} -> ERROR  symbol={outcome.symbol}  {outcome.error}")


async def _sweep_and_print(symbol: str, *, ctx: LiveOperationContext, now: datetime) -> None:
    checkpoint_runs = await sweep_due_checkpoints(symbol, ctx=ctx, now=now)
    for run in checkpoint_runs:
        if run.error is not None:
            print(f"    checkpoint sweep audit_id={run.audit_id}: ERROR {run.error}")
            continue
        if not run.captured:
            continue
        labels = ", ".join(f"{c.checkpoint_label.value}={c.status.value}" for c in run.captured)
        print(f"    checkpoint sweep audit_id={run.audit_id}: captured [{labels}]" + (" + reconciled" if run.reconciliation is not None else ""))


async def _run_one_query(query: str, *, ctx: LiveOperationContext, now: datetime, auto_atm: bool = False) -> None:
    try:
        if auto_atm:
            outcomes = await run_controlled_observation_set(query, ctx=ctx, now=now)
        else:
            outcomes = [await run_live_analysis(query, ctx=ctx, now=now)]
    except Exception as exc:  # noqa: BLE001 -- one query's unexpected failure must never abort the batch (Phase 2/18)
        print(f"[{query!r}] ERROR (unexpected): {exc}")
        return

    symbols_touched: list[str] = []
    for outcome in outcomes:
        _print_outcome(outcome.query, outcome, now=now)
        if outcome.symbol is not None and outcome.symbol not in symbols_touched:
            symbols_touched.append(outcome.symbol)

    for symbol in symbols_touched:
        await _sweep_and_print(symbol, ctx=ctx, now=now)


async def run(queries: list[str], *, repeat_seconds: float | None, auto_atm: bool = False) -> None:
    if not settings.upstox_access_token:
        print("WAITING: UPSTOX_ACCESS_TOKEN is not set. See docs/data-sources/PROVIDER_DECISION.md.")
        return

    async with httpx.AsyncClient() as client:
        master = await fetch_instrument_master(client, cache_path=_MASTER_CACHE_PATH)
        try:
            mcx_master = await fetch_instrument_master(client, cache_path=_MCX_MASTER_CACHE_PATH, master_url=_MCX_MASTER_URL)
        except Exception as exc:  # noqa: BLE001 -- a failed MCX fetch must degrade gracefully, never abort the run
            print(f"(MCX instrument master unavailable this run, crude oil context omitted: {exc})")
            mcx_master = None

        provider = UpstoxProvider(client=client, access_token=settings.upstox_access_token, base_url=settings.upstox_base_url)
        strategy = EMAVWAPAlignmentStrategy()
        repositories = Repositories(
            quotes=JsonlQuoteRepository(_PERSISTENCE_DIR / "quotes.jsonl"),
            option_chains=JsonlOptionChainRepository(_PERSISTENCE_DIR / "option_chains.jsonl"),
            iv_observations=JsonlIvObservationRepository(_PERSISTENCE_DIR / "iv_observations.jsonl"),
        )
        journal = JsonlAuditJournalRepository(_JOURNAL_DIR)
        ctx = LiveOperationContext(
            provider=provider, instrument_master=master, strategy=strategy, repositories=repositories,
            journal=journal, config=PipelineConfig(), mcx_instrument_master=mcx_master,
        )

        iteration = 0
        while True:
            iteration += 1
            now = utc_now()
            print(f"=== iteration {iteration}, wall-clock (UTC): {now.isoformat()} ===")
            started = time.perf_counter()
            for query in queries:
                await _run_one_query(query, ctx=ctx, now=now, auto_atm=auto_atm)
            elapsed = time.perf_counter() - started
            print(f"(iteration wall-clock: {elapsed:.2f}s for {len(queries)} quer{'y' if len(queries) == 1 else 'ies'})\n")

            if repeat_seconds is None:
                return
            await asyncio.sleep(repeat_seconds)


def main() -> None:
    args = sys.argv[1:]
    repeat_seconds: float | None = None
    auto_atm = False

    while args and args[0] in ("--repeat", "--auto-atm"):
        if args[0] == "--repeat":
            if len(args) < 2:
                print("usage: python -m scripts.run_live_analysis [--auto-atm] --repeat SECONDS [QUERY ...]")
                return
            repeat_seconds = float(args[1])
            args = args[2:]
        else:
            auto_atm = True
            args = args[1:]

    queries = args or DEFAULT_QUERIES
    asyncio.run(run(queries, repeat_seconds=repeat_seconds, auto_atm=auto_atm))


if __name__ == "__main__":
    main()
