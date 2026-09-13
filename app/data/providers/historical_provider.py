"""Replay data provider.

Implements the same `MarketDataProvider`/`OptionChainProvider` Protocols as
any live provider, but reads from persisted, already-ingested data instead
of the network. This is what lets `replay/` (a later milestone) reuse the
entire pipeline unchanged — only the provider (and the scheduler) differ
between real-time and replay mode (ARCHITECTURE.md §19, Addendum A5).

Every method receives `as_of` and independently re-checks that nothing
returned exceeds it. The persistence layer already enforces this (see
`persistence/interfaces.py`), but this provider is exactly the boundary the
no-look-ahead safety tests target, so it asserts the contract itself too
rather than trusting the repository alone — a repository that ever violated
this contract is a bug that must surface loudly, not get silently filtered.
"""

from __future__ import annotations

from datetime import date, datetime

from app.data.providers.base import RawCandle, RawOptionChain, RawOptionLeg, RawQuote
from app.data.providers.exceptions import ProviderMalformedResponse, ProviderUnavailable
from app.data.providers.health import HealthStatus, ProviderHealth
from app.domain.market.models import ExchangeSegment, Timeframe
from app.persistence.interfaces import CandleRepository, OptionChainRepository
from app.utils.time import utc_now


class HistoricalProvider:
    """Read-only replay provider backed by persisted candles/option chains."""

    name = "historical"

    def __init__(self, *, candles: CandleRepository, option_chains: OptionChainRepository) -> None:
        self._candles = candles
        self._option_chains = option_chains

    async def get_ohlcv(
        self,
        *,
        security_id: str,
        exchange_segment: ExchangeSegment,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
        as_of: datetime,
    ) -> list[RawCandle]:
        stored = await self._candles.query(
            instrument_id=security_id, timeframe=timeframe, start=start, end=end, as_of=as_of
        )
        leaked = [c for c in stored if c.freshness.data_timestamp > as_of]
        if leaked:
            raise ProviderUnavailable(
                f"CandleRepository returned {len(leaked)} candle(s) beyond as_of={as_of.isoformat()} "
                "— this violates the no-future-data-leakage contract (Addendum A5)"
            )
        return [
            RawCandle(
                timestamp=c.freshness.data_timestamp,
                open=float(c.open),
                high=float(c.high),
                low=float(c.low),
                close=float(c.close),
                volume=c.volume,
                open_interest=c.open_interest,
            )
            for c in stored
        ]

    async def get_quote(self, *, security_id: str, exchange_segment: ExchangeSegment, as_of: datetime) -> RawQuote:
        # Quote persistence (QuoteRepository) isn't wired into this provider
        # yet in the Data Foundation milestone. Replay mode is expected to
        # derive an effective "quote" from the most recent candle close at
        # the orchestration layer rather than have this guess one.
        raise ProviderMalformedResponse(
            "HistoricalProvider.get_quote is not implemented in this milestone — replay mode "
            "derives an effective quote from the most recent candle at the orchestration layer"
        )

    async def health(self) -> ProviderHealth:
        return ProviderHealth(provider=self.name, status=HealthStatus.UP, checked_at=utc_now())

    async def get_chain(self, *, underlying: str, expiry: date, as_of: datetime) -> RawOptionChain:
        snapshot = await self._option_chains.latest(underlying=underlying, expiry=expiry, as_of=as_of)
        if snapshot is None:
            raise ProviderMalformedResponse(
                f"no persisted option chain snapshot for {underlying} {expiry.isoformat()} "
                f"at or before as_of={as_of.isoformat()}"
            )
        if snapshot.freshness.data_timestamp > as_of:
            raise ProviderUnavailable(
                "OptionChainRepository returned a snapshot beyond as_of "
                "— this violates the no-future-data-leakage contract (Addendum A5)"
            )
        strikes: dict[float, list[RawOptionLeg]] = {}
        for leg in snapshot.legs:
            strikes.setdefault(float(leg.strike), []).append(
                RawOptionLeg(
                    security_id=leg.security_id,
                    right=leg.right,
                    last_price=float(leg.last_price) if leg.last_price is not None else None,
                    bid_price=float(leg.bid_price) if leg.bid_price is not None else None,
                    ask_price=float(leg.ask_price) if leg.ask_price is not None else None,
                    volume=leg.volume,
                    open_interest=leg.open_interest,
                    previous_open_interest=None,
                    implied_volatility=float(leg.implied_volatility) if leg.implied_volatility is not None else None,
                    delta=float(leg.delta) if leg.delta is not None else None,
                    theta=float(leg.theta) if leg.theta is not None else None,
                    gamma=float(leg.gamma) if leg.gamma is not None else None,
                    vega=float(leg.vega) if leg.vega is not None else None,
                )
            )
        underlying_price = snapshot.underlying_last_price
        return RawOptionChain(
            underlying=snapshot.underlying,
            expiry=snapshot.expiry,
            underlying_last_price=float(underlying_price) if underlying_price is not None else None,
            strikes=strikes,
        )

    async def get_expiries(self, *, underlying: str, as_of: datetime) -> list[date]:
        # Not derivable from CandleRepository/OptionChainRepository alone
        # without an explicit persisted expiry list. Left unimplemented here
        # on purpose rather than guessed — a future OptionContractRepository
        # (ARCHITECTURE.md §7's `option_contracts` table) is the right home.
        raise ProviderMalformedResponse(
            "HistoricalProvider.get_expiries is not implemented in the Data Foundation milestone "
            "— requires an OptionContractRepository (ARCHITECTURE.md §7), not yet built"
        )
