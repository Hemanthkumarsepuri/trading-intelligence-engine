# TIRE Desk — Deployment Runbook (staging first)

Nothing here has been executed. It is derived from `netlify.toml`, `railway.json`, `server/config.ts`, `server/main.ts` and `.env.example`. Do not paste secret values into chat or tickets.

## Architecture
Browser → Netlify (static `dist/` + Edge Function on `/api/*`) → Railway Desk API (`node dist-server/main.cjs`) → file KV on the Railway volume at `/data/desk`. The Railway service is the only backend and the only scheduler. The TIRE engine is a separate service and volume.

## Environment variables (from source)
| Variable | Where | Required | Purpose / validation | If missing or bad |
|---|---|---|---|---|
| `SESSION_SECRET` | Railway | yes | ≥32 chars, signs sessions | login refused (503 NOT_CONFIGURED) |
| `APP_PASSWORD_HASH` | Railway | yes | from `npm run hash-password` | login refused |
| `DESK_DATA_DIR` | Railway | yes | absolute, a sub-directory, e.g. `/data/desk` | server refuses to start |
| `DESK_PUBLIC_ORIGIN` | Railway | yes | https origin of the Netlify site | server refuses to start |
| `DESK_PROXY_SECRET` | Railway **and** Netlify (scope: Functions) | yes | ≥32 random chars, identical on both | Railway start refused; Netlify proxy answers 503 |
| `DESK_API_ORIGIN` | Netlify | yes | https origin of the Railway service (non-private) | proxy answers 503 PROXY_NOT_CONFIGURED |
| `DESK_ALLOWED_ORIGINS` | Railway | no | extra exact https origins | start refused if not https or localhost |
| `DESK_SCHEDULER` | Railway | no | `on`/`off`, default on in production; exactly one owner | alerts/digests/backups don't run if off |
| `UPSTOX_ANALYTICS_TOKEN` (or `UPSTOX_ACCESS_TOKEN`) | Railway | no | option chains/quotes | falls back to NSE/Yahoo, flagged |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Railway only | no | notifications | notifications disabled, failures logged |
| `TIRE_ENGINE_URL` | Railway | no | read-only health probe | probe skipped |
| `PORT`, `RAILWAY_ENVIRONMENT_NAME`, `RAILWAY_DEPLOYMENT_ID`, `NODE_ENV` | platform | n/a | production detection, port binding | |
| `APP_PASSWORD`, `DESK_OFFLINE`, `DESK_STORAGE`, `DESK_DEV_API_ORIGIN`, `DESK_REQUIRE_PROXY` | local dev only | no | refused or ignored in production | |

## Procedure (staging)
1. Create a **separate** Railway service and its own volume mounted at `/data`; set `DESK_DATA_DIR=/data/desk`. Never attach the engine's volume.
2. Set Railway variables; generate `DESK_PROXY_SECRET` and `SESSION_SECRET` fresh for staging.
3. Deploy Railway; wait for the health check (`/api/health`, 60 s timeout).
4. Create the Netlify site (base dir `desk`, build `npm run build:web`, publish `dist`); set `DESK_API_ORIGIN` and `DESK_PROXY_SECRET` with Functions scope.
5. Deploy Netlify; run the stages in `LIVE_VALIDATION_CHECKLIST.md` in order.
6. Rollback: Railway → redeploy the previous deployment; Netlify → publish the previous deploy. The volume survives; take a `/api/export` download before every deploy.

## Prerequisites before step 1
Commit the current working-tree changes (needs your approval), then confirm the local gate again on the committed tree.
