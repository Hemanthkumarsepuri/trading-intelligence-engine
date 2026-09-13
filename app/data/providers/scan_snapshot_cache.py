"""Per-scan reuse of quote/OHLCV snapshots.

Does not mix providers. Does not average prints. Keys include instrument,
timeframe, and calendar window so Nifty history fetched for relative
strength is not re-requested for every Stage-2 symbol in the same scan.
`as_of` is not part of the OHLCV key because Upstox historical candles are
fetched by calendar date range; the pipeline still bounds by `as_of`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from app.data.providers.base import RawCandle, RawQuote
from app.domain.market.models import ExchangeSegment, Timeframe
from app.utils.time import utc_now


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
