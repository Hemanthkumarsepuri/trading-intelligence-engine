# TIRE — Final live F&O validation report

Date/time: **2026-09-20 17:04 IST** (Sunday).  
Branch: `phase-3-historical-validation`.  
Code at certification: `ab9d717` (observed chain expiry) plus `scripts/certify_live_fno_scan.py`.  
Prior Watch v1: `b7b16f7` / `d3d133b`. Feature: `e71afc5`.

Production:

- Frontend: https://tire-research-terminal.netlify.app
- API: https://api-production-983e.up.railway.app
- Railway deployment at audit start: `ba3628eb-76a7-46d8-860c-6009c86bea47`
- Provider: Upstox read-only. `broker_execution=impossible`. `QWEN_ENABLED=false`. `TIRE_DATA_ROOT=/data`.

## 1. Market session

| Field | Value |
| --- | --- |
| `session_window` | **CLOSED** |
| `calendar_date_ist` | 2026-09-20 |
| `is_trading_day` | false |
| `is_weekend` | true |
| Basis | NSE trading calendar + IST clock. Not a live exchange ping. |

`python -m scripts.certify_live_fno_scan` against production exited **2** and printed:

`FULL F&O LIVE VALIDATION = PENDING — MARKET CLOSED`

It did **not** start `POST /api/research/jobs/discover`. Closed-session last prints were not relabelled live.

## 2. Full F&O live scan

| Item | Result |
| --- | --- |
| Universe size this session | **PENDING** |
| Stage 1 metrics | **PENDING** |
| Stage 2 metrics | **PENDING** |
| Performance | **PENDING** |
| Research-state distribution | **PENDING** |
| Integrity sampling (10 cases) | **PENDING** |

Architecture check (code, not a live run):

- Whole-market `run_daily_research()` uses `ScreeningConfig(survivor_cap=DEFAULT_WHOLE_MARKET_SURVIVOR_CAP)` with **`DEFAULT_WHOLE_MARKET_SURVIVOR_CAP = 1000`**.
- Historical Stage-2 cap of **30 is not the production default**. Default `ScreeningConfig.survivor_cap = 80` is only for callers that construct a config without the whole-market override.
- Deferrals are labelled `STAGE_2_DEFERRED_CAPACITY` / `STAGE_2_DEFERRED_RATE_BUDGET`, not hidden.
- Last production job: `GET /api/research/jobs/latest` → `status: NONE` (no scan left in-process after restarts).

When the next NSE regular session is **OPEN**, run:

```text
python -m scripts.certify_live_fno_scan --api https://api-production-983e.up.railway.app
```

That is the only allowed path to turn this gate PASS.

## 3. Defects found this sprint

| Priority | Defect | Root cause | Fix | Validation |
| --- | --- | --- | --- | --- |
| P1 | Screener `contract_expiry` used `term_structure.expiries[0]` | First listed expiry is not necessarily the chain used for the observation | Use `visual.option_chain.expiry` / `requested_contract.expiry` only; leave null if missing | Synthetic snapshot tests: chain expiry copied; term-structure-only stays None |
| P1 | Watch T0 expiry/DTE always missing even when a chain was fetched | Analyze visual did not carry the fetched chain expiry; snapshot only read `parsed_expiry_hint` | Persist observed chain expiry on `OptionChainVisual` / `RequestedContractVisual`; snapshot copies it; DTE only from matching term-structure row | Unit tests labelled synthetic |
| P0 | Live F&O not executed | Weekend / CLOSED | Harness refuses to scan | Script exit 2 on production 2026-09-20 |

## 4. Screener

**PASS** (closed-session product surface; no live universe rows this session).

Rows expose symbol, research state, day move, pattern, evidence completeness (`research_confidence` WEAK/STRONG — not a probability), contract, freshness. Mobile cards now include **Missing** when `what_is_missing` is present. Hidden WATCH controls remain inert.

## 5. Option coverage

**PASS** for closed-session last print of **RELIANCE 1270 PE** (prior Watch v1 cert). **PENDING** for live CE/PE/ATM/OTM/expiry matrix.

Expiry: engine had term structure `2026-09-29` and `2026-10-27`. T0 previously left expiry null rather than guessing `expiries[0]` — correct honesty, incomplete wiring. After this fix, T0 may show the **fetched chain expiry** when the visual carries it. Still never inferred from the first term-structure row.

## 6. Observation Watch

**PASS** (v1, `b7b16f7`). Option vs underlying = `OBSERVATION_SCOPE_CHANGED`. Same-instrument `generated_at`/`audit_id` only = `NO_MATERIAL_CHANGE` (no JSONL latest). Analyze auto-refresh same-instrument only. Live evolution **PENDING**.

## 7. Historical learning

**UNAVAILABLE** in this deployment.

| Question | Answer |
| --- | --- |
| Code | Replay provider, dataset builder, pattern aggregation, outcome horizons exist |
| Tests | Unit/integration replay and outcome tests exist |
| Local dataset | No `research_dataset` JSONL in this workspace |
| Railway `/data` | `GET /api/research/patterns?source=replay` → **404** `HISTORICAL REPLAY DATASET NOT AVAILABLE IN THIS DEPLOYMENT` |
| Frontend | Honest unavailable copy |

Do not build or upload a synthetic production dataset.

Live journal pattern aggregation: `source=LIVE`, 5 named-pattern observations, 0 determined outcomes, explicit SMALL SAMPLE disclaimer (not a win rate).

## 8. Provider redundancy

**NOT APPLICABLE to implement now.**

Upstox = primary, wired. 5paisa = documented, not implemented. Dhan = adapter unused. No second token. Do not average providers. `PROVIDER_CONFLICT` is classified in tests only until a second live provider exists.

## 9. Qwen

**OPTIONAL / DISABLED in production** (`qwen_enabled=false`). Fail-open. Deterministic research does not wait on Qwen. Health stream: AI Explanation UNKNOWN / not configured.

## 10. Performance

Live full-scan latency **PENDING**. Prior measured architecture (docs, 16 Sep 2026 session): Stage 2 ~2.6 s/symbol at concurrency 6 after JSONL index fix; whole-market cap raised because of that measurement. Those numbers are **not** today's live cert.

## 11. Browser / MCP

Prior Watch v1 MCP **PASS** (four viewports, 0 uncaught JS, 0 localhost). This sprint’s frontend copy change (Completeness / Missing) requires a Netlify deploy to appear in production; re-verify after that deploy.

## 12. Safety

**PASS.** No `place_order` / `modify_order` / `cancel_order` / execute_order in `app/`. Safety tests present. Production health `broker_execution=impossible`. Token backend-only.

## 13. Tests (this machine)

- pytest: **2167** passed (was 2165; +2 synthetic expiry tests)
- ruff: clean on app/tests/cert script
- mypy `app --strict`: clean (167 files)

## 14. Remaining blockers

1. **FULL F&O LIVE VALIDATION** — NSE must be OPEN; run `scripts.certify_live_fno_scan`.
2. Historical replay dataset — only if a verified real dataset is later placed on `/data` without invention.
3. Live Observation Watch evolution — same OPEN session.
4. Live option-contract matrix (CE/PE, expiries, liquidity) — same OPEN session.

## 15. Decision

```text
CONDITIONAL GREEN — FULL F&O LIVE VALIDATION PENDING — MARKET CLOSED
```

Not FINAL GREEN.
