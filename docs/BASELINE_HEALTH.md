# TIRE baseline health (Master Green Build, Stage 0)

Recorded **before** Master Green production-behavior changes. Test counts are a baseline, not proof of research-truth.

## Engineering

| Check | Result |
| ----- | ------ |
| pytest collection | 1641 |
| passed | 1641 |
| failed | 0 |
| skipped | 0 |
| `ruff check app tests` | All checks passed |
| `mypy app` (strict) | Success, 139 source files |

## Safety

- `BROKER_ORDER_EXECUTION_ENABLED=true` fails in `Settings.assert_broker_execution_disabled()`.
- `real_lifespan` calls that assert **before** `httpx.AsyncClient()` and providers.
- No `place_order` / `modify_order` / `cancel_order` in `app/`.
- IPO `estimated_probability` is retail **allotment** math, not options-trade probability. Keep labeled as such.

## Data sources

- Upstox quotes, M15, option chain, futures, index context, news (authorized).
- Option-chain `as_of` is **HTTP receipt time**, not matching-engine time.
- Sample Nifty 50 breadth (not official NSE A/D).
- Delivery: NSE `sec_bhavdata_full` EOD / previous session.
- FII/DII: UNKNOWN unless caller-supplied.
- Holiday overlay file existed but contained **no dates** (weekend-only) until this build’s calendar stage.
- Unused runtime pins still in `pyproject.toml` / settings: SQLAlchemy, asyncpg, Redis, APScheduler (not imported by `app/` orchestration). Not removed in Stage 0.

## Known semantic risks (pre-change)

1. `EARLY_SETUP` used `supporting_evidence_count >= 2` (hidden score).
2. Evidence VWAP voted from **rolling** multi-day M15 VWAP, not session VWAP (honestly labeled).
3. `technical_price_levels` copy said “session VWAP” while using rolling VWAP.
4. UI still used “WHY NOT ACTIONABLE NOW” wording.
5. `StreamFreshness` lacked explicit `usable_for_vote` / completeness fields.
6. Muhurat / special sessions not modeled.
7. Browser UAT of the live dashboard had not been executed.

## Product gaps (deferred unless this build closes them)

- No exchange per-leg chain timestamps from Upstox producer.
- No verified FII/DII parseable producer.
- Session VWAP not yet computed (M15 **does** contain IST timestamps + volume — implementable without a new vendor).
- Daily UAT script does not pass cash-context kwargs.
