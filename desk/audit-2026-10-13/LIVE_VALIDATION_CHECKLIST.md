# TIRE Desk — Live Validation Checklist

Every stage below is **NOT RUN — external access or authorization required.** Mark PASS only after observing the result, and attach the evidence. Use staging, not production. Never record secret values.

| # | Stage | Exact test | Expected | Evidence | Fail if | Status |
|---|---|---|---|---|---|---|
| 1 | Static configuration | Compare Netlify and Railway variables with the runbook table | all required present, `DESK_PROXY_SECRET` equal on both (compare by length/fingerprint only) | variable names list | any required variable missing | NOT RUN |
| 2 | Local integration | `npm run check` on the committed tree | exit 0, 244+ tests | log | any failure | PASS locally on the working tree (not yet on a commit) |
| 3 | Staging deploy | Deploy Railway then Netlify; open the site | health 200; SPA loads; deep link `/market` loads | screenshot, `/api/health` body | 5xx, blank page | NOT RUN |
| 4 | Auth and proxy | Call Railway URL directly with no proxy key → 403 `DIRECT_ACCESS`; via site, wrong password ×5 → 401 then 429; correct login; `Set-Cookie` has `__Host-`, `HttpOnly`, `Secure`, `SameSite=Strict`; logout; replay old cookie → 401; response bodies never contain the proxy secret | as stated | curl output with cookie values redacted | any mismatch | NOT RUN |
| 5 | Provider connectivity | Settings → "Run self-test" from the Railway service | each provider reports ok or an honest failure; no zeros presented as data | self-test JSON | fabricated or zero data shown | NOT RUN |
| 6 | Persistence and restart | Add a trade, watchlist item and alert; restart the Railway service; redeploy; verify all present | data identical after both | before/after export diff | any loss | NOT RUN |
| 7 | Backup and recovery | Trigger `/api/backup/run`; verify with `/api/backup/verify`; restore into a throw-away staging dataset; download `/api/export` and store it off-volume | verify ok; restore round-trips | JSON counts | verify not ok | NOT RUN |
| 8 | Controlled notification | With the owner's explicit approval and a confirmed test chat only: `POST /api/telegram/test` once | one message in the test chat; log shows success | Telegram screenshot | duplicate or none | NOT RUN — needs authorization |
| 9 | Production smoke | After approval: login, one read of each page, logout | no console errors, correct freshness labels | screenshots | errors | NOT RUN |
| 10 | Rollback readiness | Redeploy the previous Railway and Netlify builds in staging | service restored, data intact | deploy ids | data loss | NOT RUN |

Also verify on Railway (NOT RUN): the service binds the platform `PORT`; exactly one scheduler heartbeat appears in `/api/status`; log lines contain no tokens or cookies.
