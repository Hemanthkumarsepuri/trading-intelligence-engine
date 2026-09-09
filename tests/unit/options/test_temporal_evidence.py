from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from app.data.normalization.base import DefaultNormalizer
from app.data.providers.base import RawOptionChain, RawOptionLeg
from app.domain.market.models import OptionChainSnapshot, OptionRight
from app.domain.options.price_oi_interpretation import PriceOIQuadrant
from app.domain.options.temporal_evidence import (
    EvidenceQuality,
    TemporalObservation,
    compute_temporal_observation,
    strikes_from_intersection,
)

UNDERLYING = "NIFTY"
EXPIRY = date(2026, 9, 24)
T0 = datetime(2026, 8, 29, 10, 0, tzinfo=UTC)

_FLAT = Decimal("0.5")
_MIN_OI = 10_000
_MIN_VOL = 1_000
_MAX_INTERVAL = timedelta(minutes=20)


def _leg(*, right: OptionRight, price: float, oi: int, volume: int, iv: float | None = 14.0) -> RawOptionLeg:
    return RawOptionLeg(right=right, last_price=price, open_interest=oi, volume=volume, implied_volatility=iv)


def _snapshot(strikes: dict[float, list[RawOptionLeg]], *, at: datetime) -> OptionChainSnapshot:
    raw = RawOptionChain(underlying=UNDERLYING, expiry=EXPIRY, underlying_last_price=24800.0, strikes=strikes)
    return DefaultNormalizer(provider_name="test").normalize_option_chain(raw, received_at=at)


def _compute(
    earlier: OptionChainSnapshot, later: OptionChainSnapshot, *, strike: Decimal = Decimal("24800.0")
) -> TemporalObservation:
    return compute_temporal_observation(
        earlier=earlier, later=later, strike=strike, right=OptionRight.CE, price_flat_threshold=_FLAT,
        min_oi_for_high_quality=_MIN_OI, min_volume_for_high_quality=_MIN_VOL, max_reasonable_interval=_MAX_INTERVAL,
    )


def test_raises_on_non_positive_interval() -> None:
    snap = _snapshot({24800.0: [_leg(right=OptionRight.CE, price=100, oi=1000, volume=500)]}, at=T0)
    with pytest.raises(ValueError, match="strictly after"):
        _compute(snap, snap)


def test_computes_real_price_and_oi_percentage_change() -> None:
    earlier = _snapshot({24800.0: [_leg(right=OptionRight.CE, price=100, oi=10000, volume=1000)]}, at=T0)
    later = _snapshot({24800.0: [_leg(right=OptionRight.CE, price=110, oi=12000, volume=1500)]}, at=T0 + timedelta(minutes=5))

    obs = _compute(earlier, later)
    assert obs.price_change_pct == Decimal(10)  # (110-100)/100 * 100
    assert obs.oi_change_pct == Decimal(20)  # (12000-10000)/10000 * 100
    assert obs.volume_change == 500
    assert obs.interval == timedelta(minutes=5)
    assert obs.price_oi.quadrant == PriceOIQuadrant.PRICE_UP_OI_UP


def test_high_quality_when_oi_and_volume_clear_the_floor() -> None:
    earlier = _snapshot({24800.0: [_leg(right=OptionRight.CE, price=100, oi=10000, volume=1000)]}, at=T0)
    later = _snapshot({24800.0: [_leg(right=OptionRight.CE, price=110, oi=15000, volume=3000)]}, at=T0 + timedelta(minutes=5))

    obs = _compute(earlier, later)
    assert obs.quality == EvidenceQuality.HIGH


def test_low_quality_when_below_floor() -> None:
    earlier = _snapshot({24800.0: [_leg(right=OptionRight.CE, price=100, oi=100, volume=10)]}, at=T0)
    later = _snapshot({24800.0: [_leg(right=OptionRight.CE, price=101, oi=105, volume=15)]}, at=T0 + timedelta(minutes=5))

    obs = _compute(earlier, later)
    assert obs.quality == EvidenceQuality.LOW


def test_low_quality_when_interval_exceeds_reasonable_window() -> None:
    earlier = _snapshot({24800.0: [_leg(right=OptionRight.CE, price=100, oi=15000, volume=3000)]}, at=T0)
    later = _snapshot({24800.0: [_leg(right=OptionRight.CE, price=110, oi=20000, volume=5000)]}, at=T0 + timedelta(hours=2))

    obs = _compute(earlier, later)
    assert obs.quality == EvidenceQuality.LOW
    assert "exceeds" in obs.quality_reasons[0]


def test_insufficient_quality_when_leg_missing_in_one_snapshot() -> None:
    earlier = _snapshot({}, at=T0)
    later = _snapshot({24800.0: [_leg(right=OptionRight.CE, price=110, oi=20000, volume=5000)]}, at=T0 + timedelta(minutes=5))

    obs = _compute(earlier, later)
    assert obs.quality == EvidenceQuality.INSUFFICIENT
    assert obs.price_oi.quadrant == PriceOIQuadrant.INSUFFICIENT_DATA


def test_iv_change_computed_when_both_present() -> None:
    earlier = _snapshot({24800.0: [_leg(right=OptionRight.CE, price=100, oi=10000, volume=1000, iv=14.0)]}, at=T0)
    later = _snapshot({24800.0: [_leg(right=OptionRight.CE, price=100, oi=10000, volume=1000, iv=16.5)]}, at=T0 + timedelta(minutes=5))

    obs = _compute(earlier, later)
    assert obs.iv_change == Decimal("2.5")


def test_strikes_from_intersection_only_includes_common_strikes() -> None:
    earlier = _snapshot(
        {24700.0: [_leg(right=OptionRight.CE, price=1, oi=1, volume=1)], 24800.0: [_leg(right=OptionRight.CE, price=1, oi=1, volume=1)]},
        at=T0,
    )
    later = _snapshot(
        {24800.0: [_leg(right=OptionRight.CE, price=1, oi=1, volume=1)], 24900.0: [_leg(right=OptionRight.CE, price=1, oi=1, volume=1)]},
        at=T0 + timedelta(minutes=1),
    )
    assert strikes_from_intersection(earlier, later) == [(Decimal("24800.0"), OptionRight.CE)]
