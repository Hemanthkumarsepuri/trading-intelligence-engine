# Deploying TIRE Desk

TIRE Desk now deploys as two parts:

- **Netlify:** the static frontend and a same-origin `/api` edge proxy.
- **Railway:** the API, storage, Telegram and the only scheduler. It runs as its own service next to the existing TIRE research engine, which is not modified.

- Gated deploy steps, each marked verified locally or needing a live check: **[DEPLOYMENT_CHECKLIST.md](DEPLOYMENT_CHECKLIST.md)**
- Step-by-step deployment, environment variables, rollback: **[RAILWAY_INTEGRATION.md](RAILWAY_INTEGRATION.md)**
- Telegram bot setup and delivery guarantees: **[TELEGRAM_SETUP.md](TELEGRAM_SETUP.md)**
- What was verified, what is still blocked: **[PRODUCTION_READINESS.md](PRODUCTION_READINESS.md)**
- Data sources, timestamps and failure behaviour: **[DATA_SOURCES.md](DATA_SOURCES.md)**

Local development:

```bash
npm install
APP_PASSWORD=dev-only-pass SESSION_SECRET=$(openssl rand -hex 32) DESK_OFFLINE=1 npm run dev
# UI http://localhost:5173 -> Vite dev proxy -> API http://localhost:8788 (data in .devdata/)
npm run check   # typecheck + lint + tests + web build (with bundle safety check) + server bundle
```
