# Pre-market contract research & Tomorrow Watch

Date/time: **2026-09-20 22:55 IST** (Sunday).  
Branch: `phase-3-historical-validation`.

## Objective

Make a closed/pre-market Symbol or Watch research view answer, for a verified contract such as RELIANCE 1270 PE: what was last observed, when, what the underlying vs the contract showed, what changed from T0, which evidence groups agree/conflict/are missing, what would confirm or invalidate, whether the contract is verified, and what must be re-checked when the next live session opens.

This is not a prediction engine. It does not answer “will RELIANCE fall tomorrow?”

## Architecture

Extends certified closed/pre-market research. Does not replace it.

- `session_window` remains OPEN / PRE_OPEN / CLOSED.
- `research_session_mode` remains LIVE / PRE_MARKET / POST_MARKET / CLOSED.
- `observation_kind` is LIVE only when OPEN; otherwise LAST_OBSERVED.
- `TomorrowWatchView` is attached to `AnalyzeResponse.tomorrow_watch` when `session_window != OPEN`.
- When OPEN, `tomorrow_watch` is `null` and the live path is unchanged.
- AT OPEN CHECK lists refresh/verify/re-evaluate steps only. No BUY/SELL language.

PRE-MARKET RESEARCH DOES NOT CONSTITUTE LIVE F&O CERTIFICATION.

GATE 1 REMAINS PENDING UNTIL A REAL LIVE MARKET SESSION IS SUCCESSFULLY VALIDATED.

## API

Existing `POST /api/analyze` response is extended (no parallel API):

- `session` — unchanged calendar payload (`session_window`, `research_session_mode`, `observation_kind`, `next_session_open_ist_label`, `live_discover_available`).
- `tomorrow_watch` — `null` on OPEN; otherwise one-line summary, grouped evidence, missing, confirm_if, invalidate_if, at_open_check, contract_dte from the matching term-structure row.
- Watch T0 comparison remains on the existing watch record (`t0` / `latest` / `changes`). Missing fields are not compared. T0 is not rewritten.

POST `/api/research/jobs/discover` remains **409** when not OPEN.

## UI

Symbol hero (closed/pre-market):

- MARKET CLOSED / PRE-OPEN / POST-MARKET
- PRE-MARKET RESEARCH (or POST-MARKET RESEARCH)
- LAST OBSERVED timestamp
- NEXT SESSION from the calendar (UNKNOWN never invented here; calendar always supplies the next regular 09:15 IST)
- RESEARCH STATE (verbatim)
- CONTRACT identity when a specific contract was requested
- One-line deterministic summary
- UNDERLYING vs CONTRACT blocks kept separate
- WHY from existing blocker/development text plus grouped evidence statuses
- MISSING / CONFIRM IF / INVALIDATE IF, with NOT AVAILABLE when the engine has no condition
- TOMORROW WATCH evidence groups (only groups that exist)
- AT OPEN CHECK

Watchlist: LATEST VERIFIED, SINCE T0, NOT AVAILABLE conditions, AT OPEN CHECK, NEXT SESSION.

No BUY / SELL / ORDER buttons.

## Watch integration

T0 remains immutable. Closed-market re-analysis does not create a fake Watch event. Comparable LTP/OI/IV/underlying_last diffs require both snapshots to have the field. Scope/strike/expiry/option-type changes keep their existing categories.

## Evidence behavior

Uses the existing Evidence Matrix groups. OI, ΔOI, volume, and IV are not four independent confirmations. No numeric score, probability, or confidence percentage is introduced. STALE isolates a group; MARKET_CLOSED last-prints do not relabel a group as STALE and do not upgrade CONFLICT to CONFIRMED.

## Contract behavior

Observed expiry is `option_chain.expiry` / `requested_contract.expiry`. DTE is copied only from the matching term-structure row. `term_structure.expiries[0]` is never used as a generic contract expiry. Unverified requested contracts remain CONTRACT UNVERIFIED.

## AT OPEN CHECK

