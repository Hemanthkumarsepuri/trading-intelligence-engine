"""DhanHQ v2 adapter — data/research use only.

No order-placement, modification, or cancellation endpoint is called
anywhere in this class, and none of Dhan's order/portfolio APIs are wired
up. See docs/data-sources/PROVIDERS.md ("DhanHQ v2 Capability Assessment")
for the full capability/gap list this implementation is built against.

Field names and response envelopes below are taken directly from Dhan's
published v2 documentation (docs.dhanhq.co/api/v2/, dhanhq.co/docs/v2/*) as
reviewed on 2026-08-27. Broker APIs change; re-verify against current docs
before relying on this in production, and treat a live response that doesn't
match these shapes as `ProviderMalformedResponse`, not as something to guess
around.
"""

from __future__ import annotations

import time
from datetime import date, datetime

import httpx

from app.data.providers.base import RawCandle, RawOptionChain, RawOptionLeg, RawQuote
from app.data.providers.exceptions import (
    ProviderMalformedResponse,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
)
from app.data.providers.health import ProviderHealth, ProviderHealthTracker
from app.domain.market.models import ExchangeSegment, OptionRight, Timeframe
from app.utils.time import from_epoch_seconds, utc_now

_SEGMENT_TO_DHAN: dict[ExchangeSegment, str] = {
    ExchangeSegment.NSE_EQ: "NSE_EQ",
    ExchangeSegment.NSE_FNO: "NSE_FNO",
    ExchangeSegment.NSE_INDEX: "IDX_I",
    ExchangeSegment.NSE_CURRENCY: "NSE_CURRENCY",
    ExchangeSegment.BSE_EQ: "BSE_EQ",
    ExchangeSegment.BSE_FNO: "BSE_FNO",
    ExchangeSegment.MCX_COMM: "MCX_COMM",
}

# Dhan's intraday interval only supports 1/5/15/25/60 minutes (documented) —
# there is no native 30-minute interval. Timeframe.M30 is intentionally
# absent: callers needing 30m must resample from M15 elsewhere (options/
# technical engine concern, not this adapter's). Requesting M30 here raises
# ProviderMalformedResponse rather than silently substituting a different
# interval and mislabeling the result.
_TIMEFRAME_TO_DHAN_INTERVAL: dict[Timeframe, int] = {
    Timeframe.M1: 1,
    Timeframe.M5: 5,
    Timeframe.M15: 15,
    Timeframe.H1: 60,
}


