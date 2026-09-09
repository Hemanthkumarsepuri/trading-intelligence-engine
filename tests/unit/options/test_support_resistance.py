from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.data.normalization.base import DefaultNormalizer
from app.data.providers.base import RawOptionChain, RawOptionLeg
from app.domain.market.freshness import DataFreshness
from app.domain.market.models import Candle, OptionChainSnapshot, OptionRight, Timeframe
from app.domain.options.support_resistance import (
    LevelKind,
    LevelSource,
    LevelStrength,
    StabilityState,
    TechnicalLevel,
    TechnicalLevelKind,
    assess_level_stability,
    classify_level_confluence,
    support_resistance_levels,
    technical_price_levels,
    vwap_ema_levels,
)

UNDERLYING = "NIFTY"
EXPIRY = date(2026, 9, 24)
RECEIVED_AT = datetime(2026, 8, 29, 10, 0, tzinfo=UTC)


def _leg(*, right: OptionRight, oi: int) -> RawOptionLeg:
    return RawOptionLeg(right=right, open_interest=oi, volume=100)


def _snapshot(strikes: dict[float, list[RawOptionLeg]], *, spot: float = 24800.0) -> OptionChainSnapshot:
    raw = RawOptionChain(underlying=UNDERLYING, expiry=EXPIRY, underlying_last_price=spot, strikes=strikes)
    return DefaultNormalizer(provider_name="test").normalize_option_chain(raw, received_at=RECEIVED_AT)


_STRIKES = {
    24600.0: [_leg(right=OptionRight.CE, oi=500), _leg(right=OptionRight.PE, oi=9000)],
    24700.0: [_leg(right=OptionRight.CE, oi=1000), _leg(right=OptionRight.PE, oi=4000)],
    24900.0: [_leg(right=OptionRight.CE, oi=8000), _leg(right=OptionRight.PE, oi=1200)],
    25000.0: [_leg(right=OptionRight.CE, oi=2000), _leg(right=OptionRight.PE, oi=500)],
}


def test_returns_both_support_and_resistance_candidates() -> None:
    levels = support_resistance_levels(_snapshot(_STRIKES), limit=2)
    kinds = {lv.kind for lv in levels}
    assert kinds == {LevelKind.SUPPORT, LevelKind.RESISTANCE}


def test_resistance_ranked_by_call_oi_descending() -> None:
    levels = support_resistance_levels(_snapshot(_STRIKES), limit=3)
    resistances = [lv for lv in levels if lv.kind == LevelKind.RESISTANCE]
    assert resistances[0].strike == Decimal("24900.0")
    assert resistances[0].open_interest == 8000


def test_support_ranked_by_put_oi_descending() -> None:
    levels = support_resistance_levels(_snapshot(_STRIKES), limit=3)
    supports = [lv for lv in levels if lv.kind == LevelKind.SUPPORT]
    assert supports[0].strike == Decimal("24600.0")
    assert supports[0].open_interest == 9000


def test_dominant_strike_is_rated_strong() -> None:
    levels = support_resistance_levels(_snapshot(_STRIKES), limit=3)
    top_support = next(lv for lv in levels if lv.kind == LevelKind.SUPPORT and lv.strike == Decimal("24600.0"))
    assert top_support.strength == LevelStrength.STRONG  # 9000 >= 2x next-highest (4000)


def test_distance_from_spot_computed_correctly() -> None:
    levels = support_resistance_levels(_snapshot(_STRIKES, spot=24800.0), limit=1)
    resistance = next(lv for lv in levels if lv.kind == LevelKind.RESISTANCE)
    assert resistance.distance_from_spot == Decimal("100.0")  # 24900 - 24800


def test_no_spot_price_returns_no_levels_geometry_cannot_be_verified() -> None:
    """S/R Geometry Fix -- without a real spot price there is no
    geometric reference to classify a strike as above/below, so this
    must return `[]` rather than levels with unverifiable/unknown
    geometry (the prior behavior, before this fix, silently returned
    OI-ranked levels with no spot check at all -- exactly the class of
    bug this fix closes)."""
    raw = RawOptionChain(underlying=UNDERLYING, expiry=EXPIRY, underlying_last_price=None, strikes=_STRIKES)
    snapshot = DefaultNormalizer(provider_name="test").normalize_option_chain(raw, received_at=RECEIVED_AT)
    levels = support_resistance_levels(snapshot, limit=1)
    assert levels == []


def test_empty_chain_returns_no_levels() -> None:
    assert support_resistance_levels(_snapshot({}), limit=3) == []


