# TIRE — Final Market-Backbone Release Report

Session: 17–18 September 2026 (IST). Branch: `phase-3-historical-validation`.
Starting commit: `02803b1`. Ending commit: `51e305e`.

Every number in this report was measured this session against real data:
the live Upstox API on the real configured token, the real locally
persisted candle history, a real headed Chrome session, and the real local
LM Studio endpoint. Where something could not be measured, this report
says so instead of estimating.

---

## 1. Initial baseline

The brief described the system as: 210/210 Stage 1 in ~7.1 s, Stage 2
~468.9 s for 29/30 symbols, 175 Stage-1 candidates never deep-analyzed,
~6 historical observations, 2000+ passing tests, and an unverified 390 px
mobile viewport.

**Two of those figures were already stale at the start of this session.**
Commits `d9bb873` and `02803b1` (both already on the branch) had removed
the fixed Stage-2 cap of 30 in favour of a measured provider rate budget,
and cut the whole-market scan from 476 s to ~85–104 s by fixing CPU
bottlenecks in the JSONL stores. The audit below therefore starts from
what the code actually did, not from the brief's snapshot.

Verified baseline on arrival (uncommitted work included):
`pytest 2067 passed`, `ruff` clean, `mypy --strict` clean.

Also found on arrival: an **uncommitted, unfinished** historical-dataset
builder (`app/orchestration/replay_dataset.py`, two scripts, one test
file) plus a built 15-symbol dataset. It was completed, corrected and
committed as part of this session.

---

## 2. Audit findings

| # | Finding | Where | Severity |
| --- | --- | --- | --- |
| 1 | A replay outcome counted **any** touch of the invalidation level within the whole +5-session window as `FAILED_SETUP`, even when the confirmation level was crossed days earlier | `pattern_aggregation.outcome_for_replay_observation()` | **P0** (corrupts the learning signal) |
| 2 | Historical sample far too small to compare anything against (15 symbols, one built dataset, ~6 live observations) | `data/historical_replay`, live outcome store | **P1** |
| 3 | The dataset existed but **no product surface could reach it** — Pattern History fetched only the live store | `index.html` `runPatternAggregation()` | **P1** |
| 4 | `renderDashboard()` ran ~25 panel renderers with no isolation: one throw blanked every later panel and skipped `scheduleRefresh()` | `index.html` | **P1** |
| 5 | `snap.duration_seconds.toFixed(1)` throws on null; three timing figures used `(x \|\| 0)`, showing a missing measurement as a real `0.0s` | `index.html` | P2 |
| 6 | `.search-row` never wrapped: at 387 px the RESEARCH HISTORY row laid out to 711 px and `LOAD HISTORY` sat off-screen | `index.html` CSS | P2 |
| 7 | Wide tables widened the whole document at phone width (pattern counts → 636 px, research history → 839 px) | `index.html` CSS/JS | P2 |
| 8 | Qwen prohibited-claim detection checked keys plus a few phrases only; "a strong buy signal", "expected return", "high probability", "predicts", "smart money", "price target" all passed | `qwen_narrative.validate_qwen_payload()` | **P1** (AI safety surface) |
| 9 | The deterministic fallback was labelled "AI explanation based on TIRE evidence" — deterministic text presented as AI output; the Qwen path never said it was Qwen | `index.html` | P2 |
| 10 | Nothing mechanically prevented a provider adapter from calling an order endpoint | provider adapters | P2 (defence in depth) |
| 11 | Chip touch targets 21–22 px tall at phone width | `index.html` CSS | P3 |

Deliberately **not** changed: Stage-1 thresholds, the decision engine,
the evidence matrix, ranking semantics, freshness rules, and the
contract-quality gate. No second decision engine was introduced and no
hidden score was added.

---

## 3. P0 / P1 / P2 classification and disposition

- **P0 — 1 finding.** Fixed (§10).
- **P1 — 4 findings** (2, 3, 4, 8). All fixed.
- **P2 — 5 findings** (5, 6, 7, 9, 10). All fixed.
- **P3 — 1 finding** (11). Fixed.

No finding was deferred. No finding was closed by weakening a check.

