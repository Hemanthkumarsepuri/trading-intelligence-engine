from __future__ import annotations

from datetime import date
from pathlib import Path

from app.domain.market.trading_calendar import (
    apply_nse_calendar_file,
    classify_session,
    configure_nse_holidays,
    configure_nse_special_sessions,
    is_trading_day,
    load_nse_calendar_file,
    most_recent_trading_day_at_or_before,
    parse_nse_calendar_file,
)

_FILE = Path(__file__).resolve().parents[3] / "data" / "reference" / "nse_trading_holidays.txt"


def test_holiday_file_parses_official_cm_dates_and_muhurat() -> None:
    holidays, special = parse_nse_calendar_file(_FILE.read_text(encoding="utf-8"))
    assert date(2026, 1, 26) in holidays
    assert date(2026, 9, 14) in holidays
    assert date(2026, 11, 8) in special
    assert date(2026, 11, 8) not in holidays


def test_muhurat_sunday_is_a_trading_day_when_file_applied() -> None:
    apply_nse_calendar_file(_FILE)
    try:
        muhurat = date(2026, 11, 8)
        assert muhurat.weekday() == 6
        assert is_trading_day(muhurat) is True
        ctx = classify_session(muhurat)
        assert ctx.is_special_session is True
        assert ctx.is_trading_day is True
    finally:
        configure_nse_holidays(None)
        configure_nse_special_sessions(None)


def test_listed_weekday_holiday_is_not_a_trading_day_when_file_applied() -> None:
    apply_nse_calendar_file(_FILE)
    try:
        republic = date(2026, 1, 26)
        assert is_trading_day(republic) is False
        assert classify_session(republic).is_holiday is True
        assert most_recent_trading_day_at_or_before(republic) == date(2026, 1, 23)
    finally:
        configure_nse_holidays(None)
        configure_nse_special_sessions(None)


def test_load_nse_calendar_file_matches_parse() -> None:
    holidays, special = load_nse_calendar_file(_FILE)
    parsed = parse_nse_calendar_file(_FILE.read_text(encoding="utf-8"))
    assert holidays == parsed[0]
    assert special == parsed[1]
