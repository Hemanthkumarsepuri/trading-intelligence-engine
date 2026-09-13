from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.data.normalization.dhan_normalizer import DhanNormalizer
from app.data.normalization.mock_normalizer import MockNormalizer
from app.data.providers.base import RawCandle, RawOptionChain, RawOptionLeg, RawQuote
from app.domain.market.models import OptionRight, Timeframe


def test_normalize_candle_maps_fields_and_stamps_provider() -> None:
    normalizer = DhanNormalizer()
    raw = RawCandle(
        timestamp=datetime(2026, 8, 27, 9, 15, tzinfo=UTC), open=100.1, high=101.2, low=99.9, close=100.5,
        volume=1000, open_interest=50,
    )
    received_at = datetime(2026, 8, 27, 9, 15, 5, tzinfo=UTC)

    candle = normalizer.normalize_candle(raw, instrument_id="INST1", timeframe=Timeframe.M5, received_at=received_at)

    assert candle.provider == "dhan"
    assert candle.open == Decimal("100.1")
    assert candle.volume == 1000
    assert candle.open_interest == 50
    assert candle.freshness.data_timestamp == raw.timestamp
    assert candle.freshness.received_timestamp == received_at


def test_normalize_quote_maps_optional_fields() -> None:
    normalizer = MockNormalizer()
    raw = RawQuote(security_id="1333", last_price=250.5, previous_close=248.0, volume=5000, average_price=249.0)
    received_at = datetime(2026, 8, 27, 9, 15, tzinfo=UTC)

    quote = normalizer.normalize_quote(raw, instrument_id="INST1", received_at=received_at)

    assert quote.provider == "mock"
    assert quote.last_price == Decimal("250.5")
    assert quote.previous_close == Decimal("248.0")
    assert quote.average_price == Decimal("249.0")


def test_normalize_quote_floors_a_future_exchange_timestamp_to_received_at() -> None:
    """Daily Market Researcher audit -- real defect found live 2026-08-31:
    a fast-moving market can produce a real Upstox `last_trade_time` a
    few seconds after this request's frozen `received_at`/`as_of` (real
    wall-clock time moved on during the multi-step pipeline; not the
    2026-08-28 malformed-timestamp bug). Previously this raised an
    uncaught `DataFreshness` ValidationError; now it's conservatively
    floored to `received_at` (age 0), never crashing, and never claiming
    a timestamp later than what the caller considers 'now'. The PRICE is
    untouched -- only the timestamp label."""
    normalizer = MockNormalizer()
    received_at = datetime(2026, 8, 31, 9, 15, 0, tzinfo=UTC)
    exchange_ahead = datetime(2026, 8, 31, 9, 15, 5, tzinfo=UTC)  # 5s AFTER received_at
    raw = RawQuote(security_id="1333", last_price=250.5, exchange_timestamp=exchange_ahead)

    quote = normalizer.normalize_quote(raw, instrument_id="INST1", received_at=received_at)

    assert quote.last_price == Decimal("250.5")  # value never altered
    assert quote.freshness.data_timestamp == received_at  # floored, not the future value
    assert quote.freshness.received_timestamp == received_at
    assert quote.freshness.received_timestamp >= quote.freshness.data_timestamp  # DataFreshness's own guard holds


def test_normalize_quote_a_severely_future_exchange_timestamp_still_raises() -> None:
    """The floor only covers ordinary pipeline latency (bounded tolerance)
    -- a severely wrong future timestamp (hours ahead, matching the real
    2026-08-28 malformed-`last_trade_time` bug class, not benign clock
    drift) must still be rejected by `DataFreshness`'s no-look-ahead
    guard, not silently papered over."""
    normalizer = MockNormalizer()
    received_at = datetime(2026, 8, 31, 9, 15, 0, tzinfo=UTC)
    exchange_way_ahead = datetime(2026, 8, 31, 11, 15, 0, tzinfo=UTC)  # 2 hours ahead
    raw = RawQuote(security_id="1333", last_price=250.5, exchange_timestamp=exchange_way_ahead)

    with pytest.raises(ValidationError):
        normalizer.normalize_quote(raw, instrument_id="INST1", received_at=received_at)


def test_normalize_quote_a_genuinely_past_exchange_timestamp_is_unaffected() -> None:
    """The floor only ever pulls a timestamp BACKWARD toward `received_at`
    -- a normal, real, past exchange timestamp is passed through exactly
    as before (no regression to the ordinary case)."""
    normalizer = MockNormalizer()
    received_at = datetime(2026, 8, 31, 9, 15, 5, tzinfo=UTC)
    exchange_past = datetime(2026, 8, 31, 9, 15, 0, tzinfo=UTC)
    raw = RawQuote(security_id="1333", last_price=250.5, exchange_timestamp=exchange_past)

    quote = normalizer.normalize_quote(raw, instrument_id="INST1", received_at=received_at)

    assert quote.freshness.data_timestamp == exchange_past


def test_normalize_quote_a_severely_past_exchange_timestamp_is_still_accepted() -> None:
    """Sprint 1 regression -- the skew guard only ever concerns FORWARD
    (future) skew; an exchange timestamp far in the past (e.g. a real
    stale/delayed quote) is a data-freshness/staleness concern for a
    different layer (`is_stale()`), never a `DataFreshness` construction
    error -- no amount of past skew should ever raise here."""
    normalizer = MockNormalizer()
    received_at = datetime(2026, 8, 31, 9, 15, 0, tzinfo=UTC)
    exchange_way_past = datetime(2026, 8, 31, 2, 0, 0, tzinfo=UTC)  # ~7 hours in the past
    raw = RawQuote(security_id="1333", last_price=250.5, exchange_timestamp=exchange_way_past)

    quote = normalizer.normalize_quote(raw, instrument_id="INST1", received_at=received_at)

    assert quote.freshness.data_timestamp == exchange_way_past
    assert quote.freshness.data_age == received_at - exchange_way_past


def test_normalize_option_chain_computes_change_in_oi() -> None:
    normalizer = DhanNormalizer()
    ce = RawOptionLeg(
        security_id="42528", right=OptionRight.CE, last_price=134.0, volume=100, open_interest=3786445,
        previous_open_interest=402220, implied_volatility=9.79, delta=0.53, theta=-15.15, gamma=0.0013, vega=12.18,
    )
    raw_chain = RawOptionChain(
        underlying="NIFTY", expiry=date(2026, 9, 25), underlying_last_price=25642.8, strikes={25650.0: [ce]}
    )
    received_at = datetime(2026, 8, 27, 9, 15, tzinfo=UTC)

    snapshot = normalizer.normalize_option_chain(raw_chain, received_at=received_at)

    assert snapshot.underlying == "NIFTY"
    assert len(snapshot.legs) == 1
    leg = snapshot.legs[0]
    assert leg.strike == Decimal("25650.0")
    assert leg.change_in_open_interest == 3786445 - 402220
    assert leg.provider == "dhan"