---

## 4. Whole-market scan (live, real Upstox)

Two real runs of `GET /api/research/daily` (the same function Discover
calls), 17 Sep 2026 post-close, NSE status `CLOSING_END`:

| | Run 1 (API, 23:15 IST) | Run 2 (browser, 23:39 IST) |
| --- | --- | --- |
| Universe | 210 F&O-eligible equity underlyings | 210 |
| Stage 1 analyzed | **210 / 210** (0.78 s) | **210 / 210** (0.7 s) |
| Stage 1 candidates | 196 | 196 |
| Stage 2 analyzed | **196 / 196** | **196 / 196** |
| Stage 2 deferred | **0** | **0** |
| Failed symbols | **0** | **0** |
| Stale symbols | **0** | 0 |
| Avg Stage-2 latency | 1.46 s/symbol | 2.8 s/symbol |
| Total wall clock | **51.3 s** | **97.4 s** |
| Remaining rate-budget capacity | 566 further Stage-2 analyses | — |
| Coverage | `HIGH — 210/210 reliably evaluated` | same |

Run 2 is slower for a stated reason: the 7 B Qwen model was loading on
the same machine during it. Both runs are reported; neither is hidden.
The honest band for this workload on this machine is **~50–100 s**.

Outcomes of run 1 (196 deep analyses): 141 `NO_DIRECTIONAL_CONVERGENCE`,
23 `ANALYZED (EARLY_SETUP)`, 15 `ANALYZED (NO_TRADE)`, 7
`ANALYZED (DATA_INSUFFICIENT)`, 5 `ANALYZED (WATCH)`, 3 `SHORTLISTED`,
2 `POOR_CONTRACT_QUALITY`, 14 `INSUFFICIENT_EARLY_STAGE_EVIDENCE`
(Stage-1 rejections). Movement stage: 188 DEVELOPING, 8 ADVANCED.

---

## 5. Stage-1 candidate selection

Audited, not rewritten. Stage 1 uses only real quote-derived facts
(day change, distance from the day's own VWAP, position in the day's
range, buy/sell quantity imbalance, session range) to emit **bucket
memberships**, never a score. The hard `_already_extended_intraday()`
exclusion is checked first and independently, which is what actually
prevents "biggest mover of the day" from being treated as early-stage.
Volume is never treated as directional; order-flow imbalance is a
separate, explicitly non-directional bucket.

Ordering (used only when capacity is short) is, in order: movement stage
(DEVELOPING before ADVANCED), count of **independent evidence
dimensions**, count of bucket labels, then a stable per-session hash.
The hash rotates ties across sessions so no name is permanently
unreachable, and cannot move a candidate past one with more real
corroboration.

**Honest finding, not fixed:** Stage 1 admits **196 of 210** names
(93%). As an information-reduction layer that is weak. It costs coverage
nothing today because all 196 are deep-analyzed within the provider
budget, and tightening the thresholds would be a change to what the
scanner considers interesting — a research decision, not a defect fix, and
explicitly out of scope for this gate. It is recorded as a limitation
(§18) rather than quietly tuned.

---

## 6. Stage-2 coverage

`STAGE_1_ANALYZED / STAGE_1_FAILED / STAGE_1_SKIPPED`,
`STAGE_1_CANDIDATE / STAGE_1_REJECTED`, and
`STAGE_2_ANALYZED / STAGE_2_FAILED / STAGE_2_SKIPPED_CAPACITY /
STAGE_2_DEFERRED_RATE_BUDGET / STAGE_2_NOT_REACHED` are recorded for
**every** universe symbol and exposed per symbol by the API.

Verified in the browser, rendered verbatim:

```
210 requested (full F&O equity universe)
210 Stage 1 analyzed
196 Stage 1 candidates (matched a real discovery bucket)
196 Stage 2 analyzed
0 deferred -- every Stage 1 candidate was deep-analyzed this run.
Per-symbol pipeline (210 symbols) -- what happened to every name
```

The per-symbol table rendered all **210** rows. Deferral, when it
happens, carries its own reason text stating it is a capacity/budget
decision and **not** a finding about that name.

