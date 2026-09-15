# TIRE — Final 95% Product Completion Report

Date: 15 September 2026. Branch: `phase-3-historical-validation`.

Everything below was **measured**, not inferred. Where something could
not be validated, this report says so plainly instead of scoring it.

---

## 1. Starting state

Baseline `46646ff` (P0 historical-outcome invalidation) on
`4123cd5` → `1fa6c63` → `226019e` → `07f3b09`, plus an uncommitted
working tree carrying Section 8 pattern segmentation.

Verified at the start of this sprint:

| Check | Result |
| --- | --- |
| `pytest -q` | 2019 passed, 48.5 s |
| `ruff check .` | All checks passed |
| `mypy app --strict` | Success, 159 source files |
| Upstox live probe | 200 OK — `NSE_EQ:RELIANCE last_price 1235.3` |
| LM Studio / Qwen | **unreachable** — `QWEN_TIMEOUT` in 2215 ms |
| `.env` tracked? | No (`.gitignore:12`) |

The previous sprint reported 2002 tests; 2019 was accurate for this tree
(the extra 17 are its own uncommitted segmentation tests). No prior
commit was rewritten.

## 2. Audit findings

Full detail: `docs/FINAL_PRODUCT_GAP_AUDIT.md`.

**P0: none.** The carried-in P0 (Section 6 invalidation correctness) was
genuinely closed in `46646ff` and verified by reading the implementation:
`_opposing_level_broken_through()` (confirmation) and
`_supporting_level_broken_through()` (invalidation) are separate
functions with separate geometry, and the Section 6 trap — "clearing
resistance on a bullish thesis is confirmation, not invalidation" — is
explicitly handled and regression-tested.

Several things the brief asked for were **already built** and were
verified rather than rebuilt: Section 12 scanner failure transparency,
Section 14 contract-quality exposure, Section 7 horizon coverage, and
Section 32 safety. Section 31 UI null-safety was swept and found clean —
the two unguarded `.toFixed` candidates are both non-nullable in their
Pydantic models, so neither is a real crash path.

**The one genuinely missing product capability** was pattern
aggregation's reachability: `pattern_aggregation.py` was correct, pure
and well tested, but **nothing in `app/` imported it**. No API route, no
UI. The operator could not reach "what happened after this type of
observation?" at all — the exact question Sections 8/29 put on the
primary research surface.

## 3. Changes made

1. **`app/orchestration/pattern_views.py` (new).** The view layer that
   makes pattern aggregation reachable. It performs no counting of its
   own — every number is produced by `pattern_aggregation.py`, so the
   surface cannot disagree with the module it displays. Section 9 is
   enforced *structurally*: every field is an `int` count or a literal
   string, so a rate, probability or confidence score has nowhere to
   live. A test asserts this and fails if anyone adds a float.
2. **`GET /api/research/patterns` (new).** Read-only, descriptive.
3. **PATTERN HISTORY UI section (new).** Renders the sample-size
   disclaimer *before* any count, then the per-pattern table, then the
   three segment dimensions collapsed, then a closing statement that
   these are counts of what already happened and never influence today's
   shortlist.
4. **Section 8 segmentation finished** (direction / timing stage /
   evidence completeness), as a pure partition over
   `aggregate_by_pattern()` so segment counts always sum back to the
   unsegmented total.
5. **Real UI bug fixed.** The RESEARCH HISTORY table rendered
   `o.selected_right + " " + o.selected_strike` unguarded — a price-only
   replay observation genuinely has neither, so it displayed
   "null null". Now reads "no contract (price-only)".
6. **Guard test updated**, `15` → `16` `fetch(apiUrl(` calls. The guard
   caught the new fetch correctly; the new call does route through
   `apiUrl()`, which is the property actually under test.
7. **`.gitignore`**: raw whole-market scan dumps excluded as debug
   artifacts (Section 37).

## 4. Historical intelligence

Replay produces real price-only observations with no lookahead,
reproducibly, with derivatives honestly marked unavailable. The
~308 s → ~11.2 s improvement on the measured 100-bar/4-session case is
intact and untouched.

## 5. Outcome correctness

