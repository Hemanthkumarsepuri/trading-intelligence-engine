# TIRE Desk: production readiness (release gate, 2026-10-13)

**Outcome: PARTIALLY READY, BLOCKERS REMAIN.**
The code, configuration and documentation are ready for a controlled deployment. The TIRE engine suite is green, including the uncommitted knowledge-time work. Nothing has been deployed, and nothing has been verified against the real Netlify or Railway services, Telegram, or market-data providers from Railway. The remaining blockers are owner actions plus live verification (§5 and `DEPLOYMENT_CHECKLIST.md`).

## 1. Verified current state (inspected, not assumed)

| Item | Finding |
|---|---|
| Repository | `Hemanthkumarsepuri/trading-intelligence-engine`, branch `phase-3-historical-validation`, HEAD `49166ac` = `origin/phase-3-historical-validation`; nothing staged |
| Working tree (owner's machine) | uncommitted engine work: 9 modified files (`app/api/main.py`, historical replay/dataset modules, 2 tests), 2 untracked (`app/domain/market/knowledge_time.py`, `tests/safety/test_historical_knowledge_time.py`); `desk/` untracked. **None of these engine files were touched.** |
| Engine on Railway | `GET https://api-production-983e.up.railway.app/api/health` → `data_root: "/data"`, `token_configured: true`, session window reported (fetched 2026-10-12 18:35 UTC). The response had no `outcome_sweep` field, which the local `main.py` adds; the deployed engine may therefore be older than local HEAD (not verified further). |
| Engine deploy config | `railway.toml`: Nixpacks, `uvicorn app.api.main:app`, health `/api/health`; `TIRE_DATA_ROOT` → volume at `/data` |
| Engine auth / CORS | no authentication on any route; CORS from `CORS_ALLOW_ORIGINS` with `allow_credentials=False` |
| Research terminal (Netlify) | `tire-research-terminal.netlify.app` points at the Railway engine (`meta-api-base`) |
| Desk before this sprint | backend = Netlify Functions + Netlify Blobs + Netlify scheduled functions; no path to Railway |

## 2. Tests and results

| Check | Result |
|---|---|
| Desk suite before changes (baseline) | 143 / 143 passed, typecheck clean |
| Desk suite (release gate) | **197 / 197 passed** (12 files). New in this gate: every registered route returns 403 direct and 401 proxied without a session (only `/api/health` is public); the health payload is exactly `{ok:true}`; storage-isolation guard; backup retention (17 nightly → 14 kept) |
| Typecheck (`tsc --noEmit`) | clean |
| Lint (`eslint . --max-warnings=0`, newly added) | clean (3 errors and 1 warning found and fixed) |
| Frontend production build + bundle safety check | built; `check-dist`: no localhost, no direct Railway/Telegram URL, no server variable names, no token-shaped strings |
| Server production bundle (`npm run build:server`) | built (`dist-server/main.cjs`) |
| `npm audit` (all deps) | 0 vulnerabilities (typescript-eslint pinned to a patched version) |
| Production bundle refuses unsafe config | verified: exits 1 listing the missing variables |
| **Crash/restart persistence, real process** | production bundle with file storage: wrote 3 trades, an alert and settings; `kill -9`; restarted; all present; no temp files left; scheduler heartbeat healthy after restart. Re-run in the release gate: 3 trades sent with the proxy headers survived `kill -9`; 0 temp files |
| Storage isolation guard (new) | the production bundle exits 1 if the data directory is a mount root, or if its parent holds engine data (`persistence`, `research_dataset`, `reference`, `historical_replay`) |
| Manual bundle inspection | `vite.config.ts` has no `define`/`loadEnv`; the only `import.meta.env` use is `PROD`. `dist/` holds 13 files with no server variable name, Railway/Telegram URL or PBKDF2 code. The only external URLs are W3C namespaces, React's error decoder and the chart library's attribution link. The service worker never handles `/api/` |
| Direct access without proxy secret | 403 (real process and tests); `/api/health` 200 |
| Session cookie behind TLS-terminating proxy | `__Host-desk_session; Path=/; HttpOnly; SameSite=Strict; Secure` from an http backend (real process) |
| **Browser E2E on the Netlify Edge runtime (Deno 2.9.6)**: the unmodified `netlify/edge-functions/api-proxy.ts` served `/api/*` over HTTPS in front of the production Node adapter, driven by Chromium | wrong-password message shown; `__Host-desk_session` is Secure, HttpOnly, SameSite=Strict, host-only and unreadable from JS; trade survives an upstream restart; `text/plain` POST → 415; foreign `Origin` → 403; missing `Origin` → 403; `Sec-Fetch-Site: cross-site` → 403; a client-forged proxy key is overwritten (→ 401); no `Access-Control-Allow-Origin` header ever; logout clears the cookie, then API → 401. Only console errors: service-worker fetches rejected by the self-signed test certificate. |
| Concurrent writes | 25 in-process, 15 through the proxy, no loss |
| Scheduler | window and interval rules; two instances tick together → one run; pre-market brief caught up once after a restart inside the window, not outside it; nightly backup once per IST day with catch-up; heartbeat-based health |
| Telegram | 429 short pause retried once; long pause → `RATE_LIMITED` with `retry_after`; 401/400 mapped; network/timeout; **token never in any error**; malformed token rejected before any call; owner test endpoint throttled to 1 per 30 s; no message sent during setup |
| Alert delivery | outbox before send; failed send retried after back-off; two overlapping retry passes send once; max 3 attempts; nothing sent when unconfigured |
| Stale data | stale price/VIX/history/ban never fire; quote with no exchange timestamp treated as stale; provider hang → `TIMEOUT` |
| Backup / restore | snapshot verify (no writes); restore is reversible (pre-restore snapshot); corrupted snapshot rejected; path traversal rejected |
| **Existing TIRE engine regression** | Setup, Python 3.13.16:<br>• clone at `49166ac`;<br>• the owner's 11 uncommitted files overlaid, byte-identical to the owner's copies;<br>• the owner's own git-ignored fixtures copied from the owner's machine (`RELIANCE_M15.csv`, `context/NIFTY50_M15.csv`, `nse_trading_holidays.txt`); none fabricated.<br>Result: **2398 passed, 3 skipped, 0 failed**, identical on two runs. The 9 earlier failures were environment-only (missing fixtures). The 3 skips need large local stores that were not copied: audit journal, option-chain store, instrument master. |
| Live Netlify → Railway | **BLOCKED**: not deployed (no credentials; deployment not authorised) |
| Live Telegram delivery | **BLOCKED**: no bot configured; by design no real message is sent without the owner |
| Market providers from Railway | **BLOCKED**: sandbox egress blocks Yahoo/NSE/Railway; use Settings → Run self-test after deploy |
| Netlify Edge (Deno) runtime | real module executed under Deno 2.9.6 locally (above). Netlify's own platform still needs the live check: env scope, `/api/*` routing, limits (40 s response-header limit; the proxy times out at 30 s) |
| Railway volume semantics | simulated with a local filesystem (same fsync/rename path); not tested on a Railway volume |

## 3. Security findings

| # | Finding | Status |
|---|---|---|
| S1 | **TIRE engine API is unauthenticated and public** (its own H-1). Anyone can call `/api/analyze`, `/api/research/jobs/discover` (spends the Upstox quota), create/delete research watches and write personal-journal entries. | Open, **not changed** (engine out of scope; has uncommitted work). Recommend auth or a private-network-only engine before relying on its data. |
| S2 | Engine `/api/health` discloses `data_root` and token presence | Low; open |
| S3 | Engine packaging: in `pyproject.toml` the `dependencies = [...]` list sits under `[tool.setuptools.package-data]`, so `pip install .` (the Nixpacks install step) installs **no runtime dependencies**. The live engine is healthy, so its current build gets them some other way; not verified. A future rebuild could fail. | Open, not changed; flagged |
| S4 | Desk cross-origin auth would have failed (cookie `Secure` lost behind proxy, CSRF origin mismatch) | Fixed |
| S5 | Desk login lockout keyed on a spoofable header | Fixed (proxy-attested IP only) |
| S6 | Desk API reachable directly | Fixed (proxy secret required; health public) |
| S7 | Secrets: `.env` exists locally (not read), git-ignored and never committed (checked in index and full history). Desk bundle checked for secrets on every build. | OK |
| S9 | Docs/config drift found in the release gate: `npm run hash-password` told the owner to put `APP_PASSWORD_HASH` and `SESSION_SECRET` on **Netlify**, and three code comments still described Netlify Functions/Blobs | Fixed (now points to Railway; comments corrected) |
| S10 | Deploy previews, branch deploys and custom domains are separate origins, so writes from them are refused unless each is listed in `DESK_ALLOWED_ORIGINS` | By design; documented in the checklist |
| S11 | No Desk alerts during the Muhurat session (Sun 2026-11-08), because alert jobs run weekdays 08:30–16:30 IST only | Known gap; documented |
| S8 | Residual Desk risks: single password with no 2FA; sessions revoked only by rotating `SESSION_SECRET`; CSP allows inline styles; one possible duplicate Telegram message if the process dies mid-send | Accepted, documented |

## 4. Persistence and restart

- Desk data lives on its own Railway volume under `/data/desk` (separate service and volume). The engine's `/data` is not touched.
- Writes are atomic and durable (temp file, fsync, rename, directory fsync). All read-modify-writes use compare-and-set. A Railway volume runs one instance, so there is one writer.
- Every user record type survives a restart. Verified for journal, watchlist, alerts and alert history, holdings import and settings (tests), and with a real `kill -9` (trades, alerts, settings).
- In-memory only, rebuilt on start: the hot quote cache.
- Backups: nightly snapshot (14 kept), pre-import/restore snapshot (5 kept), verify and restore endpoints, plus manual JSON download. Rollback steps: `RAILWAY_INTEGRATION.md` §5.

## 5. Remaining blockers (owner actions, then live verification)

1. Create the `tire-desk-api` Railway service with a volume at `/data` and the variables in `RAILWAY_INTEGRATION.md` §2. Deploy and confirm `/api/health` and a 403 on other paths.
2. Create the Netlify site (base `desk`) with `DESK_API_ORIGIN` and `DESK_PROXY_SECRET`, then set `DESK_PUBLIC_ORIGIN` on Railway to the final site origin.
3. Sign in on the deployed site. Run the self-test and record which providers work from Railway. Add the Upstox Analytics token if the option chain fails.
4. Configure Telegram (`TELEGRAM_SETUP.md`) and send the owner test message.
5. Take a snapshot, verify it, download a backup. Then redeploy the Railway service once and confirm the data is still there (the live restart check).
6. Commit or park the uncommitted engine work separately from `desk/` before any engine redeploy. Its suite passed in the release gate.
7. Optional: set engine watch paths so Desk pushes do not restart the engine.

Until steps 1–5 are done and pass, do not treat the integration as live.
