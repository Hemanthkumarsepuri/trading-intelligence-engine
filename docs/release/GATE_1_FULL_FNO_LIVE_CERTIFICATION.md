# GATE 1 — Full live F&O scan

Certification date: **21 September 2026**.  
Session: NSE cash/F&O **09:15–15:30 IST**, observed **OPEN**.  
Branch: `phase-3-historical-validation`.  
Commit at certification: see git after this document is committed.  
Prerequisite: GATE 0 PASS.

Production:

- API: https://api-production-983e.up.railway.app
- Frontend: https://tire-research-terminal.netlify.app
- Railway project TIRE `a65737bd-78f0-42b9-9f61-abcd30143918`, service `api` `02afa032-20ff-443d-9118-ebf59343b4df`, env `production` `9dd3e9aa-28a9-4985-aaca-b04677d3066c`
- Deployment after Gate 1 defect fix: **`1d98a829-b006-43bf-a212-aed117b7ed8a` SUCCESS** (replaced `b49953ec-6616-4bcc-b742-42302874772b`)

## Status

**PASS** — LIVE F&O SCAN — CONFIGURED UNIVERSE

This is not whole-market coverage beyond the configured eligible universe.

## Session (before Discover)

| Field | Value |
| --- | --- |
| IST calendar date | 2026-09-21 |
| `session_window` | OPEN |
| `research_session_mode` | LIVE |
| `observation_kind` | LIVE |
| `is_trading_day` | true |
| `live_discover_available` | true |
| next session | 22 Sep 2026 · 09:15 IST |
| `broker_execution` | impossible |

First harness attempt (`python -m scripts.certify_live_fno_scan --api https://api-production-983e.up.railway.app`) started live Discover job **`42aefbefc369`** which completed. The harness then hung because it polled for status `COMPLETED` while the API uses `COMPLETE`. That is a certification-script defect, not a failed scan.

## Discover (official post-fix run)

Command (unchanged):

```text
python -m scripts.certify_live_fno_scan --api https://api-production-983e.up.railway.app
```

Exit code **0**.

| Item | Value |
| --- | --- |
| Job ID | `5351650c99a4` |
| Scan start | 2026-09-21 **14:50:00 IST** (`2026-09-21T09:20:00.018837Z`) |
| Scan end | 2026-09-21 **14:50:55 IST** (`2026-09-21T09:20:55.371265Z`) |
| Duration | **55.35 s** (job elapsed 57.3 s) |
| Configured universe | `upstox_instrument_master.NSE_FO_equity_underlyings` — **210** F&O-eligible equity underlyings |
| Configured cap | `DEFAULT_WHOLE_MARKET_SURVIVOR_CAP = 1000`; this run `survivor_cap_applied=false`, Stage-2 budget capacity **566**, limit reason `NONE` |
| Actual coverage | Stage 1 **210/210** success, 0 fail; Stage 2 **202/202** success, 0 fail; 8 names did not promote to Stage 2 (not failures) |
| Coverage class | HIGH |
| `market_state` | `LIVE_SNAPSHOT` |
| `scan_kind` | `F&O_UNIVERSE` / `DEFAULT_WHOLE_FO_SCAN` |
| Provider errors | none (`data_issues` empty, `timeout_failures` 0, `timestamp_quality_failures` 0, `failed_symbols` 0) |

Earlier same-session live job `42aefbefc369` (14:33:44 IST, 198 Stage-2 / 210 Stage-1, 53.85 s) is additional live proof. GET `/api/research/discover` was **not** used as the Gate 1 job (that alias still runs a synchronous daily scan; it is not the certified path).

## Live data proof (RELIANCE, 21 Sep 2026 session)

Observations are **21 September 2026**, not 18 September last prints.

Underlying (analyze, ~14:37 IST): quote stream **LIVE**, `data_timestamp` `2026-09-21T09:07:17.008000Z`, LTP region **~1245**, day change about **+1.5%**, `market_state=LIVE_SNAPSHOT`.

Options (near monthly chain **expiry 2026-09-29**, ATM **1250**):

| Contract | Strike | Type | LTP | Bid | Ask | Volume | OI | ΔOI | IV |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ATM CE | 1250 | CE | 12.1 | 12.1 | 12.15 | 15,275,500 | 5,958,500 | -780,000 | 17.46 |
| ATM PE | 1250 | PE | 14.2 | 14.15 | 14.2 | 5,852,500 | 3,418,500 | -1,022,000 | 18.07 |
| OTM CE | 1270 | CE | 5.2 | 5.2 | 5.25 | 6,721,000 | 2,940,000 | +59,500 | 17.82 |
| ITM/OTM PE | 1270 | PE | 27.4 | 27.1 | 27.4 | 384,500 | 1,176,500 | -45,500 | 18.68 |

Greeks were returned on those legs (delta/gamma/theta/vega). Chain as-of is HTTP receipt time (provider does not supply per-leg exchange match timestamps).

## RELIANCE 1270 PE

**PASS — contract available** on the live 2026-09-29 chain (not assumed from closed-market validation).

Analyze `RELIANCE 1270 PE` ~14:38 IST: `found=true`, expiry **2026-09-29**, DTE **8** from the matching term-structure row, LTP **27.40**, bid **27.20**, ask **27.50**, OI **1,176,500**, ΔOI **-45,500**, volume **384,500**, IV **18.80**, research state **CONFIRMATION_PENDING** (WATCH). Quote timestamp `2026-09-21T09:08:06.098000Z`.

## Expiry integrity

