# TIRE Desk — Audit Summary (2026-10-13)

Scope: `desk/` at commit `2b63248` plus the uncommitted hardening in this working tree. Engine code is out of scope and untouched.
Status legend: `PASS` / `FAIL` / `BLOCKED` / `NOT RUN`. **Everything below is local verification unless it says otherwise. No live Netlify, Railway, provider or Telegram check was performed.**

## Result
Local code quality gate: **PASS**. Production readiness: **NOT ESTABLISHED** (no live validation; see `RELEASE_BLOCKERS.md`). Recommendation: ready for a *controlled staging* deployment, not for production use.

## Defects found and fixed in this working tree (uncommitted)
| ID | Sev | Defect | Fix | Status |
|---|---|---|---|---|
| A1 | P1 | Participant-OI parser turned blank/garbage cells into `0` | `parseParticipantOi` throws `MALFORMED` | PASS (tests) |
| A2 | P1 | Market-breadth parser turned bad counts into `0` advances / `0` declines | new `parseBreadth` throws `MALFORMED` | PASS (tests) |
| A3 | P2 | Logout only cleared the cookie; a stateless token stayed valid up to 14 days | per-session id (`jti`), persistent revocation records, fail-closed checks | PASS (tests) |
| A4 | P2 | Missing candle volume became `0`, deflating the 20-day average and inflating `VOLUME_SPIKE` | `Candle.v: number \| null`; average uses known days only; needs ≥10 known days and a known current volume | PASS (tests) |
| A6 | P3 | Upstream non-JSON error echoed the first 60 characters of the response body to the browser in the 502 message | message now carries the host only; the excerpt goes to the server log | PASS (test) |
| A5 | P3 | Login lockout read → hash → record, so parallel guesses could exceed 5 | atomic reserve-before-verify (`reserveAttempt`) | PASS (tests) |

Propagation of A1/A2: `fetchBreadth` and `fetchParticipantOi` are called only through `cached(...)` in `services/market.ts` and in `services/selftest.ts`. A `MALFORMED` `ProviderError` therefore surfaces as a provider failure (HTTP 502 `UPSTREAM`, or the stale cached copy flagged `stale`), never as a market fact.

## Local verification (final code)
| Check | Result |
|---|---|
| `npm run check` (typecheck, eslint `--max-warnings=0`, vitest, vite build, `check-dist`, server build) | PASS, exit 0 |
| Tests | PASS, 15 files, 244/244 |
| `check-dist` | PASS, "9 files clean" |
| `npm audit` | PASS, exit 0, 0 vulnerabilities |
| `git diff --check` | PASS, exit 0 |
| Mutation check of the new tests (revocation check removed → 6 failures; per-key lock removed → 1 failure) | PASS |

## Not verified
Browser E2E (no Playwright configured): NOT RUN. Netlify Edge runtime, Railway volume persistence, provider connectivity from Railway, Telegram delivery, rollback: NOT RUN — external access or authorization required.

Details: `TEST_MATRIX.md`, `SECURITY_REVIEW.md`, `DEPLOYMENT_RUNBOOK.md`, `LIVE_VALIDATION_CHECKLIST.md`, `RELEASE_BLOCKERS.md`.
