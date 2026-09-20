"""Last-observed index prints from already-persisted quotes.

Read-only presentation. Never calls a provider. Never fabricates a price.
A missing instrument or missing quote is UNAVAILABLE with a reason.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from app.data.providers.upstox_instrument_master import resolve_symbol
from app.domain.market.models import Quote
from app.domain.market.trading_calendar import classify_session_window
from app.domain.options.freshness_label import FreshnessLabel
from app.utils.time import to_ist


class _QuoteLookup(Protocol):
    async def latest(self, *, instrument_id: str, as_of: datetime) -> Quote | None: ...


_INDEX_CELLS: tuple[tuple[str, str, str], ...] = (
    ("nifty", "NIFTY", "NIFTY50"),
    ("banknifty", "BANKNIFTY", "BANKNIFTY"),
    ("vix", "INDIA VIX", "INDIA VIX"),
)


def _unavailable(*, label: str, symbol: str, reason: str) -> dict[str, Any]:
    return {
        "label": label,
        "symbol": symbol,
        "analyze_symbol": symbol.split()[0] if symbol != "INDIA VIX" else "INDIA VIX",
        "last_price": None,
        "day_change_pct": None,
        "observed_at": None,
        "observed_at_ist": None,
            "observation_kind": "UNAVAILABLE",
            "freshness_label": FreshnessLabel.UNAVAILABLE.value,
            "unavailable_reason": reason,
    }


def _day_change_pct(quote: Quote) -> Decimal | None:
    if quote.previous_close is None or quote.previous_close == 0:
        return None
    return (quote.last_price - quote.previous_close) / quote.previous_close * Decimal(100)


async def build_observed_market(
    *,
    instrument_master: Sequence[dict[str, object]] | None,
    quotes: _QuoteLookup | None,
    as_of: datetime,
) -> dict[str, Any]:
    window = classify_session_window(as_of)
    session_kind = "LIVE" if window.session_window == "OPEN" else "LAST_OBSERVED"
    cells: dict[str, dict[str, Any]] = {}
    wanted: dict[str, str] = {}
    if instrument_master is None:
        for cell_id, label, symbol in _INDEX_CELLS:
            cells[cell_id] = _unavailable(label=label, symbol=symbol, reason="instrument master not loaded")
    else:
        for cell_id, label, symbol in _INDEX_CELLS:
            ref = resolve_symbol(instrument_master, symbol)
            if ref is None:
                cells[cell_id] = _unavailable(
                    label=label, symbol=symbol, reason="instrument not in loaded master",
                )
                continue
            wanted[cell_id] = ref.instrument_key

    latest: dict[str, Quote] = {}
    if quotes is not None and wanted:
        batched = getattr(quotes, "latest_for_ids", None)
        if callable(batched):
            latest = await batched(frozenset(wanted.values()), as_of=as_of)
        else:
            for instrument_id in wanted.values():
                quote = await quotes.latest(instrument_id=instrument_id, as_of=as_of)
                if quote is not None:
                    latest[instrument_id] = quote
    elif quotes is None:
        for cell_id, label, symbol in _INDEX_CELLS:
            if cell_id not in cells:
                cells[cell_id] = _unavailable(label=label, symbol=symbol, reason="quote store not configured")

    for cell_id, label, symbol in _INDEX_CELLS:
        if cell_id in cells:
            continue
        instrument_id = wanted[cell_id]
        quote = latest.get(instrument_id)
        if quote is None:
            cells[cell_id] = _unavailable(
                label=label, symbol=symbol, reason="no persisted quote observation",
            )
            continue
        observed_ist = to_ist(quote.freshness.data_timestamp)
        change = _day_change_pct(quote)
        cells[cell_id] = {
            "label": label,
            "symbol": symbol,
            "analyze_symbol": "NIFTY" if cell_id == "nifty" else ("BANKNIFTY" if cell_id == "banknifty" else "INDIA VIX"),
            "last_price": str(quote.last_price),
            "day_change_pct": str(change) if change is not None else None,
            "observed_at": quote.freshness.data_timestamp.isoformat(),
            "observed_at_ist": observed_ist.strftime("%d %b · %H:%M IST"),
            "observation_kind": session_kind,
            "freshness_label": (
                FreshnessLabel.LIVE.value if session_kind == "LIVE" else FreshnessLabel.MARKET_CLOSED.value
            ),
            "unavailable_reason": None,
            "instrument_id": instrument_id,
        }

    observed_dates = [
        datetime.fromisoformat(cell["observed_at"]).date()
        for cell in cells.values()
        if cell.get("observed_at")
    ]
    newest = max(observed_dates) if observed_dates else None
    if newest is not None:
        for cell in cells.values():
            stamp = cell.get("observed_at")
            if not stamp:
                continue
            if datetime.fromisoformat(stamp).date() < newest:
                cell["freshness_label"] = FreshnessLabel.STALE.value

    return {
        "session_window": window.session_window,
        "calendar_date_ist": window.calendar_date_ist.isoformat(),
        "is_trading_day": window.is_trading_day,
        "basis": window.basis,
        "as_of": as_of.isoformat(),
        "indices": cells,
        "breadth": {
            "observation_kind": "UNAVAILABLE",
            "unavailable_reason": "sample breadth is only present after a scan or symbol analysis observes it",
            "advances": None,
            "declines": None,
            "observed_count": None,
        },
    }
