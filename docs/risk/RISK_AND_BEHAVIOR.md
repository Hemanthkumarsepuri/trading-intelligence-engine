# Risk Engine & Behavioral Guard — Design Reference

Status: **PROPOSED — awaiting approval**. Part of [Phase 0 architecture](../architecture/ARCHITECTURE.md).

This document exists separately from the main architecture because these rules become direct unit-test fixtures (`tests/unit/risk`, `tests/unit/behavior`, `tests/safety`) — keeping them here gives implementation something concrete to test against without re-deriving intent from the main doc.

---

## Part 1 — Risk Engine (`domain/risk`, `engines/risk_engine.py`)

### Inputs
`Setup` (from setup detection) + `SignalScore` (from signal engine) + `AccountRiskConfig` (from `.env`/config, currently `MAX_RISK_PER_TRADE`, `MAX_DAILY_LOSS`, `MAX_OPEN_POSITIONS` — all zeroed placeholders today, must be set to real values before Phase 3 exit) + `OpenExposureState` (current open paper/real positions, today's realized P&L, recent trade history).

### Computations (pure functions, each independently testable)

| Function | Output | Notes |
|---|---|---|
| `entry_zone()` | price band | Derived from the setup's trigger level, not a single tick — avoids false precision |
| `invalidation_level()` | price | The structural level that, if breached, proves the thesis wrong — **not** an arbitrary %, derived from market structure (e.g. beyond the swing low/high that defined the setup) |
| `stop_loss_reference()` | price | Normally equals `invalidation_level()`; kept as a separate function because for options the SL reference may need premium-based translation |
| `target_zones()` | list of prices | Derived from structure (next S/R) not a fixed R-multiple fantasy target |
| `risk_reward()` | ratio | `(target - entry) / (entry - invalidation)`, direction-adjusted |
| `position_size()` | quantity/lots | `min(config.max_risk_per_trade, remaining_daily_loss_budget) / (entry - invalidation)` in premium terms for options, capped by lot size |
| `exposure_check()` | pass/fail | Current open exposure + this candidate's size ≤ `MAX_OPEN_POSITIONS`/configured max notional |

### Hard checks (any failure = reject, independent of `SignalScore.composite`)

| Check | Rejects when |
|---|---|
| `RISK_REWARD_VALID` | `risk_reward()` below configured minimum (e.g. < 1.5) |
| `RISK_LIMITS_VALID` | position size would exceed `MAX_RISK_PER_TRADE`, or today's realized loss + this trade's max loss would exceed `MAX_DAILY_LOSS` |
| `MAX_OPEN_POSITIONS` | opening this candidate would exceed the configured concurrent-position cap |
| `CONSECUTIVE_LOSS_PROTECTION` | N consecutive losing trades (config threshold) within a configured lookback → cooldown period active |
| `DUPLICATE_SETUP_PROTECTION` | an equivalent open (or very recently closed) candidate already exists on the same underlying/direction/expiry bucket |
| `CORRELATION_EXPOSURE` | proposed candidate is highly correlated with existing open exposure (e.g. same index CE and a component stock's CE simultaneously) beyond configured limit |

Any hard check failing means the risk engine returns a reject object carrying the specific failed check name — this feeds directly into the trade gate's `RISK_REWARD_VALID`/`RISK_LIMITS_VALID` conditions (Architecture §18) and into the audit trail.

**Explicitly out of scope for the formula itself:** the *numeric values* of thresholds like "minimum 1.5 R:R" or "3 consecutive losses" are not fixed in this document — they belong in `AccountRiskConfig`, start at conservative defaults, and are only loosened based on paper-trading evidence (Architecture §20, §28).

---

## Part 2 — Behavioral Guard (`domain/behavior`, one detector per pattern)

Each detector is a pure function `(candidate, recent_history) -> BehavioralWarning | None`, `severity ∈ {LOW, MEDIUM, HIGH}`. A `HIGH` severity warning is itself a trade-gate condition (`NO_BEHAVIORAL_RED_FLAG` in Architecture §18) — it doesn't just get logged, it can block the trade even if every other check passed.

| Pattern | Detector logic (indicative) | Default severity trigger |
|---|---|---|
| **FOMO** | Entry trigger price within a small band of a resistance/support level already tested multiple times recently, or preceded by an unusually large single candle (> N×ATR) with no pullback | HIGH if entering directly into an untested extension after a > N×ATR spike |
| **No stop-loss** | `invalidation_level()` is null/undefined for the candidate, or SL distance is zero/negative | HIGH — this one is close to an automatic reject, not just a warning |
| **Averaging down** | An existing open (paper or real) losing position exists on the same underlying/direction and the new candidate would add exposure in the same direction at a worse price | HIGH |
| **Revenge trading** | A losing trade closed within a short configured window (e.g. last 30–60 min) and a new candidate appears on the same or a related underlying, especially with larger size than the prior trade | HIGH |
| **P&L anchoring** | Position sizing or target derivation traces back to "recover ₹X" rather than to `target_zones()`/structure — detected structurally by checking whether size/target inputs reference a fixed rupee recovery figure rather than risk-engine outputs (this is mostly a design-time guarantee: the risk engine's functions never take a "recover this much" parameter at all, so this pattern is prevented by construction, and the detector is a defense-in-depth check on config/inputs) | HIGH if such an input path is ever detected |
| **Greed / failure to protect gains** | An existing open profitable paper/real position has reached or exceeded its original target zone and no exit/trim has been recorded | MEDIUM — advisory, since this is about an *existing* position, not the incoming candidate |
| **Overtrading** | More than N trade candidates opened within a configured short window (e.g. > 3 in an hour) regardless of individual setup quality | MEDIUM, escalating to HIGH beyond a second configured threshold |
| **News chasing** | Candidate's setup timestamp falls within a short configured window after a detected news/event spike on the same underlying | HIGH |
| **Option lottery** | Reuses `domain/options`' `OPTION_LOTTERY` flag (far-OTM, poor liquidity) — surfaced here too so it appears in the behavioral trail, not just the options trail | HIGH |

### Design notes
- Detectors read from `analysis_runs`/`trade_candidates`/`paper_trades` history (via `persistence`), not from the LLM — behavioral detection must stay deterministic for the same reason price data must.
- Warnings are cumulative per cycle — a candidate can trigger multiple detectors; the trade gate's `NO_BEHAVIORAL_RED_FLAG` condition fails if *any* triggered warning is `HIGH` severity (configurable to a stricter "any MEDIUM+" policy once paper-trading data shows whether MEDIUM warnings correlate with real bad outcomes).
- These detectors are the direct implementation of the ten mistake patterns you listed at the top of the brief — each one maps 1:1 to a row above, and each is testable in isolation with synthetic history fixtures (no live account/broker needed to test this module).
