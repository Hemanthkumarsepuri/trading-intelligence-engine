"""Early-Move Discovery Engine -- live UAT entrypoint (real Upstox data
only, read-only, no order execution capability anywhere in this call
graph -- see SAFETY_LOCK.txt).

Runs the REAL `run_daily_research()` -- the exact same function
`GET /api/research/daily` calls -- against real live Upstox data, twice:

  1. The curated six-symbol set (`DEFAULT_RESEARCH_UNIVERSE`) -- Stage 1
     skipped (explicit symbols), all six go straight to Stage 2.
  2. The full real dynamic F&O equity universe -- Stage 1 (cheap batched
     screen) runs for real, then Stage 2 runs only on real survivors.

For every shortlisted candidate, prints every real field required to
prove the researcher is NOT simply returning today's biggest movers:
real day-change%, position-in-today's-range, M15 trend direction, VWAP
position, Stage-1 participation signal, OI/change-in-OI, directional
supporting-group counts, early-stage state (move maturity), research
confidence (setup quality), room-to-move/room-to-breakeven, the selected
contract and why it was preferred over its nearest alternative, event
risk, market context, and the real, unmodified `FinalDecision`
(actionability) -- never upgraded or overridden by anything computed
here. Every real rejection reason is also printed, unmodified.

Usage:
    python -m scripts.run_daily_research_uat
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx

from app.config.settings import settings
from app.data.providers.upstox_instrument_master import fetch_instrument_master
from app.data.providers.upstox_provider import UpstoxProvider
from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy
from app.orchestration.daily_research import (
    DEFAULT_RESEARCH_UNIVERSE,
    DailyResearchResult,
    RankedCandidate,
    build_research_thesis,
    list_fo_eligible_equity_underlyings,
    run_daily_research,
)
from app.orchestration.options_intelligence_pipeline import PipelineConfig, Repositories
from app.persistence.jsonl_file import (
    JsonlAuditJournalRepository,
    JsonlIvObservationRepository,
    JsonlOptionChainRepository,
    JsonlQuoteRepository,
    JsonlResearchOutcomeRepository,
    JsonlResearchRunRepository,
)
from app.utils.time import utc_now

_MASTER_CACHE_PATH = Path("data/reference/upstox_nse_instruments.json")
_PERSISTENCE_DIR = Path("data/persistence")
_JOURNAL_DIR = Path("data/persistence/audit_journal")
_RESEARCH_JOURNAL_DIR = Path("data/persistence/research_journal")
_RESEARCH_OUTCOME_DIR = Path("data/persistence/research_outcomes")
_UAT_OUTPUT_DIR = Path(".scratch_uat")


def _print_candidate(candidate: RankedCandidate, *, rank: int) -> dict[str, object]:
    thesis = build_research_thesis(candidate)
    c = candidate.contract
    v = candidate.response.visual
    dc = candidate.dc
    spot = dc.paths.spot if dc.paths else None
    trend = next((r for r in v.evidence if r.name == "M15 trend"), None) if v else None
    vwap = next((r for r in v.evidence if r.name == "VWAP"), None) if v else None
    regime = next((r for r in v.evidence if r.name == "Market regime"), None) if v else None
    oi_row = next((r for r in v.evidence if "Change in OI" in r.name and r.detail and "ATM" in r.name), None) if v else None

    record: dict[str, object] = {
        "rank": rank,
        "symbol": candidate.symbol,
        "direction": candidate.direction,
        "spot": str(spot) if spot is not None else None,
        "m15_trend": trend.direction if trend else None,
        "m15_trend_detail": trend.detail if trend else None,
        "vwap_position": vwap.direction if vwap else None,
        "market_regime": regime.detail if regime else None,
        "supporting_groups": dc.bullish_supporting_groups if candidate.direction == "BULLISH" else dc.bearish_supporting_groups,
        "early_stage_state": thesis.early_stage_state,
        "what_is_developing": thesis.what_is_developing,
        "why_not_extended": thesis.why_not_extended,
        "research_confidence": thesis.research_confidence,
        "final_decision_actionability": thesis.actionability,
        "market_context": thesis.market_context,
        "market_context_detail": thesis.market_context_detail,
        "participation_note": thesis.participation_note,
        "nearest_important_level": thesis.nearest_important_level,
        "room_before_level_pct": thesis.room_before_level_pct,
        "room_to_breakeven": thesis.room_to_breakeven,
        "event_risk": thesis.event_risk,
        "event_risk_reason": thesis.event_risk_reason,
        "sector_note": thesis.sector_note,
        # Sprint 4 -- multi-day early-move context, reported only.
        "structural_context": thesis.structural_context,
        "structural_context_detail": thesis.structural_context_detail,
        "participation_depth": thesis.participation_depth,
        "participation_depth_detail": thesis.participation_depth_detail,
        "relative_strength": thesis.relative_strength,
        "relative_strength_detail": thesis.relative_strength_detail,
        "pre_breakout_signal": thesis.pre_breakout_signal,
        "pre_breakout_detail": thesis.pre_breakout_detail,
        "selected_contract": f"{c.right} {c.strike}",
        "contract_ltp": str(c.ltp) if c.ltp is not None else None,
        "contract_quality": c.structural_quality,
        "liquidity_grade": c.liquidity_grade,
        "decay_verdict": c.decay_verdict,
        "why_this_contract": thesis.why_this_contract,
        "oi_change_row": oi_row.detail if oi_row else None,
        "thesis": thesis.thesis,
    }
    print(json.dumps(record, indent=2, default=str))
    return record


def _print_rejections(result: DailyResearchResult, *, label: str) -> None:
    print(f"\n--- {label}: {len(result.rejected)} real rejection(s) ---")
    for r in result.rejected:
        print(f"  {r.symbol}: {r.reason}")


async def _run_uat() -> None:
    if not settings.upstox_access_token:
        print("WAITING: UPSTOX_ACCESS_TOKEN is not set. See docs/data-sources/PROVIDER_DECISION.md.")
        return

    _UAT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    now = utc_now()
    print(f"=== Early-Move Discovery Engine live UAT -- wall-clock (UTC): {now.isoformat()} ===\n")

    async with httpx.AsyncClient(timeout=30.0) as client:
        master = await fetch_instrument_master(client, cache_path=_MASTER_CACHE_PATH)
        provider = UpstoxProvider(client=client, access_token=settings.upstox_access_token, base_url=settings.upstox_base_url)
        strategy = EMAVWAPAlignmentStrategy()
        repositories = Repositories(
            quotes=JsonlQuoteRepository(_PERSISTENCE_DIR / "quotes.jsonl"),
            option_chains=JsonlOptionChainRepository(_PERSISTENCE_DIR / "option_chains.jsonl"),
            iv_observations=JsonlIvObservationRepository(_PERSISTENCE_DIR / "iv_observations.jsonl"),
        )
        journal = JsonlAuditJournalRepository(_JOURNAL_DIR)
        research_journal = JsonlResearchRunRepository(_RESEARCH_JOURNAL_DIR)
        # Sprint 5, Objective 14 -- wire the same outcome_repository the
        # real /api/research/daily route already uses (Sprint 3), so a
        # live UAT run also exercises real ResearchObservation persistence
        # end-to-end (previously this script never passed one, so no
        # live UAT run before this sprint actually verified it).
        outcome_repository = JsonlResearchOutcomeRepository(_RESEARCH_OUTCOME_DIR)
        config = PipelineConfig()

        all_results: dict[str, object] = {"generated_at": now.isoformat()}

        # -- Run 1: curated six-symbol set (Stage 1 skipped) -----------
        print("=" * 70)
        print(f"RUN 1 -- curated six-symbol set: {DEFAULT_RESEARCH_UNIVERSE}")
        print("=" * 70)
        t0 = time.monotonic()
        six_result = await run_daily_research(
            list(DEFAULT_RESEARCH_UNIVERSE), provider=provider, instrument_master=master,
            strategy=strategy, repositories=repositories, config=config, as_of=now,
            journal=journal, research_journal=research_journal, outcome_repository=outcome_repository,
        )
        run1_elapsed = time.monotonic() - t0
        print(f"market_state={six_result.market_state}  screened={six_result.screened_count}  "
              f"deep_analyzed={six_result.deep_analyzed_count}  shortlisted={len(six_result.shortlist)}  "
              f"no_high_conviction={six_result.no_high_conviction}  elapsed_seconds={run1_elapsed:.1f}  "
              f"coverage={six_result.coverage.classification if six_result.coverage else None}")
        six_records = [_print_candidate(c, rank=c.rank) for c in six_result.shortlist]
        _print_rejections(six_result, label="RUN 1 rejections")
        all_results["run_1_six_symbol_set"] = {
            "market_state": six_result.market_state, "screened_count": six_result.screened_count,
            "deep_analyzed_count": six_result.deep_analyzed_count, "no_high_conviction": six_result.no_high_conviction,
            "elapsed_seconds": run1_elapsed,
            "coverage": six_result.coverage.classification if six_result.coverage else None,
            "shortlist": six_records, "rejected": [{"symbol": r.symbol, "reason": r.reason} for r in six_result.rejected],
        }

        # -- Run 2: full real dynamic F&O equity universe (Stage 1 runs) -
        universe = list_fo_eligible_equity_underlyings(master)
        print("\n" + "=" * 70)
        print(f"RUN 2 -- full real F&O equity universe ({len(universe)} symbols), Stage 1 screening live")
        print("=" * 70)
        t1 = time.monotonic()
        full_result = await run_daily_research(
            None, provider=provider, instrument_master=master, strategy=strategy,
            repositories=repositories, config=config, as_of=now, journal=journal, research_journal=research_journal,
            outcome_repository=outcome_repository,
        )
        run2_elapsed = time.monotonic() - t1
        print(f"market_state={full_result.market_state}  universe_size={len(full_result.universe)}  "
              f"stage_one_survivor_count={full_result.stage_one_survivor_count}  "
              f"deep_analyzed={full_result.deep_analyzed_count}  shortlisted={len(full_result.shortlist)}  "
              f"no_high_conviction={full_result.no_high_conviction}  elapsed_seconds={run2_elapsed:.1f}  "
              f"coverage={full_result.coverage.classification if full_result.coverage else None}")
        print(f"rejection_summary={full_result.rejection_summary}")
        full_records = [_print_candidate(c, rank=c.rank) for c in full_result.shortlist]
        _print_rejections(full_result, label="RUN 2 rejections (first 40)")
        all_results["run_2_full_universe"] = {
            "market_state": full_result.market_state, "universe_size": len(full_result.universe),
            "stage_one_survivor_count": full_result.stage_one_survivor_count,
            "deep_analyzed_count": full_result.deep_analyzed_count, "no_high_conviction": full_result.no_high_conviction,
            "elapsed_seconds": run2_elapsed,
            "coverage": full_result.coverage.classification if full_result.coverage else None,
            "rejection_summary": full_result.rejection_summary,
            "shortlist": full_records, "rejected_count": len(full_result.rejected),
            "rejected_sample": [{"symbol": r.symbol, "reason": r.reason} for r in full_result.rejected[:40]],
        }

        out_path = _UAT_OUTPUT_DIR / f"uat_{now.strftime('%Y%m%dT%H%M%SZ')}.json"
        out_path.write_text(json.dumps(all_results, indent=2, default=str), encoding="utf-8")
        print(f"\nFull UAT record written to: {out_path}")


def main() -> None:
    asyncio.run(_run_uat())


if __name__ == "__main__":
    main()
