"""NSE trading-calendar abstraction (Part 4A of the "pending-capability
completion" milestone) — fixes the known limitation documented at the end
of Sprint 7: `eod_deadline()` previously computed a same-calendar-day
15:30 IST instant with no awareness of whether that calendar day was
actually an NSE trading day. A snapshot generated on a Sunday would get a
Sunday-15:30 "EOD deadline" that could never correspond to a real market
close.

=== WEEKEND DETECTION: a real, uncontroversial FACT ===
NSE has never traded on a Saturday or Sunday. `is_weekend()` is pure
calendar arithmetic, not a claim requiring any external source.

=== HOLIDAY DETECTION: explicitly NOT fabricated ===
No authorized, licensed NSE trading-holiday calendar source has been
integrated into this project — the same conclusion this project already
reached for corporate-events/news data (see docs/data-sources/
PROVIDER_DECISION.md's "no legitimate accessible source" findings) applies
here: NSE's own holiday list is published on its website but consuming it
programmatically without a license would be exactly the "unofficial
scraping" this project's policy forbids, and no free authorized API for
it was found. `NSE_HOLIDAYS` therefore starts as an explicit, empty,
clearly-labeled frozenset. Official CM-segment holidays and special sessions are loaded from
`data/reference/nse_trading_holidays.txt`, transcribed from NSE circular
NSE/CMTR/71775 (12 Dec 2025, nsearchives.nseindia.com). The in-module
`NSE_HOLIDAYS` constant stays empty so tests that pass an explicit set
are not coupled to a year. Muhurat / special sessions are `SPECIAL:`
lines -- they ARE trading days even on a weekend.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from app.utils.time import IST, to_ist

# Deliberately empty -- see module docstring's HOLIDAY DETECTION section.
# Populate only from a real, authorized NSE holiday-calendar source; never
# fabricated here. Every function below degrades gracefully to
# weekend-only trading-day detection while this stays empty.
NSE_HOLIDAYS: frozenset[date] = frozenset()
_runtime_holidays: frozenset[date] | None = None
_runtime_special_sessions: frozenset[date] | None = None


def parse_nse_calendar_file(text: str) -> tuple[frozenset[date], frozenset[date]]:
    """ISO dates are CM-segment holidays. `SPECIAL:YYYY-MM-DD` is a
    trading session on a day that would otherwise be closed (e.g. Muhurat).
    `#` starts a comment. Never infers dates from weekday names.
    """
    holidays: set[date] = set()
    special: set[date] = set()
    for raw in text.splitlines():
        stripped = raw.split("#", 1)[0].strip()
        if not stripped:
            continue
        if stripped.upper().startswith("SPECIAL:"):
            special.add(date.fromisoformat(stripped.split(":", 1)[1].strip()))
        else:
            holidays.add(date.fromisoformat(stripped))
    return frozenset(holidays), frozenset(special)


def parse_nse_holiday_file(text: str) -> frozenset[date]:
    holidays, _special = parse_nse_calendar_file(text)
    return holidays


def load_nse_holiday_file(path: Path) -> frozenset[date]:
    if not path.is_file():
        return frozenset()
    return parse_nse_holiday_file(path.read_text(encoding="utf-8"))


def load_nse_calendar_file(path: Path) -> tuple[frozenset[date], frozenset[date]]:
    if not path.is_file():
        return frozenset(), frozenset()
    return parse_nse_calendar_file(path.read_text(encoding="utf-8"))


def configure_nse_holidays(holidays: frozenset[date] | None) -> None:
    """Process-level overlay used by defaulted calendar calls. Pass
    `None` to return to the empty `NSE_HOLIDAYS` constant. Callers that
    pass an explicit `holidays=` set still win.
    """
    global _runtime_holidays
    _runtime_holidays = holidays


def configure_nse_special_sessions(sessions: frozenset[date] | None) -> None:
    global _runtime_special_sessions
    _runtime_special_sessions = sessions


def apply_nse_calendar_file(path: Path) -> None:
    holidays, special = load_nse_calendar_file(path)
    configure_nse_holidays(holidays if holidays else None)
    configure_nse_special_sessions(special if special else None)


def resolve_nse_holidays(explicit: frozenset[date] | None = None) -> frozenset[date]:
    if explicit is not None:
        return explicit
    if _runtime_holidays is not None:
        return _runtime_holidays
    return NSE_HOLIDAYS


def resolve_nse_special_sessions(explicit: frozenset[date] | None = None) -> frozenset[date]:
    if explicit is not None:
        return explicit
    if _runtime_special_sessions is not None:
        return _runtime_special_sessions
    return frozenset()


def is_weekend(day: date) -> bool:
    """FACT: NSE has never traded on a Saturday or Sunday."""
    return day.weekday() >= 5  # Monday=0 ... Saturday=5, Sunday=6


def is_nse_holiday(day: date, holidays: frozenset[date] | None = None) -> bool:
    """Defaults to the configured overlay, else the empty `NSE_HOLIDAYS`
    constant. Always `False` until an operator-supplied official list is
    loaded; never fabricated."""
    return day in resolve_nse_holidays(holidays)


def is_special_session(day: date, special_sessions: frozenset[date] | None = None) -> bool:
    return day in resolve_nse_special_sessions(special_sessions)


def is_trading_day(day: date, holidays: frozenset[date] | None = None) -> bool:
    if is_special_session(day):
        return True
    return not is_weekend(day) and not is_nse_holiday(day, holidays)


def next_trading_day(day: date, holidays: frozenset[date] | None = None) -> date:
    """Rolls forward to the next real trading day, EXCLUSIVE of `day`
    itself -- if `day` is already a trading day, this returns the day
    AFTER it. A caller wanting "day itself if it's already a trading day,
    else roll forward" should check `is_trading_day(day)` first (see
    `app.orchestration.audit_journal.eod_deadline`, which does exactly
    that). Bounded to a generous 14-day search so a caller error (e.g. an
    `NSE_HOLIDAYS` set that accidentally covers an entire month) fails
    loudly rather than looping forever.
    """
    resolved = resolve_nse_holidays(holidays)
    candidate = day
    for _ in range(14):
        candidate = candidate + timedelta(days=1)
        if is_trading_day(candidate, resolved):
            return candidate
    raise ValueError(f"no trading day found within 14 days of {day.isoformat()} -- check NSE_HOLIDAYS for an error")


def most_recent_trading_day_at_or_before(day: date, holidays: frozenset[date] | None = None) -> date:
    """The INCLUSIVE mirror of `next_trading_day()` -- rolls BACKWARD to
    the most recent real trading day at or before `day`, returning `day`
    itself when it is already a trading day. The one shared, authoritative
    answer to "what is the latest session a legitimate quote could
    genuinely be from, as of this date" -- both `classify_market_data_state()`
    (`app.domain.market.data_state`) and the Daily Market Researcher's own
    Stage-1 screen (`app.orchestration.daily_research`) call this SAME
    function rather than each rolling their own calendar arithmetic.
    Bounded to 14 days back, matching `next_trading_day()`'s own bound, so
    a caller error still fails loudly rather than looping forever.
    """
    resolved = resolve_nse_holidays(holidays)
    candidate = day
    for _ in range(14):
        if is_trading_day(candidate, resolved):
            return candidate
        candidate -= timedelta(days=1)
    raise ValueError(f"no trading day found within 14 days before {day.isoformat()} -- check NSE_HOLIDAYS for an error")


@dataclass(frozen=True)
class MarketSessionContext:
    """A descriptive classification of one calendar day relative to the
    NSE trading calendar -- never a claim about whether the market is
    OPEN right now (that remains `MarketDataState`'s job, derived from
    the real Upstox market-status endpoint, not from this calendar).
    `next_trading_day` is `None` when `calendar_date` is itself already a
    trading day.
    """

    calendar_date: date
    is_weekend: bool
    is_holiday: bool
    is_special_session: bool
    is_trading_day: bool
    next_trading_day: date | None


def classify_session(day: date, holidays: frozenset[date] | None = None) -> MarketSessionContext:
    resolved = resolve_nse_holidays(holidays)
    weekend = is_weekend(day)
    holiday = is_nse_holiday(day, resolved)
    special = is_special_session(day)
    trading = is_trading_day(day, resolved)
    return MarketSessionContext(
        calendar_date=day, is_weekend=weekend, is_holiday=holiday, is_special_session=special,
        is_trading_day=trading, next_trading_day=None if trading else next_trading_day(day, resolved),
    )


# Regular NSE cash/F&O session clock (IST). These hours are a documented
# exchange fact, not a live ping. Pre-open is the 09:00–09:15 call auction
# window Upstox reports as PRE_OPEN_START/PRE_OPEN_END.
_NSE_PRE_OPEN = time(9, 0)
_NSE_REGULAR_OPEN = time(9, 15)
_NSE_REGULAR_CLOSE = time(15, 30)


@dataclass(frozen=True)
class SessionWindow:
    """Clock+calendar session label for the UI.

    `session_window` stays OPEN / PRE_OPEN / CLOSED so live-F&O
    certification can keep treating only OPEN as a live session.

    `research_session_mode` is the closed/pre-market research context:
    LIVE, PRE_MARKET, POST_MARKET, or CLOSED. It never upgrades last
    observed prints into live evidence.
    """

    session_window: str
    calendar_date_ist: date
    is_trading_day: bool
    is_weekend: bool
    is_holiday: bool
    is_special_session: bool
    research_session_mode: str
    next_session_open_ist: datetime
    basis: str = "NSE trading calendar plus IST clock. Not a live exchange ping."


def next_regular_session_open(as_of: datetime, holidays: frozenset[date] | None = None) -> datetime:
    """Next regular NSE cash/F&O open (09:15 IST). Does not invent special-session hours."""
    ist = to_ist(as_of)
    day = classify_session(ist.date(), holidays)
    clock = ist.time()
    if day.is_trading_day and clock < _NSE_REGULAR_OPEN:
        return datetime.combine(day.calendar_date, _NSE_REGULAR_OPEN, tzinfo=IST)
    nxt = next_trading_day(ist.date(), holidays)
    return datetime.combine(nxt, _NSE_REGULAR_OPEN, tzinfo=IST)


def classify_research_session_mode(
    session_window: str,
    *,
    is_trading_day: bool,
    clock: time,
) -> str:
    """Research context. Independent of live Discover / Gate 1 OPEN checks."""
    if session_window == "OPEN":
        return "LIVE"
    if session_window == "PRE_OPEN":
        return "PRE_MARKET"
    if is_trading_day and clock > _NSE_REGULAR_CLOSE:
        return "POST_MARKET"
    if is_trading_day and clock < _NSE_PRE_OPEN:
        return "PRE_MARKET"
    return "CLOSED"


def classify_session_window(as_of: datetime, holidays: frozenset[date] | None = None) -> SessionWindow:
    """OPEN / PRE_OPEN / CLOSED from the IST clock and `classify_session()`."""
    ist = to_ist(as_of)
    day = classify_session(ist.date(), holidays)
    clock = ist.time()
    if not day.is_trading_day or clock < _NSE_PRE_OPEN:
        window = "CLOSED"
    elif clock < _NSE_REGULAR_OPEN:
        window = "PRE_OPEN"
    elif clock <= _NSE_REGULAR_CLOSE:
        window = "OPEN"
    else:
        window = "CLOSED"
    return SessionWindow(
        session_window=window,
        calendar_date_ist=day.calendar_date,
        is_trading_day=day.is_trading_day,
        is_weekend=day.is_weekend,
        is_holiday=day.is_holiday,
        is_special_session=day.is_special_session,
        research_session_mode=classify_research_session_mode(
            window, is_trading_day=day.is_trading_day, clock=clock,
        ),
        next_session_open_ist=next_regular_session_open(as_of, holidays),
    )


def session_window_payload(window: SessionWindow) -> dict[str, Any]:
    next_open = window.next_session_open_ist
    next_ist = to_ist(next_open)
    observation_kind = "LIVE" if window.session_window == "OPEN" else "LAST_OBSERVED"
    return {
        "session_window": window.session_window,
        "calendar_date_ist": window.calendar_date_ist.isoformat(),
        "is_trading_day": window.is_trading_day,
        "is_weekend": window.is_weekend,
        "is_holiday": window.is_holiday,
        "is_special_session": window.is_special_session,
        "basis": window.basis,
        "research_session_mode": window.research_session_mode,
        "next_session_open_ist": next_open.isoformat(),
        "next_session_open_ist_label": next_ist.strftime("%d %b %Y · %H:%M IST"),
        "live_discover_available": window.session_window == "OPEN",
        "observation_kind": observation_kind,
    }