# -- S/R geometry fix: real GAIL-shaped UAT bug ------------------------
# Real observed defect: spot=171.10, heaviest PUT OI sat at 190.0/180.0
# (both ABOVE spot -- e.g. real hedging/protective-put OI far from a spot
# that has since moved) and was reported as SUPPORT anyway, because the
# old code ranked by OI alone with no spot-side check. A strike must
# never be classified support unless it is strictly below spot, and never
# resistance unless strictly above -- regardless of which side's OI is
# heavier there.

_GAIL_SHAPED_STRIKES = {
    165.0: [_leg(right=OptionRight.CE, oi=100), _leg(right=OptionRight.PE, oi=300)],
    167.5: [_leg(right=OptionRight.CE, oi=150), _leg(right=OptionRight.PE, oi=500)],
    170.0: [_leg(right=OptionRight.CE, oi=200), _leg(right=OptionRight.PE, oi=400)],
    171.10: [_leg(right=OptionRight.CE, oi=50), _leg(right=OptionRight.PE, oi=50)],  # == spot exactly
    172.5: [_leg(right=OptionRight.CE, oi=600), _leg(right=OptionRight.PE, oi=100)],
    175.0: [_leg(right=OptionRight.CE, oi=900), _leg(right=OptionRight.PE, oi=9000)],  # heavy PE OI, but ABOVE spot
    180.0: [_leg(right=OptionRight.CE, oi=700), _leg(right=OptionRight.PE, oi=8000)],  # heavy PE OI, but ABOVE spot
    190.0: [_leg(right=OptionRight.CE, oi=300), _leg(right=OptionRight.PE, oi=7000)],  # heavy PE OI, but ABOVE spot
}


def _gail_shaped_snapshot() -> OptionChainSnapshot:
    return _snapshot(_GAIL_SHAPED_STRIKES, spot=171.10)


def test_gail_shaped_chain_support_is_always_strictly_below_spot() -> None:
    levels = support_resistance_levels(_gail_shaped_snapshot(), limit=3)
    supports = [lv for lv in levels if lv.kind == LevelKind.SUPPORT]
    assert supports  # real candidates exist below spot (165/167.5/170)
    assert all(lv.strike < Decimal("171.10") for lv in supports)
    # The heavy-PE-OI strikes above spot (175/180/190) must NEVER appear as support.
    assert {lv.strike for lv in supports}.isdisjoint({Decimal("175.0"), Decimal("180.0"), Decimal("190.0")})


def test_gail_shaped_chain_resistance_is_always_strictly_above_spot() -> None:
    levels = support_resistance_levels(_gail_shaped_snapshot(), limit=3)
    resistances = [lv for lv in levels if lv.kind == LevelKind.RESISTANCE]
    assert resistances
    assert all(lv.strike > Decimal("171.10") for lv in resistances)


def test_gail_shaped_chain_no_overlap_between_support_and_resistance() -> None:
    levels = support_resistance_levels(_gail_shaped_snapshot(), limit=5)
    support_strikes = {lv.strike for lv in levels if lv.kind == LevelKind.SUPPORT}
    resistance_strikes = {lv.strike for lv in levels if lv.kind == LevelKind.RESISTANCE}
    assert support_strikes.isdisjoint(resistance_strikes)


def test_strike_exactly_at_spot_is_neither_support_nor_resistance() -> None:
    levels = support_resistance_levels(_gail_shaped_snapshot(), limit=10)
    assert all(lv.strike != Decimal("171.10") for lv in levels)


def test_support_signed_distance_is_negative_resistance_positive() -> None:
    """Preserves the existing SIGNED distance contract (never switched to
    absolute value): support below spot is negative, resistance above
    spot is positive."""
    levels = support_resistance_levels(_gail_shaped_snapshot(), limit=3)
    for lv in levels:
        assert lv.distance_from_spot is not None
        if lv.kind == LevelKind.SUPPORT:
            assert lv.distance_from_spot < 0
        else:
            assert lv.distance_from_spot > 0


def test_heavy_oi_on_the_wrong_side_never_promotes_a_level_across_spot() -> None:
    """A strike cannot become support merely because its PE OI is the
    single highest in the entire chain, when that strike sits above
    spot -- this is the exact real-world shape of the reported defect
    (175/180/190 all have far heavier PE OI than any strike below spot,
    yet none may ever appear as support)."""
    levels = support_resistance_levels(_gail_shaped_snapshot(), limit=3)
    for lv in levels:
        if lv.kind == LevelKind.SUPPORT:
            assert lv.strike not in (Decimal("175.0"), Decimal("180.0"), Decimal("190.0"))


# -- assess_level_stability ---------------------------------------------


_MEANINGFUL = Decimal("0.10")


