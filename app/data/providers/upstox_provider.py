"""Upstox v3 adapter — data/research use only, read-only by construction.

No order-placement, modification, or cancellation endpoint is called
anywhere in this class, and none of Upstox's order/portfolio APIs are wired
up — on top of that, the Analytics Token this adapter is designed to use is
itself incapable of authorizing trading operations at all (Upstox's own
documentation: "does not support trading operations"), a defense-in-depth
property Dhan's access-token does not have. See
docs/data-sources/PROVIDER_DECISION.md for the full comparison.

Field names and response envelopes below are taken directly from Upstox's
published v3 API documentation (upstox.com/developer/api-documentation/),
fetched and reviewed 2026-08-28. Broker APIs change; re-verify against
current docs before relying on this in production, and treat a live
response that doesn't match these shapes as `ProviderMalformedResponse`, not
as something to guess around.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime
from enum import Enum
from urllib.parse import quote

import httpx

from app.data.providers.base import (
    ProviderCapabilities,
    RawCandle,
    RawIPODetail,
    RawIPOInvestorCategory,
    RawIPOListing,
    RawIPOPage,
    RawIPORegistrarInfo,
    RawIPOTimeline,
    RawNewsItem,
    RawOHLC,
    RawOptionChain,
    RawOptionLeg,
    RawQuote,
)
from app.data.providers.exceptions import (
    ProviderMalformedResponse,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
)
from app.data.providers.health import ProviderHealth, ProviderHealthTracker
from app.domain.market.models import ExchangeSegment, OptionRight, Timeframe
from app.utils.time import ensure_utc, utc_now

# Upstox v3 historical-candle "unit"/"interval" pair per Timeframe. Only the
# timeframes this codebase already uses elsewhere are mapped — Upstox's API
# actually supports arbitrary minute intervals (1-300) including a native
# 30-minute bar, but extending Timeframe/this mapping beyond what's already
# in use is out of scope here (not needed by anything yet).
_TIMEFRAME_TO_UPSTOX_UNIT_INTERVAL: dict[Timeframe, tuple[str, str]] = {
    Timeframe.M1: ("minutes", "1"),
    Timeframe.M5: ("minutes", "5"),
    Timeframe.M15: ("minutes", "15"),
    Timeframe.H1: ("hours", "1"),
    Timeframe.D1: ("days", "1"),
}


class ExchangeStatus(str, Enum):
    """Mirrors the exact string values confirmed against the real, live
    `GET /v2/market/status/{exchange}` endpoint (which itself matches the
    `MarketStatus` enum in Upstox's own WebSocket protobuf schema) — a real
    `CLOSING_END` response was observed 2026-08-28. `UNKNOWN` is not
    returned by Upstox; it exists for a caller that could not determine
    status at all (e.g. the provider call itself failed).
    """

    PRE_OPEN_START = "PRE_OPEN_START"
    PRE_OPEN_END = "PRE_OPEN_END"
    NORMAL_OPEN = "NORMAL_OPEN"
    NORMAL_CLOSE = "NORMAL_CLOSE"
    CLOSING_START = "CLOSING_START"
    CLOSING_END = "CLOSING_END"
    UNKNOWN = "UNKNOWN"


class UpstoxProvider:
    """Implements `MarketDataProvider` against the Upstox v3 REST API.

    The `httpx.AsyncClient` is injected so tests can swap in a
    `MockTransport` — this class never constructs its own client and never
    touches the network in a unit test.
    """

    name = "upstox"
    # Phase 3 gap-closure -- a live provider genuinely can supply every
    # evidence family (its own defaults), unlike a replay provider. See
    # `ProviderCapabilities`'s own docstring.
    capabilities = ProviderCapabilities()

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        access_token: str,
        base_url: str = "https://api.upstox.com",
        request_timeout_seconds: float = 10.0,
    ) -> None:
        self._client = client
        self._access_token = access_token
        self._base_url = base_url.rstrip("/")
        self._timeout = request_timeout_seconds
        self._health_tracker = ProviderHealthTracker(provider=self.name)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/json",
        }

    async def _get(self, path: str, *, params: dict[str, str] | None = None) -> object:
        at = utc_now()
        started = time.monotonic()
        try:
            response = await self._client.get(
                f"{self._base_url}{path}", headers=self._headers(), params=params, timeout=self._timeout
            )
        except httpx.TimeoutException as exc:
            self._health_tracker.record_failure(at=at, error=str(exc))
            raise ProviderTimeout(f"Upstox request to {path} timed out") from exc
        except httpx.HTTPError as exc:
            self._health_tracker.record_failure(at=at, error=str(exc))
            raise ProviderUnavailable(f"Upstox request to {path} failed: {exc}") from exc

        latency_ms = (time.monotonic() - started) * 1000

        if response.status_code == 429:
            self._health_tracker.record_failure(at=at, error="rate limited (HTTP 429)")
            raise ProviderRateLimited(f"Upstox rate limit hit on {path}")
        if response.status_code >= 500:
            self._health_tracker.record_failure(at=at, error=f"HTTP {response.status_code}")
            raise ProviderUnavailable(f"Upstox {path} returned HTTP {response.status_code}")
        if response.status_code >= 400:
            self._health_tracker.record_failure(at=at, error=f"HTTP {response.status_code}: {response.text[:200]}")
            raise ProviderMalformedResponse(f"Upstox {path} returned HTTP {response.status_code}: {response.text[:200]}")

        try:
            payload: object = response.json()
        except ValueError as exc:
            self._health_tracker.record_failure(at=at, error="response body was not valid JSON")
            raise ProviderMalformedResponse(f"Upstox {path} returned a non-JSON body") from exc

        self._health_tracker.record_success(at=at, latency_ms=latency_ms)
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
        """`security_id` must be an Upstox `instrument_key`
        (e.g. `NSE_EQ|INE002A01018`) — Upstox identifies instruments by
        exchange-segment-prefixed ISIN, not a plain ticker symbol.
        `exchange_segment` is accepted for Protocol-signature compatibility
        but not used to build the request: it is already encoded inside
        `security_id`/`instrument_key` for this provider.
        """
        unit_interval = _TIMEFRAME_TO_UPSTOX_UNIT_INTERVAL.get(timeframe)
        if unit_interval is None:
            raise ProviderMalformedResponse(
                f"UpstoxProvider does not support timeframe {timeframe.value} "
                "(not in this codebase's currently-used timeframe set)"
            )
        unit, interval = unit_interval

        encoded_key = quote(security_id, safe="")
        path = (
            f"/v3/historical-candle/{encoded_key}/{unit}/{interval}/"
            f"{end.date().isoformat()}/{start.date().isoformat()}"
        )

        payload = self._expect_dict(await self._get(path), context=path)
        if payload.get("status") != "success":
            raise ProviderMalformedResponse(f"{path}: non-success status")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ProviderMalformedResponse(f"{path}: missing 'data'")
        raw_rows = data.get("candles")
        if not isinstance(raw_rows, list):
            raise ProviderMalformedResponse(f"{path}: missing 'candles' array")

        candles: list[RawCandle] = []
        try:
            for row in raw_rows:
                if not isinstance(row, list) or len(row) < 6:
                    raise ProviderMalformedResponse(f"{path}: candle row has unexpected shape: {row!r}")
                timestamp = ensure_utc(datetime.fromisoformat(str(row[0])))
                if timestamp > as_of:
                    continue  # never surface a candle from beyond the requested as_of instant
                open_interest = _require_int(row[6]) if len(row) > 6 and row[6] is not None else None
                candles.append(
                    RawCandle(
                        timestamp=timestamp,
                        open=_require_float(row[1]),
                        high=_require_float(row[2]),
                        low=_require_float(row[3]),
                        close=_require_float(row[4]),
                        volume=_require_int(row[5]),
                        open_interest=open_interest,
                    )
                )
        except (TypeError, ValueError) as exc:
            raise ProviderMalformedResponse(f"{path}: could not parse candle row: {exc}") from exc

        # Upstox's documented response does not state candle ordering
        # explicitly; sort ascending defensively (this is order
        # canonicalization at the provider boundary, the same role
        # `InMemoryCandleRepository.query()` already plays elsewhere in this
        # codebase — it is not a repair of structurally invalid data, which
        # this method never attempts).
        candles.sort(key=lambda c: c.timestamp)
        return candles

    async def get_quote(self, *, security_id: str, exchange_segment: ExchangeSegment, as_of: datetime) -> RawQuote:
        """Implemented against the REAL `/v2/market-quote/quotes` response,
        verified live 2026-08-28 (not guessed) — see `get_quotes()`, which
        this delegates to. Earlier milestones deliberately left this
        unimplemented rather than guess a schema; that schema is now
        confirmed.
        """
        quotes = await self.get_quotes([security_id])
        raw_quote = quotes.get(security_id)
        if raw_quote is None:
            raise ProviderMalformedResponse(f"/v2/market-quote/quotes: no data returned for {security_id!r}")
        return raw_quote

    async def get_quotes(self, security_ids: list[str]) -> dict[str, RawQuote]:
        """Upstox-specific batch quote fetch: not part of the generic
        `MarketDataProvider` Protocol (which is single-instrument), but
        exposed directly because Upstox's `/v2/market-quote/quotes` natively
        accepts comma-separated `instrument_key`s in one request, and
        multi-instrument current-analysis explicitly needs this rather than
        one request per instrument. Returns a mapping keyed by each result's
        own `instrument_token` (not by the response's `"EXCHANGE:SYMBOL"`
        outer key, which is a display convenience, not the identifier this
        codebase uses elsewhere).

        `previous_close` is derived as `last_price - net_change` (Upstox
        does not return a bare `previous_close` field on this endpoint) —
        cross-checked against the `cp` field of the independent
        `/v3/market-quote/ltp` endpoint on real data and found to match
        exactly (2026-08-28).
        """
        if not security_ids:
            return {}
        path = "/v2/market-quote/quotes"
        payload = self._expect_dict(
            await self._get(path, params={"instrument_key": ",".join(security_ids)}), context=path
        )
        if payload.get("status") != "success":
            raise ProviderMalformedResponse(f"{path}: non-success status")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ProviderMalformedResponse(f"{path}: missing 'data'")

        result: dict[str, RawQuote] = {}
        try:
            for entry in data.values():
                if not isinstance(entry, dict):
                    raise ProviderMalformedResponse(f"{path}: quote entry was not an object: {entry!r}")
                token = entry.get("instrument_token")
                if not isinstance(token, str):
                    raise ProviderMalformedResponse(f"{path}: quote entry missing 'instrument_token'")

                last_price = _require_float(entry["last_price"])
                net_change = entry.get("net_change")
                previous_close = last_price - _require_float(net_change) if net_change is not None else None

                volume = entry.get("volume")
                open_interest = entry.get("oi")
                average_price = entry.get("average_price")
                ltt_raw = entry.get("last_trade_time")

                # Daily Researcher early-stage discovery -- real fields
                # Upstox already returns on this same batched quote
                # (confirmed live 2026-08-31), previously unparsed. Own,
                # tolerant sub-parse: a malformed/absent `ohlc`/buy-sell
                # figure must never take down the whole quote (which the
                # REQUIRED fields above correctly do via the outer
                # except) -- these are purely additive, never fabricated.
                raw_ohlc = entry.get("ohlc")
                ohlc: RawOHLC | None = None
                if isinstance(raw_ohlc, dict):
                    try:
                        ohlc = RawOHLC(
                            open=_require_float(raw_ohlc["open"]), high=_require_float(raw_ohlc["high"]),
                            low=_require_float(raw_ohlc["low"]), close=_require_float(raw_ohlc["close"]),
                        )
                    except (TypeError, ValueError, KeyError):
                        ohlc = None
                total_buy_quantity = entry.get("total_buy_quantity")
                total_sell_quantity = entry.get("total_sell_quantity")

                result[token] = RawQuote(
                    security_id=token,
                    last_price=last_price,
                    previous_close=previous_close,
                    volume=_require_int(volume) if volume is not None else None,
                    open_interest=_require_int(open_interest) if open_interest is not None else None,
                    average_price=_require_float(average_price) if average_price is not None else None,
                    exchange_timestamp=_parse_last_trade_time(ltt_raw) if ltt_raw is not None else None,
                    ohlc=ohlc,
                    total_buy_quantity=_require_int(total_buy_quantity) if total_buy_quantity is not None else None,
                    total_sell_quantity=_require_int(total_sell_quantity) if total_sell_quantity is not None else None,
                )
        except (TypeError, ValueError, KeyError) as exc:
            raise ProviderMalformedResponse(f"{path}: could not parse quote entry: {exc}") from exc

        return result

    async def get_market_status(self, *, exchange: str = "NSE") -> ExchangeStatus:
        """`exchange` is the plain exchange code (`"NSE"`, `"BSE"`, ...), not
        an `ExchangeSegment` — confirmed live against
        `GET /v2/market/status/NSE`, 2026-08-28.
        """
        path = f"/v2/market/status/{exchange}"
        payload = self._expect_dict(await self._get(path), context=path)
        if payload.get("status") != "success":
            raise ProviderMalformedResponse(f"{path}: non-success status")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ProviderMalformedResponse(f"{path}: missing 'data'")
        raw_status = data.get("status")
        try:
            return ExchangeStatus(str(raw_status))
        except ValueError:
            raise ProviderMalformedResponse(f"{path}: unrecognized exchange status {raw_status!r}") from None

    async def health(self) -> ProviderHealth:
        return self._health_tracker.snapshot()

    # -- OptionChainProvider -------------------------------------------------
    # `/v2/option/chain` and `/v2/option/contract` are real, working Upstox
    # endpoints -- confirmed live against the actual API 2026-08-28 (not
    # found via the documentation site, which 404'd on every guessed URL for
    # these two; verified by calling them directly instead and inspecting
    # the real response). `underlying` is an Upstox `instrument_key`
    # (e.g. `NSE_EQ|INE002A01018`), matching this Protocol's existing
    # convention elsewhere in this codebase.

    async def get_chain(self, *, underlying: str, expiry: date, as_of: datetime) -> RawOptionChain:
        path = "/v2/option/chain"
        payload = self._expect_dict(
            await self._get(path, params={"instrument_key": underlying, "expiry_date": expiry.isoformat()}),
            context=path,
        )
        if payload.get("status") != "success":
            raise ProviderMalformedResponse(f"{path}: non-success status")
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise ProviderMalformedResponse(f"{path}: missing 'data' array")

        underlying_last_price: float | None = None
        strikes: dict[float, list[RawOptionLeg]] = {}
        try:
            for row in rows:
                if not isinstance(row, dict):
                    raise ProviderMalformedResponse(f"{path}: chain row was not an object: {row!r}")
                strike_price = _require_float(row["strike_price"])
                spot = row.get("underlying_spot_price")
                if isinstance(spot, int | float):
                    underlying_last_price = float(spot)

                legs: list[RawOptionLeg] = []
                for side_key, right in (("call_options", OptionRight.CE), ("put_options", OptionRight.PE)):
                    side = row.get(side_key)
                    if not isinstance(side, dict):
                        continue
                    market_data = _as_dict(side.get("market_data"))
                    greeks = _as_dict(side.get("option_greeks"))
                    legs.append(
                        RawOptionLeg(
                            security_id=_optional_str(side.get("instrument_key")),
                            right=right,
                            last_price=_optional_float(market_data.get("ltp")),
                            bid_price=_optional_float(market_data.get("bid_price")),
                            ask_price=_optional_float(market_data.get("ask_price")),
                            volume=_optional_int(market_data.get("volume")),
                            open_interest=_optional_int(market_data.get("oi")),
                            previous_open_interest=_optional_int(market_data.get("prev_oi")),
                            implied_volatility=_optional_float(greeks.get("iv")),
                            delta=_optional_float(greeks.get("delta")),
                            theta=_optional_float(greeks.get("theta")),
                            gamma=_optional_float(greeks.get("gamma")),
                            vega=_optional_float(greeks.get("vega")),
                        )
                    )
                strikes[strike_price] = legs
        except (TypeError, ValueError, KeyError) as exc:
            raise ProviderMalformedResponse(f"{path}: could not parse chain row: {exc}") from exc

        if not strikes:
            raise ProviderMalformedResponse(f"{path}: no strikes returned for {underlying} expiry {expiry.isoformat()}")

        return RawOptionChain(underlying=underlying, expiry=expiry, underlying_last_price=underlying_last_price, strikes=strikes)

    async def get_expiries(self, *, underlying: str, as_of: datetime) -> list[date]:
        """Derived from `/v2/option/contract` (the same real endpoint that
        lists every contract for `underlying`) rather than a separate
        expiry-list endpoint — no dedicated one was found at either of the
        two plausible paths tried against the real API (`/v2/option/expiries`,
        `/v2/option/contract/expiries`), both of which returned real error
        responses (not found), confirmed 2026-08-28.
        """
        path = "/v2/option/contract"
        payload = self._expect_dict(await self._get(path, params={"instrument_key": underlying}), context=path)
        if payload.get("status") != "success":
            raise ProviderMalformedResponse(f"{path}: non-success status")
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise ProviderMalformedResponse(f"{path}: missing 'data' array")

        expiries: set[date] = set()
        try:
            for row in rows:
                if not isinstance(row, dict):
                    raise ProviderMalformedResponse(f"{path}: contract row was not an object: {row!r}")
                expiries.add(date.fromisoformat(str(row["expiry"])))
        except (TypeError, ValueError, KeyError) as exc:
            raise ProviderMalformedResponse(f"{path}: could not parse contract row: {exc}") from exc

        return sorted(expiries)

    async def get_news(self, *, instrument_key: str) -> list[RawNewsItem]:
        """Real, first-party, already-authorized news via `/v2/news?
        category=instrument_keys&instrument_keys=...` — confirmed live
        2026-08-29 (not documented anywhere in Upstox's published API docs
        found this session; discovered by live-probing `/v2/news`'s own
        validation error messages, which named the real required params).
        Returns real per-company headlines for a real `NSE_EQ` instrument
        key; confirmed empty (never an error) for an index key or an
        unrecognized instrument key — both real, honest "no news" results,
        not failures. See docs/data-sources/PROVIDER_DECISION.md.

        Only ONE `instrument_key` per call — a live multi-key test (two
        comma-separated keys) returned data for only the first, so this
        method does not attempt Upstox's own batching for this endpoint.
        """
        path = "/v2/news"
        payload = self._expect_dict(
            await self._get(path, params={"category": "instrument_keys", "instrument_keys": instrument_key}),
            context=path,
        )
        if payload.get("status") != "success":
            raise ProviderMalformedResponse(f"{path}: non-success status")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ProviderMalformedResponse(f"{path}: missing 'data'")

        rows = data.get(instrument_key, [])
        if not isinstance(rows, list):
            raise ProviderMalformedResponse(f"{path}: expected a list for {instrument_key!r}, got {type(rows).__name__}")

        items: list[RawNewsItem] = []
        try:
            for row in rows:
                if not isinstance(row, dict):
                    raise ProviderMalformedResponse(f"{path}: news row was not an object: {row!r}")
                items.append(
                    RawNewsItem(
                        heading=str(row["heading"]),
                        summary=str(row["summary"]) if row.get("summary") is not None else None,
                        article_link=str(row["article_link"]) if row.get("article_link") is not None else None,
                        thumbnail=str(row["thumbnail"]) if row.get("thumbnail") is not None else None,
                        published_time=_parse_epoch_millis(row["published_time"]),
                    )
                )
        except (TypeError, ValueError, KeyError) as exc:
            raise ProviderMalformedResponse(f"{path}: could not parse news row: {exc}") from exc

        return items

    # -- IPO Intelligence -----------------------------------------------

    _VALID_IPO_STATUSES = frozenset({"upcoming", "open", "closed", "listed"})

    async def get_ipos(self, *, status: str, page_number: int = 1) -> tuple[list[RawIPOListing], RawIPOPage]:
        """Real, first-party, already-authorized `GET /v2/ipos` -- live-
        verified 2026-08-30 (see docs/data-sources/IPO_DATA_SOURCE_
        DECISION.md). This CORRECTS an earlier conclusion in this project
        that no authorized IPO endpoint existed on this token; a fresh
        live probe this milestone found it. `status` must be one of
        `upcoming`/`open`/`closed`/`listed` -- Upstox's own real,
        enumerated values (confirmed via a real `400 UDAPI1219` naming
        them when an invalid value was tried); this method does not
        invent a fifth value. Real pagination confirmed (`meta_data.page`,
        `page_number` query param) -- this returns exactly one page; a
        caller wanting every record must page through `RawIPOPage.
        total_pages` itself.
        """
        if status not in self._VALID_IPO_STATUSES:
            raise ValueError(f"invalid IPO status {status!r} -- must be one of {sorted(self._VALID_IPO_STATUSES)}")
        path = "/v2/ipos"
        payload = self._expect_dict(
            await self._get(path, params={"status": status, "page_number": str(page_number)}), context=path
        )
        if payload.get("status") != "success":
            raise ProviderMalformedResponse(f"{path}: non-success status")
        data = payload.get("data")
        meta = payload.get("meta_data")
        if not isinstance(data, list) or not isinstance(meta, dict):
            raise ProviderMalformedResponse(f"{path}: missing 'data' list or 'meta_data'")
        page_obj = meta.get("page")
        if not isinstance(page_obj, dict):
            raise ProviderMalformedResponse(f"{path}: missing 'meta_data.page'")

        try:
            listings = [RawIPOListing.model_validate(row) for row in data]
            page = RawIPOPage.model_validate(page_obj)
        except Exception as exc:
            raise ProviderMalformedResponse(f"{path}: could not parse response: {exc}") from exc
        return listings, page

    async def get_ipo_detail(self, *, ipo_id: str) -> RawIPODetail:
        """Real `GET /v2/ipos/{id}` -- the richer per-IPO shape (timeline,
        registrar, investor reservation categories, and, once listed, the
        real `listing_price`). `ipo_id` is the real `id` field from
        `get_ipos()`'s own listing rows -- this method never guesses one.
        """
        path = f"/v2/ipos/{quote(ipo_id, safe='')}"
        payload = self._expect_dict(await self._get(path), context=path)
        if payload.get("status") != "success":
            raise ProviderMalformedResponse(f"{path}: non-success status")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ProviderMalformedResponse(f"{path}: missing 'data'")
        try:
            timeline = RawIPOTimeline.model_validate(data.get("timeline") or {})
            registrar_info = RawIPORegistrarInfo.model_validate(data.get("registrar_info") or {})
            investors = [RawIPOInvestorCategory.model_validate(row) for row in (data.get("investors") or [])]
            return RawIPODetail.model_validate({**data, "timeline": timeline, "registrar_info": registrar_info, "investors": investors})
        except Exception as exc:
            raise ProviderMalformedResponse(f"{path}: could not parse response: {exc}") from exc

    @staticmethod
    def _expect_dict(payload: object, *, context: str) -> dict[str, object]:
        if not isinstance(payload, dict):
            raise ProviderMalformedResponse(f"{context}: expected a JSON object, got {type(payload).__name__}")
        return payload


def _require_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"expected a numeric value, got {type(value).__name__}")
    return float(value)


def _require_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"expected an integer-like value, got {type(value).__name__}")
    return int(value)


def _optional_float(value: object) -> float | None:
    return None if value is None else _require_float(value)


def _optional_int(value: object) -> int | None:
    return None if value is None else _require_int(value)


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)


def _as_dict(value: object) -> dict[str, object]:
    """Narrows an arbitrary JSON value to a `dict[str, object]`, or an empty
    dict if it isn't one — used for optional nested objects (e.g. a chain
    row's `market_data`/`option_greeks`) where mypy cannot narrow a
    `.get(...)`-then-`isinstance` ternary on its own (it re-evaluates the
    `.get()` call in the true-branch, losing the `isinstance` check from the
    condition).
    """
    return value if isinstance(value, dict) else {}


def _parse_epoch_millis(value: object) -> datetime:
    """`/v2/news`'s `published_time` is a real epoch-milliseconds integer
    (e.g. `1787740313933`) -- confirmed live 2026-08-29 by decoding a real
    response into a real, recent, plausible date."""
    try:
        millis = int(str(value))
    except ValueError as exc:
        raise ProviderMalformedResponse(f"could not parse published_time {value!r}") from exc
    if millis <= 0:
        raise ProviderMalformedResponse(f"published_time is non-positive: {value!r}")
    return datetime.fromtimestamp(millis / 1000, tz=UTC)


def _parse_last_trade_time(value: object) -> datetime:
    """`last_trade_time` on `/v2/market-quote/quotes` is a *string* of epoch
    milliseconds (e.g. `"1787912999563"`) — confirmed live 2026-08-28,
    distinct from the WebSocket LTPC's int64 `ltt` field.
    """
    try:
        millis = int(str(value))
    except ValueError as exc:
        raise ProviderMalformedResponse(f"could not parse last_trade_time {value!r}") from exc
    if millis <= 0:
        raise ProviderMalformedResponse(f"last_trade_time is non-positive: {value!r}")
    return datetime.fromtimestamp(millis / 1000, tz=UTC)

