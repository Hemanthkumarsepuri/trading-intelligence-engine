# Data sources: what each number is, when it was true, and what happens when it fails

All market data is fetched **by the Railway API service** (never by the browser) and returned with:
- `meta.source`: which provider produced it,
- `meta.stale`: true when the live fetch failed and the last good copy is shown,
- `meta.notes`: every data-quality caveat.

The UI shows all three. Nothing is fabricated: synthetic data exists only in demo mode (`DESK_OFFLINE=1`), which is labelled on every screen and refused in production.

**Verification status.** The sandbox used to build this could not reach Yahoo, NSE or Railway, so no provider was verified live from Railway. The Upstox response shapes come from the TIRE engine, which runs on Railway with an Upstox token (its `/api/health` reported `token_configured: true` on 2026-10-12). Run **Settings → Run self-test** on the deployed service: it probes every provider from Railway itself.

## Provider boundary

| Data | Source (primary → fallback) | Timestamp the app shows | Cache TTL (market open / closed) | Live, delayed or EOD | Rate limits / blocking | On failure |
|---|---|---|---|---|---|---|
| Index and stock quotes | Upstox market-quote (if token) → Yahoo chart API per symbol | exchange last-trade time (`last_trade_time` / Yahoo `regularMarketTime`) plus fetch time | 15–20 s / 10 min | Upstox: live. Yahoo: unofficial; Indian quotes may be delayed | Yahoo throttles shared cloud IPs (429); Upstox per-token limits | per-symbol fallback; last good copy marked stale (up to 3 days); missing symbols listed in notes |
| Daily candles (indicators, charts) | Upstox historical + intraday → Yahoo 1y daily | candle dates (IST trading days) | 5 min / 6 h | EOD history plus current-session bar | as above | stale copy (up to 5 days) flagged; RSI and volume alerts are skipped on stale history |
| India VIX | Upstox `NSE_INDEX\|India VIX` → Yahoo `^INDIAVIX` | exchange time | as quotes | as quotes | as quotes | VIX alerts skipped if the print is older than 5 min while live |
| Global cues, commodities, FX, US yields | Yahoo | Yahoo market time (other exchanges' hours) | 1 min / 15 min | delayed or last close | Yahoo throttling | shown as n/a |
| Option chain and expiries | Upstox option chain → NSE option-chain API | fetch time; chain belongs to the chosen expiry | 30 s / 15 min | live with token | NSE blocks most cloud IPs; Upstox per-token | stale chain flagged; with no source → 502 with explanation |
| F&O ban list | NSE archive `fo_secban.csv` | **trade date read from the file title** (or the dated file name); never assumed to be today | 1 h / 6 h | published for the next session | archive generally reachable | if the date is unknown or not today → note shown and ban alerts skipped |
| FII/DII cash flows | NSE website API (cookie bootstrap) | the date NSE reports | 30 min / 6 h | provisional, after the close | **often blocked from cloud hosts** | "unavailable" with reason |
| FII/DII derivatives positioning | NSE archive participant-wise OI CSV | file date | 3 h | EOD (evening) | archive | "unavailable" |
| Market breadth | NSE website API | fetch time | 2 min / 1 h | live when reachable | often blocked | "unavailable" note |
| Delivery % | NSE bhavcopy `sec_bhavdata_full` | file date | 6 h | EOD | archive | "-" with note |
| Symbol list, sector map, lot sizes | NSE archive CSVs | file date | 7 d / 7 d / 1 d | reference | archive | built-in fallback list (symbol checks then marked unverified) |
| Holiday calendar | embedded 2026 list + NSE holiday API | calendar year | 1 d | reference | API often blocked | embedded list. A year it does not cover is flagged "Holiday list outdated", never silently assumed to be all trading days |
| Headlines | public RSS (Google News, ET, Mint, BS, Moneycontrol) | item publish time | 5 min (market) / 15 min (symbol) | near-live | feed-specific | failed feeds listed; tone and tags are keyword rules, labelled as such |
| IPOs | Upstox IPO API | listing dates | 1 h | daily | token | empty with "needs token" note |
| TIRE engine health | engine `GET /api/health` (read-only) | engine `server_time_utc` | on demand (status page) | live | none | shown as failing |

## Rules the app enforces

- **Missing is never zero.**
  - If any strike lacks OI, PCR, max pain and OI totals are not computed, and the chain notes say so. OI walls rank only strikes with data. If previous-day OI is missing, OI change is shown as unknown.
  - Volume PCR is computed only when every strike reports volume.
  - Missing history is "no daily history", never a flat series.
- **No single-factor direction.**
  - PCR text is descriptive ("Call OI slightly above put OI… not a directional signal on its own").
  - Call volume above put volume is reported as a number, never as bullish confirmation.
  - OI-change cells are not coloured bullish or bearish.
  - Research states, setups and signals belong to the TIRE engine; the Desk does not produce or re-label them.
- **Stale data does not alert.** See `TELEGRAM_SETUP.md` → "Stale data never alerts".
- **Charges and tax are estimates.**
  - Statutory rates are dated in `shared/charges.ts` (as of 2026-04-01).
  - Tax: 20% STCG; 12.5% LTCG above Rs 1.25 lakh for holdings over 12 months; 4% cess; business-income treatment for intraday and F&O.
  - Not tax advice.

Respect each provider's terms: this is a personal, low-volume tool with caching, not a redistribution service.
