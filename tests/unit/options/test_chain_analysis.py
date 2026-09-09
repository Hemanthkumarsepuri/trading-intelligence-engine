from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from app.data.normalization.base import DefaultNormalizer
from app.data.providers.base import RawOptionChain, RawOptionLeg
from app.domain.market.models import OptionChainSnapshot, OptionRight
from app.domain.options.chain_analysis import (
    atm_strike,
    build_chain_rows,
    chain_totals,
    classify_moneyness,
    distinct_strikes,
    strike_window,
    strike_window_around,
    top_oi_strikes,
)
from app.domain.options.models import Moneyness

UNDERLYING = "NSE_INDEX|Nifty 50"
EXPIRY = date(2026, 9, 24)
RECEIVED_AT = datetime(2026, 8, 29, 10, 0, tzinfo=UTC)


def _leg(*, right: OptionRight, oi: int | None, volume: int | None = 100, delta: float | None = 0.5) -> RawOptionLeg:
    return RawOptionLeg(
        right=right, last_price=10.0, bid_price=9.5, ask_price=10.5, volume=volume,
        open_interest=oi, previous_open_interest=None, implied_volatility=15.0, delta=delta,
    )


def _snapshot(strikes: dict[float, list[RawOptionLeg]], *, spot: float = 24800.0) -> OptionChainSnapshot:
    raw = RawOptionChain(underlying=UNDERLYING, expiry=EXPIRY, underlying_last_price=spot, strikes=strikes)
    normalizer = DefaultNormalizer(provider_name="test")
    return normalizer.normalize_option_chain(raw, received_at=RECEIVED_AT)


_STANDARD = {
    24700.0: [_leg(right=OptionRight.CE, oi=1000), _leg(right=OptionRight.PE, oi=5000)],
    24800.0: [_leg(right=OptionRight.CE, oi=8000), _leg(right=OptionRight.PE, oi=3000)],
    24900.0: [_leg(right=OptionRight.CE, oi=6000), _leg(right=OptionRight.PE, oi=1200)],
    25000.0: [_leg(right=OptionRight.CE, oi=9000), _leg(right=OptionRight.PE, oi=400)],
}


# -- atm_strike / distinct_strikes / strike_window --------------------------


def test_distinct_strikes_sorted_ascending() -> None:
    snapshot = _snapshot(_STANDARD)
    assert distinct_strikes(snapshot) == [Decimal("24700.0"), Decimal("24800.0"), Decimal("24900.0"), Decimal("25000.0")]


def test_atm_strike_is_the_closest_real_strike_to_spot() -> None:
    snapshot = _snapshot(_STANDARD, spot=24810.0)
    assert atm_strike(snapshot) == Decimal("24800.0")


def test_atm_strike_none_when_no_spot_price() -> None:
    raw = RawOptionChain(underlying=UNDERLYING, expiry=EXPIRY, underlying_last_price=None, strikes=_STANDARD)
    snapshot = DefaultNormalizer(provider_name="test").normalize_option_chain(raw, received_at=RECEIVED_AT)
    assert atm_strike(snapshot) is None


def test_atm_strike_none_for_empty_chain() -> None:
    snapshot = _snapshot({})
    assert atm_strike(snapshot) is None


def test_strike_window_returns_strikes_within_n_positions_of_atm() -> None:
    snapshot = _snapshot(_STANDARD, spot=24800.0)  # ATM = 24800
    window = strike_window(snapshot, strikes_each_side=1)
    assert window == [Decimal("24700.0"), Decimal("24800.0"), Decimal("24900.0")]


def test_strike_window_clamps_at_the_edges() -> None:
    snapshot = _snapshot(_STANDARD, spot=24700.0)  # ATM = 24700, first strike
    window = strike_window(snapshot, strikes_each_side=5)
    assert window == distinct_strikes(snapshot)  # clamped, not an out-of-range crash


def test_strike_window_empty_when_atm_cannot_be_determined() -> None:
    snapshot = _snapshot({})
    assert strike_window(snapshot, strikes_each_side=2) == []


# -- classify_moneyness ------------------------------------------------------


def test_classify_moneyness_call_itm_atm_otm() -> None:
    atm = Decimal("24800")
    spot = Decimal("24800")
    assert classify_moneyness(strike=Decimal("24700"), right=OptionRight.CE, atm=atm, spot=spot) == Moneyness.ITM
    assert classify_moneyness(strike=Decimal("24800"), right=OptionRight.CE, atm=atm, spot=spot) == Moneyness.ATM
    assert classify_moneyness(strike=Decimal("24900"), right=OptionRight.CE, atm=atm, spot=spot) == Moneyness.OTM


def test_classify_moneyness_put_itm_atm_otm() -> None:
    atm = Decimal("24800")
    spot = Decimal("24800")
    assert classify_moneyness(strike=Decimal("24900"), right=OptionRight.PE, atm=atm, spot=spot) == Moneyness.ITM
    assert classify_moneyness(strike=Decimal("24800"), right=OptionRight.PE, atm=atm, spot=spot) == Moneyness.ATM
    assert classify_moneyness(strike=Decimal("24700"), right=OptionRight.PE, atm=atm, spot=spot) == Moneyness.OTM


