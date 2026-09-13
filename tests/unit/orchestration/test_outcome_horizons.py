"""Phase 3 -- pure, deterministic unit tests for the price-path outcome
horizons and the confirmation/invalidation split (Sections 16-19). No
network, no pipeline re-run -- everything here operates on directly
constructed `ResearchObservation`/`Candle` fixtures, mirroring
`tests/unit/orchestration/test_research_outcome.py`'s own style.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.audit.research_models import ResearchObservation
from app.domain.market.freshness import DataFreshness
from app.domain.market.models import Candle, Timeframe
from app.orchestration.outcome_horizons import (
    ConfirmationOutcome,
    InvalidationOutcome,
    OutcomeHorizonLabel,
    compute_all_horizons,
    compute_price_path_outcome,
    horizon_target_timestamp,
)

_FRIDAY_0915 = datetime(2026, 8, 28, 3, 45, tzinfo=UTC)  # 09:15 IST


def _observation(
    *, direction: str = "BULLISH", spot: str = "1000", breakeven: str | None = "1020",
    nearest_level_kind: str | None = "resistance", nearest_level_value: str | None = "1030",
    invalidation_level_kind: str | None = None, invalidation_level_value: str | None = None,
    generated_at: datetime = _FRIDAY_0915, right: str = "CE",
) -> ResearchObservation:
    return ResearchObservation(
        run_id="run1", audit_id=None, generated_at=generated_at, symbol="RELIANCE", direction=direction,
        selected_right=right, selected_strike="1000", early_stage_state="EARLY_DIRECTIONAL_BUILD",
        research_confidence="MODERATE", actionability="WATCH", spot_at_observation=spot,
        contractual_expiry_breakeven=breakeven, nearest_level_kind=nearest_level_kind,
        nearest_level_value=nearest_level_value,
        invalidation_level_kind=invalidation_level_kind, invalidation_level_value=invalidation_level_value,
        market_context=None, participation_note=None,
        coverage_classification="REPLAY", thesis="test thesis",
    )


def _candle(*, timestamp: datetime, open_: str, high: str, low: str, close: str, volume: int = 1000) -> Candle:
    return Candle(
        provider="test", freshness=DataFreshness(data_timestamp=timestamp, received_timestamp=timestamp),
        instrument_id="NSE_EQ|TEST", timeframe=Timeframe.M15,
        open=Decimal(open_), high=Decimal(high), low=Decimal(low), close=Decimal(close), volume=volume,
    )


# ============================================================
# horizon_target_timestamp
# ============================================================


def test_intraday_horizons_are_straight_offsets() -> None:
    obs = _observation()
    assert horizon_target_timestamp(obs, OutcomeHorizonLabel.PLUS_30M) == obs.generated_at + timedelta(minutes=30)
    assert horizon_target_timestamp(obs, OutcomeHorizonLabel.PLUS_1H) == obs.generated_at + timedelta(hours=1)


def test_session_horizon_skips_the_weekend_and_targets_session_close() -> None:
    obs = _observation(generated_at=_FRIDAY_0915)  # a Friday
    target = horizon_target_timestamp(obs, OutcomeHorizonLabel.PLUS_1D)
    # +1 session from Friday is Monday 15:30 IST = 10:00 UTC.
    assert target == datetime(2026, 8, 31, 10, 0, tzinfo=UTC)


def test_plus_3_and_plus_5_sessions_are_strictly_ordered() -> None:
    obs = _observation()
    t1 = horizon_target_timestamp(obs, OutcomeHorizonLabel.PLUS_1D)
    t3 = horizon_target_timestamp(obs, OutcomeHorizonLabel.PLUS_3D)
    t5 = horizon_target_timestamp(obs, OutcomeHorizonLabel.PLUS_5D)
    assert t1 < t3 < t5


# ============================================================
# compute_price_path_outcome -- data sufficiency (Section 16: only
# calculate horizons for which enough future data genuinely exists)
# ============================================================


def test_insufficient_when_target_instant_has_not_been_reached_yet() -> None:
    obs = _observation()
    as_of = obs.generated_at + timedelta(minutes=5)  # far short of the +30m target
    result = compute_price_path_outcome(obs, OutcomeHorizonLabel.PLUS_30M, [], as_of=as_of)
    assert result.data_sufficient is False
    assert result.confirmation_outcome == ConfirmationOutcome.UNKNOWN
    assert result.invalidation_outcome == InvalidationOutcome.UNKNOWN


def test_insufficient_when_series_does_not_reach_back_to_the_observation() -> None:
    obs = _observation()
    as_of = obs.generated_at + timedelta(hours=2)
    # Only candles from AFTER the observation -- the observation instant
    # itself is not covered.
    candles = [_candle(timestamp=obs.generated_at + timedelta(minutes=45), open_="1000", high="1005", low="998", close="1002")]
    result = compute_price_path_outcome(obs, OutcomeHorizonLabel.PLUS_1H, candles, as_of=as_of)
    assert result.data_sufficient is False


def test_insufficient_when_series_falls_short_of_the_horizon() -> None:
    obs = _observation()
    as_of = obs.generated_at + timedelta(hours=2)
    # Covers the observation instant but stops well short of +1h.
    candles = [_candle(timestamp=obs.generated_at, open_="1000", high="1002", low="998", close="1001")]
    result = compute_price_path_outcome(obs, OutcomeHorizonLabel.PLUS_1H, candles, as_of=as_of)
    assert result.data_sufficient is False


def test_insufficient_when_no_spot_at_observation() -> None:
    obs = _observation(spot=None)  # type: ignore[arg-type]
    as_of = obs.generated_at + timedelta(hours=2)
    candles = [
        _candle(timestamp=obs.generated_at + timedelta(minutes=i * 15), open_="1000", high="1010", low="995", close="1000")
        for i in range(5)
    ]
    result = compute_price_path_outcome(obs, OutcomeHorizonLabel.PLUS_1H, candles, as_of=as_of)
    assert result.data_sufficient is False


# ============================================================
# compute_price_path_outcome -- real excursion math
# ============================================================


def _window_candles(obs: ResearchObservation, *, highs: list[str], lows: list[str], closes: list[str]) -> list[Candle]:
    return [
        _candle(
            timestamp=obs.generated_at + timedelta(minutes=15 * i), open_=lows[i], high=highs[i], low=lows[i], close=closes[i],
        )
        for i in range(len(highs))
    ]


def test_bullish_favorable_and_adverse_move_from_real_high_low() -> None:
    obs = _observation(direction="BULLISH", spot="1000")
    candles = _window_candles(obs, highs=["1000", "1010", "1025", "1015"], lows=["995", "1000", "1010", "1005"], closes=["1000", "1008", "1020", "1010"])
    as_of = obs.generated_at + timedelta(hours=1)
    result = compute_price_path_outcome(obs, OutcomeHorizonLabel.PLUS_1H, candles, as_of=as_of)
    assert result.data_sufficient is True
    assert result.subsequent_high == Decimal("1025")
    assert result.subsequent_low == Decimal("995")
    # favorable = (1025 - 1000) / 1000 * 100 = 2.5
    assert result.max_favorable_move_pct == Decimal("2.5")
    # adverse = (1000 - 995) / 1000 * 100 = 0.5
    assert result.max_adverse_move_pct == Decimal("0.5")


def test_bearish_favorable_and_adverse_move_is_mirrored() -> None:
    obs = _observation(direction="BEARISH", spot="1000", right="PE", breakeven="980", nearest_level_kind="support", nearest_level_value="970")
    candles = _window_candles(obs, highs=["1000", "1005", "1008", "1002"], lows=["990", "985", "980", "988"], closes=["995", "988", "982", "990"])
    as_of = obs.generated_at + timedelta(hours=1)
    result = compute_price_path_outcome(obs, OutcomeHorizonLabel.PLUS_1H, candles, as_of=as_of)
    # favorable (bearish) = (spot - low) / spot * 100 = (1000-980)/1000*100 = 2.0
    assert result.max_favorable_move_pct == Decimal("2.0")
    # adverse (bearish) = (high - spot) / spot * 100 = (1008-1000)/1000*100 = 0.8
    assert result.max_adverse_move_pct == Decimal("0.8")


# ============================================================
# confirmation / invalidation -- SEPARATE outcomes (Sections 18-19)
# ============================================================


def test_confirmation_confirmed_when_ce_breakeven_is_reached() -> None:
    obs = _observation(direction="BULLISH", spot="1000", breakeven="1020", right="CE")
    candles = _window_candles(obs, highs=["1005", "1015", "1025", "1022"], lows=["1000", "1005", "1015", "1018"], closes=["1003", "1012", "1022", "1020"])
    result = compute_price_path_outcome(obs, OutcomeHorizonLabel.PLUS_1H, candles, as_of=obs.generated_at + timedelta(hours=1))
    assert result.confirmation_outcome == ConfirmationOutcome.CONFIRMED


def test_confirmation_not_confirmed_when_breakeven_never_reached() -> None:
    obs = _observation(direction="BULLISH", spot="1000", breakeven="1020", right="CE")
    candles = _window_candles(obs, highs=["1005", "1008", "1010", "1009"], lows=["1000", "1003", "1005", "1004"], closes=["1003", "1006", "1008", "1007"])
    result = compute_price_path_outcome(obs, OutcomeHorizonLabel.PLUS_1H, candles, as_of=obs.generated_at + timedelta(hours=1))
    assert result.confirmation_outcome == ConfirmationOutcome.NOT_CONFIRMED


def test_confirmation_unknown_when_neither_breakeven_nor_level_recorded() -> None:
    obs = _observation(breakeven=None, nearest_level_kind=None, nearest_level_value=None)
    candles = _window_candles(obs, highs=["1005", "1010", "1012", "1011"], lows=["1000", "1003", "1005", "1004"], closes=["1003", "1006", "1008", "1007"])
    result = compute_price_path_outcome(obs, OutcomeHorizonLabel.PLUS_1H, candles, as_of=obs.generated_at + timedelta(hours=1))
    assert result.confirmation_outcome == ConfirmationOutcome.UNKNOWN


def test_confirmation_not_confirmed_when_only_breakeven_missing_but_level_holds() -> None:
    """Confirmation now checks BOTH signals -- a real, recorded opposing
    level that hasn't broken still yields NOT_CONFIRMED, never UNKNOWN,
    even with no breakeven."""
    obs = _observation(breakeven=None)  # default nearest_level_kind="resistance"/"1030" stays
    candles = _window_candles(obs, highs=["1005", "1010", "1012", "1011"], lows=["1000", "1003", "1005", "1004"], closes=["1003", "1006", "1008", "1007"])
    result = compute_price_path_outcome(obs, OutcomeHorizonLabel.PLUS_1H, candles, as_of=obs.generated_at + timedelta(hours=1))
    assert result.confirmation_outcome == ConfirmationOutcome.NOT_CONFIRMED


def test_invalidation_stays_unknown_from_the_confirmation_level_alone() -> None:
    """The CONFIRMATION-relevant opposing level (`nearest_level_kind`/
    `nearest_level_value`) must never feed `invalidation_outcome`, no
    matter what price does -- fabricating one from the other would
    mislabel a real breakout as "invalidated" (the exact bug fixed in
    the 95% sprint). Genuine invalidation now comes ONLY from the
    separately-tracked `invalidation_level_kind`/`invalidation_level_value`
    (see the tests below); this observation doesn't carry one, so
    `invalidation_outcome` must stay UNKNOWN even though price clearly
    broke through the (confirmation-relevant) resistance."""
    obs = _observation(direction="BULLISH", spot="1000", nearest_level_kind="resistance", nearest_level_value="1010")
    candles = _window_candles(obs, highs=["1005", "1015", "1025", "1022"], lows=["1000", "1005", "1015", "1018"], closes=["1003", "1012", "1022", "1020"])
    result = compute_price_path_outcome(obs, OutcomeHorizonLabel.PLUS_1H, candles, as_of=obs.generated_at + timedelta(hours=1))
    assert result.invalidation_outcome == InvalidationOutcome.UNKNOWN


def test_invalidation_unknown_when_no_level_recorded() -> None:
    obs = _observation(nearest_level_kind=None, nearest_level_value=None)
    candles = _window_candles(obs, highs=["1005", "1010", "1012", "1011"], lows=["1000", "998", "996", "997"], closes=["1003", "1006", "1008", "1007"])
    result = compute_price_path_outcome(obs, OutcomeHorizonLabel.PLUS_1H, candles, as_of=obs.generated_at + timedelta(hours=1))
    assert result.invalidation_outcome == InvalidationOutcome.UNKNOWN


# ============================================================
# genuine invalidation -- final 95% sprint (Section 6): populated ONLY
# for PRE_BREAKOUT_COMPRESSION observations via the separately-tracked
# `invalidation_level_kind`/`invalidation_level_value` fields.
# ============================================================


def test_invalidation_invalidated_when_bullish_support_is_broken() -> None:
    """A BULLISH PRE_BREAKOUT_COMPRESSION observation's own supporting
    floor (support, below spot) giving way is genuine invalidation --
    the compression range breaking on the WRONG side."""
    obs = _observation(
        direction="BULLISH", spot="1000", breakeven=None, nearest_level_kind=None, nearest_level_value=None,
        invalidation_level_kind="support", invalidation_level_value="990",
    )
    candles = _window_candles(obs, highs=["1000", "998", "995", "992"], lows=["995", "992", "988", "985"], closes=["996", "993", "990", "987"])
    result = compute_price_path_outcome(obs, OutcomeHorizonLabel.PLUS_1H, candles, as_of=obs.generated_at + timedelta(hours=1))
    assert result.invalidation_outcome == InvalidationOutcome.INVALIDATED


def test_invalidation_not_invalidated_when_bullish_support_holds() -> None:
    obs = _observation(
        direction="BULLISH", spot="1000", breakeven=None, nearest_level_kind=None, nearest_level_value=None,
        invalidation_level_kind="support", invalidation_level_value="980",
    )
    candles = _window_candles(obs, highs=["1005", "1008", "1006", "1004"], lows=["998", "996", "995", "997"], closes=["1000", "999", "998", "1000"])
    result = compute_price_path_outcome(obs, OutcomeHorizonLabel.PLUS_1H, candles, as_of=obs.generated_at + timedelta(hours=1))
    assert result.invalidation_outcome == InvalidationOutcome.NOT_INVALIDATED


def test_invalidation_invalidated_when_bearish_resistance_is_broken() -> None:
    """The mirrored BEARISH case: the thesis's own supporting ceiling
    (resistance, above spot) giving way on the WRONG side."""
    obs = _observation(
        direction="BEARISH", spot="1000", breakeven=None, nearest_level_kind=None, nearest_level_value=None,
        right="PE", invalidation_level_kind="resistance", invalidation_level_value="1010",
    )
    candles = _window_candles(obs, highs=["1005", "1008", "1012", "1015"], lows=["1000", "1002", "1005", "1008"], closes=["1003", "1006", "1010", "1013"])
    result = compute_price_path_outcome(obs, OutcomeHorizonLabel.PLUS_1H, candles, as_of=obs.generated_at + timedelta(hours=1))
    assert result.invalidation_outcome == InvalidationOutcome.INVALIDATED


def test_invalidation_and_confirmation_are_independent_outcomes() -> None:
    """A real observation can carry BOTH a confirmation-relevant opposing
    level and an invalidation-relevant supporting level at once (a real
    PRE_BREAKOUT_COMPRESSION candidate has both); the two must be
    evaluated independently, never conflated."""
    obs = _observation(
        direction="BULLISH", spot="1000", breakeven=None,
        nearest_level_kind="resistance", nearest_level_value="1030",
        invalidation_level_kind="support", invalidation_level_value="980",
    )
    # Price stays comfortably inside the compression range: neither the
    # resistance (confirmation) nor the support (invalidation) breaks.
    candles = _window_candles(obs, highs=["1005", "1008", "1006", "1004"], lows=["998", "996", "995", "997"], closes=["1000", "999", "998", "1000"])
    result = compute_price_path_outcome(obs, OutcomeHorizonLabel.PLUS_1H, candles, as_of=obs.generated_at + timedelta(hours=1))
    assert result.confirmation_outcome == ConfirmationOutcome.NOT_CONFIRMED
    assert result.invalidation_outcome == InvalidationOutcome.NOT_INVALIDATED


def test_confirmation_confirmed_when_the_opposing_level_is_broken_through() -> None:
    """The real, corrected semantics: a BULLISH thesis's recorded nearest
    RESISTANCE (the opposing obstacle) being broken through IS
    confirmation, matching every named pattern's own documented
    `confirm_if` text ("price holds beyond the nearby opposing level")."""
    obs = _observation(direction="BULLISH", spot="1000", breakeven=None, nearest_level_kind="resistance", nearest_level_value="1010")
    candles = _window_candles(obs, highs=["1005", "1015", "1025", "1022"], lows=["1000", "1005", "1015", "1018"], closes=["1003", "1012", "1022", "1020"])
    result = compute_price_path_outcome(obs, OutcomeHorizonLabel.PLUS_1H, candles, as_of=obs.generated_at + timedelta(hours=1))
    assert result.confirmation_outcome == ConfirmationOutcome.CONFIRMED


def test_confirmation_not_confirmed_when_the_opposing_level_holds() -> None:
    obs = _observation(direction="BULLISH", spot="1000", breakeven=None, nearest_level_kind="resistance", nearest_level_value="1030")
    candles = _window_candles(obs, highs=["1005", "1008", "1010", "1009"], lows=["1000", "1003", "1005", "1004"], closes=["1003", "1006", "1008", "1007"])
    result = compute_price_path_outcome(obs, OutcomeHorizonLabel.PLUS_1H, candles, as_of=obs.generated_at + timedelta(hours=1))
    assert result.confirmation_outcome == ConfirmationOutcome.NOT_CONFIRMED


def test_compute_all_horizons_returns_all_five_in_ascending_order() -> None:
    obs = _observation()
    results = compute_all_horizons(obs, [], as_of=obs.generated_at)
    assert [r.horizon for r in results] == [
        OutcomeHorizonLabel.PLUS_30M, OutcomeHorizonLabel.PLUS_1H, OutcomeHorizonLabel.PLUS_1D,
        OutcomeHorizonLabel.PLUS_3D, OutcomeHorizonLabel.PLUS_5D,
    ]
    assert all(r.data_sufficient is False for r in results)  # as_of == generated_at -- nothing has elapsed yet
