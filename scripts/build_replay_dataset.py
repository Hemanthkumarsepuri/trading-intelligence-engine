"""Build the REAL historical research dataset: replay every locally
backfilled symbol over its full available window with the UNMODIFIED
intelligence engine, collapse per-bar observations into episodes, and
record KNEW_THEN separately from HAPPENED_AFTER.

Release gate (Sections 9-12). The companion to
`scripts.backfill_replay_history`, which fetches the real candles this
reads. See `app.orchestration.replay_dataset` for the record shape and
why episodes (not bars) are the counting unit.

Honesty properties, inherited rather than re-implemented here:
  - NO LOOKAHEAD. Every `as_of` bound is enforced inside
    `app.orchestration.historical_replay` / `HistoricalReplayProvider`.
    Outcomes are computed afterwards, from the same local series, bounded
    by the dataset's own last real candle.
  - PRICE-ONLY. There is no historical option chain or futures history,
    so every observation is `derivatives_evidence_available=False` and
    derivatives-dependent patterns are never selected. Nothing here
    fabricates OI, IV, greeks, basis or a contract.
  - Written to its OWN locations, never the live research stores:
      data/research_dataset/replay_dataset.jsonl          one row per episode
      data/research_dataset/replay_dataset_summary.json   sizes and runtime
      data/persistence/replay_research_outcomes/          representative observations

Run (offline -- uses only the locally cached instrument master):
    python -m scripts.build_replay_dataset [--workers N] [SYMBOL ...]
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.data.providers.nse_sector_index import fetch_sector_index
from app.data.providers.upstox_instrument_master import fetch_instrument_master, resolve_symbol
from app.domain.audit.research_models import ResearchObservation
from app.domain.market.models import Candle
from app.domain.market.trading_calendar import apply_nse_calendar_file
from app.orchestration.dashboard_service import AnalyzeResponse
from app.orchestration.historical_replay import replay_symbol_window
from app.orchestration.options_intelligence_pipeline import Repositories
from app.orchestration.replay_dataset import (
    DATASET_VERSION,
    CapturedObservation,
    MarketContextSeries,
    build_dataset_row,
    capture_knew_then,
    group_into_episodes,
)
from app.persistence.in_memory import (
    InMemoryCandleRepository,
    InMemoryIvObservationRepository,
    InMemoryOptionChainRepository,
    InMemoryQuoteRepository,
)
from app.persistence.jsonl_file import JsonlResearchOutcomeRepository
from scripts.csv_candle_loader import load_candles_from_csv

HISTORY_DIR = Path("data/historical_replay")
CONTEXT_CSV = HISTORY_DIR / "context" / "NIFTY50_M15.csv"
DATASET_DIR = Path("data/research_dataset")
REPLAY_OUTCOME_DIR = Path("data/persistence/replay_research_outcomes")
MASTER_CACHE = Path("data/reference/upstox_nse_instruments.json")
SECTOR_CACHE = Path("data/reference/nse_sector_index.csv")
HOLIDAYS = Path("data/reference/nse_trading_holidays.txt")


def available_symbols() -> dict[str, Path]:
    return {p.stem.removesuffix("_M15"): p for p in sorted(HISTORY_DIR.glob("*_M15.csv"))}


async def _load_master() -> list[dict[str, object]]:
    if not MASTER_CACHE.exists():
        raise SystemExit(f"instrument master cache missing: {MASTER_CACHE} (start the dashboard once to cache it)")
    async with httpx.AsyncClient() as client:
        return await fetch_instrument_master(client, cache_path=MASTER_CACHE)


def _load_candles(master: list[dict[str, object]], symbol: str, path: Path) -> list[Candle]:
    ref = resolve_symbol(master, symbol)
    if ref is None:
        return []
    # Candles are keyed by the resolved Upstox INSTRUMENT KEY, which is what
    # the replay queries -- keyed by ticker the replay silently sees 0 bars.
    return load_candles_from_csv(path, instrument_id=ref.instrument_key)


def replay_one_symbol(symbol: str, csv_path: str) -> dict[str, Any]:
    """Worker-process entry point. Returns plain JSON-able data only."""
    apply_nse_calendar_file(HOLIDAYS)
    started = time.perf_counter()
    master = asyncio.run(_load_master())
    candles = _load_candles(master, symbol, Path(csv_path))
    if not candles:
        return {"symbol": symbol, "error": "no candles or symbol not in instrument master", "captured": []}

    captured: list[dict[str, Any]] = []

    def _observe(observation: ResearchObservation, response: AnalyzeResponse) -> None:
        captured.append({"observation": observation.model_dump(mode="json"), "knew_then": capture_knew_then(observation, response)})

    async def _run() -> tuple[int, int, int]:
        repository = InMemoryCandleRepository()
        for candle in candles:
            await repository.save(candle)
        start = min(c.freshness.data_timestamp for c in candles)
        end = max(c.freshness.data_timestamp for c in candles)
        from app.utils.time import to_ist  # local: keep worker import surface small

        result = await replay_symbol_window(
            symbol, to_ist(start).date(), to_ist(end).date(), candle_repository=repository, instrument_master=master,
            repositories=Repositories(
                quotes=InMemoryQuoteRepository(), option_chains=InMemoryOptionChainRepository(),
                iv_observations=InMemoryIvObservationRepository(),
            ),
            on_observation=_observe,
        )
        return len(result.session_results), sum(r.bars_evaluated for r in result.session_results), len(result.observations)

    try:
        sessions, bars, raw = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 -- one symbol must never abort the whole dataset
        return {"symbol": symbol, "error": f"{type(exc).__name__}: {str(exc)[:200]}", "captured": captured}
    return {
        "symbol": symbol, "error": None, "sessions": sessions, "bars": bars, "raw_observations": raw,
        "candles": len(candles), "seconds": round(time.perf_counter() - started, 1), "captured": captured,
    }


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    workers = 5
    if "--workers" in args:
        i = args.index("--workers")
        workers = int(args[i + 1])
        del args[i : i + 2]
    wanted = {a.upper() for a in args}
    symbols = {s: p for s, p in available_symbols().items() if not wanted or s in wanted}
    if not symbols:
        print(f"no backfilled history in {HISTORY_DIR} (run scripts.backfill_replay_history first)")
        return 2

    apply_nse_calendar_file(HOLIDAYS)
    started = time.perf_counter()
    print(f"replaying {len(symbols)} symbol(s) with {workers} worker process(es)...", flush=True)
    results: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(replay_one_symbol, s, str(p)): s for s, p in symbols.items()}
        for future in futures:
            result = future.result()
            results.append(result)
            status = result["error"] or (
                f"sessions={result['sessions']} bars={result['bars']} raw_obs={result['raw_observations']} {result['seconds']}s"
            )
            print(f"  {result['symbol']}: {status}", flush=True)
    replay_seconds = time.perf_counter() - started

    master = asyncio.run(_load_master())
    candles_by_symbol = {s: _load_candles(master, s, p) for s, p in symbols.items()}
    market = MarketContextSeries(load_candles_from_csv(CONTEXT_CSV, instrument_id="NIFTY50")) if CONTEXT_CSV.exists() else None
    sector_map: dict[str, str] = {}
    if SECTOR_CACHE.exists():
        async def _sectors() -> dict[str, str]:
            async with httpx.AsyncClient() as client:
                return await fetch_sector_index(client, cache_path=SECTOR_CACHE)
        sector_map = asyncio.run(_sectors())
    dataset_as_of = max(c.freshness.data_timestamp for candles in candles_by_symbol.values() for c in candles)

    captured = [
        CapturedObservation(observation=ResearchObservation.model_validate(item["observation"]), knew_then=item["knew_then"])
        for result in results for item in result["captured"]
    ]
    episodes = group_into_episodes(captured)

    DATASET_DIR.mkdir(parents=True, exist_ok=True)
    if REPLAY_OUTCOME_DIR.exists():
        shutil.rmtree(REPLAY_OUTCOME_DIR)  # rebuilt from scratch every run: never mixes dataset versions
    repository = JsonlResearchOutcomeRepository(REPLAY_OUTCOME_DIR)
    status_counts: Counter[str] = Counter()
    pattern_counts: Counter[str] = Counter()
    rows_path = DATASET_DIR / "replay_dataset.jsonl"
    with rows_path.open("w", encoding="utf-8", newline="\n") as handle:
        for episode in episodes:
            symbol = episode.first.observation.symbol
            observation, status, row = build_dataset_row(
                episode, candles=candles_by_symbol[symbol], dataset_as_of=dataset_as_of, market=market,
                sector=sector_map.get(symbol),
                provenance={"candle_source": f"{HISTORY_DIR.as_posix()}/{symbol}_M15.csv", "provider": "upstox historical-candle v3"},
            )
            asyncio.run(repository.save_observation(observation))
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            status_counts[status.value] += 1
            pattern_counts[str(observation.pattern)] += 1

    summary = {
        "dataset_version": DATASET_VERSION,
        "built_at": datetime.now(UTC).isoformat(),
        "dataset_as_of": dataset_as_of.isoformat(),
        "symbols": len(symbols),
        "symbols_failed": [r["symbol"] for r in results if r["error"]],
        "candles": sum(r.get("candles", 0) for r in results),
        "sessions": sum(r.get("sessions", 0) for r in results),
        "bars_evaluated": sum(r.get("bars", 0) for r in results),
        "raw_bar_observations": len(captured),
        "episodes": len(episodes),
        "episodes_by_pattern": dict(pattern_counts.most_common()),
        "reference_status_plus_5d": dict(status_counts.most_common()),
        "replay_seconds": round(replay_seconds, 1),
        "total_seconds": round(time.perf_counter() - started, 1),
        "workers": workers,
        "market_context_series": CONTEXT_CSV.as_posix() if market is not None else None,
        "derivatives_history": "DERIVATIVES_HISTORY_UNAVAILABLE",
        "per_symbol": [{k: v for k, v in r.items() if k != "captured"} for r in results],
    }
    (DATASET_DIR / "replay_dataset_summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "per_symbol"}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
