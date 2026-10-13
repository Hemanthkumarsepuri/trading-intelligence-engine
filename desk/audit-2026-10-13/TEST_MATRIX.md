# TIRE Desk — Test Matrix (2026-10-13)

All results are local (vitest, in-process, isolated temp storage or `MemoryKV`). Real-browser E2E: NOT RUN for every row.

| Area | Implementation | Tests | Result | Gap |
|---|---|---|---|---|
| Auth: login, wrong password, lockout | `server/auth.ts`, `router.ts` | `auth.test.ts`, `api.test.ts`, `session-security.test.ts` | PASS | no real-browser cookie test |
| Session revocation (logout) | `auth.ts` `revokeSession` / `authenticate` / `pruneRevocations` | `session-security.test.ts` (10 tests incl. pruning) | PASS | one KV read per authenticated request |
| Lockout concurrency | `reserveAttempt` + keyed mutex | `session-security.test.ts` (6 tests incl. 25-way and HTTP 12-way) | PASS | exact within one process; CAS only across processes |
| Proxy trust boundary (direct access, spoofed IP) | `node-http.ts` | `railway.test.ts` | PASS | Netlify runtime itself: NOT RUN |
| Edge proxy (header allow-list, https-only upstream, unreachable) | `netlify/edge-functions/api-proxy.ts` | `railway.test.ts` | PASS | not exercised on Netlify |
| CSRF, origin, content-type, body size, 404/405 | `router.ts` | `api.test.ts`, `railway.test.ts` | PASS | |
| Candle volume semantics | `knownVolume`, `analyze`, providers | `volume-semantics.test.ts` (18 tests) | PASS | |
| Volume-spike alert | `shared/alerts.ts` | `alerts.test.ts`, `volume-semantics.test.ts` | PASS | |
| Option chain OI/volume null vs zero | `optionchain.ts`, NSE/Upstox parsers | `optionchain.test.ts`, `volume-semantics.test.ts` | PASS | |
| NSE participant OI / breadth integrity | `providers/nse.ts` | `provider-integrity.test.ts` (6, incl. error-body non-disclosure) | PASS | live NSE format drift: NOT RUN |
| Alerts: fresh-only, CAS claim, no double send | `alertsRunner.ts` | `api.test.ts` | PASS | claim-before-save crash window (see blockers) |
| Telegram retry/outbox/idempotency (mocked) | `notify.ts`, `providers/telegram.ts` | `railway.test.ts`, `api.test.ts` | PASS | real delivery: NOT RUN |
| Scheduler ownership, catch-up, overlap | `scheduler.ts` | `railway.test.ts` | PASS | two live deployments overlapping during a redeploy: NOT RUN |
| Backup, restore, verify | `services/backup.ts` | `api.test.ts`, `railway.test.ts` | PASS | same-volume only; no restore drill on Railway |
| FileKV concurrency (Windows) | `kv.ts` | `kv.test.ts` (12) | PASS | Linux/Railway filesystem: NOT RUN |
| Journal, portfolio, tax, charges | `shared/*` | `journal`, `portfolio`, `tax`, `charges`, `india` tests | PASS | independence of expected values not re-audited this sprint |
| Frontend pages / components | `src/**` | none (type-check, lint, build, `check-dist` only) | NOT RUN | no component or E2E tests |
| Coverage percentage | — | no coverage tooling configured | NOT RUN | |
