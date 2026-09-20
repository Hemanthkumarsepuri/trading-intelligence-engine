# Observation Watch — release report

Date: 2026-09-20 IST. Product commit: `e71afc5` (`feat: add observation-backed research watch`). Deploy hygiene: `849403a` (`chore: ignore Netlify local CLI state`).

v1 scope hardening: `b7b16f7` (`fix: separate observation scope from contract change`). Production cert: `docs/release/OBSERVATION_WATCH_V1_CERTIFICATION.md`. Railway `ba3628eb`. Netlify `6aaf9a2d98b6c0d5e748d4cb`.

TIRE remains read-only. Visual system and research engine were not rewritten.

`instrument_type` (`UNDERLYING` / `OPTION_CONTRACT` / `FUTURES_CONTRACT`) is stored on the snapshot when derivable. Option vs underlying is `OBSERVATION_SCOPE_CHANGED`, not `CONTRACT_CHANGED`. Same-option strike / right / expiry have their own categories. Analyze auto-refresh is same-instrument only. Error observations cannot mint a watch.

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

`expiry`/`dte` only when present on the payload — never invented. Production RELIANCE 1270 PE T0 had `expiry=null` and `dte=null` because AnalyzeResponse did not carry them.

## Migration

`POST /api/research/watches/migrate` accepts legacy `TIRE_PERSONAL_WATCHLIST` symbols. If a screener observation exists, it becomes T0. Otherwise `t0_unavailable=true` (`LEGACY WATCH` / `T0 OBSERVATION UNAVAILABLE`). Duplicates skipped. Server is authoritative.

## Persistence

Same append-only JSONL as journal/outcomes. Corrupt lines skipped. Production volume `tire-data` mounted at `/data`. In-place Railway restart of deployment `e98e4b44-97fb-4bfc-bb68-30b1f6ec1f76` kept WatchRecord `0b61403787004264aba8e75a76315769` with T0 observation_id unchanged.

## API

| Method | Path |
| --- | --- |
| POST | `/api/research/watches` |
| GET | `/api/research/watches` |
| GET | `/api/research/watches/{id}` |
| POST | `/api/research/watches/{id}/latest` |
| DELETE | `/api/research/watches/{id}` |
| POST | `/api/research/watches/migrate` |

`GET /api/research/watches/latest` is not a collection. Production returns **404** `watch not found` (path captured as `{watch_id}`). Expected.

CORS `allow_methods` includes `DELETE`. Origins remain exact; never `*`. Browser Origin `https://tire-research-terminal.netlify.app` succeeded for GET/POST/DELETE.

Successful `/api/analyze` refreshes latest for an active watch of that symbol; T0 stays put. Lookahead: a print older than T0 is ignored as latest.

## T0 / delta semantics

T0 ≠ CURRENT unless fields match. Deterministic categories include `NO_MATERIAL_CHANGE`, `STATE_CHANGED`, `PATTERN_CHANGED`, `DIRECTION_CHANGED`, `CONTRACT_CHANGED`, `CONTRACT_DEGRADED`, `FRESHNESS_DEGRADED`, `EVIDENCE_CHANGED`, `CONFIRMATION_APPEARED`, `CONFLICT_APPEARED`, `EXTENDED`, `DATA_BECAME_INSUFFICIENT`, plus missing/condition text changes. No scores. No Qwen.

Production RELIANCE 1270 PE:

1. After pin, T0 and latest shared observation_id `b8f0b4e40aea41ed83d0907cdcdd0367` → `NO_MATERIAL_CHANGE`.
2. Re-analyze of the same 1270 PE (closed-session last print 2026-09-18T10:28:33.894Z) created a new journal `audit_id` `0e18c5e84e1c4b229edc6e287c4d44e7`. POST latest: **T0 byte/field equivalent**, latest id changed, category still `NO_MATERIAL_CHANGE` (same print, no material field change).
3. OPEN SYMBOL analyzed underlying RELIANCE (no strike). Auto-refresh of latest dropped strike/PE/`excellent` contract_state. T0 remained 1270 PE. Categories: `CONTRACT_CHANGED` on `contract_state`, `strike`, `option_type`. Honest: market still closed; this is observation-shape change, not a live tape move.

## UX (production Netlify)

