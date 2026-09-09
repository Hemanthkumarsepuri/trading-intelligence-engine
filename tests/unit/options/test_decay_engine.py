from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.market.models import OptionRight
from app.domain.options.decay_engine import AttributionConfidence, attribute_decay
from app.domain.options.price_oi_interpretation import classify_price_oi
from app.domain.options.temporal_evidence import EvidenceQuality, TemporalObservation

T0 = datetime(2026, 8, 29, 10, 0, tzinfo=UTC)
_ATTRIBUTED = Decimal("0.25")
_UNRELIABLE = Decimal("0.75")


def _observation(
    *, price_t0: Decimal | None, price_t1: Decimal | None, iv_t0: Decimal | None = None, iv_t1: Decimal | None = None,
    interval: timedelta = timedelta(days=1),
) -> TemporalObservation:
    iv_change = (iv_t1 - iv_t0) if iv_t0 is not None and iv_t1 is not None else None
    price_oi = classify_price_oi(
        current_price=price_t1, previous_price=price_t0, current_oi=100, previous_oi=100,
        price_flat_threshold=Decimal("0.01"),
    )
    return TemporalObservation(
        strike=Decimal("100"), right=OptionRight.CE, t0=T0, t1=T0 + interval, interval=interval,
        price_t0=price_t0, price_t1=price_t1, price_change_pct=None,
        oi_t0=100, oi_t1=100, oi_change_pct=Decimal(0),
        volume_t0=100, volume_t1=100, volume_change=0,
        iv_t0=iv_t0, iv_t1=iv_t1, iv_change=iv_change,
        price_oi=price_oi, quality=EvidenceQuality.HIGH, quality_reasons=[],
    )


def test_insufficient_data_when_option_price_missing() -> None:
    obs = _observation(price_t0=None, price_t1=Decimal("10"))
    result = attribute_decay(
        obs, underlying_price_t0=Decimal("100"), underlying_price_t1=Decimal("101"),
        delta_t0=Decimal("0.5"), gamma_t0=Decimal("0.01"), vega_t0=Decimal("0.1"), theta_t0=Decimal("-1"),
        attributed_max_residual_fraction=_ATTRIBUTED, unreliable_min_residual_fraction=_UNRELIABLE,
    )
    assert result.confidence == AttributionConfidence.INSUFFICIENT_DATA


def test_insufficient_data_when_a_greek_is_missing() -> None:
    obs = _observation(price_t0=Decimal("10"), price_t1=Decimal("12"))
    result = attribute_decay(
        obs, underlying_price_t0=Decimal("100"), underlying_price_t1=Decimal("102"),
        delta_t0=None, gamma_t0=Decimal("0.01"), vega_t0=Decimal("0.1"), theta_t0=Decimal("-1"),
        attributed_max_residual_fraction=_ATTRIBUTED, unreliable_min_residual_fraction=_UNRELIABLE,
    )
    assert result.confidence == AttributionConfidence.INSUFFICIENT_DATA
    assert "delta" in result.detail


def test_fully_attributed_when_residual_is_small() -> None:
    # Constructed so delta+gamma+vega+theta sum ~= observed change.
    obs = _observation(price_t0=Decimal("10"), price_t1=Decimal("15"), iv_t0=Decimal("20"), iv_t1=Decimal("20"), interval=timedelta(days=1))
    # delta_effect = 0.5 * 10 = 5.0 ; gamma/vega/theta = 0 -> matches observed change of 5 exactly
    result = attribute_decay(
        obs, underlying_price_t0=Decimal("100"), underlying_price_t1=Decimal("110"),
        delta_t0=Decimal("0.5"), gamma_t0=Decimal("0"), vega_t0=Decimal("0"), theta_t0=Decimal("0"),
        attributed_max_residual_fraction=_ATTRIBUTED, unreliable_min_residual_fraction=_UNRELIABLE,
    )
    assert result.observed_price_change == Decimal("5")
    assert result.delta_effect == Decimal("5.0")
    assert result.residual == Decimal("0.0")
    assert result.confidence == AttributionConfidence.ATTRIBUTED


def test_unreliable_when_residual_dominates() -> None:
    obs = _observation(price_t0=Decimal("10"), price_t1=Decimal("50"), iv_t0=Decimal("20"), iv_t1=Decimal("20"), interval=timedelta(days=1))
    # Greeks explain almost none of a +40 move -> huge residual
    result = attribute_decay(
        obs, underlying_price_t0=Decimal("100"), underlying_price_t1=Decimal("100.10"),
        delta_t0=Decimal("0.1"), gamma_t0=Decimal("0"), vega_t0=Decimal("0"), theta_t0=Decimal("0"),
        attributed_max_residual_fraction=_ATTRIBUTED, unreliable_min_residual_fraction=_UNRELIABLE,
    )
    assert result.confidence == AttributionConfidence.UNRELIABLE


def test_theta_effect_scales_with_real_elapsed_days() -> None:
    obs = _observation(price_t0=Decimal("10"), price_t1=Decimal("9"), interval=timedelta(hours=12))
    result = attribute_decay(
        obs, underlying_price_t0=Decimal("100"), underlying_price_t1=Decimal("100"),
        delta_t0=Decimal("0"), gamma_t0=Decimal("0"), vega_t0=Decimal("0"), theta_t0=Decimal("-2"),
        attributed_max_residual_fraction=_ATTRIBUTED, unreliable_min_residual_fraction=_UNRELIABLE,
    )
    assert result.theta_effect == Decimal("-1.0")  # -2/day * 0.5 day


def test_zero_observed_change_is_attributed_trivially() -> None:
    obs = _observation(price_t0=Decimal("10"), price_t1=Decimal("10"), iv_t0=Decimal("20"), iv_t1=Decimal("20"))
    result = attribute_decay(
        obs, underlying_price_t0=Decimal("100"), underlying_price_t1=Decimal("100"),
        delta_t0=Decimal("0.5"), gamma_t0=Decimal("0.01"), vega_t0=Decimal("0.1"), theta_t0=Decimal("-1"),
        attributed_max_residual_fraction=_ATTRIBUTED, unreliable_min_residual_fraction=_UNRELIABLE,
    )
    assert result.confidence == AttributionConfidence.ATTRIBUTED


def test_never_claims_exact_attribution_language() -> None:
    obs = _observation(price_t0=Decimal("10"), price_t1=Decimal("15"), iv_t0=Decimal("20"), iv_t1=Decimal("20"))
    result = attribute_decay(
        obs, underlying_price_t0=Decimal("100"), underlying_price_t1=Decimal("110"),
        delta_t0=Decimal("0.5"), gamma_t0=Decimal("0"), vega_t0=Decimal("0"), theta_t0=Decimal("0"),
        attributed_max_residual_fraction=_ATTRIBUTED, unreliable_min_residual_fraction=_UNRELIABLE,
    )
    assert "exact" not in result.detail.lower()
