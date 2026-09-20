# TIRE production operations (no secrets)

Public HTTPS origin for this deployment (same-origin frontend + FastAPI):

`https://api-production-983e.up.railway.app`

Railway project: **TIRE** / service **api** / environment **production**.
Volume `tire-data` is mounted at `/data` (`TIRE_DATA_ROOT=/data`).

## Restart backend

```text
railway restart --service api --environment production
```

Or Railway dashboard → TIRE → api → Restart.

In-process discover jobs do not survive restart. JSONL on `/data` does.

## Redeploy backend

From the linked repo directory (never upload `.env`):

```text
railway up -y -c -s api
```

## Rotate Upstox token

Set the new token as a Railway service variable named `UPSTOX_ACCESS_TOKEN` only (never Netlify, never git, never frontend). Then restart/redeploy the api service. Do not print the value.

## Disable deployment

Railway dashboard → api → remove the public domain, or set `SYSTEM_HALTED=true` and restart (research routes halt; this is the product kill switch, not an order switch).

## Roll back backend

Railway dashboard → api → Deployments → Redeploy a previous successful deployment.

## Frontend

This release serves `app/api/static` from the same FastAPI process (empty `api-base`, same origin).

A split Netlify publish is prepared (`netlify.toml` + `scripts/prepare_netlify_static.py` + `TIRE_API_BASE`) but was not published because this machine has no Netlify CLI login. After `netlify login`:

1. Set Netlify env `TIRE_API_BASE=https://api-production-983e.up.railway.app`
2. Deploy publish directory `dist-netlify`
3. Set Railway `CORS_ALLOW_ORIGINS` to the Netlify HTTPS origin only (never `*`)
4. Redeploy/restart api so CORS loads

## What was actually tested (2026-09-20)

- Backend **restart** of the live SUCCESS deployment: persisted history and operator journal survived; in-memory discover job did not.
- Backend **older-image rollback** was not executed (would swap the running container; volume JSONL would remain).
- Frontend Netlify rollback cannot be tested until a Netlify deploy exists.

Netlify Deploys → publish a previous deploy. Then keep `CORS_ALLOW_ORIGINS` aligned with the live origin.
