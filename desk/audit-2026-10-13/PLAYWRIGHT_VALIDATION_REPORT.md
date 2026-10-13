# TIRE Desk — Playwright Validation Report (2026-10-13)

Status words: PASS / FAIL / BLOCKED / NOT RUN / NOT IMPLEMENTED. Everything here is **local** (offline synthetic data). No live Netlify, Railway, provider or Telegram behaviour was tested.

## 1. Starting state
Branch `phase-3-historical-validation`, HEAD `befe7b1354db0b8fc3413049c63b26b65e76fb4f`. Status at start: 9 modified engine files, 2 untracked engine files, `Claude outputs/`, untracked `desk/audit-2026-10-13/STAGING_RUNBOOK.md`. Baseline `npm run check`: exit 0, 244/244.

## 2. Startup (secrets omitted; generated per run, never printed)
Defined in `desk/e2e/playwright.config.ts`:
- API: `npx tsx server/dev.ts` with `PORT=8799 DESK_OFFLINE=1 DESK_STORAGE=file DESK_DATA_DIR=<fresh temp dir> APP_PASSWORD=<random> SESSION_SECRET=<random> DESK_SCHEDULER=off`, outbound HTTPS forced to a dead proxy (`NODE_USE_ENV_PROXY=1 HTTPS_PROXY=http://127.0.0.1:9`). No Telegram variables.
- Web: `npm run build:web` then `npx vite preview --port 4179` (production bundle; `/api` proxied to the local API).
- Run: `cd desk/e2e; npm install; npx playwright test`.

Playwright 1.63.0 lives in its own package `desk/e2e/` (own lockfile) so `desk/package.json` and `desk/package-lock.json` are unchanged. It uses the already-cached Chromium 1243.

## 3. Coverage
Chromium only. Viewports: 1440×900 (desktop), 390×844, 360×800, 320×800 (phone). 31 browser tests: 2 exploratory route sweeps (every route, both widths, console/network/overflow capture, screenshots) + 29 assertion tests.

| Area | Tests | Result |
|---|---|---|
| Unauthenticated access (all 8 routes + 5 API endpoints) | 1 | PASS |
| Wrong password, valid sign-in, refresh, cookie flags, no secrets in URL/storage/page | 2 | PASS |
| Logout + server-side revocation (cookie replay refused), tampered cookie, CSRF foreign origin | 3 | PASS |
| Lockout after 5 failures (runs last) | 1 | PASS |
| Watchlist add → quote → symbol page/chart → remove; unverifiable symbol warning; rejected symbol | 3 | PASS (after fix PW-5) |
| Alerts create/list/"Check now"/delete (no Telegram, nothing delivered) | 1 | PASS |
| Journal log closed trade → charges → overview → delete | 1 | PASS (after fix PW-4) |
| Settings persist across reload; invalid value refused; self-test; snapshot | 2 | PASS |
| Portfolio CSV preview + add | 1 | PASS |
| Options switch underlying/expiry | 1 | PASS |
| Data states via API-boundary mocks: stale banner, unavailable quote not 0, provider 502 + Retry recovers, HTML error body never shown, network failure not stuck, unknown candle volume (no invented volume/ratio, chart renders), server volume sanity, session-check failure recovers | 8 | PASS (after fix PW-2) |
| Layout: tab bar fits at 320/360/390, no sideways page scroll on any route; option table scrolls to put columns; session pill shows time | 5 | PASS (after fixes PW-1, PW-3) |

**Final full-suite result (resumed pass, final working tree): 31/31 PASS** (`npx playwright test --workers=1`, Chromium, exit 0, 1.3 min). An earlier run in this pass was 30/31: the journal test failed because the *test* hard-coded an exit time of 14:00 while the form's entry time defaults to "now" (18:53), so the app correctly refused "Exit cannot be before entry." Test-side bug (time-of-day dependent), not an app defect; fixed by pinning both entry and exit times. The earlier out-of-memory aborts are superseded by this run.

