# Options Workbench

Read-only research for Indian F&O: search, validate a contract, chart the underlying and option across
timeframes, see explainable breakout structure, scan the F&O universe, and read exchange/news context.
**No route or control places, modifies or cancels an order** (enforced by a unit test and a browser test).

## Screens

| Screen | Path | Purpose |
|---|---|---|
| Options Workbench | `#/workbench?u=SYMBOL&tf=15m&c=<contract id>` | search, underlying validation, charts, breakout status, contract builder + validation |
| Market Scanner | `#/scanner` | filtered, evidence-ranked candidates with audit trail |
| Watchlist | `#/watchlist` | equity watchlist (unchanged) + tracked option contracts and comparison |
| Contract Explorer | `#/explorer` | every strike for an underlying/expiry with per-quote quality and filters |
| News and Events | `#/events` | NSE filings, results calendar, RSS headlines with source/time/status |
| Data Health | `#/data-health` | provider status, freshness limits, entitlements, in-app alerts |

On phones the bottom bar has eight slots (Today, Market, Workbench, Scanner, Watchlist, Alerts, Journal, More); the rest is under More.

## Architecture

```
shared/        pure, deterministic, unit-tested (browser and server)
  quality.ts   data states, freshness classification, provider reconciliation
  candles.ts   session-aligned aggregation (1m,3m,5m,15m,30m,1h,1d), availability
  levels.ts    swing clusters, session/opening-range levels, VWAP, relative volume, trend, MTF agreement
  breakout.ts  lifecycle state machine (up and down share one path)
  contract.ts  contract identity, search parsing, validation, liquidity class, Greeks
  events.ts    announcement/news/calendar normalisation, chart markers, upcoming events
  scanner.ts   qualification, filters, ranking, exclusion summary
  wbalerts.ts  alert derivation and deduplication
server/services/wb/   data.ts (providers, cache, health)  analysis.ts  contracts.ts  scanner.ts  events.ts  misc.ts
server/fixtures-wb.ts demo data (DESK_OFFLINE=1 only)
src/wb/, src/pages/   React screens; charts use TradingView Lightweight Charts (Apache-2.0, already a dependency)
```

Market data is separate from analysis: providers return normalised candles/quotes/chains with provenance; `shared/` never does I/O.
Charting is a drawing layer only. ECharts was evaluated and not adopted: the screens need financial candles with synchronised panes,
which Lightweight Charts covers; adding a second library would add weight for no gain. `technicalindicators` was not adopted: the
indicators used (EMA, RSI, ATR, MACD, Bollinger, VWAP) are small, are implemented in `shared/` and unit-tested.

## Providers and actual entitlements

| Data | Source | Notes |
|---|---|---|
| Underlying quote, intraday and daily candles | Yahoo Finance chart API | unofficial, no entitlement or SLA, **delayed**; always labelled DELAYED; accepted up to 20 min old |
| Option chain (stocks and indices), bid/ask depth, volume, OI, IV | NSE website JSON (`option-chain-v3`) | public pages; may be blocked from cloud IPs; provider snapshot time is parsed and used for freshness |
| Option chain, Greeks, option candles | Upstox Analytics token (optional) | official, read-only; implemented, **not verified live** (no token configured) |
| Lot sizes, symbol master, sectors | NSE archive CSVs | existing integration |
| Filings, results/board-meeting calendar | NSE website JSON | existing-style fail-soft provider |
| Headlines | public RSS (existing) | reported by outlet, not verified |
| Macro/sector event calendar | none | reported as UNSUPPORTED; nothing is invented |

No order API is configured or callable. Commercial use and redistribution of Yahoo/NSE data are not licensed by this app.

## Data states and freshness

`VALID, STALE, DATA_INSUFFICIENT, CONFLICT, UNKNOWN, UNSUPPORTED, MARKET_CLOSED`.

* Missing values are `UNKNOWN` (`null`), never zero. Untraded contracts (LTP 0, no book) are "no quote".
* Market open: quote 120 s, option chain 180 s, intraday candles 240 s, delayed free feed 20 min. Beyond that: `STALE`.
* Market closed (weekend/holiday/after 15:30 IST): `MARKET_CLOSED`, not an outage.
* Provider timestamp missing while open: `UNKNOWN` (cannot be proven current).
* A timestamp from the future: `CONFLICT`. Chain spot vs independent quote beyond tolerance (0.5%, 1.5% for a delayed feed): `CONFLICT`, both shown.
* Premium below intrinsic: time value withheld, `CONFLICT`.
* STALE/CONFLICT/UNKNOWN data never yields a CONFIRMED breakout and never raises an actionable alert.
* If a refresh fails, the last good copy is served marked stale with its original provider time.

Volume (contracts traded) and open interest (positions outstanding) are separate fields. Depth quantities are units (shares), divided by lot size for lots.

## Timeframes and aggregation

