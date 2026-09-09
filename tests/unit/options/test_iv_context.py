from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.data.normalization.base import DefaultNormalizer
from app.data.providers.base import RawOptionChain, RawOptionLeg
from app.domain.market.freshness import DataFreshness
from app.domain.market.models import OptionChainSnapshot, OptionRight
from app.domain.options.iv_context import (
    IvRankStatus,
    IvTrend,
    atm_iv_summary,
    classify_iv_trend,
    compute_iv_rank,
    iv_smile,
)
from app.domain.options.models import IvObservation

UNDERLYING = "NIFTY"
EXPIRY = date(2026, 9, 24)
RECEIVED_AT = datetime(2026, 8, 29, 10, 0, tzinfo=UTC)


def _leg(*, right: OptionRight, iv: float | None) -> RawOptionLeg:
    return RawOptionLeg(right=right, implied_volatility=iv)


def _snapshot(strikes: dict[float, list[RawOptionLeg]], *, spot: float = 24800.0) -> OptionChainSnapshot:
    raw = RawOptionChain(underlying=UNDERLYING, expiry=EXPIRY, underlying_last_price=spot, strikes=strikes)
    return DefaultNormalizer(provider_name="test").normalize_option_chain(raw, received_at=RECEIVED_AT)


def _iv_obs(ts: datetime, *, chain_iv: str) -> IvObservation:
    return IvObservation(
        provider="test", freshness=DataFreshness(data_timestamp=ts, received_timestamp=ts), underlying=UNDERLYING,
        expiry=EXPIRY, atm_strike=Decimal("24800"), atm_ce_iv=Decimal(chain_iv), atm_pe_iv=Decimal(chain_iv),
        chain_iv=Decimal(chain_iv),
    )


# -- atm_iv_summary -----------------------------------------------------


def test_atm_iv_summary_computes_chain_iv_and_skew() -> None:
    strikes = {24800.0: [_leg(right=OptionRight.CE, iv=14.0), _leg(right=OptionRight.PE, iv=12.0)]}
    summary = atm_iv_summary(_snapshot(strikes, spot=24800.0))
    assert summary.atm_strike == Decimal("24800.0")
    assert summary.chain_iv == Decimal("13.0")
    assert summary.ce_pe_skew == Decimal("2.0")


def test_atm_iv_summary_one_side_missing_no_skew() -> None:
    strikes = {24800.0: [_leg(right=OptionRight.CE, iv=14.0)]}
    summary = atm_iv_summary(_snapshot(strikes, spot=24800.0))
    assert summary.chain_iv == Decimal("14.0")
    assert summary.ce_pe_skew is None  # never fabricated as zero


def test_atm_iv_summary_treats_zero_iv_as_unusable_not_a_real_reading() -> None:
    # Real, live-observed case (RELIANCE far-dated expiry, 2026-08-29):
    # Upstox reported a literal 0.0 IV on an illiquid far strike -- this
    # must read as "no usable reading" (matching a missing leg), never as
    # a genuine zero-volatility data point.
    strikes = {24800.0: [_leg(right=OptionRight.CE, iv=0.0), _leg(right=OptionRight.PE, iv=14.0)]}
    summary = atm_iv_summary(_snapshot(strikes, spot=24800.0))
    assert summary.atm_ce_iv is None
    assert summary.chain_iv == Decimal("14.0")  # falls back to the one usable side, exactly like a missing leg would
    assert summary.ce_pe_skew is None  # one side unusable -> no fabricated skew


def test_atm_iv_summary_no_spot_price_returns_all_none() -> None:
    raw = RawOptionChain(underlying=UNDERLYING, expiry=EXPIRY, underlying_last_price=None, strikes={})
    snapshot = DefaultNormalizer(provider_name="test").normalize_option_chain(raw, received_at=RECEIVED_AT)
    summary = atm_iv_summary(snapshot)
    assert summary.atm_strike is None
    assert summary.chain_iv is None


# -- iv_smile -------------------------------------------------------------


def test_iv_smile_treats_zero_iv_as_unusable_not_a_real_reading() -> None:
    strikes = {24800.0: [_leg(right=OptionRight.CE, iv=0.0), _leg(right=OptionRight.PE, iv=14.0)]}
    smile = iv_smile(_snapshot(strikes, spot=24800.0), strikes_each_side=0)
    assert smile[0].ce_iv is None
    assert smile[0].pe_iv == Decimal("14.0")