def test_unconfirmed_when_no_prior_snapshot() -> None:
    levels = support_resistance_levels(_snapshot(_STRIKES), limit=1)
    results = assess_level_stability(levels, previous_snapshot=None, meaningful_oi_change_fraction=_MEANINGFUL)
    assert all(r.state == StabilityState.UNCONFIRMED for r in results)


def test_strengthening_when_oi_grew_meaningfully() -> None:
    previous_strikes = {24900.0: [_leg(right=OptionRight.CE, oi=4000)]}
    previous = _snapshot(previous_strikes)
    levels = support_resistance_levels(_snapshot(_STRIKES), limit=1)
    resistance = next(lv for lv in levels if lv.kind == LevelKind.RESISTANCE)
    results = assess_level_stability([resistance], previous_snapshot=previous, meaningful_oi_change_fraction=_MEANINGFUL)
    assert results[0].state == StabilityState.STRENGTHENING  # 8000 vs prior 4000


def test_weakening_when_oi_shrank_meaningfully() -> None:
    previous_strikes = {24900.0: [_leg(right=OptionRight.CE, oi=20000)]}
    previous = _snapshot(previous_strikes)
    levels = support_resistance_levels(_snapshot(_STRIKES), limit=1)
    resistance = next(lv for lv in levels if lv.kind == LevelKind.RESISTANCE)
    results = assess_level_stability([resistance], previous_snapshot=previous, meaningful_oi_change_fraction=_MEANINGFUL)
    assert results[0].state == StabilityState.WEAKENING


def test_stable_when_oi_barely_changed() -> None:
    previous_strikes = {24900.0: [_leg(right=OptionRight.CE, oi=7950)]}
    previous = _snapshot(previous_strikes)
    levels = support_resistance_levels(_snapshot(_STRIKES), limit=1)
    resistance = next(lv for lv in levels if lv.kind == LevelKind.RESISTANCE)
    results = assess_level_stability([resistance], previous_snapshot=previous, meaningful_oi_change_fraction=_MEANINGFUL)
    assert results[0].state == StabilityState.STABLE


def test_unconfirmed_when_strike_absent_in_prior_snapshot() -> None:
    previous = _snapshot({24600.0: [_leg(right=OptionRight.CE, oi=100)]})
    levels = support_resistance_levels(_snapshot(_STRIKES), limit=1)
    resistance = next(lv for lv in levels if lv.kind == LevelKind.RESISTANCE)
    results = assess_level_stability([resistance], previous_snapshot=previous, meaningful_oi_change_fraction=_MEANINGFUL)
    assert results[0].state == StabilityState.UNCONFIRMED


def test_never_claims_a_multi_session_pattern_in_the_detail_text() -> None:
    previous_strikes = {24900.0: [_leg(right=OptionRight.CE, oi=4000)]}
    previous = _snapshot(previous_strikes)
    levels = support_resistance_levels(_snapshot(_STRIKES), limit=1)
    resistance = next(lv for lv in levels if lv.kind == LevelKind.RESISTANCE)
    results = assess_level_stability([resistance], previous_snapshot=previous, meaningful_oi_change_fraction=_MEANINGFUL)
    for phrase in ("repeatedly", "sessions", "days"):
        assert phrase not in results[0].detail.lower()


# ============================================================
# Sprint 6 -- technical price levels + OI/technical confluence.
# ============================================================


def _candle(ts: datetime, *, high: float, low: float) -> Candle:
    return Candle(
        provider="test", freshness=DataFreshness(data_timestamp=ts, received_timestamp=ts),
        instrument_id="TEST", timeframe=Timeframe.M15,
        open=Decimal(str((high + low) / 2)), high=Decimal(str(high)), low=Decimal(str(low)), close=Decimal(str((high + low) / 2)), volume=1000,
    )


def test_technical_price_levels_insufficient_history_returns_empty() -> None:
    candles = [_candle(RECEIVED_AT, high=100, low=90)]
    assert technical_price_levels(candles, lookback=20) == []


def test_technical_price_levels_reads_real_swing_high_and_low() -> None:
    candles = [_candle(RECEIVED_AT + timedelta(minutes=15 * i), high=100 + i, low=90 - i) for i in range(20)]
    levels = technical_price_levels(candles, lookback=20)
    resistance = next(lv for lv in levels if lv.kind == TechnicalLevelKind.RESISTANCE)
    support = next(lv for lv in levels if lv.kind == TechnicalLevelKind.SUPPORT)
    assert resistance.price == Decimal("119")  # 100 + 19
    assert support.price == Decimal("71")  # 90 - 19


