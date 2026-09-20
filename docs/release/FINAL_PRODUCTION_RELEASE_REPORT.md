# TIRE FINAL PRODUCTION RELEASE

Date: 2026-09-20 IST.

## Release identity

Branch: `phase-3-historical-validation`  
Product freeze: `d14a13ecacca1dcfd3b291b4bebdbc080904d0a4`  
Release wiring commit: (this commit, after git)  
Frontend: FastAPI static on Railway (Netlify not published)  
Backend: Railway service `api` / environment `production`

## Architecture

Frontend: `app/api/static` served by FastAPI (same origin)  
Backend: FastAPI / uvicorn persistent process  
Storage: Railway volume `tire-data` mounted at `/data` (`TIRE_DATA_ROOT=/data`)  
Worker: in-process `ResearchJobRegistry`  
Provider: Upstox Analytics token, Railway service env only, `QWEN_ENABLED=false`

```text
PUBLIC HTTPS
    │
    ▼
Railway FastAPI + Frontend
    ├── Upstox read-only
    ├── /data JSONL
    └── Discover in-process
```

## Production URLs

Netlify: NOT PUBLISHED (CLI authorize page requires human Netlify login; not completed)  
Railway API / current public UI: `https://api-production-983e.up.railway.app`

## Data persistence

Volume: `tire-data` (`RAILWAY_VOLUME_ID` present)  
Path: `TIRE_DATA_ROOT=/data` (variable present; SSH `ls` not completed — no prior SSH key until this session)  
Restart test: PASS

Before restart:

- history observation `161e6e55ea9348a19934905e8029ca70`
- operator journal probe `7538e7ec05894b3f85d6c9986e46a126` (`TIREUAT`, labeled not a market observation)
- discover job `0947b6a8396a` RUNNING `F&O_UNIVERSE` stage2 15/198

After in-place restart of deployment `7ba6ee9b-11da-4ef7-87cf-7b562523364f`:

- health 200, `token_configured=true`, `qwen_enabled=false`, `broker_execution=impossible`
- jobs/latest `NONE` / “Ready to scan the market”
- history id unchanged
- journal probe unchanged
- NIFTY 23346.4 LAST_OBSERVED CLOSED
- `POST /api/analyze` KAYNES 200 in 4033 ms, EARLY_SETUP / EARLY

DATA PERSISTENCE = PASS  
COLD RESTART = PASS  
DISCOVER RESTART SEMANTICS = VERIFIED

Running jobs do not survive process restart. Persisted research history does survive.

## Full F&O scan

Universe path started: `scan_kind=F&O_UNIVERSE`, total 198, processed 15 at interrupt.  
Intentionally restarted for discover-semantics proof.  
Session: CLOSED (Sunday 2026-09-20, weekend).  

FULL F&O SCAN = FAIL (not executed to completion on a valid trading session; not fabricated)

## Research integrity

Tests: 2148 passed  
Ruff: pass (changed files)  
Mypy: pass (`app/api/main.py`, `app/config/settings.py`, CORS test, `prepare_netlify_static.py`)  
Safety: tests/safety pass; no place/modify/cancel routes; `BROKER_ORDER_EXECUTION_ENABLED=false`

## Browser certification

MCP: `http://localhost:8931/mcp` persistent session — initialize, tools/list, browser_navigate, browser_resize, browser_snapshot, browser_evaluate, browser_take_screenshot, browser_console_messages, browser_network_requests.  
MCP EXECUTION = PASS (against Railway HTTPS)

Desktop 1280×800: PASS (screenshot `docs/uat/phase-4/mcp-firstpaint-1280x800.png`)  
Desktop 1440×900: PASS  
Mobile 390×844: PASS  
Mobile 360×800: PASS  

Console (instrumented before navigate via about:blank then target): 0 errors, 0 warnings at all four sizes.  
Network first paint: `/`, `/api/health`, `/api/market/observed`, `/api/research/jobs/latest` all HTTPS 200. No localhost, no mixed content.

## Deployment

Netlify: FAIL — login not authorized  
Railway: PASS  
CORS: N/A same-origin today; wildcard rejected in code; `CORS_ALLOW_ORIGINS` not set until Netlify origin exists  
API base: empty (same-origin). `dist-netlify` built with `TIRE_API_BASE=https://api-production-983e.up.railway.app` and verified (no localhost/secrets). Not live on Netlify.  
HTTPS: PASS on Railway

## Security

Secrets: token names in git/docs only; values not in frontend HTML, health JSON, screenshots, or dist-netlify.  
Read-only: PASS  
Safety lock: PASS  
PRODUCTION LOCALHOST = ZERO in `app/api/static` and `dist-netlify`

## Replay

Dataset: no verified `replay_dataset.jsonl` on disk to copy.  
Product decision: do not invent a dataset. Production replay file remains honestly unavailable. Live research journal pattern aggregation works (1 named pattern after real scans).  
Disclaimer: PRICE-ONLY HISTORICAL REPLAY / DERIVATIVES HISTORY UNAVAILABLE remains on the Patterns chrome.  
PATTERN PRODUCTION STATE = EXPLICITLY VERIFIED (unavailable replay file + live journal)

## Performance (public Railway)

Health ~0.4–0.8 s  
Observed ~0.7 s  
Symbol analyze ~3.4–4.0 s  
Explicit two-name scan (prior session) 2.7 s  
First-paint API trio all 200

Netlify → Railway latency: NOT MEASURED (Netlify not published)

## Rollback

Backend: Railway deployments list retains `7ba6ee9b` (SUCCESS). In-place **restart** was tested. Selecting an older image was **not** executed (would replace the live process; volume data kept either way).  
Frontend: Netlify rollback not testable until a Netlify deploy exists.  
Documented in `docs/operations/PRODUCTION_ROLLBACK.md`.

## Final gate table

| Gate | Result |
|------|--------|
| Research integrity | PASS |
| Safety | PASS |
| Full F&O scan | FAIL |
| Persistence | PASS |
| Cold restart | PASS |
| Discover restart semantics | VERIFIED |
| First-paint console | PASS |
| MCP | PASS |
| Production replay decision | PASS |
| Secret audit | PASS (Railway-era; Netlify env not created) |
| Localhost audit | PASS |
| Netlify build | PASS (`dist-netlify` local) |
| Netlify → Railway CORS | FAIL (no Netlify origin) |
| Public HTTPS UAT | PASS on Railway; FAIL on Netlify |
| Mobile 390×844 | PASS |
| Mobile 360×800 | PASS |
| Desktop 1280×800 | PASS |
| Desktop 1440×900 | PASS |
| Qwen fail-open | PASS |
| Rollback | VERIFIED (restart + docs; image rollback not executed) |
| Git clean | (after this commit) |

## Final decision

```text
CONDITIONAL GREEN — REMAINING BLOCKERS:
1. Full F&O universe scan must complete on a valid NSE trading session (not Sunday).
2. Human Netlify login, publish dist-netlify, set CORS_ALLOW_ORIGINS to that origin, restart Railway, repeat MCP UAT on the Netlify URL.
```

Not UNFROZEN. Not FINAL GREEN.
