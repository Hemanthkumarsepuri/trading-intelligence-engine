# GATE 0 — Baseline integrity

Date/time: **2026-09-20 17:14 IST**.  
Branch: `phase-3-historical-validation`.  
HEAD: `4b40527`.

## Status

**PASS**

## What was tested

- `git status` / branch / `git log --oneline -20`
- Uncommitted work: only untracked `_tmp_reliance_analyze.json` (scratch analyze dump; not part of the product)
- Production health `GET https://api-production-983e.up.railway.app/api/health`
- Production session `GET /api/market/observed`
- Railway environment production service `api` deployment `59ff794c-59a4-4e35-8195-52d8df71af1d` SUCCESS, volume `/data`
- Netlify `https://tire-research-terminal.netlify.app/` HTTPS 200
- pytest / ruff / mypy
- Safety: no `place_order` / `modify_order` / `cancel_order` / `execute_order` in `app/`
- `SAFETY_LOCK.txt` present
- Observation Watch v1 files and RELIANCE 1270 PE regression tests remain
- Observed expiry wiring: `OptionChainVisual.expiry` / `RequestedContractVisual.expiry` from `report.option_chain.expiry`; screener `contract_expiry` from those fields; snapshot DTE only when term-structure row matches that expiry
- `TermStructure.nearest` still returns `expiries[0]` for **term-structure slope math only**. It is not used as observed contract expiry.

## Defects found

None in this gate.

## Fixes made

None. Baseline already intact.

## Tests

- pytest: **2167 passed** (86.42s)
- ruff `app tests scripts/certify_live_fno_scan.py`: clean
- mypy `app --strict`: clean (167 files)

## Production validation

| Check | Result |
| --- | --- |
| Health | 200 |
| `broker_execution` | `impossible` |
| `qwen_enabled` | `false` |
| `data_root` | `/data` |
| `token_configured` | `true` |
| Session | CLOSED, 2026-09-20, not a trading day, weekend |
| Latest research job | `status: NONE` |
| Frontend localhost / `127.0.0.1` / mixed `http://api` | absent in served HTML |
| Railway | SUCCESS `59ff794c-59a4-4e35-8195-52d8df71af1d` |

## Evidence

- Canonical contract tests: `tests/unit/research/test_watch_record.py` (`RELIANCE 1270 PE`, observed-chain expiry vs term-structure-only null)
- Whole-market cap (architecture, not a live scan): `DEFAULT_WHOLE_MARKET_SURVIVOR_CAP = 1000` in `run_daily_research`

## Next allowed gate

GATE 1 — Full F&O live scan
