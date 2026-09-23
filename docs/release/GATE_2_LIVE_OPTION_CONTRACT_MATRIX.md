# GATE 2 — Live option contract matrix

Certification date: **23 September 2026**.
Live execution: **09:25–09:33 IST** (Wednesday).
Branch: `phase-3-historical-validation`.
Implementation commit (unchanged this run): `2608d11`.
Certification commit: this document.

**GATE 0 remains PASS.**  
**GATE 1 remains PASS.**  
**GATE 1.1 remains PASS WITH KNOWN LIMITATIONS.**

## Status

**PASS WITH KNOWN LIMITATIONS**

Live matrix was executed against production while NSE was **OPEN**. No POST_MARKET prints were treated as LIVE. No product code was changed (no live defect).

| Check | Result |
| --- | --- |
| calendar_date_ist | 2026-09-23 |
| session_window | OPEN |
| research_session_mode | LIVE |
| observation_kind | LIVE |
| live_discover_available | true |
| broker_execution | impossible |
| qwen_enabled | false |

## Live expiries (authoritative chain)

RELIANCE term structure observed **09:25 IST**:

| Expiry | Role | DTE | Weekly |
| --- | --- | --- | --- |
| **2026-09-29** | nearest | 6 | no |
| **2026-10-27** | next / later (only two upcoming) | 34 | no |

ATM observed: **1240**. Spot region ~1244–1246 (futures LTP 1246.0).

Selected strikes from the live chain:

- ATM: 1240
- ITM CE / OTM PE: 1230
- OTM CE / ITM PE: 1250
- Mandatory: 1270 PE

## Contract matrix (LIVE VERIFIED)

Nearest **2026-09-29** (~09:26–09:27 IST, all streams LIVE, DTE 6):

| Query | Right | Moneyness | LTP | Bid | Ask | Vol | OI | ΔOI | IV | Δ/Γ/Θ/V |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1240 CE | CE | ATM | 13.45 | 13.4 | 13.5 | 607000 | 2347500 | +43000 | 16.85 | yes |
| 1240 PE | PE | ATM | 9.15 | 9.15 | 9.25 | 606500 | 2358500 | +103500 | 17.70 | yes |
| 1230 CE | CE | ITM | 19.9 | 20.05 | 20.2 | 224500 | 574500 | −31000 | 17.09 | yes |
| 1250 PE | PE | ITM | 14.25 | 14.15 | 14.3 | 313500 | 2657500 | +37000 | 17.33 | yes |
| 1250 CE | CE | OTM | 8.65 | 8.7 | 8.8 | 1128000 | 5907500 | +214000 | 16.72 | yes |
| 1230 PE | PE | OTM | 5.6 | 5.65 | 5.7 | 534500 | 1405500 | +51000 | 17.70 | yes |
| 1270 PE | PE | ITM | 28.45 | 28.5 | 28.8 | 73000 | 1075500 | −25500 | 18.55 | yes |
| 1270 PE SEP | PE | ITM | 28.45 | 28.5 | 28.8 | 73000 | 1075500 | −25500 | 18.07 | yes |

October **2026-10-27** (DTE 34, hint OCT never stored as expiry):

| Query | Right | Moneyness | LTP | Found | Chain expiry |
| --- | --- | --- | --- | --- | --- |
| 1270 PE OCT / OCTOBER 2026 / OCT 1270 PE / OCTOBER 1270 PE | PE | ITM | 39.5 | true | 2026-10-27 |
| 1270 CE OCT | CE | OTM | 22.9 | true | 2026-10-27 |
| 1240 CE OCT | CE | ATM | 37.35 | true | 2026-10-27 |
| 1240 PE OCT | PE | ATM | 24.6 | true | 2026-10-27 |
| 1230 CE OCT | CE | ITM | 42.5 | true | 2026-10-27 |
| 1250 PE OCT | PE | ITM | 29.25 | true | 2026-10-27 |
| 1250 CE OCT | CE | OTM | 32.0 | true | 2026-10-27 |
| 1230 PE OCT | PE | OTM | 19.95 | true | 2026-10-27 |

Identity: requested symbol/strike/right/resolved expiry == observed in every found row. CE never became PE. SEP LTP ~28.45 vs OCT LTP ~39.5 — no cross-expiry contamination.

Unavailable:

- `RELIANCE 9999 CE` → `found=false`, detail `CE 9999 was not found in the real chain for this expiry`. No ATM/neighbour fill.
- `RELIANCE 1270 PE JANUARY 2027` → `CONTRACT UNAVAILABLE` — another expiry was not substituted; no chain.

## Freshness (LIVE VERIFIED)

| Stream | Label | Vote |
| --- | --- | --- |
| underlying_quote | LIVE (same-day `2026-09-23T03:55–03:57Z`) | yes |
| candles_m15 | LIVE `2026-09-23T03:45:00Z` (09:15 IST open of in-progress bar) | yes |
| option_chain | LIVE (HTTP/as_of) | yes |
| futures | LIVE | yes |