# -- chain_totals -------------------------------------------------------------


def test_chain_totals_sums_oi_and_volume_by_side() -> None:
    snapshot = _snapshot(_STANDARD)
    totals = chain_totals(snapshot)
    assert totals.total_call_oi == 1000 + 8000 + 6000 + 9000
    assert totals.total_put_oi == 5000 + 3000 + 1200 + 400
    assert totals.legs_with_missing_oi == 0


def test_chain_totals_put_call_ratio_computed_from_totals() -> None:
    snapshot = _snapshot(_STANDARD)
    totals = chain_totals(snapshot)
    assert totals.put_call_ratio_oi == Decimal(9600) / Decimal(24000)


def test_chain_totals_ratio_is_none_when_call_oi_is_zero_not_a_zero_division() -> None:
    strikes = {24700.0: [_leg(right=OptionRight.CE, oi=0), _leg(right=OptionRight.PE, oi=500)]}
    snapshot = _snapshot(strikes)
    totals = chain_totals(snapshot)
    assert totals.put_call_ratio_oi is None  # call_oi == 0 -> ratio undefined, not fabricated as inf/0


def test_chain_totals_excludes_missing_oi_from_sum_and_counts_it() -> None:
    strikes = {24700.0: [_leg(right=OptionRight.CE, oi=None), _leg(right=OptionRight.PE, oi=500)]}
    snapshot = _snapshot(strikes)
    totals = chain_totals(snapshot)
    assert totals.total_call_oi == 0
    assert totals.legs_with_missing_oi == 1
    assert totals.legs_with_known_oi == 1


# -- top_oi_strikes -----------------------------------------------------------


def test_top_oi_strikes_ranks_descending_and_respects_limit() -> None:
    snapshot = _snapshot(_STANDARD)
    top = top_oi_strikes(snapshot, right=OptionRight.CE, limit=2)
    assert [t.strike for t in top] == [Decimal("25000.0"), Decimal("24800.0")]


def test_top_oi_strikes_excludes_legs_with_unknown_oi() -> None:
    strikes = {
        24700.0: [_leg(right=OptionRight.CE, oi=None)],
        24800.0: [_leg(right=OptionRight.CE, oi=500)],
    }
    snapshot = _snapshot(strikes)
    top = top_oi_strikes(snapshot, right=OptionRight.CE, limit=5)
    assert len(top) == 1
    assert top[0].strike == Decimal("24800.0")


# -- strike_window_around -----------------------------------------------------


def test_strike_window_around_centers_on_an_arbitrary_strike() -> None:
    snapshot = _snapshot(_STANDARD)
    window = strike_window_around(snapshot, center=Decimal("24900.0"), strikes_each_side=1)
    assert window == [Decimal("24800.0"), Decimal("24900.0"), Decimal("25000.0")]


def test_strike_window_around_matches_strike_window_when_centered_on_atm() -> None:
    snapshot = _snapshot(_STANDARD)  # ATM at spot=24800 -> 24800.0
    assert strike_window_around(snapshot, center=Decimal("24800.0"), strikes_each_side=2) == strike_window(snapshot, strikes_each_side=2)


def test_strike_window_around_empty_when_center_not_a_real_strike() -> None:
    snapshot = _snapshot(_STANDARD)
    assert strike_window_around(snapshot, center=Decimal("99999"), strikes_each_side=2) == []


def test_strike_window_around_clamps_at_chain_edges() -> None:
    snapshot = _snapshot(_STANDARD)
    window = strike_window_around(snapshot, center=Decimal("24700.0"), strikes_each_side=5)
    assert window == distinct_strikes(snapshot)  # clamped to the whole chain, never an out-of-range strike


# -- build_chain_rows ----------------------------------------------------------


def test_build_chain_rows_pairs_ce_and_pe_by_strike() -> None:
    snapshot = _snapshot(_STANDARD)
    rows = build_chain_rows(snapshot)
    assert [r.strike for r in rows] == distinct_strikes(snapshot)
    for row in rows:
        assert row.call is not None and row.call.right == OptionRight.CE and row.call.strike == row.strike
        assert row.put is not None and row.put.right == OptionRight.PE and row.put.strike == row.strike


def test_build_chain_rows_leaves_a_missing_side_as_none() -> None:
    strikes = {24700.0: [_leg(right=OptionRight.CE, oi=1000)]}  # no PE leg at this strike
    snapshot = _snapshot(strikes)
    rows = build_chain_rows(snapshot)
    assert len(rows) == 1
    assert rows[0].call is not None
    assert rows[0].put is None


def test_build_chain_rows_empty_chain_returns_empty_list() -> None:
    snapshot = _snapshot({})
    assert build_chain_rows(snapshot) == []
