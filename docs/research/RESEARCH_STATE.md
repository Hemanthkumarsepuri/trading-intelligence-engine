# Research-state semantics (actual behavior)

Research state is **maturity of a situation**, not a buy/sell instruction.
`TRADEABLE` on the API remains a **compatibility** label only.

## Precedence (`derive_research_state`)

1. `DATA_INSUFFICIENT` — core decision is unusable.
2. `CONFLICT` — independent evidence groups disagree.
3. `CONFIRMATION_PENDING` — quote **or** chain **or** M15 is not current. Futures-only staleness does not force this global state.
4. `NO_TRADE` — evidence does not justify active attention.
5. `EXTENDED` — `|day_change| >= 6%` and decision is TRADEABLE or WATCH.
6. `CONFIRMED_SETUP` — compatibility decision TRADEABLE with current quote, chain, and M15.
7. `EARLY_SETUP` — decision is WATCH **and** a named development pattern is not `NONE`.
8. `WATCH` — something to observe; no named pattern.
9. `UNKNOWN`

## EARLY_SETUP is not a score

`supporting_evidence_count >= 2` does **not** produce EARLY_SETUP.

Named patterns (`app.domain.options.development.classify_development`):

- `OI_MIGRATION` — meaningful CE/PE OI-weighted strike migration on a current chain.
- `RELATIVE_STRENGTH` — stock vs Nifty gap with non-opposing current M15.
- `FUTURES_STRUCTURE` — comparable basis **change** (not a single CURRENT_BASIS print).
- `PRE_BREAKOUT_COMPRESSION` — caller-verified range compression + early-stage structure + proximity to a real opposing level, on current quote and M15.
- `FAILED_BREAKDOWN_RECLAIM` — caller-verified failed breakdown that has been reclaimed, on current quote and M15.

`ALREADY_MOVED` is a **timing/bucket** label in `app.domain.options.early_opportunity`, not a `ResearchState` member. Adding it here would collapse timing into maturity.

Move context (`classify_move_context`) is separate: CONTAINED / BUILDING / MATURE / EXTENDED. The 6% day-change bar still yields EXTENDED; a 4%+ expansion without compression, or a >=5% intraday range without compression, is MATURE.

Each pattern carries: what is developing, why it matters, what is missing, CONFIRM IF, INVALIDATE IF, per-stream freshness note.

## Volume and PCR

- Volume imbalance is **activity**, always `NEUTRAL`/`UNKNOWN`, never BULLISH/BEARISH.
- PCR **level** is context only.
- PCR **change** requires a comparable prior chain and corroborating OI.

## Cash context

Sample breadth, delivery, and FII/DII are **not** EvidenceGroups and do not vote.
