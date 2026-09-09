# Provider capability matrix — Upstox vs 5paisa vs unused Dhan

TIRE remains **read-only**. Order, portfolio, and funds APIs must never
be called, including 5paisa Place Order and Dhan order endpoints.

Conflicting prints are **not averaged**. A real disagreement is a
`PROVIDER_CONFLICT` data-quality event (historical name:
`PROVIDER_DISAGREEMENT`). This session does not yet emit that event in
the live pipeline because a second live provider is not wired.

## Capability matrix (public docs + what TIRE actually calls)

| Capability | Upstox (wired, primary) | 5paisa Xstream (documented, not wired) | DhanHQ v2 (adapter exists, unused by dashboard) |
|---|---|---|---|
| Quotes / LTP / OHLC | Yes — batched quotes are Stage 1 | Yes — market snapshot | Yes |
| Historical candles | Yes — used (M15/D and other provider intervals) | Yes — 1m, 5m, 10m, 15m, 30m, 60m, 1d; expired F&O historical not supported | Yes |
| Option chain | Yes — primary chain path | Yes — getExpiry + getOptionChain | Yes |
| Greeks | Where Upstox supplies them | Documented | Documented |
| Futures / OI | Yes | Snapshot includes OI | Yes |
| News | Yes — `get_news()`, timestamped, no-lookahead filtered | Not used | Not used |
| Websocket | Yes (optional live path) | Documented | Documented |
| Instrument master | Yes | Scrip master documented | Yes |
| Order APIs | **Forbidden** (`SAFETY_LOCK.txt`) | **Must never be called** | **Must never be called** |
| Status in TIRE this session | Live primary | Documented only — no adapter, no token | Code present, dashboard does not construct it |

Sources for 5paisa public surface (re-verify at wiring time):

- https://xstream.5paisa.com/dev-docs/market-data-system/historical-candles
- https://xstream.5paisa.com/dev-docs/market-data-system (market snapshot)

## Fallback / cross-validation policy (when a second provider is wired)

1. Primary path remains Upstox.
2. Secondary is used for redundancy, capability coverage, historical
   backfill, and cross-checks — not to manufacture extra evidence groups.
3. If both return current, comparable values that disagree beyond
   documented tolerance → `PROVIDER_DISAGREEMENT`; neither print votes.
4. If primary is unavailable and secondary is current → use secondary
   **and declare the source**. Do not silently swap.
5. Never blend IV, OI, or LTP from two brokers into one “better” number.

## Historical research honesty

Provider-native intervals are labeled as such. Locally resampled bars
must be labeled resampled. Missing granularity must remain missing
(`INSUFFICIENT_HISTORY`). This session does not add a new local OHLCV
store.
