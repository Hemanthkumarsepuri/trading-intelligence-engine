# Pre-market / closed-market research mode

Date/time: **2026-09-20 17:45 IST** (Sunday).  
Branch: `phase-3-historical-validation`.

## Objective

Allow Symbol and Watch research when NSE is not OPEN, using **latest verified observations**, without relabelling those observations as live and without converting Gate 1 into a pass.

## Architecture

Existing calendar (`classify_session_window`) remains the only trading calendar.

`session_window` is unchanged: **OPEN / PRE_OPEN / CLOSED**.  
Live Discover and Gate 1 still treat only **OPEN** as a live F&O session.

Additive fields on the same object:

| Field | Meaning |
| --- | --- |
| `research_session_mode` | LIVE / PRE_MARKET / POST_MARKET / CLOSED |
| `observation_kind` | LIVE only when `session_window=OPEN`; otherwise LAST_OBSERVED |
| `next_session_open_ist` | Next regular 09:15 IST open from the same calendar |
| `live_discover_available` | true only when OPEN |

Weekend/holiday: `session_window=CLOSED`, `research_session_mode=CLOSED`. UI research label is **PRE-MARKET** (research before the next session).  
Trading-day before 09:00: CLOSED + PRE_MARKET.  
09:00–09:15: PRE_OPEN + PRE_MARKET.  
09:15–15:30: OPEN + LIVE.  
After 15:30 on a trading day: CLOSED + POST_MARKET.

Special/Muhurat sessions still do not invent hours.

## Data truth

- Closed prints remain LAST OBSERVED. They are never CURRENT/LIVE.
- Stale isolation for live voting is unchanged (`classify_market_data_state`, per-stream freshness).
- Observed option-chain expiry is still `option_chain.expiry` / `requested_contract.expiry`. Term-structure `expiries[0]` is not used as contract expiry.
- DTE is still copied only from a matching term-structure row.
- Unverified requested contracts surface **CONTRACT UNVERIFIED**.

## UI

- Glance/session pill: MARKET CLOSED / PRE-OPEN / MARKET OPEN / POST-MARKET.
- Screener: RESEARCH MODE, NEXT SESSION, empty scan stays **NO LATEST MARKET SCAN**. Live scan button disabled when not OPEN.
- Discover POST: **409** `LIVE DISCOVER UNAVAILABLE — MARKET CLOSED`.
- Symbol workspace: LAST OBSERVED timestamps, underlying/contract fields that exist, WHY / MISSING / CONFIRM IF / INVALIDATE IF, Watch T0 vs latest verified, WHAT CHANGED only for comparable same-scope fields.
- Quote source labelled LAST OBSERVED when the session is not OPEN.

## Watch

T0 remains immutable. Closed-market re-analysis does not rewrite T0. Scope semantics unchanged. LTP/OI/IV/underlying_last diffs are EVIDENCE_CHANGED only when **both** snapshots have the field.

## Tests

- pytest: **2173** passed (was 2167).
- ruff: clean.
- mypy `app --strict`: clean.

## Production verification

Railway deployment: `868f8548-1280-438c-ae3d-493a8d57d225` SUCCESS.  
Netlify: `6aafcb372630dac5966f4678`.  
Frontend: https://tire-research-terminal.netlify.app  
API: https://api-production-983e.up.railway.app

| Check | Result |
| --- | --- |
| Health | 200, `broker_execution=impossible`, `qwen_enabled=false`, `data_root=/data` |
| Session | CLOSED, research_session_mode CLOSED, observation_kind LAST_OBSERVED |
| Next session | 21 Sep 2026 · 09:15 IST |
| POST `/api/research/jobs/discover` | 409 LIVE DISCOVER UNAVAILABLE — MARKET CLOSED |
| Analyze `RELIANCE 1270 PE` | 200, CONFLICT, MARKET_CLOSED_LATEST_DATA, session LAST_OBSERVED |
| Observed expiry | option_chain and requested_contract **2026-09-29**; parsed_expiry_hint null |
| MCP 1440 / ~1280 / 390 / 360 | no horizontal overflow; MARKET CLOSED; RESEARCH MODE PRE-MARKET; scan button disabled |
| Last observed quote | RELIANCE ₹1226.40 · 18 Sep · not CURRENT PRICE |
| Contract | PE 1270 2026-09-29 last observed LTP/OI/IV/bid/ask |
| Localhost / mixed content | absent |
| BUY/SELL buttons | none |
| Gate 1 harness | exit 2 PENDING — MARKET CLOSED |

## Known limitations

- Next session time is the **regular** 09:15 IST open. Special-session hours are not invented.
- GET `/api/research/daily` remains callable (explicit-symbol research in tests). The product live-scan button uses POST `/api/research/jobs/discover`, which is blocked when not OPEN.
- Historical replay dataset is still unavailable. This mode does not create one.

## Explicit statements

PRE-MARKET RESEARCH DOES NOT CONSTITUTE LIVE F&O CERTIFICATION.

GATE 1 REMAINS PENDING UNTIL A REAL LIVE MARKET SESSION IS SUCCESSFULLY VALIDATED.