CONFIRMATION and INVALIDATION are tracked as two independent outcomes.
All five horizons (+30m, +1h, +1/+3/+5 sessions) exist; the three
session horizons resolve through `target_trading_session_date()` — real
NSE sessions, never calendar days — targeting that session's real 15:30
IST close.

Invalidation is scoped to `PRE_BREAKOUT_COMPRESSION` only, because that
is the one pattern whose documented `invalidate_if` geometry a single
static level can honestly represent. Everywhere else it returns
`UNKNOWN` rather than manufacturing a rule — exactly what Section 6
demands. `test_outcome_horizons.py` covers all seven required cases,
including the whipsaw case (a window that touches both levels is
reported as the failed setup it structurally was, never overstated).

## 6. Pattern aggregation

Now a real product capability rather than a library function. Live
output at the time of writing:

```
total_observations                  44
observations_with_named_pattern      6
observations_without_named_pattern  38   (predate the `pattern` field)
derivatives_evidence_observations   44
price_only_observations              0
derivatives_history   DERIVATIVES_HISTORY_AVAILABLE
```

| Pattern | Observed | Follow-through | No follow-through | Failed | Pending |
| --- | --- | --- | --- | --- | --- |
| FUTURES_STRUCTURE | 2 | 0 | 0 | 0 | 2 |
| OI_MIGRATION | 1 | 0 | 0 | 0 | 1 |
| PRE_BREAKOUT_COMPRESSION | 2 | 0 | 0 | 0 | 2 |
| RELATIVE_STRENGTH | 1 | 0 | 0 | 0 | 1 |

**This proves nothing yet, and the product says so first.** Six named
observations, zero determined outcomes. The surface leads with a
`SMALL SAMPLE` disclaimer naming the real denominator and the 38
excluded observations, per Section 25.

One honesty check worth recording: `derivatives_evidence_observations:
44` rests on the field's `True` default for the 38 legacy rows. That was
verified as **factually grounded, not a default artifact** — all 41
pre-existing observations carry a real `selected_right` and
`selected_strike`, so a real contract genuinely was evaluated for each.

## 7. Whole-market scan metrics

A real, complete scan of the full intended universe, run live this
session (job `ac4b640f90dd`, scan `8fe77965e9cc…`). Market was closed
(18:10 IST), so this ran against the last session's real close data —
correctly reported throughout as `MARKET_CLOSED_LATEST_DATA`.

| Metric | Value |
| --- | --- |
| Universe | **210** F&O-eligible equities (`upstox_instrument_master.NSE_FO_equity_underlyings`) |
| Total runtime | **476.4 s** |
| Stage 1 runtime | **7.1 s** |
| Stage 2 runtime | **468.9 s** |
| Stage 1 attempted / successful | 210 / **210** (0 failed) |
| Stage 2 attempted / successful | 30 / **29** (1 failed) |
| Truncated by survivor cap | **175** |
| Failed symbols | 1 — CIPLA, `INSUFFICIENT_HISTORY` |
| Stale symbols | 0 |
| Timeout failures | 0 |
| Timestamp/quality failures | 0 |
| Coverage classification | **HIGH** — 209/210 reliably evaluated |
| Shortlisted | 3 |
| WATCH / EARLY_SETUP / CONFIRMED_SETUP | 0 / **0** / 0 |
| EXTENDED | 0 in results; **1 rejected as already extended** |
| CONFLICT | 0 |
| DATA_INSUFFICIENT | 3 |
| Avg Stage-2 latency | 87.2 s/symbol (≈15.6 s wall at concurrency 6) |

Rejection breakdown: 175 stage-1 screened out, 26 no directional
convergence, 4 insufficient early-stage evidence, 1 already extended,
1 analysis error.

## 8. Scanner quality audit (Section 11)

Inspected manually. The scanner is **not** selecting biggest movers,
highest volume, OI or PCR:

- All three shortlisted names (POWERINDIA, ETERNAL, CROMPTON) are
  mid-caps, promoted on **bucket corroboration count**, then
  alphabetically — no movement or volume magnitude anywhere in the
  ordering.
- Every one carries a **named pattern** (`PRE_BREAKOUT_COMPRESSION`,
  `RELATIVE_STRENGTH`, `FUTURES_STRUCTURE`) and an explicit
  `what_is_missing`.