- Nav **Watchlist** workspace (T0 / NOW / what changed / missing / confirm / invalidate)
- Screener **WATCHED CHANGES** strip
- Button WATCH / WATCHED = pin observation (implementation `toggleWatch`)
- Filter **Needs confirmation** = engine `research_state=WATCH` (unchanged backend)
- Symbol workspace: **T0 WATCH** vs **CURRENT** when pinned
- Pattern replay copy: `HISTORICAL REPLAY DATASET NOT AVAILABLE IN THIS DEPLOYMENT`

No BUY/SELL. No score. No probability on Watchlist.

## Mobile

At ≤720px only the card `.watch-pin` is interactive; table WATCH is disabled. First-paint MCP at 390×844 and 360×800: 0 uncaught JS errors; Watchlist chrome present. WATCH click forensics were executed at 1280×800 because screener had no RELIANCE row until an observation existed.

## Tests (this machine, after deploy)

- `pytest`: **2161 passed** in 87.81s
- `ruff check app tests`: clean
- `mypy app --strict`: 167 files, clean

Test count did not drop.

## Git leftovers (classified; not part of e71afc5)

| Path | Class | Action |
| --- | --- | --- |
| `e71afc5` tree | RELEASE FILE | preserved |
| `.gitignore` `.netlify/` (`849403a`) | LOCAL TOOLING | committed ignore only |
| `.netlify/state.json` | LOCAL TOOLING | not committed |
| `docs/uat/phase-4/mcp-firstpaint-*.png` | UAT ARTIFACT | not folded into feature commit |
| `_tmp_reliance_analyze.json` | TEMPORARY ARTIFACT | not committed |
| `.scratch_uat/` MCP scripts and dumps | LOCAL TOOLING | not committed |

No `.env`, tokens, or credentials committed. `git remote` is empty — **push to origin is NOT APPLICABLE**. Deploy used Railway `up` + Netlify CLI from the verified local tree.

## Production identity

| Item | Value |
| --- | --- |
| Product commit | `e71afc5` |
| Hygiene commit | `849403a` (HEAD) |
| Railway project | TIRE `a65737bd-78f0-42b9-9f61-abcd30143918` |
| Railway service | api `02afa032-20ff-443d-9118-ebf59343b4df` |
| Railway env | production `9dd3e9aa-28a9-4985-aaca-b04677d3066c` |
| Railway deployment | `e98e4b44-97fb-4bfc-bb68-30b1f6ec1f76` SUCCESS |
| Volume | `tire-data` → `/data` |
| API | `https://api-production-983e.up.railway.app` |
| Netlify | `https://tire-research-terminal.netlify.app` |
| Netlify deploy (this frontend) | `6aaf919442f408ee5a5f4723` |

Health: `broker_execution=impossible`, `qwen_enabled=false`, `token_configured=true`, `data_root=/data`. `UPSTOX_ACCESS_TOKEN` exists as a backend variable; value not printed.

## RELIANCE 1270 PE journey (production, session CLOSED)

NSE **CLOSED** (weekend 2026-09-20 IST). Last market observation used: **2026-09-18T10:28:33.894Z**. Not treated as live.

Resolved from AnalyzeResponse, not assumed: strike **1270 PE** found, LTP 32.5, bid 32.55, ask 33.05, OI 1221500, IV 17.58, liquidity excellent, research_state **CONFLICT**, pattern **NONE**, timing **UNKNOWN**, `expiry` not on payload.

Pin path: production ANALYZE of `RELIANCE 1270 PE` then `toggleWatch('RELIANCE')` (same function as the WATCH button). Screener had no RELIANCE row at first paint, so the visible table button was not the entry. Network: `POST /api/research/watches` **200**.

WatchRecord:

- `watch_id` `0b61403787004264aba8e75a76315769`
- `created_at` `2026-09-20T08:02:14.944594Z`
- T0 `observation_id` `b8f0b4e40aea41ed83d0907cdcdd0367`
- T0 timestamp `2026-09-18T10:28:33.894000Z`
- T0 research_state `CONFLICT`
- contract context 1270 PE when AnalyzeResponse carried it

Duplicate POST same symbol: **409** `already watching RELIANCE`. Active count remained 1.

## KAYNES / BEL / ZZZXNOTREAL

