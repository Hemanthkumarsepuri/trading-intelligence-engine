# GATE 1.1 — Live stabilization (after Gate 1 PASS)

Certification date: **21 September 2026**.  
Session: NSE cash/F&O **09:15–15:30 IST**. Live forensic and recertify ran while **OPEN** (approx. 15:06–15:21 IST).  
Branch: `phase-3-historical-validation`.  
Commits: `de41428` (M15 intraday merge, expiry-hint resolution, futures field propagation), `75524da` (Symbol DTE from matching term-structure row).  
**GATE 1 REMAINS PASS.** This document does not replace `GATE_1_FULL_FNO_LIVE_CERTIFICATION.md`.

Production:

- API: https://api-production-983e.up.railway.app
- Frontend: https://tire-research-terminal.netlify.app
- Railway deployment after Gate 1.1: **`8ae24409-e295-4266-b9cf-9b73599cc1ed`** (logic) then **`ff5bf311-755f-4af9-b816-f0625517fbca`** (DTE UI) SUCCESS
- Netlify static: deploy **`6ab0fe045b7bbcf864221b82`** (api-base production API)
- Gate 1 baseline job: `5351650c99a4` (14:50:00–14:50:55 IST, 55.35 s, 210/210 Stage 1, 202/202 Stage 2, 0 failures)

## Status

**PASS WITH KNOWN LIMITATIONS** — live intelligence remained truthful; genuine correctness defects were fixed and re-verified on the open session.

Gate 2 was **not** started.

## Session (during validation)

| Field | Value |
| --- | --- |
| IST | 2026-09-21, first check **15:06:14 IST** |
| `session_window` | OPEN |
| `research_session_mode` | LIVE |
| `observation_kind` (session chrome) | LIVE |
| `live_discover_available` | true |
| `broker_execution` | impossible |
| `primary_provider` | upstox |

## 1. Gate 1 baseline

Unchanged. Job `5351650c99a4`, commit `1f8bc00`, deploy `1d98a829`. Known limitations from that certificate were the investigation list for this pass.

## 2. M15 forensic

**Root cause (not a stale-threshold bug):** Upstox `GET /v3/historical-candle/{key}/{unit}/{interval}/{to}/{from}` does **not** include the current trading day’s bars. Gate 1’s live RELIANCE M15 stream was `data_timestamp=2026-09-18T09:45:00Z` (`PREVIOUS_SESSION`, Friday 15:15 IST candle **open**), age ≫ 20 min → **STALE**, votes withheld. Classification was **correct** given that series.

**Timestamp semantics:** provider ISO timestamps are candle **open** (not close, not HTTP receipt). TIRE stores `ensure_utc(datetime.fromisoformat(row[0]))`. Freshness uses `candle_series_stale_threshold=20 minutes`. The in-progress bar is **not** stripped; after the fix the latest bar during 15:12 IST was `2026-09-21T09:30:00Z` = **15:00 IST open** (current 15:00–15:15 interval).

**Fix:** same vendor documented sibling `GET /v3/historical-candle/intraday/{key}/{unit}/{interval}`, merge by timestamp (intraday overwrites), **fail-open** to historical if the intraday call errors. Stale threshold **not** widened. Intraday path already allowlisted under `/v3/historical-candle/`. Stage-2 candle cost 2→3.

**Live retest (after `8ae24409`, ~15:12 IST):**

| Symbol | Latest M15 | Age vs 15:12 IST | Freshness | Vote |
| --- | --- | --- | --- | --- |
| RELIANCE | 2026-09-21T09:30:00Z | ~12 min | LIVE | usable |
| HDFCBANK | 2026-09-21T09:30:00Z | ~12 min | LIVE | usable |

Research state moved off `CONFIRMATION_PENDING` when M15 was live (RELIANCE `CONFIRMED_SETUP` on underlying-only analyze). Stale isolation still holds: before the fix, M15 did not vote.

## 3. NIFTY glance

Persisted quote remains Friday `2026-09-18T10:30:00+00:00` (**18 Sep · 16:00 IST**). After Gate 1 fix this is **LAST_OBSERVED / STALE**, never LIVE. Reconfirmed on `GET /api/market/observed` at 15:10 IST and on Railway-served UI: `NIFTY 23,346.40 +0.33% · LAST OBSERVED · 18 Sep · 16:00 IST · STALE`.

Netlify screener strip can also show NIFTY from the **latest Discover result** as “this scan” while that client cache exists. That is scan-sourced, not a relabel of the Friday persist as LIVE.

