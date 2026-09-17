"""Backfill a REAL multi-symbol, multi-month M15 candle history for
historical replay, by chunking Upstox's own historical-candle endpoint.

Final release gate (Section 9/10) -- the pattern-aggregation machinery was
complete and correct but had almost nothing to learn from: one local
symbol (RELIANCE) and a handful of observations. This exists to grow that
sample HONESTLY, from the authorized provider already in production, and
from nothing else.

Why chunking is required (measured against the live API, 16 Sep 2026):
Upstox's v3 `historical-candle` endpoint rejects a 15-minute request
spanning much more than a month with `UDAPI1148 Invalid date range`
(`2026-08-15 -> 2026-09-11` returns 500 candles; `2026-07-15 ->
2026-09-11` returns HTTP 400). Data itself is available far further
back -- a `2025-09-01 -> 2025-09-28` probe returned a full 500 candles --
so the limit is per-REQUEST span, not history depth. This walks backwards
in `_CHUNK_DAYS` windows and concatenates.

What this does NOT do, deliberately:
  - never fabricates, interpolates, or forward-fills a missing candle;
    a window the provider returns nothing for simply contributes nothing
  - never invents derivatives history. These are PRICE candles only.
    Replay observations built from them stay honestly price-only
    (`derivatives_evidence_available=False`), and derivatives-dependent
    patterns remain unevaluable -- see `docs/HISTORICAL_REPLAY.md`.
  - never writes a partial file over a good one: each symbol's CSV is
    written once, at the end, only if candles were actually retrieved.

Run:
    python -m scripts.backfill_replay_history [months] [symbol ...]
    python -m scripts.backfill_replay_history [months] CONTEXT   # NIFTY 50 market-context series

With no arguments it backfills the default liquid-symbol set for six
months. Requires `UPSTOX_ACCESS_TOKEN` (never printed or persisted).
"""

from __future__ import annotations

import asyncio
import csv
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx

from app.config.settings import settings
from app.data.providers.exceptions import ProviderError
from app.data.providers.upstox_provider import UpstoxProvider
from app.domain.market.models import ExchangeSegment, Timeframe
from app.utils.time import to_ist, utc_now

# Comfortably inside the measured per-request limit, with margin -- a
# 28-day window returned a full page in every probe.
_CHUNK_DAYS = 28
_OUT_DIR = Path("data/historical_replay")

# Liquid, continuously-traded NSE F&O underlyings across several real
# sectors (banking, IT, energy, metals, FMCG, pharma, auto, infra), so the
# resulting sample is not one sector's regime wearing many tickers.
# `instrument_key` values come from Upstox's own instrument master.
DEFAULT_SYMBOLS: dict[str, str] = {
    "RELIANCE": "NSE_EQ|INE002A01018",
    "SBIN": "NSE_EQ|INE062A01020",
    "INFY": "NSE_EQ|INE009A01021",
    "TCS": "NSE_EQ|INE467B01029",
    "ICICIBANK": "NSE_EQ|INE090A01021",
    "HDFCBANK": "NSE_EQ|INE040A01034",
    "ITC": "NSE_EQ|INE154A01025",
    "LT": "NSE_EQ|INE018A01030",
    "AXISBANK": "NSE_EQ|INE238A01034",
    "KOTAKBANK": "NSE_EQ|INE237A01036",
    "WIPRO": "NSE_EQ|INE075A01022",
    "TATASTEEL": "NSE_EQ|INE081A01020",
    "ONGC": "NSE_EQ|INE213A01029",
    "MARUTI": "NSE_EQ|INE585B01010",
    "SUNPHARMA": "NSE_EQ|INE044A01036",
    # Release gate (Section 9) -- widened from 15 large caps so the sample
    # also covers telecom, consumer, cement, power, capital goods, aviation,
    # realty, PSU banks and several MID-CAP F&O names (KAYNES, DIXON,
    # PERSISTENT, TRENT) whose price behaviour differs from index heavyweights.
    # Keys resolved from the cached Upstox instrument master, 17 Sep 2026.
    "BHARTIARTL": "NSE_EQ|INE397D01024",
    "HINDUNILVR": "NSE_EQ|INE030A01027",
    "BAJFINANCE": "NSE_EQ|INE296A01032",
    "ASIANPAINT": "NSE_EQ|INE021A01026",
    "TITAN": "NSE_EQ|INE280A01028",
    "ULTRACEMCO": "NSE_EQ|INE481G01011",
    "NTPC": "NSE_EQ|INE733E01010",
    "POWERGRID": "NSE_EQ|INE752E01010",
    "M&M": "NSE_EQ|INE101A01026",
    "HCLTECH": "NSE_EQ|INE860A01027",
    "TECHM": "NSE_EQ|INE669C01036",
    "JSWSTEEL": "NSE_EQ|INE019A01038",
    "HINDALCO": "NSE_EQ|INE038A01020",
    "COALINDIA": "NSE_EQ|INE522F01014",
    "DRREDDY": "NSE_EQ|INE089A01031",
    "CIPLA": "NSE_EQ|INE059A01026",
    "NESTLEIND": "NSE_EQ|INE239A01024",
    "BAJAJ-AUTO": "NSE_EQ|INE917I01010",
    "EICHERMOT": "NSE_EQ|INE066A01021",
    "ADANIPORTS": "NSE_EQ|INE742F01042",
    "DLF": "NSE_EQ|INE271C01023",
    "BEL": "NSE_EQ|INE263A01024",
    "TRENT": "NSE_EQ|INE849A01020",
    "INDIGO": "NSE_EQ|INE646L01027",
    "KAYNES": "NSE_EQ|INE918Z01012",
    "DIXON": "NSE_EQ|INE935N01020",
    "PERSISTENT": "NSE_EQ|INE262H01021",
    "VEDL": "NSE_EQ|INE205A01025",
    "BANKBARODA": "NSE_EQ|INE028A01039",
    "PNB": "NSE_EQ|INE160A01022",
}


