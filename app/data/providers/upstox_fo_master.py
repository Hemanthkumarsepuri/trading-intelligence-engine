"""F&O (futures & options) eligibility, expiry, lot-size, and futures
instrument-key intelligence — all derived from the already-cached NSE
instrument master's `NSE_FO` segment. Zero new API calls are needed for any
of this: the same master file already fetched by `upstox_instrument_master`
for equity/index symbol resolution also contains the complete derivatives
segment (confirmed live 2026-08-28: 29,713 real `NSE_FO` entries, each
carrying `underlying_symbol`, `expiry` (epoch ms), `weekly` (bool),
`strike_price`, `instrument_type` (CE/PE/FUT), `lot_size`, `tick_size`,
`minimum_lot`).

Never invents an expiry, lot size, or eligibility answer — an underlying
absent from the `NSE_FO` segment (most NSE equities have no derivatives at
all) resolves to `False`/`None`/`[]`, never a guess.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

from app.data.providers.instrument_master_index import index_for


@dataclass(frozen=True)
class ExpiryInfo:
    expiry: date
    is_weekly: bool


def is_fo_eligible(master: Sequence[dict[str, object]], underlying_symbol: str) -> bool:
    """True if `underlying_symbol` has at least one real `NSE_FO` contract
    in the master. Matched exactly (case-insensitively) against the
    contract's own `underlying_symbol` field — never inferred from the
    underlying merely existing in `NSE_EQ`/`NSE_INDEX`.
    """
    canonical = underlying_symbol.strip().upper()
    # Final release gate (Section 7) -- served from a memoized index
    # rather than scanning all ~29,713 rows per call. The index applies
    # the SAME two conditions this expression did (segment == NSE_FO and
    # a canonical underlying match), so the answer is unchanged.
    return bool(index_for(master).fo_by_underlying().get(canonical))


def list_expiries(master: Sequence[dict[str, object]], underlying_symbol: str) -> list[ExpiryInfo]:
    """Every distinct real expiry for `underlying_symbol`'s F&O contracts,
    ascending, each tagged weekly/monthly per Upstox's own `weekly` flag —
    never inferred by day-of-month heuristics.
    """
    canonical = underlying_symbol.strip().upper()
    seen: dict[date, bool] = {}
    # Final release gate (Section 7) -- the segment/underlying filter is
    # now the index lookup (same two conditions, same row order); the
    # expiry/weekly logic below is UNCHANGED.
    for entry in index_for(master).fo_by_underlying().get(canonical, ()):
        expiry_date = _expiry_date(entry)
        if expiry_date is None:
            continue
        weekly = bool(entry.get("weekly", False))
        seen[expiry_date] = seen.get(expiry_date, False) or weekly
    return [ExpiryInfo(expiry=d, is_weekly=seen[d]) for d in sorted(seen)]


def nearest_expiry(master: Sequence[dict[str, object]], underlying_symbol: str, *, as_of: date) -> ExpiryInfo | None:
    """The soonest real expiry on/after `as_of` — never a past expiry, and
    `None` (never invented) if the underlying has no F&O contracts, or all
    of them have already expired as of `as_of`.
    """
    for info in list_expiries(master, underlying_symbol):
        if info.expiry >= as_of:
            return info
    return None


_MONTH_NUMBER: dict[str, int] = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def resolve_expiry_for_hint(
    master: Sequence[dict[str, object]],
    underlying_symbol: str,
    *,
    hint: str,
    as_of: date,
    year: int | None = None,
) -> ExpiryInfo | None:
    """Match a user-typed month (and optional year) to a real upcoming
    expiry. Never falls back to the nearest expiry of a different month.

    When several contracts share the month, the nearest monthly
    (`is_weekly=False`) is preferred; otherwise the soonest expiry in that
    month. `None` if no verified match exists — callers must not substitute.
    """
    month = _MONTH_NUMBER.get(hint.strip().upper())
    if month is None:
        return None
    upcoming = [info for info in list_expiries(master, underlying_symbol) if info.expiry >= as_of]
    matches = [
        info for info in upcoming
        if info.expiry.month == month and (year is None or info.expiry.year == year)
    ]
    if not matches:
        return None
    monthly = [info for info in matches if not info.is_weekly]
    pool = monthly or matches
    return min(pool, key=lambda info: info.expiry)


def select_relevant_expiries(master: Sequence[dict[str, object]], underlying_symbol: str, *, as_of: date) -> list[ExpiryInfo]:
    """Up to three real, distinct expiries on/after `as_of`, ascending —
    the nearest, the next one after it, and the nearest MONTHLY
    (`is_weekly=False`) one — for multi-expiry term-structure comparison
    (`app.domain.options.term_structure`). Never fabricates a third
    expiry that doesn't exist: an underlying with only one or two real
    upcoming expiries (e.g. most equities, which trade monthly-only)
    returns exactly that many, not three padded/invented entries.

    This is a selection policy (ENGINEERING HEURISTIC, not a fact) —
    "nearest + next + monthly" is a reasonable, commonly-useful default
    set for comparing term structure, not the only one a caller could
    want; a caller needing a different selection should call
    `list_expiries()` directly instead.
    """
    upcoming = [info for info in list_expiries(master, underlying_symbol) if info.expiry >= as_of]
    if not upcoming:
        return []

    selected: list[ExpiryInfo] = [upcoming[0]]
    if len(upcoming) > 1:
        selected.append(upcoming[1])
    monthly = next((info for info in upcoming if not info.is_weekly), None)
    if monthly is not None and monthly not in selected:
        selected.append(monthly)

    return sorted(selected, key=lambda info: info.expiry)


def lot_size(master: Sequence[dict[str, object]], underlying_symbol: str) -> int | None:
    """The real lot size for `underlying_symbol`'s F&O contracts (constant
    across every contract of one underlying under NSE's real F&O
    convention). `None` if the underlying has no F&O segment at all —
    never a guessed default.
    """
    canonical = underlying_symbol.strip().upper()
    # Section 7 -- index lookup replaces the full scan; same segment/
    # underlying conditions, same row order, so the FIRST valid lot size
    # found is the same one as before.
    for entry in index_for(master).fo_by_underlying().get(canonical, ()):
        size = entry.get("lot_size")
        if isinstance(size, int | float) and not isinstance(size, bool):
            return int(size)
    return None


def futures_instrument_key(master: Sequence[dict[str, object]], underlying_symbol: str, *, expiry: date) -> str | None:
    """The real `instrument_key` for `underlying_symbol`'s FUT contract at
    exactly `expiry`, or `None` if no such contract exists in the master
    (e.g. an index/equity with no futures leg for that expiry, or no F&O
    segment at all).
    """
    canonical = underlying_symbol.strip().upper()
    # Section 7 -- index lookup replaces the segment/underlying scan; the
    # FUT and expiry conditions below are UNCHANGED, and row order is
    # preserved so the first match is the same one as before.
    for entry in index_for(master).fo_by_underlying().get(canonical, ()):
        if entry.get("instrument_type") != "FUT":
            continue
        if _expiry_date(entry) != expiry:
            continue
        key = entry.get("instrument_key")
        return key if isinstance(key, str) else None
    return None


def nearest_futures_instrument_key(
    master: Sequence[dict[str, object]], underlying_symbol: str, *, segment: str, as_of: date
) -> str | None:
    """The real `instrument_key` for `underlying_symbol`'s nearest-expiry
    FUT contract on/after `as_of`, in `segment` — generalizes
    `futures_instrument_key()` (which is `NSE_FO`-only) to any real
    derivatives segment the same master file carries, e.g. `NCD_FO`
    (currency derivatives — real USD/INR futures, confirmed live
    2026-08-29) or, in the SEPARATE MCX master file, `MCX_FO` (real Crude
    Oil/Gold futures, also confirmed live). `None` if no matching FUT
    contract exists — never a guessed key.
    """
    canonical = underlying_symbol.strip().upper()
    candidates: list[tuple[date, str]] = []
    # Release gate (Section 7) -- the segment/underlying filter is served
    # from the memoized index (same two conditions, master row order); the
    # segment is compared as `str()` there, so a row whose segment is
    # absent can never collide with a real segment name.
    for entry in index_for(master).by_segment_underlying().get((segment, canonical), ()):
        if entry.get("segment") != segment or entry.get("instrument_type") != "FUT":
            continue
        expiry_date = _expiry_date(entry)
        if expiry_date is None or expiry_date < as_of:
            continue
        key = entry.get("instrument_key")
        if isinstance(key, str):
            candidates.append((expiry_date, key))
    if not candidates:
        return None
    return min(candidates, key=lambda pair: pair[0])[1]


def _underlying(entry: dict[str, object]) -> str:
    return str(entry.get("underlying_symbol", "")).strip().upper()


def _expiry_date(entry: dict[str, object]) -> date | None:
    expiry_ms = entry.get("expiry")
    if isinstance(expiry_ms, bool) or not isinstance(expiry_ms, int | float):
        return None
    return datetime.fromtimestamp(expiry_ms / 1000, tz=UTC).date()
