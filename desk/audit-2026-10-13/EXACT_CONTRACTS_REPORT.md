# Exact contracts and custom watchlists: report

Branch `feature/exact-contracts`, from `f963ece`; feature commit `d3671ff`; fast-forwarded to `origin/staging/desk-rc-2026-10-13` only.

Tests: typecheck PASS; lint PASS; Vitest 478/478 (24 files; 53 new in `wb-exact-shared`, `wb-exact-api`); `build:web`/`check-dist`/`build:server` PASS;
Playwright 60/60 (31 original + 22 workbench + 7 new in `07-contracts`). Existing tests edited: the route-allowlist test (new user-data writes),
and `03-workflows` now selects the watchlist "Add" button with `exact: true` because "Add contract" also contains the word.

Staging: deployment `4fbf8da2-3eca-4c03-a705-944ac82353e0` SUCCESS (`desk@d3671ffeae5c manifest:69d1e3e17c40`), health 200, unauthenticated live acceptance 24/24,
all new routes 401 without a session.

NOT verified: authenticated use of these screens on staging (no password available), live NSE contracts such as ANGELONE (the ANGELONE cases run against a
faked NSE/Yahoo, not against the exchange), Upstox identifiers/Greeks/option candles (no token).