Capacity is bounded by Upstox's published per-API 30-minute limit
(2000 requests) through a process-wide request ledger, with 300 requests
reserved for interactive use. After run 1 the ledger still allowed 566
further Stage-2 analyses, so the cap did not bind.

---

## 7. Performance before / after

| Metric | Brief's baseline | Measured now |
| --- | --- | --- |
| Stage 1, 210 symbols | 7.1 s | **0.78 s** |
| Stage 2 | 468.9 s for 30 symbols | **50.4 s for 196 symbols** |
| Stage 2 per symbol | ~15.6 s | **1.46 s** |
| Deep-analyzed names | 29–30 | **196** |
| Not deep-analyzed | 175 | **0** |
| Total scan | 476.4 s | **51.3 s** (97.4 s under concurrent load) |

The Stage-2 speed-up itself landed in `d9bb873`/`02803b1` (before this
session) and is re-verified here on a live run. **No new optimization was
needed this session**, and none of the forbidden shortcuts was used: no
stale data, no removed evidence, no reduced universe, no dropped
option-chain checks, no weakened freshness, no raised concurrency
(unchanged at 6).

---

## 8. Historical dataset size

| | Before | After |
| --- | --- | --- |
| Symbols | 15 | **45** |
| NSE industries | ~8 | **16** |
| Sessions replayed | 1,860 | **5,580** |
| M15 bars evaluated | 46,500 | **139,497** |
| Raw per-bar observations | 3,427 | **8,826** |
| Episodes (counting unit) | 1,032 | **2,610** |
| Determined outcomes (+5 sessions) | 894 | **2,280** |
| Build time | 899.8 s (5 workers) | **1,228 s (7 workers)** |
| Symbols failed | 0 | **0** |

Backfill: 139,497 real candles plus the NIFTY 50 context series in
**28 s**, no window skipped. Window: 17 Mar → 16 Sep 2026, identical for
every symbol.

Episodes, not bars, are the counting unit: contiguous bars of the same
session/pattern/direction collapse into one episode represented by its
**first** bar (the earliest instant TIRE could have known), with the bar
count kept as provenance. Counting the raw 8,826 would have inflated
every figure with autocorrelated duplicates.

Each row keeps the two halves apart, exactly as Section 10 requires:
`knew_then` (symbol, timestamp, pattern, direction, timing stage,
research state, supporting / conflicting / neutral / missing evidence,
the pattern's own confirm and invalidate text with real levels, market
context, sector context, spot) and `happened_after` (all five horizons —
30 m, 1 h, 1 d, 3 d, 5 d — each with `data_sufficient`, confirmation,
invalidation, first-crossing timestamps and excursions, plus the
+5-session reference status), plus `provenance` (candle source, provider,
episode bars, dataset `as_of`).

---

## 9. Pattern statistics

`GET /api/research/patterns?source=replay`, read from the live product:

```
PRE_BREAKOUT_COMPRESSION   observations 2610
  follow-through observed  1179
  no follow-through           2
  failed setup             1099
  pending                   160
  insufficient data         170
  symbols                    45
```

Segmented by DIRECTION (1,368 bullish / 1,242 bearish), TIMING_STAGE
(2,608 EARLY_SETUP), EVIDENCE_COMPLETENESS (2,610 PRICE_ONLY), MARKET
CONTEXT (1,120 NIFTY_DOWN / 763 NIFTY_FLAT / 727 NIFTY_UP) and SECTOR
(16 real NSE industries).

No probability, win rate, expected return or confidence is computed
anywhere — the response model has no float field for one. The
sample-size note renders **before** any count and states that the counts
are not a probability or a validated edge, that no significance test was
run, that observations within a pattern share market periods and are not
independent trials, and that a per-segment count can still be small.

**Two honest caveats, stated in the product:**

1. Only **one** pattern exists in this history. With no historical
   option chain or futures data, the unchanged classifier never selects
   derivatives-dependent patterns; `PRE_BREAKOUT_COMPRESSION` is the
   only one this data can produce. This is missing history, not an
   observation that nothing else occurs.
