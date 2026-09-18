# TIRE — 18 September 2026 release report

Session: 18 September 2026 (IST), market closed (17:20–19:xx IST, NSE
closes 15:30). Branch: `phase-3-historical-validation`. Starting commit:
`0dec102`.

Every number in this report was measured this session: against the real
Upstox API on the real configured token, the real locally persisted
candle store, a real headed Chromium session at 1440×900 and at a real
390×844 touch viewport, and the real local LM Studio endpoint. Where
something could not be measured, this report says so.

---

## 1. Baseline on arrival

Verified, not taken from the previous report:

| Check | Result |
| --- | --- |
| `pytest` | 2,106 passed |
| `ruff check .` | clean |
| `mypy --strict app` | clean (163 files) |
| Historical dataset | 45 symbols, 139,497 bars, 5,580 sessions, 2,610 episodes, **one** pattern |
| Scorecard | CONDITIONAL GREEN (17 Sep), blocked on provider redundancy and Qwen hardware |

The previous session's own scorecard named the two data-side limits to
attack next: the historical sample is **one pattern**, price-only, over
**one window**. That is where this session started.

---

## 2. What the audit found

### 2.1 P0 — the M15 trend row called a falling tape BULLISH

`app.domain.technical.ema_alignment` reports the EMA values as a NUMERIC
SEQUENCE in period order. With `[9, 21, 50]`:

- `ASCENDING` = EMA9 < EMA21 < EMA50 — the faster averages **below** the
  slower ones, the ordering a **falling** series produces.
- `DESCENDING` = EMA9 > EMA21 > EMA50 — the ordering a **rising** series
  produces.

The module's own docstring said this explicitly and warned callers not to
read the label as a direction. All three callers did anyway, and read it
backwards:

| Call site | Was | Meaning |
| --- | --- | --- |
| `evidence_matrix.row_m15_trend` | `ASCENDING` → BULLISH | a falling series reported as bullish evidence |
| `market_regime.classify_market_regime` | `ASCENDING` + above VWAP → `TRENDING_BULLISH` | a falling market labelled a bullish trend |
| `EMAVWAPAlignmentStrategy` | `ASCENDING` + above VWAP → BULLISH setup | the strategy's own documented rule, inverted |

**Measured, not argued.** Over 1,404 real M15 samples drawn from the
local 45-symbol candle store, taking the trailing 50-bar price change at
each sample:

| Sequence label | Samples | Mean trailing 50-bar move | Price had risen |
| --- | --- | --- | --- |
| `ASCENDING` (→ was BULLISH) | 532 | **−1.68%** | 9.8% |
| `DESCENDING` (→ was BEARISH) | 563 | **+1.96%** | 89.3% |
| `MIXED` | 309 | +0.02% | 48.2% |

Why 2,106 tests missed it: every test asserted the enum, and the enum was
self-consistent. The shared strategy fixture file named a series falling
from 100 to 82.6 `BULLISH_CLOSES`, and named a clean uptrend
`CONFLICTING_UPTREND_CLOSES`. The names encoded the defect, so the tests
agreed with it.

**Blast radius.** The M15 trend row and the VWAP row are the two rows in
the `UNDERLYING_PRICE_STRUCTURE` evidence group, both computed from the
same candle series. An inverted M15 row therefore contradicted the VWAP
row whenever price actually trended, sending the group to CONFLICT so it
voted nothing. Two existing tests passed only because of that:

- `test_market_regime_and_transmission_never_appear_in_the_evidence_matrix`
  compared two runs that differed in the CRUDE quote — which feeds the
  macro regime AND the matrix-resident `Global context` row. The
  comparison was confounded; it agreed only because both runs were forced
  to CONFLICT. Rewritten to vary the geopolitical headline alone, plus a
  third run that changes the macro regime through USD/INR, the one macro
  input explicitly excluded from the global-context verdict
  (`contributes_to_verdict=False`) — so real macro context differs while
  every evidence row is untouched, and the decision must still match.
- `test_analyze_exposes_real_reasoning_and_never_fabricates_invalidation_without_a_thesis`
  pinned empty invalidation fields that only existed because the fixture
  landed on CONFLICT → NO_TRADE. Rewritten around the invariant it was
  meant to prove: a level appears if and only if a condition explaining it
  does, and the level must be one of this analysis's own real S/R levels.

**Regression protection.** `tests/research_truth/test_ema_direction_truth.py`
asserts against REAL PRICE MOVEMENT rather than the enum: the strongest
real advance in the repository's own RELIANCE series must not read
BEARISH, the deepest real decline must not read BULLISH, and the row must
agree with the trailing move across the whole sample (a 2:1 majority bar
— this pins the SIGN, which is what was wrong, not the indicator's
quality). Verified to fail against the pre-fix mapping by stashing the
fix and re-running: 3 of 4 tests failed.

