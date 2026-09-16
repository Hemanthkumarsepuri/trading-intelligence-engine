"""Memoized lookup indices over the Upstox instrument master.

Final release gate (Section 7) -- a measured fix, not a guess. Profiling a
single real replay session (RELIANCE, 25 bars) showed ~52 s of ~55 s of
actual compute spent re-scanning this one static list:

    resolve_symbol       101 calls   30.98 s   (0.307 s/call)
    list_expiries         50 calls   13.92 s   (0.278 s/call)
    _underlying      1,827,875 calls  7.16 s
    dict.get        17,431,883 calls 12.80 s
    str.upper        9,850,461 calls  5.60 s

The master holds ~29,713 `NSE_FO` rows plus the equity/index segments, and
every `resolve_symbol()`/`list_expiries()` call walked ALL of them,
calling `.strip().upper()` on a field of each row, every time. Each
`analyze_symbol()` does several such lookups, so this cost the LIVE scan
too -- it was simply hidden behind the much larger option-chain
repository cost until that was fixed.

The master is a static reference list loaded once at process startup and
never mutated, so the derived indices below are built once per master and
reused. They are pure restructurings of rows the caller already handed
in: no row is filtered out, reordered within its own bucket, or altered,
so every consumer's own predicate still decides the final answer exactly
as before.

Cache identity: entries are keyed by `id(master)` but validated with an
`is` check against a STRONG reference held alongside. Holding that
reference means the id cannot be recycled by a different object while the
entry lives, so a stale index can never be served for a different master.
Only the most recent master is retained.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

_Row = dict[str, object]
_Master = Sequence[_Row]


class _MasterIndex:
    """Every derived index for ONE master, built lazily and independently
    -- a caller that only resolves symbols never pays to index the F&O
    segment, and vice versa."""

    def __init__(self, master: _Master) -> None:
        self.master = master
        self._by_trading_symbol: dict[str, list[_Row]] | None = None
        self._fo_by_underlying: dict[str, list[_Row]] | None = None

    def by_trading_symbol(self) -> dict[str, list[_Row]]:
        """Canonical (stripped, upper-cased) `trading_symbol` -> its rows,
        in the master's own original order so any caller tie-break over
        `matches` behaves exactly as it did over a linear scan."""
        if self._by_trading_symbol is None:
            index: dict[str, list[_Row]] = {}
            for entry in self.master:
                # No row is skipped -- not even an empty key -- so a lookup
                # returns exactly the rows a linear equality scan would.
                key = str(entry.get("trading_symbol", "")).strip().upper()
                index.setdefault(key, []).append(entry)
            self._by_trading_symbol = index
        return self._by_trading_symbol

    def fo_by_underlying(self) -> dict[str, list[_Row]]:
        """Canonical `underlying_symbol` -> its `NSE_FO` rows, in the
        master's own original order. Rows outside the `NSE_FO` segment are
        excluded, which is exactly the condition every caller of this
        index already applies itself."""
        if self._fo_by_underlying is None:
            index: dict[str, list[_Row]] = {}
            for entry in self.master:
                if entry.get("segment") != "NSE_FO":
                    continue
                key = str(entry.get("underlying_symbol", "")).strip().upper()
                index.setdefault(key, []).append(entry)
            self._fo_by_underlying = index
        return self._fo_by_underlying


# Keyed by id(master); the tuple's first element is a STRONG reference to
# that same master, so the id cannot be reused while this entry lives.
_CACHE: dict[int, tuple[Any, _MasterIndex]] = {}


def index_for(master: _Master) -> _MasterIndex:
    key = id(master)
    cached = _CACHE.get(key)
    if cached is not None and cached[0] is master:
        return cached[1]
    built = _MasterIndex(master)
    # Bounded: this process analyses against one master at a time, so
    # retaining only the newest avoids pinning superseded copies.
    _CACHE.clear()
    _CACHE[key] = (master, built)
    return built


def clear_cache() -> None:
    """Drop every memoized index. Only needed by tests that deliberately
    mutate a master in place -- production never mutates one."""
    _CACHE.clear()
