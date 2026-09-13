from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.data.providers.health import HealthStatus, ProviderHealth
from app.data.validation.quality_gate import DataQualityGate
from app.domain.market.freshness import DataFreshness, FreshnessClass
from app.domain.market.models import (
    Candle,
    OptionChainSnapshot,
    OptionQuote,
    OptionRight,
    Quote,
    Timeframe,
)


def _freshness(data_ts: datetime, received_ts: datetime | None = None) -> DataFreshness:
    return DataFreshness(data_timestamp=data_ts, received_timestamp=received_ts or data_ts)


def _candle(ts: datetime, *, close: Decimal = Decimal("100"), volume: int = 100) -> Candle:
    return Candle(
        provider="mock",
        freshness=_freshness(ts),
        instrument_id="INST1",
        timeframe=Timeframe.M5,
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=volume,
    )


def test_valid_candles_pass_all_checks() -> None:
    gate = DataQualityGate()
    t0 = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
    candles = [_candle(t0), _candle(t0 + timedelta(minutes=5))]
    result = gate.evaluate_candles(
        candles, freshness_class=FreshnessClass.ANALYSIS_INTERVAL, as_of=t0 + timedelta(minutes=5)
    )
    assert result.valid, result.failed_checks


def test_stale_candle_fails_freshness_check() -> None:
    gate = DataQualityGate()
    t0 = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
    stale = Candle(
        provider="mock",
        freshness=_freshness(t0, t0 + timedelta(seconds=30)),
        instrument_id="INST1",
        timeframe=Timeframe.M5,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=100,
    )
    result = gate.evaluate_candles([stale], freshness_class=FreshnessClass.REAL_TIME, as_of=t0 + timedelta(seconds=30))
    assert not result.valid
    assert any(c.check.value == "FRESHNESS" and not c.passed for c in result.checks)


def test_future_dated_candle_fails_timestamp_validity() -> None:
    gate = DataQualityGate()
    t0 = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
    future = _candle(t0 + timedelta(minutes=10))
    result = gate.evaluate_candles([future], freshness_class=FreshnessClass.ANALYSIS_INTERVAL, as_of=t0)
    assert not result.valid
    assert any(c.check.value == "TIMESTAMP_VALIDITY" and not c.passed for c in result.checks)


def test_duplicate_candles_detected() -> None:
    gate = DataQualityGate()
    t0 = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
    candles = [_candle(t0), _candle(t0)]
    result = gate.evaluate_candles(candles, freshness_class=FreshnessClass.ANALYSIS_INTERVAL, as_of=t0)
    assert not result.valid
    assert any(c.check.value == "DUPLICATE_RECORDS" and not c.passed for c in result.checks)


def test_impossible_price_jump_detected() -> None:
    gate = DataQualityGate()
    t0 = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
    spiked = _candle(t0, close=Decimal("1000"))
    result = gate.evaluate_candles(
        [spiked], freshness_class=FreshnessClass.ANALYSIS_INTERVAL, as_of=t0, reference_price=Decimal("100")
    )
    assert not result.valid
    assert any(c.check.value == "IMPOSSIBLE_PRICES" and not c.passed for c in result.checks)


def test_provider_down_fails_gate() -> None:
    gate = DataQualityGate()
    t0 = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
    health = ProviderHealth(provider="dhan", status=HealthStatus.DOWN, checked_at=t0)
    result = gate.evaluate_candles(
        [_candle(t0)], freshness_class=FreshnessClass.ANALYSIS_INTERVAL, as_of=t0, provider_health=health
    )
    assert not result.valid
    assert any(c.check.value == "PROVIDER_STATUS" and not c.passed for c in result.checks)


def test_option_chain_completeness_flags_missing_leg() -> None:
    gate = DataQualityGate()
    t0 = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
    fresh = _freshness(t0)
    ce_only = OptionQuote(
        provider="mock", freshness=fresh, underlying="NIFTY", expiry=date(2026, 9, 25), strike=Decimal("25000"),
        right=OptionRight.CE,
    )
    snapshot = OptionChainSnapshot(
        provider="mock", freshness=fresh, underlying="NIFTY", expiry=date(2026, 9, 25), legs=[ce_only]
    )
    result = gate.evaluate_option_chain(snapshot, freshness_class=FreshnessClass.SHORT_INTERVAL, as_of=t0)
    assert not result.valid
    assert any(c.check.value == "OPTION_CHAIN_COMPLETENESS" and not c.passed for c in result.checks)


def test_option_chain_complete_when_every_strike_has_both_legs() -> None:
    gate = DataQualityGate()
    t0 = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
    fresh = _freshness(t0)
    ce = OptionQuote(
        provider="mock", freshness=fresh, underlying="NIFTY", expiry=date(2026, 9, 25), strike=Decimal("25000"),
        right=OptionRight.CE,
    )
    pe = OptionQuote(
        provider="mock", freshness=fresh, underlying="NIFTY", expiry=date(2026, 9, 25), strike=Decimal("25000"),
        right=OptionRight.PE,
    )
    snapshot = OptionChainSnapshot(
        provider="mock", freshness=fresh, underlying="NIFTY", expiry=date(2026, 9, 25), legs=[ce, pe]
    )
    result = gate.evaluate_option_chain(snapshot, freshness_class=FreshnessClass.SHORT_INTERVAL, as_of=t0)
    assert result.valid, result.failed_checks


def test_quote_missing_fields_flagged() -> None:
    gate = DataQualityGate()
    t0 = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
    quote = Quote(provider="mock", freshness=_freshness(t0), instrument_id="INST1", last_price=Decimal("100"))
    result = gate.evaluate_quote(quote, freshness_class=FreshnessClass.REAL_TIME, as_of=t0)
    assert not result.valid
    assert any(c.check.value == "MISSING_FIELDS" and not c.passed for c in result.checks)


def test_quote_with_all_fields_passes_missing_fields_check() -> None:
    gate = DataQualityGate()
    t0 = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
    quote = Quote(
        provider="mock", freshness=_freshness(t0), instrument_id="INST1", last_price=Decimal("100"),
        previous_close=Decimal("99"), volume=1000,
    )
    result = gate.evaluate_quote(quote, freshness_class=FreshnessClass.REAL_TIME, as_of=t0)
    assert result.valid, result.failed_checks
