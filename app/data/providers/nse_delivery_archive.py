"""Official NSE end-of-day security bhavcopy with delivery columns.

Verified live 2026-09-08 against:
`https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{ddmmyyyy}.csv`

Same static-archive class as `nse_sector_index.py` (plain GET of a public
CSV NSE publishes). Not an interactive nseindia.com page scrape.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import httpx

from app.data.providers.exceptions import ProviderMalformedResponse, ProviderUnavailable
from app.domain.market.delivery_context import DeliveryFreshness, DeliveryObservation

DELIVERY_ARCHIVE_URL_TEMPLATE = (
    "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{ddmmyyyy}.csv"
)
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; trading-intelligence-engine/1.0)"}


def _csv_from_header(text: str, header_token: str) -> str:
    lines = text.lstrip("\ufeff").splitlines()
    needle = header_token.upper()
    for i, line in enumerate(lines):
        cells = [c.strip().upper() for c in line.split(",")]
        if needle in cells:
            return "\n".join(lines[i:])
    return text


def _url_for(trade_date: date) -> str:
    return DELIVERY_ARCHIVE_URL_TEMPLATE.format(ddmmyyyy=trade_date.strftime("%d%m%Y"))


def _cache_path(cache_dir: Path, trade_date: date) -> Path:
    return cache_dir / f"sec_bhavdata_full_{trade_date.strftime('%d%m%Y')}.csv"


def parse_delivery_row(text: str, *, symbol: str, series: str = "EQ") -> tuple[date | None, int | None, int | None, Decimal | None, str | None]:
    """Returns (trade_date, traded_qty, deliv_qty, deliv_pct, series) for the
    first matching EQ (or requested series) row. Missing columns -> None.
    """
    reader = csv.DictReader(io.StringIO(_csv_from_header(text, "SYMBOL")))
    if reader.fieldnames is None:
        raise ProviderMalformedResponse("delivery archive missing header")
    fields = {name.strip(): name for name in reader.fieldnames}
    required = ("SYMBOL", "SERIES")
    if any(key not in fields for key in required):
        raise ProviderMalformedResponse(f"delivery archive missing expected columns (got {reader.fieldnames!r})")

    needle = symbol.strip().upper()
    for row in reader:
        row_symbol = (row.get(fields["SYMBOL"]) or "").strip().upper()
        row_series = (row.get(fields["SERIES"]) or "").strip().upper()
        if row_symbol != needle or row_series != series.upper():
            continue
        date_raw = (row.get(fields.get("DATE1", "DATE1"), "") or "").strip()
        trade_date = _parse_trade_date(date_raw)
        traded = _optional_int(_cell(row, fields, "TTL_TRD_QNTY"))
        deliv_qty = _optional_int(_cell(row, fields, "DELIV_QTY"))
        deliv_pct = _optional_decimal(_cell(row, fields, "DELIV_PER"))
        return trade_date, traded, deliv_qty, deliv_pct, row_series
    return None, None, None, None, None


def _cell(row: dict[str, str], fields: dict[str, str], logical: str) -> str:
    raw_name = fields.get(logical)
    if raw_name is None:
        for stripped, original in fields.items():
            if stripped == logical:
                raw_name = original
                break
    if raw_name is None:
        return ""
    return (row.get(raw_name) or "").strip()


def _parse_trade_date(raw: str) -> date | None:
    if not raw:
        return None
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()  # noqa: DTZ007 -- calendar date only, no clock
        except ValueError:
            continue
    return None


def _optional_int(raw: str) -> int | None:
    if raw == "" or raw.upper() in {"-", "NA", "N/A"}:
        return None
    try:
        return int(Decimal(raw.replace(",", "")))
    except (InvalidOperation, ValueError):
        return None


def _optional_decimal(raw: str) -> Decimal | None:
    if raw == "" or raw.upper() in {"-", "NA", "N/A"}:
        return None
    try:
        return Decimal(raw.replace(",", ""))
    except InvalidOperation:
        return None


async def fetch_delivery_observation(
    client: httpx.AsyncClient,
    *,
    symbol: str,
    trade_date: date,
    retrieved_at: datetime,
    session: DeliveryFreshness,
    cache_dir: Path | None = None,
) -> DeliveryObservation:
    url = _url_for(trade_date)
    cache_path = _cache_path(cache_dir, trade_date) if cache_dir is not None else None
    if cache_path is not None and cache_path.exists():
        text = cache_path.read_text(encoding="utf-8")
    else:
        try:
            response = await client.get(url, headers=_HEADERS, timeout=30.0)
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"could not fetch NSE delivery archive: {exc}") from exc
        if response.status_code >= 400:
            raise ProviderMalformedResponse(f"NSE delivery archive returned HTTP {response.status_code} for {url}")
        text = response.text
        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(text, encoding="utf-8")

    parsed_date, traded, deliv_qty, deliv_pct, series = parse_delivery_row(text, symbol=symbol)
    if series is None:
        return DeliveryObservation(
            symbol=symbol, trade_date=trade_date, traded_quantity=None, deliverable_quantity=None,
            delivery_pct=None, series=None, source=url, retrieved_at=retrieved_at, session=session,
            detail=f"no EQ row for {symbol} in NSE delivery archive for {trade_date.isoformat()}",
        )
    return DeliveryObservation(
        symbol=symbol, trade_date=parsed_date or trade_date, traded_quantity=traded,
        deliverable_quantity=deliv_qty, delivery_pct=deliv_pct, series=series, source=url,
        retrieved_at=retrieved_at, session=session,
        detail=(
            f"{symbol} delivery {deliv_pct}% (traded {traded}, deliverable {deliv_qty}) -- "
            f"{session.value} NSE archive trade date {(parsed_date or trade_date).isoformat()}"
            if deliv_pct is not None
            else f"{symbol} delivery columns missing in NSE archive for {(parsed_date or trade_date).isoformat()}"
        ),
    )