NIFTY persisted glance: **LAST_OBSERVED / STALE / 18 Sep · 16:00 IST** — not LIVE. Session chrome remained LIVE.

Stale isolation: pytest fixtures (2203) still withhold stale quote/chain/M15 votes. Threshold unchanged.

**PROVIDER LIMITATION:** chain freshness is not a per-leg matching-engine timestamp. Not invented.

## Watch (LIVE VERIFIED)

Pin `a6f1c96a853c41a7a8152441ac7c4d7a`.

T0 **immutable**: `RELIANCE 1270 PE` / **2026-09-29** / `CONFIRMATION_PENDING` / observation `e9687121c74e48c8b15d250dfd6ba133` before and after.

| Transition | Latest identity | Categories include |
| --- | --- | --- |
| SEP → OCT 1270 PE | 2026-10-27 PE 1270 | **EXPIRY_CHANGED** (2026-09-29 → 2026-10-27), not hint OCT |
| OCT → SEP 1270 PE | 2026-09-29 PE 1270 | STATE/PATTERN vs T0 (same expiry as T0) |
| 1270 PE → 1250 PE SEP | 1250 PE 2026-09-29 | **STRIKE_CHANGED** |
| 1270 PE → 1270 CE SEP | 1270 CE 2026-09-29 | **OPTION_TYPE_CHANGED** |
| option → RELIANCE | UNDERLYING | **OBSERVATION_SCOPE_CHANGED** only |
| return 1270 PE SEP | 1270 PE 2026-09-29 | **STATE_CHANGED** / **PATTERN_CHANGED** |

Watch API latest ~0.6–0.7 s.

## Evidence

Volume rows LIVE: `direction=NEUTRAL` — “observable activity imbalance only, not a directional vote”. Groups preserved. CONFLICT remains CONFLICT. Missing strike does not invent a contract.

## Liquidity / usability

Identity separate from `liquidity_grade` (excellent/good). **ENGINEERING RUBRIC ONLY. NOT A SMART-MONEY SCORE.** Not a live-validated market model.

## Discover (LIVE VERIFIED)

Job **`1d00362c01ee`** POST `/api/research/jobs/discover` **09:26 IST**.

| Item | Value |
| --- | --- |
| Stage 1 | **210/210**, 0 fail |
| Stage 2 | **164/164**, 0 fail (promotion vs Gate 1.1 202 — live conditions, not provider failure) |
| timeouts / timestamp quality | 0 |
| elapsed | **66.2 s** (Gate 1.1 ~71 s; M15 intraday merge retained) |
| market_state | LIVE_SNAPSHOT |

Screener UI: `MARKET OPEN · RESEARCH MODE LIVE`, last scan **23 Sept, 09:26 am IST · 210 SYMBOLS**.

## UI (LIVE VERIFIED)

Netlify Symbol `RELIANCE 1270 PE OCTOBER 2026`: **PE 1270 2026-10-27**, **DTE 34**, MARKET OPEN, RESEARCH MODE LIVE. Not PE 1270 2026-09-29. No BUY/SELL.

| Viewport | Overflow |
| --- | --- |
| 1440×900 | none |
| 1280×800 | none |
| 390×844 | none |
| 360×800 | none |

NIFTY strip: LAST OBSERVED Friday persist (honest). After Discover, scan strip can show NIFTY “this scan” separately.

## Performance

| Path | Elapsed |
| --- | --- |
| RELIANCE (no hint) | 8.3 s wall / 6.27 s API |
| specific contracts | 3.3–4.6 s typical |
| Watch latest | 0.59–0.74 s |
| Discover | 66.2 s |

## Tests

pytest **2203 passed**. ruff clean. mypy `app --strict` clean. No tests deleted or xfails added.

## Production

- Railway: **`82b446bf-3350-4a7a-b009-8c65d9ecd8c1`** (no redeploy; code unchanged)
- Netlify: **`6ab0fe045b7bbcf864221b82`** (no frontend change)
- API / Symbol / Watch / Screener: LIVE VERIFIED above

## Safety

`broker_execution=impossible`. No BUY/SELL/order path. `SAFETY_LOCK.txt` retained.

## Known limitations

- **LIVE LIMITATION:** persisted NIFTY glance can remain Friday LAST_OBSERVED until a same-day index quote is stored.
- **PROVIDER LIMITATION:** option-chain freshness is HTTP/as_of, not per-leg matching-engine time.
- **ARCHITECTURAL LIMITATION:** Discover job registry is in-memory; GET `/api/research/discover` is still synchronous (not used as the certified path).
- **OFFLINE / ENGINEERING:** liquidity grades are a documented rubric, not a smart-money score.
- **LIVE CONDITION:** Stage 2 promotion 164 vs prior 202 — 0 provider failures.

## Next allowed gate

**NONE.** Do not start Gate 3.
