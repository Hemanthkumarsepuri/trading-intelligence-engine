"""Per-scan reuse of quote/OHLCV snapshots.

Does not mix providers. Does not average prints. Keys include instrument,
timeframe, and calendar window so Nifty history fetched for relative
strength is not re-requested for every Stage-2 symbol in the same scan.
`as_of` is not part of the OHLCV key because Upstox historical candles are
fetched by calendar date range; the pipeline still bounds by `as_of`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TypeVar, cast

from app.data.providers.base import RawCandle, RawQuote
from app.domain.market.models import ExchangeSegment, Timeframe
from app.utils.time import utc_now

_T = TypeVar("_T")


@dataclass(frozen=True)
class CachedSnapshot:
    """Identity of one reused provider snapshot -- never mixed across providers."""

    provider: str
    received_at: datetime
    market_timestamp: datetime | None
    instrument: str
    data_type: str
    validity: str


class ScanSnapshotCache:
    """Delegates every unknown attribute to the inner provider."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self._ohlcv: dict[tuple[str, str, str, str], list[RawCandle]] = {}
        self._quotes: dict[tuple[str, str, str], RawQuote] = {}
        self.hits: int = 0
        self.misses: int = 0
        self.last_snapshot: CachedSnapshot | None = None
        # Release gate (Section 7) -- in-flight/complete fetches shared
        # within ONE wall-clock UTC minute. Measured on a real 80-symbol
        # scan: `market/status/NSE` was requested 81 times and the
        # identical market-wide context batches (NIFTY/BANKNIFTY/VIX,
        # USD/INR, crude) once per symbol. A task (not a value) is stored
        # so concurrent Stage-2 symbols missing at the same moment share
        # ONE request instead of racing. Failures are never cached.
        self._minute_tasks: dict[tuple[object, ...], asyncio.Task[Any]] = {}

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def _note(self, *, hit: bool, instrument: str, data_type: str, market_timestamp: datetime | None, validity: str) -> None:
        if hit:
            self.hits += 1
        else:
            self.misses += 1
        provider = str(getattr(self._inner, "name", "unknown"))
        self.last_snapshot = CachedSnapshot(
            provider=provider,
            received_at=utc_now(),
            market_timestamp=market_timestamp,
            instrument=instrument,
            data_type=data_type,
            validity=validity,
        )

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
        key = (security_id, timeframe.value, start.date().isoformat(), end.date().isoformat())
        cached = self._ohlcv.get(key)
        if cached is not None:
            ts = cached[-1].timestamp if cached else None
            self._note(
                hit=True, instrument=security_id, data_type="ohlcv",
                market_timestamp=ts, validity="same calendar window this scan",
            )
            return cached
        rows = cast(
            list[RawCandle],
            await self._inner.get_ohlcv(
                security_id=security_id,
                exchange_segment=exchange_segment,
                timeframe=timeframe,
                start=start,
                end=end,
                as_of=as_of,
            ),
        )
        self._ohlcv[key] = rows
        ts = rows[-1].timestamp if rows else None
        self._note(
            hit=False, instrument=security_id, data_type="ohlcv",
            market_timestamp=ts, validity="same calendar window this scan",
        )
        return rows

    async def get_quote(
        self,
        *,
        security_id: str,
        exchange_segment: ExchangeSegment,
        as_of: datetime,
    ) -> RawQuote:
        key = (security_id, exchange_segment.value, str(int(as_of.timestamp() // 60)))
        cached = self._quotes.get(key)
        if cached is not None:
            self._note(
                hit=True, instrument=security_id, data_type="quote",
                market_timestamp=None, validity="same UTC minute this scan",
            )
            return cached
        quote = cast(
            RawQuote,
            await self._inner.get_quote(
                security_id=security_id, exchange_segment=exchange_segment, as_of=as_of,
            ),
        )
        self._quotes[key] = quote
        self._note(
            hit=False, instrument=security_id, data_type="quote",
            market_timestamp=quote.exchange_timestamp, validity="same UTC minute this scan",
        )
        return quote

    @staticmethod
    def _wall_minute() -> str:
        # Wall clock, not a caller's `as_of`: these endpoints return
        # "now" regardless of any as_of, so the only honest freshness
        # boundary is real elapsed time.
        return str(int(utc_now().timestamp() // 60))

    async def _shared_within_minute(
        self, key: tuple[object, ...], fetch: Callable[[], Awaitable[_T]], *, instrument: str, data_type: str,
    ) -> _T:
        full_key = (*key, self._wall_minute())
        task = self._minute_tasks.get(full_key)
        hit = task is not None
        if task is None:
            task = asyncio.ensure_future(fetch())
            self._minute_tasks[full_key] = task
        try:
            result = await asyncio.shield(task)
        except BaseException:
            if self._minute_tasks.get(full_key) is task:
                del self._minute_tasks[full_key]
            raise
        self._note(hit=hit, instrument=instrument, data_type=data_type, market_timestamp=None, validity="same UTC minute this scan")
        return cast(_T, result)

    async def get_market_status(self, *, exchange: str = "NSE") -> Any:
        return await self._shared_within_minute(
            ("market_status", exchange), lambda: self._inner.get_market_status(exchange=exchange),
            instrument=exchange, data_type="market_status",
        )

    async def get_quotes(self, security_ids: list[str]) -> dict[str, RawQuote]:
        # Keyed on the exact ordered key list: only a byte-identical batch
        # is shared, so a per-symbol batch (e.g. that symbol's futures
        # quote) is never answered from another symbol's request. The
        # returned dict is copied so no caller can mutate a shared result.
        result = await self._shared_within_minute(
            ("quotes", tuple(security_ids)), lambda: self._inner.get_quotes(list(security_ids)),
            instrument=",".join(security_ids), data_type="quotes",
        )
        return dict(cast(dict[str, RawQuote], result))
