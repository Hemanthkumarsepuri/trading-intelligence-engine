"""`detect_structural_reclaim()` -- the previously-missing fact behind
`DevelopmentPattern.FAILED_BREAKDOWN_RECLAIM`.

Every case here is built from explicit session OHLC, so what each
assertion depends on is visible in the test itself rather than hidden in
a fixture. `_candles()` expands one session's OHLC into real M15 candles
in an IST session window, because the detector deliberately reads the
same aggregated session bars the rest of the pipeline does -- it must
work on candles, not on a convenient pre-aggregated shortcut.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.domain.market.freshness import DataFreshness
from app.domain.market.models import Candle, Timeframe
from app.domain.options.structural_reclaim import (
    MAX_RECLAIM_AGE_SESSIONS,
    StructuralReclaimStatus,
    detect_structural_reclaim,
)

_KEY = "NSE_EQ|TEST"
# 2026-06-01 is a Monday; every date below is a real weekday, and the
# detector's own session aggregation is calendar-date based.
_FIRST_SESSION = date(2026, 6, 1)


def _session_dates(count: int) -> list[date]:
    days: list[date] = []
    cursor = _FIRST_SESSION
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _candles(sessions: list[tuple[date, float, float, float, float]]) -> list[Candle]:
    """Four real M15 bars per session whose aggregate is exactly the
    requested O/H/L/C: open bar, high bar, low bar, close bar."""
    out: list[Candle] = []
    for day, o, h, low, c in sessions:
        start = datetime(day.year, day.month, day.day, 3, 45, tzinfo=UTC)  # 09:15 IST
        prices = [(o, o, o), (h, h, h), (low, low, low), (c, c, c)]
        for index, (bo, bh, bl) in enumerate(prices):
            ts = start + timedelta(minutes=15 * index)
            out.append(Candle(
                provider="test", freshness=DataFreshness(data_timestamp=ts, received_timestamp=ts),
                instrument_id=_KEY, timeframe=Timeframe.M15,
                open=Decimal(str(bo)), high=Decimal(str(bh)), low=Decimal(str(bl)), close=Decimal(str(bl)),
                volume=1000,
            ))
        close_ts = start + timedelta(minutes=15 * len(prices))
        out.append(Candle(
            provider="test", freshness=DataFreshness(data_timestamp=close_ts, received_timestamp=close_ts),
            instrument_id=_KEY, timeframe=Timeframe.M15,
            open=Decimal(str(c)), high=Decimal(str(c)), low=Decimal(str(c)), close=Decimal(str(c)),
            volume=1000,
        ))
    return out


def _as_of(day: date) -> datetime:
    """Well after that session's 15:30 IST close, so it counts as completed."""
    return datetime(day.year, day.month, day.day, 12, 30, tzinfo=UTC)


def _detect(sessions: list[tuple[date, float, float, float, float]], *, spot: float | None):
    last_day = sessions[-1][0]
    return detect_structural_reclaim(
        _candles(sessions), instrument_id=_KEY, timeframe=Timeframe.M15,
        as_of=_as_of(last_day), spot=Decimal(str(spot)) if spot is not None else None,
    )


def _failed_breakdown(days: list[date]) -> list[tuple[date, float, float, float, float]]:
    """Five flat reference sessions with a 100.0 floor, a session that
    breaks to 98.0 (2% -- well past MIN_BREAK_PCT), a reclaiming close
    above the floor, then a held session."""
    return [
        (days[0], 102.0, 104.0, 100.0, 103.0),
        (days[1], 103.0, 105.0, 100.5, 104.0),
        (days[2], 104.0, 105.0, 100.2, 102.0),
        (days[3], 102.0, 103.5, 100.1, 101.0),
        (days[4], 101.0, 102.0, 100.0, 101.5),
        (days[5], 101.0, 101.5, 98.0, 99.0),      # break: low 98.0 vs 100.0 floor
        (days[6], 99.0, 102.5, 98.5, 102.0),      # reclaim: closes back above 100.0
        (days[7], 102.0, 103.0, 101.0, 102.5),    # held
    ]


def test_failed_breakdown_reclaim_is_detected_with_its_real_level_and_dates() -> None:
    days = _session_dates(8)
    result = _detect(_failed_breakdown(days), spot=102.8)
    assert result.status == StructuralReclaimStatus.OK
    assert result.direction == "BULLISH"
    assert result.level_kind == "support"
    assert result.level == Decimal("100.0")
    assert result.broken_on == days[5]
    assert result.reclaimed_on == days[6]
    assert result.break_depth_pct == Decimal("2")
    assert result.sessions_since_reclaim == 1
    assert "reclaimed" in result.detail


def test_failed_breakout_rejection_is_the_mirror_case() -> None:
    days = _session_dates(8)
    sessions = [
        (days[0], 98.0, 100.0, 96.0, 97.0),
        (days[1], 97.0, 99.5, 96.5, 98.0),
        (days[2], 98.0, 99.8, 97.0, 98.5),
        (days[3], 98.5, 99.9, 97.5, 99.0),
        (days[4], 99.0, 100.0, 98.0, 99.5),
        (days[5], 99.5, 102.0, 99.0, 101.0),      # break above the 100.0 ceiling
        (days[6], 101.0, 101.5, 97.5, 98.0),      # rejected back below
        (days[7], 98.0, 99.0, 97.0, 97.5),        # held below
    ]
    result = _detect(sessions, spot=97.2)
    assert result.status == StructuralReclaimStatus.OK
    assert result.direction == "BEARISH"
    assert result.level_kind == "resistance"
    assert result.level == Decimal("100.0")


