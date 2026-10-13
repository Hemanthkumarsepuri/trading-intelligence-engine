# TIRE Desk

A private, single-owner market desk for Indian markets: frontend on Netlify, API and scheduler on Railway.

- **Market monitor**: indices, India VIX regime, breadth, sector strength, global cues (US/Asia/Europe, crude, gold, USD/INR, DXY, US 10Y), FII/DII cash flows, FII derivatives positioning, F&O ban list, IPOs, headlines.
- **Option chain**: Nifty, Bank Nifty, Fin Nifty, Midcap Select. PCR, max pain, OI walls, fresh writing, ATM IV, put-call skew, market-implied move.
- **Watchlist and alerts**: quotes with daily technicals, symbol pages with candlestick chart; price/%/RSI/volume/VIX/F&O-ban alerts delivered to Telegram, checked every 5 minutes during market hours; pre-market brief and end-of-day summary.
- **Journal and risk**: every trade with Indian charges (STT, exchange, SEBI, stamp, GST, DP), R-multiples, equity curve, setup/weekday/emotion breakdowns, rule-violation detection, a daily risk guard, position sizing, and FY tax estimates (STCG/LTCG, speculative vs non-speculative).
- **Portfolio**: broker CSV import (Zerodha/Upstox/Groww style), live valuation, sector exposure, long-term/short-term lot status.

It is a **read-only research tool**. It never places orders, gives no buy/sell advice and shows no probabilities or confidence scores. Observations are facts about price, volume and positioning.

## Architecture

Netlify serves the frontend. Its edge function proxies `/api/*` to the **TIRE Desk API service on Railway**, which owns the data (Railway volume, `/data/desk`), Telegram delivery and the only scheduler. The existing TIRE research engine on Railway is untouched. See [RAILWAY_INTEGRATION.md](RAILWAY_INTEGRATION.md) for the design and [DEPLOYMENT_CHECKLIST.md](DEPLOYMENT_CHECKLIST.md) for the gated deploy steps.

## Quick start (local, demo data)

```bash
npm install
APP_PASSWORD=dev-only-pass SESSION_SECRET=$(openssl rand -hex 32) DESK_OFFLINE=1 npm run dev   # UI on http://localhost:5173
```

## Commands

| Command | What it does |
|---|---|
| `npm run dev` | local API (8788) + Vite UI (5173) |
| `npm run check` | typecheck, lint, tests, web build with bundle safety check, server bundle |
| `npm run build:web` | Netlify build (frontend) |
| `npm run build:server` / `npm start` | Railway build / start |
| `npm run hash-password` | create `APP_PASSWORD_HASH` |

## Layout

```
shared/                   pure logic shared by server and UI (calendar, charges, tax, indicators, option chain, journal, alerts)
server/                   router, auth, file store (compare-and-set), cache, providers, services
server/main.ts            Railway entry point (API + scheduler)
server/scheduler.ts       the single scheduler (alerts, digests, backups)
netlify/edge-functions/   api-proxy.ts: same-origin /api proxy to Railway
src/                      React UI
tests/                    vitest suites (logic, API, Railway integration, persistence, scheduler, Telegram)
```
