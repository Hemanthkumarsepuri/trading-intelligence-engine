# TIRE GREEN READINESS REPORT

Recorded after Master Green Build stages 0–9 against the live repository.
Baseline (pre-change) is `docs/BASELINE_HEALTH.md` (1641/1641). This report is **not** a claim that passing tests equal market truth.

## 1. Executive verdict

**CONDITIONAL GREEN**

Engineering gates (pytest / Ruff / mypy / safety searches) pass. Research-state no longer uses a hidden evidence-count score. Session VWAP, holiday calendar, stream vote-gating, development narratives, and CONFIRM IF / INVALIDATE IF are in the product path.

It is not full GREEN because: browser UAT was **not** executed against a live authenticated dashboard in this session; option-chain timestamps remain HTTP receipt time (honestly labeled, not exchange matching time); FII/DII remain UNKNOWN without a verified producer; unused infrastructure pins (SQLAlchemy / asyncpg / Redis / APScheduler) were left in place rather than removed without a dedicated runtime audit; volatility-structure EARLY_SETUP (Pattern D) was **not** added because it would become an opaque interpretation without a justified independent observable.

## 2. Engineering health

| Check | Result |
| ----- | ------ |
| pytest | **1720 passed**, 0 failed, 0 skipped, 0 collection errors |
| `ruff check app tests` | All checks passed |
| `mypy app --strict` | Success, 141 source files |
| Imports | Critical options path compiles and is exercised by integration tests |

## 3. Safety health

- Broker execution assert still runs at API lifespan **before** provider/client init (`settings.assert_broker_execution_disabled()`).
- No `place_order` / `modify_order` / `cancel_order` in `app/`.
- TRADEABLE is compatibility-only; UI shows `COMPATIBLE (NOT AN ORDER)` with warn (amber), not a green action button.
- RHP fetch remains HTTPS allowlist + `follow_redirects=False`.
- Volume cannot vote BULLISH/BEARISH (`row_volume` + repo regression).

## 4. Data-quality health

- Chain quality now flags empty chain, duplicate strike+right, and expired expiry vs `as_of` date, in addition to stale / dead / one-sided / missing Greeks / wide / crossed.
- Missing PCR history stays UNKNOWN / non-directional until a comparable prior snapshot exists.
- Cash context (sample breadth, delivery, FII/DII) is not an EvidenceGroup and cannot vote.
- **Stop condition honored:** Upstox chain `as_of` is HTTP receipt, not matching-engine time. Copy states this explicitly. Not relabeled as exchange observation time.

## 5. Freshness health

- Ten named streams (quote, M15, chain, futures, macro, news, sector, sample breadth, delivery, FII/DII) with source, timestamps, label, completeness, `usable_for_display`, `usable_for_vote`, blast radius (`withheld_calculations` / `blocks_entire_report`).
- Stale chain / futures / quote / M15 cannot silently vote; stale quote/chain/M15 force `CONFIRMATION_PENDING`. Futures-only staleness does **not** globally pending.
- Labels in use: LIVE / RECENT / STALE / MARKET_CLOSED / DEGRADED / UNAVAILABLE / EOD / NOT_APPLICABLE / UNKNOWN / ERROR. `PREVIOUS_SESSION` appears as M15 session text when candles are stale. First-class `HISTORICAL` / `INVALID` enums were not added as duplicates of EOD / UNAVAILABLE.

## 6. Evidence independence

- Formal map: `app/domain/options/evidence_dependency.py`.
- OPTIONS_OI rows (PCR, ΔOI, volume, OI S/R) remain **one group**.
- Relative strength vs GLOBAL Nifty overlap is documented; they are different questions (stock vs index gap vs market-wide verdict) but share Nifty day-change — not counted as five chain confirmations.
- PCR **level** is context only. Volume is activity only.

## 7. Research-state correctness

Documented in `docs/research/RESEARCH_STATE.md`.

Precedence: DATA_INSUFFICIENT → CONFLICT → CONFIRMATION_PENDING (quote or chain or M15) → NO_TRADE → EXTENDED (≥6% day move on TRADEABLE/WATCH) → CONFIRMED_SETUP → EARLY_SETUP (WATCH + named pattern ≠ NONE) → WATCH → UNKNOWN.

`supporting_evidence_count >= 2` **does not** produce EARLY_SETUP (regression tested).

## 8. Early-opportunity capability

Named patterns in `classify_development`:

- **OI_MIGRATION** — meaningful CE/PE OI-weighted strike migration, current chain, no CONFLICT.
- **RELATIVE_STRENGTH** — stock vs Nifty gap with non-opposing current M15.
- **FUTURES_STRUCTURE** — comparable **basis change**, not a single CURRENT_BASIS print.

Each narrative includes what / why / missing / confirm_if / invalidate_if / per-stream freshness. Compact report and research-context UI render this.