1m and 3m from 1m (5 sessions), 5m from 5m, 15m/30m/1h from 15m (about 20 sessions), daily from daily history. Buckets anchor to 09:15 IST;
1h bars start 09:15, 10:15 … 15:15 (the last is 15 minutes). A bucket is complete only if every source bar is present, none is an off-grid
snapshot, and its end time has passed. Missing bars are never invented; the forming bar is drawn grey and never used for confirmation.
At least 30 completed bars are needed to offer a timeframe; otherwise the button is disabled with the reason.

## Breakout methodology (`shared/breakout.ts`)

`NOT_READY → APPROACHING_LEVEL → POTENTIAL_BREAKOUT → CONFIRMATION_PENDING → CONFIRMED`, plus `FAILED`, `INVALIDATED`, `DATA_INSUFFICIENT`, `STALE`.
Defaults (configurable in code): approach within 0.5×ATR(14); a close must clear the level by 0.1×ATR; breakout-bar volume ≥ 1.5× the mean of the
previous 20 bars (needs ≥ 10 known-volume bars, else the verdict cannot reach CONFIRMED); hold = 2 consecutive closes or a retest within 0.25×ATR that holds;
FAILED = completed close back through the level within 8 bars; INVALIDATED = close 1×ATR on the wrong side.
Levels: confirmed swing pivots (3 bars each side) clustered within 0.5×ATR, previous-session high/low, opening range (first 15 minutes), rolling 20-bar high/low.
Every level records its method and touches. Indices carry no volume, so volume confirmation is unavailable for them.

## Candidate qualification (scanner)

Universe: F&O stocks (NSE lot-size file) plus four indices, large liquid names first, capped (default 40, max 60) to respect provider limits.
Pass 1 (all): daily + 15m/30m/1h structure, trend, relative volume, freshness. Pass 2 (best 15): scheduled events and the option chain.
A candidate needs fresh data, enough history, an active setup (approaching / potential / pending / confirmed by default) and, by default, quoted option contracts.
Filters: segment, direction, option type, expiry, liquidity, timeframe agreement, minimum relative volume, hide results-soon.
Ranking: lifecycle state, timeframes agreeing with the setup, relative volume, closeness to the level. No blended score. The summary separates underlyings from contracts
and, when fewer than five qualify, says so and lists the exclusion reasons; nothing is added to reach five.
Contracts to inspect are ATM and the next strike on the side matching the setup, on the nearest expiry at least five days away; this is shown, and is not a recommendation.

## News and events

NSE announcements (exchange-confirmed, IST broadcast time), NSE event calendar (scheduled date only), RSS headlines (reported). Each item keeps headline, publisher, original time and zone,
link (exchange links restricted to NSE/BSE hosts), symbols, type, verification, retrieval time and limitations. Only events with a reliable publication time are placed on charts.
Wording is "possible context"; causation is never claimed.

## Alerts

In-app only (`POST /api/workbench/alerts/evaluate`, shown on Data Health): approaching level, potential/confirmed/failed breakout, abnormal relative volume, spread widening,
liquidity deterioration, stale contract data, event proximity. Each carries evidence, source timestamp, timeframe, contract and invalidation; deduplicated per key with a six-hour cooldown.
Nothing is sent to Telegram. Scheduler integration is deferred.

## Configuration and secrets

No new secrets. Optional `UPSTOX_ANALYTICS_TOKEN` (existing variable) enables Upstox chains/greeks/option candles. Tracked contracts are private user data, stored with the rest of the
user data and included in exports and off-site backups (older backups without them leave existing tracked contracts untouched on import).
`DESK_OFFLINE=1` serves synthetic data and is refused in production; the `x-desk-demo-now` header (offline only) freezes the clock for browser tests.

## Tests

```
cd desk
npm run typecheck && npm run lint && npx vitest run        # unit + integration (faked providers)
npm run build:web && npm run build:server
cd e2e && npx playwright test                              # needs build:web first
```

## Deploy and verify staging

`node scripts/release-staging.mjs --dry-run` then without the flag (requires the commit to be published on `origin/staging/desk-rc-2026-10-13` and `desk/` clean).
Then `node scripts/live-acceptance.mjs` (unauthenticated checks) and, with the password in the environment only, `DESK_PASSWORD=… node scripts/live-acceptance.mjs`.

## Known limitations and deferred items

* Underlying data is delayed (free feed); real-time needs an entitled provider.
* Option candles need Upstox; otherwise only app-observed premium snapshots (labelled, gappy). Upstox code paths are not verified live.
* No licensed macro calendar; BSE filings are not integrated.
* Greeks are calculated (Black-Scholes, r = 6.5%, no dividends) when the provider supplies none; labelled.
* Sector mapping covers ten NSE industries; others show "no sector index mapped".
* Alerts are not scheduled and have no external delivery.
* Scanner covers a capped, liquidity-ordered subset per run.
* The authenticated live path (real NSE chain through the deployed app) was not exercised because no staging password is available to the assistant.
