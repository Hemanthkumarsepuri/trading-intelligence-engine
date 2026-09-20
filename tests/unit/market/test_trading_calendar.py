"""Part 4A -- NSE trading-calendar abstraction tests. `NSE_HOLIDAYS` stays
empty (no fabricated dates), so these tests exercise a custom holiday set
where holiday-specific behavior needs to be proven, and the real empty
default everywhere else."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.domain.market.trading_calendar import (
    classify_session,
    classify_session_window,
    is_nse_holiday,
    is_trading_day,
    is_weekend,
    most_recent_trading_day_at_or_before,
    next_trading_day,
)

_FRIDAY = date(2026, 8, 28)
_SATURDAY = date(2026, 8, 29)
_SUNDAY = date(2026, 8, 30)
_MONDAY = date(2026, 8, 31)


def test_weekday_is_a_trading_day() -> None:
    assert is_weekend(_FRIDAY) is False
    assert is_trading_day(_FRIDAY) is True


def test_saturday_is_not_a_trading_day() -> None:
    assert is_weekend(_SATURDAY) is True
    assert is_trading_day(_SATURDAY) is False


def test_sunday_is_not_a_trading_day() -> None:
    assert is_weekend(_SUNDAY) is True
    assert is_trading_day(_SUNDAY) is False


def test_holiday_set_is_empty_by_default_never_fabricated() -> None:
    """The single most important invariant of this module: with no
    authorized holiday source integrated, EVERY weekday is treated as a
    trading day -- never a guessed holiday."""
    from app.domain.market.trading_calendar import NSE_HOLIDAYS

    assert NSE_HOLIDAYS == frozenset()
    assert is_nse_holiday(_MONDAY) is False


def test_a_declared_holiday_is_not_a_trading_day() -> None:
    holidays = frozenset({_MONDAY})
    assert is_nse_holiday(_MONDAY, holidays) is True
    assert is_trading_day(_MONDAY, holidays) is False
    # An undeclared weekday remains a trading day even with a non-empty set.
    assert is_trading_day(_FRIDAY, holidays) is True


def test_next_trading_day_from_friday_is_monday() -> None:
    assert next_trading_day(_FRIDAY) == _MONDAY


def test_next_trading_day_from_saturday_is_monday() -> None:
    assert next_trading_day(_SATURDAY) == _MONDAY


def test_next_trading_day_from_sunday_is_monday() -> None:
    assert next_trading_day(_SUNDAY) == _MONDAY


def test_next_trading_day_skips_a_declared_holiday() -> None:
    holidays = frozenset({_MONDAY})
    assert next_trading_day(_FRIDAY, holidays) == date(2026, 9, 1)  # Tuesday, since Monday is a holiday


def test_next_trading_day_is_exclusive_of_an_already_trading_day() -> None:
    """`next_trading_day(FRIDAY)` never returns FRIDAY itself -- callers
    wanting "today if already a trading day" must check `is_trading_day()`
    first (exactly what `eod_deadline()` does)."""
    assert next_trading_day(_FRIDAY) != _FRIDAY


def test_next_trading_day_raises_if_no_trading_day_found_within_the_search_window() -> None:
    all_days_off = frozenset(_FRIDAY + timedelta(days=i) for i in range(1, 21))  # every day for the next 20 days is "off"
    with pytest.raises(ValueError, match="no trading day found"):
        next_trading_day(_FRIDAY, all_days_off)


def test_classify_session_on_a_trading_day() -> None:
    ctx = classify_session(_FRIDAY)
    assert ctx.is_trading_day is True
    assert ctx.is_weekend is False
    assert ctx.is_holiday is False
    assert ctx.next_trading_day is None


def test_classify_session_on_a_weekend() -> None:
    ctx = classify_session(_SUNDAY)
    assert ctx.is_trading_day is False
    assert ctx.is_weekend is True
    assert ctx.is_holiday is False
    assert ctx.next_trading_day == _MONDAY


def test_classify_session_on_a_declared_holiday() -> None:
    holidays = frozenset({_MONDAY})
    ctx = classify_session(_MONDAY, holidays)
    assert ctx.is_trading_day is False
    assert ctx.is_weekend is False
    assert ctx.is_holiday is True
    assert ctx.next_trading_day == date(2026, 9, 1)


# ============================================================
# Sprint 3 -- `most_recent_trading_day_at_or_before()`, promoted here from
# Sprint 2's Stage-1-local helper so `classify_market_data_state()` can
# reuse the exact same calendar arithmetic.
# ============================================================


def test_most_recent_trading_day_returns_itself_when_already_a_trading_day() -> None:
    assert most_recent_trading_day_at_or_before(_FRIDAY) == _FRIDAY


def test_most_recent_trading_day_rolls_back_over_a_weekend() -> None:
    assert most_recent_trading_day_at_or_before(_SATURDAY) == _FRIDAY
    assert most_recent_trading_day_at_or_before(_SUNDAY) == _FRIDAY


def test_most_recent_trading_day_skips_a_declared_holiday() -> None:
    holidays = frozenset({_MONDAY})
    tuesday = date(2026, 9, 1)
    assert most_recent_trading_day_at_or_before(_MONDAY, holidays) == _FRIDAY
    assert most_recent_trading_day_at_or_before(tuesday, holidays) == tuesday


def test_holiday_file_parses_iso_dates_and_ignores_comments() -> None:
    from app.domain.market.trading_calendar import parse_nse_holiday_file

    parsed = parse_nse_holiday_file("# official circular\n2026-01-26\n\n2026-08-15  # independence day\n")
    assert parsed == frozenset({date(2026, 1, 26), date(2026, 8, 15)})


def test_configure_nse_holidays_affects_defaulted_calls_then_resets() -> None:
    from app.domain.market.trading_calendar import configure_nse_holidays, is_trading_day

    configure_nse_holidays(frozenset({_MONDAY}))
    try:
        assert is_trading_day(_MONDAY) is False
        assert is_trading_day(_FRIDAY) is True
    finally:
        configure_nse_holidays(None)
    assert is_trading_day(_MONDAY) is True


def test_most_recent_trading_day_raises_if_no_trading_day_found_within_the_search_window() -> None:
    all_days_off = frozenset(_FRIDAY - timedelta(days=i) for i in range(20))  # today and the prior 19 days are all "off"
    with pytest.raises(ValueError, match="no trading day found"):
        most_recent_trading_day_at_or_before(_FRIDAY, all_days_off)


def test_session_window_uses_ist_clock_on_a_trading_day() -> None:
    from datetime import UTC, datetime

    # Friday 28 Aug 2026 06:30 UTC = 12:00 IST — regular session.
    open_window = classify_session_window(datetime(2026, 8, 28, 6, 30, tzinfo=UTC))
    assert open_window.session_window == "OPEN"
    assert open_window.is_trading_day is True

    pre = classify_session_window(datetime(2026, 8, 28, 3, 40, tzinfo=UTC))  # 09:10 IST
    assert pre.session_window == "PRE_OPEN"

    closed_morning = classify_session_window(datetime(2026, 8, 28, 2, 0, tzinfo=UTC))  # 07:30 IST
    assert closed_morning.session_window == "CLOSED"

    closed_evening = classify_session_window(datetime(2026, 8, 28, 11, 0, tzinfo=UTC))  # 16:30 IST
    assert closed_evening.session_window == "CLOSED"


def test_session_window_is_closed_on_a_weekend_even_during_weekday_hours() -> None:
    from datetime import UTC, datetime

    saturday_noon_ist = classify_session_window(datetime(2026, 8, 29, 6, 30, tzinfo=UTC))
    assert saturday_noon_ist.is_weekend is True
    assert saturday_noon_ist.session_window == "CLOSED"
