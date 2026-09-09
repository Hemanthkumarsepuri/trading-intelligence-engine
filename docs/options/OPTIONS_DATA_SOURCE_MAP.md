# Options Data Source Map

Real capability findings for the Options Intelligence Engine's data
foundation — every endpoint below was called live against Upstox during
this session and its response shape captured directly, not taken from
documentation (the two dedicated options doc pages 404'd; everything here
was confirmed by calling the real API and inspecting the real response).

Verified: 2026-08-28 / 2026-08-29. Provider: Upstox (see
[`docs/data-sources/PROVIDER_DECISION.md`](../data-sources/PROVIDER_DECISION.md)
for why Upstox and not Dhan/NSE-direct).

## Instrument discovery (zero new API calls)

The instrument master already fetched for equity/index symbol resolution
(`https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz`,
cached at `data/reference/upstox_nse_instruments.json`) contains the
**complete `NSE_FO` derivatives segment** — 29,713 real entries, confirmed
by direct inspection. Nothing in this section requires a dedicated API call.

| Data field | Source | Real field name | Notes |
|---|---|---|---|
| F&O eligibility | cached master, `NSE_FO` segment | `underlying_symbol` | An underlying with zero `NSE_FO` rows is simply not optionable — most NSE equities have none. |
| Expiry list (all) | cached master | `expiry` (epoch ms) | No dedicated expiry-list endpoint exists (see below); derived by distinct-value scan. |
| Weekly vs. monthly | cached master | `weekly` (bool) | Upstox's own flag, not inferred by day-of-month heuristics. Confirmed: RELIANCE has 3 expiries, all `weekly=False` (equity F&O is monthly-only); NIFTY has 18 expiries, near-dated ones `weekly=True`. |
| Lot size | cached master | `lot_size` / `minimum_lot` | Constant per underlying. |
| Tick size | cached master | `tick_size` | Not yet consumed by any analysis code (documented, unused). |
| Strike list per expiry | cached master | `strike_price` | Also obtainable from the live chain response directly. |
| Futures instrument_key | cached master, `instrument_type="FUT"` | `instrument_key` | **Only exists for monthly expiries** in practice — confirmed live: NIFTY has exactly 3 FUT contracts (matching its 3 monthly expiries) against 18 total option expiries; a weekly option expiry with no matching monthly date has no futures contract at all (real market structure, not a data gap). |

Implemented in [`app/data/providers/upstox_fo_master.py`](../../app/data/providers/upstox_fo_master.py).

## Live/near-live data (real API calls, all read-only)

| Data field | Endpoint | Update frequency | Timestamp | Reliability | Real-time usable? |
|---|---|---|---|---|---|
| Full option chain (all strikes, CE+PE, LTP/bid/ask/OI/prev-OI/Greeks/IV) | `GET /v2/option/chain?instrument_key=...&expiry_date=YYYY-MM-DD` | On request (REST poll) | None per-leg — Upstox returns no per-leg exchange timestamp on this endpoint; `data_timestamp` in this codebase is the receipt instant (documented, conservative) | High — real, live-tested across NIFTY, BANKNIFTY, RELIANCE, TCS, SBIN | Yes, via polling. No WebSocket option-chain feed integrated this session (see Limitations). |
| Contract/expiry list for one underlying | `GET /v2/option/contract?instrument_key=...` | Static within a trading day | n/a | High | Used as `get_expiries()`'s backing call; not needed for eligibility/expiry lookups since the cached master already covers those. |
| Futures LTP/OI/volume | `GET /v2/market-quote/quotes?instrument_key=NSE_FO|...` | On request | `last_trade_time` (real exchange epoch-ms) | High | Yes — same endpoint already used for equity/index quotes, works unmodified for `NSE_FO` FUT keys. |
| Underlying spot price (equity/index) | Same `/v2/option/chain` response, `underlying_spot_price` field | On request | Same as chain | High | Redundant with the existing equity/index quote path but free — comes back with every chain call. |
| Market open/closed state | `GET /v2/market/status/{exchange}` | On request | `last_updated` | High | Already integrated (`UpstoxProvider.get_market_status`), reused unchanged for options context. |

## Two guessed endpoint paths that do NOT exist (confirmed by real error, not assumed)

- `GET /v2/option/expiries` — real HTTP error response, confirmed live.
- `GET /v2/option/contract/expiries` — real HTTP error response, confirmed live.

No dedicated expiry-list endpoint exists; `/v2/option/contract`'s full
contract list is the only real source, and the cached instrument master is
strictly cheaper for the same information.

## Fields available but NOT wired into any DTO/consumer (documented, not built)

- `pop` (probability of profit) on each leg's `option_greeks` — real field,
  confirmed present on every leg in every live response this session, but
  has no consumer in `RawOptionLeg`/`OptionQuote` yet. Deliberately not
  added to the DTO without an immediate consumer (this project's
  no-speculative-surface discipline).
- `close_price` (previous session's close) on each leg's `market_data` —
  same status: real, present, unused.
- Per-strike `pcr` — Upstox pre-computes this per strike; this codebase
  computes its own chain-wide PCR from raw OI instead (`chain_totals()`),
  so Upstox's version is redundant and not parsed.

## Limitations / not built this session

- **No WebSocket option-Greeks/depth feed integrated.** The v3 Market Data
  Feed protocol (already integrated for equity/index LTPC) supports an
  `option_greeks` subscription mode per its `.proto` schema, but no code
  subscribes to it — the REST `/v2/option/chain` poll is the only options
  data path built this session. Real-time (sub-second) option Greek
  streaming remains unproven.
- **No option-chain snapshot persistence for replay.** `/v2/option/chain`
  is real-time only (matching Dhan's chain endpoint per
  `ARCHITECTURE.md` Addendum A1) — a chain not persisted at fetch time is
  unreplayable at that timestamp. Persistence was out of scope this round.
- **IV rank/percentile, skew, and any historical-IV comparison** need a
  history of chain snapshots this session did not build; today's chain is
  evidence for "what the market is pricing right now," not "how that
  compares to the last N days," and nothing in this codebase should be
  read as claiming the latter yet.
