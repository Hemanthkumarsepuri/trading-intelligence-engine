# Provider Decision — Upstox (Analytics Token) Selected as Primary

Status: **Decision made, implemented as `app/data/providers/upstox_provider.py`.** Dhan remains implemented (`dhan_provider.py`), not removed — this is an addition/reprioritization, not a replacement.

## Decision

**Upstox, using its Analytics Token, is the primary read-only data source for this analysis engine.** Facts below were fetched directly from Upstox's own current published API documentation (upstox.com/developer/api-documentation/{analytics-token,v3/get-historical-candle-data,v3/get-market-data-feed}/) on 2026-08-28 — not assumed, not carried over from an older summary.

## Why Upstox over Dhan for this specific engine

| Criterion | Upstox (Analytics Token) | Dhan (existing `access-token`) |
|---|---|---|
| Can place/modify/cancel orders, even in principle | **No — structurally incapable.** Upstox's own docs: "does not support trading operations." | **Yes** — the token itself is a full-privilege account token; safety today relies entirely on this codebase never calling an order endpoint, not on the credential being scoped. |
| Credential lifetime | **1 year**, one manual dashboard generation, no OAuth redirect flow. | 24h JWT (per `docs/data-sources/PROVIDERS.md`'s existing Dhan capability assessment) — needs a renewal flow. |
| Setup complexity | No API key/secret/client ID needed to *use* it — one bearer token. | `client-id` + `access-token`, both required on every request. |
| M15 historical candles | Yes — `unit=minutes&interval=15` on the v3 historical-candle endpoint, confirmed. | Yes — already implemented (`docs/data-sources/PROVIDERS.md`). |
| Live streaming | WebSocket market-data feed, confirmed (protobuf-encoded). | WebSocket, per Dhan's own docs (not independently re-verified this round). |
| Architecture fit | Implements the exact same `MarketDataProvider` Protocol already defined in `app/data/providers/base.py` — zero architecture change. | Same. |
| Existing code in this repo | New (`upstox_provider.py`, this milestone). | Already implemented, unchanged. |

**The decisive factor is the safety property, not any performance/cost difference**: this project's entire premise is a read-only decision-support engine that must never execute trades. A token that cannot authorize a trade even if this codebase later had a bug is a genuine defense-in-depth layer Dhan's credential model does not offer. Given that, and given Upstox's simpler, longer-lived credential story, it is the better primary for exactly this engine's stated purpose — not a general claim that Upstox is "better than Dhan" for every use case.

## What was verified vs. what still needs live confirmation

**Verified against current official docs (2026-08-28):**
- Analytics Token: manual dashboard generation, 1 year validity, read-only, covers historical data + market feed + WebSocket, cannot place orders.
- Historical candle v3: `GET /v3/historical-candle/{instrument_key}/{unit}/{interval}/{to_date}/{from_date}`, `Authorization: Bearer {token}`, response `data.candles` = array of `[timestamp, open, high, low, close, volume, open_interest]` rows. Minute data (1-15min) available for the last ~1 month.
- WebSocket feed: `wss://` endpoint, `Authorization: Bearer {token}` header before connecting, **protobuf binary encoding** (not JSON) — requires the `MarketDataFeed.proto` schema to decode.

**Not verified / explicitly not implemented this milestone:**
- The exact market-quote/LTP REST endpoint's field names — two guessed doc URLs both 404'd. `UpstoxProvider.get_quote()` deliberately raises rather than guessing (see that method's docstring); current-state analysis derives an effective price from the latest candle/tick instead.
- The live WebSocket wire protocol itself (protobuf decoding, the "authorize then redirect" connection flow) — **not implemented this milestone.** It requires the `.proto` schema and a real token to test against meaningfully; building an untested binary-protocol client would be worse than not building it. See `docs/data-sources/LIVE_STREAM_STATUS.md`.
- `instrument_key` format uses ISIN, not a plain ticker (e.g. `NSE_EQ|INE002A01018` for Reliance Industries) — confirm the exact key from Upstox's own downloadable instrument master before first real use; do not trust a recalled ISIN as certain.

## Credentials required

Exactly one: `UPSTOX_ACCESS_TOKEN` (env var, see `.env.example`). Generated manually via the Upstox Developer dashboard's Analytics tab — no API key/secret/client ID needed. `UPSTOX_BASE_URL` defaults to `https://api.upstox.com` and rarely needs changing.

## Second-source re-evaluation (Options Intelligence Engine milestone, 2026-08-29)

Re-examined, specifically for whether a second legitimate source would close a concrete information gap for options analysis — not merely "for redundancy":

| Candidate gap | Legitimate accessible source? | Decision |
|---|---|---|
| F&O data (chain, OI, Greeks, futures) | Already fully covered by Upstox (`/v2/option/chain`, `/v2/option/contract`, `/v2/market-quote/quotes` on `NSE_FO` keys) — see `docs/options/OPTIONS_DATA_SOURCE_MAP.md`. | **Not needed.** |
| Market breadth (advance/decline, etc.) | No producer found without an NSE institutional data license (same conclusion as the prior milestone's NSE investigation) or scraping (excluded by policy). | **Not built** — documented gap, not guessed around. |
| Corporate events (earnings, board meetings, corporate actions) | NSE publishes some of this on its public website, but consuming it programmatically without a license is exactly the "unofficial scraping" this project's policy forbids; no API-based legitimate free source was found. | **Not built** — documented gap. |
| News | No accessible, legitimately licensable real-time news API was identified within this session's scope (financial news APIs are commercial products requiring their own paid subscription/licensing decision, out of scope to acquire unilaterally). | **Not built** — documented gap, an explicit "owner decision required" if pursued. |
| Volatility context beyond option-chain IV | India VIX is already resolvable and quotable through the existing Upstox index-quote path (`NSE_INDEX|India VIX`, confirmed in an earlier milestone) — not wired into this engine's report this round, but no new provider is needed to add it. | **Not needed** — a same-provider follow-up, not a second-source question. |
| Sector context | No producer of any kind exists in this codebase or was found accessible without a paid data vendor. | **Not built** — documented gap. |

**Conclusion: no second data provider was added.** Every concrete gap examined either has no legitimate accessible source at all (breadth, corporate events, news, sector context — each would require a paid commercial vendor or a license this project does not have) or is already fully served by Upstox alone (F&O data, India VIX). This mirrors the prior milestone's NSE-direct conclusion: Upstox remains sufficient, and adding a provider "for completeness" without a concrete need it uniquely solves would be exactly the unjustified complexity this project's engineering discipline forbids.

## News/event provider re-audit (2026-08-29, explicit STOP per policy)

Re-investigated specifically per this milestone's explicit "STOP and report" requirement before ever wiring in a news/event source. Live web search results (2026-08-29) confirm the prior conclusion, with more specific detail:

| Candidate | What it actually is | Verdict |
|---|---|---|
| GitHub "free" NSE/BSE API wrappers (`stock-nse-india`, `nse-bse-api`, `Indian-Stock-Market-API`) | Unofficial community libraries — several explicitly wrap Yahoo Finance or scrape `nseindia.com`'s public web pages directly. | **Rejected** — not an official/regulatory primary source (this project's own Section 20 source-priority ranking places these below official sources), and several are literal scrapers of NSE's site, which this project's policy already forbids regardless of the wrapper's convenience. |
| Apify NSE/BSE Announcements Scraper | A paid scraping-as-a-service product built on top of `nseindia.com`. | **Rejected** — scraping, paid, and a third party sitting between this system and the exchange; fails on two independent grounds. |
| TrueData | A real, NSE/BSE/MCX-*authorized* commercial data vendor offering corporate announcements/results/fundamentals via a licensed API. | **Legitimate in principle, requires a paid subscription decision.** Not integrated — acquiring a second paid vendor's credentials unilaterally is exactly the "STOP and report" case this milestone's own instructions require; this is an owner decision, not an engineering one. |
| PressMonitor NSE News API | A commercial financial-news-intelligence API covering NSE-listed companies. | Same status as TrueData — legitimate commercial product, requires a paid-subscription decision this project has not been authorized to make. |
| NSE's own official real-time data subscription (`nseindia.com/static/market-data/real-time-data-subscription`) | NSE's own paid real-time data product. | Same conclusion as the original NSE-direct investigation: institutional-grade, paid, licensed — not pursued for this personal-use system. |

**Conclusion, unchanged and now more specifically evidenced: no legitimate, free, ToS-compliant, real-time news/corporate-announcement source exists for this system today.** The only two credible options (TrueData, PressMonitor) are real, authorized commercial products — not fabricated placeholders — but both require an owner decision to pay for and configure a credential this project has never been given. This is reported here exactly as Section 22 requires, rather than silently deferred or worked around with a scraper.

## News source re-audit — real first-party source found (Sprint 4, 2026-08-29)

**This supersedes, for company news specifically, the "News/event provider
re-audit" section above.** That section investigated THIRD-PARTY vendors
(TrueData, PressMonitor, NSE's own paid feed, scraper wrappers) and
correctly rejected all of them. It never tried Upstox's own API surface
beyond what was already wired up. A fresh, live probe this milestone found
one Upstox endpoint had never been tried.

**Live-verified 2026-08-29**: `GET /v2/news?category=instrument_keys&
instrument_keys={instrument_key}` is a real, working endpoint on the SAME
Upstox account/Analytics Token this project already uses — not a new
credential, not a new vendor, not scraping. Discovered by live-probing
`/v2/news` (returned a real `400` naming its own required parameters,
not a `404`), then live-testing the parameter values it named.

Confirmed via real requests:
- Returns real per-company headlines (verified against RELIANCE: 3 items;
  TCS: 9 items; Infosys: 5 items) with real `heading`/`summary`/
  `article_link`/`published_time` fields, `published_time` a real
  epoch-milliseconds timestamp decoding to real, recent dates.
- An unrecognized or index instrument key (`NSE_INDEX|Nifty 50`) returns a
  real, honest empty result (`total_records: 0`) — never an error. This
  endpoint does not cover index-level or sector-level news, only
  individual-company headlines.
- Only ONE `instrument_key` is reliably returned per call even when
  multiple were requested comma-separated — this codebase calls it once
  per instrument, never batched.
- Real latency: ~0.07s per call — negligible against this project's
  latency budget.

**Classification: AVAILABLE** (per this document's own AVAILABLE /
PARTIALLY_AVAILABLE / PAID_REQUIRED / UNRELIABLE / BLOCKED / NOT_RELEVANT
scale) for company-level headlines. Implemented as `UpstoxProvider.
get_news()` / `app.domain.news.models`.

**What this does NOT change**: structured corporate-event data (earnings
dates, board-meeting schedules, regulatory filings as discrete fields
rather than headline text) has no legitimate source in this endpoint or
anywhere else found — that gap, and the sector/macro/global-event gaps
identified in the section above, remain open, undocumented-around STOPs,
exactly as before. This system also performs NO sentiment/direction
classification on the real headlines it now has — see `app.domain.news.
models`'s own docstring for why that remains a hard `UNKNOWN` by
construction, not a scope gap to fill later with a heuristic.

## Max-pain / dedicated Greeks endpoint re-check (2026-08-29)

Live-tested three plausible endpoint guesses directly against the real API (not docs, which don't cover this): `/v2/option/max-pain`, `/v2/option/greeks`, `/v2/market-quote/greeks` — all three returned a real `UDAPI100012 "Invalid Endpoint"` error. **Confirmed: Upstox has no dedicated max-pain or standalone-Greeks endpoint.** Greeks are already obtained via `/v2/option/chain`'s embedded `option_greeks` object (already used); max pain, if ever wanted, would need to be computed by this codebase from the chain's own OI data — not duplicating a provider value, since none exists.
