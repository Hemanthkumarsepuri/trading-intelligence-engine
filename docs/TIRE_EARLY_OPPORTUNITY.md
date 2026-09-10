# TIRE — Early Opportunity Discovery (final report)

Date: 9 September 2026.
Market session at UAT: **MARKET_CLOSED_LATEST_DATA**. Quality labels
WEAK are expected after the close; they are freshness/session facts, not
a claim that the new classifier is broken.

This is **not** a profitability report. “95%” means research-quality
correctness on defined cases, not win rate.

Architecture map (open beside chat):
`C:\Users\Hemanth Kumar\.cursor\projects\c-Users-Hemanth-Kumar-trading-intelligence-engine\canvases\tire-early-opportunity-architecture.canvas.tsx`

Also: `docs/EARLY_OPPORTUNITY_ARCHITECTURE.md`,
`docs/PROVIDER_CAPABILITY_MATRIX.md`.

---

## A. Existing architecture understood — what was reused

Not redesigned:

- Evidence matrix and anti-double-counting.
- `derive_research_state()` precedence (`docs/research/RESEARCH_STATE.md`).
- Freshness: stale evidence does not vote; missing is not confirmation.
- Two-stage daily scan (batched Stage 1 quotes → survivor cap → Stage 2
  `run_analysis()` with concurrency 6).
- Named development patterns, news no-lookahead, geopolitical
  transmission, dynamic macro-regime classifier.
- Contract quality (liquidity / decay / structure) as a separate
  question from direction.
- `SAFETY_LOCK.txt` and startup `assert_broker_execution_disabled()`.
- Ranked top-N shortlist (`rank_candidates`) kept for compatibility —
  it is no longer the primary UI.

`ALREADY_MOVED` is a **timing/bucket** label. It was **not** added to
`ResearchState`.

---

## B. Early-opportunity architecture — what was added

- `app/domain/options/early_opportunity.py` — research buckets + timing
  + universe tier. No numeric score.
- `PRE_BREAKOUT_COMPRESSION` and `FAILED_BREAKDOWN_RECLAIM` in
  `development.py`.
- `classify_development_evolution()` for snapshot T0→T1 maturity change.
- Daily view buckets over **every Stage-2 gated symbol**, not only top-3:
  `developing_now`, `already_moved`, `confirmed`, `extended`, `conflict`,
  `data_insufficient`, `already_moved_rejections`,
  `zero_valid_early_opportunities`.
- Dashboard **DEVELOPING NOW** (not Top Gainers / not TOP OPPORTUNITIES).
- Personal journal persistence + `POST/GET /api/journal/personal`.

Four questions stay separate: direction, timing, contract quality,
confirmation.

---

## C. Pattern library (deterministic requirements)

| Pattern | Required evidence | Must not claim |
|---|---|---|
| `NONE` | Default / CONFLICT / unusable streams | A directional setup |
| `OI_MIGRATION` | Meaningful CE or PE OI-weighted strike migration; **current chain** | Trader intent / “smart money” |
| `RELATIVE_STRENGTH` | Stock vs Nifty gap; M15 not opposing; current quote+M15 | Sector leadership (no Upstox sector field) |
| `FUTURES_STRUCTURE` | Comparable **basis change**, not a single CURRENT_BASIS print; current futures | Intent from basis |
| `PRE_BREAKOUT_COMPRESSION` | Caller-verified compression + early-stage structure + proximity to a real opposing level; current quote **and** M15 | That a breakout will occur |
| `FAILED_BREAKDOWN_RECLAIM` | Caller-verified failed breakdown then reclaim; current quote **and** M15 | Trapped traders |

CONFLICT always yields `NONE`. Stale candles/quote cannot form the two
new structural patterns.

`HIGH_QUALITY_DEVELOPING` is **not** `EARLY_SETUP` alone. It requires
the documented pre-breakout combo + acceptable liquidity + early timing
+ not `NO_TRADE` / `CONFIRMATION_PENDING`.

---

## D. News intelligence

- Source in production: Upstox `get_news()` (authorized account).
- No NSE/BSE scrape-as-primary (existing policy).
- `published_at` / `retrieved_at`; `filter_news_no_lookahead(as_of)`.
- Keyword event categories (company / sector / macro / global) plus
  geopolitical **transmission channels**. Headline direction stays
  `UNKNOWN` — no sentiment vote.
- `EVENT_DRIVEN` bucket: fresh material item in the 48h window **and**
  early timing. That is monitoring, not a directional call.

---

## E. Whole-market scanner

Universe remains **F&O-eligible equities**.