**Pattern D (volatility structure) not implemented** — IV/skew is non-directional context today; promoting it to EARLY_SETUP without a justified independent confirmation rule would be a hidden score.

## 9. Confirmation/invalidation capability

- Per-pattern CONFIRM IF / INVALIDATE IF on `DevelopmentNarrative`.
- Candidate `invalidation_condition` and watch-next conditions remain; watch-next now also surfaces CONFIRM IF / INVALIDATE IF as observational (CANNOT_BE_EVALUATED) rows — not orders.
- No universal “count ≥ N” confirmation threshold.

## 10. Contract-quality capability

Unchanged architecture: liquidity, spread, IV, DTE, decay remain **separate** from directional research state. Direction / contract quality / timing are still different questions. TRADEABLE is not a combined mega-score for “buy this strike.”

## 11. UI/UAT status

**Code/UI contract (unit + HTML string tests):**

- Research state first, then developing observation, CONFIRM IF / INVALIDATE IF, SAMPLE BREADTH, delivery date/source, FII UNKNOWN/caller, COMPATIBLE (NOT AN ORDER), WHY NOT CONFIRMED.

**Browser E2E: NOT EXECUTED in this session.**

Reason: no authenticated TIRE dashboard process was running for this agent (no live uvicorn + operator token session to drive). Static HTML/API contract tests ran; a real browser pass over live streams, stale badges, and JS errors was not performed. Do not treat this report as browser-validated.

## 12. Security status

- Execution API surface still absent.
- RHP: fail-closed allowlist, no redirect following.
- Secrets remain env-based; unused DB/Redis URLs are still settings placeholders, not runtime clients.
- No new unrestricted URL fetcher.
- Dependency CVE scan was **not** run (no `pip-audit` in the required gate). Residual risk: pinned unused packages still in `pyproject.toml`.

## 13. Performance status

No Redis/Celery/Kafka added. Architecture remains single FastAPI + Upstox + JSONL.

Not re-profiled live this session. Known remaining cost: option-chain + quotes + M15 + index context per analyze; session VWAP is local computation on the already-fetched M15 series. Do not add infra until a measured bottleneck exists.

## 14. Test results

- **1720 passed / 0 failed / 0 skipped**
- Adversarial matrix: ≥40 named research-state cases plus withhold / volume / PCR / basis-history / safety / HTTP-receipt copy tests (`tests/unit/options/test_green_adversarial_matrix.py`)
- Calendar: weekday, weekend, file-sourced 2026 CM holidays (NSE/CMTR/71775), Muhurat Sunday `SPECIAL:2026-11-08`
- Session VWAP: no previous-session contamination, no look-ahead, first-bar typical price
- Development patterns and EARLY_SETUP-without-count

## 15. Remaining limitations

- Chain timestamps ≠ exchange matching engine.
- FII/DII UNKNOWN unless caller-supplied; never inferred from price/OI.
- Sample breadth is Nifty 50 sample, not official NSE A/D.
- Sector relative strength still limited by authorized classification coverage.
- Pattern D volatility EARLY_SETUP withheld.
- Unused sqlalchemy/asyncpg/redis/apscheduler runtime pins remain (not imported on the options path).
- Browser UAT not executed.
- Outcome tracking can store confirmation/invalidation text; it is **not** a backtester and must not rewrite original state from future prices.

## 16. Known risks

- Loading the official holiday file at API startup changes `/api/status` `is_holiday` on real holiday dates (tests now assert `bool`, not always false).
- Session VWAP with sparse/missing M15 volume degrades to unavailable (labeled); rolling VWAP still exists for strategy/regime and is labeled rolling.
- Relative strength and GLOBAL both touch Nifty day-change — operators must read the dependency map, not treat them as fully independent.
- Green UI for CONFIRMED_SETUP means **confirmed/current**, not buy.

## 17. What should NOT be built

- Order placement, tips, BUY/SELL, confidence %, probabilities, expected returns, smart-money scores
- LLM/Qwen/embeddings as source of truth
- Unofficial NSE scrape, Yahoo hidden dependency, paid vendor zoo
- Redis/Celery/Kafka without a measured need
- A numeric EARLY_SETUP classifier
- Relabeling HTTP receipt as exchange time
- Inferring FII/DII or index F&O from cash or OI
- Volatility EARLY_SETUP until an independent, testable observable exists

## 18. Single highest-value next improvement

**Run a live authenticated browser UAT on the operator workstation** (dashboard load, research-state hero, CONFIRM IF / INVALIDATE IF, SAMPLE BREADTH, delivery trade date, FII source, stale stream badges, no green TRADEABLE action) and capture one real F&O symbol through the ten personal-effectiveness questions. That is the remaining gap between CONDITIONAL GREEN and operator-trusted GREEN — not more features.
