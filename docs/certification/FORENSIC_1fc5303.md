# TIRE Deep Forensic Certification — Baseline `1fc5303`

**Final result: RED — RESEARCH / SAFETY INTEGRITY FAILURE**

| | |
|---|---|
| Branch | `phase-3-historical-validation` |
| Commit | `1fc53030ac445ca48358cf68a3480777446618c2` (`certify: validate forward research loop integrity`) |
| Working tree | clean before and after the audit (no source, test or config change; nothing committed by the audit) |
| Performed | 2026-09-25 / 26 (IST), read-only |
| Environment | Windows 11, Python 3.12 (`.venv`), local repository and local dev `data/` only |
| Production | **not contacted** (read-only audit; several production GET routes trigger scans) |
| **PLAYWRIGHT** | **BLOCKED** — Playwright MCP unavailable in the audit session |
| **LIVE CERTIFICATION** | **NOT PERFORMED** — no live market session, no credentials used |

This file records the audit as it was performed, against `1fc5303`; the
findings below are not revised after the fact. It is committed in Phase 0 of
the integrity programme. That commit resolves only M-1 (a test-fixture clock
fix; no application code changed); every other defect below remains open.
Current status is tracked in
[`docs/product/PRODUCT_CONTRACT.md`](../product/PRODUCT_CONTRACT.md).

---

## A. Executive summary

**Tested (read-only):** the full pytest suite and 14 focused suites; Ruff;
`mypy app --strict`; a code trace of provider → normalization → analysis →
evidence → decision → `ResearchObservation` → forward capture → JSONL →
scheduler → outcome engine → +1/+3/+5 → `ResearchProgression` → API; and
adversarial probes run as scratch scripts outside the repository against
temporary directories or the local dev data (read-only).

**Not testable:** any browser/UI behaviour (Playwright unavailable); live
market behaviour (no live session; no Discover run; no live checkpoint);
Railway runtime facts (volume mount, replica count, deploy overlap).

**Status:** the forward live research loop holds its core invariants on
fixtures (T0 immutability, horizon isolation, idempotent persistence, backend
anti-double-counting, broker lockout). Material defects exist outside those
certified paths: lookahead in historical replay, a fabricated futures basis
change that alters research state, in-process JSONL corruption, a
stale-evidence bypass on market context, and a UI tally that counts
non-voting evidence. The previously certified test suite also fails on the
real calendar date.

## B. Test results at `1fc5303`

| Check | Result |
|---|---|
| Full pytest (real date 2026-09-25) | **19 failed, 2359 passed** (Sprint 3.6 certified 2378 passed / 0 failed) |
| Same 19, clock pinned out-of-tree to 2026-09-23 05:00Z | all pass (58/58 in the affected files) → root cause is the test fixtures, not the app (M-1) |
| Ruff | clean |
| `mypy app --strict` | clean, 172 files |
| Safety | 67 passed |
| Research truth | 114 passed |
| Evidence voting / matrix / dependency | 94 passed |
| Evidence availability | 25 passed |
| Forward capture | 31 passed |
| Outcome | 165 passed, 3 failed (all among the 19) |
| Scheduler | 33 passed |
| Historical replay | 69 passed (does not detect C-1) |
| Watch | 99 passed, 2 failed (both among the 19) |
| Freshness / stale | 106 passed (does not detect H-3) |
| No-lookahead | 53 passed (checks `ts <= as_of`, not bar duration — does not detect C-1) |
| LLM / Qwen | 32 passed (does not detect M-4) |
| API | 88 passed, 19 failed (the date failures) |
| Options unit | 595 passed |

## C. Verified capabilities (fixture-level)

- Forward capture: `verify_forward_observation()` is the single rule set;
  deterministic identity hash over symbol, underlying key, right, strike,
  observed expiry, contract key and T0; `persisted_at` excluded from identity;
  capture refuses non-LIVE sessions and non-live market states.
- `ResearchObservation` and checkpoints are frozen models; outcomes are a
  separate append-only file; +1/+3/+5 read only bars in `[T0, horizon close]`;
  first-crossing recorded; same-bar confirmation+invalidation → INSUFFICIENT.
- `save_observation_once` / `save_checkpoint_once` are file-backed and
  lock-guarded within one process; torn tails in the research stores are
  skipped and counted.
- Backend anti-double-counting: `VOTING_GROUPS` is the single policy;
  `OPTIONS_IV` does not vote; supporting/conflicting counts are per group.
