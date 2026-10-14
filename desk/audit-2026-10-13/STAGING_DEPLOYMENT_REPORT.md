# TIRE Desk — Staging Deployment Report (2026-10-13)

**Outcome (2026-10-14, release closure): STAGING COMPLETE — PRODUCTION BLOCKED.** The verified release `b5a7fd7` is published to `staging/desk-rc-2026-10-13` and is the code running on the isolated staging API. Every required staging check passed live, including a scheduled market-hours alert tick and a restore of an off-volume export into an isolated empty store (§17). **Production is blocked by:** (1) the proxy shared secret cannot be stored as a scoped, secret variable on the Netlify Free plan (L3, needs a paid plan, not purchased); (2) no independent off-volume backup is implemented (L9).

Labels: PASS (tested live, passed) · FAIL · BLOCKED · NOT TESTED · INFERRED (from config or indirect evidence). No secrets, hashes, cookies or tokens appear in this report.

## 1. What is deployed
| Item | Value |
|---|---|
| Netlify staging site | https://tire-desk-staging.netlify.app (new site `tire-desk-staging`, account `hemanthsepuri0`, Free plan) |
| Railway staging API | https://tire-desk-api-production.up.railway.app (new project `TIRE Desk Staging`, service `tire-desk-api`, environment `production`, region sfo, 1 replica, volume `tire-desk-api-volume` at `/data`) |
| API code | **`b5a7fd7`** `fix(desk): use NSE option-chain-v3 …` = `315b7f6` + 3 Desk files. Uploaded as `git archive b5a7fd7:desk` (127 files, identical to the commit; differs from the `315b7f6` tree in exactly `server/providers/nse.ts`, `server/services/options.ts`, `tests/provider-integrity.test.ts`) |
| Frontend code | `315b7f6:desk` build (Netlify deploy `6ace6305653c14f3324262a6`). `b5a7fd7` changes no frontend file, so no redeploy was needed |
| Railway deployment | **`9646d209-311a-4d3e-9f67-0e27410140c4` SUCCESS**, health-check gated on `/api/health`. Earlier: `3c0c84f8`, `fdb04ed9`, `3045095a`, `ac395c79` (crashed, §3) — all REMOVED |
| Git | **Published 2026-10-14**: remote `staging/desk-rc-2026-10-13` = `b5a7fd7c04e1bfc7c682735f2026ab0379429c38` (fast-forward from `315b7f6`, explicit refspec, read back with `git ls-remote`). `phase-3-historical-validation` = `49166ac` (unchanged). Local HEAD = `b5a7fd7` |

## 2. How it was deployed (secrets omitted)
1. Netlify site: `netlify-cli@27.12.0 sites:create --name tire-desk-staging --disable-linking`.
2. Railway, from `C:\tds-staging` (outside the user profile, linked only to the new project; every command also carried explicit project/service/environment IDs): `railway init`, `railway add --service tire-desk-api` (empty), `railway volume add -m /data`, variables via `railway variable set --stdin --skip-deploys`, `railway up` of the archived tree, `railway domain`. Health check set with the CLI's `railway api` (`serviceInstanceUpdate`).
3. Netlify variables via `netlify api createEnvVars`, then `netlify deploy --build --prod` from the archived tree.
4. Staging password (round 2): the owner-chosen staging-only password was hashed with the repo's own `scripts/hash-password.mjs` (pbkdf2-sha256, 600 000 iterations) and piped straight into `railway variable set APP_PASSWORD_HASH --stdin`. Nothing was printed; the hash format and length were checked.

Railway variables (names only): `SESSION_SECRET`, `APP_PASSWORD_HASH`, `DESK_PROXY_SECRET`, `DESK_DATA_DIR=/data/desk`, `DESK_PUBLIC_ORIGIN=https://tire-desk-staging.netlify.app`, `DESK_SCHEDULER=on`, `RAILPACK_BUILD_CMD=npm run build:server`. **Unset:** `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TIRE_ENGINE_URL`, `UPSTOX_ANALYTICS_TOKEN`, `DESK_ALLOWED_ORIGINS`, `DESK_OFFLINE`, `DESK_STORAGE`, `APP_PASSWORD`.
Railway service settings: `healthcheckPath=/api/health`, `healthcheckTimeout=60`, `restartPolicyType=ON_FAILURE`, `restartPolicyMaxRetries=10`.
Netlify variables: `DESK_API_ORIGIN`, `DESK_PROXY_SECRET` (see L3).

