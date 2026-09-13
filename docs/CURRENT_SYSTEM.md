# TIRE — current system (code after GREEN transformation pass)

This document describes **what the running codebase actually does**.
`docs/architecture/ARCHITECTURE.md` is a Phase-0 design (2026-08-27) and
is **not** a description of the current runtime. Prefer this file when
the two disagree.

## ARCHITECTURE

```
UI (app/api/static/index.html)
 → Research API (app/api/main.py)
 → Orchestration (dashboard_service, daily_research, options_intelligence_pipeline)
 → Domain intelligence (evidence matrix, research state, development, buckets, historical structure)
 → Normalized models (app.data.normalization)
 → Provider adapters (Upstox primary)
 → External sources
```

The UI never parses Upstox JSON. Domain code never imports Upstox response shapes.

Persistence is local JSONL under `data/persistence/`. `DATABASE_URL` / Redis
are unused placeholders.

## DATA SOURCES

| Stream | Producer | Status |
|---|---|---|
| Quotes, candles, option chain, futures, news | Upstox (wired) | Primary, actually used |
| Instrument master | Upstox | Loaded at startup |
| Official industry / sector | NSE Nifty 500 constituent CSV | Loaded at startup when possible |
| Delivery | NSE bhavcopy archive | Per-symbol, EOD |
| FII/DII | None verified | Always unknown; never fabricated |
| 5paisa | Not implemented | Documented only |
| Dhan | Adapter exists | Not constructed by the dashboard |

Conflicting current prints from two providers are `PROVIDER_CONFLICT`
(historical alias `PROVIDER_DISAGREEMENT`). They are never averaged.
A second live provider is **not wired**, so this event is classified
in tests only until a secondary is actually used.

## HISTORICAL STRUCTURE

Stage-2 research aggregates already-fetched M15 candles into IST session
bars (`app.domain.options.historical_structure`). No extra vendor.

- No lookahead: `bounded_series(..., as_of=)`.
- Fewer than 5 completed sessions → `INSUFFICIENT_HISTORY` (never called stable or compressing).
- Multi-day compression requires a tight lookback range **and** contracting true-range. Pipeline `PRE_BREAKOUT_COMPRESSION` requires that plus proximity to lookback extreme or a real S/R level.
- Stage 1 still uses **today's quote OHLC** only and labels candidates, not confirmed multi-day compression.

## FRESHNESS

Field-specific. Stale streams do not silently vote.

- Underlying quote: LTT or receipt time
- Candles: candle close; indicators refer to the last completed candle
- Option chain: HTTP receipt time when exchange matching time is unavailable — never implied as exchange event time
- Futures: LTT when present
- News: published + retrieved; failure is `NEWS_DATA_UNAVAILABLE`, not `NO_NEWS`
- Delivery: previous session / EOD
- FII/DII: unknown

A live quote can legitimately differ from the last completed M15 candle.
That is labeled LIVE QUOTE vs LAST COMPLETED CANDLE, not automatically an error.

PCR change without a prior chain snapshot is `INSUFFICIENT_HISTORY`, not STABLE.
Futures basis without a prior comparable print is `INSUFFICIENT_DATA`, not STABLE.

## RESEARCH STATES

See `docs/research/RESEARCH_STATE.md`. No probability, confidence %, or
BUY/SELL score exists. `TRADEABLE` is a compatibility label only.

UI shows human labels (`EARLY_SETUP` → "Developing opportunity"). Internal enums stay in the API.

MAIN BLOCKER follows `determine_blockers()`: contract illiquidity outranks "waiting for confirmation" (KAYNES). Insufficient history is a real blocker class but does not outrank contract unusable.

## EARLY OPPORTUNITY DEFINITIONS

Named patterns only (`PRE_BREAKOUT_COMPRESSION`, `FAILED_BREAKDOWN_RECLAIM`,
`OI_MIGRATION`, `RELATIVE_STRENGTH`, `RELATIVE_STRENGTH_ROTATION`,
`FUTURES_STRUCTURE`). WATCH + pattern NONE is never a developing setup.
Fresh news without a named pattern is `EVENT_DRIVEN` (Events to Monitor).

Timing / move context: EARLY / DEVELOPING / MATURE / EXTENDED / ALREADY_MOVED.
The ±6% day-change bar still marks EXTENDED; ATR multiples and session range
also apply when those facts exist. See `classify_move_context()` docstring.

## PROVIDER CAPABILITIES

See `docs/PROVIDER_CAPABILITY_MATRIX.md`. Upstox is the only production
provider. 5paisa is not production-ready in this repo.

Whole-market Stage 2 wraps the provider in `ScanSnapshotCache` so Nifty/history
calendar windows are not re-fetched for every survivor. Quotes are reused only
within the same UTC minute. This is reuse of one provider's snapshot, not averaging.