def test_confluence_requires_both_oi_and_technical_levels_to_independently_agree() -> None:
    oi_levels = support_resistance_levels(_snapshot(_STRIKES), limit=1)
    resistance = next(lv for lv in oi_levels if lv.kind == LevelKind.RESISTANCE)  # strike 24900.0
    technical = [TechnicalLevel(kind=TechnicalLevelKind.RESISTANCE, price=Decimal("24905"), evidence="real swing high")]
    classified = classify_level_confluence(oi_levels, technical, near_pct_threshold=Decimal("2.0"))
    matched = next(c for c in classified if c.price == resistance.strike)
    assert matched.source == LevelSource.CONFLUENCE


def test_oi_level_stays_structural_when_no_technical_level_is_nearby() -> None:
    oi_levels = support_resistance_levels(_snapshot(_STRIKES), limit=1)
    classified = classify_level_confluence(oi_levels, [], near_pct_threshold=Decimal("2.0"))
    assert all(c.source == LevelSource.OI_STRUCTURAL for c in classified)
    for c in classified:
        assert "not a confirmed level" in c.detail


def test_technical_level_reported_on_its_own_when_no_oi_concentration_is_nearby() -> None:
    far_technical = [TechnicalLevel(kind=TechnicalLevelKind.RESISTANCE, price=Decimal("99999"), evidence="real swing high, far from any OI level")]
    classified = classify_level_confluence([], far_technical, near_pct_threshold=Decimal("2.0"))
    assert len(classified) == 1
    assert classified[0].source == LevelSource.TECHNICAL_PRICE
    assert classified[0].price == Decimal("99999")


# ============================================================
# Sprint 7A, Objective 11 -- VWAP/EMA levels + STRONGER_CONFLUENCE.
# ============================================================


def test_vwap_ema_levels_support_when_spot_above() -> None:
    levels = vwap_ema_levels(spot=Decimal("24900"), vwap_value=Decimal("24800"), ema_value=Decimal("24750"))
    vwap_level = next(lv for lv in levels if lv.source_label == "VWAP")
    ema_level = next(lv for lv in levels if lv.source_label == "EMA")
    assert vwap_level.kind == TechnicalLevelKind.SUPPORT
    assert ema_level.kind == TechnicalLevelKind.SUPPORT


def test_vwap_ema_levels_resistance_when_spot_below() -> None:
    levels = vwap_ema_levels(spot=Decimal("24700"), vwap_value=Decimal("24800"), ema_value=Decimal("24850"))
    vwap_level = next(lv for lv in levels if lv.source_label == "VWAP")
    ema_level = next(lv for lv in levels if lv.source_label == "EMA")
    assert vwap_level.kind == TechnicalLevelKind.RESISTANCE
    assert ema_level.kind == TechnicalLevelKind.RESISTANCE


def test_vwap_ema_levels_empty_without_real_spot_or_values() -> None:
    assert vwap_ema_levels(spot=None, vwap_value=Decimal("24800"), ema_value=Decimal("24750")) == []
    assert vwap_ema_levels(spot=Decimal("24900"), vwap_value=None, ema_value=None) == []


def test_confluence_upgrades_to_stronger_when_two_distinct_sources_agree() -> None:
    oi_levels = support_resistance_levels(_snapshot(_STRIKES), limit=1)
    resistance = next(lv for lv in oi_levels if lv.kind == LevelKind.RESISTANCE)  # strike 24900.0
    technical = [
        TechnicalLevel(kind=TechnicalLevelKind.RESISTANCE, price=Decimal("24905"), evidence="real swing high", source_label="SWING"),
        TechnicalLevel(kind=TechnicalLevelKind.RESISTANCE, price=Decimal("24895"), evidence="real VWAP", source_label="VWAP"),
    ]
    classified = classify_level_confluence(oi_levels, technical, near_pct_threshold=Decimal("2.0"))
    matched = next(c for c in classified if c.price == resistance.strike)
    assert matched.source == LevelSource.STRONGER_CONFLUENCE


def test_confluence_stays_regular_when_only_one_source_agrees() -> None:
    oi_levels = support_resistance_levels(_snapshot(_STRIKES), limit=1)
    resistance = next(lv for lv in oi_levels if lv.kind == LevelKind.RESISTANCE)
    technical = [TechnicalLevel(kind=TechnicalLevelKind.RESISTANCE, price=Decimal("24905"), evidence="real swing high", source_label="SWING")]
    classified = classify_level_confluence(oi_levels, technical, near_pct_threshold=Decimal("2.0"))
    matched = next(c for c in classified if c.price == resistance.strike)
    assert matched.source == LevelSource.CONFLUENCE
