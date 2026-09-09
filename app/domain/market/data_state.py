"""Market-data state classification — answers, explicitly and auditably,
"how should this result be trusted right now": is it live-streaming, a
fresh snapshot, the market's latest-known data while closed, gone stale, or
built on too little history to mean anything.

Pure, deterministic: every input is caller-supplied (`data_age` is a
precomputed `timedelta`, never derived from a clock read in here — the
caller is responsible for computing it from an explicit `as_of` and the
data's own timestamp, exactly like every other no-look-ahead-respecting
function in this codebase).
"""

from __future__ import annotations

from datetime import date, timedelta
from enum import Enum

from app.domain.market.trading_calendar import (
    most_recent_trading_day_at_or_before,
    resolve_nse_holidays,
)


class MarketDataState(str, Enum):
    """Deliberately distinct from `IndicatorStatus`/`Setup.direction` —
    this describes the TRUSTWORTHINESS of the data a result is built on,
    never a trading interpretation.
    """

    LIVE_STREAMING = "LIVE_STREAMING"
    LIVE_SNAPSHOT = "LIVE_SNAPSHOT"
    MARKET_CLOSED_LATEST_DATA = "MARKET_CLOSED_LATEST_DATA"
    # Final Hardening Pass, Phase 2 -- distinct from MARKET_CLOSED_LATEST_DATA:
    # the real NSE pre-open call-auction window (Upstox's own
    # PRE_OPEN_START/PRE_OPEN_END exchange status), not plain after-hours
    # closure. Same trust level as MARKET_CLOSED_LATEST_DATA (the most
    # recent real trading day's data is still the correct technical
    # basis) -- this label exists purely so a user checking the terminal
    # during the pre-open auction is told that honestly, rather than
    # reading the generic "market closed" wording at a moment the market
    # is, in a real sense, already in its opening process.
    PRE_MARKET = "PRE_MARKET"
    STALE_DATA = "STALE_DATA"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    ERROR = "ERROR"


def classify_market_data_state(
    *,
    exchange_status_is_open: bool,
    is_live_stream: bool,
    data_age: timedelta,
    data_date: date,
    as_of_date: date,
    candles_available: int,
    minimum_candles: int,
    stale_threshold: timedelta,
    holidays: frozenset[date] | None = None,
    is_pre_market: bool = False,
) -> MarketDataState:
    """`PROVIDER_UNAVAILABLE`/`ERROR` are not produced here — those describe
    a call that failed outright, which this function is never reached for;
    the caller wraps a failed provider call with that state directly instead
    of calling this classifier at all.

    Precedence, most authoritative first: insufficient history always wins
    (a strategy result cannot exist regardless of how fresh the raw data
    is); then exchange status (closed dominates even fresh-looking data —
    a snapshot from a closed market must never be called "live"); then
    staleness; only once all of those pass does live-vs-snapshot matter.

    Sprint 3 — closes a real trust gap: previously, once
    `exchange_status_is_open` was False, this function returned
    `MARKET_CLOSED_LATEST_DATA` unconditionally, with NO staleness bound
    at all — a quote from three months ago would have read identically to
    one from five minutes after close. `data_date`/`as_of_date` (the real
    calendar dates the caller already has on hand alongside `data_age`,
    never a new fetch) let this function distinguish the two: the
    closed-market floor is the most recent REAL trading day strictly
    BEFORE `as_of_date` — reusing the exact same, already-authoritative
    `most_recent_trading_day_at_or_before()` the Daily Market Researcher's
    own Stage-1 screen already relies on (never a second, duplicate
    calendar implementation, never a flat hours/days tolerance). That
    one-day offset is deliberate: it makes the same rule correct for BOTH
    a post-close check ("today already closed, is today's own quote
    acceptable" — trivially yes) AND a pre-open check ("today hasn't
    opened yet, is Friday's quote acceptable on Monday morning" — yes,
    Friday >= Friday) without a separate PRE_OPEN vs NORMAL_CLOSE/
    CLOSING_*/UNKNOWN special case. A quote older than that floor now
    reads `STALE_DATA`, honestly, instead of the unconditional
    `MARKET_CLOSED_LATEST_DATA` it used to.
    """
    resolved = resolve_nse_holidays(holidays)
    if candles_available < minimum_candles:
        return MarketDataState.INSUFFICIENT_HISTORY
    if not exchange_status_is_open:
        expected_last_session = most_recent_trading_day_at_or_before(as_of_date - timedelta(days=1), resolved)
        if data_date < expected_last_session:
            return MarketDataState.STALE_DATA
        return MarketDataState.PRE_MARKET if is_pre_market else MarketDataState.MARKET_CLOSED_LATEST_DATA
    if data_age > stale_threshold:
        return MarketDataState.STALE_DATA
    if is_live_stream:
        return MarketDataState.LIVE_STREAMING
    return MarketDataState.LIVE_SNAPSHOT
