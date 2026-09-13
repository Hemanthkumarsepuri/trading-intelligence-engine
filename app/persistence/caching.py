"""95% sprint, Sprint 2b -- a read-through, in-memory CACHE wrapper around
any real `CandleRepository`, scoped to ONE caller-held instance (in
practice, one replay run -- see `app.orchestration.historical_replay`).

The problem this fixes: `JsonlCandleRepository.query()` re-reads and
re-parses its ENTIRE backing file on every single call (documented as
its own known trade-off in `persistence/jsonl_file.py`'s module
docstring). A replay walks dozens of bars in one session, and EACH bar's
`analyze_symbol()` call issues at least two `query()`-family calls for
the SAME (instrument_id, timeframe) -- turning what should be one real
file read into dozens. This wrapper reads the underlying repository
ONCE per (instrument_id, timeframe) pair (via its own real `query()`,
with a deliberately wide bound -- see `_WIDE_START`/`_WIDE_END` below),
caches the parsed `Candle` list in memory, and serves every subsequent
call for that same key by filtering the cached list in Python -- no
further file I/O.

CORRECTNESS, not just speed: no-lookahead is enforced by the SAME
filtering logic as before (`start <= data_timestamp < end` and
`data_timestamp <= as_of`), applied fresh on every call against the
cached list -- caching WHEN the data was read never changes WHAT a
given `as_of` is allowed to see. The wide bound used to populate the
cache is a real, honest superset (everything currently in the backing
store for that key); it is never itself returned to a caller -- every
call still narrows to its own real `start`/`end`/`as_of`. `save()`
still writes through to the real repository and invalidates that key's
cache entry, so a mixed read/write caller (not how replay uses this
today, but kept correct regardless) never serves stale data.

Deliberately NOT a general-purpose cache: no TTL, no eviction, no
cross-process sharing, no Redis. One process, one replay run, freed with
the process -- exactly Section 10's own instruction ("in-memory caching
where safe... do NOT introduce Redis/PostgreSQL/distributed storage").
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.market.models import Candle, Timeframe
from app.persistence.interfaces import CandleRepository

# A real, honest superset covering any date this codebase's real data
# could ever plausibly hold (NSE has existed since long after 1990; this
# system's own `data/` never holds future-dated candles) -- never
# returned to a caller directly, only used to populate the cache.
_WIDE_START = datetime(1990, 1, 1, tzinfo=UTC)
_WIDE_END = datetime(2100, 1, 1, tzinfo=UTC)


class CachedCandleRepository:
    """See module docstring. Implements the same `CandleRepository`
    Protocol (`persistence.interfaces`) as the repository it wraps, so it
    is a drop-in substitute anywhere one is accepted."""

    def __init__(self, inner: CandleRepository) -> None:
        self._inner = inner
        self._cache: dict[tuple[str, Timeframe], list[Candle]] = {}

    async def save(self, candle: Candle) -> None:
        await self._inner.save(candle)
        self._cache.pop((candle.instrument_id, candle.timeframe), None)

    async def _all_for(self, instrument_id: str, timeframe: Timeframe) -> list[Candle]:
        key = (instrument_id, timeframe)
        cached = self._cache.get(key)
        if cached is None:
            cached = await self._inner.query(
                instrument_id=instrument_id, timeframe=timeframe, start=_WIDE_START, end=_WIDE_END, as_of=_WIDE_END,
            )
            self._cache[key] = cached
        return cached

    async def query(
        self, *, instrument_id: str, timeframe: Timeframe, start: datetime, end: datetime, as_of: datetime,
    ) -> list[Candle]:
        candles = await self._all_for(instrument_id, timeframe)
        return [c for c in candles if start <= c.freshness.data_timestamp < end and c.freshness.data_timestamp <= as_of]
