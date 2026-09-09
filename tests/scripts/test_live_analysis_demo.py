from __future__ import annotations

from scripts.live_analysis_demo import render_current_state, run_live_pipeline


def test_pipeline_produces_a_result_and_latency_samples() -> None:
    run = run_live_pipeline()

    assert run.last_result is not None
    assert len(run.latency_samples_ms) > 0
    assert all(sample >= 0 for sample in run.latency_samples_ms)


def test_latency_measurement_does_not_alter_result() -> None:
    """Running the pipeline twice (real wall-clock time necessarily differs
    between runs, so the latency numbers differ) must still produce the
    exact same strategy/analysis result -- proving the timing code has no
    influence on the computation it measures.
    """
    first = run_live_pipeline()
    second = run_live_pipeline()

    assert first.last_result == second.last_result
    assert len(first.latency_samples_ms) == len(second.latency_samples_ms)


def test_render_current_state_is_clearly_labeled_synthetic() -> None:
    run = run_live_pipeline()
    report = render_current_state(run)

    assert "SYNTHETIC" in report
    assert "NOT REAL MARKET PERFORMANCE" in report
    assert "LOOK-AHEAD: NONE" in report
    assert "SETUP:" in report
    assert "PROCESSING LATENCY" in report


def test_final_result_matches_direct_batch_computation() -> None:
    """The live tick-by-tick pipeline's final result must agree with what
    `assemble_current_analysis()` produces directly from the equivalent
    batch-built candles -- the live path must not silently diverge from the
    already-proven-safe batch/replay path.

    At the very last tick, the last candle's own bucket has not yet rolled
    over (no later tick has arrived to finalize it), so it is correctly
    still `current_partial_candle`, not part of `completed_candles` -- the
    batch comparison must therefore use only the candles *before* it, to
    compare like with like.
    """
    from datetime import timedelta

    from app.domain.market.freshness import DataFreshness
    from app.domain.market.market_state import assemble_market_state
    from app.domain.market.models import Quote, Timeframe
    from app.domain.strategy.current_analysis import assemble_current_analysis
    from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy
    from scripts.dev_replay_demo import build_synthetic_candles
    from scripts.live_analysis_demo import INSTRUMENT_ID

    live_run = run_live_pipeline()

    batch_candles = build_synthetic_candles()
    completed_only = batch_candles[:-1]  # the still-partial last bucket excluded, matching the live pipeline
    as_of = batch_candles[-1].freshness.data_timestamp + timedelta(seconds=899)
    quote = Quote(
        provider="synthetic-dev",
        freshness=DataFreshness(data_timestamp=as_of, received_timestamp=as_of),
        instrument_id=INSTRUMENT_ID,
        last_price=batch_candles[-1].close,
    )
    market_state = assemble_market_state(quote, as_of=as_of)
    batch_result = assemble_current_analysis(
        market_state=market_state,
        completed_candles={Timeframe.M15: completed_only},
        current_partial_candle=None,
        strategy=EMAVWAPAlignmentStrategy(),
        as_of=as_of,
    )

    assert live_run.last_result.setup == batch_result.setup
    assert live_run.last_result.ema_alignment == batch_result.ema_alignment
    assert live_run.last_result.price_vs_vwap == batch_result.price_vs_vwap
    assert live_run.last_result.ema9 == batch_result.ema9
    assert live_run.last_result.vwap_value == batch_result.vwap_value