- Freshness gates for M15, chain, futures and underlying quote
  (`withhold_stale_stream_rows`, `data_is_current`).
- PCR change and futures ΔOI without prior history → UNKNOWN /
  INSUFFICIENT_DATA, never STABLE. Session VWAP is IST 09:15–15:30 on the
  `as_of` date, bounded by `as_of`.
- No broker path: no order code anywhere in `app/`; the Upstox provider only
  issues GETs; an AST test allowlists every provider path; startup refuses
  `BROKER_ORDER_EXECUTION_ENABLED=true`. The LLM is never a source of truth.

## D. Defects

### Critical

**C-1 — Historical replay lookahead (15 minutes).** Upstox M15 candles are
stamped at the bar **open** (the real `data/historical_replay/RELIANCE_M15.csv`
runs 03:45Z–09:45Z = 09:15–15:15 IST). `replay_symbol_session()` uses
`as_of = bar.freshness.data_timestamp` (`app/orchestration/historical_replay.py:178`,
while its docstring claims "each bar's own close"). The candle series is
correctly bounded (repository query is end-exclusive with `end=as_of`), but
`HistoricalReplayProvider._latest_candle()` queries with `end=as_of + 1s`
(`app/data/providers/historical_replay_provider.py:150`), so the replay quote
equals bar T's **close**. Probe: at T0 = 04:00Z the reconstructed spot was
1397.8 = the close of the bar ending 04:15Z. Quote-derived inputs that see the
future: `day_change_pct` (RELATIVE_STRENGTH vote, EXTENDED threshold),
`historical_structure(spot=)` (PRE_BREAKOUT_COMPRESSION), `structural_reclaim(spot=)`
(FAILED_BREAKDOWN_RECLAIM), regime current price, S/R proximity, and context
index quotes through `get_quotes` (GLOBAL vote, NIFTY relative strength).
Scope: all 4,880 persisted replay observations,
`GET /api/research/patterns?source=replay`, `GET /api/research/replay-dataset`.
The forward live path is not affected. The recorded `spot_at_observation`
(previous bar's close) is not leaked. The effect on classifications was not
quantified.

### High

**H-1 — No API authentication (still present).** The production origin is
public. Unauthenticated routes that change state: `POST /api/analyze`,
`/api/watchlist`, `/api/followup` (write quotes/chains/journal, refresh
Watch latest); `POST/DELETE /api/research/watches*` and `/migrate`;
`POST /api/journal/personal*`; `POST /api/research/explain` (Qwen proxy);
`POST /api/ipo/*`. `GET /api/research/daily` and `/discover` run a synchronous
whole-market scan, are not session-gated (unlike `POST /api/research/jobs/discover`),
write journals, and can be triggered cross-site as simple GETs. No request
body size limits (the `/data` volume can be filled). Forward capture still
fails closed outside the live session.

**H-2 — In-process thread-concurrent appends corrupt unlocked JSONL stores.**
Probe: three threads appending option-chain-sized lines to one `_JsonlStore`
produced **98 malformed of 572 lines; 28 records lost**. Mechanism:
`append_line` writes the record and the `\n` separately and checks the last
byte racily. Production has three writer threads (main loop — `/api/analyze`,
GET `/daily`; the Discover worker; the sweep worker) on `option_chains.jsonl`,
`quotes.jsonl`, `iv_observations.jsonl` and `analysis_snapshots.jsonl`. One
newline-terminated malformed line makes `option_chains.latest()` and
`quotes.latest()` raise (probe); `analyze_symbol` calls `latest()` unguarded, so
every later analysis of that underlying fails until the file is hand-repaired,
and the sweep fails that symbol every tick. The earlier finding M-5 (torn
tails) is still present for the candle, quote, audit-journal and research-run
readers (probe: all raise). The research observation/checkpoint stores are
safe (shared lock, tolerant reads). Local data was clean — the race is latent.

**H-3 — Stale market context can vote.** GLOBAL is a voting group, but context
quotes (NIFTY, BANKNIFTY, VIX, USDINR, CRUDE, sector index) have no freshness
gate (`options_intelligence_pipeline.py:1165`); `MARKET_CONTEXT` availability
has no STALE state; RELATIVE_STRENGTH is gated on the underlying quote but not
on the NIFTY quote. Two staleness shapes exist (`classify_market_data_state`
for price/quote/futures; chain-quality issues for the chain) and neither covers
context. Live impact not measured.

**H-4 — UI tally counts non-voting and correlated rows.**
`summarizeGroupDirection` (`app/api/static/index.html:4324-4336`) counts raw
rows and includes `options_iv`; a single IV-skew row can make "SECONDARY
POSITIONING" read "Bullish", and one chain snapshot's rows are counted
separately. Backend decision and `supporting_evidence_count` are unaffected. A
test pins the behaviour (`tests/integration/frontend/test_dashboard_js_pure_functions.py:227`).

**H-5 — Fabricated futures basis change raises research state.** The current
underlying quote is saved at stage 3 (`options_intelligence_pipeline.py:471`);
at `:975` `quotes.latest(as_of − 1µs)` returns that same quote as the
"previous" underlying quote (its `data_timestamp` is the last trade time, normally
before `as_of`), so previous basis = F_prev − S_now. Probe: a truly STABLE basis
(+5 on both days) was classified **WIDENING_PREMIUM**. This feeds the
FUTURES_STRUCTURE development pattern (`app/domain/options/development.py:171`),
which turns WATCH into EARLY_SETUP; the pattern name is stored on forward
observations. No test covers this path.

### Medium

- **M-1 — Test time bomb.** Fixtures hard-code `EXPIRY = 2026-09-24` while the
  API tests use the real clock; 19 tests fail after that date and the
  certified baseline is not reproducible.
- **M-2 — CONFIRMED_SETUP while the market is closed.** When closed,
  `MARKET_CLOSED_LATEST_DATA` counts as a current quote, and the chain's
  freshness is its HTTP receipt time, so an after-hours chain is always
  "current". Forward capture correctly refuses. Whether the UI labels these
  LAST OBSERVED was not verified (no browser).
- **M-3 — `SYSTEM_HALTED` not wired.** Defined in settings, never read;
  `docs/operations/PRODUCTION_ROLLBACK.md` presented it as the product kill switch.
- **M-4 — Qwen claim filter bypassable.** Dict-valued fields are stringified
  unchecked. Probe: `BUY NOW -- 90% probability`, `expected return 12%` and
  `{'recommendation': 'BUY', 'confidence': '95%'}` were accepted. Display-only;
  `facts` is client-supplied and unauthenticated (prompt-injection surface).
- **M-5 — Watch T0 is client-asserted.** A forged `CONFIRMED_SETUP` T0 was
  accepted; an invalid symbol (`NOT_A_REAL_SYMBOL_$$`) minted a watch. T0 is
  immutable once stored, and Watch never touches the research store.
- **M-6 — No comparability window for "prior" observations.** The prior chain
  (PCR change) and prior futures quote (ΔOI quadrant) can be any age — seconds
  or sessions — so the vote depends on how recently the symbol was analysed.
- **M-7 — Calendar is 2026-only.** No year-coverage check; 2027 silently
  becomes weekend-only: horizons count holidays as sessions, holiday-dated
  checkpoints freeze permanently as INSUFFICIENT, and the Discover gate opens
  on holidays. First exposure: horizons spanning 26 Jan 2027.
- **M-8 — Forward and replay outcome vocabularies differ** (the Sprint 3.6 M4,
  still present). The forward summary ignores its own first-crossing facts and
  can report FOLLOW_THROUGH after price invalidated first.

Previously recorded Sprint 3.6 medium findings M1 (`audit_id` not required at
capture), M2, M3 and M5 remain present.

### Low

- **L-1** `classify_direction_comparison` counts raw OPTIONS_OI rows for the
  BOTH_SIDES_STRONG label (conflict-branch labelling only).
- **L-2** pandas, numpy, apscheduler, redis, sqlalchemy, asyncpg and pypdf are
  declared but never imported. The `pyproject.toml` comment stating pypdf is
  used by `app/data/providers/rhp_fetcher.py` is false (`rhp_fetcher.py` does
  not import it). Recorded here; dependency configuration untouched.
- **L-3** `technical_price_levels` takes no `as_of` and relies on its caller
  bounding the candles (safe for its single current caller).
- **L-4** No fsync on the research stores.
- **L-5** Futures-only staleness does not force CONFIRMATION_PENDING (documented).

## E. Known limitations (not defects)

Browser certification blocked (no Playwright). Live certification never
performed — locally 0 forward captures and 0 checkpoints (71 legacy
observations). No historical F&O option chain, futures or news — replay is
price-only. Chain freshness is HTTP receipt time. Discover job registry is
in-memory; GET Discover is synchronous. The replay dataset may not be
deployed in production. Special sessions approximated. Dataset is
selection-conditioned. Scheduler health is in-memory. The NIFTY
persisted-quote limitation remains documented and was not re-verified live.
Discover Stage-2 promotion variance (202 vs 164) is quote-driven Stage-1
bucket behaviour — expected, not re-verified live.

## F. Invariant certification

| Invariant | Result |
|---|---|
| T0 immutable | YES (forward loop) |
| No lookahead | PARTIAL — C-1 |
| Evidence voting enforced | PARTIAL — backend yes, UI tally no (H-4) |
| Stale data isolated | PARTIAL — H-3, M-2 |
| Contract identity immutable | YES |
| Evidence availability immutable | YES (MARKET_CONTEXT cannot be STALE — H-3) |
| Outcomes separate from T0 | YES |
| +1/+3/+5 independent | YES |
| Restart safe | PARTIAL — research stores yes; market-data stores no (H-2) |
| Duplicate safe | YES within one process |
| Cross-process safe | NO (not certified) |
| Watch T0 immutable | YES once stored; not server-attested (M-5) |
| LLM non-authoritative | YES (filter bypass M-4 is display-only) |
| Broker execution blocked | YES |
| API mutation authenticated | NO |

## G. Adversarial probes performed (outside the repository)

1. Three threads appending ~40 KB lines to one `_JsonlStore` → 98/572
   malformed, 28 lost (H-2).
2. Torn tail / mid-file malformed line in candle, quote, option-chain,
   audit-journal and research-run stores → readers raise (H-2).
3. `HistoricalReplayProvider.get_quote` at a real bar's timestamp → returns
   that bar's close (C-1).
4. Persisted replay observations vs source bars: 4,880 observations, all on
   bar-open instants; recorded spot matches the bar open 2,065 times and the
   close 97 times (recorded spot = previous close; not leaked).
5. Stage-3 save then stage-12 prior read on a real `JsonlQuoteRepository` →
   the "previous" quote is the current one; STABLE basis → WIDENING_PREMIUM (H-5).
6. `parse_qwen_explanation` with dict-valued fields → forbidden claims accepted (M-4).
7. `ResearchWatchService.create` with an invalid symbol and a forged T0 → both accepted (M-5).
8. Out-of-tree pytest plugin pinning `utc_now` → the 19 failures pass (M-1).
9. Read-only integrity scan of local `data/persistence/*.jsonl` → 0 malformed;
   `audit_research_dataset` clean (research_outcomes: 71 legacy, 0 forward, 0
   checkpoints; replay: 4,880).

## H. Browser and live status

- **PLAYWRIGHT: BLOCKED.** Browser/UI certification **NOT PERFORMED**.
  Screenshots: **NONE**. UI findings (H-4, M-2 labelling) come from reading
  static frontend and backend code, not from observed UI.
- **LIVE CERTIFICATION: NOT PERFORMED.**

## I. Recommended priorities (recommendations only)

- **P0:** C-1 replay quote must read only closed bars, then invalidate and
  rebuild the replay dataset · H-5 read the prior underlying quote before
  saving the current one · H-2 one process-wide lock and single write per
  line per store, tolerant readers everywhere · H-1 authenticate all
  state-changing routes; session-gate or remove GET `/daily` and `/discover`.
- **P1:** H-3 context-quote freshness and a MARKET_CONTEXT STALE state · H-4
  exclude non-voting groups and count by group in the UI · M-1 pin the test
  clock · M-3 implement `SYSTEM_HALTED` or correct the runbook · M-4 validate
  Qwen fields as strings · M-7 fail closed when the calendar does not cover
  the year.
- **P2:** M-2 session-qualified state when closed · M-5 server-attested Watch
  T0 · M-6 comparability window · M-8 unify outcome vocabularies · L-2
  dependency cleanup · perform live certification.

## J. Final result

**RED — RESEARCH / SAFETY INTEGRITY FAILURE.** Driven by proven lookahead in
the historical validation dataset (C-1) and a fabricated evidence path that
changes the live research state (H-5). Forward T0, outcome and broker-safety
guarantees held on fixtures. Browser certification is reported separately as
BLOCKED and did not affect this result.