Deterministic checklist generated only when the session is not OPEN. Typical items: refresh underlying, refresh option chain (if a specific contract), verify strike/type/expiry/bid-ask/liquidity, recalculate freshness, re-evaluate groups and research state, re-check existing confirm/invalidate/missing text. No directional forecast.

## Test results

- pytest: **2192 passed** (was 2173).
- ruff `app tests scripts/certify_live_fno_scan.py`: clean.
- mypy `app --strict`: clean.

Covered: closed Sunday, pre-open, PRE_MARKET before 09:00, OPEN omits Tomorrow Watch, POST_MARKET, next-session 09:15 IST, special-session hours not invented, verified/invalid/missing expiry + matching DTE, contract unverified note, Watch T0 immutability / missing-field comparison / no material change / scope / expiry, evidence grouping without scores, CONFLICT preserved, STALE isolation, AT OPEN CHECK language, confirmation/invalidation not manufactured, HTML Tomorrow Watch + no BUY/SELL, Gate 1 script still requires OPEN.

## MCP / browser results

Local `http://127.0.0.1:8000` and production `https://tire-research-terminal.netlify.app`.

Viewports: **1440×900**, **1280×800**, **390×844**, **360×800**. Horizontal overflow **0** at 390 and 360; desktop scrollWidth ≤ client (no page-level overflow).

RELIANCE then RELIANCE **1270 PE**:

- MARKET CLOSED, PRE-MARKET RESEARCH, LAST OBSERVED 18 Sep, NEXT SESSION **21 Sep 2026 · 09:15 IST**
- CONTRACT RELIANCE 1270 PE **2026-09-29**, DTE **9**, LTP 32.5 / OI / ΔOI / IV / Bid / Ask
- UNDERLYING RELIANCE last observed ₹1226.40
- RESEARCH STATE **CONFLICT** (not upgraded)
- WHY, MISSING, CONFIRM IF, INVALIDATE IF, TOMORROW WATCH, AT OPEN CHECK
- No BUY / SELL / ORDER buttons, no prediction, no confidence score
- LIVE DISCOVER UNAVAILABLE and disabled

## Production results

Railway deployment: `b49953ec-6616-4bcc-b742-42302874772b` SUCCESS.  
Netlify: `6ab016e7e77aff83899b2710`.  
Frontend: https://tire-research-terminal.netlify.app  
API: https://api-production-983e.up.railway.app

| Check | Result |
| --- | --- |
| Health | 200, `broker_execution=impossible`, `qwen_enabled=false`, `data_root=/data` |
| Session | CLOSED, research_session_mode CLOSED, observation_kind LAST_OBSERVED |
| Next session | 21 Sep 2026 · 09:15 IST |
| POST `/api/research/jobs/discover` | 409 LIVE DISCOVER UNAVAILABLE — MARKET CLOSED |
| Analyze `RELIANCE 1270 PE` | 200, CONFLICT, MARKET_CLOSED_LATEST_DATA, `tomorrow_watch` present, DTE 9 |
| Observed expiry | option_chain and requested_contract **2026-09-29** |
| CORS | `https://tire-research-terminal.netlify.app` |
| Frontend api-base | Railway HTTPS; no localhost |
| Gate 1 harness | exit 2 PENDING — MARKET CLOSED |

## Limitations

- Next session time is the **regular** 09:15 IST open. Special-session hours are not invented.
- Tomorrow Watch is omitted while `session_window=OPEN`; live research uses the existing LIVE path.
- Confirmation/invalidation text is copied from the existing engine. When none exists, the UI shows NOT AVAILABLE.
- Production Watch list was empty at this verification (no pins on `/data`). T0 immutability is certified by unit tests and local Watch UI.
- LIVE Discover remains unavailable until a real OPEN session. This work does not certify live F&O.

## Explicit statements

PRE-MARKET RESEARCH DOES NOT CONSTITUTE LIVE F&O CERTIFICATION.

GATE 1 REMAINS PENDING UNTIL A REAL LIVE MARKET SESSION IS SUCCESSFULLY VALIDATED.