## SAFETY

`SAFETY_LOCK.txt` + `Settings.assert_broker_execution_disabled()` at
`real_lifespan` startup. No order routes exist. Analytics token cannot
place orders. Tests in `tests/safety/`.

## UI PHILOSOPHY

Open TIRE → market status, last scan, SCAN MARKET. Home does **not** auto-run
a whole-market scan. `POST /api/research/jobs/discover` starts background
discovery; `GET /api/research/jobs/latest` reports progress. GET
`/api/research/daily` remains a synchronous test/compat path.

Nav: DISCOVER / WATCHLIST / MARKET / RESEARCH / HISTORY.
Symbol research leads with what is happening, MAIN BLOCKER, confirm/invalidate.
Technical metrics stay under View Evidence. Optional **Explain simply** paraphrases
TIRE facts only (`POST /api/research/explain`).

## QWEN ROLE

Optional LM Studio adapter (`qwen2.5-coder-7b-instruct` at
`http://127.0.0.1:1234/v1`). Health is `GET /models` with a ~2s timeout
(never a generation). Explain timeout default 8s. Schema: summary,
developing_observation, supporting_evidence, conflicting_evidence,
missing_evidence, confirmation_condition, invalidation_condition,
data_quality_note.
Rejected: recommendation, buy/sell, probability, confidence, targets, stop-loss,
and unsupported claim phrases. On failure, the UI falls back to deterministic text.
If Qwen is down, TIRE continues. See `docs/TIRE_QWEN.md`.

## PHASE 3 — HISTORICAL REPLAY (added 2026-09-13, gap-closed same day)

`app.orchestration.historical_replay` runs the SAME `analyze_symbol()`/
`run_analysis()` pipeline against a chosen past instant, fed by
`HistoricalReplayProvider` (locally persisted M15 candles only — never a
live call). `analyze_symbol`'s `provider` parameter is typed against a
Protocol (`AnalysisProvider`, in `options_intelligence_pipeline.py`)
carrying an explicit `capabilities: ProviderCapabilities` declaration
(`historical_option_chain`/`historical_futures`/`historical_news`) rather
than checking a provider's class name. A missing historical option chain
is no longer fatal for a provider that structurally never had one
(`HistoricalReplayProvider`) — the pipeline degrades to price-only
evidence instead (technical/regime/relative-strength/PRE_BREAKOUT_
COMPRESSION/FAILED_BREAKDOWN_RECLAIM all already worked chain-free; only
OI_MIGRATION/FUTURES_STRUCTURE, which genuinely need chain/futures data,
are correctly never selected). It stays fatal, byte-for-byte unchanged,
for a live `UpstoxProvider` fetch failure. `decide()`/`determine_blockers()`
each gained one backward-compatible `derivatives_evidence_available: bool
= True` parameter — every pre-Phase-3 and every live call site is
provably unaffected (full 1956-test suite re-verified green after every
edit); `TRADEABLE` remains mathematically unreachable without a real
selected contract. A new `build_price_only_observation()` produces a real
`ResearchObservation` (contract fields honestly `None`,
`derivatives_evidence_available=False`) when price evidence alone
supports a named developing pattern — verified against the real sample
RELIANCE data (12 genuine `PRE_BREAKOUT_COMPRESSION` observations on one
real session). See `docs/HISTORICAL_REPLAY.md` for the full design and
its remaining, explicitly-scoped follow-ups (technical-level exposure for
invalidation outcomes; no UI surface yet).

## KNOWN LIMITATIONS

- Full-universe scans are Stage-1 screen + bounded Stage-2 concurrency
  (`survivor_cap` default 30). When the cap truncates, `stage1_cap_applied`
  is exposed; TIRE does not call a truncated scan complete coverage of every
  matching name.
- Stage 1 uses quote OHLC only for compression; it never claims option-chain
  or futures evidence. See `docs/TIRE_SPRINT2.md`.
- Default `history_lookback` is 10 calendar days (~5–7 sessions). Fewer than
  5 completed sessions cannot validate multi-day compression.
- F&O-ban status is `FNO_BAN_STATUS_UNKNOWN`.
- Option chain / futures / news health stay non-GREEN until a successful fetch this session (health does not probe them).
- No local multi-year OHLCV warehouse; no 5paisa live failover. A
  controlled, per-symbol local M15 candle store now exists for replay
  (`JsonlCandleRepository`), populated only for symbols/ranges an
  operator explicitly backfills — not an automatic whole-universe
  warehouse.
- Cash-only universe is not scanned by default.
- Ranked structural shortlist can still include gated `NOT_INTERESTING` names; they are not in Developing Now.
- Qwen is explanation-only and may be unreachable.
- Whole-market live timings must be taken from a restarted process; do not copy Sprint-2 numbers from an old PID.
