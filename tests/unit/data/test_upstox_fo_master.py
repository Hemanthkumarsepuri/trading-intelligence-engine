from __future__ import annotations

from datetime import date

from app.data.providers.upstox_fo_master import (
    ExpiryInfo,
    futures_instrument_key,
    is_fo_eligible,
    list_expiries,
    lot_size,
    nearest_expiry,
    nearest_futures_instrument_key,
    select_relevant_expiries,
)

# Epoch-ms values mirror the real field shape (23:59:59 IST close, expressed
# as UTC epoch ms) -- exact instant doesn't matter here, only that it decodes
# to the intended calendar date in UTC.
_SEP_3_2026_MS = 1788460199000  # 2026-09-03 18:29:59 UTC -> 2026-09-03 date
_SEP_10_2026_MS = 1789064999000  # 2026-09-10 18:29:59 UTC -> 2026-09-10 date
_SEP_24_2026_MS = 1790274599000  # 2026-09-24 18:29:59 UTC -> 2026-09-24 date

_MASTER: list[dict[str, object]] = [
    {"segment": "NSE_EQ", "underlying_symbol": None, "trading_symbol": "RELIANCE"},
    {
        "segment": "NSE_FO",
        "underlying_symbol": "RELIANCE",
        "instrument_type": "CE",
        "expiry": _SEP_24_2026_MS,
        "weekly": False,
        "lot_size": 500,
        "instrument_key": "NSE_FO|1",
    },
    {
        "segment": "NSE_FO",
        "underlying_symbol": "RELIANCE",
        "instrument_type": "PE",
        "expiry": _SEP_24_2026_MS,
        "weekly": False,
        "lot_size": 500,
        "instrument_key": "NSE_FO|2",
    },
    {
        "segment": "NSE_FO",
        "underlying_symbol": "NIFTY",
        "instrument_type": "CE",
        "expiry": _SEP_3_2026_MS,
        "weekly": True,
        "lot_size": 65,
        "instrument_key": "NSE_FO|3",
    },
    {
        "segment": "NSE_FO",
        "underlying_symbol": "NIFTY",
        "instrument_type": "FUT",
        "expiry": _SEP_24_2026_MS,
        "weekly": False,
        "lot_size": 65,
        "instrument_key": "NSE_FO|4",
    },
    {
        "segment": "NSE_FO",
        "underlying_symbol": "NIFTY",
        "instrument_type": "CE",
        "expiry": _SEP_10_2026_MS,
        "weekly": True,
        "lot_size": 65,
        "instrument_key": "NSE_FO|5",
    },
]


def test_is_fo_eligible_true_for_underlying_with_fo_contracts() -> None:
    assert is_fo_eligible(_MASTER, "RELIANCE") is True
    assert is_fo_eligible(_MASTER, "reliance") is True  # case-insensitive


def test_is_fo_eligible_false_for_underlying_without_fo_contracts() -> None:
    assert is_fo_eligible(_MASTER, "SOME_EQUITY_WITH_NO_DERIVATIVES") is False


def test_list_expiries_returns_distinct_ascending_dates_tagged_weekly() -> None:
    expiries = list_expiries(_MASTER, "RELIANCE")
    assert expiries == [ExpiryInfo(expiry=date(2026, 9, 24), is_weekly=False)]


def test_list_expiries_tags_weekly_expiries_correctly() -> None:
    expiries = list_expiries(_MASTER, "NIFTY")
    assert ExpiryInfo(expiry=date(2026, 9, 3), is_weekly=True) in expiries
    assert ExpiryInfo(expiry=date(2026, 9, 24), is_weekly=False) in expiries


def test_list_expiries_empty_for_ineligible_underlying() -> None:
    assert list_expiries(_MASTER, "NOT_A_REAL_SYMBOL") == []


def test_nearest_expiry_returns_soonest_on_or_after_as_of() -> None:
    info = nearest_expiry(_MASTER, "NIFTY", as_of=date(2026, 9, 1))
    assert info == ExpiryInfo(expiry=date(2026, 9, 3), is_weekly=True)


def test_nearest_expiry_skips_past_expiries() -> None:
    info = nearest_expiry(_MASTER, "NIFTY", as_of=date(2026, 9, 11))
    assert info == ExpiryInfo(expiry=date(2026, 9, 24), is_weekly=False)


def test_nearest_expiry_none_when_all_expiries_are_past() -> None:
    assert nearest_expiry(_MASTER, "NIFTY", as_of=date(2027, 1, 1)) is None


def test_nearest_expiry_none_for_ineligible_underlying() -> None:
    assert nearest_expiry(_MASTER, "NOT_A_REAL_SYMBOL", as_of=date(2026, 9, 1)) is None


def test_lot_size_returns_real_value() -> None:
    assert lot_size(_MASTER, "RELIANCE") == 500
    assert lot_size(_MASTER, "NIFTY") == 65


def test_lot_size_none_for_ineligible_underlying() -> None:
    assert lot_size(_MASTER, "NOT_A_REAL_SYMBOL") is None