## 3. Deployment incidents
| # | What happened | Root cause | Resolution |
|---|---|---|---|
| D1 | First deploy `ac395c79` CRASHED: `Cannot find module '/app/dist-server/main.cjs'` | Railway did not apply `railway.json` to the CLI upload (`fileServiceManifest: {}`); Railpack ran `npm run build` (web) | `RAILPACK_BUILD_CMD=npm run build:server` (value from `railway.json`); default start `npm run start` = `node dist-server/main.cjs` |
| D2 | `railway environment edit --service-config …` → "No changes to apply" for every key | CLI 5.41.2 behaviour | Health check set through `railway api` instead (round 2); root dir/config file still unset (L1) |
| D3 | Netlify `env:set` silent no-op in a non-interactive shell; scoped variables 403 | CLI TTY behaviour; Free plan | `netlify api createEnvVars` with unscoped variables; no variable landed on the other two sites |

## 4. Build and test results (code `b5a7fd7`)
| Check | Result |
|---|---|
| `npm run typecheck` | PASS |
| `npm run lint` (`--max-warnings=0`) | PASS |
| `vitest run` (single worker) | PASS **249/249**, 15 files (244 existing + 5 new v3 regression tests) |
| `npm run build:web` + `check-dist` | PASS, 9 files clean |
| `npm run build:server` (local and on Railway) | PASS |
| Local Playwright (offline synthetic, Chromium) | PASS 31/31 |
| Changed provider run once against live NSE from the workstation | PASS: 18 NIFTY expiries, 108-row chain, spot 22721.95 |
| Live API acceptance (`live.mjs`, new password) | PASS 25/25 (phase A 20 + cleanup/logout 5) |
| Live option-chain checks (`live3.mjs`) | PASS 12/12 |
| Live round-2 checks (`live2.mjs`: login 4, expiry 4, lock 2, unlock 1, alerts 6, restore 8) | PASS 25/25 |
| Live browser smoke, Chromium 1440×900 and 390×844 | PASS 6/6, then signed-in sweep 6/6 more (`--repeat-each=3`); one earlier run FAIL, see L11 |

The 315b7f6 round (before the fix) also passed: 244/244, 31/31, 33/33 live HTTP checks, 6/6 browser smoke.

## 5. HTTP checks (live)
| Check | Result |
|---|---|
| `GET <railway>/api/health` | PASS 200 `{"ok":true}` |
| Protected endpoints on the Railway origin, with no or a fake `x-desk-proxy-key` | PASS 403 `DIRECT_ACCESS` |
| `GET <site>/` | PASS 200; CSP, `X-Frame-Options: DENY`, `X-Robots-Tag: noindex`, HSTS |
| `GET <site>/api/auth/me` | PASS 200 through the edge proxy; `configured:true, demo:false` |
| `GET <site>/api/{overview,settings,journal,export,status}` unauthenticated | PASS 401 |
| Client-supplied `x-desk-proxy-key` / `x-desk-client-ip` via the site | PASS no effect |
| Served bundle (9 files) contains proxy secret, password or a Railway origin | PASS none — **not** evidence of secure storage (L3) |
| Browser calls a `*.railway.app` host directly | PASS never |
| `/market` path | 404 by design (hash routes; `/#/market` works) |

