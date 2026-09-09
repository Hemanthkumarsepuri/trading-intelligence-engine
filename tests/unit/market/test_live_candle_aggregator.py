from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.market.live_candle_aggregator import LiveCandleAggregator, PartialCandle
from app.domain.market.models import Timeframe

INSTRUMENT_ID = "RELIANCE"
T0 = datetime(2026, 8, 27, 3, 45, 0, tzinfo=UTC)  # 09:15 IST -- an M15 bucket boundary


def _aggregator() -> LiveCandleAggregator:
    return LiveCandleAggregator(instrument_id=INSTRUMENT_ID, timeframe=Timeframe.M15, provider_name="test")


def test_first_tick_starts_a_partial_candle_not_a_completed_one() -> None:
    agg = _aggregator()
    agg.on_tick(price=Decimal("100"), volume_since_last_tick=10, timestamp=T0)

    assert agg.completed_candles == []
    partial = agg.current_partial_candle
    assert isinstance(partial, PartialCandle)
    assert partial.open == partial.high == partial.low == partial.close == Decimal("100")
    assert partial.volume == 10
    assert partial.period_start == T0
    assert partial.period_end == T0 + timedelta(minutes=15)


def test_ticks_within_same_bucket_update_high_low_close_and_accumulate_volume() -> None:
    agg = _aggregator()
    agg.on_tick(price=Decimal("100"), volume_since_last_tick=10, timestamp=T0)
    agg.on_tick(price=Decimal("105"), volume_since_last_tick=5, timestamp=T0 + timedelta(minutes=2))
    agg.on_tick(price=Decimal("98"), volume_since_last_tick=3, timestamp=T0 + timedelta(minutes=5))
    agg.on_tick(price=Decimal("101"), volume_since_last_tick=2, timestamp=T0 + timedelta(minutes=10))

    partial = agg.current_partial_candle
    assert partial is not None
    assert partial.open == Decimal("100")
    assert partial.high == Decimal("105")
    assert partial.low == Decimal("98")
    assert partial.close == Decimal("101")
    assert partial.volume == 20
    assert agg.completed_candles == []  # bucket has not rolled over yet


def test_tick_in_later_bucket_finalizes_previous_partial_into_completed_candle() -> None:
    agg = _aggregator()
    agg.on_tick(price=Decimal("100"), volume_since_last_tick=10, timestamp=T0)
    agg.on_tick(price=Decimal("110"), volume_since_last_tick=5, timestamp=T0 + timedelta(minutes=10))
    next_bucket_tick = T0 + timedelta(minutes=15, seconds=1)
    agg.on_tick(price=Decimal("111"), volume_since_last_tick=7, timestamp=next_bucket_tick)

    completed = agg.completed_candles
    assert len(completed) == 1
    finalized = completed[0]
    assert finalized.open == Decimal("100")
    assert finalized.high == Decimal("110")
    assert finalized.low == Decimal("100")
    assert finalized.close == Decimal("110")
    assert finalized.volume == 15
    assert finalized.freshness.data_timestamp == T0

    partial = agg.current_partial_candle
    assert partial is not None
    assert partial.period_start == T0 + timedelta(minutes=15)
    assert partial.open == Decimal("111")


def test_out_of_order_tick_is_rejected_not_silently_reordered() -> None:
    agg = _aggregator()
    agg.on_tick(price=Decimal("100"), volume_since_last_tick=1, timestamp=T0 + timedelta(minutes=20))

    with pytest.raises(ValueError, match="out-of-order"):
        agg.on_tick(price=Decimal("99"), volume_since_last_tick=1, timestamp=T0)


def test_naive_timestamp_rejected() -> None:
    agg = _aggregator()
    with pytest.raises(ValueError, match="timezone-aware"):
        agg.on_tick(price=Decimal("100"), volume_since_last_tick=1, timestamp=datetime(2026, 8, 27, 9, 15))  # noqa: DTZ001


