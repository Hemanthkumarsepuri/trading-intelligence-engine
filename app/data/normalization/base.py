"""Normalization: `Raw*` provider DTOs -> canonical `app.domain.market.models`.

This is the boundary that erases provider identity (ARCHITECTURE.md §8 /
Addendum A1). `data/providers` and `data/normalization` are the only two
packages allowed to know a provider's field names; everything past this
module sees only canonical domain types plus a `provider` string for
provenance/audit purposes.

`DefaultNormalizer` is deliberately provider-agnostic: today's Dhan and Mock
mappings are identical in shape (the `Raw*` DTOs are already provider-neutral
by design), so there is nothing Dhan-specific to do here yet. A future
provider whose raw shapes genuinely need different handling (unit
conversion, a different OI-change convention, ...) gets its own `Normalizer`
implementation rather than a special case bolted onto this one.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

from app.data.providers.base import RawCandle, RawOptionChain, RawQuote
from app.domain.market.freshness import DataFreshness
from app.domain.market.models import Candle, OptionChainSnapshot, OptionQuote, Quote, Timeframe
from app.utils.time import ensure_utc

# How far a real exchange timestamp may read AHEAD of this pipeline's
# frozen `as_of`/`received_at` before it's treated as ordinary
# multi-step-fetch clock drift (floored) rather than a likely provider
# defect (left to raise). 5 minutes matches this codebase's existing
# order-of-magnitude for "reasonable data-processing latency"
# (`PipelineConfig.max_option_quote_age`), not a new, unrelated number.
_MAX_CLOCK_SKEW_TOLERANCE = timedelta(minutes=5)


class Normalizer(Protocol):
    """One implementation per provider (or shared, where mappings coincide).
    `provider_name` is stamped onto every produced model's provenance field.
    """

    provider_name: str

    def normalize_candle(
        self, raw: RawCandle, *, instrument_id: str, timeframe: Timeframe, received_at: datetime
    ) -> Candle: ...

    def normalize_quote(self, raw: RawQuote, *, instrument_id: str, received_at: datetime) -> Quote: ...

    def normalize_option_chain(self, raw: RawOptionChain, *, received_at: datetime) -> OptionChainSnapshot: ...


def _to_decimal(value: float) -> Decimal:
    # str() first to avoid binary-float artifacts leaking into Decimal.
    return Decimal(str(value))


def _optional_decimal(value: float | None) -> Decimal | None:
    return None if value is None else _to_decimal(value)


class DefaultNormalizer:
    """Provider-agnostic mapping from `Raw*` DTOs to canonical domain models."""

    def __init__(self, *, provider_name: str) -> None:
        self.provider_name = provider_name

    def normalize_candle(
        self, raw: RawCandle, *, instrument_id: str, timeframe: Timeframe, received_at: datetime
    ) -> Candle:
        freshness = DataFreshness(data_timestamp=ensure_utc(raw.timestamp), received_timestamp=ensure_utc(received_at))
        return Candle(
            provider=self.provider_name,
            freshness=freshness,
            instrument_id=instrument_id,
            timeframe=timeframe,
            open=_to_decimal(raw.open),
            high=_to_decimal(raw.high),
            low=_to_decimal(raw.low),
            close=_to_decimal(raw.close),
            volume=raw.volume,
            open_interest=raw.open_interest,
        )

    def normalize_quote(self, raw: RawQuote, *, instrument_id: str, received_at: datetime) -> Quote:
        # Dhan's quote/OHLC marketfeed responses don't carry a distinct
        # per-quote exchange timestamp in the documented shape, so a polled
        # quote's data_timestamp there is the poll (receipt) instant itself —
        # the correct conservative choice: it can never understate age.
        # Upstox's `/v2/market-quote/quotes` DOES supply a real exchange
        # timestamp (`last_trade_time`, confirmed against the live API
        # 2026-08-28) via `raw.exchange_timestamp` — when a provider
        # populates it, it is authoritative and used directly; the
        # receipt-instant fallback is unchanged for any provider that
        # doesn't (still `None` by default on `RawQuote`).
        received_utc = ensure_utc(received_at)
        data_timestamp = ensure_utc(raw.exchange_timestamp) if raw.exchange_timestamp is not None else received_utc
        # Daily Market Researcher audit -- real defect found live 2026-08-31
        # (fast-moving market, RELIANCE/NIFTY/BANKNIFTY/UNOMINDA all hit
        # this in one run): `received_at` is `as_of`, frozen once at the
        # START of a multi-step async pipeline (instrument resolution,
        # quote fetch, candles, chain, futures, ...) -- by the time THIS
        # quote's own real exchange `last_trade_time` comes back, genuine
        # wall-clock time has moved on, and a fast market can produce a
        # real trade timestamped a few seconds after that frozen instant.
        # That is NOT the 2026-08-28 malformed-timestamp bug (hours/days
        # off, a real provider defect) -- it is an ordinary consequence of
        # holding `as_of` fixed across a multi-step live fetch. Never
        # calling `utc_now()` here (see app/utils/time.py's module
        # docstring: real-time and replay must run the exact same code
        # path) -- instead, conservatively floor `data_timestamp` at
        # `received_utc` so this quote is reported as fresh as possible
        # (age 0) rather than claiming a timestamp later than `as_of`, but
        # ONLY within a bounded, generous tolerance for ordinary pipeline
        # latency -- a skew beyond that is far more likely to be a real
        # provider defect (like 2026-08-28's) than clock drift, and
        # `DataFreshness`'s no-look-ahead guard is left to raise for it,
        # exactly as before. The PRICE is never altered either way, only
        # this timestamp label, and only ever pulled BACKWARD toward
        # `as_of`, never forward.
        skew = data_timestamp - received_utc
        if timedelta(0) < skew <= _MAX_CLOCK_SKEW_TOLERANCE:
            data_timestamp = received_utc
        freshness = DataFreshness(data_timestamp=data_timestamp, received_timestamp=received_utc)
        return Quote(
            provider=self.provider_name,
            freshness=freshness,
            instrument_id=instrument_id,
            last_price=_to_decimal(raw.last_price),
            previous_close=_optional_decimal(raw.previous_close),
            volume=raw.volume,
            open_interest=raw.open_interest,
            average_price=_optional_decimal(raw.average_price),
        )

    def normalize_option_chain(self, raw: RawOptionChain, *, received_at: datetime) -> OptionChainSnapshot:
        # Same reasoning as normalize_quote: Dhan's option chain response is a
        # real-time snapshot with no per-leg exchange timestamp, so
        # data_timestamp = receipt instant.
        received_utc = ensure_utc(received_at)
        freshness = DataFreshness(data_timestamp=received_utc, received_timestamp=received_utc)

        legs: list[OptionQuote] = []
        for strike, raw_legs in raw.strikes.items():
            for raw_leg in raw_legs:
                change_in_oi: int | None = None
                if raw_leg.open_interest is not None and raw_leg.previous_open_interest is not None:
                    change_in_oi = raw_leg.open_interest - raw_leg.previous_open_interest
                legs.append(
                    OptionQuote(
                        provider=self.provider_name,
                        freshness=freshness,
                        underlying=raw.underlying,
                        expiry=raw.expiry,
                        strike=_to_decimal(strike),
                        right=raw_leg.right,
                        security_id=raw_leg.security_id,
                        last_price=_optional_decimal(raw_leg.last_price),
                        bid_price=_optional_decimal(raw_leg.bid_price),
                        ask_price=_optional_decimal(raw_leg.ask_price),
                        volume=raw_leg.volume,
                        open_interest=raw_leg.open_interest,
                        change_in_open_interest=change_in_oi,
                        implied_volatility=_optional_decimal(raw_leg.implied_volatility),
                        delta=_optional_decimal(raw_leg.delta),
                        theta=_optional_decimal(raw_leg.theta),
                        gamma=_optional_decimal(raw_leg.gamma),
                        vega=_optional_decimal(raw_leg.vega),
                    )
                )

        return OptionChainSnapshot(
            provider=self.provider_name,
            freshness=freshness,
            underlying=raw.underlying,
            expiry=raw.expiry,
            underlying_last_price=_optional_decimal(raw.underlying_last_price),
            legs=legs,
        )
