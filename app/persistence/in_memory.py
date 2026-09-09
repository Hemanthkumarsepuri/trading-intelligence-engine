"""In-memory implementations of `persistence.interfaces`.

Used by tests, by `HistoricalProvider` in this milestone, and as the
reference implementation of the repository contracts before a real
SQLAlchemy/Postgres backend lands. Not durable — nothing here survives a
process restart. Swapping this for a Postgres-backed implementation later is
an infrastructure change behind the same Protocols; no caller outside
`persistence/` should need to change.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from app.domain.market.models import Candle, OptionChainSnapshot, Quote, Timeframe
from app.domain.options.models import IvObservation


class InMemoryCandleRepository:
    def __init__(self) -> None:
        self._candles: list[Candle] = []

    async def save(self, candle: Candle) -> None:
        self._candles.append(candle)

    async def query(
        self,
        *,
        instrument_id: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
        as_of: datetime,
    ) -> list[Candle]:
        results = [
            candle
            for candle in self._candles
            if candle.instrument_id == instrument_id
            and candle.timeframe == timeframe
            and start <= candle.freshness.data_timestamp < end
            and candle.freshness.data_timestamp <= as_of
        ]
        return sorted(results, key=lambda c: c.freshness.data_timestamp)


class InMemoryQuoteRepository:
    def __init__(self) -> None:
        self._quotes: list[Quote] = []

    async def save(self, quote: Quote) -> None:
        self._quotes.append(quote)

    async def latest(self, *, instrument_id: str, as_of: datetime) -> Quote | None:
        candidates = [
            quote
            for quote in self._quotes
            if quote.instrument_id == instrument_id and quote.freshness.data_timestamp <= as_of
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda q: q.freshness.data_timestamp)


class InMemoryOptionChainRepository:
    def __init__(self) -> None:
        self._snapshots: list[OptionChainSnapshot] = []

    async def save(self, snapshot: OptionChainSnapshot) -> None:
        self._snapshots.append(snapshot)

    async def latest(self, *, underlying: str, expiry: date, as_of: datetime) -> OptionChainSnapshot | None:
        candidates = [
            snapshot
            for snapshot in self._snapshots
            if snapshot.underlying == underlying and snapshot.expiry == expiry and snapshot.freshness.data_timestamp <= as_of
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda s: s.freshness.data_timestamp)

    async def query_range(
        self, *, underlying: str, expiry: date, start: datetime, end: datetime, as_of: datetime
    ) -> list[OptionChainSnapshot]:
        results = [
            snapshot
            for snapshot in self._snapshots
            if snapshot.underlying == underlying
            and snapshot.expiry == expiry
            and start <= snapshot.freshness.data_timestamp <= end
            and snapshot.freshness.data_timestamp <= as_of
        ]
        return sorted(results, key=lambda s: s.freshness.data_timestamp)


class InMemoryIvObservationRepository:
    def __init__(self) -> None:
        self._observations: list[IvObservation] = []

    async def save(self, observation: IvObservation) -> None:
        self._observations.append(observation)

    async def query_history(self, *, underlying: str, as_of: datetime, lookback: timedelta) -> list[IvObservation]:
        start = as_of - lookback
        results = [
            obs
            for obs in self._observations
            if obs.underlying == underlying and start <= obs.freshness.data_timestamp <= as_of
        ]
        return sorted(results, key=lambda o: o.freshness.data_timestamp)
