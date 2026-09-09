from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.data.normalization.base import DefaultNormalizer
from app.data.providers.base import RawOptionChain, RawOptionLeg
from app.domain.market.models import OptionChainSnapshot, OptionRight
from app.domain.options.data_quality import check_chain_quality
from app.domain.options.models import ChainQualityIssueKind

UNDERLYING = "NSE_INDEX|Nifty 50"
EXPIRY = date(2026, 9, 24)
RECEIVED_AT = datetime(2026, 8, 29, 10, 0, tzinfo=UTC)

_DEFAULT_MAX_AGE = timedelta(seconds=15)
_DEFAULT_SPREAD = Decimal("0.15")


def _healthy_leg(*, right: OptionRight) -> RawOptionLeg:
    return RawOptionLeg(
        right=right, last_price=10.0, bid_price=9.7, ask_price=10.3, volume=500,
        open_interest=1000, previous_open_interest=900, implied_volatility=15.0,
        delta=0.5, theta=-1.2, gamma=0.01, vega=2.0,
    )


def _snapshot(strikes: dict[float, list[RawOptionLeg]], *, spot: float | None = 24800.0) -> OptionChainSnapshot:
    raw = RawOptionChain(underlying=UNDERLYING, expiry=EXPIRY, underlying_last_price=spot, strikes=strikes)
    return DefaultNormalizer(provider_name="test").normalize_option_chain(raw, received_at=RECEIVED_AT)


def _check(snapshot: OptionChainSnapshot, *, as_of: datetime | None = None) -> list[ChainQualityIssueKind]:
    issues = check_chain_quality(
        snapshot, as_of=as_of or RECEIVED_AT, max_age=_DEFAULT_MAX_AGE, max_spread_fraction=_DEFAULT_SPREAD
    )
    return [i.kind for i in issues]


def test_healthy_chain_has_no_issues() -> None:
    strikes = {24800.0: [_healthy_leg(right=OptionRight.CE), _healthy_leg(right=OptionRight.PE)]}
    assert _check(_snapshot(strikes)) == []


def test_stale_snapshot_flagged_when_age_exceeds_max() -> None:
    strikes = {24800.0: [_healthy_leg(right=OptionRight.CE), _healthy_leg(right=OptionRight.PE)]}
    snapshot = _snapshot(strikes)
    issues = _check(snapshot, as_of=RECEIVED_AT + timedelta(minutes=5))
    assert ChainQualityIssueKind.STALE_SNAPSHOT in issues


def test_missing_underlying_price_flagged() -> None:
    strikes = {24800.0: [_healthy_leg(right=OptionRight.CE), _healthy_leg(right=OptionRight.PE)]}
    snapshot = _snapshot(strikes, spot=None)
    assert ChainQualityIssueKind.NO_UNDERLYING_PRICE in _check(snapshot)


def test_dead_chain_flagged_when_every_leg_has_zero_oi_and_volume() -> None:
    dead = RawOptionLeg(right=OptionRight.CE, open_interest=0, volume=0)
    dead_pe = RawOptionLeg(right=OptionRight.PE, open_interest=0, volume=0)
    snapshot = _snapshot({24800.0: [dead, dead_pe]})
    assert ChainQualityIssueKind.DEAD_CHAIN in _check(snapshot)


def test_dead_chain_not_flagged_when_at_least_one_leg_has_activity() -> None:
    dead = RawOptionLeg(right=OptionRight.CE, open_interest=0, volume=0)
    active = _healthy_leg(right=OptionRight.PE)
    snapshot = _snapshot({24800.0: [dead, active]})
    assert ChainQualityIssueKind.DEAD_CHAIN not in _check(snapshot)


def test_one_sided_strike_flagged_when_a_strike_is_missing_a_leg() -> None:
    strikes = {24800.0: [_healthy_leg(right=OptionRight.CE)]}  # no PE leg at this strike
    assert ChainQualityIssueKind.ONE_SIDED_STRIKE in _check(_snapshot(strikes))