BANKNIFTY/VIX persisted quotes: **UNAVAILABLE** (no stored observation).

## 4. Futures forensic

Provider quote already had LTP, OI, volume; ΔOI from `classify_price_oi` / prior observation; expiry from the selected F&O contract. Visual previously dropped expiry/volume/ΔOI (**pipeline C/D**: orchestration visual + UI).

**Live (RELIANCE near futures ~15:12 IST):** LTP 1249.4, OI 123,648,000, ΔOI −4,000, volume 7,713,000, expiry **2026-09-29**, basis_pct ~0.22% premium. Stream LIVE.

**Live (RELIANCE October futures on Oct chain):** LTP 1255.6, OI 26,299,500, volume 4,302,000, expiry **2026-10-27**, basis_pct ~0.74%. First print ΔOI `None` with “no prior observation of this instrument”; later `0` with “no meaningful price move” — not labelled STABLE. HDFCBANK near futures: LTP 741.5, OI 342,346,550, ΔOI +1,605,500, volume 41,910,050, expiry 2026-09-29, basis_pct `0` (futures LTP equal to the basis inputs that run produced).

## 5. Expiry / option chain

**Root cause:** (1) parser treated `OCTOBER` as unrecognized and `2026` as a second strike (`parse_warnings` on production pre-fix). (2) `expiry_hint` was parsed but **never passed** into `analyze_symbol`, which always called `nearest_expiry()`. UI month text therefore could say October while the loaded chain was **2026-09-29**. That is contract-identity corruption.

**Fix:** full month names; 4-digit years only in 1990–2100; `resolve_expiry_for_hint` matches month/year, prefers monthly, **never** falls back to nearest other month; miss → `CONTRACT UNAVAILABLE` and no chain.

**Live (~15:12–15:16 IST):**

| Request | `parsed_expiry_hint` | Observed chain | DTE | Match |
| --- | --- | --- | --- | --- |
| RELIANCE 1270 PE OCTOBER 2026 | OCT | **2026-10-27** | **36** | PASS |
| RELIANCE 1270 PE OCT | OCT | **2026-10-27** | 36 | PASS |
| RELIANCE 1270 PE SEP | SEP | **2026-09-29** | 8 | PASS |
| RELIANCE (no hint) | — | 2026-09-29 (nearest) | 8 | PASS |
| RELIANCE 1270 PE JANUARY 2027 | JAN | none | — | `CONTRACT UNAVAILABLE` — no substitute |

Term structure still lists 2026-09-29 (8d) and 2026-10-27 (36d). DTE 36 is calendar days 21 Sep → 27 Oct 2026 on the **matching** row, not `expiries[0]` and not the UI month label.

Chain rows share a single `option_chain.expiry`. Requested-contract `expiry` equals that field.

## 6. Contract matrix (RELIANCE, OPEN)

| Query | Expiry | LTP | Bid | Ask | OI | ΔOI | Vol | IV |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1250 CE (ATM) | 2026-09-29 | 12.25 | 12.2 | 12.3 | 5,572,000 | −1,166,500 | 16,609,000 | 17.21 |
| 1250 PE (ATM) | 2026-09-29 | 13.8 | 13.85 | 13.9 | 3,479,500 | −961,000 | 6,472,500 | 18.19 |
| 1270 CE (OTM) | 2026-09-29 | 5.4 | 5.4 | 5.45 | 2,736,000 | −144,500 | 7,371,500 | 17.94 |
| 1230 PE (OTM) | 2026-09-29 | 6.3 | 6.2 | 6.25 | 1,317,500 | +78,000 | 4,911,500 | 18.55 |
| 1270 PE October | 2026-10-27 | 39.45 | 39.25 | 39.7 | 386,500 | +3,500 | 137,000 | 20.2 |
| 1270 PE Sep | 2026-09-29 | 26.7 | 26.5 | 26.7 | 1,175,500 | −46,500 | 426,500 | 18.43 |

## 7. Observation Watch

Existing pin `a6f1c96a853c41a7a8152441ac7c4d7a`. T0 remains `RELIANCE 1270 PE` / expiry **2026-09-29** / `CONFIRMATION_PENDING` (immutable). Latest October snapshot: expiry **2026-10-27**, category **`EXPIRY_CHANGED`** (`2026-09-29` → `2026-10-27`). Not reported as `NO_MATERIAL_CHANGE`. UI also showed `EXPIRY CHANGED` (one line abbreviated the after-value as `OCT` from the query hint — API stored the date).

## 8. Stale isolation / evidence