def test_iv_smile_returns_points_within_window() -> None:
    strikes = {
        24700.0: [_leg(right=OptionRight.CE, iv=15.0), _leg(right=OptionRight.PE, iv=14.0)],
        24800.0: [_leg(right=OptionRight.CE, iv=13.0), _leg(right=OptionRight.PE, iv=13.0)],
        24900.0: [_leg(right=OptionRight.CE, iv=14.5), _leg(right=OptionRight.PE, iv=15.5)],
    }
    smile = iv_smile(_snapshot(strikes, spot=24800.0), strikes_each_side=1)
    assert [p.strike for p in smile] == [Decimal("24700.0"), Decimal("24800.0"), Decimal("24900.0")]
    assert smile[1].ce_iv == Decimal("13.0")


def test_iv_smile_empty_when_no_atm() -> None:
    assert iv_smile(_snapshot({}), strikes_each_side=2) == []


# -- compute_iv_rank -------------------------------------------------------


def test_iv_rank_unavailable_with_no_current_iv() -> None:
    result = compute_iv_rank([], current_chain_iv=None, min_observations=3)
    assert result.status == IvRankStatus.UNAVAILABLE


def test_iv_rank_unavailable_with_insufficient_history() -> None:
    t0 = RECEIVED_AT
    history = [_iv_obs(t0 - timedelta(days=1), chain_iv="13.0")]
    result = compute_iv_rank(history, current_chain_iv=Decimal("14.0"), min_observations=3)
    assert result.status == IvRankStatus.UNAVAILABLE
    assert result.observations_used == 1


def test_iv_rank_computed_with_sufficient_history() -> None:
    t0 = RECEIVED_AT
    history = [
        _iv_obs(t0 - timedelta(days=3), chain_iv="10.0"),
        _iv_obs(t0 - timedelta(days=2), chain_iv="15.0"),
        _iv_obs(t0 - timedelta(days=1), chain_iv="12.0"),
    ]
    result = compute_iv_rank(history, current_chain_iv=Decimal("15.0"), min_observations=3)
    assert result.status == IvRankStatus.OK
    assert result.value == Decimal(100)  # current == max of [10,15,12,15] -> rank 100
    assert result.observations_used == 3


def test_iv_rank_unavailable_when_range_degenerate() -> None:
    t0 = RECEIVED_AT
    history = [_iv_obs(t0 - timedelta(days=i), chain_iv="13.0") for i in range(1, 4)]
    result = compute_iv_rank(history, current_chain_iv=Decimal("13.0"), min_observations=3)
    assert result.status == IvRankStatus.UNAVAILABLE


# -- classify_iv_trend -------------------------------------------------------

_TREND_STEP = Decimal("1.0")


def test_iv_trend_insufficient_history_with_no_history() -> None:
    assert classify_iv_trend(current_chain_iv=Decimal("15"), history=[], meaningful_change_points=_TREND_STEP) == IvTrend.INSUFFICIENT_HISTORY


def test_iv_trend_insufficient_history_with_no_current_iv() -> None:
    history = [_iv_obs(RECEIVED_AT - timedelta(days=1), chain_iv="15.0")]
    assert classify_iv_trend(current_chain_iv=None, history=history, meaningful_change_points=_TREND_STEP) == IvTrend.INSUFFICIENT_HISTORY


def test_iv_trend_rising_when_current_meaningfully_above_most_recent() -> None:
    history = [_iv_obs(RECEIVED_AT - timedelta(days=1), chain_iv="10.0")]
    trend = classify_iv_trend(current_chain_iv=Decimal("13.0"), history=history, meaningful_change_points=_TREND_STEP)
    assert trend == IvTrend.RISING


def test_iv_trend_falling_when_current_meaningfully_below_most_recent() -> None:
    history = [_iv_obs(RECEIVED_AT - timedelta(days=1), chain_iv="10.0")]
    trend = classify_iv_trend(current_chain_iv=Decimal("7.0"), history=history, meaningful_change_points=_TREND_STEP)
    assert trend == IvTrend.FALLING


def test_iv_trend_stable_when_change_below_threshold() -> None:
    history = [_iv_obs(RECEIVED_AT - timedelta(days=1), chain_iv="10.0")]
    trend = classify_iv_trend(current_chain_iv=Decimal("10.4"), history=history, meaningful_change_points=_TREND_STEP)
    assert trend == IvTrend.STABLE


def test_iv_trend_uses_only_the_most_recent_observation() -> None:
    # Two historical points; only the LAST (most recent) one must matter.
    history = [
        _iv_obs(RECEIVED_AT - timedelta(days=5), chain_iv="30.0"),  # would suggest FALLING if used
        _iv_obs(RECEIVED_AT - timedelta(days=1), chain_iv="10.0"),  # the real most-recent point
    ]
    trend = classify_iv_trend(current_chain_iv=Decimal("13.0"), history=history, meaningful_change_points=_TREND_STEP)
    assert trend == IvTrend.RISING
