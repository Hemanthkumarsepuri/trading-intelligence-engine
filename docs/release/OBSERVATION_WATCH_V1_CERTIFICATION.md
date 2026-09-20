# Observation Watch v1 — production hardening certification

Date: 2026-09-20 IST.

## Identity

| Item | Value |
| --- | --- |
| Feature | `e71afc5` feat: add observation-backed research watch |
| Hygiene | `849403a` |
| Prior cert docs | `8f4bf19` |
| This hardening | `b7b16f7` fix: separate observation scope from contract change |
| Railway | `ba3628eb-76a7-46d8-860c-6009c86bea47` SUCCESS |
| Netlify | `6aaf9a2d98b6c0d5e748d4cb` live at https://tire-research-terminal.netlify.app |
| API | https://api-production-983e.up.railway.app |

WatchRecord was not rebuilt. No new product surface beyond scope semantics, delta categories, and copy that names those categories.

## Architecture

Netlify static → Railway FastAPI → Upstox read-only → `/data` JSONL.

`WatchEvent` remains CREATED / LATEST_UPDATED / REMOVED. T0 is CREATED only.

A WatchRecord points at existing journal observations via `observation_id` (`audit_id`). Outcome checkpoints are looked up on that id (`+30m` … `+5d` labels when captured). History is not duplicated into a second journal.

## Scope semantics

`ObservationSnapshot.instrument_type` is derived, never invented:

- `OPTION_CONTRACT` — strike and/or CE/PE (or `has_specific_contract`)
- `FUTURES_CONTRACT` — FUT
- `UNDERLYING` — otherwise

Expiry stays missing when AnalyzeResponse has no `parsed_expiry_hint`.

Delta categories are disjoint:

| Situation | Category |
| --- | --- |
| Same instrument, research fields change | `STATE_CHANGED` / `PATTERN_CHANGED` / … |
| Same option, liquidity/usability | `CONTRACT_CHANGED` / `CONTRACT_DEGRADED` |
| Same option, strike | `STRIKE_CHANGED` |
| Same option, CE/PE | `OPTION_TYPE_CHANGED` |
| Same option, expiry | `EXPIRY_CHANGED` |
| Option vs underlying vs futures | `OBSERVATION_SCOPE_CHANGED` only |
| No field meaning change (including new `audit_id` / `generated_at`) | `NO_MATERIAL_CHANGE`; JSONL latest event is not appended |

`/api/analyze` auto-refreshes latest **only** when the incoming observation is the same instrument as T0. Opening Symbol on a pinned `RELIANCE 1270 PE` uses the pinned query. Analyzing bare `RELIANCE` does not silently overwrite an option T0 as `CONTRACT_CHANGED`.

Explicit `POST …/latest` with a different scope is allowed and is labelled `OBSERVATION_SCOPE_CHANGED`. That is not market deterioration.

Error payloads (`observation.error`) cannot mint a WatchRecord (400).

## Production RELIANCE 1270 PE

NSE **CLOSED** weekend 2026-09-20. Last print **2026-09-18T10:28:33.894Z**. Not live.

Resolved from AnalyzeResponse: strike 1270 PE, LTP 32.5, bid 32.55, ask 33.05, volume 848500, OI 1221500, ΔOI -11000, IV 17.58, liquidity excellent, freshness MODERATE, `expiry=null`, `dte=null`, `instrument_type=OPTION_CONTRACT`, state CONFLICT.

Watch `b4aeb4df853f4466a87013e043a28963`, T0 observation_id `479efc4173f041eb965efb531104fbb7`.

Same-scope re-analyze: T0 identical, `NO_MATERIAL_CHANGE`.

Explicit latest = RELIANCE underlying: T0 identical, `OBSERVATION_SCOPE_CHANGED` (`RELIANCE 1270 PE` → `RELIANCE`).

OPEN SYMBOL then same-scope analyze restored latest to `OPTION_CONTRACT` with `NO_MATERIAL_CHANGE` versus T0 (UI Watchlist after restart).

## Restart

In-place restart of `ba3628eb`. Health `broker_execution=impossible`, `data_root=/data`. Same `watch_id`. T0 observation_id, 1270 PE, OPTION_CONTRACT unchanged. No duplicate.

## MCP

Instrument before navigate. 1280×800, 1440×900, 390×844, 360×800: 0 uncaught JS errors, 0 localhost, Watchlist copy WHAT I PINNED / WHY (AT T0) / T0 / NOW / WHAT CHANGED / MISSING / CONFIRM IF / INVALIDATE IF. WATCHED CHANGES showed `RELIANCE 1270 PE OBSERVATION SCOPE CHANGED` with before/after and market time. Symbol: T0 WATCH vs CURRENT OBSERVATION separate. After restart + same-scope restore: NO MATERIAL CHANGE. REMOVE then reload: empty.

Expected replay 404 unchanged when Patterns is opened.

## Other symbols

| Symbol | Result |
| --- | --- |
| KAYNES | Analyze 200, EARLY_SETUP, underlying, last print 2026-09-18T10:24:36Z |
| BEL | Analyze 200, EARLY_SETUP, underlying, last print 2026-09-18T10:29:21Z |
| ZZZXNOTREAL | Analyze error from instrument master; watch 400 |

## Tests

`pytest` **2165** passed (was 2161; +4 synthetic/API scope tests, labelled synthetic where fixtures are used).  
`ruff check app tests` clean.  
`mypy app --strict` 167 files clean.

## Full F&O / replay

FULL F&O LIVE VALIDATION = **PENDING** (session CLOSED).  
HISTORICAL REPLAY = **HONESTLY UNAVAILABLE**.

## Product scores (re-scored after this hardening)

| Axis | Score | Why |
| --- | --- | --- |
| Engineering readiness | 84 | Deployed, 2165 tests, persistence/restart proven. No git origin. |
| Research integrity | 80 | No fabricated expiry/LTP; scope vs market change split; replay still honest-unavailable. |
| Trader workflow | 72 | Pin → delta → confirm/invalidate copy is live; universe scan not run this session. |
| Observation Watch | 86 | T0 immutable, scope categories, Watchlist memory language. Not a book. |
| Historical learning | 38 | Outcome labels wired; no checkpoints on this pin; no replay dataset. |
| Options analytics | 68 | Real 1270 PE last print; expiry not on payload so not shown. |
| UX | 74 | T0 vs CURRENT split; WATCHED CHANGES names the category. Dense evidence line. |
| Mobile | 78 | First-paint four sizes; hidden WATCH is disabled/inert. No live mobile pin click this pass (no screener row). |
| Performance | 70 | Analyze few seconds on closed print; health 200 after restart. |
| Safety | 93 | No order APIs; token backend-only; `broker_execution=impossible`. |
| Differentiation | 76 | Observation memory, not a scorecard. Still not a live-session proof. |

Not a blended “58/100”. These are separate axes.

## Gates

| Gate | Result |
| --- | --- |
| Observation Watch | PASS |
| T0 | PASS |
| Latest | PASS |
| Scope semantics | PASS |
| Delta | PASS |
| Persistence | PASS |
| Restart | PASS |
| Mobile | PASS (first paint + control isolation) |
| MCP | PASS |
| Safety | PASS |
| Full F&O | PENDING |
| Historical replay | HONESTLY UNAVAILABLE |

## Decision

```text
CONDITIONAL GREEN — FULL F&O LIVE VALIDATION PENDING
```

Not FINAL GREEN.