# Market-context series. Written to `_OUT_DIR / "context"` -- a SEPARATE
# directory -- so the dataset builder can never mistake an index for a
# replayable stock. Used only to record, as-of bounded, what the broad
# market had done at each observation instant.
CONTEXT_SYMBOLS: dict[str, str] = {"NIFTY50": "NSE_INDEX|Nifty 50"}


class MissingCredentialError(RuntimeError):
    """Raised when UPSTOX_ACCESS_TOKEN is absent. Carries only a
    description of what is missing, never the (absent) value."""


def _require_token() -> str:
    if not settings.upstox_access_token:
        raise MissingCredentialError(
            "UPSTOX_ACCESS_TOKEN is not set -- see .env.example and "
            "docs/data-sources/PROVIDER_DECISION.md"
        )
    return settings.upstox_access_token


def _chunks(start: date, end: date) -> list[tuple[date, date]]:
    """Consecutive `<= _CHUNK_DAYS` windows covering `[start, end]`."""
    windows: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        stop = min(cursor + timedelta(days=_CHUNK_DAYS - 1), end)
        windows.append((cursor, stop))
        cursor = stop + timedelta(days=1)
    return windows


async def backfill_symbol(
    provider: UpstoxProvider, *, symbol: str, instrument_key: str, start: date, end: date, out_dir: Path = _OUT_DIR,
) -> int:
    """Fetch and write one symbol's real M15 series. Returns candles
    written (0 means nothing was retrieved and NO file was written --
    never an empty or partial CSV left behind)."""
    by_timestamp: dict[datetime, tuple[object, ...]] = {}
    for chunk_start, chunk_end in _chunks(start, end):
        try:
            raw = await provider.get_ohlcv(
                security_id=instrument_key, exchange_segment=ExchangeSegment.NSE_EQ, timeframe=Timeframe.M15,
                start=datetime(chunk_start.year, chunk_start.month, chunk_start.day, tzinfo=UTC),
                end=datetime(chunk_end.year, chunk_end.month, chunk_end.day, 23, 59, 59, tzinfo=UTC),
                as_of=utc_now(),
            )
        except ProviderError as exc:
            # One bad window must never discard the windows that worked.
            print(f"    {chunk_start} -> {chunk_end}: SKIPPED ({type(exc).__name__}: {str(exc)[:70]})")
            continue
        for candle in raw:
            # Keyed by real timestamp, so overlapping windows de-duplicate
            # rather than double-count a session.
            by_timestamp[candle.timestamp] = (
                candle.open, candle.high, candle.low, candle.close, candle.volume,
            )
        print(f"    {chunk_start} -> {chunk_end}: {len(raw)} candles")

    if not by_timestamp:
        return 0

    out_path = out_dir / f"{symbol}_M15.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        for timestamp in sorted(by_timestamp):
            o, h, low, c, v = by_timestamp[timestamp]
            writer.writerow({
                "timestamp": timestamp.isoformat(), "open": o, "high": h, "low": low, "close": c, "volume": v,
            })
    return len(by_timestamp)


async def run(months: int, symbols: dict[str, str], *, out_dir: Path = _OUT_DIR) -> int:
    token = _require_token()
    # IST, not the machine's local date -- the trading day this history
    # belongs to is an NSE date, and `utc_now()` is what the rest of the
    # codebase measures "now" with.
    end = to_ist(utc_now()).date() - timedelta(days=1)
    start = end - timedelta(days=int(months * 30.5))
    total = 0
    async with httpx.AsyncClient(timeout=60) as client:
        provider = UpstoxProvider(client=client, access_token=token, base_url=settings.upstox_base_url)
        for symbol, instrument_key in symbols.items():
            print(f"--- {symbol}  {start} -> {end}")
            written = await backfill_symbol(
                provider, symbol=symbol, instrument_key=instrument_key, start=start, end=end, out_dir=out_dir,
            )
            print(f"    WROTE {written} candles")
            total += written
    return total


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    months = int(args[0]) if args and args[0].isdigit() else 6
    wanted = [a.upper() for a in args[1:]] if len(args) > 1 else []
    if wanted == ["CONTEXT"]:
        try:
            total = asyncio.run(run(months, CONTEXT_SYMBOLS, out_dir=_OUT_DIR / "context"))
        except MissingCredentialError as exc:
            print(f"error: {exc}")
            return 1
        print(f"\nTOTAL real context candles written: {total}")
        return 0
    symbols = {s: k for s, k in DEFAULT_SYMBOLS.items() if not wanted or s in wanted}
    if not symbols:
        print(f"no known symbols in {wanted!r}; known: {sorted(DEFAULT_SYMBOLS)}")
        return 2
    try:
        total = asyncio.run(run(months, symbols))
    except MissingCredentialError as exc:
        print(f"error: {exc}")
        return 1
    print(f"\nTOTAL real candles written: {total} across {len(symbols)} symbol(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
