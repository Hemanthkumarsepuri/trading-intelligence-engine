# TIRE — Final 95% Sprint: Status

Date: 13 September 2026. Branch: `phase-3-historical-validation`.
Baseline commits: `07f3b09` (GREEN) → `226019e` (gitignore fix) →
`1fa6c63` (Phase 3 gap-closure: price-only replay observations).

## Sprint 0 — forensic baseline (verified, not assumed)

- `git status`: clean at start of this sprint (previous work committed
  as `1fa6c63`).
- Branch: `phase-3-historical-validation`. No rewrite of `07f3b09`/
  `226019e`.
- `.gitignore`: `.env`/`.env.*` correctly ignored (with `!.env.example`
  kept); no `.env` ever tracked (`git ls-files | grep .env` empty).
- `SAFETY_LOCK.txt` present at repo root.
- Test count: **1982 tests collected**, matches the number reported at
  the end of the prior session.
- Qwen integration: `app/llm/qwen_narrative.py` (361 lines) already
  implements a bounded explainer — 8s generation timeout, 2s health
  timeout, a forbidden-word/phrase rejection list (buy/sell/probability/
  confidence/recommendation/expected_return/"X% confidence"/"institutional
  buyers are accumulating"), and a documented deterministic fallback on
  any failure. This already satisfies Sections 3/4/13/14's structural
  requirements — not rebuilt this sprint, only spot-verified (Sprint 4
  below).
- UI nav already matches Section 15's requested shape: DISCOVER /
  WATCHLIST / MARKET / RESEARCH / HISTORY (`app/api/static/index.html`).
- Historical replay (this session's own prior work): real price-only
  observations, no-lookahead, reproducible, derivatives honestly marked
  unavailable — see `docs/HISTORICAL_REPLAY.md`.

## Highest-value remaining gaps identified (P0/P1)

1. **P0 — Technical-level threading for price-only observations.**
   `nearest_level_kind`/`nearest_level_value` are `None` on every
   price-only (replay) observation because the price-only gate reuses
   `report.support_levels`/`resistance_levels` (chain-OI-derived only,
   empty with no chain) instead of the pipeline's own real candle-based
   `technical_levels`. This is the single most valuable closeable gap:
   it's what makes invalidation outcomes honestly `UNKNOWN` instead of a
   real determination. **→ Sprint 1.**
2. **P1 — No deterministic historical pattern aggregation.** Individual
   observations and their outcomes exist and are queryable one at a
   time; there is no "when PRE_BREAKOUT_COMPRESSION appeared historically,
   what happened afterward" summary. **→ Sprint 2.**
3. **P1 — Historical replay performance.** ~3s/bar, dominated by
   `JsonlCandleRepository` re-reading and re-parsing its entire file on
   every `query()`/`latest()` call within one replay session (documented
   in `docs/HISTORICAL_REPLAY.md` §7). **→ Sprint 2b.**
4. **P2 — Everything else in this brief** (whole-market scanner review,
   provider architecture, UX polish, browser UAT, safety audit) —
   largely already satisfied by the existing baseline per this audit;
   spot-verified rather than rebuilt, with any genuine small gaps fixed
   inline. Full detail in the final report.

## Sprint plan (this session)

1. Sprint 1: thread real candle-based technical levels into the
   price-only observation path (reuse existing `technical_price_levels()`
   / `vwap_ema_levels()` — no second TA engine).
2. Verify outcome horizons (+30m/+1h/+1/+3/+5 sessions) — already
   implemented and tested; add any missing edge-case coverage only.
3. Sprint 2: deterministic pattern-aggregation module (descriptive counts
   only — never a probability/win-rate).
4. Replay performance: an in-memory candle cache scoped to one replay
   run (no Redis/Postgres), benchmarked before/after.
5. Sprint 3: verify whole-market scanner's EARLY_SETUP gating (named
   pattern required) and skim for obvious, safe performance issues —
   spot-check only, no blind rewrite.
6. Sprint 4: verify Qwen bounded timeout/fallback with a live check
   against the local LM Studio endpoint if reachable; otherwise verify
   via the existing test suite and document the hardware reality.
7. Sprint 5: verify symbol-page/UI requirements against the existing
   `index.html`; browser UAT.
8. Safety audit (mechanical checklist).
9. Final gates: `pytest -q`, `ruff check .`, `mypy app --strict`.
10. Final report + git commit(s).

## Explicitly deferred (per Section 30, decided now, not discovered late)

5paisa full integration, GIFT Nifty, BSE dual listing, Level-2/tick
data, advanced fundamentals, shareholder/pledge intelligence, ML/
prediction models, broker execution, new paid data vendors, distributed
infrastructure, a full UI redesign, downloading a different/larger local
model, and any multi-agent AI orchestration. None of these are touched
this sprint.