| Tier | Status |
|---|---|
| TIER_1_FO_LIQUID | Classified from acceptable liquidity grade |
| TIER_1_FO_ILLIQUID | Classified; **not** presented as an options opportunity |
| TIER_2 cash-only | UNKNOWN — no cash universe invented |
| TIER_3 / TIER_4 | UNKNOWN this pass |
| TIER_5 F&O restricted | Only if a real restriction flag is passed (none from Upstox today) |

Stage 1 still cannot see OI/chain/news (honest). Stage 2 is the expensive
path. Scan coverage fields include universe source, F&O-ban unknown,
survivor-cap truncation, and per-stage timings (`docs/TIRE_SPRINT2.md`).
`GET /api/research/discover` is an alias of `/api/research/daily`.

---

## F. Historical research

- Provider-native M15/D (and other Upstox intervals) already used.
- No new local OHLCV store. No pretence of missing granularity.
- Evolution classifier compares two already-computed research states.
- Look-ahead on news remains `filter_news_no_lookahead`.

---

## G. Upstox + 5paisa

See `docs/PROVIDER_CAPABILITY_MATRIX.md`.

- Upstox: live primary.
- 5paisa Xstream: documented, **not wired** (no token, no adapter).
  Order APIs must never be called.
- Dhan adapter exists unused.
- Disagreement policy: never average; emit `PROVIDER_DISAGREEMENT` when
  a second live provider exists. Not emitted this pass because there is
  no second live provider.

---

## H. Qwen 2.5

**Not wired.** Allowed future use: summarize already-validated evidence,
bounded news extraction, narrative. Forbidden: creating market data,
overriding deterministic evidence, BUY/SELL, probabilities, scores,
freshness, safety, provenance. TIRE must run if Qwen is absent.

---

## I. Personal journal

Schema: `PersonalJournalEntry` / `PersonalJournalOutcome` /
`MistakeClass` in `app/domain/journal/personal_journal.py`.

- Append-only JSONL under `data/persistence/personal_journal/`.
- Emotion is **never inferred**. `EMOTIONAL_DECISION` only if the
  operator records it.
- API: `POST/GET /api/journal/personal`, outcome sub-routes.
- UAT: RECORD on the dashboard persisted
  `journal_id=cb20854cab44402db957b945a89628a4` for RELIANCE with
  explicit “no order was placed.”

---

## J. Performance

Architecture unchanged (Stage-2 concurrency 6). Measured this session:

| Run | Duration | Notes |
|---|---|---|
| Prior quality-gate full-universe scans | 218.9s / 310.5s | 30 Stage-2 survivors |
| UAT 21 explicit F&O names (Stage 1 skipped) | **221.4s** | 20/21 successful, 1 ANALYSIS_ERROR |
| UAT RELIANCE+KAYNES | **27.9s** | 2/2 evaluated |

No silent stale-data shortcut was added.

---

## K. Tests

```
pytest:  1869 passed, 0 failed, 0 skipped
ruff:    All checks passed (app + tests)
mypy:    Success: no issues found in 145 source files (--strict)
```

New/extended: `test_early_opportunity.py`, `test_development_evolution.py`,
`test_personal_journal_api.py`, development pattern tests, research-truth
closed vocabulary including the two new patterns, daily-research view
fields, dashboard HTML no longer contains `TOP OPPORTUNITIES`.

---

## L. Browser UAT (live, 9 Sep 2026, market closed)

Dashboard: `http://127.0.0.1:8010/` after server restart.

| Symbol / scan | Result |
|---|---|
| RELIANCE analyze | `research_state=CONFLICT`, decision `NO_TRADE`, Quick View PRIMARY BLOCKER CONFLICT |
| KAYNES analyze | `NO_TRADE`, development `NONE`. Pair-scan: rejected `HIGH_RISK_STRUCTURE` (contract not ACCEPTABLE) |
| NIFTY analyze | `CONFLICT`, `NO_TRADE`, Quick View RESEARCH STATE CONFLICT |
| BANKNIFTY analyze | `CONFLICT`, `NO_TRADE` |
| 21 liquid F&O names | DEVELOPING NOW: **ONGC EVENT_DRIVEN** only. 17 rejected. 0 already-extended. Developing now count = 1 |
| RELIANCE+KAYNES daily | `ZERO_VALID_EARLY_OPPORTUNITIES` |
| Journal | Recorded; no order path |

21-name list: RELIANCE, HDFCBANK, ICICIBANK, INFY, TCS, SBIN, BHARTIARTL,
ITC, LT, AXISBANK, KOTAKBANK, HINDUNILVR, BAJFINANCE, MARUTI, SUNPHARMA,
TITAN, ULTRACEMCO, ASIANPAINT, WIPRO, ONGC, KAYNES.