- All three resolved to `DATA_INSUFFICIENT`, **not** EARLY_SETUP.
  `developing_now` is 0.
- EARLY_SETUP gating verified in code (`early_opportunity.py:328`):
  requires a named pattern plus early timing plus not-late. There is no
  "N signals = setup" rule and no hidden score.

**The system found nothing it would call a developing setup, and said
so.** That is the correct result, and the most important single piece of
evidence in this report.

## 9. Provider status

**RED, and honestly reported as such.** `/api/health` returns
`secondary_provider: "not_wired"`; the UI shows `5paisa UNKNOWN` and
`Dhan UNKNOWN`.

Section 16 determination: **5paisa cannot provide immediate redundancy,
and should not be attempted.** Redundancy needs a second live
credential; `.env` carries only `UPSTOX_ACCESS_TOKEN`. Anything built
against 5paisa or Dhan this sprint could not have been validated against
real data, and would claim a redundancy the system does not have.
`classify_provider_conflict()` already exists, is tested, and correctly
refuses to average disagreeing prints — the missing piece is purely the
second feed. If the operator ever wants redundancy, **Dhan is the
shorter path**: adapter, normalizer and settings already exist; only
`DHAN_CLIENT_ID`/`DHAN_ACCESS_TOKEN` are missing.

## 10. Qwen status and measured latency

All five Section 20 cases exercised over **real HTTP** through the real
adapter and a real `httpx` client against a local stub endpoint:

| Case | Status | Latency | Result |
| --- | --- | --- | --- |
| A — healthy, valid output | `OK` | 13–17 ms | Explanation accepted |
| B — unavailable | `UNAVAILABLE` | 2024 ms | Fail-open |
| C — timeout (2 s budget) | `TIMEOUT` | 2021 ms | Budget respected exactly |
| D — invalid (non-JSON) output | `VALIDATION_REJECTED` ("malformed JSON") | 13 ms | Fail-open |
| E — forbidden output | `VALIDATION_REJECTED` ("prohibited field(s): buy, confidence") | 11 ms | Fail-open |

Every non-A case returns `explanation=None` and the UI falls back to the
deterministic explanation. Verified live in the browser: "Explain
simply" rendered *"AI explanation unavailable — showing TIRE's evidence
summary"* followed by the full deterministic In simple terms / Why it
matters / What's missing / What confirms it / What invalidates it block,
footed with *"The deterministic engine is the authority."* The UI never
froze.

### QWEN HARDWARE LIMITED

LM Studio is **not running**, and the endpoint is unreachable. At the
previously measured ~0.13 tokens/sec this CPU cannot produce a
600-token response inside any sane budget (that is ~77 minutes against
an 8 s timeout), so even with LM Studio up, Case C is the realistic
live outcome. Per Section 21 this was documented rather than chased. **A
deterministic TIRE without Qwen is the working configuration today, and
it is fully usable.**

## 11. UX and UAT results

Real headed-browser session against the live server.

| Screen | Result |
| --- | --- |
| HOME | Renders; nav, status badges, honest `5paisa UNKNOWN` / `Dhan UNKNOWN` |
| DISCOVER | Coverage block renders universe, stage counts, cap truncation, classification |
| RELIANCE | `CONFLICT` → `NO_TRADE`; "evidence matrix convergence is CONFLICT — no coherent bias to act on"; contract unusability surfaced |
| KAYNES | `DATA_INSUFFICIENT`; **"Contract is not usable for reliable options research, no sufficiently liquid option candidate exists for this bias"** — exactly the Section 14 case; CONFIRM IF / INVALIDATE IF both rendered |
| NIFTY | `WATCH`, evidence MODERATE — a third distinct, honest state |
| EXPLAIN SIMPLY | Deterministic fallback, correctly labelled, ~2 s |
| HISTORY | Renders; today's three new observations at top, all PENDING |
| PATTERN HISTORY (new) | Disclaimer first, counts table, all three segment dimensions, closing disclaimer |
| Console errors | **Zero**, across the whole session |

