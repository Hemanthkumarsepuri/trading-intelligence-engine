# Observation Watch — release report

Date: 2026-09-20. Commit target: `feat: add observation-backed research watch`.

TIRE remains read-only. Visual system and research engine were not rewritten.

## Architecture

Personal WATCH is no longer `localStorage` ticker bookmarks.

`WatchEvent` JSONL (`TIRE_DATA_ROOT/persistence/research_watches/research_watches.jsonl`):

- `CREATED` — T0 frozen
- `LATEST_UPDATED` — current observation only
- `REMOVED` — tombstone

`JsonlWatchRecordRepository` + `ResearchWatchService` fold events into `WatchRecord`. T0 is never rewritten.

`POST /api/watchlist` is unchanged (compare analyses).

## WatchRecord schema

Identity: `watch_id`, `symbol`, `created_at`, `query`

T0 / latest `ObservationSnapshot`: observation_id, timestamps, research_state, timing_stage, pattern, direction, contract_state, freshness, evidence_groups/summary, missing_confirmation, confirmation/invalidation conditions, contract fields that AnalyzeResponse actually carries (strike, right, LTP, bid/ask, volume, OI, ΔOI, IV, liquidity).

`expiry`/`dte` only when present on the payload — never invented.

## Migration

`POST /api/research/watches/migrate` accepts legacy `TIRE_PERSONAL_WATCHLIST` symbols. If a screener observation exists, it becomes T0. Otherwise `t0_unavailable=true` (`LEGACY WATCH` / `T0 OBSERVATION UNAVAILABLE`). Duplicates skipped. Server is authoritative.

## Persistence

Same append-only JSONL as journal/outcomes. Corrupt lines skipped. Restart is a new repository instance on the same file (unit-tested). Production Railway restart is **pending deploy of this commit**.

## API

| Method | Path |
| --- | --- |
| POST | `/api/research/watches` |
| GET | `/api/research/watches` |
| GET | `/api/research/watches/{id}` |
| POST | `/api/research/watches/{id}/latest` |
| DELETE | `/api/research/watches/{id}` |
| POST | `/api/research/watches/migrate` |

CORS `allow_methods` now includes `DELETE`. Origins remain exact; never `*`.

Successful `/api/analyze` refreshes latest for an active watch of that symbol; T0 stays put. Lookahead: a print older than T0 is ignored as latest.

## T0 / delta semantics

T0 ≠ CURRENT unless fields match. Deterministic categories: `NO_MATERIAL_CHANGE`, `STATE_CHANGED`, `PATTERN_CHANGED`, `DIRECTION_CHANGED`, `CONTRACT_CHANGED`, `CONTRACT_DEGRADED`, `FRESHNESS_DEGRADED`, `EVIDENCE_CHANGED`, `CONFIRMATION_APPEARED`, `CONFLICT_APPEARED`, `EXTENDED`, `DATA_BECAME_INSUFFICIENT`, plus missing/condition text changes. No scores. No Qwen.

`INVALIDATION_CONDITION_REACHED` is reserved; the engine has no first-class INVALIDATED research_state. Text change of invalidation_condition is `CONDITION_TEXT_CHANGED`. Do not invent invalidation from elapsed time.

## UX

- Nav **Watchlist** workspace (T0 / NOW / what changed / missing / confirm / invalidate)
- Screener **WATCHED CHANGES** strip
- Button WATCH / WATCHED = pin observation
- Filter **Needs confirmation** = engine `research_state=WATCH` (unchanged backend)
- Chip shows **T0** state, not live overlay
- Symbol workspace: T0 WATCH vs CURRENT when pinned
- History lists watch T0 vs journal `generated_at` without relabelling
- Pattern replay 404 copy: `HISTORICAL REPLAY DATASET NOT AVAILABLE IN THIS DEPLOYMENT` (not FAILED)

## Mobile

At ≤720px only the card `.watch-pin` is interactive; table WATCH is disabled, `aria-hidden`, `pointer-events: none`. Desktop inverse. `stopPropagation` retained.

## Tests (this machine)

- `pytest`: 2160 passed after HTML `fetch(apiUrl(` count update; one prior fail was that count (18→22)
- `ruff check app tests`: clean
- `mypy app --strict`: 167 files, clean
- Safety lock tests: pass
- Watch unit/API: create, duplicate 409, remove, migrate, T0 immutability, no-lookahead, JSONL corruption, restart-via-reopen

## RELIANCE 1270 PE / KAYNES / BEL / ZZZXNOTREAL

Sunday 20 Sep 2026 IST — NSE **CLOSED**. Live contract resolution, chain prints, and conflict evolution **cannot be honestly claimed**. Unit tests use synthetic RELIANCE 1270 PE snapshots. Frontend refuses to pin without an observation (invalid symbol cannot mint a WatchRecord).

Full F&O live-session scan: **honestly pending**.

Production MCP UAT against Netlify: **pending deploy** of this commit (current production still has localStorage WATCH).

Railway restart of WatchRecord on `/data`: **pending deploy**.

## Security / safety

- No place/modify/cancel routes added
- WATCH does not call Upstox trading
- Frontend: no localhost / :8000 / :8931 / token strings in `index.html`
- Qwen is not on the watch path

## Remaining gaps

- Browser notifications not implemented (in-app CHANGED list only)
- Replay dataset still not on Railway `/data` (honest unavailable)
- Full F&O scan next NSE session
- Production HTTPS/CORS re-verify after deploy
- `INVALIDATION_CONDITION_REACHED` needs a real engine/outcome flag before it can display