Before M15 fix: M15/VWAP withheld, `CONFIRMATION_PENDING`. After: M15 **BULLISH** votes when LIVE. Groups on October RELIANCE 1270 PE (19 rows): M15, VWAP, regime bullish; OI/PCR/volume/IV/support/resistance/liquidity/news/ΔOI ATM mostly NEUTRAL; RS and global bullish. No duplicate group names. No confidence %, probability, expected-return vote, or BUY/SELL. Missing data stays NEUTRAL/withheld, not fabricated confirmation.

## 9. Screener / Discover retest

Command unchanged:

```text
python -m scripts.certify_live_fno_scan --api https://api-production-983e.up.railway.app
```

| Item | Value |
| --- | --- |
| Job | `637167ebb3be` |
| Start | 2026-09-21T09:43:22Z (**15:13:22 IST**) |
| Elapsed | **71.0 s** (Gate 1 was 55.35 s; extra historical+intraday candle cost) |
| Universe | 210 |
| Stage 1 | 210/210, 0 fail |
| Stage 2 | 202/202, 0 fail |
| `market_state` | LIVE_SNAPSHOT |
| timeouts / timestamp quality | 0 |

Not a coverage regression. Timing longer is expected (candle budget 3 vs 2).

**Screener UI while OPEN (Netlify, before second Railway deploy):** `MARKET OPEN · RESEARCH MODE LIVE`, rows Available; LIVE, disclaimer not BUY/SELL. After `ff5bf311` the in-memory job registry **cleared** (known limitation): Railway origin showed `LAST SCAN — none yet` / `NO LATEST MARKET SCAN` rather than inventing rows.

## 10. MCP / browser

Viewports: 1440×900, 1280×800, 390×844, 360×800. No horizontal overflow. No BUY/SELL/PLACE ORDER. Console probe empty. Railway UI post-DTE-fix: `PE 1270 2026-10-27`, **DTE 36**, MARKET OPEN / LIVE. Netlify (pre-HTML-update) already showed October chain identity via API; workspace DTE was UNKNOWN until static deploy `6ab0fe04`.

## 11. Safety

`broker_execution=impossible`. No order routes. Qwen disabled.

## 12. Tests

On `de41428` working tree before the HTML-only DTE commit:

- pytest: **2201 passed** in 207.77 s (baseline 2192+)
- ruff: clean (`app tests scripts`)
- mypy `app --strict`: clean (168 files)

Closed/pre-market/LAST_OBSERVED tests in `test_observed_market.py` and research-mode suite were not weakened.

## 13. Closed-market regression (code)

Existing calendar + `observation_kind` rules unchanged: LIVE only for same-IST-date prints during OPEN. Friday NIFTY cannot become LIVE. Discover POST 409 when not OPEN remains.

## 14. Known limitations (honest, remaining)

1. Glance NIFTY/BANKNIFTY/VIX are **persisted quotes**; Friday NIFTY stays LAST_OBSERVED until a same-day index quote is persisted. Not fabricated LIVE.
2. Discover job registry is **in-memory** and clears on Railway deploy.
3. GET `/api/research/discover` remains synchronous; certified path is POST `/api/research/jobs/discover`.
4. No dedicated month dropdown: October is a **query hint** (`OCT` / `OCTOBER 2026`).
5. Watch UI may show the hint token `OCT` in one change line while the snapshot expiry is `2026-10-27`.
6. Current incomplete M15 bar is included (candle **open** timestamp). 20-minute freshness is unchanged.
7. F&O ban status still `FNO_BAN_STATUS_UNKNOWN` where previously noted.
8. Netlify and Railway static can briefly diverge until both are deployed; this pass published both.

## 15. After market close

Checked **2026-09-21T20:12:26 IST** (same trading date; session already past 15:30). Not a tick-by-tick 15:30 capture.

| Field | Value |
| --- | --- |
| `session_window` | CLOSED |
| `research_session_mode` | POST_MARKET |
| `observation_kind` | LAST_OBSERVED |
| `live_discover_available` | false |
| POST `/api/research/jobs/discover` | **409** `LIVE_DISCOVER_UNAVAILABLE — MARKET CLOSED` |
| NIFTY glance | LAST_OBSERVED / `MARKET_CLOSED` / 18 Sep · 16:00 IST — **not LIVE** |
| `broker_execution` | impossible |

This is a normal session transition, not a Gate 1 failure.

## Next allowed gate

**NONE automatically.** Do not start Gate 2 unless an operator explicitly requests it.
