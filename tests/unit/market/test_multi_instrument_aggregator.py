from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.market.models import Timeframe
from app.domain.market.multi_instrument_aggregator import MultiInstrumentCandleAggregator

T0 = datetime(2026, 8, 27, 3, 45, 0, tzinfo=UTC)


def _agg() -> MultiInstrumentCandleAggregator:
    return MultiInstrumentCandleAggregator(timeframe=Timeframe.M15, provider_name="test")


def test_first_tick_for_an_instrument_creates_its_aggregator_lazily() -> None:
    agg = _agg()
    assert agg.known_instruments() == []

    agg.on_tick(instrument_key="A", price=Decimal("100"), volume_since_last_tick=10, timestamp=T0)

    assert agg.known_instruments() == ["A"]
    assert agg.current_partial_candle("A") is not None
    assert agg.completed_candles("A") == []


def test_ticks_route_to_the_correct_instrument_and_stay_isolated() -> None:
    agg = _agg()

    agg.on_tick(instrument_key="A", price=Decimal("100"), volume_since_last_tick=10, timestamp=T0)
    agg.on_tick(instrument_key="B", price=Decimal("200"), volume_since_last_tick=5, timestamp=T0)
    agg.on_tick(instrument_key="A", price=Decimal("105"), volume_since_last_tick=3, timestamp=T0 + timedelta(minutes=2))

    partial_a = agg.current_partial_candle("A")
    partial_b = agg.current_partial_candle("B")
    assert partial_a is not None and partial_a.close == Decimal("105") and partial_a.volume == 13
    assert partial_b is not None and partial_b.close == Decimal("200") and partial_b.volume == 5


def test_unknown_instrument_returns_empty_state_not_an_error() -> None:
    agg = _agg()
    assert agg.completed_candles("NEVER_SEEN") == []
    assert agg.current_partial_candle("NEVER_SEEN") is None


def test_completed_candle_in_one_instrument_does_not_affect_another() -> None:
    agg = _agg()
    agg.on_tick(instrument_key="A", price=Decimal("100"), volume_since_last_tick=1, timestamp=T0)
    agg.on_tick(instrument_key="B", price=Decimal("200"), volume_since_last_tick=1, timestamp=T0)
    # Roll A's bucket over into a completed candle; B must remain untouched.
    agg.on_tick(instrument_key="A", price=Decimal("101"), volume_since_last_tick=1, timestamp=T0 + timedelta(minutes=15, seconds=1))

    assert len(agg.completed_candles("A")) == 1
    assert agg.completed_candles("B") == []
    partial_b = agg.current_partial_candle("B")
    assert partial_b is not None and partial_b.close == Decimal("200")