UI verified: heading **DEVELOPING NOW**, card
`ONGC -- PE 235.0 [EVENT_DRIVEN]`, **WHY INVESTIGATE NOW** news-monitor
sentence (not BUY), STRUCTURAL SHORTLIST collapsed separately, research
summary “Developing now: 1”, “Zero valid early opportunities: no”.

Quiet names (most of the 21) did **not** become EARLY_SETUP.

---

## M. Known limitations (not hidden)

1. 5paisa not implemented (not production-ready in TIRE).
2. Qwen is an optional explanation adapter (`qwen2.5-coder-7b-instruct` via LM Studio). It is not the decision engine. `qwen3:30b` in `.env.example` is unused leftover config.
3. No NSE/BSE official-announcement scrape.
4. No local multi-timeframe historical store.
5. No cash-only (Tier 2) universe.
6. No F&O-ban flag from Upstox.
7. Official NSE Nifty 500 industry classification **is** used when the sector map loaded. The UI says `Sector classification unavailable` when it did not. Stock-vs-sector RS uses that official industry map plus sector index day-change; it is not inferred from the company name. Upstox instrument master is not the sector source.

8. Pipeline `classify_development()` still does not compute compression/
   headroom itself; daily cards surface `PRE_BREAKOUT_COMPRESSION` from
   `_pre_breakout_signal()`.
9. Ranked top-3 can still include `NOT_INTERESTING` gated names; they
   are **not** in DEVELOPING NOW.
10. This 21-name closed-market sample had **zero** `ALREADY_MOVED` /
    `EXTENDED` gated cards — the classifier is covered by unit tests;
    live already-moved names appear when Stage 1/gate sees ~6% extension.
11. First in-page RUN RESEARCH stayed on “researching…” while overlapping
    CDP fetches ran; the same endpoint later rendered DEVELOPING NOW.
    Operator path: one scan at a time after reload if the button sticks.

---

## N. Research examples (why TIRE classified them that way)

### 1. Genuine developing (live) — ONGC `EVENT_DRIVEN`

- Early-stage `EARLY_DIRECTIONAL_BUILD`, timing `EARLY`, not extended.
- Liquidity `excellent`.
- `MAJOR_EVENT_RISK` (fresh news in the recency window).
- Named development pattern `NONE` — so this is **not** HIGH_QUALITY
  pre-breakout.
- Relative strength diverging vs NIFTY (session fact).
- Why: early structure **plus** a fresh material catalyst to monitor.
  TIRE did **not** say ONGC will rise or fall. Confirmation is still
  missing as a named pattern.

### 2. Already-moved (classifier; none in this 21-name live sample)

If `early_stage_state=BREAKOUT_CONFIRMATION`, bucket is `ALREADY_MOVED`
even with a named pattern. A ≥6% day move on an otherwise clean setup
is `EXTENDED`. Live 21-name scan: **0 rejected as already extended**.
That is honest for this liquid, closed-session sample — not a missing
feature.

### 3. False setup (live) — BHARTIARTL on the structural shortlist

- Gated enough to appear in the old ranked shortlist.
- Bucket `NOT_INTERESTING`: `NO_TRADE`, `RANGE_BOUND`, pattern `NONE`,
  no pre-breakout, no fresh event.
- **Not** in DEVELOPING NOW. Ranking ≠ early opportunity.

Same family, unit-tested: `FALSE_BREAKOUT_RISK` without
`FAILED_BREAKDOWN_RECLAIM` is `NOT_INTERESTING`.

### 4. Insufficient-data (live + tests)

- 21-name scan: 1 `ANALYSIS_ERROR` (coverage 20/21).
- RELIANCE+KAYNES: KAYNES rejected for `HIGH_RISK_STRUCTURE` — contract
  not researchable as an options opportunity.
- Unit: `DATA_INSUFFICIENT` outranks pre-breakout, news, and patterns.

### 5. Conflicting evidence (live) — RELIANCE, NIFTY, BANKNIFTY

- Analyze: `research_state=CONFLICT`, decision `NO_TRADE`.
- Quick View: PRIMARY BLOCKER CONFLICT.
- Daily pair-scan: RELIANCE `BOTH_SIDES_WEAK` — no defensible direction.
- Bucket precedence: CONFLICT is never an early opportunity.

---

## Safety

Read-only. No order APIs. Journal text states no order was placed.
`SAFETY_LOCK.txt` unchanged.

## Verdict

The product question is now answerable on the first screen:

> Across this scan, where is independent evidence beginning to align
> before a move is obvious, what is missing, and what would prove it
> wrong?

On 9 Sep 2026 after the close, for 21 liquid F&O names, the truthful
answer was **one EVENT_DRIVEN name (ONGC)** and **not** a list of
manufactured EARLY_SETUPs. RELIANCE+KAYNES alone was
`ZERO_VALID_EARLY_OPPORTUNITIES`. That is success under the stated
quality bar.
