# Data Source Classification & Provider Abstraction

Status: **PROPOSED — awaiting approval**. Part of [Phase 0 architecture](../architecture/ARCHITECTURE.md).

Important caveat: exact pricing, rate limits, and licensing terms for third-party vendors change and must be **verified directly with the vendor at procurement time** — figures below are a classification framework and qualitative comparison to guide the decision, not a quote. Treat any specific number here as "as of vendor's public docs, last generally known" rather than current fact, and re-verify before committing budget. This is deliberate: the same "don't invent numbers" discipline that governs the trading engine applies to this document.

---

## Classification

### A. Official exchange / licensed sources
NSE's own data licensing arm (NSE Data & Analytics / NSE market data feeds) and exchange-licensed real-time feeds.
- **Pros:** authoritative, contractually reliable, includes full depth/option-chain/Greeks where licensed.
- **Cons:** meaningful cost, formal licensing process, typically aimed at institutional consumers rather than a solo retail project.
- **Fit:** the eventual gold-standard source if this project matures; not a Phase 1 requirement.

### B. Broker APIs
Examples: Zerodha Kite Connect, Upstox API, Angel One SmartAPI, Fyers API.
- **Pros:** you likely already have (or can open) an account; reasonable latency; typically includes OHLC, LTP, and NSE option-chain data; modest subscription fee for the data API tier; used here strictly **read-only** (quotes/candles/chain), never for order placement in Phase 1.
- **Cons:** each has its own auth flow (token refresh daily is common), rate limits, and occasional API changes; being tied to a broker account means an outage/maintenance window on their side affects you too.
- **Fit:** strong primary-candidate category for Phase 1 — this is the most practical "real API, not scraping" path for a retail Indian trader today. Recommend picking one as primary once you tell me which broker you already use or are open to opening a data-API-only relationship with.

### C. Market-data APIs (non-broker)
Examples: Global Datafeeds (GDFL), TrueData, AlgoTest-style data resellers.
- **Pros:** purpose-built for algo/quant consumers, often include historical tick/candle backfill useful for the replay engine, sometimes better option-chain/Greeks completeness than broker APIs.
- **Cons:** subscription cost, another account/credential to manage, coverage and reliability vary by vendor — needs due diligence before relying on one as primary.
- **Fit:** good fallback/secondary provider, or primary if a broker API turns out insufficient for option-chain depth.

### D. News APIs
Examples: NewsAPI.org, Marketaux, GDELT, financial-news RSS feeds (exchange/company announcements, major business-news outlets).
- **Pros:** structured, timestamped, sourceable — fits the `news_events` model directly.
- **Cons:** India-specific market-moving news coverage and low latency are the two weakest points across most generalist news APIs; RSS feeds from NSE/BSE corporate-announcement pages are often the most reliable low-latency source for company-specific events.
- **Fit:** combine an RSS/announcement feed (for corporate events) with a general news API (for macro/sentiment) rather than relying on one source — matches the multi-provider fallback design.

### E. Public web sources (unofficial scraping)
Examples: unofficial NSE website JSON endpoints, financial portals without a public API.
- **Pros:** free, sometimes the only place certain figures (e.g. some breadth stats) are published.
- **Cons:** no SLA, ToS/legal risk, brittle to markup/endpoint changes, IP-block risk under any polling frequency.
- **Fit:** **optional fallback/cross-check only**, never a primary path for anything the trade gate depends on — consistent with the architecture's explicit rejection of scraping-as-primary (see Architecture §29).

### F. Optional secondary validation sources
Any second provider from A–D used purely to cross-check a first provider's LTP/OI/IV for the `PROVIDER_DISAGREEMENT` check in the failure-mode matrix. Doesn't need to be full-featured — even a low-frequency secondary LTP source is enough to catch a primary provider serving stale/wrong data.

---

## Evaluation criteria template (fill in per candidate provider before procurement)

