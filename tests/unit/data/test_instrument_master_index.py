"""The memoized master index must be a pure restructuring: every lookup
answers exactly as the original linear scan over the same master did."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from app.data.providers.instrument_master_index import clear_cache, index_for
from app.data.providers.upstox_fo_master import (
    futures_instrument_key,
    is_fo_eligible,
    list_expiries,
    lot_size,
)
from app.data.providers.upstox_instrument_master import resolve_symbol

_REAL_MASTER = Path("data/reference/upstox_nse_instruments.json")


def _naive_resolve(master: list[dict[str, object]], symbol: str, segment: str | None) -> list[dict[str, object]]:
    canonical = symbol.strip().upper()
    return [
        e for e in master
        if str(e.get("trading_symbol", "")).strip().upper() == canonical
        and (segment is None or e.get("segment") == segment)
        and isinstance(e.get("instrument_key"), str)
    ]


def _naive_fo_rows(master: list[dict[str, object]], underlying: str) -> list[dict[str, object]]:
    canonical = underlying.strip().upper()
    return [
        e for e in master
        if e.get("segment") == "NSE_FO" and str(e.get("underlying_symbol", "")).strip().upper() == canonical
    ]


def _synthetic() -> list[dict[str, object]]:
    return [
        {"segment": "NSE_EQ", "trading_symbol": " reliance ", "instrument_key": "NSE_EQ|R", "underlying_symbol": None},
        {"segment": "NSE_FO", "trading_symbol": "RELIANCE FUT", "underlying_symbol": "RELIANCE",
         "instrument_type": "FUT", "expiry": 1790274599000, "lot_size": 500, "instrument_key": "NSE_FO|F"},
        {"segment": "NSE_FO", "trading_symbol": "X", "underlying_symbol": "", "instrument_type": "CE",
         "expiry": 1790274599000, "lot_size": 1, "instrument_key": "NSE_FO|E"},
        {"segment": "NSE_EQ", "trading_symbol": "", "instrument_key": "NSE_EQ|EMPTY"},
    ]


def test_index_rows_match_a_linear_scan_including_empty_keys() -> None:
    clear_cache()
    master = _synthetic()
    for symbol in ("RELIANCE", "reliance", "", "MISSING"):
        assert index_for(master).fo_by_underlying().get(symbol.strip().upper(), []) == _naive_fo_rows(master, symbol)
        assert [
            e for e in index_for(master).by_trading_symbol().get(symbol.strip().upper(), [])
            if isinstance(e.get("instrument_key"), str)
        ] == _naive_resolve(master, symbol, None)
    assert is_fo_eligible(master, "") is True  # the empty-underlying row still matches, as a scan would
    assert lot_size(master, "RELIANCE") == 500
    assert futures_instrument_key(master, "RELIANCE", expiry=date(2026, 9, 24)) == "NSE_FO|F"


def test_cache_never_serves_a_different_master() -> None:
    clear_cache()
    first = _synthetic()
    assert is_fo_eligible(first, "RELIANCE")
    second = [row for row in _synthetic() if row.get("underlying_symbol") != "RELIANCE"]
    assert not is_fo_eligible(second, "RELIANCE")
    assert index_for(second).master is second


@pytest.mark.skipif(not _REAL_MASTER.exists(), reason="real instrument master not cached locally")
def test_equivalence_against_the_real_upstox_master() -> None:
    clear_cache()
    master: list[dict[str, object]] = json.loads(_REAL_MASTER.read_text(encoding="utf-8"))
    for symbol in ("RELIANCE", "NIFTY", "BANKNIFTY", "KAYNES", "SBIN", "TCS", "M&M", "NOTAREALSYMBOL"):
        for segment in (None, "NSE_EQ", "NSE_INDEX"):
            expected = _naive_resolve(master, symbol, segment)
            ref = resolve_symbol(master, symbol, segment=segment)
            assert (ref is None) == (not expected)
        fo_rows = _naive_fo_rows(master, symbol)
        assert is_fo_eligible(master, symbol) == bool(fo_rows)
        expiries = {
            datetime.fromtimestamp(float(e["expiry"]) / 1000, tz=UTC).date()  # type: ignore[arg-type]
            for e in fo_rows if isinstance(e.get("expiry"), int | float)
        }
        assert {info.expiry for info in list_expiries(master, symbol)} == expiries
        assert all(info.expiry >= date(2000, 1, 1) for info in list_expiries(master, symbol))