| Symbol | Result |
| --- | --- |
| KAYNES | Analyze **200**, error None, EARLY_SETUP / EARLY, last print 2026-09-18T10:24:36.107Z. Not pinned (canonical pin was RELIANCE 1270 PE). |
| BEL | Analyze **200**, error None, EARLY_SETUP / EARLY, last print 2026-09-18T10:29:21.622Z. Not pinned. |
| ZZZXNOTREAL | Analyze **200** with error `'ZZZXNOTREAL' not found in the real Upstox instrument master`. POST watch with no observation: **400** `T0 OBSERVATION UNAVAILABLE`. UI `toggleWatch` returns early without a lastResponse/row. |

A tester-injected POST of `{symbol, error}` was accepted as an empty snapshot (implementation maps Analyze-shaped dicts without requiring `audit_id`). That record was **DELETE**d immediately and is not a UI path. Not treated as a shipping defect in this freeze; do not fabricate a WatchRecord from the UI for this symbol.

## Railway restart

Before restart: 1 watch, T0 id `b8f0…`, latest id `0e18…` then later `5004…` after OPEN SYMBOL auto-refresh.

Restart: in-place on `e98e4b44`. Health 200, `broker_execution=impossible`, `data_root=/data`.

After restart: same `watch_id`, **T0 unchanged** (1270 PE, observation_id `b8f0…`). Latest still the post-OPEN-SYMBOL RELIANCE underlying snapshot (`CONTRACT_CHANGED`). Frontend Watchlist loaded from Netlify with those fields. Persistence **PASS**.

Hung Railway SSH exit `4294967295` from a prior session is **not** a TIRE defect and was not reopened.

## Remove

Watchlist REMOVE → `DELETE /api/research/watches/0b61403787004264aba8e75a76315769` **200**. DOM: `NO PINNED OBSERVATIONS`. GET list empty. Hard reload: still empty. Append-only tombstone (no T0 rewrite).

## MCP

Playwright MCP `localhost:8931`, instrumentation before navigate.

| Viewport | First paint |
| --- | --- |
| 1280×800 | PASS |
| 1440×900 | PASS |
| 390×844 | PASS |
| 360×800 | PASS |

Uncaught JS errors: 0. Mixed content: 0. First-paint APIs HTTPS 200 (`/api/health`, `/api/market/observed`, `/api/research/watches`, `/api/research/jobs/latest`). No localhost / `:8000` / `:8931` in loaded scripts.

Expected network 404: `GET /api/research/patterns?source=replay` when Patterns is opened — honest replay unavailable. Browser “Failed to load resource” for that 404 is **not** an uncaught application exception.

## Safety

Grep of `app/` Python: no `place_order` / `/orders` / `execute_order` / `modify_order` / `cancel_order`. Frontend watch path only GET/POST/DELETE `/api/research/watches*`. `broker_execution=impossible`.

## Full F&O scan

Session CLOSED weekend 2026-09-20. **Not executed. PENDING.** Do not call GREEN.

## Gate table

| Gate | Result |
| --- | --- |
| Commit `e71afc5` preserved | PASS |
| pytest 2161 | PASS |
| ruff | PASS |
| mypy `--strict` | PASS |
| Watch API | PASS |
| T0 immutability | PASS |
| Duplicate 409 | PASS |
| Invalid ZZZXNOTREAL (no observation) | PASS |
| Migration (duplicate skip; local unit) | PASS |
| Persistence `/data` | PASS |
| Railway restart | PASS |
| Netlify frontend proof | PASS |
| Watchlist | PASS |
| Watched Changes (no fake alerts; CONTRACT_CHANGED when latest shape changed) | PASS |
| Mobile first paint | PASS |
| MCP | PASS |
| Security / token not in frontend | PASS |
| Safety / no broker execution | PASS |
| RELIANCE 1270 PE (closed-session last print) | PASS |
| KAYNES observe | PASS |
| BEL observe | PASS |
| Git push origin | NOT APPLICABLE (no remote) |
| Full F&O live scan | PENDING |
| Historical replay dataset | NOT APPLICABLE / honest unavailable |

## Decision

```text
CONDITIONAL GREEN — FULL F&O LIVE VALIDATION PENDING
```

Not FINAL GREEN. Not UNFROZEN.

v1 follow-up (`b7b16f7`): scope vs contract change is certified in `OBSERVATION_WATCH_V1_CERTIFICATION.md`. Full F&O remains the open live-session gate.
