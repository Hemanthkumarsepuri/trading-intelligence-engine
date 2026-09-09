"""Live analysis demo — DEVELOPMENT / SYNTHETIC REPLAY, NOT REAL MARKET DATA.

Feeds a deterministic synthetic tick stream through the real production
pipeline — `LiveCandleAggregator` -> `assemble_current_analysis()` ->
unmodified `EMAVWAPAlignmentStrategy` — one tick at a time, and prints the
"what does the strategy see right now, and why" auditable report. Also
measures actual application processing latency (tick received -> result
produced) with `time.perf_counter()`.

The latency measurement is observability only: `time.perf_counter()` is
called *around* the pipeline, never inside `domain/`, and its value is never
fed back into any strategy/domain function — it cannot influence the result,
only report how long producing it took. See
`tests/scripts/test_live_analysis_demo.py::test_latency_measurement_does_not_alter_result`.

Run: `python -m scripts.live_analysis_demo`
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from app.domain.market.freshness import DataFreshness
from app.domain.market.live_candle_aggregator import LiveCandleAggregator
from app.domain.market.market_state import assemble_market_state
from app.domain.market.models import Candle, Quote, Timeframe
from app.domain.strategy.current_analysis import CurrentAnalysisResult, assemble_current_analysis
from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy
from app.utils.time import to_ist
from scripts.dev_replay_demo import build_synthetic_candles

INSTRUMENT_ID = "DEMO"


def _ticks_from_candle(candle: Candle) -> list[tuple[Decimal, int, datetime]]:
    """Splits one already-built candle into 4 ticks (open/high/low/close, in
    chronological order within the bucket) that, once fed through
    `LiveCandleAggregator`, reconstruct that exact same candle — proven by
    `tests/unit/market/test_live_candle_aggregator.py::
    test_tick_aggregation_reproduces_batch_built_candles_exactly`.
    """
    start = candle.freshness.data_timestamp
    quarter = candle.volume // 4 or 1
    offsets_prices_volumes = (
        (1, candle.open, quarter),
        (300, candle.high, quarter),
        (600, candle.low, quarter),
        (899, candle.close, candle.volume - 3 * quarter),
    )
    return [(price, volume, start + timedelta(seconds=offset)) for offset, price, volume in offsets_prices_volumes]


@dataclass
class PipelineRun:
    last_result: CurrentAnalysisResult
    latency_samples_ms: list[float]


def run_live_pipeline() -> PipelineRun:
    """Runs the full tick -> aggregate -> current-analysis pipeline over
    every tick in the deterministic synthetic stream, timing every call.
    """
    strategy = EMAVWAPAlignmentStrategy()
    aggregator = LiveCandleAggregator(instrument_id=INSTRUMENT_ID, timeframe=Timeframe.M15, provider_name="synthetic-dev")

    last_result: CurrentAnalysisResult | None = None
    latency_samples_ms: list[float] = []

    for candle in build_synthetic_candles():
        for price, volume, timestamp in _ticks_from_candle(candle):
            started = time.perf_counter()

            aggregator.on_tick(price=price, volume_since_last_tick=volume, timestamp=timestamp)
            as_of = timestamp
            quote = Quote(
                provider="synthetic-dev",
                freshness=DataFreshness(data_timestamp=as_of, received_timestamp=as_of),
                instrument_id=INSTRUMENT_ID,
                last_price=price,
            )
            market_state = assemble_market_state(quote, as_of=as_of)
            result = assemble_current_analysis(
                market_state=market_state,
                completed_candles={Timeframe.M15: aggregator.completed_candles},
                current_partial_candle=aggregator.current_partial_candle,
                strategy=strategy,
                as_of=as_of,
            )

            latency_samples_ms.append((time.perf_counter() - started) * 1000)
            last_result = result

    assert last_result is not None
    return PipelineRun(last_result=last_result, latency_samples_ms=latency_samples_ms)


def render_current_state(run: PipelineRun) -> str:
    result = run.last_result
    samples = run.latency_samples_ms
    last_latency = samples[-1]
    avg_latency = sum(samples) / len(samples)
    max_latency = max(samples)

    last_completed = (
        to_ist(result.last_completed_candle_timestamp).strftime("%H:%M")
        if result.last_completed_candle_timestamp
        else "none yet"
    )

    lines = [
        "=" * 50,
        "CURRENT STRATEGY ANALYSIS - SYNTHETIC / DEVELOPMENT ONLY",
        "=" * 50,
        "",
        f"TIME: {to_ist(result.as_of).strftime('%H:%M:%S')} IST",
        f"INSTRUMENT: {result.instrument_id}",
        f"TIMEFRAME: {result.timeframe.value}",
        f"DATA AS OF: {to_ist(result.as_of).strftime('%H:%M:%S')} IST",
        f"LAST COMPLETED CANDLE: {last_completed}",
        f"CANDLES AVAILABLE: {result.candles_available}",
        "",
        f"EMA9: {result.ema9}",
        f"EMA21: {result.ema21}",
        f"EMA50: {result.ema50}",
        f"EMA ALIGNMENT: {result.ema_alignment.value if result.ema_alignment else result.ema_status.value}",
        "",
        f"M15 VWAP: {result.vwap_value}",
        f"PRICE: {result.price}",
        f"PRICE VS VWAP: {result.price_vs_vwap.value if result.price_vs_vwap else result.vwap_status.value}",
        "",
        f"SETUP: {result.setup.direction if result.setup else 'NONE'}",
        f"STRATEGY VERSION: {result.strategy_version}",
        "",
        f"DATA FRESHNESS: {result.data_freshness_seconds:.1f}s",
        "LOOK-AHEAD: NONE (as_of-bounded; see tests/safety/test_ema_vwap_alignment_no_lookahead.py)",
        "",
        f"PROCESSING LATENCY (this tick): {last_latency:.3f} ms",
        f"PROCESSING LATENCY (avg over {len(samples)} ticks): {avg_latency:.3f} ms",
        f"PROCESSING LATENCY (max): {max_latency:.3f} ms",
        "  (application compute time only -- tick to result; excludes network/broker latency)",
        "",
        "=" * 50,
        "SYNTHETIC DATA. NOT REAL MARKET PERFORMANCE.",
        "=" * 50,
    ]
    return "\n".join(lines)


def main() -> None:
    run = run_live_pipeline()
    print(render_current_state(run))


if __name__ == "__main__":
    main()
