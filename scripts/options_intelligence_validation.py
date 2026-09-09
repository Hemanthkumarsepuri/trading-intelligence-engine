"""Real-data validation for the Options Intelligence Engine's data-foundation
layer. For each symbol: resolve the real instrument -> check real F&O
eligibility -> find the real nearest expiry -> fetch the real option chain
-> run chain-quality checks -> compute ATM/PCR/top-OI -> (if available)
fetch the real futures quote. Every data point printed comes from a real
Upstox API call made during this run; nothing is mocked or fabricated.

No order-placement/modification/cancellation endpoint exists anywhere in
this codebase's Upstox adapter, and none is called here.

Run: `python -m scripts.options_intelligence_validation [SYMBOL ...]`
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import httpx

from app.config.settings import settings
from app.data.normalization.base import DefaultNormalizer
from app.data.providers.exceptions import ProviderError
from app.data.providers.upstox_fo_master import (
    futures_instrument_key,
    is_fo_eligible,
    lot_size,
    nearest_expiry,
)
from app.data.providers.upstox_instrument_master import fetch_instrument_master, resolve_symbol
from app.data.providers.upstox_provider import UpstoxProvider
from app.domain.market.models import OptionRight
from app.domain.options.chain_analysis import atm_strike, chain_totals, top_oi_strikes
from app.domain.options.data_quality import check_chain_quality
from app.utils.time import to_ist, utc_now

DEFAULT_SYMBOLS = ["NIFTY50", "BANKNIFTY", "RELIANCE", "TCS", "SBIN"]
_MASTER_CACHE_PATH = Path("data/reference/upstox_nse_instruments.json")

# Illustrative-only data-quality thresholds for THIS validation run -- not a
# tuned production value. `check_chain_quality()` deliberately requires the
# caller to supply these explicitly rather than defaulting them (see its
# docstring): there is no single correct "stale" or "wide" threshold for
# every underlying/liquidity tier.
_MAX_CHAIN_AGE = timedelta(seconds=60)
_MAX_SPREAD_FRACTION = Decimal("0.20")


async def _validate_one(
    symbol: str, *, master: list[dict[str, object]], provider: UpstoxProvider, normalizer: DefaultNormalizer
) -> str:
    lines = [f"=== {symbol} ==="]

    t0 = time.perf_counter()
    ref = resolve_symbol(master, symbol)
    if ref is None:
        lines.append("  UNRESOLVED -- not found in the real instrument master")
        return "\n".join(lines)
    resolve_ms = (time.perf_counter() - t0) * 1000
    lines.append(f"  instrument_key: {ref.instrument_key}  (resolved in {resolve_ms:.1f}ms)")

    underlying_symbol = ref.trading_symbol
    eligible = is_fo_eligible(master, underlying_symbol)
    lines.append(f"  F&O eligible: {eligible}")
    if not eligible:
        lines.append("  -- no derivatives segment for this underlying; stopping here.")
        return "\n".join(lines)

    size = lot_size(master, underlying_symbol)
    lines.append(f"  lot size: {size}")

    t1 = time.perf_counter()
    as_of = utc_now()
    expiry_info = nearest_expiry(master, underlying_symbol, as_of=as_of.date())
    expiry_ms = (time.perf_counter() - t1) * 1000
    if expiry_info is None:
        lines.append("  -- no upcoming expiry found in the master; stopping here.")
        return "\n".join(lines)
    lines.append(
        f"  nearest expiry: {expiry_info.expiry} (weekly={expiry_info.is_weekly}, "
        f"resolved in {expiry_ms:.1f}ms from cached master)"
    )

    t2 = time.perf_counter()
    as_of = utc_now()
    try:
        raw_chain = await provider.get_chain(underlying=ref.instrument_key, expiry=expiry_info.expiry, as_of=as_of)
    except ProviderError as exc:
        lines.append(f"  CHAIN FETCH FAILED (real API call): {exc}")
        return "\n".join(lines)
    chain_ms = (time.perf_counter() - t2) * 1000
    snapshot = normalizer.normalize_option_chain(raw_chain, received_at=as_of)
    lines.append(
        f"  chain fetched: {len(snapshot.legs)} legs across {len(raw_chain.strikes)} strikes "
        f"({chain_ms:.1f}ms real REST call to /v2/option/chain)"
    )
    lines.append(f"  underlying spot (from chain response): {snapshot.underlying_last_price}")

    atm = atm_strike(snapshot)
    lines.append(f"  ATM strike: {atm}")

    totals = chain_totals(snapshot)
    pcr_str = f"{totals.put_call_ratio_oi:.3f}" if totals.put_call_ratio_oi is not None else "n/a (call OI is zero)"
    lines.append(
        f"  total OI -- calls: {totals.total_call_oi:,}  puts: {totals.total_put_oi:,}  PCR(OI): {pcr_str}"
    )
    lines.append(f"  legs with known OI: {totals.legs_with_known_oi}  missing OI: {totals.legs_with_missing_oi}")

    top_calls = top_oi_strikes(snapshot, right=OptionRight.CE, limit=3)
    top_puts = top_oi_strikes(snapshot, right=OptionRight.PE, limit=3)
    lines.append(
        "  top call OI strikes (candidate resistance): "
        + ", ".join(f"{s.strike}@{s.open_interest:,}" for s in top_calls)
    )
    lines.append(
        "  top put OI strikes (candidate support): "
        + ", ".join(f"{s.strike}@{s.open_interest:,}" for s in top_puts)
    )

    issues = check_chain_quality(
        snapshot, as_of=as_of, max_age=_MAX_CHAIN_AGE, max_spread_fraction=_MAX_SPREAD_FRACTION
    )
    if issues:
        lines.append(f"  DATA QUALITY ISSUES ({len(issues)}):")
        for issue in issues:
            lines.append(f"    - {issue.kind.value}: {issue.detail}")
    else:
        lines.append("  data quality: no issues flagged")

    t3 = time.perf_counter()
    fut_key = futures_instrument_key(master, underlying_symbol, expiry=expiry_info.expiry)
    if fut_key is None:
        lines.append("  futures: no futures contract found in the master for this underlying/expiry")
    else:
        try:
            fut_quotes = await provider.get_quotes([fut_key])
        except ProviderError as exc:
            lines.append(f"  futures quote FETCH FAILED (real API call, {fut_key}): {exc}")
        else:
            fut_ms = (time.perf_counter() - t3) * 1000
            fut_quote = fut_quotes.get(fut_key)
            if fut_quote is None:
                lines.append(f"  futures ({fut_key}): quote not present in the real response")
            else:
                lines.append(
                    f"  futures ({fut_key}): LTP {fut_quote.last_price}  OI {fut_quote.open_interest}  "
                    f"volume {fut_quote.volume}  ({fut_ms:.1f}ms real REST call)"
                )

    return "\n".join(lines)


async def run(symbols: list[str]) -> None:
    if not settings.upstox_access_token:
        print("WAITING: UPSTOX_ACCESS_TOKEN is not set. See docs/data-sources/PROVIDER_DECISION.md.")
        return

    async with httpx.AsyncClient() as client:
        master = await fetch_instrument_master(client, cache_path=_MASTER_CACHE_PATH)
        provider = UpstoxProvider(client=client, access_token=settings.upstox_access_token, base_url=settings.upstox_base_url)
        normalizer = DefaultNormalizer(provider_name=provider.name)

        print("=" * 70)
        print("OPTIONS INTELLIGENCE ENGINE -- REAL DATA VALIDATION")
        print(f"Run time: {to_ist(utc_now()).strftime('%Y-%m-%d %H:%M:%S')} IST")
        print(f"Symbols: {', '.join(symbols)}")
        print("=" * 70)
        print()

        total_started = time.perf_counter()
        for symbol in symbols:
            block = await _validate_one(symbol, master=master, provider=provider, normalizer=normalizer)
            print(block)
            print()
        total_s = time.perf_counter() - total_started

        print("=" * 70)
        print(f"Total real-data validation time for {len(symbols)} symbols: {total_s:.2f}s")
        print("READ-ONLY. NO ORDER/EXECUTION ENDPOINT WAS EVER CALLED.")
        print("=" * 70)


def main() -> None:
    symbols = sys.argv[1:] or DEFAULT_SYMBOLS
    asyncio.run(run(symbols))


if __name__ == "__main__":
    main()