def test_futures_instrument_key_finds_the_real_contract() -> None:
    key = futures_instrument_key(_MASTER, "NIFTY", expiry=date(2026, 9, 24))
    assert key == "NSE_FO|4"


def test_futures_instrument_key_none_when_no_futures_at_that_expiry() -> None:
    assert futures_instrument_key(_MASTER, "RELIANCE", expiry=date(2026, 9, 24)) is None  # only CE/PE, no FUT


def test_futures_instrument_key_none_for_ineligible_underlying() -> None:
    assert futures_instrument_key(_MASTER, "NOT_A_REAL_SYMBOL", expiry=date(2026, 9, 24)) is None


def test_select_relevant_expiries_returns_nearest_next_and_monthly() -> None:
    # NIFTY has 3 real expiries here: Sep 3 (weekly), Sep 10 (weekly), Sep 24 (monthly).
    selected = select_relevant_expiries(_MASTER, "NIFTY", as_of=date(2026, 9, 1))
    assert selected == [
        ExpiryInfo(expiry=date(2026, 9, 3), is_weekly=True),
        ExpiryInfo(expiry=date(2026, 9, 10), is_weekly=True),
        ExpiryInfo(expiry=date(2026, 9, 24), is_weekly=False),
    ]


def test_select_relevant_expiries_never_fabricates_a_third_when_only_one_exists() -> None:
    # RELIANCE has exactly one real expiry -- must return exactly one, not three padded entries.
    selected = select_relevant_expiries(_MASTER, "RELIANCE", as_of=date(2026, 9, 1))
    assert selected == [ExpiryInfo(expiry=date(2026, 9, 24), is_weekly=False)]


def test_select_relevant_expiries_deduplicates_when_nearest_is_already_monthly() -> None:
    # If as_of is between Sep 10 and Sep 24, only 2 real expiries remain (Sep 24 is both
    # "next" and "the monthly") -- must not duplicate it.
    selected = select_relevant_expiries(_MASTER, "NIFTY", as_of=date(2026, 9, 20))
    assert selected == [ExpiryInfo(expiry=date(2026, 9, 24), is_weekly=False)]


def test_select_relevant_expiries_empty_when_none_upcoming() -> None:
    assert select_relevant_expiries(_MASTER, "NIFTY", as_of=date(2027, 1, 1)) == []


def test_select_relevant_expiries_empty_for_ineligible_underlying() -> None:
    assert select_relevant_expiries(_MASTER, "NOT_A_REAL_SYMBOL", as_of=date(2026, 9, 1)) == []


# -- nearest_futures_instrument_key (generalized: NCD_FO/MCX_FO, not just NSE_FO) --

_CROSS_SEGMENT_MASTER: list[dict[str, object]] = [
    {
        "segment": "NCD_FO", "underlying_symbol": "USDINR", "instrument_type": "FUT",
        "expiry": 1790447399000, "instrument_key": "NCD_FO|1",  # 2026-09-26
    },
    {
        "segment": "NCD_FO", "underlying_symbol": "USDINR", "instrument_type": "FUT",
        "expiry": 1793125799000, "instrument_key": "NCD_FO|2",  # 2026-10-27
    },
    {
        "segment": "MCX_FO", "underlying_symbol": "CRUDEOIL", "instrument_type": "FUT",
        "expiry": 1790447399000, "instrument_key": "MCX_FO|1",
    },
]


def test_nearest_futures_instrument_key_finds_the_real_nearest_contract() -> None:
    key = nearest_futures_instrument_key(_CROSS_SEGMENT_MASTER, "USDINR", segment="NCD_FO", as_of=date(2026, 9, 1))
    assert key == "NCD_FO|1"


def test_nearest_futures_instrument_key_skips_expired_contracts() -> None:
    key = nearest_futures_instrument_key(_CROSS_SEGMENT_MASTER, "USDINR", segment="NCD_FO", as_of=date(2026, 10, 1))
    assert key == "NCD_FO|2"


def test_nearest_futures_instrument_key_respects_segment() -> None:
    # A real USDINR FUT exists, but not in MCX_FO -- must not cross segments.
    assert nearest_futures_instrument_key(_CROSS_SEGMENT_MASTER, "USDINR", segment="MCX_FO", as_of=date(2026, 9, 1)) is None


def test_nearest_futures_instrument_key_works_for_mcx_crude_oil() -> None:
    key = nearest_futures_instrument_key(_CROSS_SEGMENT_MASTER, "CRUDEOIL", segment="MCX_FO", as_of=date(2026, 9, 1))
    assert key == "MCX_FO|1"


def test_nearest_futures_instrument_key_none_when_all_expired() -> None:
    assert nearest_futures_instrument_key(_CROSS_SEGMENT_MASTER, "USDINR", segment="NCD_FO", as_of=date(2027, 1, 1)) is None


def test_nearest_futures_instrument_key_none_for_unknown_underlying() -> None:
    assert nearest_futures_instrument_key(_CROSS_SEGMENT_MASTER, "NOT_REAL", segment="NCD_FO", as_of=date(2026, 9, 1)) is None