## 6. Authentication and session (live)
| Check | Result |
|---|---|
| New owner-chosen staging password signs in (200) | **PASS** (17:53:05Z, again 18:08:19Z and in every later run) |
| Wrong password → 401 `BAD_CREDENTIALS`, no cookie | PASS |
| Previous generated password → 401 | PASS |
| Cookie `__Host-`, `HttpOnly`, `Secure`, `SameSite=Strict`, `Path=/`, no `Domain` | PASS |
| Password absent from responses, URL, web storage, `document.cookie` | PASS |
| Unauthenticated: every route shows only sign-in (both viewports) | PASS |
| Refresh keeps the session | PASS |
| Foreign `Origin` write with a valid session → 403 `BAD_ORIGIN` | PASS |
| Logout → old cookie replay 401; tampered cookie 401 | PASS (repeated after the fix deploy) |
| Session expiry: correctly signed token with past `exp` → 401, `authenticated:false`; unexpired control token → 200, then revoked | PASS |
| Lockout: 5×401, then correct password → 429 `LOCKED`, `Retry-After: 900` | PASS (twice) |
| Existing session keeps working during a lockout | PASS |
| **Lockout expiry**: lock at 17:53:12Z; correct password at +908 s → 200 | PASS |

## 7. Storage and persistence (live)
| Check | Result |
|---|---|
| Startup `storage=file dir=/data/desk scheduler=on offline=false` | PASS (every deployment, incl. `9646d209`) |
| Self-test storage "consistent, compare-and-set enforced" | PASS |
| Synthetic data + session survive `railway restart` | PASS |
| Synthetic data + session survive `railway redeploy` | PASS |
| Backups survive a code deploy (`pre-import-…` snapshot still listed after `9646d209`, `kept:2`) | PASS |
| Synthetic records removed afterwards | PASS |

## 8. Scheduler (live)
| Check | Result |
|---|---|
| One owner: 1 service, 1 service instance, `numReplicas: 1` (sfo), exactly 1 active deployment, `/api/status` `instance` bound to that deployment | PASS |
| `enabled:true, healthy:true`, heartbeat advancing, daily `backup` job `lastOk:true` | PASS |
| Alert evaluation: synthetic `RSI_ABOVE 1` on INFY → evaluated 1, fired 1 ("INFY daily RSI is 54.1, above 1."), event recorded `delivered:false` ("Telegram not configured"), `notified:0`; `once` alert did not re-fire; alert deleted | PASS |
| Scheduled (not manual) alert ticks during a live market session | PASS (2026-10-14): synthetic `PRICE_ABOVE NIFTY 1` created 03:36:40Z; the scheduler's own tick at 03:45:38Z (09:15:38 IST) fired it once ("NIFTY is at 22672.60 … traded 09:15:38 IST"), `delivered:false` ("Telegram not configured"); the 03:50Z tick did not re-fire it; probe deleted |
| Unattended jobs | PASS: nightly backup ran at 02:00 IST (`lastOkDate 2026-10-14`), pre-market digest 08:45 IST, 11 alert ticks by 09:20 IST, all `lastOk:true` |

## 9. Providers and data honesty (live, Railway sfo)
| Provider | Result |
|---|---|
| Storage, Yahoo, NSE archives, NSE live API (FII/DII, breadth), News | ok (1 news feed failed, reported) |
| **NSE option chain** | **PASS after fix `b5a7fd7`**: NIFTY 18 expiries, BANKNIFTY 6; chains `source: nse`, not stale, no notes; spot 22721.95 / 55481.05; every value a finite number or `null` |
| Upstox, Telegram, TIRE engine | not configured (by decision) |

Root cause of the earlier failure: NSE retired `/api/option-chain-indices` (404 HTML even from a residential IP with valid cookies). Fix: expiries from `/api/option-chain-contract-info`, chain from `/api/option-chain-v3?type=Indices&symbol=…&expiry=DD-Mon-YYYY`; the parser accepts the v3 row expiry field (`expiryDates`) and `buyPrice1`/`sellPrice1` as bid/ask, keeping the legacy fields. No trading logic, analytics or credential changed. Honest failure kept: a 404/HTML answer is a `ProviderError` (unit test); an expiry with no rows is `MALFORMED`; live, an unlisted expiry → 400 `BAD_EXPIRY`, a malformed expiry → 400, an unknown underlying → 404, and no upstream HTML reaches the client. IV of 0 (expiry day) stays unknown (`-`), an observed zero bid stays 0 (existing semantics).

