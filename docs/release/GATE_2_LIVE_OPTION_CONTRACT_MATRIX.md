# GATE 2 — Live option contract matrix

Certification date: **22 September 2026**.  
IST at Phase 1: **19:05:22 IST** (Tuesday).  
Branch: `phase-3-historical-validation`.

**GATE 0 remains PASS.**  
**GATE 1 remains PASS.**  
**GATE 1.1 remains PASS WITH KNOWN LIMITATIONS.**

This document does **not** award Gate 2 PASS.

## Status

**LIVE CERTIFICATION DEFERRED — MARKET CLOSED**

Production `GET /api/market/observed` at 19:05 IST:

| Field | Value |
| --- | --- |
| calendar_date_ist | 2026-09-22 (trading day) |
| session_window | CLOSED |
| research_session_mode | POST_MARKET |
| observation_kind | LAST_OBSERVED |
| live_discover_available | false |
| next_session_open_ist | 23 Sep 2026 · 09:15 IST |
| broker_execution | impossible |

No live Discover, no live contract matrix, and no LIVE labels were fabricated from POST_MARKET prints.

## Phase 0 — current contract data flow

1. **Query** `parse_instrument_query` → symbol, strike, CE/PE, month hint, optional year. Hint is not a date.
2. **Dashboard** `run_analysis` passes `requested_strike` / `requested_right` / `requested_expiry_hint` / `requested_expiry_year` into `analyze_symbol`.
3. **Expiry** `resolve_expiry_for_hint` matches month/year on the instrument master; miss → `CONTRACT UNAVAILABLE` and **no nearest substitute**. Else `nearest_expiry()`.
4. **Provider** `UpstoxProvider.get_chain(underlying, expiry_date=observed ISO date)` — expiry is a request parameter, not inferred from LTP.
5. **Strike/right** `compare_contracts` looks up exact `strike` + `right` on that snapshot. Missing → `found=false`, `requested_not_in_chain_detail`, **empty alternatives** (no neighbour fill). Alternatives exist only when the requested leg is present, same right only.
6. **Liquidity** `assess_liquidity` is a separate grade (excellent/good/poor/untradeable) from identity. Stale quote or missing bid/ask → UNTRADEABLE without deleting identity.
7. **Freshness** stream labels: underlying quote, M15, option_chain (chain-level as_of / receipt), futures. Stale streams `usable_for_vote=false`. Liquidity also ages each leg vs `max_option_quote_age`. Chain-level LIVE does not invent a per-leg exchange matching timestamp (provider does not supply one).
8. **Evidence** grouped (price, OI, IV, RS, …). Volume imbalance is **NEUTRAL**, not bullish/bearish. Missing/stale does not confirm.
9. **Decision/state** `derive_research_state` / blockers; contract existence is not directional confirmation.
10. **Watch** `snapshot_from_analyze_payload` + `compare_snapshots`. T0 immutable. Categories include EXPIRY_CHANGED, STRIKE_CHANGED, OPTION_TYPE_CHANGED, OBSERVATION_SCOPE_CHANGED.
11. **UI** Symbol workspace prints `right strike expiry` from `requested_contract`; DTE from matching term-structure row (Gate 1.1).

### Findings

| ID | Class | Finding |
| --- | --- | --- |
| G2-1 | P1 | Watch snapshot preferred `parsed_expiry_hint` (`OCT`) over observed `requested_contract.expiry` / `option_chain.expiry`. Canonical identity leaked the hint. **Fixed offline** (see below). |
| G2-2 | INFO | AnalyzeResponse keeps `parsed_expiry_hint` separate from visual observed expiry. Correct split. |
| G2-3 | INFO | Per-leg option timestamps are not an exchange matching-engine clock; chain freshness is HTTP/as_of. Documented at Gate 1. Not a substitution bug. |
| G2-4 | INFO | Liquidity grades are documented engineering defaults, not a hidden “smart money” score. Identity stays even if UNTRADEABLE. |
| G2-5 | INFO | In-memory Discover registry still clears on deploy. |
| G2-6 | INFO | GET `/api/research/discover` still synchronous; certified path remains POST jobs. |

No P0 architecture blocker. Core identity path already refuses expiry/strike/CE-PE substitution.

## Offline validation (not live)

pytest **2203 passed**, ruff clean, mypy `app --strict` clean.

Covered offline:

- Query forms: `RELIANCE 1270 PE`, `… OCT`, `RELIANCE OCT 1270 PE`, `… SEP`, `… CE OCT`, `OCTOBER 2026`.
- October hint loads 2026-10-27 chain; JAN 2027 → CONTRACT UNAVAILABLE, no chain.
- Missing strike: `found=false`, no alternatives.
- CE alternatives never include PE.
- Watch T0 immutable; EXPIRY/STRIKE/OPTION_TYPE/SCOPE categories; snapshot expiry **2026-10-27** even when hint is OCT.

P1 fix: `app/domain/research/watch_record.py` — canonical expiry = observed chain/requested expiry only.

## Live matrix procedure (next OPEN session)

Do not run this while CLOSED. Exact dates must come from the live chain, not this file’s 21 Sep examples.

1. Confirm `session_window=OPEN`, `research_session_mode=LIVE`, `live_discover_available=true`.
2. `POST /api/analyze` RELIANCE (no hint) → record nearest expiry, ATM.
3. Matrix: ATM CE/PE, one ITM, one OTM, both rights, nearest and next expiry; mandatory `RELIANCE 1270 PE` SEP and OCT if those expiries still exist.
4. For each: requested vs observed underlying/expiry/strike/right; DTE vs matching term-structure row; LTP/bid/ask/volume/OI/ΔOI/IV/greeks if present.
5. JAN (or other missing month) → CONTRACT UNAVAILABLE.
6. Switch SEP↔OCT on 1270 PE; Watch EXPIRY_CHANGED; T0 unchanged; then same-contract field change (not EXPIRY_CHANGED); PE→CE OPTION_TYPE_CHANGED; 1270→1250 STRIKE_CHANGED; option→underlying OBSERVATION_SCOPE_CHANGED.
7. Stale isolation: M15/quote/chain `usable_for_vote`; LAST_OBSERVED indices not LIVE.
8. UI 1440×900, 1280×800, 390×844, 360×800; canonical date visible (not OCT-only).
9. Optional Discover timing vs Gate 1.1 ~71 s; do not drop intraday M15 merge.
10. Safety: `broker_execution=impossible`.

Until that OPEN run completes, Gate 2 stays **DEFERRED**.

## Production at this gate

Railway at start: `ff5bf311-755f-4af9-b816-f0625517fbca` (Gate 1.1).  
Netlify: `6ab0fe045b7bbcf864221b82`.

Watch expiry canonicalization requires an API deploy **after** this commit; it is not a live-matrix PASS.

## Next allowed gate

**NONE.** Do not start Gate 3, backtesting, profitability, or prediction.
