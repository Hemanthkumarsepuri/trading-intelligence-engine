# Dependency Direction Finding — `domain/market` → `domain/technical`

Status: **Documentation-only finding, produced by the MARKET INPUT ARCHITECTURE FINALIZATION milestone's Phase 1.** No production code accompanies or is changed by this document. Labels: `[A]` explicit repository specification, `[B]` strong architectural inference, `[C]` design proposal requiring owner approval, `[D]` unsupported/unresolved.

---

## 1. The Question

`app/domain/market/candle_inputs.py` and `app/domain/market/timeframe_requirement.py` (Strategy Input Foundation milestone) both import `bounded_series()`/`InvalidCandleSeriesError` from `app.domain.technical.series`. ARCHITECTURE.md §6's dependency diagram draws `domain/market → domain/technical` (technical consumes market's output), the opposite direction from this import. Is this (A) a genuine architectural violation, (B) an acceptable shared-domain dependency, (C) evidence `bounded_series()` belongs in a lower/shared module, or (D) something else?

## 2. Evidence

- ARCHITECTURE.md §6's explicit textual rule, quoted verbatim: *"nothing in `domain/*` may import from `data/*`, `persistence/*`, `llm/*`, or `orchestration/*`."* `domain/market` importing `domain/technical` is not named by this rule anywhere — textually permitted.
- ARCHITECTURE.md §6's diagram nonetheless draws the arrow the other way (`domain/market → domain/technical`), consistent with `domain/market` being upstream/foundational and `domain/technical` consuming its output.
- `app/domain/technical/series.py`'s own module docstring (read fresh this session) frames `bounded_series()` as belonging to `domain/technical`: *"Every `calculate_*`/`find_*` function in `domain/technical/` funnels its input candles through `bounded_series()` first."*
- `bounded_series()`'s actual implementation (read fresh this session) operates purely on `Candle`/`Timeframe`/`instrument_id`/`as_of` — no indicator math, no `domain/technical`-specific logic of any kind. Nothing about its content requires it to live in `domain/technical`.
- `app/domain/market/candle_inputs.py`'s own docstring (written the prior milestone) already names this exact tension and explicitly declines to resolve it, deferring to "a future, separately-scoped minimal refactor."

## 3. Finding

**Not a genuine violation (A)** — no explicit textual rule in §6 is broken; the rule constrains what `domain/*` may import from `data/*`/`persistence/*`/`llm/*`/`orchestration/*`, not inter-`domain` imports.

**Currently tolerable as (B), with a documented (C) undertone.** The dependency is one-directional (no import cycle — `domain/technical` does not import back from `domain/market`) and crosses no data/persistence/llm/orchestration boundary. But `bounded_series()`'s content is itself evidence that its present package is an accident of "where it was first needed" (the Technical Engine milestone) rather than a considered ownership decision — it is, in substance, a generic `Candle`-boundary utility with zero technical-indicator logic, and now two packages depend on it directly.

## 4. Why This Is Not Resolved Unilaterally Now

- Moving it changes the import surface of all ten existing `domain/technical/*.py` modules plus the two `domain/market` modules that depend on it today — a mechanical but wide-blast-radius change in service of a cosmetic direction improvement, not a functional defect. Nothing is currently broken by the present location.
- The correct destination is itself a design choice (a `domain/market/series.py`, since the function operates purely on `domain/market` types? A new, narrowly-scoped shared module — explicitly not a generic `utils` dumping ground, which this task's own instructions forbid?) — genuinely `[C]`, requiring the same owner sign-off every other architecture proposal in this repository has received before implementation.
- This task's own explicit instruction: *"Do NOT modify architecture merely to make the dependency graph look cleaner."*

## 5. Recommendation (not adopted — awaiting owner approval)

Revisit if/when a third package (`domain/options`, `domain/regime`, etc.) also needs candle-boundary filtering — three independent consumers of the same utility is meaningfully stronger evidence for consolidation than the current two, and would also clarify what the shared destination should be (rather than guessing now, ahead of a third real use case).

## 6. Verdict

No code changed by this document. This finding stands alongside — and does not replace — the identical flag already present in `candle_inputs.py`'s own docstring; both should be read together by whoever eventually revisits this.
