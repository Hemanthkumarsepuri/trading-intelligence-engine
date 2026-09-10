# TIRE Sprint 2 — Whole-market discovery (code as implemented)

Date: 9 September 2026. This describes **what the code does**, not a
wish list.

## Universe

- **Source:** `upstox_instrument_master.NSE_FO_equity_underlyings`
  (`list_fo_eligible_equity_underlyings()`). Distinct `underlying_symbol`
  values on `NSE_FO` rows, minus `NSE_INDEX` trading symbols.
- **Size:** determined at scan time from the loaded instrument master
  (previously measured ~210 equities; the UI prints the **actual count**
  for that run).
- **Not whole-market:** `GET /api/research/daily?symbols=...` and
  `GET /api/research/discover?symbols=...` set
  `scan_mode=EXPLICIT_SYMBOL_QUERY` and `universe_source=explicit_symbols_override`.
- **F&O ban:** `FNO_BAN_STATUS_UNKNOWN`. No authoritative ban feed is
  wired. TIRE does not guess banned/not-banned.
- **Duplicates / indices / cash-only:** indices excluded by master
  cross-reference. Cash-only names are not in this universe. Duplicate
  underlyings collapse via set membership.
- **Unusable derivatives:** Stage 1 rejects symbols with no upcoming
  F&O expiry (`nearest_expiry`) or no `NSE_EQ` key.
- **Survivor cap:** default 30 Stage-2 promotions. When more names match
  Stage 1, `stage1_cap_applied=true` and `truncated_at_stage1` is
  counted. The UI states this is a provider-safety limit, not a claim
  that truncated names were uninteresting.

## Stage 1 (cheap)

Batched `get_quotes()` (chunks of 50) via `BatchedQuoteProvider`
(Upstox adapter today). **No option chain, no IV/OI/PCR, no futures
history, no news.**

Named observations (membership, not a score):

| Observation | Required quote facts | Must not claim |
|---|---|---|
| `DEVELOPING_MOMENTUM` | moderate day-change still near session VWAP-equivalent | option evidence |
| `ORDER_FLOW_PARTICIPATION` | buy/sell quantity imbalance | accumulation / smart money |
| `EARLY_REVERSAL` + `FAILED_BREAKDOWN_RECLAIM_CANDIDATE` | day-change sign vs position in today's range | trapped traders / Stage-2 reclaim |
| `PRE_BREAKOUT_COMPRESSION_CANDIDATE` | today's range compressed **and** price near session high/low | multi-day compression, `PRE_BREAKOUT_COMPRESSION` |
| `RELATIVE_STRENGTH_VS_INDEX` | stock vs Nifty day-change gap when a Nifty index quote is on the same batch | sector RS; omitted if Nifty quote missing |

Promotion order: more independent observations, then symbol. Reasons
are `Promoted because: A + B`, never a numeric score.

## Stage 2

Unchanged `run_analysis()` pipeline (chain, OI, IV, futures, M15/D,
news, evidence matrix, decision engine) on **promoted names only**,
concurrency 6.

Buckets: Developing Now (named structural patterns only), Events to
Monitor (`EVENT_DRIVEN`), Already Moved / Extended, Confirmation
Pending, Conflict, Data Problem. News without a named pattern is never
`DEVELOPING`.

## Already-moved

Not a single 6% rule. `classify_move_context()` uses day-change, session
range (Stage 2: last M15 session high-low; Stage 1: quote OHLC), and
`RANGE_COMPRESSION`. `BREAKOUT_CONFIRMATION` remains `ALREADY_MOVED`.

## API

`GET /api/research/daily` and alias `GET /api/research/discover`.

## Qwen

Optional explanation adapter. Not used by the scanner. TIRE runs if Qwen
is down.

## Known gaps

- Stage 1 compression is **today's range only**.
- News timing is inside Stage 2 per symbol (honestly labeled).
- Live whole-market wall-clock is still provider-throttling dominated.
- No F&O-ban feed. No cash universe. No 5paisa adapter.
