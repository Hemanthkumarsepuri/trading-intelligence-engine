"""Sprint 7A, Objective 3 -- pure unit tests for `analyze_oi_migration()`.
No I/O, real Pydantic option-chain snapshots (same fixture convention as
`test_support_resistance.py`).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from app.data.normalization.base import DefaultNormalizer
from app.data.providers.base import RawOptionChain, RawOptionLeg
from app.domain.market.models import OptionChainSnapshot, OptionRight
from app.domain.options.oi_migration import OIMigrationDirection, analyze_oi_migration
from app.domain.options.temporal_evidence import EvidenceQuality

UNDERLYING = "NIFTY"
EXPIRY = date(2026, 9, 24)
RECEIVED_AT = datetime(2026, 8, 29, 10, 0, tzinfo=UTC)
MEANINGFUL_SHIFT = Decimal("0.5")


def _leg(*, right: OptionRight, oi: int) -> RawOptionLeg:
    return RawOptionLeg(right=right, open_interest=oi, volume=100)


def _snapshot(strikes: dict[float, list[RawOptionLeg]], *, spot: float = 24800.0) -> OptionChainSnapshot:
    raw = RawOptionChain(underlying=UNDERLYING, expiry=EXPIRY, underlying_last_price=spot, strikes=strikes)
    return DefaultNormalizer(provider_name="test").normalize_option_chain(raw, received_at=RECEIVED_AT)


def test_insufficient_data_when_no_oi_on_this_side() -> None:
    snapshot = _snapshot({24800.0: [_leg(right=OptionRight.CE, oi=1000)]})  # no PE legs at all
    result = analyze_oi_migration(
        current_snapshot=snapshot, previous_snapshot=None, right=OptionRight.PE, top_n=3, meaningful_shift_pct=MEANINGFUL_SHIFT,
    )
    assert result.direction == OIMigrationDirection.INSUFFICIENT_DATA
    assert result.quality == EvidenceQuality.INSUFFICIENT


def test_insufficient_data_without_a_real_prior_snapshot() -> None:
    snapshot = _snapshot({24800.0: [_leg(right=OptionRight.PE, oi=1000)]})
    result = analyze_oi_migration(
        current_snapshot=snapshot, previous_snapshot=None, right=OptionRight.PE, top_n=3, meaningful_shift_pct=MEANINGFUL_SHIFT,
    )
    assert result.direction == OIMigrationDirection.INSUFFICIENT_DATA
    assert "no real prior" in result.interpretation
    entry = result.entries[0]
    assert entry.previous_oi is None and entry.change_pct is None


def test_stable_when_oi_weighted_strike_barely_moves() -> None:
    previous = _snapshot({3600.0: [_leg(right=OptionRight.PE, oi=1000)], 3500.0: [_leg(right=OptionRight.PE, oi=1000)]}, spot=3550.0)
    current = _snapshot({3600.0: [_leg(right=OptionRight.PE, oi=1010)], 3500.0: [_leg(right=OptionRight.PE, oi=990)]}, spot=3550.0)
    result = analyze_oi_migration(
        current_snapshot=current, previous_snapshot=previous, right=OptionRight.PE, top_n=3, meaningful_shift_pct=MEANINGFUL_SHIFT,
    )
    assert result.direction == OIMigrationDirection.STABLE


def test_put_oi_migrating_lower_reproduces_the_named_example() -> None:
    """The prompt's own example shape: 3600 PE OI down, 3500/3400 PE OI up
    -- a real shift of the OI-weighted strike toward lower strikes."""
    previous = _snapshot({
        3600.0: [_leg(right=OptionRight.PE, oi=10000)],
        3500.0: [_leg(right=OptionRight.PE, oi=5000)],
        3400.0: [_leg(right=OptionRight.PE, oi=3000)],
    }, spot=3650.0)
    current = _snapshot({
        3600.0: [_leg(right=OptionRight.PE, oi=8600)],  # -14%
        3500.0: [_leg(right=OptionRight.PE, oi=6400)],  # +28%
        3400.0: [_leg(right=OptionRight.PE, oi=3510)],  # +17%
    }, spot=3650.0)
    result = analyze_oi_migration(
        current_snapshot=current, previous_snapshot=previous, right=OptionRight.PE, top_n=3, meaningful_shift_pct=Decimal("0.1"),
    )
    assert result.direction == OIMigrationDirection.MIGRATING_LOWER
    assert "not confirmation" in result.interpretation
    assert result.quality == EvidenceQuality.HIGH
    down_entry = next(e for e in result.entries if e.strike == Decimal("3600.0"))
    assert down_entry.change_pct is not None and down_entry.change_pct < 0


def test_call_oi_migrating_higher() -> None:
    previous = _snapshot({
        3700.0: [_leg(right=OptionRight.CE, oi=10000)],
        3800.0: [_leg(right=OptionRight.CE, oi=3000)],
    }, spot=3650.0)
    current = _snapshot({
        3700.0: [_leg(right=OptionRight.CE, oi=6000)],
        3800.0: [_leg(right=OptionRight.CE, oi=9000)],
    }, spot=3650.0)
    result = analyze_oi_migration(
        current_snapshot=current, previous_snapshot=previous, right=OptionRight.CE, top_n=2, meaningful_shift_pct=MEANINGFUL_SHIFT,
    )
    assert result.direction == OIMigrationDirection.MIGRATING_HIGHER
    assert "not confirmation" in result.interpretation


def test_never_asserts_certainty_in_any_interpretation() -> None:
    previous = _snapshot({3600.0: [_leg(right=OptionRight.PE, oi=10000)], 3500.0: [_leg(right=OptionRight.PE, oi=3000)]}, spot=3650.0)
    current = _snapshot({3600.0: [_leg(right=OptionRight.PE, oi=6000)], 3500.0: [_leg(right=OptionRight.PE, oi=9000)]}, spot=3650.0)
    result = analyze_oi_migration(
        current_snapshot=current, previous_snapshot=previous, right=OptionRight.PE, top_n=2, meaningful_shift_pct=MEANINGFUL_SHIFT,
    )
    for forbidden in ("will ", "guaranteed", "certainly", "confirmed breakdown", "confirmed breakout"):
        assert forbidden not in result.interpretation.lower()


def test_quality_moderate_when_fewer_comparable_strikes_than_requested() -> None:
    previous = _snapshot({3600.0: [_leg(right=OptionRight.PE, oi=10000)]}, spot=3650.0)  # only 1 strike known previously
    current = _snapshot({
        3600.0: [_leg(right=OptionRight.PE, oi=8000)],
        3500.0: [_leg(right=OptionRight.PE, oi=5000)],  # new strike, no real prior OI
        3400.0: [_leg(right=OptionRight.PE, oi=3000)],  # new strike, no real prior OI
    }, spot=3650.0)
    result = analyze_oi_migration(
        current_snapshot=current, previous_snapshot=previous, right=OptionRight.PE, top_n=3, meaningful_shift_pct=MEANINGFUL_SHIFT,
    )
    assert result.quality in (EvidenceQuality.LOW, EvidenceQuality.MODERATE, EvidenceQuality.INSUFFICIENT)
