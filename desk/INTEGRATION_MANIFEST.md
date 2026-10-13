# Integration manifest: TIRE Desk release gate (2026-10-13)

Copy the **contents** of this `tire-desk/` folder into `trading-intelligence-engine/desk/`. Overwrite only inside `desk/`. Nothing in this package belongs outside `desk/`, and no engine file is included.

Compared against the `desk/` folder on your machine, which is byte-identical to the sprint-1 `Claude outputs/tire-desk.zip`.

- New files: 13
- Changed files: 39
- Tombstones (now `export {};`; delete them, see below): 6
- Unchanged: 53
- Files only in your copy: 0, so there are no edits of yours to lose

## After copying

1. Delete the obsolete files, which nothing imports:
   - `desk/netlify/functions/`, the whole folder
   - `desk/server/runtime.ts`
2. In `desk/`, run `npm ci` and then `npm run check`. Expect 197 tests passing and a clean `check-dist`.
3. Before any commit, run `git status`. The 9 modified engine files and 2 untracked engine files must stay **unstaged**. Stage with `git add desk/` only.

## New

- `DEPLOYMENT_CHECKLIST.md`
- `PRODUCTION_READINESS.md`
- `RAILWAY_INTEGRATION.md`
- `TELEGRAM_SETUP.md`
- `eslint.config.js`
- `netlify/edge-functions/api-proxy.ts`
- `railway.json`
- `scripts/check-dist.mjs`
- `server/main.ts`
- `server/node-http.ts`
- `server/scheduler.ts`
- `server/services/notify.ts`
- `tests/railway.test.ts`

## Changed

- `.env.example`
- `.gitignore`
- `DATA_SOURCES.md`
- `DEPLOY.md`
- `ISSUES_FIXED.md`
- `README.md`
- `netlify.toml`
- `package-lock.json`
- `package.json`
- `scripts/hash-password.mjs`
- `server/auth.ts`
- `server/config.ts`
- `server/dev.ts`
- `server/kv.ts`
- `server/providers/nse.ts`
- `server/providers/telegram.ts`
- `server/router.ts`
- `server/services/alertsRunner.ts`
- `server/services/backup.ts`
- `server/services/digests.ts`
- `server/services/market.ts`
- `server/services/selftest.ts`
- `server/services/userdata.ts`
- `shared/alerts.ts`
- `shared/market.ts`
- `shared/optionchain.ts`
- `shared/portfolio.ts`
- `shared/types.ts`
- `src/charts.tsx`
- `src/pages/Alerts.tsx`
- `src/pages/Login.tsx`
- `src/pages/Market.tsx`
- `src/pages/Options.tsx`
- `src/pages/Settings.tsx`
- `tests/alerts.test.ts`
- `tests/api.test.ts`
- `tests/kv.test.ts`
- `tests/optionchain.test.ts`
- `vite.config.ts`

## Tombstones (delete)

- `netlify/functions/alerts-tick.mts`
- `netlify/functions/api.mts`
- `netlify/functions/backup-daily.mts`
- `netlify/functions/digest-eod.mts`
- `netlify/functions/digest-premarket.mts`
- `server/runtime.ts`
