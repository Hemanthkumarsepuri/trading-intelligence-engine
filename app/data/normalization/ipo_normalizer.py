"""Converts Upstox's real `RawIPOListing`/`RawIPODetail` (data layer,
wire-shaped) into the domain `IPOIdentity` (never the reverse — `domain/`
never imports `data/`, per this codebase's dependency-direction rule;
this converter lives here in `data/normalization/`, the same layer
`DefaultNormalizer` already occupies for every other provider).

Pure field mapping only — no interpretation, no scoring, no invented
field. `issue_type`/`status` pass through Upstox's own real string values
after validation against the known enum; an unrecognized value raises
rather than silently defaulting, since a provider adding a new real value
should be a loud signal, not a silently-swallowed one.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.data.providers.base import RawIPODetail, RawIPOListing
from app.domain.ipo.models import IPOIdentity, IssueType

_ISSUE_TYPE_MAP = {"sme": IssueType.SME, "regular": IssueType.MAINBOARD}


def _issue_type(raw: str) -> IssueType:
    return _ISSUE_TYPE_MAP.get(raw.lower(), IssueType.UNKNOWN)


def _decimal(value: float | str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (ValueError, ArithmeticError):
        return None


def normalize_ipo_listing(raw: RawIPOListing, *, source: str, retrieved_at: datetime) -> IPOIdentity:
    """From the cheaper list-view shape (`GET /v2/ipos`) -- no
    timeline/registrar/investor-category detail (that requires
    `get_ipo_detail()`)."""
    return IPOIdentity(
        company_name=raw.name, issue_type=_issue_type(raw.issue_type),
        price_band_low=_decimal(raw.minimum_price), price_band_high=_decimal(raw.maximum_price),
        opening_date=raw.bidding_start_date, closing_date=raw.bidding_end_date,
        source=source, retrieved_at=retrieved_at,
        ipo_id=raw.id, symbol=raw.symbol, isin=raw.isin, status=raw.status,
        aggregate_subscription_times=_decimal(raw.total_subscription),
    )


def normalize_ipo_detail(raw: RawIPODetail, *, source: str, retrieved_at: datetime) -> IPOIdentity:
    """From the richer per-IPO shape (`GET /v2/ipos/{id}`)."""
    return IPOIdentity(
        company_name=raw.name, issue_type=_issue_type(raw.issue_type),
        price_band_low=_decimal(raw.minimum_price), price_band_high=_decimal(raw.maximum_price),
        lot_size=raw.lot_size, face_value=_decimal(raw.face_value),
        opening_date=raw.bidding_start_date, closing_date=raw.bidding_end_date,
        allotment_date=raw.timeline.allotment_date, listing_date=raw.timeline.listing_date,
        registrar=raw.registrar_info.registrar,
        source=source, retrieved_at=retrieved_at,
        ipo_id=raw.id, symbol=raw.symbol, isin=raw.isin, status=raw.status,
        listing_price=_decimal(raw.listing_price), listing_exchange=raw.listing_exchange,
        rhp_url=raw.rhp_url, drhp_url=raw.drhp_url,
        investor_categories=[c.category for c in raw.investors],
        aggregate_subscription_times=_decimal(raw.total_subscription),
    )
