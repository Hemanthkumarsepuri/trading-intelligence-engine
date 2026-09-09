from __future__ import annotations

from decimal import Decimal

from app.domain.options.price_oi_interpretation import (
    BasisChangeDirection,
    PriceOIQuadrant,
    classify_basis_change,
    classify_price_oi,
)

_THRESHOLD = Decimal("0.5")
_BASIS_THRESHOLD = Decimal("0.2")


def test_insufficient_data_when_any_input_missing() -> None:
    obs = classify_price_oi(
        current_price=Decimal("100"), previous_price=None, current_oi=1000, previous_oi=900,
        price_flat_threshold=_THRESHOLD,
    )
    assert obs.quadrant == PriceOIQuadrant.INSUFFICIENT_DATA
    assert obs.price_change is None


def test_price_up_oi_up() -> None:
    obs = classify_price_oi(
        current_price=Decimal("105"), previous_price=Decimal("100"), current_oi=1200, previous_oi=1000,
        price_flat_threshold=_THRESHOLD,
    )
    assert obs.quadrant == PriceOIQuadrant.PRICE_UP_OI_UP
    assert obs.price_change == Decimal("5")
    assert obs.oi_change == 200
    assert "unconfirmed" in obs.conventional_reading


def test_price_up_oi_down() -> None:
    obs = classify_price_oi(
        current_price=Decimal("105"), previous_price=Decimal("100"), current_oi=800, previous_oi=1000,
        price_flat_threshold=_THRESHOLD,
    )
    assert obs.quadrant == PriceOIQuadrant.PRICE_UP_OI_DOWN


def test_price_down_oi_up() -> None:
    obs = classify_price_oi(
        current_price=Decimal("95"), previous_price=Decimal("100"), current_oi=1200, previous_oi=1000,
        price_flat_threshold=_THRESHOLD,
    )
    assert obs.quadrant == PriceOIQuadrant.PRICE_DOWN_OI_UP


def test_price_down_oi_down() -> None:
    obs = classify_price_oi(
        current_price=Decimal("95"), previous_price=Decimal("100"), current_oi=800, previous_oi=1000,
        price_flat_threshold=_THRESHOLD,
    )
    assert obs.quadrant == PriceOIQuadrant.PRICE_DOWN_OI_DOWN


def test_price_flat_when_change_below_threshold() -> None:
    obs = classify_price_oi(
        current_price=Decimal("100.1"), previous_price=Decimal("100"), current_oi=1200, previous_oi=1000,
        price_flat_threshold=_THRESHOLD,
    )
    assert obs.quadrant == PriceOIQuadrant.PRICE_FLAT


def test_never_returns_buildup_vocabulary_directly() -> None:
    # The raw enum value itself must never spell out "buildup"/"covering"/
    # "unwinding" -- those are the disclaimed conventional_reading text
    # only, never the fact-level classification.
    for q in PriceOIQuadrant:
        assert "buildup" not in q.value
        assert "covering" not in q.value
        assert "unwinding" not in q.value


# -- Final Hardening Pass, Phase 6: futures basis change -------------------


def test_basis_change_insufficient_data_when_no_prior_basis() -> None:
    obs = classify_basis_change(current_basis_pct=Decimal("0.5"), previous_basis_pct=None, meaningful_change_pct=_BASIS_THRESHOLD)
    assert obs.direction == BasisChangeDirection.INSUFFICIENT_DATA
    assert obs.change_pct_points is None


def test_basis_change_stable_when_below_threshold() -> None:
    obs = classify_basis_change(current_basis_pct=Decimal("0.55"), previous_basis_pct=Decimal("0.5"), meaningful_change_pct=_BASIS_THRESHOLD)
    assert obs.direction == BasisChangeDirection.STABLE


def test_basis_change_widening_premium() -> None:
    obs = classify_basis_change(current_basis_pct=Decimal("1.2"), previous_basis_pct=Decimal("0.5"), meaningful_change_pct=_BASIS_THRESHOLD)
    assert obs.direction == BasisChangeDirection.WIDENING_PREMIUM
    assert obs.change_pct_points == Decimal("0.7")


def test_basis_change_narrowing_premium() -> None:
    obs = classify_basis_change(current_basis_pct=Decimal("0.3"), previous_basis_pct=Decimal("1.0"), meaningful_change_pct=_BASIS_THRESHOLD)
    assert obs.direction == BasisChangeDirection.NARROWING_PREMIUM


def test_basis_change_widening_discount() -> None:
    obs = classify_basis_change(current_basis_pct=Decimal("-1.0"), previous_basis_pct=Decimal("-0.3"), meaningful_change_pct=_BASIS_THRESHOLD)
    assert obs.direction == BasisChangeDirection.WIDENING_DISCOUNT


def test_basis_change_narrowing_discount() -> None:
    obs = classify_basis_change(current_basis_pct=Decimal("-0.3"), previous_basis_pct=Decimal("-1.0"), meaningful_change_pct=_BASIS_THRESHOLD)
    assert obs.direction == BasisChangeDirection.NARROWING_DISCOUNT


def test_basis_change_widening_premium_never_asserts_certainty() -> None:
    obs = classify_basis_change(current_basis_pct=Decimal("1.2"), previous_basis_pct=Decimal("0.5"), meaningful_change_pct=_BASIS_THRESHOLD)
    assert "unconfirmed" in obs.interpretation
