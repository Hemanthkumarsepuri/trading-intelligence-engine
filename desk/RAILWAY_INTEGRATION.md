# TIRE Desk on Netlify + Railway

This is the deployment design for TIRE Desk: the frontend on Netlify, the backend (API, storage, scheduler, Telegram) on Railway, and the existing TIRE research engine left exactly as it is.

## 1. Architecture

```
 Browser ── https://<desk-site>.netlify.app ─────────────────────────────┐
   │  static app (dist/)                                                  │
   │  /api/*  ──► Netlify Edge Function api-proxy (transport only)        │
   │              adds x-desk-proxy-key + x-desk-client-ip                │
   ▼                                                                      │
 Railway project                                                          │
 ├─ service "api" (EXISTING TIRE research engine, Python/FastAPI) ◄── unchanged
 │    volume mounted at /data  (research JSONL, reference data)           │
 │    https://api-production-983e.up.railway.app                          │
 │                                                                        │
 └─ service "tire-desk-api" (NEW, Node, root directory /desk) ◄───────────┘
      volume mounted at /data, Desk data under /data/desk
      API + Telegram + the only scheduler (alerts, digests, backups)
      optional read-only health probe of the engine (TIRE_ENGINE_URL)
```

### Why a separate Railway service instead of adding routes to the engine

Checked against the real repository (`phase-3-historical-validation` @ `49166ac`):

- The engine has **no authentication on any route**. That is its own audit finding H-1. Desk data (journal, holdings, alert rules) cannot be served from an unauthenticated service.
- Adding auth to the engine means editing `app/api/main.py`, which has **uncommitted work in progress**: the C-1 knowledge-time changes in 9 modified files plus 2 untracked ones. Touching it risks that work and the engine's evidence rules.
- The Desk's business logic is TypeScript (charges, tax, journal, alerts) with 190+ tests. Porting it to Python would duplicate the logic in two languages.
- A Railway volume attaches to exactly one service. A separate service with its own volume keeps the engine's `/data` untouched by construction.

Result: **no engine file was modified** and the engine's endpoints remain backward compatible. Desk data lives in its own volume, under its own `/data/desk` namespace.

### Why a same-origin proxy instead of calling Railway from the browser

- A cookie set by `*.up.railway.app` is a third-party cookie on `*.netlify.app`. Safari and Firefox block or partition those, so a cross-origin cookie login would fail for real users.
- Through the edge proxy the browser only ever talks to the Netlify origin:
  - The session cookie stays `__Host-`, `Secure`, `HttpOnly`, `SameSite=Strict`.
  - CSP `connect-src 'self'` stays strict.
  - No CORS is needed, and none is enabled (see §4).
- Railway refuses every request that does not carry the shared `DESK_PROXY_SECRET`, except `/api/health`. The API is therefore not directly usable from the internet. The visitor IP the proxy reports is trustworthy, which the login lockout relies on.

## 2. Environment variables

### Netlify site (Site configuration → Environment variables; scope must include **Functions**)

| Name | Value |
|---|---|
| `DESK_API_ORIGIN` | https origin of the Railway Desk service, e.g. `https://tire-desk-api-production.up.railway.app` (no path) |
| `DESK_PROXY_SECRET` | same value as on Railway |

Edge functions read only variables with the **Functions** scope, never variables declared in `netlify.toml`. A changed value takes effect only after a new Netlify deploy.

Nothing else goes on Netlify. That rules out Telegram, Upstox, session or password values. The build fails if the bundle contains a localhost URL, a Railway or Telegram URL, a server variable name or a token-shaped string (`scripts/check-dist.mjs`).

### Railway service `tire-desk-api`

| Name | Required | Value |
|---|---|---|
| `SESSION_SECRET` | yes | `openssl rand -hex 32` |
| `APP_PASSWORD_HASH` | yes | output of `npm run hash-password` |
| `DESK_DATA_DIR` | yes | `/data/desk` |
| `DESK_PUBLIC_ORIGIN` | yes | the Netlify site origin, e.g. `https://tire-desk.netlify.app` |
| `DESK_PROXY_SECRET` | yes | `openssl rand -hex 24` (48 chars), same as Netlify |
| `UPSTOX_ANALYTICS_TOKEN` | recommended | read-only Upstox Analytics token |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | for alerts | see `TELEGRAM_SETUP.md` |
| `TIRE_ENGINE_URL` | optional | engine origin for the read-only status probe, e.g. `https://api-production-983e.up.railway.app` (or its `*.railway.internal` private address) |
| `DESK_ALLOWED_ORIGINS` | optional | extra exact https origins allowed to write, comma-separated. Needed for a custom domain plus the `.netlify.app` name, or for a deploy-preview origin you want to use. Otherwise writes from that origin get 403 |
| `DESK_SCHEDULER` | optional | defaults to `on` on Railway. Keep it `on` here and nowhere else |