## 10. Backup and recovery (live, synthetic data)
| Check | Result |
|---|---|
| `POST /api/backup/run` → `backup/daily-2026-10-13` | PASS |
| `GET /api/backup/verify?key=…` → `ok:true`, counts match | PASS |
| Restore without `confirm:true` → 400 | PASS |
| Restore drill: probe A written → backup → A deleted, B added → `POST /api/backup/restore {key, confirm:true}` → A back, B gone | PASS |
| Restore first takes a safety snapshot (`backup/pre-import-2026-10-13T17-53-23-383Z`) | PASS |
| `GET /api/export` contains the data, no secrets; one synthetic export stored off-Railway (`C:\tds-staging\live\export-phaseA.json`) | PASS |
| Recovery into an isolated empty store from the off-volume export | PASS (2026-10-14, §17): released `b5a7fd7` server started on a new empty data directory (local, offline, isolated); corrupt backup rejected 422 with nothing changed; import of the staging export → trades, holdings, watchlist, alerts and settings byte-identical to the backup; restored trade computed by the journal; target deleted |
| Restore onto a new Railway volume | NOT TESTED (not created: avoids extra resources/cost; the import path and file store are the same code) |
| Independent (off-volume) backups | **NOT IMPLEMENTED** — scheduled backups share the data volume; the only off-volume copy is a manual `GET /api/export` download (L9) |
| Auth state in backups | Export carries no session-revocation or lockout state (verified: 0 auth entries in the restored store). After a volume loss, previously revoked but unexpired sessions would be accepted again unless `SESSION_SECRET` is rotated — **rotate `SESSION_SECRET` as part of any recovery** |

## 11. Test-side corrections (not app defects)
Backup verify needs `?key=`; portfolio view omits notes (persistence checked via export); Options heading is "Option chain"; a time-of-day assumption in one local Playwright test (fixed earlier).

## 12. Telegram and production safety
- Telegram unset; self-test "Not configured"; alert event shows "Telegram not configured"; `/api/telegram/test` never called. PASS (INFERRED nothing can be sent).
- Railway `TIRE`: never linked, deployed, restarted or edited. Read-only check at the end: `api` latest deployment `82b446bf` (2026-09-22) SUCCESS; volume `tire-data` at `/data`.
- Netlify `tire-research-terminal` (published 2026-09-21) and `alankrithabeautycreations` (2026-07-19) unchanged, no variables.
- No production credentials, data, or engine URL used. Only push: `b5a7fd7` to `staging/desk-rc-2026-10-13` on 2026-10-14 (§1, §17); the engine branch was not pushed.

