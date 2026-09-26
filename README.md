# Trading Intelligence & Risk Engine (TIRE)

Institutional-style Indian NSE equity/F&O market research
and risk decision-support system.

## Safety

This project is initially research/paper-analysis only.

It MUST NOT:
- place broker orders
- modify positions
- automatically execute trades
- override risk controls
- use an LLM as the source of financial data

## Product contract and certification status

- [`docs/product/PRODUCT_CONTRACT.md`](docs/product/PRODUCT_CONTRACT.md) —
  the canonical product contract: what TIRE is and is not, and its 18 core
  principles, each with its invariant, enforcement, tests, status and any
  open defect.
- [`docs/certification/FORENSIC_1fc5303.md`](docs/certification/FORENSIC_1fc5303.md) —
  the latest forensic certification (baseline `1fc5303`): **RED**. Open
  defects include historical-replay lookahead (C-1) and missing API
  authentication (H-1). Browser (Playwright) and live-market certification
  have **not** been performed.

## Architecture

Two separate analytical domains (Options Intelligence, IPO Intelligence),
coupled only through a single query-routing check, each following the
same pipeline shape:

Real provider data (Upstox)
→ Normalization
→ Domain analysis (technical / options / IPO engines)
→ Immutable snapshot
→ Append-only audit journal
→ API response

No LLM is ever the source of financial data or a required part of this
pipeline — see the Safety section above. There is no live LLM package on
the request path.

## Persistence

The running application persists everything to local JSONL files under
`data/persistence/` (options audit journal, IPO audit journal, quotes,
option chains, IV observations) — append-only, no update/delete method
exists anywhere in the persistence layer. This is the ACTUAL, current
persistence architecture; there is no database in the runtime path.

- `DATABASE_URL` exists in `app/config/settings.py` (a Postgres-shaped
  default) but is **not read or used anywhere else in the codebase** —
  it is a placeholder from an earlier architectural plan, not a real
  dependency.
- Postgres and Redis are **not** part of the runtime architecture.
- No Railway (or other) deployment is currently active; this runs
  locally.
- If a Railway deployment happens later, persistent storage becomes
  required (Railway's default filesystem does not survive a redeploy).
  The currently preferred approach is a **Railway Volume mounted at
  `data/`**, keeping the existing JSONL architecture unchanged — not a
  database migration, unless future evidence proves one is actually
  necessary.

This is a **read-only** personal Indian-market options intelligence terminal.
It does not place, modify, or cancel orders. It does not emit BUY/SELL
instructions, confidence percentages, or trade probabilities.

Current runtime architecture, providers, freshness, Qwen, and limitations:
[`docs/CURRENT_SYSTEM.md`](docs/CURRENT_SYSTEM.md).

Research maturity (`WATCH` / `EARLY_SETUP` / `CONFIRMATION_PENDING` /
`CONFIRMED_SETUP` / `EXTENDED` / `CONFLICT` / `DATA_INSUFFICIENT` /
`NO_TRADE`) is documented in `docs/research/RESEARCH_STATE.md`.
`TRADEABLE` is a compatibility label only.

NSE CM-segment holidays and Muhurat special sessions are loaded from
`data/reference/nse_trading_holidays.txt` (NSE circular NSE/CMTR/71775).
Evidence VWAP on the matrix is **session VWAP** when current-session M15
bars exist; rolling multi-day VWAP remains labeled rolling.

## Development

Python 3.12+
FastAPI
JSONL (local file persistence — see Persistence above)
pytest
ruff
mypy