Railway injects `PORT` and `RAILWAY_ENVIRONMENT_NAME`. The latter switches on production hardening even without `NODE_ENV`.

**Refused at startup** (the deploy fails its health check and the previous deployment keeps serving):
- `DESK_OFFLINE=1`
- memory storage
- a relative `DESK_DATA_DIR`
- a non-https or missing `DESK_PUBLIC_ORIGIN`
- a missing or short `DESK_PROXY_SECRET`
- a `*`, http or localhost allowed origin
- a volume that is not writable
- a data directory that is a mount root, or whose parent holds TIRE engine data (`persistence/`, `research_dataset/`, `reference/`, `historical_replay/`). This means the volume is the engine's.

## 3. Railway service settings

| Setting | Value |
|---|---|
| Source | the same GitHub repo and branch as the engine |
| Root directory | `/desk` |
| Config-as-code file | `/desk/railway.json` |
| Build command | `npm run build:server` (esbuild bundle → `dist-server/main.cjs`) |
| Start command | `node dist-server/main.cjs` |
| Health check | `GET /api/health`, timeout 60 s |
| Restart policy | on failure, 10 retries |
| Volume | new volume for this service, mount path `/data` |
| Replicas | 1 (a service with a volume runs one instance; that is what makes the file store single-writer) |
| Watch paths | `/desk/**` (in `railway.json`) |
| Public networking | generate a domain (the Netlify proxy needs an https URL) |

The **engine service** should get watch paths that exclude `/desk/**` (for example `/app/**`, `/pyproject.toml`, `/requirements.txt`, `/nixpacks.toml`, `/railway.toml`, `/Procfile`). Without that, pushing Desk changes also redeploys and restarts the engine. This is a recommendation only; nothing in the engine was changed.

## 4. API routes, auth, CORS and CSRF

All routes are under `/api` and handled by `server/router.ts`.

- **Public:** `GET /api/health`, `GET /api/auth/me`, `POST /api/auth/login`, `POST /api/auth/logout`.
- **Everything else requires the session cookie:**
  - market: overview, sectors, flows, ban, ipos, news, symbols, options
  - watchlist, alerts, `alerts-run`
  - journal, trades, risk, sizing
  - portfolio, holdings, `holdings-import`
  - settings
  - status, `telegram/status`, `telegram/test`
  - `digest/:kind`
  - export, import, `backup/run`, `backup/verify`, `backup/restore`

Request-time rules, in order:

1. **Proxy secret.** Without a valid `x-desk-proxy-key` → 403 `DIRECT_ACCESS` (except `/api/health`). Client-sent `x-desk-client-ip` and `x-desk-proxy-key` headers are stripped before routing.
2. **Session.** HMAC-signed 14-day cookie, PBKDF2-SHA256 (600k iterations) password. Lockout: 5 failures per IP in 15 minutes, keyed on the proxy-reported IP.
3. **CSRF on writes.** `Origin` must exactly match `DESK_PUBLIC_ORIGIN` or an entry of `DESK_ALLOWED_ORIGINS`. It is mandatory in production. `Sec-Fetch-Site` must be same-origin, the body must be JSON, and body sizes are limited per route.
4. **CORS: none.** The API sends no `Access-Control-Allow-*` headers, so browsers block any cross-origin read. With the same-origin proxy none is needed; enabling CORS would only widen the attack surface.

**Reused engine endpoint:** `GET /api/health` (read-only, server-to-server, only when `TIRE_ENGINE_URL` is set). It is shown on the Desk status page. The Desk calls no other engine route, so it cannot bypass the engine's evidence or safety rules, and it never presents research states or signals.

## 5. Storage, restart behaviour, backups