## 13. Visual check
Screenshots outside the repo: `C:\tds-staging\live\screens\` (desktop and 390px for every route, plus `*-options-after-fix.png`). Option chain renders spot, PCR, max pain, implied move, OI chart and table on both widths; all 8 tabs fit at 390px; no horizontal overflow. Chromium only — Firefox/WebKit NOT TESTED.

## 14. Limitations and open items
| ID | Sev | Item | Action |
|---|---|---|---|
| **L3** | **P1 — production gate (BLOCKED, needs a purchase decision); accepted for staging** | **Netlify Free plan cannot protect the proxy secret.** Netlify docs: variable scopes are "available on Pro and Enterprise plans", and secret values "cannot have the post processing scope" and "must be set to explicit deploy contexts and scopes". On Free every variable is forced into all scopes incl. post-processing, so the secret flag is impossible (observed: scope → 403, secret → 422). `DESK_PROXY_SECRET` is therefore a plain variable readable by the account owner in settings/API and available to Netlify-run builds. Absence from the served bundle does **not** prove secure storage. Impact if leaked (from code): direct calls to the Railway API become possible and the `x-desk-client-ip` header can be forged, defeating the per-IP login lockout (a brute-force throttle bypass); all data endpoints still require a valid session. No alternative removes the need for a credential stored on Netlify, so no code change was made (a global lockout would add a denial-of-service risk) | **Required before production:** upgrade the Netlify team to Pro (paid — not done without owner approval), then recreate `DESK_PROXY_SECRET` with scope = Functions, contexts = production/deploy-preview/branch-deploy, secret = on; rotate it on Railway (`railway variable set --stdin`) and Netlify in one sequence; re-run proxy/direct-access checks. Current staging mitigations: staging-only value, single-member team, no Netlify-side builds or plugins (local CLI builds), rotate on any account change |
| L1 | P2 | Railway service is not GitHub-connected (CLI upload of `b5a7fd7:desk`); `railway.json` is not applied (build via `RAILPACK_BUILD_CMD`, health check set directly). Publishing the branch does not deploy | To update: re-archive a reviewed commit and `railway up`. Or dashboard: connect repo/branch, Root Directory `/desk`, Config file `/desk/railway.json`, then remove `RAILPACK_BUILD_CMD` |
| ~~L12~~ | — | Deployed commit not on GitHub: **RESOLVED** — `b5a7fd7` published 2026-10-14 | — |
| L5 | P3 | Netlify injects `/.netlify/scripts/hud`; the app's CSP blocks its inline code (console error only) | Optionally disable the Netlify drawer/HUD |
| L6 | P3 | HSTS `max-age=31536000` on `netlify.app` (config 63072000), platform-controlled | None for staging |
| L7 | P3 | Netlify site deployed by CLI, not linked to GitHub | Link if continuous deploys are wanted |
| L10 | P3 | Fired-alert events retry delivery while Telegram is unset ("Telegram not configured") | Expected while Telegram is disabled |
| L11 | P3 | One desktop browser run logged two `401` resource errors in the console; not reproduced in 7 further signed-in runs (all instrumented: no 401 while signed in or after sign-out). Cause unknown | Watch for recurrence |
| L8 | — | Firefox/WebKit; restore onto a new Railway volume | NOT TESTED (scheduled market-hours tick and empty-store restore now PASS) |
| **L9** | **P1 — production gate** | **No independent disaster recovery.** Scheduled backups live on the same Railway volume as the data; the only off-volume copy is a manual `/api/export` download. Recovery from that export into an empty store is proven (§10) | Before production: either schedule an automated off-volume copy of the export to separate storage (not implemented in Desk today), or adopt a documented manual procedure (download `/api/export` before every deploy and on a fixed schedule, store it off Railway) and accept the recovery-point gap explicitly. Include `SESSION_SECRET` rotation in the recovery runbook |
| ~~L2~~ | — | Health check: RESOLVED (`/api/health`, 60 s; deployments `3c0c84f8` and `9646d209` recorded it) | — |
| ~~L4~~ | — | Option chains: RESOLVED by `b5a7fd7` | — |

## 15. Rollback
- Railway API: `TIRE Desk Staging` → `tire-desk-api` → Deployments. The previous code (`315b7f6`) is not kept as an active deployment; redeploy it with `railway up` from `C:\tds-staging\release` (archive of `315b7f6:desk`) and the same `-p/-s/-e` IDs. Volume untouched. Git: the branch can be moved back only with a normal new commit (e.g. `git revert b5a7fd7`), never a force-push.
- Netlify: `tire-desk-staging` → Deploys → publish a previous deploy. Cut the API link: delete `DESK_API_ORIGIN` (proxy → 503 `PROXY_NOT_CONFIGURED`).
- Emergency stop: remove the Railway service domain, or delete the `TIRE Desk Staging` project after `GET /api/export`.
- None of these touch `TIRE` or the other Netlify sites.

## 16. Secrets and local artefacts
- The generated secrets file (previous password and hash, session and proxy secrets) and the temporary new-password file were overwritten and deleted from the Claude Code session scratchpad after the new password was verified; the scratchpad `secrets/` folder is empty. Live values now exist only in the Railway and Netlify variable stores. Leftover test sessions were logged out (replay → 401) and their state files deleted.
- The new staging password is known to the owner only; it was shared in the chat that requested it, so treat it as staging-only and never reuse it.
- `C:\tds-staging\`: archived trees (`release` = `315b7f6`, `release-b5a7fd7`), deploy logs, live test scripts (no secret values), screenshots, one synthetic export.

## 17. Release closure log (2026-10-14)
All results below were observed directly on 2026-10-14 unless marked otherwise.

| # | Check | Command / method | Result |
|---|---|---|---|
| 1 | `b5a7fd7` parent = `315b7f6`; changes exactly 3 Desk files; fast-forward | `git rev-parse b5a7fd7^`, `git diff --name-status`, `git merge-base --is-ancestor` | PASS |
| 2 | Publish staging branch only | `git push origin b5a7fd7…:refs/heads/staging/desk-rc-2026-10-13` → `315b7f6..b5a7fd7`; `git ls-remote` read-back | PASS (engine branch still `49166ac`) |
| 3 | Staging deployment / health | `railway deployment list` → `9646d209` SUCCESS; `/api/health` 200; direct `/api/overview` 403; site 200; `auth/me` via proxy `configured:true, demo:false` | PASS |
| 4 | Proxy-secret storage options | Netlify docs (environment variables overview; Secrets Controller) + earlier 403/422 | BLOCKED on Free plan → L3 |
| 5 | Quality gate on the exact `b5a7fd7:desk` tree | `npm run typecheck`, `npm run lint`, `vitest run` (249/249), `build:web` + `check-dist`, `build:server`; local Playwright 31/31 | PASS |
| 6 | Live API acceptance (auth, proxy, CSRF, cookies, writes, backup, export) | `live.mjs A` 20/20, `LOGOUT` 5/5 (cleanup, revocation, tampered cookie) | PASS |
| 7 | Persistence across a controlled restart | `railway restart` (CLI returned after the restart; logs show SIGTERM and a new listen; new scheduler instance `…:35c5e709`), then `live.mjs B` 3/3 | PASS |
| 8 | Option chains with the v3 endpoints | `live3.mjs` 12/12: NIFTY 18 expiries (rolled to 2026-10-19), BANKNIFTY 6; chains `source: nse`, not stale; all values finite numbers or `null`; unlisted/malformed expiry 400, unknown underlying 404, no upstream HTML | PASS |
| 9 | Browser smoke, Chromium desktop 1440 + mobile 390 | `playwright test -c live/live.config.ts` 6/6, no 401/5xx | PASS |
| 10 | Scheduled market-hours alert tick, no delivery | `live4.mjs` (§8) | PASS |
| 11 | DR drill: off-volume export → isolated empty store | `dr.mjs STAGE/TARGET/CLEAN` 4/4, 12/12, 2/2 (§10) | PASS |
| 12 | Single scheduler owner, fresh heartbeat | `/api/status` (one instance, heartbeat 03:50:39Z), 1 replica, 1 active deployment | PASS |
| 13 | Session expiry, lockout expiry | not repeated (no code change since 2026-10-13 round 2) | PASS on 2026-10-13 (§6) — not re-run |
| 14 | Firefox/WebKit | — | NOT TESTED |
| 15 | Production untouched | `TIRE`/`api` latest `82b446bf` (2026-09-22); volume `tire-data` at `/data`; `tire-research-terminal` deploy `6ab0fe04` (2026-09-21), `alankrithabeautycreations` `6a5ca151` (2026-07-19) | PASS |

Clean-up: all synthetic records (trades, holdings, watchlist, alerts) deleted from staging; all test sessions logged out (replay → 401); the temporary password file was overwritten and deleted; the isolated DR target and the local export copy were deleted. No secrets were printed or written to the repository.

### Production gates (all must be met before any production use)
1. **L3** — Netlify plan with scoped secret variables, proxy secret recreated as Functions-only secret and rotated on both sides, proxy/direct-access checks re-run.
2. **L9** — independent off-volume backups (automated, or an explicitly accepted manual schedule), recovery runbook including `SESSION_SECRET` rotation.
3. **L1** — a reproducible deploy path (GitHub-connected service with Root Directory `/desk` and `railway.json` applied, or a documented archive-upload procedure).
4. Production secrets and password must be newly generated (never the staging values) and Telegram enabled only with explicit approval and a test chat first.
