from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from app.data.providers.nse_delivery_archive import parse_delivery_row
from app.domain.market.delivery_context import DeliveryFreshness, unknown_delivery
from app.domain.market.institutional_flows import FlowAvailability, unknown_institutional_flows


def test_parse_delivery_row_extracts_eq_columns() -> None:
    csv = (
        "ignored preamble\n"
        "SYMBOL, SERIES, DATE1, TTL_TRD_QNTY, DELIV_QTY, DELIV_PER\n"
        "RELIANCE, EQ, 08-Sep-2026, 1000, 314, 31.40\n"
        "TCS, EQ, 08-Sep-2026, 2000, 500, 25.00\n"
    )
    trade_date, traded, deliv_qty, deliv_pct, series = parse_delivery_row(csv, symbol="RELIANCE")
    assert trade_date == date(2026, 9, 8)
    assert traded == 1000
    assert deliv_qty == 314
    assert deliv_pct == Decimal("31.40")
    assert series == "EQ"


def test_delivery_unknown_is_not_zero() -> None:
    obs = unknown_delivery(symbol="RELIANCE", retrieved_at=datetime(2026, 9, 9, tzinfo=UTC), reason="missing")
    assert obs.delivery_pct is None
    assert obs.traded_quantity is None
    assert obs.session == DeliveryFreshness.UNKNOWN


def test_fii_dii_unavailable_is_unknown_not_neutral() -> None:
    ctx = unknown_institutional_flows(retrieved_at=datetime(2026, 9, 9, tzinfo=UTC))
    assert ctx.availability == FlowAvailability.UNKNOWN
    assert ctx.fii_cash_net is None
    assert "INDEX F&O FLOW" in ctx.detail
    assert "NEUTRAL" not in ctx.detail