def test_a_reclaim_already_lost_again_is_not_reported() -> None:
    """The HOLD condition: a later completed session closed back below
    the reclaimed floor, so the reclaim did not survive."""
    days = _session_dates(9)
    sessions = [*_failed_breakdown(days[:8]), (days[8], 102.0, 102.5, 97.0, 97.5)]
    result = _detect(sessions, spot=97.2)
    assert result.status == StructuralReclaimStatus.NONE


def test_spot_back_below_the_level_is_not_reported_even_when_sessions_held() -> None:
    days = _session_dates(8)
    result = _detect(_failed_breakdown(days), spot=99.0)
    assert result.status == StructuralReclaimStatus.NONE


def test_a_shallow_undercut_is_noise_not_a_break() -> None:
    days = _session_dates(8)
    sessions = _failed_breakdown(days)
    sessions[5] = (days[5], 101.0, 101.5, 99.9, 100.2)  # 0.1% undercut, below MIN_BREAK_PCT
    # The following session must not dip through prior structure either,
    # or it would be a genuine (different) break rather than this one.
    sessions[6] = (days[6], 100.2, 102.5, 100.1, 102.0)
    result = _detect(sessions, spot=102.8)
    assert result.status == StructuralReclaimStatus.NONE


def test_an_old_reclaim_is_history_not_a_developing_observation() -> None:
    days = _session_dates(8 + MAX_RECLAIM_AGE_SESSIONS + 1)
    sessions = _failed_breakdown(days[:8])
    for day in days[8:]:
        sessions.append((day, 102.5, 103.0, 101.5, 102.5))
    result = _detect(sessions, spot=102.8)
    assert result.status == StructuralReclaimStatus.NONE
    assert "completed sessions" in result.detail


def test_a_whipsaw_qualifying_on_both_sides_is_reported_as_conflicting() -> None:
    """Two opposing structural readings of the same tape are not
    evidence for either side, and the detector must never pick one."""
    days = _session_dates(9)
    sessions = [
        (days[0], 100.0, 103.0, 99.0, 101.0),
        (days[1], 101.0, 103.0, 99.0, 100.0),
        (days[2], 100.0, 103.0, 99.0, 101.0),
        (days[3], 101.0, 103.0, 99.0, 100.5),
        (days[4], 100.5, 103.0, 99.0, 101.0),
        (days[5], 101.0, 105.0, 96.0, 100.0),   # breaks BOTH the 99.0 floor and the 103.0 ceiling
        (days[6], 100.0, 102.0, 99.5, 101.0),   # closes back inside both
        (days[7], 101.0, 102.0, 99.5, 101.0),
        (days[8], 101.0, 102.0, 99.5, 101.0),
    ]
    result = _detect(sessions, spot=101.0)
    assert result.status == StructuralReclaimStatus.CONFLICTING
    assert result.direction is None
    assert result.level is None


def test_too_little_history_is_insufficient_not_none() -> None:
    days = _session_dates(3)
    sessions = [(day, 100.0, 101.0, 99.0, 100.0) for day in days]
    result = _detect(sessions, spot=100.0)
    assert result.status == StructuralReclaimStatus.INSUFFICIENT_HISTORY
    assert "INSUFFICIENT_HISTORY" in result.detail


def test_no_candles_at_all_is_insufficient_history() -> None:
    result = detect_structural_reclaim(
        [], instrument_id=_KEY, timeframe=Timeframe.M15, as_of=_as_of(_FIRST_SESSION), spot=Decimal("100"),
    )
    assert result.status == StructuralReclaimStatus.INSUFFICIENT_HISTORY


def test_the_still_forming_session_cannot_break_or_reclaim_structure() -> None:
    """No lookahead, and no half-formed facts: with `as_of` inside the
    last session, that session is not completed and is excluded, so the
    reclaim that only exists because of it is not reported."""
    days = _session_dates(8)
    sessions = _failed_breakdown(days)
    result = detect_structural_reclaim(
        _candles(sessions), instrument_id=_KEY, timeframe=Timeframe.M15,
        # 15:00 IST on the last session -- before its 15:30 close.
        as_of=datetime(days[7].year, days[7].month, days[7].day, 9, 30, tzinfo=UTC), spot=Decimal("102.8"),
    )
    assert result.status == StructuralReclaimStatus.OK
    assert result.sessions_since_reclaim == 0  # the reclaim session is now the last COMPLETED one


def test_future_candles_never_participate() -> None:
    """`as_of` bounding: evaluated as of the break session, the reclaim
    has not happened yet and must not be visible."""
    days = _session_dates(8)
    sessions = _failed_breakdown(days)
    result = detect_structural_reclaim(
        _candles(sessions), instrument_id=_KEY, timeframe=Timeframe.M15,
        as_of=_as_of(days[5]), spot=Decimal("99.0"),
    )
    assert result.status == StructuralReclaimStatus.NONE
