# TIRE FINAL PRODUCTION RELEASE

Date: 2026-09-20 IST.

## Release identity

Branch: `phase-3-historical-validation` (local HEAD `849403a`)  
Product implementation: `e71afc5` feat: add observation-backed research watch  
Hygiene: `849403a` chore: ignore Netlify local CLI state  
Git origin: **none** — push NOT APPLICABLE; Railway/Netlify deploys were from this machine’s verified tree.

Frontend: Netlify static `https://tire-research-terminal.netlify.app`  
Backend: Railway FastAPI `https://api-production-983e.up.railway.app`

## Architecture

```text
PUBLIC HTTPS  https://tire-research-terminal.netlify.app
    │  TIRE_API_BASE=https://api-production-983e.up.railway.app
    ▼
Railway FastAPI
    ├── Upstox read-only (UPSTOX_ACCESS_TOKEN backend-only)
    ├── /data JSONL (TIRE_DATA_ROOT=/data, volume tire-data)
    └── Discover in-process
```

`broker_execution=impossible`. `QWEN_ENABLED=false`. `BROKER_ORDER_EXECUTION_ENABLED` remains off.

## Production URLs

Netlify: `https://tire-research-terminal.netlify.app` (deploy `6aaf919442f408ee5a5f4723`)  
Railway API: `https://api-production-983e.up.railway.app` (deployment `e98e4b44-97fb-4bfc-bb68-30b1f6ec1f76` SUCCESS)

Browser proof of new Observation Watch frontend: Watchlist copy “Pinned observations — what was true at T0 versus what TIRE observes now”, WATCHED CHANGES, OPEN SYMBOL / REMOVE, HTTPS GET/POST/DELETE `/api/research/watches`.

## Data persistence

Volume: `tire-data` at `/data`  
Health `data_root=/data`  
WatchRecord JSONL survived in-place restart of `e98e4b44`.  
T0 observation_id `b8f0b4e40aea41ed83d0907cdcdd0367` unchanged across latest updates and restart.  
REMOVE left an append-only tombstone; GET list empty after reload.

DATA PERSISTENCE = PASS  
COLD RESTART (watch) = PASS  

Hung SSH `4294967295` is not a TIRE defect.

## Full F&O scan

Session: CLOSED (Sunday 2026-09-20, weekend).  
FULL F&O SCAN = **PENDING** (not executed; not fabricated)

## Research integrity

Tests: **2161 passed**  
Ruff: pass  
Mypy: pass (`app --strict`, 167 files)  
Safety: no order/modify/cancel/execute routes; watch APIs are research JSONL only

## Browser certification

MCP: Playwright persistent session `http://localhost:8931/mcp`. Console/network instrumentation started before navigation.

| Viewport | Result |
| --- | --- |
| 1280×800 | PASS |
| 1440×900 | PASS |
| 390×844 | PASS |
| 360×800 | PASS |

First paint: 0 uncaught console errors. 0 mixed content. Health/observed/watches/jobs HTTPS 200. No localhost.

Expected Patterns replay 404: `HISTORICAL REPLAY DATASET NOT AVAILABLE IN THIS DEPLOYMENT`.

Watch forensics (1280×800, production): ANALYZE RELIANCE 1270 PE → pin → Watchlist → nav Market/History/Patterns/Screener/Watchlist/Symbol → reload → OPEN SYMBOL (T0 WATCH vs CURRENT) → Railway restart → WATCHED CHANGES CONTRACT_CHANGED → REMOVE → reload empty.

## Deployment

Netlify: PASS (live; browser-proven, not assumed from CLI success)  
Railway: PASS (`e98e4b44`)  
CORS: browser Origin Netlify → Railway watches 200  
API base in `dist-netlify`: Railway HTTPS; no `127.0.0.1` / localhost / `:8000` / `:8931`  
HTTPS: PASS  
Git push: NOT APPLICABLE

## Security

`UPSTOX_ACCESS_TOKEN` listed on Railway service variables; value never printed.  
Secrets not in frontend HTML or screenshots.  
Read-only: PASS  
Safety lock: PASS  

## Replay

No verified replay dataset on this deployment. Honest unavailable copy retained.  
PATTERN PRODUCTION STATE = EXPLICITLY VERIFIED (unavailable replay + live journal aggregation)

## Performance (public)

Health ~sub-second after restart (200 on first poll).  
RELIANCE 1270 PE analyze (closed print) ~few seconds.  
KAYNES / BEL analyze 200 on closed-session last prints.

## Rollback

Backend: prior SUCCESS images remain in Railway deployment list; in-place restart tested. Image rollback not executed.  
Frontend: Netlify deploy history; current live UI proven as Observation Watch.  
See `docs/operations/PRODUCTION_ROLLBACK.md`.

## Observation Watch gates

| Gate | Result |
|------|--------|
| Watch API | PASS |
| T0 | PASS |
| Persistence | PASS |
| Migration | PASS (unit + production duplicate skip) |
| Delta | PASS (NO_MATERIAL_CHANGE then CONTRACT_CHANGED from real later analyze) |
| Watchlist | PASS |
| Watched Changes | PASS |
| Mobile | PASS (first paint) |
| MCP | PASS |
| Security | PASS |
| Safety | PASS |
| Performance | PASS |
| Restart | PASS |
| RELIANCE 1270 PE | PASS (closed-session last print; not live) |
| KAYNES | PASS (observe only) |
| BEL | PASS (observe only) |
| ZZZXNOTREAL | PASS (400 without observation; instrument master miss) |
| Full F&O scan | PENDING |
| Git push | NOT APPLICABLE |

## Final decision

```text
CONDITIONAL GREEN — FULL F&O LIVE VALIDATION PENDING
```

Required watch/deploy gates passed. NSE was closed; full universe scan was not run and is not claimed.

Not FINAL GREEN. Not UNFROZEN.
