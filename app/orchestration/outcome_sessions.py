"""Sprint 3.4 -- the ONE definition of "which trading session, and when does
it END" for every +N-session outcome, used identically by historical replay
(`outcome_horizons`) and the forward outcome sweep (`research_outcome`).

Sessions come from the canonical `trading_calendar` (`next_trading_day`) -- no
second calendar. "+1" is the next valid trading session after the
observation's own session, "+3" the third, "+5" the fifth; weekends, exchange
holidays and closed days never count. The observation's session is its IST
calendar date (T0 is a UTC instant; the NSE session is an IST fact).

A horizon's information boundary is the target session's own close
(15:30 IST). Nothing at or after that boundary's later sessions may
influence a checkpoint for it, and a checkpoint is only *available* once that
close has actually been reached -- never merely once the target date starts.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import TYPE_CHECKING

from app.domain.market.trading_calendar import next_trading_day
from app.utils.time import IST, ensure_utc, to_ist

if TYPE_CHECKING:
    from app.domain.audit.research_models import ResearchObservation

SESSION_OPEN = time(9, 15)
SESSION_CLOSE = time(15, 30)


def target_trading_session_date(observation_date: date, sessions_ahead: int, holidays: frozenset[date] | None = None) -> date:
    """+1/+3/+5 real trading SESSIONS ahead of `observation_date` --
    "+1 session" always means the next real trading day after
    `observation_date`, regardless of whether the observation itself was
    made mid-session or after that same day's close (a session that has
    already happened cannot be a FUTURE checkpoint for itself).

    `holidays=None` defers to the canonical calendar's configured holiday
    list (`resolve_nse_holidays`). Defaulting to the empty `NSE_HOLIDAYS`
    constant, as this function previously did, silently bypassed a loaded
    official holiday calendar and counted exchange holidays as sessions."""
    candidate = observation_date
    for _ in range(sessions_ahead):
        candidate = next_trading_day(candidate, holidays)
    return candidate


def observation_session_date(observation: ResearchObservation) -> date:
    """The observation's own trading-session date (IST calendar date of T0)."""
    return to_ist(observation.generated_at).date()


def session_close_utc(session_date: date) -> datetime:
    return ensure_utc(datetime.combine(session_date, SESSION_CLOSE, tzinfo=IST))


def horizon_session_date(observation: ResearchObservation, sessions_ahead: int) -> date:
    return target_trading_session_date(observation_session_date(observation), sessions_ahead)


def horizon_close_utc(observation: ResearchObservation, sessions_ahead: int) -> datetime:
    """The instant a +N-session horizon's information ends: the target
    session's close. This is both the boundary no later data may cross and
    the earliest moment the checkpoint can be evaluated."""
    return session_close_utc(horizon_session_date(observation, sessions_ahead))


def next_session_open_after(session_date: date, holidays: frozenset[date] | None = None) -> datetime:
    """Open of the first trading session after `session_date`. Until this
    instant no data belonging to a LATER session can exist, so an analysis run
    strictly before it (and at/after the horizon close) describes the horizon
    session itself and nothing beyond it."""
    return ensure_utc(datetime.combine(next_trading_day(session_date, holidays), SESSION_OPEN, tzinfo=IST))