2. At +5 sessions only **2 of 2,610** episodes ended with neither level
   touched, so that horizon is near-saturated and is a weak
   discriminator. The shorter horizons carry the real signal: at +1
   session, 661 episodes crossed only the confirmation level, 673 only
   the invalidation level, 557 confirmed first, 478 invalidated first,
   59 were same-bar, and 46 touched neither. All five horizons are stored
   per row so this is inspectable rather than asserted.

---

## 10. Outcome correctness (the P0 fix)

`outcome_for_replay_observation()` checked invalidation **first**, over
the entire +5-session window. Any touch of the invalidation level made
the episode a `FAILED_SETUP`, even if the confirmation level had been
crossed days earlier. Over five sessions a compression range is usually
crossed in both directions, so the rule manufactured failures.

Measured on the real dataset: **1,694 of 2,440 determined episodes (69%)
crossed both levels.** The old rule called all 1,694 failures. By first
crossing:

| | Count |
| --- | --- |
| Confirmed first | **827** |
| Invalidated first | **811** |
| Both inside one M15 bar (unresolvable) | **56** |

So the old rule mislabelled **827 episodes — 34% of the determined
sample**. The dataset headline moved from 74.6% FAILED (770/1,032) to
45% follow-through / 42% failed.

The fix adds `first_confirmation_at` / `first_invalidation_at`, computed
bar by bar with the **same** predicates as the whole-window
determination, so the two can never disagree about *whether* a level was
crossed — only add *when*. Confirmation and invalidation remain two
separate facts (Section 12). The reference status now reads their order,
and a same-bar double crossing returns `INSUFFICIENT_OUTCOME_DATA`
because the order is genuinely unknowable at M15 granularity — it is not
guessed in either direction. A confirmation followed later by a reversal
stays `FOLLOW_THROUGH_OBSERVED`, with the later crossing still visible in
the per-horizon facts.