- Loaded `option_chain.expiry` = **2026-09-29**.
- Term structure rows: **2026-09-29 / 8 DTE** and **2026-10-27 / 36 DTE**. DTE for the loaded chain matches the **2026-09-29** row, not `expiries[0]` reused as a generic label.
- Query `RELIANCE 1250 CE OCT` still loaded the near chain (`requested.expiry=2026-09-29`). Month hint does not switch the fetched chain. Far expiry remains visible on term structure only. Not fabricated.

## Futures

Returned fields: `instrument_key=NSE_FO|68777`, LTP **1248.4**, OI **123,764,500**, `basis_pct` **~0.27%** (premium). Visual does **not** expose contract expiry, volume, ΔOI, or absolute basis on this payload. OI interpretation: no meaningful price move to classify. Basis-change history: **INSUFFICIENT / not STABLE**. Stream **LIVE** (`2026-09-21T09:07:02.130000Z`).

## PCR

PCR(OI) **0.52**. Change **0.52 → 0.52 (+0.00)** — not a meaningful change. Context-only; not directional confirmation. Not labelled STABLE.

## VWAP

Compact report: `VWAP: WITHHELD (ROLLING_INTRADAY_VWAP, not session VWAP)`. Current-session VWAP **DATA INSUFFICIENT**. M15 last bar **2026-09-18T09:45:00Z** (previous session). Not substituted with live session VWAP.

## Freshness and stale isolation

| Stream | Label | Votes |
| --- | --- | --- |
| underlying_quote | LIVE | yes |
| option_chain | LIVE | yes |
| futures | LIVE | yes |
| candles_m15 | STALE | **no** (withholds m15_trend, rolling_vwap_position, intraday_regime, breakout_confirmation) |
| delivery | EOD | no |
| sample_breadth | LIVE display | no vote |

Research state **CONFIRMATION_PENDING** follows existing precedence (stale M15). Report freshness_label **DEGRADED**. Entire decision is not collapsed.

### Glance-strip defect (fixed during this gate)

`GET /api/market/observed` NIFTY persisted print `2026-09-18T10:30:00+00:00` (**18 Sep · 16:00 IST**) was labelled **LIVE** solely because the clock was OPEN. That violates “do not upgrade a previous-session print to LIVE”.

Fix: if the quote’s IST date is not the current IST date, `observation_kind=LAST_OBSERVED` and, during LIVE session, `freshness_label=STALE`. After deploy `1d98a829`: NIFTY **LAST_OBSERVED / STALE / 18 Sep · 16:00 IST**. Session chrome remains OPEN / LIVE.

## Evidence engine

Groups observed: price structure (withheld — stale M15), futures, options_oi (PCR/volume/OI together), options_iv, relative strength, liquidity, data quality, global, news. No confidence %, probability, expected return, or smart-money score as a voting input. `research_confidence` on screener shortlist cards is existing structured quality language, not a new Gate 1 score.

## Screener / Symbol / Watch (MCP)

Desktop **1440×900**, **1280×800**; mobile **390×844**, **360×800**.

- MARKET OPEN, RESEARCH MODE LIVE, last scan **21 Sept, 02:33 pm IST** (first live job; post-deploy job **14:50 IST**).
- Not labelled MARKET_CLOSED. Glance NIFTY after fix is LAST_OBSERVED for the Friday persist.
- No BUY / SELL / PLACE ORDER / EXECUTE controls. Disclaimer “not a prediction, not BUY/SELL”.
- No horizontal overflow. No captured console errors.
- Symbol `RELIANCE 1270 PE`: MARKET OPEN · LIVE · RESEARCH MODE LIVE, CONFIRMATION PENDING, T0 WATCH vs current observation.

Watch `a6f1c96a853c41a7a8152441ac7c4d7a`: T0 observation `e9687121c74e48c8b15d250dfd6ba133` immutable; latest `fdce285b1de544dc90bb8c42efe5b6ad`; category `EVIDENCE_CHANGED` on `underlying_last` **1245.4 → 1245.2** (live tick, not audit-only).

## Performance

55–57 s for 210 Stage-1 and 202 Stage-2. Within previously accepted daily-scan durations (historical ~310 s for 210/30). **PASS**.

## Safety

`broker_execution=impossible`. No order placement/modify/cancel routes. Qwen disabled in production.

## Tests

- pytest: **2193 passed** (205.94 s) after the observed-market test addition (prior certified baseline 2192 + 1).
- ruff: clean on touched files.
- mypy `app/orchestration/observed_market.py --strict`: clean.

## Defects found and fixed

1. Cert harness treated `COMPLETE` as still running (`COMPLETED` only). Now accepts `COMPLETE` / `COMPLETED`.
2. Observed index glance labelled previous-session NIFTY prints LIVE during OPEN. Now LAST_OBSERVED / STALE.

## Known limitations

- M15 (and therefore session VWAP / M15 trend) remains STALE during this live session because candles come from Upstox historical M15 ending the prior session. Isolated correctly; CONFIRMATION_PENDING is expected.
- Glance NIFTY/BANKNIFTY/VIX are **persisted quotes only** (endpoint never calls the provider). Friday NIFTY remains visible as LAST_OBSERVED until a same-day index quote is persisted.
- Futures visual omits expiry, volume, ΔOI, absolute basis.
- Expiry-month query hint does not switch the loaded option chain off the near expiry.
- Screener “20 OBSERVATIONS” is the displayed research-row subset, not 210 Stage-1 rows.
- In-memory Discover job registry resets on Railway deploy.
- F&O ban status `FNO_BAN_STATUS_UNKNOWN`.
- GET `/api/research/discover` still executes a synchronous whole-universe scan; certified path is POST `/api/research/jobs/discover`.

## Next allowed gate

**NONE automatically.** Gate 2 (live option contract matrix) must not start until an operator explicitly requests it.
