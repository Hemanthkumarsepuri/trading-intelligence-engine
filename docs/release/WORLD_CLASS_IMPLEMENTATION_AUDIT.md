# World-class implementation audit — observation-backed watch

Date: 2026-09-20. Production freeze of visual system and research engine remains in force. This document records **existing** paths before code lands.

## Current Watch implementation

Personal WATCH lives only in the Netlify/static dashboard:

- Key: `localStorage["TIRE_PERSONAL_WATCHLIST"]`
- Value: JSON array of **symbol strings** (`["KAYNES"]`)
- Functions: `loadPersonalWatch`, `savePersonalWatch`, `toggleWatch` in `app/api/static/index.html`
- Chip `MY WATCHLIST` overlays **latest** `screenerRows.research_state`, not T0
- Duplicate: toggle (splice/push), not a second row
- Persistence: this browser only; lost on another device / cleared storage
- Network: **none**. Does not call `POST /api/watchlist`

`POST /api/watchlist` (`app/api/main.py`, `WatchlistRequest`, `run_watchlist`) is a **compare** of up to 20 independent `/api/analyze` queries. It must **not** be reused as personal research memory.

## Observation model (reusable)

A completed analysis is `AnalyzeResponse` (`app/orchestration/dashboard_service.py`):

- Identity: `symbol`, `audit_id`, `generated_at`, `query`, `parsed_strike`, `parsed_right`, `has_specific_contract`
- Research: `research_state`, `timing_stage`, `decision`, `market_observed_at` (quote print, not `generated_at`)
- Conditions: `invalidation_condition`, `reasoning`
- `visual`: evidence rows, development (`pattern`, `what_is_missing`, `confirm_if`, `invalidate_if`), blockers (`missing_confirmation`), freshness streams, `requested_contract` assessment (strike, right, LTP, bid/ask, volume, OI, ΔOI, IV, liquidity_grade)

Screener rows are `OpportunityCardView`-shaped objects from the daily researcher (`developing_pattern`, `research_state`, `contract_usability`, `options_data_quality`, `observed_at`, confirmation/invalidation strings). Weaker than a full analyze payload but still a real scan observation.

Immutable journal: `AnalysisSnapshot` in `app/domain/audit/models.py` via `JsonlAuditJournalRepository` (`analysis_snapshots.jsonl`). Does **not** store `research_state` as a first-class field; T0 for WatchRecord should be taken from the **analyze/screener observation that the operator pinned**, not reconstructed by inventing research_state from the audit journal.

## Existing repositories (JSONL under `TIRE_DATA_ROOT`)

`app/api/main.py`:

- `_DATA_ROOT = Path(settings.tire_data_root)` (Railway `/data`)
- `_PERSISTENCE_DIR / quotes.jsonl`, `option_chains.jsonl`, `iv_observations.jsonl`
- `persistence/audit_journal/`
- `persistence/research_journal/`
- `persistence/research_outcomes/`
- `persistence/personal_journal/`
- `_REPLAY_DATASET_DIR = _DATA_ROOT / "research_dataset"`

Shared machinery: `app/persistence/jsonl_file.py` `_JsonlStore` (append-only, no in-place update). Personal journal and research runs **never delete**. Watch **remove** must be a **tombstone event**, not a rewrite of T0.

Do **not** introduce PostgreSQL for this feature.

## Reusable APIs

Keep: `/api/analyze`, `/api/research/discover`, `/api/health`, `/api/market/observed`, journal, patterns, personal journal.

Add (new, registered **before** `/api/research/{observation_id}/outcome` so `watches` is not captured as an id):

- `POST /api/research/watches`
- `GET /api/research/watches`
- `GET /api/research/watches/{id}`
- `DELETE /api/research/watches/{id}`
- `POST /api/research/watches/{id}/latest` (current observation only)
- `POST /api/research/watches/migrate`

CORS today: `GET, POST, OPTIONS` only. DELETE requires adding `DELETE` to `allow_methods` (exact-origin still; never `*`).

## Frontend state

- `screenerRows`, `lastDiscoverResult`, `state.lastResponse`
- Hash views: `market | screener | history | patterns | symbol` — Watchlist is missing
- Filter `data-filter="watch"` maps to `research_state === "WATCH"` — collision with the WATCH button
- Mobile: table hidden via CSS but WATCH buttons remain in DOM (0×0 hit-test risk)

## Pattern history

`GET /api/research/patterns?source=replay` raises `ReplayDatasetUnavailable` as HTTP **404** with “historical replay dataset not built…”. UI prefixes **PATTERN HISTORY FAILED**. Production volume typically has no replay JSONL. Honest copy is required; do not invent counts.

## Test gaps

- No WatchRecord / T0 immutability / delta / migration tests
- No API tests for personal watches
- MCP UAT covered bookmark WATCH, not observation pin
- Full F&O live-session scan still pending (Sunday closed)

## Migration

For each legacy localStorage symbol: if a current screener/analyze observation exists, create WatchRecord with that observation as T0; else create `t0_unavailable=true` (LEGACY WATCH / T0 OBSERVATION UNAVAILABLE). No duplicates. Server is authoritative after migrate.

## Safety

WATCH must not call Upstox order APIs. Existing `tests/safety/test_broker_execution_lock.py` remains the lock. Qwen must not generate T0 or deltas.