- **Store.** `FileKV`, one JSON file per key under `/data/desk`. Keys are URL-encoded, so `backup/daily-2026-10-13` becomes `backup%2Fdaily-2026-10-13.json`.
- **Writes.** Each write is a temp file, then fsync, then an atomic rename, then a directory fsync. A crash leaves the old or the new value, never a torn file.
- **Concurrency.** Every read-modify-write is a compare-and-set with retry, plus in-process serialisation.
- **Restart test.** Verified with the production bundle: 3 trades, an alert and settings were written, the process was killed with `kill -9`, and everything was present after restart. The automated suite repeats this through the proxy.
- **Not persisted (by design).** The quote cache's in-memory layer; it rebuilds from the on-disk cache.
- **Nightly snapshot.** At or after 02:00 IST, `backup/daily-<IST date>`. The last 14 are kept, and missed runs are caught up after downtime.
- **Before any import or restore,** the current data is snapshotted to `backup/pre-import-<time>` (the last 5 are kept).
- **Verify a snapshot** without writing: Settings → Server snapshots → Verify (`GET /api/backup/verify?key=…`).
- **Restore** with `POST /api/backup/restore {key, confirm:true}`. It is reversible, because the pre-restore state is itself snapshotted first.
- **Off-platform copy:** Settings → Download backup (.json). Keep one outside Railway.

### Rollback

| What went wrong | What to do |
|---|---|
| Bad Desk code deploy | Railway → `tire-desk-api` → Deployments → previous deployment → Redeploy. Data is untouched; the volume is not part of the deploy. |
| Bad data | Settings → Server snapshots → Restore the last good snapshot. Or POST your downloaded JSON via Settings → Restore from file. |
| Abandon the integration | Remove `DESK_API_ORIGIN` on Netlify; the proxy then answers 503. Delete the `tire-desk-api` service only after downloading a backup. The engine is unaffected either way. |

### Migrating data from an earlier Netlify-only Desk deploy (if one exists)

That version stored data in Netlify Blobs. Before switching that site to this build:

1. On the old deployment, Settings → Download backup.
2. After the new deployment is live, Settings → Restore from file.

No automatic migration exists, and none was run.

## 6. Scheduler ownership

There is exactly **one** scheduler: `server/scheduler.ts` inside the Railway `tire-desk-api` process. The Netlify scheduled functions were deleted. The engine's own outcome sweep is separate and untouched.

| Job | When (IST) | Restart catch-up |
|---|---|---|
| Alert checks | every 5 min, 08:30–16:30 weekdays; live-price alerts only while the market is open (exchange calendar) | next tick |
| Pre-market brief | 08:45–09:14 weekdays, once per day | inside the window only |
| End-of-day summary | 17:00–20:00 weekdays, once per day | inside the window only |
| Backup | at or after 02:00 daily, once per day | yes, any time later that day |

- Job state, heartbeat and last results live in the store (`data/scheduler-state`).
- Every job start is claimed with a compare-and-set, so overlapping ticks or two instances cannot double-run a job.
- If the heartbeat is older than 3 minutes, the status page reports the scheduler as unhealthy.
- Known gap: jobs run on weekdays only, so the Muhurat trading session (Sunday 2026-11-08, evening) gets no alert checks or digests.

## 7. Deploy steps (owner, in order)

1. Delete `desk/netlify/functions/` and `desk/server/runtime.ts` (inert tombstones). Commit `desk/` on a branch and push:
   - stage it with `git add desk/` only, so the uncommitted engine work stays out of the commit;
   - do not commit `.env`.

   The full gated list is in `DEPLOYMENT_CHECKLIST.md`.
2. Railway → the existing project → **New service** → GitHub repo. Then configure it:
   - Root directory `/desk`, config file `/desk/railway.json`.
   - Add a **Volume** mounted at `/data`.
   - Set the Railway variables from §2 and generate a public domain.
3. Deploy. Check `https://<desk-api-domain>/api/health` returns `{"ok":true,…}`. Any other path without the proxy secret must return 403.
4. Netlify → new site from the same repo:
   - Base directory `desk`; build command and publish dir come from `desk/netlify.toml`.
   - Set `DESK_API_ORIGIN` and `DESK_PROXY_SECRET` with the Functions scope.
   - Deploy.
5. Set `DESK_PUBLIC_ORIGIN` on Railway to the final Netlify https origin (custom domain, if any) and redeploy the Railway service.
6. Open the site and sign in. Then Settings → Run self-test, and Telegram → Send test message (owner-initiated).
7. Settings → Snapshot now, then Verify on that snapshot. Download a backup.
8. Optional: set the engine service's watch paths as in §3.

None of these steps was performed by the assistant. The integration is **not live** until these steps are done and verified against the deployed services.
