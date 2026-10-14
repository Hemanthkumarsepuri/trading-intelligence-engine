# Off-site backups and disaster recovery

The nightly snapshot (`backup/daily-*`) lives on the same Railway volume as the data. It protects
against bad edits, **not** against losing the volume. The off-site backup is the independent copy.

## What it does

Every night at or after 02:10 IST (job `offsite`, retried every 30 minutes until it succeeds) the server:

1. exports all collections (trades, holdings, watchlist, alerts, settings),
2. encrypts the JSON with **AES-256-GCM** using `BACKUP_ENCRYPTION_KEY` (the bucket only ever holds ciphertext),
3. uploads it to an S3-compatible bucket as `<prefix>daily/<UTC timestamp>-<8 hex of sha256>.tdbk` (unique, never overwritten),
4. reads the object back, decrypts it, compares the checksum and re-validates it with the same validator a restore uses,
5. records a sanitised status (`/api/status` → `offsite`: last OK time, object name, size, counts, last error, consecutive failures).

Failures (destination down, bad credentials, corrupt read-back) are retried (2 s, 8 s) and recorded; they
never change application data and never fail the local snapshot. Telegram is not involved.

The app needs only **put / get / list** on the bucket. It never deletes remote objects, so a compromised
service cannot erase history. Retention is the bucket's job (lifecycle rule, e.g. keep 90 days), and
object versioning/lock is recommended.

## Configuration (Railway variables, all required together)

| Variable | Meaning |
|---|---|
| `BACKUP_S3_ENDPOINT` | `https://` S3 endpoint (Backblaze B2, Cloudflare R2, MinIO, AWS S3 …) |
| `BACKUP_S3_BUCKET` | a **private** bucket dedicated to this app |
| `BACKUP_S3_ACCESS_KEY_ID` / `BACKUP_S3_SECRET_ACCESS_KEY` | key restricted to that bucket: read, write, list; **no delete** |
| `BACKUP_ENCRYPTION_KEY` | 32 random bytes (64 hex chars or base64). Generate: `node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"` |
| `BACKUP_S3_REGION` | optional, default `auto` (R2); use the real region for AWS/B2 |
| `BACKUP_S3_PREFIX` | optional, default `tire-desk/` (use `tire-desk-staging/` for staging) |

A partial set is reported as a configuration problem on the status page instead of being ignored.
Set them with `railway variable set NAME --stdin` so values never appear in a shell history.

**Keep a copy of `BACKUP_ENCRYPTION_KEY` outside Railway** (password manager). Without it the backups
cannot be read; with it and the bucket credentials, the volume is replaceable.

## Rotation

* **Bucket credentials:** create a new restricted key, set the two variables, deploy/restart, run
  `POST /api/backup/offsite/run`, confirm `offsite.lastOkAt` advanced, then revoke the old key.
* **Encryption key:** set the new `BACKUP_ENCRYPTION_KEY`; new backups use it immediately. Each object
  records the id of its key, so old objects stay readable as long as you keep the old key
  (`offsite-restore.ts` accepts the key from the environment; decrypt old objects by temporarily using the old key).
  Keep every retired key until the last object encrypted with it has aged out.

## Recovery procedure (volume or service lost)

Always restore into a **new, isolated** location first; never over live data.

1. Create the replacement Railway service/volume (or a scratch directory). Do not reuse the old volume.
2. Export the five `BACKUP_*` variables into your shell (from the password manager).
3. Inspect: `npx tsx scripts/offsite-restore.ts list` and `… verify` (fetches the newest object, decrypts and validates; writes nothing).
4. Rebuild the data directory: `npx tsx scripts/offsite-restore.ts restore latest <newEmptyDir>`.
   It refuses a non-empty directory, authenticates the ciphertext (a corrupt/tampered object is rejected and
   nothing is created), validates every row, and takes a `backup/pre-import-*` safety snapshot via the normal import path.
   To restore a specific day use the object name from `list` instead of `latest`.
5. Copy that directory to the new volume (`DESK_DATA_DIR`, e.g. `/data/desk`) and start the service.
6. **Rotate `SESSION_SECRET`** (new random 48+ hex chars) before users return. Exports do not carry
   session-revocation or login-lockout state; rotation invalidates every previously issued session, including
   ones that had been revoked. Then confirm an old session cookie is rejected (401) and sign in again.
7. Verify: trades, holdings, watchlist, alerts and settings match expectations; `/api/status` shows the scheduler
   with one owner; run `POST /api/backup/offsite/run` and confirm `offsite.lastOkAt`.

If only the **bucket** is lost: set up a new bucket and variables; the next run (or `POST /api/backup/offsite/run`)
writes a fresh copy. Until then the local snapshot is the only copy.

## Manual fallback

`GET /api/export` (signed in) downloads the plaintext export. Treat it as sensitive (it is not encrypted);
store it off Railway. This is a stop-gap, not an automated independent backup.

## What was verified

See the production-readiness section of `audit-2026-10-13/STAGING_DEPLOYMENT_REPORT.md`. Automated tests
(`tests/offsite.test.ts`) cover the crypto envelope, the SigV4 signer against AWS's published vector, the
upload/read-back cycle against a local S3 stand-in, retries, sanitised failures, corruption detection and
restore into an empty directory.
