"""Process-wide rolling ledger of real Upstox requests, per API.

Release gate (Section 8) -- why this exists. Once Stage 2 became cheap
enough to deep-analyze EVERY Stage-1 candidate (measured on a live scan,
16 Sep 2026: 193/193 candidates in 103.7 s at concurrency 6), the binding
provider constraint stopped being latency and became Upstox's published
rate limits, which are enforced **per API, per user**
(upstox.com/developer/api-documentation/rate-limiting, read 16 Sep 2026):

    50 requests / second    500 requests / minute    2000 requests / 30 minutes

One full scan measured 424 `market-quote/quotes` and 386 `option/chain`
requests. The per-second and per-minute windows are comfortably respected
by the fixed concurrency of 6, but the 30-minute window is NOT
automatically safe: roughly five full scans inside half an hour would
exhaust the quotes budget and turn the scanner into a source of HTTP 429s.

So capacity is decided by measurement of what this process has actually
spent, never by optimism: `stage_two_capacity()` returns how many Stage-2
analyses still fit inside every API's remaining 30-minute budget (after a
reserve for the interactive dashboard). Candidates beyond that are
deferred, visibly, with their own reason -- never silently dropped, and
never analysed on a budget that would trip the provider.

Honest limits of this ledger: it counts requests made by THIS process
only. Requests made with the same token elsewhere (another process, the
Upstox web app) are invisible to it, which is why a reserve is held back.
It counts attempts, including failed ones, because the provider does.
"""

from __future__ import annotations

import re
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping

UPSTOX_PER_API_LIMIT_30_MIN = 2000
WINDOW_SECONDS = 30 * 60

# Held back for the interactive dashboard (manual analyses, symbol pages,
# health checks) and for traffic this process cannot see.
RESERVED_REQUESTS_PER_API = 300

# Conservative per-Stage-2-symbol request cost per API, rounded UP from the
# live 193-symbol measurement (quotes 424 -> 2.2/symbol, chain 386 -> 2.0,
# candles 193 -> 1.0, news 193 -> 1.0). Rounded up so an estimate can only
# defer early, never overspend.
STAGE_TWO_COST_PER_SYMBOL: Mapping[str, int] = {
    "/v2/market-quote/quotes": 3,
    "/v2/option/chain": 2,
    "/v3/historical-candle": 2,
    "/v2/news": 1,
}

_DYNAMIC_SEGMENT = re.compile(r"^(?:NSE|BSE|MCX|NCD|BCD)_[A-Z]+(?:%7C|\|).*$|^\d{4}-\d{2}-\d{2}$|^\d+$")


def api_family(path: str) -> str:
    """The rate-limited API a request path belongs to -- the path with its
    instrument keys, dates and numeric parameters removed, then trimmed to
    the endpoint itself (`/v3/historical-candle/<key>/minutes/15/...` ->
    `/v3/historical-candle`)."""
    parts = [p for p in path.split("?")[0].split("/") if p]
    kept: list[str] = []
    for part in parts:
        if _DYNAMIC_SEGMENT.match(part):
            break
        kept.append(part)
    if len(kept) >= 2 and kept[0] in {"v2", "v3"} and kept[1] == "historical-candle":
        kept = kept[:2]
    return "/" + "/".join(kept)


class RequestLedger:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic, window_seconds: float = WINDOW_SECONDS) -> None:
        self._clock = clock
        self._window = window_seconds
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def record(self, path: str) -> None:
        now = self._clock()
        with self._lock:
            self._events.setdefault(api_family(path), deque()).append(now)

    def reset(self) -> None:
        with self._lock:
            self._events.clear()

    def used(self, family: str) -> int:
        cutoff = self._clock() - self._window
        with self._lock:
            events = self._events.get(family)
            if events is None:
                return 0
            while events and events[0] <= cutoff:
                events.popleft()
            return len(events)

    def stage_two_capacity(
        self, *, limit: int = UPSTOX_PER_API_LIMIT_30_MIN, reserve: int = RESERVED_REQUESTS_PER_API,
        cost_per_symbol: Mapping[str, int] = STAGE_TWO_COST_PER_SYMBOL,
    ) -> int:
        """How many Stage-2 symbol analyses still fit inside EVERY API's
        remaining 30-minute budget -- the minimum across APIs, never below
        zero."""
        capacity: int | None = None
        for family, cost in cost_per_symbol.items():
            remaining = limit - reserve - self.used(family)
            fits = max(0, remaining // cost) if cost > 0 else remaining
            capacity = fits if capacity is None else min(capacity, fits)
        return max(0, capacity or 0)


UPSTOX_REQUEST_LEDGER = RequestLedger()