| Criterion | Question to answer |
|---|---|
| Data provided | Exactly which fields (OHLCV/LTP/option chain/Greeks/news) and at what granularity? |
| Latency | Real-time, delayed-by-N, or polling-only? |
| Historical availability | How far back, what granularity, is backfill needed for `replay/`? |
| API limits | Requests/minute, symbols per request, concurrent connections |
| Reliability | Published uptime/SLA if any; anecdotal reliability from other users |
| Cost | Subscription tier that covers your actual symbol/frequency needs |
| Licensing/usage restrictions | Personal-use only? Redistribution restrictions? Any restriction relevant to storing historical snapshots (needed for audit/replay)? |
| Production suitability | Is this provider meant for exactly this kind of continuous polling use, or is it rate-limited for casual/dashboard use only? |
| Fallback provider | Which category-A–D alternative does the registry fail over to if this one is down? |

---

## Provider Abstraction Design

```python
# data/providers/base.py (illustrative shape, not final code)

class MarketDataProvider(Protocol):
    async def get_ohlcv(self, instrument: str, timeframe: Timeframe, since: datetime) -> list[RawCandle]: ...
    async def get_quote(self, instrument: str) -> RawQuote: ...
    async def health(self) -> ProviderHealth: ...

class OptionChainProvider(Protocol):
    async def get_chain(self, underlying: str, expiry: date) -> RawOptionChain: ...
    async def health(self) -> ProviderHealth: ...

class NewsProvider(Protocol):
    async def get_recent(self, since: datetime) -> list[RawNewsItem]: ...
    async def health(self) -> ProviderHealth: ...
```

- `ProviderRegistry` holds an ordered list per `Protocol`, each entry carrying a priority and a circuit-breaker state (`CLOSED` → normal, `OPEN` → skipped after N consecutive failures, `HALF_OPEN` → periodic retry probe).
- `data/ingestion` always asks the registry, never imports a concrete provider directly — this is the single change point if a provider is swapped or added.
- Every provider call is wrapped with a timeout and structured error mapping (`ProviderTimeout`, `ProviderRateLimited`, `ProviderMalformedResponse`, `ProviderUnavailable`) so `data/validation` and the failure-mode matrix have consistent inputs regardless of which vendor is behind them.
- All raw payloads are persisted (candles/option_chain_snapshots/news_events) *before* normalization discards provider-specific shape — this is what makes replay and audit possible even if a provider's API later changes shape.

---

## DhanHQ v2 Capability Assessment

**Decision (2026-08-27 review):** DhanHQ v2 is the approved first concrete provider implementation, for data/research only (category B — broker API, used strictly read-only). Details below confirmed against the public DhanHQ v2 documentation (`docs.dhanhq.co/api/v2/`, `dhanhq.co/docs/v2/*`) at the time of this review — re-verify against current docs before implementation, since broker APIs change.

### What Dhan provides (and where it maps in this architecture)

| Dhan capability | Endpoint | Maps to |
|---|---|---|
| LTP | `POST /v2/marketfeed/ltp` | `MarketDataProvider.get_quote()` — `REAL_TIME` class |
| OHLC snapshot | `POST /v2/marketfeed/ohlc` | `MarketDataProvider.get_quote()` |
| Full quote (depth, OI, avg price, circuit limits, net change) | `POST /v2/marketfeed/quote` | `MarketDataProvider.get_quote()`, feeds `market breadth`-adjacent fields where available |
| Daily historical OHLCV (+ optional OI) | `POST /charts/historical` | `MarketDataProvider.get_ohlcv()` — daily timeframe, and `HistoricalProvider` backfill source |
| Intraday historical OHLCV (1/5/15/25/60 min, + optional OI) | `POST /charts/intraday` | `MarketDataProvider.get_ohlcv()` — 5m/15m/30m/1h timeframes (30m derived by resampling 15m or via a supported interval) |
| Real-time option chain: OI, volume, IV, Greeks (delta/theta/gamma/vega), LTP, top bid/ask per strike | `POST /optionchain` | `OptionChainProvider.get_chain()` — `SHORT_INTERVAL` class |
| Expiry list per underlying | `POST /optionchain/expirylist` | Options engine's expiry-selection input |
| Instrument/security master (CSV or `GET /v2/instrument/{exchangeSegment}`) | per-segment (`NSE_EQ`, `NSE_FNO`, `NSE_CURRENCY`, `BSE_EQ`, `BSE_FNO`, `BSE_CURRENCY`, `MCX_COMM`, `IDX_I`) | `SLOW_REFRESH` — `instruments`/`option_contracts` reference tables |
| WebSocket live feed | separate from REST marketfeed | Future `REAL_TIME` optimization; Phase 1 uses REST polling only, matching the freshness-class design (A2) rather than adding streaming complexity before it's needed |

