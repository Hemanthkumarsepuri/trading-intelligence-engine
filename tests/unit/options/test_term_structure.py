from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from app.data.normalization.base import DefaultNormalizer
from app.data.providers.base import RawOptionChain, RawOptionLeg
from app.domain.market.models import OptionChainSnapshot, OptionRight
from app.domain.options.term_structure import (
    ExpirySummary,
    TermStructure,
    build_expiry_selection_note,
    build_expiry_summary,
)

UNDERLYING = "NIFTY"
AS_OF = datetime(2026, 8, 29, 10, 0, tzinfo=UTC)
_MAX_AGE = timedelta(minutes=5)
_MAX_SPREAD = Decimal("0.20")


def _leg(*, right: OptionRight, iv: float | None) -> RawOptionLeg:
    return RawOptionLeg(
        right=right, last_price=100.0, bid_price=99.0, ask_price=101.0, volume=1000, open_interest=10000, implied_volatility=iv
    )


def _snapshot(*, expiry: date, spot: float = 24800.0, iv: float | None = 14.0) -> OptionChainSnapshot:
    strikes = {24800.0: [_leg(right=OptionRight.CE, iv=iv), _leg(right=OptionRight.PE, iv=iv)]}
    raw = RawOptionChain(underlying=UNDERLYING, expiry=expiry, underlying_last_price=spot, strikes=strikes)
    return DefaultNormalizer(provider_name="test").normalize_option_chain(raw, received_at=AS_OF)


def _summary(*, expiry: date, is_weekly: bool, iv: float | None = 14.0) -> ExpirySummary:
    return build_expiry_summary(
        _snapshot(expiry=expiry, iv=iv), is_weekly=is_weekly, as_of=AS_OF,
        max_chain_age_for_quality_check=_MAX_AGE, max_spread_fraction_for_quality_check=_MAX_SPREAD,
    )


def test_build_expiry_summary_computes_days_remaining_and_atm() -> None:
    summary = _summary(expiry=date(2026, 9, 5), is_weekly=True)
    assert summary.days_remaining == 7
    assert summary.atm_strike == Decimal("24800.0")
    assert summary.is_weekly is True


def test_term_structure_requires_at_least_one_expiry() -> None:
    with pytest.raises(ValueError, match="at least one"):
        TermStructure(expiries=[])


def test_term_structure_nearest_is_the_first_entry() -> None:
    ts = TermStructure(expiries=[_summary(expiry=date(2026, 9, 5), is_weekly=True), _summary(expiry=date(2026, 9, 26), is_weekly=False)])
    assert ts.nearest.expiry == date(2026, 9, 5)


def test_iv_slope_none_with_fewer_than_two_known_iv() -> None:
    ts = TermStructure(expiries=[_summary(expiry=date(2026, 9, 5), is_weekly=True, iv=None)])
    assert ts.iv_slope() is None


def test_iv_slope_positive_when_near_dated_richer() -> None:
    ts = TermStructure(
        expiries=[
            _summary(expiry=date(2026, 9, 5), is_weekly=True, iv=20.0),
            _summary(expiry=date(2026, 9, 26), is_weekly=False, iv=15.0),
        ]
    )
    assert ts.iv_slope() == Decimal("5.0")


def test_iv_slope_negative_when_far_dated_richer() -> None:
    ts = TermStructure(
        expiries=[
            _summary(expiry=date(2026, 9, 5), is_weekly=True, iv=12.0),
            _summary(expiry=date(2026, 9, 26), is_weekly=False, iv=18.0),
        ]
    )
    assert ts.iv_slope() == Decimal("-6.0")


def test_expiry_selection_note_flags_missing_iv() -> None:
    ts = TermStructure(expiries=[_summary(expiry=date(2026, 9, 5), is_weekly=True, iv=None)])
    note = build_expiry_selection_note(ts)
    assert any("ATM IV unavailable" in f for f in note.flags)


def test_expiry_selection_note_never_picks_a_single_best_expiry() -> None:
    # The note is a flat list of observations, never a ranked "recommended" field.
    ts = TermStructure(expiries=[_summary(expiry=date(2026, 9, 5), is_weekly=True), _summary(expiry=date(2026, 9, 26), is_weekly=False)])
    note = build_expiry_selection_note(ts)
    assert not hasattr(note, "recommended_expiry")
