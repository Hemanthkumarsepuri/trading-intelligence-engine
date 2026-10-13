# Why this is a rebuild, and what it fixes

The earlier TIRE engine (Python/FastAPI, JSONL files, a broker token in `.env`) cannot run on Netlify (no persistent disk, no long-lived server) and the forensic review found defects that matter for a tool you rely on. TIRE Desk keeps the research-only contract and the market knowledge, and fixes the classes of problem:

| Problem class | In the old engine | In TIRE Desk |
|---|---|---|
| No authentication on a tool holding journal, holdings and alert tokens | open API | single-owner login: PBKDF2-SHA256 (600k iterations), signed HttpOnly SameSite=Strict cookie (`__Host-` prefixed on HTTPS), 5-attempt lockout per IP, Origin/Fetch-Metadata checks on every write, JSON-only bodies with size limits |
| Lost writes under concurrency (H-2 class) | read-modify-write on files | every mutation is a compare-and-set on versioned storage with retry; test fires 25 simultaneous writes and asserts none are lost |
| Stale data used silently (H-3 / M-2 class) | cached values voted like fresh ones | every response carries source, `stale` and notes; the UI shows them; alerts are blocked on stale quotes |
| Holiday calendar silently wrong outside covered years (M-7 class) | assumed weekdays are trading days | uncovered years are flagged ("Holiday list outdated") and live NSE holidays are merged in |
| Wrong day-change numbers from a multi-day chart range | n/a / risk | previous close is taken from the session before the latest bar, not the start of the range |
| Double or missing notifications | n/a | alerts are claimed with compare-and-set; the event is written to an outbox before sending; failed sends are retried up to 3 times within 30 minutes with a leased, compare-and-set claim per attempt (no concurrent duplicates); digests are claimed once per day and released on failure |
| Secrets in the repo or frontend | token in `.env` | secrets only in Railway service variables; Netlify holds only the API origin and proxy secret; the build fails if the bundle contains localhost, Railway/Telegram URLs, server variable names or token-shaped strings; strict CSP |
| Look-ahead in analytics | present | indicators use completed bars only and are unit-tested; no predictions or probabilities are produced |
| Data you cannot trust and cannot see | opaque | a provider self-test page and per-screen data notes |

Remaining honest limits: free Yahoo/NSE endpoints can block cloud IPs or change shape; use the Upstox token and watch the self-test. Charges/tax are estimates.

The Python engine's own defects are not touched by this app (it is no longer needed to run it). If you keep using it locally, those fixes would be a separate piece of work.

## Railway integration sprint (2026-10-13)

Defects found in the Desk itself while integrating with Railway, and fixed:

| Defect | Effect | Fix |
|---|---|---|
| File store mangled keys containing `/` | backup retention never deleted old snapshots; backup reads failed on file storage | reversible key encoding; tests assert exact keys round-trip |
| File store swallowed IO errors as "no data" | a permission problem on the volume would look like an empty journal | only "file not found" means empty; other errors surface |
| No fsync on writes | a crash could lose the last acknowledged write | temp file, fsync, atomic rename, directory fsync |
| Session cookie lost `Secure`/`__Host-` behind Railway's TLS proxy; CSRF compared against the Railway host | login or every write would fail cross-origin | requests are evaluated against `DESK_PUBLIC_ORIGIN`; exact-origin allow-list; Origin mandatory in production |
| Login lockout keyed on spoofable `x-forwarded-for` | an attacker could rotate fake IPs | IP comes only from the authenticated proxy header; client copies are stripped |
| Telegram failures were not retried (the earlier version of this file claimed they were) | a single Telegram outage lost the alert | outbox and bounded retries (above) |
| Telegram 429 `retry_after` ignored; no record of last success/failures; no send-free token check | silent alert loss, no visibility | dedicated client, `getMe` check, delivery log, owner-initiated test endpoint |
| Send pacing used wall-clock difference | a backwards clock jump could stall all sends for hours | pacing wait is clamped |
| VIX, RSI/volume and F&O-ban alerts were not gated on freshness | alerts could fire from stale prints or yesterday's ban list | all alert types are gated; ban list must be dated for today |
| Live freshness gate was 20 min | a 15-minute-delayed feed could still trigger alerts | 5 min while the market is live |
| Ban list `asOf` was always "today", even for an older file | stale ban data presented as current | trade date parsed from the file; unknown dates flagged |
| Missing option OI and volume counted as zero | PCR, max pain and totals silently wrong | missing values make those figures unavailable, with a note |
| PCR and OI-change wording/colours implied direction | single-factor "confirmation" against the engine's evidence rules | descriptive wording; neutral OI-change display |
| Scheduler on Netlify with in-function state | no single owner across Netlify and Railway; no catch-up after downtime | one scheduler in the Railway process, persisted state, CAS-claimed job starts, restart catch-up, heartbeat |