### 2.2 P1 — a documented pattern that could never occur

`docs/CURRENT_SYSTEM.md` listed `FAILED_BREAKDOWN_RECLAIM` among TIRE's
named patterns. `classify_development()` could narrate it. Nothing ever
computed the fact behind it: the `failed_breakdown_reclaim` parameter had
no production caller anywhere in the repository and defaulted to `False`.

The pattern was unreachable in the live path and in replay. That is the
direct reason the 2,610-episode historical dataset built the day before
contained exactly one pattern — and the reason its
`InvalidationOutcome` machinery could only ever return `UNKNOWN` for
anything except `PRE_BREAKOUT_COMPRESSION`.

---

## 3. What changed

### 3.1 `app.domain.options.structural_reclaim`

Computes the missing fact and nothing else. No fetch, no new
technical-analysis engine: the same already-fetched M15 candles, the same
IST session aggregation `historical_structure` performs (imported, not
reimplemented), the same `bounded_series(..., as_of=)` no-lookahead
boundary.

The event, stated exactly (bullish side; bearish is the mirror):

1. **Reference** — the lowest low of the 5 completed sessions
   immediately before a candidate break session (at least 3 must exist),
   computed only from sessions strictly earlier than the break.
2. **Break** — that session traded below the reference by ≥ 0.30% **and
   closed below it**.
3. **Reclaim** — a completed session within 3 sessions closed back above
   the reference.
4. **Hold** — every completed session since closed above it, and current
   spot is still above it.
5. **Recency** — the reclaim is within the last 3 completed sessions.

The closing requirement in step 2 is a correction made during this
session, not a threshold tuned to a target. Measured over 5,040
session-close evaluations across the local 45-symbol store, counting any
session that merely *traded* through prior structure as a break fired on
**39.3%** of evaluations — that does not name a structural event, it
describes the tape. Requiring the market to *accept* the break (close
beyond it) gives:

| | Measured |
| --- | --- |
| Reported `OK` | 21.6% of session-close evaluations |
| `CONFLICTING` | 0.8% |
| `INSUFFICIENT_HISTORY` | 0.0% (the store always reaches back far enough) |
| **Distinct events** | **393** across 45 symbols over ~124 sessions (~8.7 per symbol, roughly one every 14 sessions) |

The presence rate exceeds the event rate because one event stays true for
up to three completed sessions after the reclaim.

Integrity properties kept deliberately:

- A whipsaw qualifying on **both** sides returns `CONFLICTING` with no
  direction and no level. Two opposing structural readings of the same
  tape are not evidence for either.
- Too little history returns `INSUFFICIENT_HISTORY`, never `NONE` — "we
  could not look" and "we looked and found nothing" are different facts.
- The still-forming session is excluded: its high, low and close are not
  final facts yet.

### 3.2 Wiring, and the direction gate

The pipeline computes the reclaim beside `historical_structure` and
offers it to `classify_development()` **only** when the detected
direction matches the evidence matrix's own convergence bias — reusing
`market_bias_from_convergence()`, the same mapping stage 14 already uses,
rather than making a second directional judgment. The gate can only
withhold the pattern; it can never create one. Existing pattern
precedence is untouched: `PRE_BREAKOUT_COMPRESSION` is still checked
first.

### 3.3 The reclaimed level becomes a real invalidation level

`FAILED_BREAKDOWN_RECLAIM`'s own `invalidate_if` text is "price loses the
reclaimed level again". That level is now a specific recorded number, so
this is the first pattern besides `PRE_BREAKOUT_COMPRESSION` whose replay
outcomes can reach a genuine INVALIDATED/NOT_INVALIDATED determination
instead of a permanent `UNKNOWN`. A persisted observation re-checks the
direction before recording the level, so a bearish-side reclaim can never
be attached to a bullish thesis.

### 3.4 TIME_WINDOW segmentation

`segment_by_calendar_quarter()` partitions the same pattern counts by the
real IST calendar quarter each observation was made in — Section 30's
"more independent time windows" made inspectable. Calendar quarters
rather than halves-of-whatever-was-collected, so boundaries do not move
when the dataset grows. A test pins that every pattern's per-quarter
counts sum back to its unsegmented total.

### 3.5 UI

- **BREAK AND RECLAIM** block in the research context panel: the real
  level, the break and reclaim dates, the break depth, the sessions held,
  and which of the four statuses applies.
- Phone touch targets: `button, input, select { min-height: 32px }` at
  ≤560px. The earlier pass fixed buttons only; measurement at a real
  390px touch viewport found 17 inputs and selects still 18–22px tall.
- An inline SVG favicon, removing the only console error a real browser
  session produced.

---

## 4. Verification

*(filled in below from the final run)*

---

## 5. Remaining gaps

*(filled in below)*
