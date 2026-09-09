# TIRE — current system (code as of Sprint 1)

This document describes **what the running codebase actually does**.
`docs/architecture/ARCHITECTURE.md` is a Phase-0 design (2026-08-27) and
is **not** a description of the current runtime. Prefer this file when
the two disagree.

## ARCHITECTURE

```
UI (app/api/static/index.html)
 → Research API (app/api/main.py)
 → Orchestration (dashboard_service, daily_research, options_intelligence_pipeline)
 → Domain intelligence (evidence matrix, research state, development, buckets)
 → Normalized models (app/data/normalization)
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

## FRESHNESS

Field-specific. Stale streams do not silently vote.

- Underlying quote: LTT or receipt time
- Candles: candle close; indicators refer to the last completed candle
- Option chain: ~60s currentness unless inspection of `PipelineConfig` says otherwise
- Futures: LTT when present
- News: published + retrieved; failure is `NEWS_DATA_UNAVAILABLE`, not `NO_NEWS`
- Delivery: previous session / EOD
- FII/DII: unknown

A live quote can legitimately differ from the last completed M15 candle.
That is labeled LIVE QUOTE vs LAST COMPLETED CANDLE, not automatically an error.

## RESEARCH STATES

See `docs/research/RESEARCH_STATE.md`. No probability, confidence %, or
BUY/SELL score exists. `TRADEABLE` is a compatibility label only.

## EARLY OPPORTUNITY DEFINITIONS

Named patterns only (`PRE_BREAKOUT_COMPRESSION`, `FAILED_BREAKDOWN_RECLAIM`,
`OI_MIGRATION`, `RELATIVE_STRENGTH`, `RELATIVE_STRENGTH_ROTATION`,
`FUTURES_STRUCTURE`). WATCH + pattern NONE is never a developing setup.
Fresh news without a named pattern is `EVENT_DRIVEN` (Events to Monitor).

Timing / move context: EARLY / DEVELOPING / MATURE / EXTENDED / ALREADY_MOVED.
The ±6% day-change bar still marks EXTENDED (same as `derive_research_state`);
it is not the only rule. See `classify_move_context()` docstring.

## PROVIDER CAPABILITIES

See `docs/PROVIDER_CAPABILITY_MATRIX.md`. Upstox is the only production
provider. 5paisa is not production-ready in this repo.

## SAFETY

`SAFETY_LOCK.txt` + `Settings.assert_broker_execution_disabled()` at
`real_lifespan` startup. No order routes exist. Analytics token cannot
place orders. Tests in `tests/safety/`.

## UI PHILOSOPHY

Home: Market Status, Developing Now, Events to Monitor, Already Moved,
Data Quality / System Status, Your Open Decisions.

Symbol cards default to plain language. Technical metrics live under
**View Evidence**. `/api/health` traffic lights are factual: GREEN only
when configured + reachable + a valid response for that stream.

## QWEN ROLE

Optional LM Studio adapter (`qwen2.5-coder-7b-instruct` at
`http://127.0.0.1:1234/v1`). Explains validated facts only. Cannot set
research state, invent prices/news, or emit BUY/SELL/probability.
If Qwen is down, TIRE continues. `OLLAMA_MODEL=qwen3:30b` in `.env.example`
is a leftover placeholder and is **not** the live explanation path.

## KNOWN LIMITATIONS

- Full-universe scans are still Stage-1 screen + bounded Stage-2 concurrency; wall clock is dominated by per-symbol Upstox calls.
- Option chain / futures / news health stay non-GREEN until a successful fetch this session (health does not probe them).
- No local multi-year OHLCV warehouse.
- No F&O-ban flag from Upstox.
- Cash-only universe is not scanned by default.
- Pipeline `classify_development()` still does not itself compute compression/headroom; daily cards may surface `PRE_BREAKOUT_COMPRESSION` from `_pre_breakout_signal()`.
- Ranked structural shortlist can still include gated `NOT_INTERESTING` names; they are not in Developing Now.
- Qwen is explanation-only and may be unreachable.
