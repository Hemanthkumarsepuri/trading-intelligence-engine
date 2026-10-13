# TIRE Desk — Release Blockers and Residual Risk (2026-10-13)

Legend: `blocks staging` / `blocks production` / `accepted`. Status words: PASS / FAIL / BLOCKED / NOT RUN.

## Must be resolved or explicitly accepted before production
| ID | Sev | Item | Blocks |
|---|---|---|---|
| B1 | P0/P1 until proven | **No live validation has been performed** (Netlify Edge, Railway volume, provider connectivity from Railway's IPs, Telegram). All stages in `LIVE_VALIDATION_CHECKLIST.md` are NOT RUN. | production |
| B2 | P2 | **Backups share the live data's Railway volume.** They protect against bad edits and a bad import, **not** against loss or corruption of the volume. This is **not independent disaster recovery.** The only off-volume copy is the manual `/api/export` download. No off-volume backup is implemented and none has been tested. | production (accept or add an off-volume copy) |
| B3 | P2 | No restore drill has been run on Railway. | production |
| B4 | P3 | `importAll` writes five keys one at a time. A crash between writes leaves a mix of old and new data. A `pre-import-…` snapshot is taken first, so recovery is `POST /api/backup/restore` with that key. Failure window: milliseconds to seconds. | accepted for staging; review for production |
| B5 | P3 | Alert claim-before-save: an alert is marked fired (CAS) before its event is persisted. A crash in that gap loses that one alert's notification (it will not re-fire for `once` alerts). This deliberately favours no duplicate over no loss. Recovery: manual re-check of the alert's `lastTriggeredAt`. | accepted |
| B6 | P2 | No browser E2E or component tests exist. | staging recommended before relying on UI behaviour |
| B7 | P3 | Login lockout and the revocation list rely on the single Railway process for exactness. During a redeploy two instances can briefly overlap; CAS writes keep the data consistent but lockout counting could be slightly loose. | accepted |

## Resolved in this sprint (local evidence only)
A1 participant-OI zero-fill, A2 breadth zero-fill, A3 logout revocation, A4 volume zero-fill, A5 lockout race — see `AUDIT_SUMMARY.md`.

## Not blockers, but documented
- Existing `Candle` consumers needing a known volume: only `analyze()` and the chart (unknown volume is omitted from the histogram, not drawn as a zero bar). Quote display shows `-` for unknown and `0` for an observed zero.
- Sessions issued before this change would be rejected (no `jti`). Nothing is deployed, so none exist.
