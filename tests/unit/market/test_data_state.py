from __future__ import annotations

from datetime import date, timedelta

from app.domain.market.data_state import MarketDataState, classify_market_data_state

STALE = timedelta(hours=1)
_FRIDAY = date(2026, 8, 28)
_SATURDAY = date(2026, 8, 29)
_SUNDAY = date(2026, 8, 30)
_MONDAY = date(2026, 8, 31)


def _classify(**overrides: object) -> MarketDataState:
    defaults: dict[str, object] = {
        "exchange_status_is_open": True,
        "is_live_stream": False,
        "data_age": timedelta(seconds=5),
        "data_date": _FRIDAY,
        "as_of_date": _FRIDAY,
        "candles_available": 100,
        "minimum_candles": 50,
        "stale_threshold": STALE,
    }
    defaults.update(overrides)
    return classify_market_data_state(**defaults)  # type: ignore[arg-type]


def test_insufficient_history_wins_over_everything_else() -> None:
    state = _classify(candles_available=10, exchange_status_is_open=True, is_live_stream=True, data_age=timedelta(0))
    assert state == MarketDataState.INSUFFICIENT_HISTORY


def test_market_closed_wins_over_fresh_looking_data() -> None:
    state = _classify(exchange_status_is_open=False, data_age=timedelta(seconds=1))
    assert state == MarketDataState.MARKET_CLOSED_LATEST_DATA


def test_pre_market_distinct_from_plain_market_closed() -> None:
    """Final Hardening Pass, Phase 2 -- the real NSE pre-open auction
    window reads PRE_MARKET, not the generic MARKET_CLOSED_LATEST_DATA,
    even though the underlying data-trust rule (most recent real trading
    day's data is still the correct basis) is identical."""
    state = _classify(exchange_status_is_open=False, data_age=timedelta(seconds=1), is_pre_market=True)
    assert state == MarketDataState.PRE_MARKET


def test_pre_market_defaults_to_false_never_changes_existing_behavior() -> None:
    """Backward compatibility: every existing caller that never passes
    `is_pre_market` gets the exact same MARKET_CLOSED_LATEST_DATA as before."""
    state = _classify(exchange_status_is_open=False, data_age=timedelta(seconds=1))
    assert state == MarketDataState.MARKET_CLOSED_LATEST_DATA


def test_pre_market_still_stale_when_data_predates_the_last_real_session() -> None:
    """Staleness still wins over the pre-market label -- PRE_MARKET is not
    a way to bypass the real staleness check."""
    state = _classify(
        exchange_status_is_open=False, is_pre_market=True, data_date=date(2026, 8, 20), as_of_date=_MONDAY,
    )
    assert state == MarketDataState.STALE_DATA


def test_stale_data_when_market_open_but_data_old() -> None:
    state = _classify(exchange_status_is_open=True, data_age=timedelta(hours=2))
    assert state == MarketDataState.STALE_DATA


def test_live_streaming_when_open_fresh_and_streaming() -> None:
    state = _classify(exchange_status_is_open=True, is_live_stream=True, data_age=timedelta(seconds=1))
    assert state == MarketDataState.LIVE_STREAMING


def test_live_snapshot_when_open_fresh_but_not_streaming() -> None:
    state = _classify(exchange_status_is_open=True, is_live_stream=False, data_age=timedelta(seconds=1))
    assert state == MarketDataState.LIVE_SNAPSHOT


def test_exact_stale_threshold_boundary_is_not_stale() -> None:
    state = _classify(data_age=STALE)  # exactly at the threshold, not beyond it
    assert state != MarketDataState.STALE_DATA


def test_just_beyond_stale_threshold_is_stale() -> None:
    state = _classify(data_age=STALE + timedelta(seconds=1))
    assert state == MarketDataState.STALE_DATA


def test_exact_minimum_candles_boundary_is_sufficient() -> None:
    state = _classify(candles_available=50, minimum_candles=50)
    assert state != MarketDataState.INSUFFICIENT_HISTORY


# ============================================================
# Sprint 3 -- closes the real gap found live: closed-market data used to
# read MARKET_CLOSED_LATEST_DATA unconditionally, with NO staleness bound
# at all. Now bounded by the real, weekend/holiday-aware trading calendar
# (never a flat hours/days tolerance) -- see
# `most_recent_trading_day_at_or_before()`.
# ============================================================


def test_closed_market_same_day_quote_is_valid_historical_data() -> None:
    """A quote from earlier the SAME real trading day, well beyond the
    live `stale_threshold`, is still valid once the market is confirmed
    closed -- the whole point of `MARKET_CLOSED_LATEST_DATA`."""
    state = _classify(
        exchange_status_is_open=False, data_age=timedelta(hours=3),
        data_date=_FRIDAY, as_of_date=_FRIDAY,
    )
    assert state == MarketDataState.MARKET_CLOSED_LATEST_DATA


def test_closed_market_quote_from_months_ago_is_stale_not_market_closed() -> None:
    """The actual real defect this sprint fixes: a quote from three months
    ago must never read identically to one from five minutes after close."""
    three_months_ago = date(2026, 5, 28)
    state = _classify(exchange_status_is_open=False, data_date=three_months_ago, as_of_date=_FRIDAY)
    assert state == MarketDataState.STALE_DATA


def test_closed_market_across_a_weekend_still_valid_historical_data() -> None:
    """The real originally-reported bug scenario, generalized to this
    shared classifier: a Monday pre-open check must accept Friday's real
    last-session quote -- weekend-aware, not a flat day-count."""
    state = _classify(exchange_status_is_open=False, data_date=_FRIDAY, as_of_date=_MONDAY)
    assert state == MarketDataState.MARKET_CLOSED_LATEST_DATA

    # Confirms weekend-awareness specifically -- a flat "1 day" tolerance
    # would have wrongly rejected this; a flat "3+ day" tolerance would
    # have wrongly accepted the months-old case above. Only the real
    # calendar gets both right.
    state_saturday_asof = _classify(exchange_status_is_open=False, data_date=_FRIDAY, as_of_date=_SATURDAY)
    assert state_saturday_asof == MarketDataState.MARKET_CLOSED_LATEST_DATA
    state_sunday_asof = _classify(exchange_status_is_open=False, data_date=_FRIDAY, as_of_date=_SUNDAY)
    assert state_sunday_asof == MarketDataState.MARKET_CLOSED_LATEST_DATA


def test_closed_market_quote_predating_the_last_real_session_is_stale() -> None:
    """Bounded, never unlimited -- a quote from BEFORE the last real
    trading session (Wednesday, skipping Thursday's real session, for a
    Friday `as_of`) is still genuinely stale."""
    wednesday = date(2026, 8, 26)
    state = _classify(exchange_status_is_open=False, data_date=wednesday, as_of_date=_FRIDAY)
    assert state == MarketDataState.STALE_DATA


def test_insufficient_history_still_wins_even_with_a_stale_closed_market_quote() -> None:
    """Precedence is unchanged -- insufficient history is checked first,
    regardless of the new closed-market staleness bound."""
    three_months_ago = date(2026, 5, 28)
    state = _classify(
        candles_available=10, exchange_status_is_open=False, data_date=three_months_ago, as_of_date=_FRIDAY,
    )
    assert state == MarketDataState.INSUFFICIENT_HISTORY
