"""Deterministic in-memory provider used by tests and by any code that needs
a `MarketDataProvider`/`OptionChainProvider` without a network call.

"Deterministic" here means: the same (security_id, timeframe, start, end)
always produces the same candle series, forever, in this process or any
other — generation is seeded from the request parameters, not from wall-clock
time or an unseeded RNG. This is what makes it safe as a shared test fixture
across `domain`/`engines` test suites (ARCHITECTURE.md §22).

This is a *provider*, not a synonym for "fake data anywhere" — it never
appears in `data/ingestion`'s real-time registry, only in tests and in
example/demo wiring.
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta

from app.data.providers.base import (
    RawCandle,
    RawOptionChain,
    RawOptionLeg,
    RawQuote,
)
from app.data.providers.health import ProviderHealth, ProviderHealthTracker
from app.domain.market.models import ExchangeSegment, OptionRight, Timeframe
from app.utils.time import utc_now

_TIMEFRAME_MINUTES: dict[Timeframe, int] = {
    Timeframe.M1: 1,
    Timeframe.M5: 5,
    Timeframe.M15: 15,
    Timeframe.M30: 30,
    Timeframe.H1: 60,
    Timeframe.D1: 24 * 60,
}


def _seed_for(*parts: object) -> int:
    return abs(hash(tuple(str(p) for p in parts))) % (2**31)


class MockMarketDataProvider:
    name = "mock"

    def __init__(self, *, base_price: float = 100.0) -> None:
        self._base_price = base_price
        self._health_tracker = ProviderHealthTracker(provider=self.name)
        self._health_tracker.record_success(at=utc_now(), latency_ms=0.0)

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
        step = timedelta(minutes=_TIMEFRAME_MINUTES[timeframe])
        rng = random.Random(_seed_for(security_id, timeframe.value))
        candles: list[RawCandle] = []
        cursor = start
        price = self._base_price
        while cursor < end and cursor <= as_of:
            drift = rng.uniform(-0.5, 0.5)
            open_price = price
            close_price = max(0.05, open_price + drift)
            high_price = max(open_price, close_price) + abs(rng.uniform(0, 0.3))
            low_price = min(open_price, close_price) - abs(rng.uniform(0, 0.3))
            volume = rng.randint(1_000, 50_000)
            candles.append(
                RawCandle(
                    timestamp=cursor,
                    open=round(open_price, 2),
                    high=round(high_price, 2),
                    low=round(max(0.01, low_price), 2),
                    close=round(close_price, 2),
                    volume=volume,
                    open_interest=None,
                )
            )
            price = close_price
            cursor += step
        return candles

    async def get_quote(self, *, security_id: str, exchange_segment: ExchangeSegment, as_of: datetime) -> RawQuote:
        rng = random.Random(_seed_for(security_id, "quote"))
        last_price = round(self._base_price + rng.uniform(-1, 1), 2)
        self._health_tracker.record_success(at=as_of, latency_ms=1.0)
        return RawQuote(
            security_id=security_id,
            last_price=last_price,
            previous_close=round(self._base_price, 2),
            volume=rng.randint(1_000, 100_000),
            open_interest=None,
            average_price=last_price,
        )

    async def health(self) -> ProviderHealth:
        return self._health_tracker.snapshot()


class MockOptionChainProvider:
    name = "mock"

    def __init__(self, *, strike_step: float = 50.0, num_strikes: int = 5) -> None:
        self._strike_step = strike_step
        self._num_strikes = num_strikes
        self._health_tracker = ProviderHealthTracker(provider=self.name)
        self._health_tracker.record_success(at=utc_now(), latency_ms=0.0)

    async def get_chain(self, *, underlying: str, expiry: date, as_of: datetime) -> RawOptionChain:
        rng = random.Random(_seed_for(underlying, expiry.isoformat()))
        # Keep the ATM strike comfortably above (num_strikes * strike_step) so
        # the generated strike window never goes non-positive.
        floor = (self._num_strikes + 2) * self._strike_step
        atm = floor + round(rng.uniform(0, 500) / self._strike_step) * self._strike_step
        strikes: dict[float, list[RawOptionLeg]] = {}
        for i in range(-self._num_strikes, self._num_strikes + 1):
            strike = atm + i * self._strike_step
            moneyness_penalty = abs(i) * 0.15
            ce = RawOptionLeg(
                right=OptionRight.CE,
                last_price=max(0.05, round(rng.uniform(5, 50) - moneyness_penalty * 10, 2)),
                bid_price=None,
                ask_price=None,
                volume=rng.randint(0, 20_000),
                open_interest=rng.randint(0, 500_000),
                previous_open_interest=rng.randint(0, 500_000),
                implied_volatility=round(rng.uniform(10, 30) + moneyness_penalty, 2),
                delta=round(max(0.01, 0.5 - i * 0.08), 4),
                theta=round(-abs(rng.uniform(1, 20)), 4),
                gamma=round(rng.uniform(0.0001, 0.01), 5),
                vega=round(rng.uniform(1, 20), 4),
            )
            pe = RawOptionLeg(
                right=OptionRight.PE,
                last_price=max(0.05, round(rng.uniform(5, 50) - moneyness_penalty * 10, 2)),
                bid_price=None,
                ask_price=None,
                volume=rng.randint(0, 20_000),
                open_interest=rng.randint(0, 500_000),
                previous_open_interest=rng.randint(0, 500_000),
                implied_volatility=round(rng.uniform(10, 30) + moneyness_penalty, 2),
                delta=round(min(-0.01, -0.5 + i * 0.08), 4),
                theta=round(-abs(rng.uniform(1, 20)), 4),
                gamma=round(rng.uniform(0.0001, 0.01), 5),
                vega=round(rng.uniform(1, 20), 4),
            )
            strikes[strike] = [ce, pe]

        self._health_tracker.record_success(at=as_of, latency_ms=2.0)
        return RawOptionChain(
            underlying=underlying,
            expiry=expiry,
            underlying_last_price=atm,
            strikes=strikes,
        )

    async def get_expiries(self, *, underlying: str, as_of: datetime) -> list[date]:
        # Deterministic: next 4 weekly Thursdays after as_of (illustrative only
        # — real expiry-day rules come from the scheduler's calendar, not here).
        expiries: list[date] = []
        cursor = as_of.date()
        while len(expiries) < 4:
            cursor += timedelta(days=1)
            if cursor.weekday() == 3:  # Thursday
                expiries.append(cursor)
        return expiries

    async def health(self) -> ProviderHealth:
        return self._health_tracker.snapshot()
