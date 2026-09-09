"""Fetch real historical M15 NSE candles from Upstox and save them as a CSV
matching `docs/data-sources/CSV_IMPORT.md`'s schema — ready for
`scripts/real_data_replay.py` to consume directly, no further conversion.

Requires `UPSTOX_ACCESS_TOKEN` (see `.env.example` /
`docs/data-sources/PROVIDER_DECISION.md`). Never prints, logs, or persists
the token itself — only whether it is present.

Run:
    python -m scripts.fetch_upstox_historical <instrument_key> <instrument_id> <from_date> <to_date> [out_path]

Example (instrument_key format: EXCHANGE_SEGMENT|ISIN — confirm the exact
key from Upstox's own instrument master before relying on this example):
    python -m scripts.fetch_upstox_historical "NSE_EQ|INE002A01018" RELIANCE 2026-07-01 2026-08-27
"""

from __future__ import annotations

import asyncio
import csv
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import httpx

from app.config.settings import settings
from app.data.providers.exceptions import ProviderError
from app.data.providers.upstox_provider import UpstoxProvider
from app.domain.market.models import ExchangeSegment, Timeframe
from app.utils.time import utc_now


class MissingCredentialError(RuntimeError):
    """Raised when UPSTOX_ACCESS_TOKEN is not configured. Never carries the
    (absent) token value itself — only a description of what's missing and
    where to configure it.
    """


def _require_token() -> str:
    if not settings.upstox_access_token:
        raise MissingCredentialError(
            "UPSTOX_ACCESS_TOKEN is not set. Generate one (read-only, 1-year validity, no OAuth flow) "
            "from the Upstox Developer dashboard's Analytics tab, then set UPSTOX_ACCESS_TOKEN in your "
            "real .env file (see .env.example and docs/data-sources/PROVIDER_DECISION.md). "
            "This is the ONE credential this pipeline needs."
        )
    return settings.upstox_access_token


async def fetch_and_save(
    *, instrument_key: str, instrument_id: str, from_date: date, to_date: date, out_path: Path
) -> int:
    """Fetches real M15 candles for `[from_date, to_date]` and writes them as
    a CSV matching `docs/data-sources/CSV_IMPORT.md`'s schema. Returns the
    number of candles written. Raises `MissingCredentialError` or a
    `ProviderError` subclass on failure — never silently writes a partial or
    fabricated file.
    """
    token = _require_token()
    async with httpx.AsyncClient() as client:
        provider = UpstoxProvider(client=client, access_token=token, base_url=settings.upstox_base_url)
        raw_candles = await provider.get_ohlcv(
            security_id=instrument_key,
            exchange_segment=ExchangeSegment.NSE_EQ,
            timeframe=Timeframe.M15,
            start=datetime(from_date.year, from_date.month, from_date.day, tzinfo=UTC),
            end=datetime(to_date.year, to_date.month, to_date.day, 23, 59, 59, tzinfo=UTC),
            as_of=utc_now(),
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        for candle in raw_candles:
            writer.writerow(
                {
                    "timestamp": candle.timestamp.isoformat(),
                    "open": candle.open,
                    "high": candle.high,
                    "low": candle.low,
                    "close": candle.close,
                    "volume": candle.volume,
                }
            )

    return len(raw_candles)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) not in (4, 5):
        print(
            "usage: python -m scripts.fetch_upstox_historical <instrument_key> <instrument_id> "
            "<from_date YYYY-MM-DD> <to_date YYYY-MM-DD> [out_path]"
        )
        return 2

    instrument_key, instrument_id, from_date_raw, to_date_raw = args[:4]
    out_path = Path(args[4]) if len(args) == 5 else Path("data/historical_replay") / f"{instrument_id}_M15.csv"

    try:
        from_date = date.fromisoformat(from_date_raw)
        to_date = date.fromisoformat(to_date_raw)
    except ValueError as exc:
        print(f"error: {exc}")
        return 2

    try:
        count = asyncio.run(
            fetch_and_save(
                instrument_key=instrument_key,
                instrument_id=instrument_id,
                from_date=from_date,
                to_date=to_date,
                out_path=out_path,
            )
        )
    except MissingCredentialError as exc:
        print(f"WAITING: {exc}")
        return 3
    except ProviderError as exc:
        print(f"error: provider call failed: {exc}")
        return 1

    print(f"Saved {count} real candles to {out_path}")
    print(f"Next: python -m scripts.real_data_replay {out_path} {instrument_id} NSE_EQ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
