# Deploying this frontend separately from the FastAPI backend

`index.html` is a single static file (no build step, no framework, no
`package.json`). Today it is served directly by the FastAPI app's `GET /`
route. It can also be hosted anywhere that serves static files (e.g.
Netlify, via the `netlify.toml` at the repo root) while the FastAPI
backend keeps running elsewhere unchanged.

## How the frontend finds the backend

Every API call in `index.html` goes through one helper, `apiUrl(path)`,
which reads the `<meta name="api-base" content="...">` tag at the top of
the file:

- **Local / current deployment (unchanged):** leave `content=""`. Every
  `apiUrl("/api/analyze")` call resolves to the relative path
  `/api/analyze`, which only works when this file is served by the same
  FastAPI app that owns those routes -- today's actual setup.
- **Separately-hosted frontend (e.g. Netlify):** set Netlify env
  `TIRE_API_BASE` to the backend HTTPS origin. `scripts/prepare_netlify_static.py`
  injects it into `dist-netlify` at build time. Never put tokens in that
  variable -- it is a public URL only.

## What must never go in this file

No `UPSTOX_ACCESS_TOKEN`, no broker credentials, no persistence
credentials, no secret of any kind. The backend never sends one to the
browser either (verified repeatedly this session via live credential-leak
checks on every API response) -- there is nothing to accidentally expose
here, and nothing should ever be added.

## What does NOT change

The FastAPI backend (Upstox integration, live analysis pipeline, live
checkpoint capture, JSONL audit journal persistence) is completely
unaffected by any of this. It keeps running exactly as it does today,
wherever it runs -- Netlify hosts the static file only.
