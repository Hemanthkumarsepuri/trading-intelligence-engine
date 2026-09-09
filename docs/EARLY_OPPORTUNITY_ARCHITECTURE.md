# TIRE — Early Opportunity Architecture

Status: implemented on top of the existing quality-gate system. This is
**not** a prediction engine. Zero valid developing names is a correct
result (`ZERO_VALID_EARLY_OPPORTUNITIES`).

The live map of the same design is the architecture canvas beside chat.

## What was reused (not redesigned)

- Evidence matrix, anti-double-counting, `derive_research_state()`
  precedence (`docs/research/RESEARCH_STATE.md`).
- Freshness engine: stale evidence does not vote.
- Two-stage daily scan (`screen_universe` → bounded Stage-2 `run_analysis`).
- Named development patterns, news no-lookahead, geopolitical transmission.
- Macro regime classifier (dynamic; not hardcoded to 9 Sep 2026).
- Liquidity / decay / contract quality as a **separate** question from
  direction and timing.
- `SAFETY_LOCK.txt` and startup `assert_broker_execution_disabled()`.

`ALREADY_MOVED` is a **timing/bucket** label. It is not a new
`ResearchState` member.

## Four questions that stay separate

1. **Direction** — evidence matrix / research state.
2. **Timing** — `TimingStage` + `early_stage_state` (early / developing /
   confirmed / already moved / extended).
3. **Contract quality** — liquidity, spread, IV/decay, DTE, structural
   quality.
4. **Confirmation** — what is missing, confirm-if, invalidate-if, blockers.

## Two-stage scan (unchanged shape, new classification)

```
F&O-eligible equity universe
        |
Stage 1  cheap quotes: unusual day-move, participation, VWAP distance;
         hard-exclude already-extended (~6% + range extreme)
         honesty: Stage 1 cannot see OI, chain, futures, or news
        |
survivor_cap (default 30)
        |
Stage 2  full run_analysis() per survivor (concurrency 6)
        |
_gate()  defensible direction + ACCEPTABLE contract
        |
classify_research_bucket() over ALL gated symbols
        |
DEVELOPING NOW  vs  ALREADY MOVED / CONFIRMED / EXTENDED / CONFLICT / DATA_INSUFFICIENT
```

The ranked top-N shortlist still exists as an explainable sort. It is no
longer the primary dashboard surface.

## Research buckets (no score)

Precedence in `classify_research_bucket()` — first match wins:

1. `DATA_INSUFFICIENT`
2. `CONFLICT`
3. `EXTENDED`
4. `ALREADY_MOVED` (`BREAKOUT_CONFIRMATION`)
5. `FALSE_BREAKOUT_RISK` without `FAILED_BREAKDOWN_RECLAIM` → `NOT_INTERESTING`
6. Illiquid F&O contract → `NOT_INTERESTING`
7. `CONFIRMED_SETUP` → `CONFIRMED`
8. Pre-breakout combo + early timing + acceptable liquidity + not
   `NO_TRADE`/`CONFIRMATION_PENDING` → `HIGH_QUALITY_DEVELOPING`
9. `EARLY_SETUP` + named pattern + early timing → `DEVELOPING`
10. Fresh material news + early timing → `EVENT_DRIVEN`
11. Other early-timing `WATCH` / `EARLY_SETUP` / `CONFIRMATION_PENDING` → `DEVELOPING`
12. else `NOT_INTERESTING`

`NO_TRADE` never becomes `HIGH_QUALITY_DEVELOPING`.

## Universe tiers (honest)

| Tier | Meaning | How TIRE knows |
|---|---|---|
| TIER_1_FO_LIQUID | F&O-eligible, acceptable contract liquidity | daily universe + liquidity grade |
| TIER_1_FO_ILLIQUID | F&O-eligible, unusable contract | liquidity `POOR`/`UNTRADEABLE` |
| TIER_2 cash-only | not classified | daily universe is F&O equities only |
| TIER_3 high-vol | not classified this pass | no independent high-vol universe |
| TIER_4 illiquid cash | not classified | no cash universe |
| TIER_5 F&O restricted | only if a real restriction flag is passed | none wired from Upstox today |

## Pattern library

See `docs/TIRE_EARLY_OPPORTUNITY.md` section C and
`app/domain/options/development.py`.

## Continuous research

`classify_development_evolution()` compares two already-computed research
states (T0 vs T1). It does not fetch. Snapshot persistence remains the
existing research journal / audit journal.

## What this pass deliberately did not fake

- 5paisa live adapter (no credentials; see provider matrix).
- Local Qwen 2.5 is an optional explanation adapter; it is not the decision engine.
- NSE/BSE scraping as a primary news feed.
- Local multi-timeframe historical OHLCV store.
- Sector leadership uses the official NSE Nifty 500 industry map when loaded.
  The Upstox instrument master is not the sector source. When the map is
  missing, the UI reports sector classification unavailable.