def test_gap_in_ticks_produces_a_gap_not_a_fabricated_candle() -> None:
    """A quiet period spanning multiple bucket widths must never invent
    placeholder candles for the buckets nothing traded in.
    """
    agg = _aggregator()
    agg.on_tick(price=Decimal("100"), volume_since_last_tick=1, timestamp=T0)
    # Next tick arrives 3 bucket-widths later (a genuine quiet gap).
    far_later = T0 + timedelta(minutes=45, seconds=1)
    agg.on_tick(price=Decimal("120"), volume_since_last_tick=1, timestamp=far_later)

    assert len(agg.completed_candles) == 1  # only the bucket that actually had ticks
    assert agg.completed_candles[0].freshness.data_timestamp == T0


def test_wall_clock_time_never_promotes_a_partial_candle_on_its_own() -> None:
    """No method on this aggregator reads the clock or promotes a partial
    candle merely because its nominal period_end has passed -- only an
    actual later tick can do that (see module docstring).
    """
    agg = _aggregator()
    agg.on_tick(price=Decimal("100"), volume_since_last_tick=1, timestamp=T0)
    # No further ticks arrive, ever -- completed_candles must stay empty
    # regardless of how much real time elapses, since nothing here reads
    # the wall clock.
    assert agg.completed_candles == []
    assert agg.current_partial_candle is not None
    assert agg.current_partial_candle.period_start == T0


def test_completed_candles_and_partial_are_snapshots_not_live_references() -> None:
    agg = _aggregator()
    agg.on_tick(price=Decimal("100"), volume_since_last_tick=1, timestamp=T0)
    agg.on_tick(price=Decimal("101"), volume_since_last_tick=1, timestamp=T0 + timedelta(minutes=16))

    snapshot = agg.completed_candles
    snapshot.append(agg.completed_candles[0])  # mutate the returned list
    assert len(agg.completed_candles) == 1  # internal state unaffected


def test_unsupported_timeframe_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="does not support timeframe"):
        LiveCandleAggregator(instrument_id=INSTRUMENT_ID, timeframe=Timeframe.M30, provider_name="test")


def test_tick_aggregation_reproduces_batch_built_candles_exactly() -> None:
    """The live tick-aggregation path must reconstruct exactly the same
    `Candle`s the batch/historical path (`build_synthetic_candles()`) built
    directly -- proving live streaming and historical/replay converge on
    identical data, the same guarantee `test_candle_inputs_replay_equivalence.py`
    already proves for the persistence/provider path.
    """
    from scripts.dev_replay_demo import build_synthetic_candles

    original = build_synthetic_candles()
    agg = LiveCandleAggregator(instrument_id="DEMO", timeframe=Timeframe.M15, provider_name="synthetic-dev")

    for candle in original:
        start = candle.freshness.data_timestamp
        quarter = candle.volume // 4 or 1
        for offset_seconds, price, volume in (
            (1, candle.open, quarter),
            (300, candle.high, quarter),
            (600, candle.low, quarter),
            (899, candle.close, candle.volume - 3 * quarter),
        ):
            agg.on_tick(price=price, volume_since_last_tick=volume, timestamp=start + timedelta(seconds=offset_seconds))

    # Push one final tick into the next bucket to flush the very last candle.
    agg.on_tick(
        price=original[-1].close,
        volume_since_last_tick=1,
        timestamp=original[-1].freshness.data_timestamp + timedelta(minutes=15, seconds=1),
    )

    reconstructed = agg.completed_candles
    assert len(reconstructed) == len(original)
    for orig, rebuilt in zip(original, reconstructed, strict=True):
        assert rebuilt.freshness.data_timestamp == orig.freshness.data_timestamp
        assert rebuilt.open == orig.open
        assert rebuilt.high == orig.high
        assert rebuilt.low == orig.low
        assert rebuilt.close == orig.close
        assert rebuilt.volume == orig.volume


def test_deterministic_given_identical_tick_sequence() -> None:
    def replay() -> list[Decimal]:
        agg = _aggregator()
        agg.on_tick(price=Decimal("100"), volume_since_last_tick=10, timestamp=T0)
        agg.on_tick(price=Decimal("105"), volume_since_last_tick=5, timestamp=T0 + timedelta(minutes=5))
        agg.on_tick(price=Decimal("110"), volume_since_last_tick=5, timestamp=T0 + timedelta(minutes=16))
        return [c.close for c in agg.completed_candles]

    assert replay() == replay()
