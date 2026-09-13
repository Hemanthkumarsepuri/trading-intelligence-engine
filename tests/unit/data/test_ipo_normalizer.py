"""IPO normalizer -- Raw (Upstox wire shape) -> domain `IPOIdentity`."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from app.data.normalization.ipo_normalizer import normalize_ipo_detail, normalize_ipo_listing
from app.data.providers.base import (
    RawIPODetail,
    RawIPOInvestorCategory,
    RawIPOListing,
    RawIPORegistrarInfo,
    RawIPOTimeline,
)
from app.domain.ipo.models import IssueType

_NOW = datetime(2026, 8, 30, 10, 0, tzinfo=UTC)


def test_normalize_listing_maps_real_fields() -> None:
    raw = RawIPOListing(
        id="ashutosh-fibre-limited-ipo", symbol="ASHUTOSH", name="Ashutosh Fibre IPO", status="open",
        isin="INE19FR01012", issue_type="sme", issue_size=56.0, industry="Textile",
        minimum_price=87.0, maximum_price=92.0, bidding_start_date=date(2026, 8, 31),
        bidding_end_date=date(2026, 9, 2), total_subscription="0.0",
    )
    identity = normalize_ipo_listing(raw, source="upstox", retrieved_at=_NOW)
    assert identity.company_name == "Ashutosh Fibre IPO"
    assert identity.issue_type == IssueType.SME
    assert identity.price_band_low == Decimal("87.0")
    assert identity.price_band_high == Decimal("92.0")
    assert identity.symbol == "ASHUTOSH"
    assert identity.status == "open"
    assert identity.source == "upstox"


def test_normalize_listing_regular_issue_type_is_mainboard() -> None:
    raw = RawIPOListing(
        id="x", symbol=None, name="X IPO", status="upcoming", isin=None, issue_type="regular",
        issue_size=None, industry=None, minimum_price=None, maximum_price=None,
        bidding_start_date=None, bidding_end_date=None, total_subscription=None,
    )
    identity = normalize_ipo_listing(raw, source="upstox", retrieved_at=_NOW)
    assert identity.issue_type == IssueType.MAINBOARD


def test_normalize_listing_unrecognized_issue_type_is_unknown_never_guessed() -> None:
    raw = RawIPOListing(
        id="x", symbol=None, name="X IPO", status="upcoming", isin=None, issue_type="something-new",
        issue_size=None, industry=None, minimum_price=None, maximum_price=None,
        bidding_start_date=None, bidding_end_date=None, total_subscription=None,
    )
    identity = normalize_ipo_listing(raw, source="upstox", retrieved_at=_NOW)
    assert identity.issue_type == IssueType.UNKNOWN


def test_normalize_detail_maps_timeline_registrar_and_real_listing_price() -> None:
    raw = RawIPODetail(
        id="tempsens-instruments-india-limited-ipo", symbol="TEMPSENS", name="Tempsens Instruments (India) IPO",
        status="listed", isin="INE1KZI01025", issue_type="regular", issue_size=650.0, industry="Engineering",
        minimum_price=285.0, maximum_price=300.0, bidding_start_date=date(2026, 8, 20), bidding_end_date=date(2026, 8, 24),
        total_subscription="213.92", face_value=4.0, tick_size=None, lot_size=50, minimum_quantity=50,
        cut_off_price=300.0, listing_price=634.0, listing_exchange="BSE,NSE",
        rhp_url="https://example.com/rhp.pdf", drhp_url=None,
        timeline=RawIPOTimeline(listing_date=date(2026, 8, 28), allotment_date=date(2026, 8, 27)),
        registrar_info=RawIPORegistrarInfo(registrar="K FINTECH"),
        investors=[RawIPOInvestorCategory(category="IND"), RawIPOInvestorCategory(category="HNI")],
    )
    identity = normalize_ipo_detail(raw, source="upstox", retrieved_at=_NOW)
    assert identity.listing_price == Decimal("634.0")
    assert identity.listing_date == date(2026, 8, 28)
    assert identity.registrar == "K FINTECH"
    assert identity.investor_categories == ["IND", "HNI"]
    assert identity.rhp_url == "https://example.com/rhp.pdf"
    assert identity.lot_size == 50


def test_normalize_detail_unlisted_ipo_has_no_listing_price_never_fabricated() -> None:
    raw = RawIPODetail(
        id="x", symbol=None, name="X IPO", status="open", isin=None, issue_type="sme", issue_size=None,
        industry=None, minimum_price=None, maximum_price=None, bidding_start_date=None, bidding_end_date=None,
        total_subscription=None, listing_price=None,
    )
    identity = normalize_ipo_detail(raw, source="upstox", retrieved_at=_NOW)
    assert identity.listing_price is None
