"""Phase 3 (Historical Intelligence + Early Opportunity Validation) --
a `provider` that satisfies `app.orchestration.options_intelligence_pipeline
.AnalysisProvider` from LOCALLY PERSISTED historical candles instead of a
live broker connection, so `analyze_symbol()`/`run_analysis()` can run
UNMODIFIED against a chosen past instant -- "one intelligence engine, two
inputs" (see that Protocol's own docstring), never a second analysis path.

WHAT IS AND ISN'T HONESTLY AVAILABLE HISTORICALLY, and how this class
handles each:

- OHLCV candles: real, from a `CandleRepository` this class is handed at
  construction (typically backfilled once from Upstox's real
  `/v3/historical-candle` endpoint -- see `scripts/fetch_upstox_historical.py`
  -- and read back through `JsonlCandleRepository`, which already enforces
  `data_timestamp <= as_of` -- see `persistence/interfaces.py`). This is
  the one stream Upstox genuinely offers for arbitrary past ranges.
- Quote (spot "now"): Upstox has no historical-tick endpoint, so this
  class RECONSTRUCTS a quote from the latest completed candle at or before
  `as_of` -- `last_price` = that candle's close, `previous_close` = the
  prior trading session's own last candle close. This is an honest
  approximation of "the last known price as of this instant," never a
  claim of a genuine intraday tick; `exchange_timestamp` is set to the
  source candle's own timestamp so downstream freshness/staleness grading
  reflects that truthfully.
- Option chain / news: Upstox has no historical option-chain snapshot API
  and no historical news archive, so both honestly raise
  `ProviderUnavailable` -- `analyze_symbol()` already tolerates either
  failing independently (see its own per-stage `try`/`except ProviderError`
  blocks) and correctly grades the resulting report as data-insufficient
  rather than pretending a chain/news stream existed. This is the same
  "never pretend unavailable derivative data existed historically"
  discipline the whole Phase-3 replay design commits to.
- Market status: Upstox's status endpoint answers "right now," which is
  meaningless for a past instant. This class instead derives OPEN/CLOSED
  deterministically from the NSE trading calendar and the fixed
  09:15-15:30 IST cash-session window -- a real, sourced fact, not a
  network call. Pre-open/closing-auction sub-states are not modelled
  (an explicit, documented simplification); every session-hours instant
  reads as `NORMAL_OPEN`.

This class is READ-ONLY like every other provider in this codebase, and is
never registered in the live provider registry (`data.ingestion`) -- it is
constructed only by `app.orchestration.historical_replay`.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from app.data.providers.base import (
    ProviderCapabilities,
    RawCandle,
    RawNewsItem,
    RawOptionChain,
    RawQuote,
)
from app.data.providers.exceptions import ProviderUnavailable
from app.data.providers.health import ProviderHealth, ProviderHealthTracker
from app.data.providers.upstox_provider import ExchangeStatus
from app.domain.market.models import Candle, ExchangeSegment, Timeframe
from app.domain.market.trading_calendar import is_trading_day, most_recent_trading_day_at_or_before
from app.persistence.interfaces import CandleRepository
from app.utils.time import IST, ensure_utc, to_ist

# The real, fixed NSE cash-session window (same fact already documented in
# `app.orchestration.audit_journal`'s own EOD-deadline constants) --
# duplicated here as its own small, named pair rather than importing a
# private constant from an unrelated orchestration module.
_SESSION_OPEN = time(9, 15)
_SESSION_CLOSE = time(15, 30)

# How far back to look for "the last candle before as_of" when
# reconstructing a quote -- generous enough to span a long weekend/holiday
# gap, never so wide it would risk pulling in a stale multi-week-old print
# without `get_quote`'s own caller ever seeing that distance (freshness
# grading downstream is computed from the REAL returned timestamp, not
# hidden by this window).
_QUOTE_LOOKBACK = timedelta(days=10)


def deterministic_exchange_status(as_of: datetime) -> ExchangeStatus:
    """The exchange status a historical instant WOULD have shown, computed
    from real calendar facts only -- never a network call, never a guess.
    See this module's own docstring for the documented simplification
    (no pre-open/closing-auction sub-states)."""
    ist = to_ist(as_of)
    if not is_trading_day(ist.date()):
        return ExchangeStatus.NORMAL_CLOSE
    if _SESSION_OPEN <= ist.time() < _SESSION_CLOSE:
        return ExchangeStatus.NORMAL_OPEN
    return ExchangeStatus.NORMAL_CLOSE


class HistoricalReplayProvider:
    """See module docstring. `advance_to()` MUST be called with the
    replay's current `as_of` before every `analyze_symbol()`/`run_analysis()`
    call -- unlike `get_quote()`/`get_ohlcv()` (which the pipeline already
    calls with an explicit `as_of`), `get_market_status()`/`get_quotes()`
    mirror the LIVE provider's own signature (no `as_of` parameter, because
    a live call means "right now") and so need this instance's own
    replay-clock state instead. Raises `RuntimeError` if used before
    `advance_to()` is ever called -- a caller-ordering bug, never silently
    defaulted to the real wall clock (that would be a live-data leak into
    a replay run).
    """

    name = "historical_replay"
    # Phase 3 gap-closure -- the one, explicit, structural declaration
    # that lets `analyze_symbol()` distinguish "this provider was never
    # going to have chain/futures/news history for this instant" from "a
    # live fetch genuinely failed." Candles remain the one real capability
    # this provider has (subject to whatever a caller has backfilled).
    capabilities = ProviderCapabilities(
        historical_candles=True, historical_option_chain=False, historical_futures=False, historical_news=False,
    )

    def __init__(self, *, candles: CandleRepository) -> None:
        self._candles = candles
        self._as_of: datetime | None = None

    def advance_to(self, as_of: datetime) -> None:
        self._as_of = ensure_utc(as_of)

    def _require_as_of(self) -> datetime:
        if self._as_of is None:
            raise RuntimeError(
                "HistoricalReplayProvider.advance_to() was never called -- "
                "a replay caller must set the replay clock before analyze_symbol()"
            )
        return self._as_of

    async def get_market_status(self, *, exchange: str) -> ExchangeStatus:
        return deterministic_exchange_status(self._require_as_of())

    async def get_ohlcv(
        self, *, security_id: str, exchange_segment: ExchangeSegment, timeframe: Timeframe,
        start: datetime, end: datetime, as_of: datetime,
    ) -> list[RawCandle]:
        candles = await self._candles.query(instrument_id=security_id, timeframe=timeframe, start=start, end=end, as_of=as_of)
        return [
            RawCandle(
                timestamp=c.freshness.data_timestamp, open=float(c.open), high=float(c.high), low=float(c.low),
                close=float(c.close), volume=c.volume, open_interest=c.open_interest,
            )
            for c in candles
        ]

    async def _latest_candle(self, *, security_id: str, timeframe: Timeframe, as_of: datetime) -> Candle | None:
        candles = await self._candles.query(
            instrument_id=security_id, timeframe=timeframe, start=as_of - _QUOTE_LOOKBACK, end=as_of + timedelta(seconds=1),
            as_of=as_of,
        )
        return candles[-1] if candles else None

    async def get_quote(self, *, security_id: str, exchange_segment: ExchangeSegment, as_of: datetime) -> RawQuote:
        """Reconstructed from the latest completed M15 candle at or before
        `as_of` -- see module docstring. Raises `ProviderUnavailable` when
        the local candle store has nothing that far back, exactly like a
        real provider that genuinely has no data for the request (never a
        fabricated price)."""
        latest = await self._latest_candle(security_id=security_id, timeframe=Timeframe.M15, as_of=as_of)
        if latest is None:
            raise ProviderUnavailable(
                f"no locally persisted historical candle for {security_id} at or before {as_of.isoformat()}"
            )
        previous_close = await self._previous_session_close(security_id=security_id, as_of=as_of, current_candle=latest)
        return RawQuote(
            security_id=security_id, last_price=float(latest.close), previous_close=previous_close,
            volume=latest.volume, exchange_timestamp=latest.freshness.data_timestamp,
        )

    async def _previous_session_close(self, *, security_id: str, as_of: datetime, current_candle: Candle) -> float | None:
        """The prior trading session's own last M15 candle close --
        `None`, honestly, when the local store doesn't reach back that
        far (never today's own open, and never a guess)."""
        current_session = to_ist(current_candle.freshness.data_timestamp).date()
        prior_session = most_recent_trading_day_at_or_before(current_session - timedelta(days=1))
        window_start = datetime.combine(prior_session, _SESSION_OPEN, tzinfo=IST)
        window_end = datetime.combine(prior_session, _SESSION_CLOSE, tzinfo=IST) + timedelta(minutes=1)
        prior_candles = await self._candles.query(
            instrument_id=security_id, timeframe=Timeframe.M15, start=ensure_utc(window_start), end=ensure_utc(window_end),
            as_of=as_of,
        )
        if not prior_candles:
            return None
        return float(prior_candles[-1].close)

    async def get_quotes(self, security_ids: list[str]) -> dict[str, RawQuote]:
        """Batched context quotes (sector/index relative-strength) --
        honestly omits any id the local store has nothing for, rather than
        raising: relative-strength context degrading to UNKNOWN for a
        missing peer is the same graceful-degradation behavior the live
        provider already produces for one bad symbol in a batch."""
        as_of = self._require_as_of()
        result: dict[str, RawQuote] = {}
        for security_id in security_ids:
            try:
                result[security_id] = await self.get_quote(security_id=security_id, exchange_segment=ExchangeSegment.NSE_EQ, as_of=as_of)
            except ProviderUnavailable:
                continue
        return result

    async def get_news(self, *, instrument_key: str) -> list[RawNewsItem]:
        raise ProviderUnavailable(
            "no historical news archive is available for replay -- Upstox's news endpoint is live-only "
            "(Phase 3 -- see docs/HISTORICAL_REPLAY.md); this is reported as NEWS_DATA_UNAVAILABLE, never as NO_NEWS"
        )

    async def get_chain(self, *, underlying: str, expiry: date, as_of: datetime) -> RawOptionChain:
        raise ProviderUnavailable(
            "no historical option-chain snapshot is available for replay -- Upstox has no historical "
            "option-chain endpoint, and this instant predates any locally captured live snapshot "
            "(Phase 3 -- see docs/HISTORICAL_REPLAY.md); derivatives evidence is honestly INSUFFICIENT for this replay"
        )

    async def health(self) -> ProviderHealth:
        return ProviderHealthTracker(provider=self.name).snapshot()