**Mobile: not fully verified.** The extension's `resize_window` did not
change the captured viewport, so a true 390 px render could not be
exercised. What *was* verified: `meta viewport width=device-width`,
`@media (max-width: 900px)` collapsing `.grid-2` to one column, and the
wide option-chain table correctly wrapped in a `.chain-scroll`
`overflow-x: auto` container. That is structurally correct but is **not
the same as a confirmed mobile pass**, and is scored CONDITIONAL.

## 12. Safety audit

| Check | Result |
| --- | --- |
| `place_order` / `cancel_order` / `modify_order` / `transaction_type` / `execute_trade` / broker order endpoints in `app/` | **Zero hits** |
| Safety lock | Runtime fail-closed — `assert_broker_execution_disabled()` called at startup (`main.py:150`), raises if ever set true; covered by two test suites |
| `/api/health` | `broker_execution: "impossible"` |
| Tracked secrets | None — only `.env.example`; `.env` ignored at `.gitignore:12` |
| Model files / screenshots / debug artifacts tracked | None |
| `subprocess` / `os.system` / `eval(` / `exec(` in `app/` | **Zero hits** |

## 13. Performance

| Measurement | Before | After |
| --- | --- | --- |
| Historical replay (100 bars / 4 sessions) | ~308 s | **~11.2 s** (prior sprint; verified not regressed) |
| Whole-market Stage 1 (210 symbols) | not previously measured | **7.1 s** |
| Whole-market Stage 2 (30 symbols) | ~551 s total (30 symbols, prior partial run) | **468.9 s** |
| Full scan total | 551 s | **476.4 s** |

Stage 2 is the real bottleneck at ~15.6 s/symbol wall. **It was
deliberately not "optimised"** — the only available levers would have
been reducing evidence, using stale data, or silently shrinking the
universe, all of which Section 26 forbids. The survivor cap is the
honest, already-surfaced mitigation.

## 14. Test results

```
pytest -q            2032 passed, 1 warning in 87.81s
ruff check .         All checks passed!
mypy app --strict    Success: no issues found in 160 source files
```

2019 → 2032 (+13 new pattern-view tests; net of the guard-test update).

## 15. Git commits

- `feat: expose historical pattern intelligence as a product capability`
- `docs: final 95% product audit, scorecard and completion report`

## 16. Remaining limitations

1. **No provider redundancy (RED).** Single feed; conflict detection
   exists but is never invoked live. Blocked on a credential.
2. **Stage-2 depth capped at 30.** 175 matching names went unanalyzed in
   the measured run. Surfaced, not hidden.
3. **Historical sample is tiny.** 6 named observations, 0 determined
   outcomes. Machinery complete; evidence needs elapsed time.
4. **Invalidation covers one pattern.** `PRE_BREAKOUT_COMPRESSION` only;
   `UNKNOWN` elsewhere by design.
5. **Qwen is hardware-limited and currently down.**
6. **Mobile viewport unconfirmed** (see §11).
7. **News is single-sourced** and reports YELLOW.
8. **F&O ban list is `UNKNOWN`** — no verified source wired, stated
   rather than guessed.

## 17. Deferred work

5paisa endpoint build-out, GIFT Nifty, BSE dual listing, Level-2/tick
data, ML/prediction, broker execution, paid vendors, distributed
infrastructure, DB migration, full UI redesign, larger local models,
multi-agent orchestration — all per Section 39.

## 18. Final release decision

# CONDITIONAL GREEN

TIRE performs the complete intended loop on real market data — live
scan → research candidates → evidence validation → state → why → what's
missing → what confirms → what invalidates → contract quality →
optional AI explanation → research observation → historical outcome →
pattern aggregation → research journal — and it does so **without a
single manufactured claim**.

The most telling evidence is not a passing test. It is that a real
210-symbol scan of the live market produced **zero EARLY_SETUPs**, three
`DATA_INSUFFICIENT` names, one rejection for being already extended, and
a plain statement that the option contract on KAYNES is not usable. A
system built to impress would have found opportunities. This one found
none and explained why.

It is not unqualified GREEN because provider redundancy is genuinely
absent, Stage-2 depth is genuinely capped, and the historical sample is
genuinely too small to learn from yet. None of those is hidden, and none
is a correctness or safety defect.

**Usable today as a personal Indian-market research backbone.**
