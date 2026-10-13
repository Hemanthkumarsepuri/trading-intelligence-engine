# TIRE Desk — Security Review (2026-10-13)

Local code review and tests only. No live penetration or deployment testing was performed.

## Session revocation (new)
- Token = `base64url(payload).HMAC-SHA256`. The payload now carries `jti` (128-bit random). Tokens without a valid `jti` are rejected because they could not be revoked. Nothing is deployed yet, so no live session is invalidated by this.
- Logout verifies the cookie's signature and expiry, then writes `auth/revoked/<sha256(jti)[:32]>` = `{exp}` to the persistent KV (`/data/desk` on Railway). One record per session, so concurrent logouts never contend. The token itself is never stored.
- Every protected request and `/api/auth/me` checks the record after the signature and expiry checks. If the KV cannot be read, the request gets **503 `AUTH_UNAVAILABLE`** (fail closed), never access.
- If the revocation write fails, logout returns **503 `LOGOUT_NOT_REVOKED`** (the browser cookie is still cleared and the UI shows the error). It is never reported as success. Retrying is idempotent.
- Pruning: after a successful logout, records whose `exp` is more than 60 s in the past are deleted (batch ≤ 200). An active or just-expired record is never removed. Growth is bounded by logouts in the last 14 days.
- Cookie flags unchanged: `__Host-` prefix, `HttpOnly`, `Secure`, `SameSite=Strict`, `Path=/` in production.
- Residual: one KV read per request. There is no "log out everywhere"; rotating `SESSION_SECRET` does that and was deliberately not used as a shortcut.

## Login lockout (changed)
`reserveAttempt` admits and counts an attempt **before** the PBKDF2 check, under a per-client mutex plus a CAS update. At most 5 attempts per client key are verified per 15-minute window. The 5th locks the client for 15 minutes. A correct login clears the count, and 15 quiet minutes reset it. Hashing is outside the lock. Clients are keyed by the proxy-reported IP, so one client cannot lock out another. Residual: anyone sharing the owner's IP could lock the owner out for 15 minutes (unchanged; acceptable for a single-owner tool).

## Provider-data integrity (changed)
Participant OI, market breadth and candle volume no longer coerce missing or garbage input to `0`. Malformed input is a provider failure, never a market fact. A genuine zero from the source is preserved.

## Upstream error bodies (changed)
`fetchJson` no longer puts any part of a provider's response body into the `ProviderError` message that reaches the browser (host only). The 60-character excerpt is written to the server log. Covered by a test.

## Reviewed, unchanged, no defect found
Direct-access refusal without the proxy key (constant-time compare). Client-supplied IP and proxy headers are stripped. The edge proxy uses a header allow-list, an https-only non-private upstream, no redirect following, body caps and timeouts. CSRF defence is an exact origin check (mandatory in production) plus `Sec-Fetch-Site`. Password hashing is PBKDF2-SHA256 at 600k iterations with constant-time comparison. A production start refuses unsafe configuration. There is no broker-order code, no source maps, and no direct Railway or Telegram call from the frontend.

## Open items
- P3: the Telegram-test and digest endpoints are authenticated but can send real messages by design.
- P2 (operational): backup and import, see `RELEASE_BLOCKERS.md`.