No lookahead was introduced: the outcome window still begins at the
observation instant, and the observation itself is still built only from
bars that closed before it (verified on a real row — the 04:15 observation
used spot 1365.6, the close of the completed 04:00 bar, and its
invalidation level 1374.0 was that bar's high).

This is visible in the product. A real KAYNES row now reads:
`invalidated 8 Jun, 13:30` before `confirmed 15 Jun, 10:30` → **FAILED
SETUP**; a later row reads `confirmed 11 Jun, 10:15 · not invalidated` →
**FOLLOW THROUGH OBSERVED**.

---

## 11. Provider status

Audited before building anything, as Section 13 requires.

- **5paisa:** no adapter, no dependency, no credential, no token. The
  only references are documentation. **Nothing was built.**
- **Dhan:** an adapter exists (`dhan_provider.py`) and is correct-looking
  read-only code, but the dashboard never constructs it and
  `DHAN_ACCESS_TOKEN` / `DHAN_CLIENT_ID` are unset.
- **Configured credentials:** `UPSTOX_ACCESS_TOKEN` only (verified live,
  200 OK).

Per the brief's own instruction — "If credentials are NOT available: do
not fabricate a second provider" — no second provider was wired and no
synthetic redundancy was added. `classify_provider_conflict()` exists and
is correct but remains uncalled; `/api/health` reports
`secondary_provider: "not_wired"`, and the UI shows `5paisa UNKNOWN` and
`Dhan UNKNOWN`. **Provider redundancy stays RED.**

---

## 12. Qwen status

`python -m scripts.qwen_operational_check` (new, reusable) drives the
**production** adapter over real HTTP. Measured with LM Studio running and
`qwen2.5-coder-7b-instruct` loaded:

| Case | Status | Latency | Fails safe |
| --- | --- | --- | --- |
| HEALTH | `QWEN_READY` | 149 ms | — |
| A — real endpoint | `TIMEOUT` | 20,302 ms | yes |
| A — success path (stub through the real parser/validator) | `OK` | 390 ms | — |
| B — unavailable | `UNAVAILABLE` | 2,059 ms | yes |
| C — timeout | `TIMEOUT` | 1,023 ms | yes |
| D — malformed | `VALIDATION_REJECTED` (malformed JSON) | 58 ms | yes |
| E — prohibited | `VALIDATION_REJECTED` (prohibited claim) | 5 ms | yes |

Nothing raised; every failure path leaves the deterministic explanation
in place. The API confirms it: `POST /api/research/explain` with Qwen down
returned `{"ok": false, "status": "UNAVAILABLE", "fallback":
"deterministic"}` in 2.1 s.

**Hardware limit, measured not assumed:** 2 completion tokens in 43.8 s
(~0.05 tok/s) for a trivial prompt. A 600-token explanation is therefore
impractical, and case A times out in normal use. Per the brief, CPU
inference was not pursued.

**Prohibited-output hardening (P1).** The validator blocked prohibited
keys plus a few phrases, but under allowed keys a response saying "a
strong buy signal … high probability … price target … smart money" was
**accepted**. It is now rejected as `prohibited claim`, along with
"expected return", "predicts", "institutions are accumulating", "will
rally", "traders want to", "you should buy", and confidence/likelihood
phrasing. Patterns are phrase-shaped, not bare words, because the
deterministic evidence Qwen must paraphrase legitimately contains
"buy/sell quantity imbalance" and "research confidence WEAK" — 15 tests
pin both directions.

**AI UX (Section 17).** The deterministic fallback used to close with "AI
explanation based on TIRE evidence", labelling deterministic text as AI
output. Verified in the browser, it now reads: `Qwen unavailable —
deterministic explanation shown (timeout)` … `Written directly from
TIRE's deterministic evidence -- no AI was used for this explanation.`
The Qwen path reads `Simplified by local Qwen 2.5 (<model>)` and states
that Qwen only re-worded existing evidence and did not find the setup.

---

## 13. Browser UAT (desktop, real headed Chrome)

Real session against `http://127.0.0.1:8010`, live token.

| Surface | Result |
| --- | --- |
| HOME | Market status, Developing Now, Events, Data Quality, Open Decisions all render |
| DISCOVER | Live progress (`41/196 checked` → `Scan complete`), coverage block, 210-row pipeline table |
| RELIANCE | Rendered, `latency=1.19s`, journal `PERSISTED`, no panel errors |
| KAYNES | Rendered, `latency=2.41s`, contract-quality warning intact |
| NIFTY | Rendered, `latency=2.26s` |
| EXPLAIN SIMPLY | Deterministic fallback with correct provenance label |
| HISTORY | 56 real observation rows |
| PATTERN HISTORY | Replay source, 2,610 observations, sample note first, segments |
| COMPARE HISTORICAL OBSERVATIONS (new) | 10 KAYNES rows with knew-then levels beside what happened after |
| Console | **0 errors** across the whole session |

Candidate cards render WHY THIS IS INTERESTING, MAIN BLOCKER, CONFIRM
WHEN and INVALIDATED IF — Section 22's "if TIRE says WATCH, do I
understand why" is satisfied on real output.

---

## 14. Mobile UAT (genuine 387 px viewport)

The previous session could not verify this. The cause was found: the
Chrome window is maximized and the automation side panel consumes ~400
CSS px, so `resize_window` cannot produce a phone-width viewport (it
reports success and changes nothing). The real page was therefore loaded
in a **same-origin 390 px frame** — real rendering, real media queries,
full DOM access — not CSS inspection. Measured viewport: **387 × 841 CSS
px**.

**Three real defects found:**

1. Document scrolled to **711 px** against a 387 px viewport. Cause:
   `.search-row` never wrapped; `LOAD HISTORY` was off-screen and
   unreachable.
2. Pattern-count tables pushed the page to **636 px**.
3. RESEARCH HISTORY's 9-column table pushed the page to **839 px**.

**After the fixes, measured at the same viewport with every section
expanded (143 `<details>` open), analysis + pattern history + 50
observation rows + research history all loaded:**

| Check | Result |
| --- | --- |
| Horizontal page overflow | **none** (scrollWidth 372 = clientWidth 372) |
| Unclipped overflowing elements | **0** |
| Buttons off-screen | **0 of 102** |
| Touch targets under 28 px | **0** (was 19 at 21–22 px) |
| Tables usable | **28 of 28** scroll inside their own container, headers and rows intact |
| JS errors (captured in-page) | **0** |
| Cards / text | Nav, badges and cards wrap; text readable (verified visually) |

Desktop was re-verified at 1440 px after the CSS change: no overflow,
tables render as normal tables, and all eight filter rows stay on a
single line — no regression.

---

## 15. Safety audit

| Check | Result |
| --- | --- |
| Order placement / modification / cancellation | **None.** Repo-wide search finds only docstrings saying such APIs are deliberately not wired |
| Non-GET routes | 10, all local research/journal/analysis writes; none touches a broker |
| Broker mutation via provider adapters | **None.** New AST test pins every `_get`/`_post` path to a read-only market-data allowlist (14 paths), rejecting order/portfolio/fund/margin/gtt/position/holding and any path not statically resolvable. Negative-checked against a synthetic `/v2/order/place` and a dynamic path |
| Startup fail-closed | `Settings.assert_broker_execution_disabled()` raises on a true `BROKER_ORDER_EXECUTION_ENABLED`; covered by 5 tests including the real lifespan |
| Safety lock | `SAFETY_LOCK.txt` present and unchanged |
| Tracked secrets | **None.** `.env` is ignored; no JWT-shaped string in any tracked file |
| Tracked model files | **None** (no `.gguf` / `.safetensors` / `.bin` / `.pt` / `.onnx`) |
| Tracked debug artifacts / data dumps | **None.** `/data/` is git-ignored; the 13 MB dataset and 139 k candles stay local |
| Arbitrary shell / eval | **None** (`subprocess`, `os.system`, `shell=True`, `eval(`, `exec(` absent from `app/` and `scripts/`) |
| LLM authority | Qwen cannot set state; forbidden output is rejected before display |

---

## 16. Tests

| Gate | Result |
| --- | --- |
| `pytest -q` | **2106 passed** (baseline 2067; +39 this session) |
| `ruff check .` | **All checks passed** |
| `mypy app --strict` | **Success: no issues found in 163 source files** |
| Dashboard JS syntax (`node --check`) | **OK** |
| Real whole-market scan | 2 live runs, 196/196 deep-analyzed, 0 failed |
| Real historical replay | 45 symbols, 139,497 bars, 0 failures |
| Real browser UAT | desktop + genuine 387 px, 0 console errors |
| Qwen five-case validation | all pass, all fail safe |
| Safety audit | clean (§15) |

One pre-existing test needed updating, not weakening: a guard pins the
exact number of `fetch(apiUrl(...))` call sites (16 → 17) so that no
`fetch("/api...")` bypasses `apiUrl()`. The new call routes through
`apiUrl()`, which is the property under test.

The +39 reconciles exactly: provider endpoint allowlist 14
(parametrized), Qwen prohibited/permitted prose 15 (parametrized),
historical-dataset tests 7 (5 inherited from the uncommitted work, 2 new
for the row reader), first-crossing outcome ordering +2 net (3 new
replacing 1 that encoded the old whipsaw rule), panel isolation under
Node 1.

---

## 17. Git commits

```
1232734  fix(P0): a replay outcome is decided by WHICH level was crossed FIRST
191a5f4  feat(P1): a real historical research dataset -- 45 symbols, 2,610 episodes
019631b  fix(P2): panel isolation, honest empty values, and a real 390px viewport
51e305e  fix: reject forbidden AI claims in prose; pin provider paths to read-only
```

No history was rewritten; every previously validated commit is intact.
Nothing outside `/data/` (git-ignored) was left untracked.

---

## 18. Remaining limitations

1. **No provider redundancy (RED).** One feed. Blocked on a credential,
   not on code. Nothing in the live path cross-validates a print, and
   `PROVIDER_CONFLICT` cannot fire.
2. **Qwen cannot generate on this hardware.** ~0.05 tok/s measured; the
   "available" path times out in practice. Every failure path is safe and
   the deterministic explanation is complete, so no capability is lost —
   but the AI feature is effectively decorative today.
3. **One historical pattern only.** `PRE_BREAKOUT_COMPRESSION`, because
   no historical option-chain or futures data exists. Derivatives-
   dependent patterns remain unevaluable from history.
4. **One 6-month window, 45 symbols.** The counts describe 17 Mar –
   16 Sep 2026 only. Observations share market periods and are not
   independent trials.
5. **+5 sessions is near-saturated** (2 of 2,610 untouched), so it is a
   weak reference horizon. The shorter horizons are stored and are more
   informative.
6. **56 episodes are genuinely unresolvable** (both levels crossed inside
   one M15 bar). Reported as `INSUFFICIENT_OUTCOME_DATA`; finer
   granularity than M15 would be needed.
7. **Stage 1 admits 93% of the universe.** Weak as an attention filter;
   costs nothing today because every candidate is deep-analyzed, but it
   would bind if the universe or per-symbol cost grew (§5).
8. **Rate budget counts this process only.** Requests made with the same
   token elsewhere are invisible to the ledger; a 300-request reserve is
   held back for that reason.
9. **News has no historical archive and one live source** (YELLOW).
10. **Scan timing varies with machine load** (51.3 s vs 97.4 s measured).
11. **F&O ban list is unverified** (`FNO_BAN_STATUS_UNKNOWN`) — stated on
    the coverage panel rather than guessed.
12. **Mobile was verified in a 390 px same-origin frame**, not a physical
    phone or a resized OS window. Layout, media queries and JS are real;
    touch behaviour and mobile-browser chrome are not covered.

---

## 19. Deferred features

Untouched by design (Section 27): ML, price prediction, automated
trading, broker execution, GIFT Nifty, BSE dual listing, Level-2 data,
tick infrastructure, paid data vendors, large model downloads,
distributed infrastructure, UI redesign. Also deferred: a second provider
adapter (blocked on credentials), historical derivatives data (does not
exist to fetch), and Stage-1 threshold tuning (a research decision, not a
release-gate fix).

---

## 20. Final scorecard

See `docs/FINAL_PRODUCT_SCORECARD.md` (rewritten this session, with the
stale 15 Sep rows superseded). Summary of 23 rows: **19 GREEN**, **3 CONDITIONAL**
(news/context, Qwen, and pattern-aggregation *evidence* — its capability
is GREEN), **1 RED** (provider redundancy).

---

## 21. Final release decision

The gate question was: *can TIRE reliably perform the complete research
loop on real data, explain why something deserves attention,
transparently show what was and wasn't analyzed, learn from historical
observations, and remain safe and useful when AI, provider or data
sources fail?*

Measured, point by point:

- **Complete loop on real data** — yes. 210/210 screened, 196/196
  deep-analyzed, 0 failed, 51.3 s, twice.
- **Explain why** — yes. Every candidate carries pattern, why it is
  interesting, main blocker, confirmation and invalidation, with
  supporting/conflicting/missing evidence separated.
- **Transparent coverage** — yes. Every one of 210 symbols has a stage
  record; deferrals are visible and labelled as capacity decisions, and
  the run that deferred nothing says so.
- **Learn from history** — yes, and materially more than before: 2,610
  real episodes with 2,280 determined outcomes, browsable per
  observation, with a P0 mislabelling defect fixed. Still one pattern and
  price-only.
- **Safe under failure** — yes. All five Qwen paths fail safe;
  deterministic output is authoritative and correctly labelled; a failing
  panel no longer blanks the page; no order path exists anywhere.

### CONDITIONAL GREEN — PERSONAL MARKET RESEARCH BACKBONE

Not RED: no correctness or safety defect remains open, and the one P0
found this session is fixed and pinned by tests.

Not unqualified GREEN, for one reason that is genuinely unresolved rather
than unmeasured: **provider redundancy does not exist**, and it cannot be
built honestly without a second credential. Every print the system shows
comes from a single feed, and TIRE cannot currently tell a bad print from
a real move. Two further conditionals (Qwen unusable on this hardware,
one price-only historical pattern) are limits of the environment and the
available data, not of the product, and both are stated on the surfaces
that depend on them.

The system is usable today as a personal Indian-market research
backbone, with those limits known and visible.
