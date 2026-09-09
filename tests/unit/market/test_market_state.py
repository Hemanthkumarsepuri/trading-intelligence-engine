from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain.market.freshness import DataFreshness
from app.domain.market.market_state import MarketState, assemble_market_state
from app.domain.market.models import Quote

T0 = datetime(2026, 8, 27, 9, 15, tzinfo=UTC)
INSTRUMENT_ID = "INST1"


def _quote(data_timestamp: datetime, *, instrument_id: str = INSTRUMENT_ID, last_price: str = "100") -> Quote:
    return Quote(
        provider="mock",
        freshness=DataFreshness(data_timestamp=data_timestamp, received_timestamp=data_timestamp),
        instrument_id=instrument_id,
        last_price=Decimal(last_price),
    )


def test_valid_construction_via_assemble_market_state() -> None:
    quote = _quote(T0)
    state = assemble_market_state(quote, as_of=T0)
    assert state.instrument_id == INSTRUMENT_ID
    assert state.as_of == T0
    assert state.quote == quote


def test_rejects_quote_instrument_id_mismatch() -> None:
    quote = _quote(T0, instrument_id="OTHER")
    with pytest.raises(ValidationError, match="instrument_id"):
        MarketState(instrument_id=INSTRUMENT_ID, as_of=T0, quote=quote)


def test_rejects_quote_from_the_future() -> None:
    quote = _quote(T0 + timedelta(minutes=1))
    with pytest.raises(ValidationError, match="as_of"):
        MarketState(instrument_id=INSTRUMENT_ID, as_of=T0, quote=quote)


def test_accepts_quote_exactly_at_as_of() -> None:
    quote = _quote(T0)
    state = MarketState(instrument_id=INSTRUMENT_ID, as_of=T0, quote=quote)
    assert state.quote.freshness.data_timestamp == state.as_of


def test_rejects_naive_as_of() -> None:
    quote = _quote(T0)
    with pytest.raises(ValidationError, match="timezone-aware"):
        MarketState(instrument_id=INSTRUMENT_ID, as_of=datetime(2026, 8, 27, 9, 15), quote=quote)  # noqa: DTZ001


def test_deterministic_construction() -> None:
    quote = _quote(T0)
    first = assemble_market_state(quote, as_of=T0)
    second = assemble_market_state(quote, as_of=T0)
    assert first == second


def test_no_directional_interpretive_or_technical_fields() -> None:
    forbidden = {
        "direction",
        "bullish",
        "bearish",
        "confidence",
        "signal",
        "setup",
        "technical_snapshot",
        "ema",
        "rsi",
        "macd",
        "vwap",
        "breadth",
        "mtf_alignment",
    }
    fields = {name.lower() for name in MarketState.model_fields}
    assert not (fields & forbidden)