def test_missing_greeks_flagged() -> None:
    no_greeks = RawOptionLeg(right=OptionRight.CE, last_price=10.0, delta=None, implied_volatility=None)
    strikes = {24800.0: [no_greeks, _healthy_leg(right=OptionRight.PE)]}
    assert ChainQualityIssueKind.MISSING_GREEKS in _check(_snapshot(strikes))


def test_wide_spread_flagged_when_spread_exceeds_threshold() -> None:
    wide = RawOptionLeg(right=OptionRight.CE, bid_price=5.0, ask_price=10.0, open_interest=100, volume=10)  # 50% spread
    strikes = {24800.0: [wide, _healthy_leg(right=OptionRight.PE)]}
    assert ChainQualityIssueKind.WIDE_SPREAD in _check(_snapshot(strikes))


def test_wide_spread_not_flagged_within_threshold() -> None:
    strikes = {24800.0: [_healthy_leg(right=OptionRight.CE), _healthy_leg(right=OptionRight.PE)]}  # ~6% spread
    assert ChainQualityIssueKind.WIDE_SPREAD not in _check(_snapshot(strikes))


def test_crossed_market_flagged_when_bid_exceeds_ask() -> None:
    """Sprint 6 -- an objectively impossible quote state (bid > ask),
    real, live-observable data-quality gap: `(ask-bid)/ask` goes negative
    for a crossed leg and would otherwise never trip WIDE_SPREAD."""
    crossed = RawOptionLeg(right=OptionRight.CE, bid_price=10.5, ask_price=10.0, open_interest=100, volume=10)
    strikes = {24800.0: [crossed, _healthy_leg(right=OptionRight.PE)]}
    issues = _check(_snapshot(strikes))
    assert ChainQualityIssueKind.CROSSED_MARKET in issues
    assert ChainQualityIssueKind.WIDE_SPREAD not in issues  # never double-flagged as merely "wide"


def test_crossed_market_not_flagged_for_a_healthy_chain() -> None:
    strikes = {24800.0: [_healthy_leg(right=OptionRight.CE), _healthy_leg(right=OptionRight.PE)]}
    assert ChainQualityIssueKind.CROSSED_MARKET not in _check(_snapshot(strikes))


def test_crossed_market_not_flagged_when_bid_equals_ask() -> None:
    """Bid == ask is unusual but not objectively impossible (a real,
    if unlikely, locked market) -- only bid > ask is flagged."""
    locked = RawOptionLeg(right=OptionRight.CE, bid_price=10.0, ask_price=10.0, open_interest=100, volume=10)
    strikes = {24800.0: [locked, _healthy_leg(right=OptionRight.PE)]}
    assert ChainQualityIssueKind.CROSSED_MARKET not in _check(_snapshot(strikes))


def test_multiple_issues_can_be_reported_together() -> None:
    strikes = {24800.0: [RawOptionLeg(right=OptionRight.CE, open_interest=0, volume=0)]}  # dead + one-sided
    issues = _check(_snapshot(strikes, spot=None))
    assert ChainQualityIssueKind.NO_UNDERLYING_PRICE in issues
def test_empty_chain_is_flagged() -> None:
    snapshot = _snapshot({})
    assert ChainQualityIssueKind.EMPTY_CHAIN in _check(snapshot)


def test_duplicate_strike_right_is_flagged() -> None:
    snapshot = _snapshot({24800.0: [_healthy_leg(right=OptionRight.CE), _healthy_leg(right=OptionRight.PE)]})
    dup = snapshot.model_copy(update={"legs": [*snapshot.legs, snapshot.legs[0]]})
    assert ChainQualityIssueKind.DUPLICATE_STRIKE_RIGHT in _check(dup)


def test_expired_contract_is_flagged() -> None:
    snapshot = _snapshot({24800.0: [_healthy_leg(right=OptionRight.CE), _healthy_leg(right=OptionRight.PE)]})
    issues = _check(snapshot, as_of=datetime(2026, 9, 25, 10, 0, tzinfo=UTC))
    assert ChainQualityIssueKind.EXPIRED_CONTRACT in issues