## 4. Defects found
| ID | Sev | Where | Problem | Status |
|---|---|---|---|---|
| PW-1 | P2 | all routes, phone ≤390px | Bottom tab bar 405px wide in a 390px fixed bar; "Settings" partly off-screen and unreachable at narrower widths (`flex:1` links without `min-width:0`) | FIXED (`src/theme.css`, `src/App.tsx`); regression: 3 layout tests, failed before fix |
| PW-2 | P2 | app start | If `/api/auth/me` fails (e.g. 503 AUTH_UNAVAILABLE) the app showed a dead-end "Cannot reach the server" banner with no Retry and no `role="alert"` | FIXED (`src/App.tsx`); regression test failed before fix |
| PW-3 | P3 | top bar, desktop | Session pill said "Market closes" with no time | FIXED (shows e.g. "Market closes 13 Oct, 15:30 IST"); regression test failed before fix |
| PW-4 | P3 | Journal overview | Per-group tables (by tag/emotion) said "No closed trades yet." when closed trades exist | FIXED (`src/pages/Journal.tsx`); regression in journal test, failed before fix |
| PW-5 | P2 | Watchlist add | Server accepts a symbol it cannot verify (offline, or live when the NSE list is unreachable) with a warning note; UI dropped the note and said only "X added" | FIXED (`src/pages/Watchlist.tsx`); regression test failed before fix |
| PW-6 | P3 | Trade form | Validation error shows raw field path ("exitAt: Exit price and exit time must both be set…") | OPEN |
| PW-7 | P3 | Journal charts | One-trade Daily P&L shows a "-₹484" axis label and duplicate x labels; equity curve flat | OPEN (cosmetic) |
| PW-8 | P3 | Options, phone | Put columns reachable only by sideways swipe in a nested scroller, no cue | OPEN (works; discoverability) |

Test-side mistakes found and corrected (not app bugs): strict-mode `Spot` selector; missing exit time in journal test; encoding damage from a PowerShell edit; option-table viewport check; a race in the original unknown-symbol test.

## 5. Evidence
Screenshots: `desk/e2e/evidence/explore-{desktop,mobile}-*.png` (git-ignored). Failure traces/screenshots: `desk/e2e/test-results/` (git-ignored). HTML report: `desk/e2e/playwright-report/`.

## 6. Other checks
**Before the PW-5 change:** `npm run check` (typecheck, eslint incl. e2e files, 244/244 unit tests, web build, `check-dist` 9 files clean, server build): PASS, exit 0. `npm audit`: desk 0, e2e 0 vulnerabilities.

**After the PW-5 change:** `npm run build:web` (tsc + vite build + `check-dist`): PASS, exit 0 — the built bundle used by the browser tests includes PW-5.

**Final verification on the final working tree (free RAM still ≈0.3 GB; run with reduced Node heap and one worker):**
| Step | Command | Result |
|---|---|---|
| Typecheck | `npm run typecheck` | PASS |
| Lint | `npm run lint` (`--max-warnings=0`) | PASS |
| Unit tests (vitest, mocked/offline) | `npx vitest run --no-file-parallelism --maxWorkers=1` | PASS, 15 files, 244/244 |
| Web build | `npm run build:web` (tsc + vite + check-dist, 9 files clean) | PASS |
| Server build | `npm run build:server` | PASS |
| Browser tests (local, offline synthetic data) | `cd e2e; npx playwright test --workers=1` | PASS, 31/31 |
| Whitespace | `git diff --check` | PASS (only LF→CRLF notices) |

## 7. Blocked / not run / not implemented
- Expired session in the browser: BLOCKED (server clock; covered by unit tests).
- Lockout expiry after 15 min: NOT RUN.
- Real provider data, malformed live payloads: NOT RUN (mocked at the API boundary only; parsers covered by unit tests).
- Telegram delivery: NOT RUN by design.
- Research/discovery requests, research job status/result views: NOT IMPLEMENTED in Desk (engine features).
- Market page sub-sections, symbol search, theme toggle, holdings edit/delete, Firefox/WebKit: NOT RUN.
- Live Netlify/Railway: NOT RUN.

## 8. Safety
No push, deployment, `railway up/link`, production change, Telegram message or paid API call. Engine files, `Claude outputs/` and `STAGING_RUNBOOK.md` untouched; nothing staged or committed.

## 9. Next action
PW-1..PW-5 are verified by a clean full browser run (31/31) plus typecheck, lint, 244/244 unit tests and both builds on the same tree. Open P3s: PW-6, PW-7, PW-8. No open P0/P1/P2. Decision for you: whether to commit the `desk/src/*` fixes and `desk/e2e/` (not done; nothing staged).

## 10. Changed files (uncommitted)
Modified: `desk/src/App.tsx`, `desk/src/theme.css`, `desk/src/pages/Journal.tsx`, `desk/src/pages/Watchlist.tsx`.
New: `desk/e2e/{package.json,package-lock.json,.gitignore,playwright.config.ts}`, `desk/e2e/tests/{helpers.ts,00-explore,02-auth,03-workflows,04-states,05-layout,zz-lockout}.e2e.ts`, this report.
