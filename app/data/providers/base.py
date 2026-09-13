"""Provider-facing contracts: raw (pre-normalization) DTOs, the `Protocol`
every provider implements per data kind, and a priority-ordered registry with
failover.

`Raw*` models are deliberately close to wire shape (permissive optional
fields) — they are what a provider adapter hands to `data/normalization`,
*before* anything is validated against business rules. They are not the
canonical `domain` models and nothing outside `data/providers`/
`data/normalization` should ever construct or consume one directly.

See docs/architecture/ARCHITECTURE.md §6 (dependency rules) and §8 / Addendum
A1 (provider abstraction, Dhan-first but never Dhan-only).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol

from pydantic import BaseModel

from app.data.providers.health import ProviderHealth, ProviderHealthTracker
from app.domain.market.models import ExchangeSegment, OptionRight, Timeframe

# --------------------------------------------------------------------------
# Raw (pre-normalization) DTOs
# --------------------------------------------------------------------------


class RawCandle(BaseModel):
    timestamp: datetime  # already converted to a timezone-aware instant by the adapter
    open: float
    high: float
    low: float
    close: float
    volume: int
    open_interest: int | None = None


class RawTick(BaseModel):
    """One decoded live market-data update for one instrument — the
    streaming-feed analog of `RawCandle`/`RawQuote`. `quantity` is the trade
    quantity this specific update represents (e.g. Upstox's LTPC `ltq`),
    never a running/cumulative total — that distinction matters because
    `LiveCandleAggregator.on_tick()`'s `volume_since_last_tick` parameter
    requires exactly this per-update quantity, not a cumulative figure.
    """

    instrument_key: str
    price: float
    quantity: int
    timestamp: datetime  # already converted to a timezone-aware instant by the adapter
    # True when this tick came from Upstox's one-time `initial_feed` message
    # (sent once per instrument right after subscribing, regardless of
    # market status) rather than a `live_feed` message (sent only while
    # trades are actually happening). Consumers must not classify an
    # initial-snapshot-sourced tick as continuous live streaming.
    is_initial_snapshot: bool = False


class RawOHLC(BaseModel):
    """Today's own real intraday open/high/low/close, when a provider
    supplies it on the quote itself (Upstox's `/v2/market-quote/quotes`
    does, via a nested `ohlc` object — confirmed live 2026-08-31, RELIANCE:
    `{"open": 1278.7, "high": 1279.7, "low": 1271.0, "close": 1278.3}`).
    Distinct from `RawCandle` (a historical M15/etc. bar) -- this is the
    single running today's-range figure, free on the same batched quote
    call the Daily Researcher's Stage 1 already makes."""

    open: float
    high: float
    low: float
    close: float


class RawQuote(BaseModel):
    security_id: str
    last_price: float
    previous_close: float | None = None
    volume: int | None = None
    open_interest: int | None = None
    average_price: float | None = None
    # Optional: the provider's own exchange/last-trade timestamp for this
    # quote, when it actually supplies one (Upstox's `/v2/market-quote/quotes`
    # does, via `last_trade_time`; Dhan's quote/OHLC marketfeed responses do
    # not). Left `None` preserves every existing provider's exact prior
    # behavior (`normalize_quote()` falls back to the receipt instant, as
    # documented there) — this is purely additive.
    exchange_timestamp: datetime | None = None
    # Additive, Daily Researcher early-stage discovery -- real, already-
    # returned fields this codebase did not previously parse. `None` for
    # any provider (or any malformed/absent response) that doesn't supply
    # them; never fabricated. Unused by `normalize_quote()`/the core
    # single-symbol pipeline -- consumed only by Stage-1 screening.
    ohlc: RawOHLC | None = None
    total_buy_quantity: int | None = None
    total_sell_quantity: int | None = None


class RawOptionLeg(BaseModel):
    security_id: str | None = None
    right: OptionRight
    last_price: float | None = None
    bid_price: float | None = None
    ask_price: float | None = None
    volume: int | None = None
    open_interest: int | None = None
    previous_open_interest: int | None = None
    implied_volatility: float | None = None
    delta: float | None = None
    theta: float | None = None
    gamma: float | None = None
    vega: float | None = None


class RawOptionChain(BaseModel):
    underlying: str
    expiry: date
    underlying_last_price: float | None = None
    strikes: dict[float, list[RawOptionLeg]]  # strike -> [CE leg, PE leg] (order not guaranteed)


class RawIPOListing(BaseModel):
    """One IPO as returned by Upstox's real, authorized `GET /v2/ipos`
    list endpoint (live-verified 2026-08-30 -- see
    docs/data-sources/IPO_DATA_SOURCE_DECISION.md). List-view shape only
    (no timeline/registrar) -- `UpstoxProvider.get_ipo_detail()` fetches
    the richer per-IPO shape."""

    id: str
    symbol: str | None
    name: str
    status: str  # "upcoming" | "open" | "closed" | "listed" -- Upstox's own real values
    isin: str | None
    issue_type: str  # "sme" | "regular" (mainboard) -- Upstox's own real values
    issue_size: float | None
    industry: str | None
    minimum_price: float | None
    maximum_price: float | None
    bidding_start_date: date | None
    bidding_end_date: date | None
    total_subscription: str | None  # a single aggregate multiple as a string; NOT category-wise


class RawIPOTimeline(BaseModel):
    pre_apply_start_date: date | None = None
    application_start_date: date | None = None
    application_end_date: date | None = None
    allotment_start_date: date | None = None
    allotment_date: date | None = None
    refund_initiation_date: date | None = None
    listing_date: date | None = None
    mandate_end_date: date | None = None


class RawIPORegistrarInfo(BaseModel):
    name: str | None = None
    email: str | None = None
    contact_name: str | None = None
    contact_number: str | None = None
    website: str | None = None
    registrar: str | None = None


class RawIPOInvestorCategory(BaseModel):
    category: str  # real observed values: "IND" (retail/individual), "EMP" (employee), "HNI", "SHA" (shareholder)
    description: str | None = None


class RawIPODetail(RawIPOListing):
    """The richer `GET /v2/ipos/{id}` shape -- adds everything the list
    view omits. `investors` lists which reservation CATEGORIES this IPO
    has (a real signal for shareholder-quota detection -- "SHA" present
    means a shareholder reservation genuinely exists) but carries no
    reserved-share counts, record dates, or minimum-holding figures --
    those remain only in the actual offer document, which this endpoint
    does not expose."""

    face_value: float | None = None
    tick_size: float | None = None
    lot_size: int | None = None
    minimum_quantity: int | None = None
    cut_off_price: float | None = None
    listing_price: float | None = None  # real, once status == "listed"; None otherwise -- never fabricated
    listing_exchange: str | None = None
    rhp_url: str | None = None
    drhp_url: str | None = None
    timeline: RawIPOTimeline = RawIPOTimeline()
    registrar_info: RawIPORegistrarInfo = RawIPORegistrarInfo()
    investors: list[RawIPOInvestorCategory] = []


class RawIPOPage(BaseModel):
    """Upstox's real pagination envelope for `/v2/ipos` (`meta_data.page`)."""

    page_number: int
    total_pages: int
    records: int
    total_records: int


class RawNewsItem(BaseModel):
    """One real, per-instrument news headline (Milestone: Sprint 4) — a
    genuinely legitimate, first-party source: the SAME already-authorized
    Upstox account this whole project already uses, not a third-party
    vendor and not scraping (see `docs/data-sources/PROVIDER_DECISION.md`'s
    "News/event provider re-audit" section for the live verification that
    found this).
    """

    heading: str
    summary: str | None = None
    article_link: str | None = None
    thumbnail: str | None = None
    published_time: datetime  # already converted to a timezone-aware instant by the adapter


# --------------------------------------------------------------------------
# Provider capabilities (Phase 3 gap-closure)
# --------------------------------------------------------------------------


class ProviderCapabilities(BaseModel):
    """What a provider can genuinely supply for a given evidence FAMILY --
    never inferred by the intelligence engine from a provider's class name
    or from a fetch happening to fail; a provider declares this about
    itself, once, structurally.

    `UpstoxProvider` (live) declares every capability `True` (its defaults)
    -- this changes nothing about live behavior. `HistoricalReplayProvider`
    declares `historical_option_chain`/`historical_futures`/
    `historical_news` `False`: Upstox genuinely has no historical
    option-chain snapshot API, no historical futures-quote reconstruction,
    and no historical news archive, so a replay provider MUST say so
    rather than let a caller discover it only via a failed fetch. This is
    the distinction the analysis engine needs to tell "the provider says
    this evidence structurally cannot exist for this instant" (never
    fatal -- degrade honestly) apart from "the provider claims it can
    supply this but the real fetch failed anyway" (a genuine live
    problem -- stays fatal, unchanged).

    `historical_candles` exists for completeness/documentation -- nothing
    currently branches on it (a provider with no candle capability at all
    cannot usefully implement `AnalysisProvider` in the first place, since
    `analyze_symbol()`'s very first data-dependent stages need a quote and
    M15 history unconditionally).
    """

    historical_candles: bool = True
    historical_option_chain: bool = True
    historical_futures: bool = True
    historical_news: bool = True


# --------------------------------------------------------------------------
# Provider protocols
# --------------------------------------------------------------------------


class BatchedQuoteProvider(Protocol):
    """Cheap multi-instrument quote fetch used by Stage-1 discovery.

    Intelligence code depends on this Protocol, not on Upstox types.
    `UpstoxProvider.get_quotes` is the current adapter. A future 5paisa
    adapter can implement the same method. Results are never averaged
    across providers.
    """

    async def get_quotes(self, security_ids: list[str]) -> dict[str, RawQuote]: ...


class MarketDataProvider(Protocol):
    """OHLCV + quote source for equities/indices/futures.

    Intended implementations: `UpstoxProvider` (wired), `DhanProvider`
    (code present, unused by the dashboard), and a future `FivePaisaProvider`
    only if a real capability gap appears. Conflicting prints must surface
    Conflicting prints must surface as PROVIDER_CONFLICT (also called
    PROVIDER_DISAGREEMENT in older docs) -- never averaged.

    Every method takes `as_of` even though a live provider mostly ignores it
    for the request itself — the parameter exists so live and replay
    providers share one call signature end-to-end (ARCHITECTURE.md §19), and
    so a live provider can use it to flag `PROVIDER_DISAGREEMENT`-relevant
    context or reject a request for a timestamp it cannot honor.
    """

    name: str

    async def get_ohlcv(
        self,
        *,
        security_id: str,
        exchange_segment: ExchangeSegment,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
        as_of: datetime,
    ) -> list[RawCandle]: ...

    async def get_quote(
        self, *, security_id: str, exchange_segment: ExchangeSegment, as_of: datetime
    ) -> RawQuote: ...

    async def health(self) -> ProviderHealth: ...


class OptionChainProvider(Protocol):
    """Option chain source. See ARCHITECTURE.md Addendum A1 — Dhan's chain
    endpoint is real-time only, so any implementation backing this Protocol
    with a live source must have its output persisted at ingestion time for
    replay to ever have data to read.
    """

    name: str

    async def get_chain(self, *, underlying: str, expiry: date, as_of: datetime) -> RawOptionChain: ...

    async def get_expiries(self, *, underlying: str, as_of: datetime) -> list[date]: ...

    async def health(self) -> ProviderHealth: ...


# --------------------------------------------------------------------------
# Registry with priority + failover
# --------------------------------------------------------------------------


class ProviderRegistry[ProviderT]:
    """Ordered list of providers for one data kind (e.g. all
    `MarketDataProvider`s), each behind its own circuit breaker.

    `data/ingestion` asks the registry for the current best provider — it
    never imports a concrete provider class directly. This is the single
    change point for adding, removing, or reprioritizing a provider.
    """

    def __init__(self) -> None:
        self._entries: list[tuple[int, ProviderT, ProviderHealthTracker]] = []

    def register(
        self,
        provider: ProviderT,
        *,
        priority: int,
        health_tracker: ProviderHealthTracker | None = None,
    ) -> None:
        name = getattr(provider, "name", provider.__class__.__name__)
        tracker = health_tracker or ProviderHealthTracker(provider=name)
        self._entries.append((priority, provider, tracker))
        self._entries.sort(key=lambda entry: entry[0])

    def tracker_for(self, provider: ProviderT) -> ProviderHealthTracker | None:
        for _, candidate, tracker in self._entries:
            if candidate is provider:
                return tracker
        return None

    def available(self, *, at: datetime | None = None) -> list[ProviderT]:
        """Providers currently allowed to be called, in priority order."""
        return [provider for _, provider, tracker in self._entries if tracker.should_attempt(at=at)]

    def all_providers(self) -> list[ProviderT]:
        return [provider for _, provider, _ in self._entries]

    def trackers(self) -> dict[str, ProviderHealthTracker]:
        return {getattr(provider, "name", provider.__class__.__name__): tracker for _, provider, tracker in self._entries}