**Auth:** `access-token` (JWT, 24h validity, renewable) + `client-id` headers (client id also required in some request bodies as `dhanClientId`). API key/secret valid 12 months if using the OAuth issuance flow. Credentials go in `.env` only (`DHAN_CLIENT_ID`, `DHAN_ACCESS_TOKEN`), never in code, per §23.

**Rate limits (as documented):** marketfeed endpoints — 1 request/second, up to 1000 instruments per request. Option chain — 1 request per 3 seconds per underlying+expiry (matches the `SHORT_INTERVAL` class's provider-bounded cadence in Addendum A2, not a policy choice). Intraday historical — max 90 days per request per interval. Daily historical — available back to a scrip's inception.

### What Dhan does NOT provide (confirmed gaps — must be sourced elsewhere or built in-house)

| Gap | Consequence for this architecture |
|---|---|
| **Historical option-chain snapshots** — the option chain API is real-time only | `option_chain_snapshots` (§7) must be populated by our own polling + persistence at ingestion time. There is no way to backfill option-chain history from Dhan after the fact — a missed real-time poll is permanently unreplayable for that timestamp. This is the single most important operational consequence of choosing Dhan (see Addendum A1). |
| **News / corporate-announcement feed** | Category D (news) must come from a separate provider — NSE/BSE announcement RSS + a general news API, per the original recommendation below. Not a Dhan gap to work around, just confirms Dhan was never expected to cover this. |
| **Market breadth / advances-declines statistics** | Sample-universe breadth is computed from the official Nifty 50 constituent list (`nsearchives.nseindia.com/content/indices/ind_nifty50list.csv`) plus Upstox quotes. This is **not** NSE official A/D. FII/DII remain UNKNOWN unless caller-supplied (`.xls` not parsed). EOD delivery uses `sec_bhavdata_full_{ddmmyyyy}.csv`. |
| **Fundamental/corporate-action data** (dividends, splits, results calendar) beyond the bare instrument master | Out of scope for Phase 1's technical/options-focused engines; would need a category-C/D vendor if ever required. |
| **A documented official India VIX endpoint distinct from treating it as a regular index quote** | Usable via the standard quote/historical endpoints if VIX is listed as an index security id in the instrument master — verify at implementation time, not assumed here. |
| **Second independent LTP source for cross-validation** | Dhan alone cannot satisfy the provider-disagreement check (Addendum A4) — that check needs a second provider (a second broker API or category-C vendor), procurement TBD. Until then, cross-validation is implemented and tested but has nothing live to compare against, logged as `CROSS_VALIDATION_UNAVAILABLE`. |

### Adapter shape

`app/data/providers/dhan_provider.py` implements both `MarketDataProvider` and `OptionChainProvider` behind an injected `httpx.AsyncClient`, translating Dhan's exchange-segment strings (`NSE_EQ`, `NSE_FNO`, `IDX_I`, …) and raw JSON shapes into the provider layer's `Raw*` types; `app/data/normalization/dhan_normalizer.py` is the only place that turns those `Raw*` types into canonical `domain` models. No other module ever sees a Dhan field name.

---

## Phase 1 procurement (updated 2026-08-27 review)

1. **Primary market-data + option-chain source: DhanHQ v2** (category B) — approved, data/research use only. See capability assessment above.
2. Secondary/cross-check source for the `PROVIDER_DISAGREEMENT` check (Addendum A4 in the architecture doc): **not yet procured** — Dhan alone cannot satisfy this. Still an open decision; not blocking for the Data Foundation milestone since `MockProvider`/synthetic fixtures exercise the cross-validation code path in tests until a second live provider exists.
3. News: NSE/BSE corporate-announcement RSS (free, low-latency, structured) + one general news API for macro headlines — still open, not needed until the news domain module (later phase).
4. Category E (unofficial scraping) not used at all in Phase 1.