class DhanProvider:
    """Implements `MarketDataProvider` and `OptionChainProvider` against the
    DhanHQ v2 REST API. The `httpx.AsyncClient` is injected so tests can swap
    in a `MockTransport` — this class never constructs its own client and
    never touches the network in a unit test.
    """

    name = "dhan"

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        client_id: str,
        access_token: str,
        base_url: str = "https://api.dhan.co",
        request_timeout_seconds: float = 10.0,
    ) -> None:
        self._client = client
        self._client_id = client_id
        self._access_token = access_token
        self._base_url = base_url.rstrip("/")
        self._timeout = request_timeout_seconds
        self._health_tracker = ProviderHealthTracker(provider=self.name)

    def _headers(self) -> dict[str, str]:
        return {
            "access-token": self._access_token,
            "client-id": self._client_id,
            "Content-Type": "application/json",
        }

    async def _post(self, path: str, body: dict[str, object]) -> object:
        at = utc_now()
        started = time.monotonic()
        try:
            response = await self._client.post(
                f"{self._base_url}{path}", headers=self._headers(), json=body, timeout=self._timeout
            )
        except httpx.TimeoutException as exc:
            self._health_tracker.record_failure(at=at, error=str(exc))
            raise ProviderTimeout(f"Dhan request to {path} timed out") from exc
        except httpx.HTTPError as exc:
            self._health_tracker.record_failure(at=at, error=str(exc))
            raise ProviderUnavailable(f"Dhan request to {path} failed: {exc}") from exc

        latency_ms = (time.monotonic() - started) * 1000

        if response.status_code == 429:
            self._health_tracker.record_failure(at=at, error="rate limited (HTTP 429)")
            raise ProviderRateLimited(f"Dhan rate limit hit on {path}")
        if response.status_code >= 500:
            self._health_tracker.record_failure(at=at, error=f"HTTP {response.status_code}")
            raise ProviderUnavailable(f"Dhan {path} returned HTTP {response.status_code}")
        if response.status_code >= 400:
            self._health_tracker.record_failure(at=at, error=f"HTTP {response.status_code}: {response.text[:200]}")
            raise ProviderMalformedResponse(f"Dhan {path} returned HTTP {response.status_code}: {response.text[:200]}")

        try:
            payload: object = response.json()
        except ValueError as exc:
            self._health_tracker.record_failure(at=at, error="response body was not valid JSON")
            raise ProviderMalformedResponse(f"Dhan {path} returned a non-JSON body") from exc

        self._health_tracker.record_success(at=at, latency_ms=latency_ms)
        return payload

    @staticmethod
    def _expect_dict(payload: object, *, context: str) -> dict[str, object]:
        if not isinstance(payload, dict):
            raise ProviderMalformedResponse(f"{context}: expected a JSON object, got {type(payload).__name__}")
        return payload

    # -- MarketDataProvider -------------------------------------------------

    async def get_ohlcv(
        self,
        *,
        security_id: str,
        exchange_segment: ExchangeSegment,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
        as_of: datetime,
    ) -> list[RawCandle]:
        dhan_segment = _SEGMENT_TO_DHAN[exchange_segment]

        if timeframe == Timeframe.D1:
            path = "/charts/historical"
            body: dict[str, object] = {
                "securityId": security_id,
                "exchangeSegment": dhan_segment,
                "instrument": "EQUITY",
                "fromDate": start.date().isoformat(),
                "toDate": end.date().isoformat(),
            }
        else:
            interval = _TIMEFRAME_TO_DHAN_INTERVAL.get(timeframe)
            if interval is None:
                raise ProviderMalformedResponse(
                    f"DhanProvider does not support timeframe {timeframe.value} directly "
                    "(Dhan intraday intervals are 1/5/15/25/60 minutes only; resample "
                    "30m from 15m upstream of this adapter)"
                )
            path = "/charts/intraday"
            body = {
                "securityId": security_id,
                "exchangeSegment": dhan_segment,
                "instrument": "EQUITY",
                "interval": interval,
                "fromDate": start.strftime("%Y-%m-%d %H:%M:%S"),
                "toDate": end.strftime("%Y-%m-%d %H:%M:%S"),
            }

        payload = self._expect_dict(await self._post(path, body), context=path)
        return self._parse_chart_arrays(payload, context=path, as_of=as_of)

    @staticmethod
    def _parse_chart_arrays(payload: dict[str, object], *, context: str, as_of: datetime) -> list[RawCandle]:
        required = ("open", "high", "low", "close", "volume", "timestamp")
        for key in required:
            if key not in payload:
                raise ProviderMalformedResponse(f"{context}: response missing '{key}' array")

        arrays: dict[str, list[object]] = {}
        for key in (*required, "open_interest"):
            value = payload.get(key)
            if value is None:
                arrays[key] = []
                continue
            if not isinstance(value, list):
                raise ProviderMalformedResponse(f"{context}: '{key}' was not an array")
            arrays[key] = value

        lengths = {key: len(arrays[key]) for key in required}
        if len(set(lengths.values())) > 1:
            raise ProviderMalformedResponse(f"{context}: parallel arrays have mismatched lengths: {lengths}")

        oi_array = arrays["open_interest"]
        has_oi = len(oi_array) == lengths["timestamp"]

        candles: list[RawCandle] = []
        try:
            for i in range(lengths["timestamp"]):
                candle_ts = from_epoch_seconds(_require_int(arrays["timestamp"][i]))
                if candle_ts > as_of:
                    continue  # never surface a candle from beyond the requested as_of instant
                candles.append(
                    RawCandle(
                        timestamp=candle_ts,
                        open=_require_float(arrays["open"][i]),
                        high=_require_float(arrays["high"][i]),
                        low=_require_float(arrays["low"][i]),
                        close=_require_float(arrays["close"][i]),
                        volume=_require_int(arrays["volume"][i]),
                        open_interest=_require_int(oi_array[i]) if has_oi else None,
                    )
                )
        except (TypeError, ValueError) as exc:
            raise ProviderMalformedResponse(f"{context}: could not parse candle array elements") from exc
        return candles

    async def get_quote(self, *, security_id: str, exchange_segment: ExchangeSegment, as_of: datetime) -> RawQuote:
        dhan_segment = _SEGMENT_TO_DHAN[exchange_segment]
        body: dict[str, object] = {dhan_segment: [int(security_id)]}
        payload = self._expect_dict(await self._post("/v2/marketfeed/quote", body), context="/v2/marketfeed/quote")

        data = payload.get("data")
        if not isinstance(data, dict) or payload.get("status") != "success":
            raise ProviderMalformedResponse("/v2/marketfeed/quote: unexpected envelope shape or non-success status")

        segment_data = data.get(dhan_segment)
        if not isinstance(segment_data, dict):
            raise ProviderMalformedResponse(f"/v2/marketfeed/quote: no data for segment {dhan_segment}")

        leg = segment_data.get(str(security_id))
        if not isinstance(leg, dict):
            raise ProviderMalformedResponse(f"/v2/marketfeed/quote: no data for security_id {security_id}")

        try:
            ohlc = leg.get("ohlc")
            previous_close = float(ohlc["close"]) if isinstance(ohlc, dict) and "close" in ohlc else None
            return RawQuote(
                security_id=security_id,
                last_price=float(leg["last_price"]),
                previous_close=previous_close,
                volume=int(leg["volume"]) if "volume" in leg and leg["volume"] is not None else None,
                open_interest=int(leg["oi"]) if "oi" in leg and leg["oi"] is not None else None,
                average_price=float(leg["average_price"]) if "average_price" in leg else None,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderMalformedResponse("/v2/marketfeed/quote: could not parse quote fields") from exc

    async def health(self) -> ProviderHealth:
        return self._health_tracker.snapshot()

    # -- OptionChainProvider -------------------------------------------------

    async def get_chain(self, *, underlying: str, expiry: date, as_of: datetime) -> RawOptionChain:
        body: dict[str, object] = {
            "UnderlyingScrip": int(underlying),
            "UnderlyingSeg": _SEGMENT_TO_DHAN[ExchangeSegment.NSE_INDEX],
            "Expiry": expiry.isoformat(),
        }
        payload = self._expect_dict(await self._post("/optionchain", body), context="/optionchain")

        if payload.get("status") != "success":
            raise ProviderMalformedResponse("/optionchain: non-success status")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ProviderMalformedResponse("/optionchain: missing 'data'")
        oc = data.get("oc")
        if not isinstance(oc, dict):
            raise ProviderMalformedResponse("/optionchain: missing 'data.oc'")

        underlying_last_price = data.get("last_price")

        strikes: dict[float, list[RawOptionLeg]] = {}
        try:
            for strike_str, legs_obj in oc.items():
                strike = float(strike_str)
                if not isinstance(legs_obj, dict):
                    raise ProviderMalformedResponse(f"/optionchain: strike {strike_str} entry was not an object")
                parsed_legs: list[RawOptionLeg] = []
                for side_key, right in (("ce", OptionRight.CE), ("pe", OptionRight.PE)):
                    side = legs_obj.get(side_key)
                    if not isinstance(side, dict):
                        continue
                    greeks_value = side.get("greeks")
                    greeks: dict[str, object] = greeks_value if isinstance(greeks_value, dict) else {}
                    parsed_legs.append(
                        RawOptionLeg(
                            security_id=str(side["security_id"]) if "security_id" in side else None,
                            right=right,
                            last_price=_optional_float(side.get("last_price")),
                            bid_price=_optional_float(side.get("top_bid_price")),
                            ask_price=_optional_float(side.get("top_ask_price")),
                            volume=_optional_int(side.get("volume")),
                            open_interest=_optional_int(side.get("oi")),
                            previous_open_interest=_optional_int(side.get("previous_oi")),
                            implied_volatility=_optional_float(side.get("implied_volatility")),
                            delta=_optional_float(greeks.get("delta")),
                            theta=_optional_float(greeks.get("theta")),
                            gamma=_optional_float(greeks.get("gamma")),
                            vega=_optional_float(greeks.get("vega")),
                        )
                    )
                strikes[strike] = parsed_legs
        except (TypeError, ValueError) as exc:
            raise ProviderMalformedResponse("/optionchain: could not parse strike/leg fields") from exc

        return RawOptionChain(
            underlying=underlying,
            expiry=expiry,
            underlying_last_price=_optional_float(underlying_last_price),
            strikes=strikes,
        )

    async def get_expiries(self, *, underlying: str, as_of: datetime) -> list[date]:
        body: dict[str, object] = {
            "UnderlyingScrip": int(underlying),
            "UnderlyingSeg": _SEGMENT_TO_DHAN[ExchangeSegment.NSE_INDEX],
        }
        payload = self._expect_dict(
            await self._post("/optionchain/expirylist", body), context="/optionchain/expirylist"
        )
        if payload.get("status") != "success":
            raise ProviderMalformedResponse("/optionchain/expirylist: non-success status")
        data = payload.get("data")
        if not isinstance(data, list):
            raise ProviderMalformedResponse("/optionchain/expirylist: missing 'data' array")
        try:
            return [date.fromisoformat(str(d)) for d in data]
        except ValueError as exc:
            raise ProviderMalformedResponse("/optionchain/expirylist: could not parse expiry dates") from exc


def _require_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"expected a numeric value, got {type(value).__name__}")
    return float(value)


def _require_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"expected an integer value, got {type(value).__name__}")
    return value


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return _require_float(value)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return _require_int(value)
