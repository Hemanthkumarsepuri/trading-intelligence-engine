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

## Choosing a provider and creating the staging bucket (owner steps)

Prices/limits as read from the providers' pages on 2026-10-14; re-check at sign-up.

| | Backblaze B2 (recommended) | Cloudflare R2 |
|---|---|---|
| Free tier | first 10 GB storage always free; Class A/B/C API calls free; egress free up to 3x stored data | 10 GB-month, 1 M Class A, 10 M Class B ops per month; egress free |
| No-delete key | **Yes**: an application key can be limited to one bucket with only `listFiles, readFiles, writeFiles` (no `deleteFiles`) | No: Object Read & Write includes delete |
| Region value | the real region from the bucket endpoint, e.g. `us-west-004` | `auto` |
| Card at sign-up | not stated on the pricing page — check | not stated — check |

The implementation needs exactly three S3 operations: `PutObject`, `GetObject`, `ListObjectsV2` (path-style). It never deletes.

Backblaze B2 steps (you perform these; nothing is created for you):
1. Create a **private** bucket, e.g. `tire-desk-staging-backups`. Turn on versioning (default) and add a lifecycle rule to keep ~90 days.
2. Create an application key restricted to that bucket with `listFiles,readFiles,writeFiles` only. The web UI's "Read and Write" preset may include delete; use the B2 CLI if so: `b2 key create --bucket <bucketName> tire-desk-staging listFiles,readFiles,writeFiles`.
3. Note the S3 endpoint (`https://s3.<region>.backblazeb2.com`), the key id and the application key (shown once).
4. Generate the encryption key locally and store it in your password manager.
5. Set the variables on the **staging** Railway service without echoing them (run each in your terminal, paste the value when prompted):
   `railway variable set BACKUP_S3_ENDPOINT --stdin -p 9f7d07c7-341e-4932-8fa6-e95e57abb657 -s 1ae958e9-ac77-4f0a-9fc7-b78e5694c79d -e e9f5adf9-5095-4bcc-8fab-3625db84a7da`
   (repeat for `BACKUP_S3_BUCKET`, `BACKUP_S3_REGION`, `BACKUP_S3_ACCESS_KEY_ID`, `BACKUP_S3_SECRET_ACCESS_KEY`, `BACKUP_ENCRYPTION_KEY`, and `BACKUP_S3_PREFIX=tire-desk-staging/`). Setting variables redeploys the service.

## If the bucket credentials are compromised

* With a no-delete key an attacker can read ciphertext (useless without `BACKUP_ENCRYPTION_KEY`) and add objects.
  They could add a forged-looking or *older* valid object to try to make recovery use stale data: authentication
  prevents forgery without the encryption key, but not replay of an old genuine object. `restore` prints the
  backup's `exportedAt` and object name; compare with the app's `/api/status` `offsite.lastKey` before trusting it.
* Rotate the bucket key immediately (create new, set variables, run an off-site backup, revoke old).
  Bucket versioning/lifecycle protects history from overwrites; a key that can delete (R2) cannot.
* If `BACKUP_ENCRYPTION_KEY` is compromised, generate a new one, run a fresh backup, and treat old objects as readable by the attacker.

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
