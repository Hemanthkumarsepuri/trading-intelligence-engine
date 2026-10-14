// Independent (off-volume) encrypted backups.
//
// The nightly snapshot in backup.ts lives on the same Railway volume as the data, so
// losing the volume loses both. This module additionally writes an AES-256-GCM
// encrypted copy of the export to any S3-compatible bucket (Backblaze B2, Cloudflare
// R2, MinIO, AWS S3 ...), reads it back, decrypts it and re-validates it before the
// run is recorded as successful.
//
// Security properties:
//  - The bucket never sees plaintext; the encryption key (BACKUP_ENCRYPTION_KEY) is a
//    separate secret, not derived from SESSION_SECRET, so rotating sessions does not
//    strand old backups, and a leaked bucket credential does not expose data.
//  - The app only needs put/get/list: it never deletes remote objects. Retention is a
//    bucket lifecycle rule, so a compromised app cannot erase its own history.
//  - Failures never touch application data; they are recorded (sanitised: HTTP status
//    and S3 error code only, never URLs, keys or credentials) and retried by the scheduler.
//  - No SDK: a small SigV4 signer (verified against AWS's published test vector).

import { promises as fs } from 'node:fs';
import { createCipheriv, createDecipheriv, createHash, createHmac, randomBytes } from 'node:crypto';
import type { Ctx, OffsiteConfig } from '../config';
import { FileKV, read, update } from '../kv';
import { exportAll, importAll, validateBackup } from './backup';

// ---- AES-256-GCM envelope ------------------------------------------------------------------------------

const MAGIC = Buffer.from('TDBK1');
const KID_LEN = 4;
const IV_LEN = 12;
const TAG_LEN = 16;

export const keyId = (key: Buffer): string => createHash('sha256').update(key).digest('hex').slice(0, KID_LEN * 2);

/** Parse a 32-byte key given as 64 hex chars or base64. Returns null when it is not exactly 32 bytes. */
export function parseEncryptionKey(raw: string | null | undefined): Buffer | null {
  if (!raw) return null;
  const t = raw.trim();
  const buf = /^[0-9a-fA-F]{64}$/.test(t) ? Buffer.from(t, 'hex') : Buffer.from(t, 'base64');
  return buf.length === 32 ? buf : null;
}

export function encryptBackup(plain: Buffer, key: Buffer): Buffer {
  const iv = randomBytes(IV_LEN);
  const header = Buffer.concat([MAGIC, Buffer.from(keyId(key), 'hex')]);
  const c = createCipheriv('aes-256-gcm', key, iv);
  c.setAAD(header);
  const ct = Buffer.concat([c.update(plain), c.final()]);
  return Buffer.concat([header, iv, c.getAuthTag(), ct]);
}

export class BackupCryptoError extends Error {}

/** `keys` may hold several keys (rotation): the one whose id matches the envelope is used. */
export function decryptBackup(blob: Buffer, keys: Buffer[]): Buffer {
  const headerLen = MAGIC.length + KID_LEN;
  if (blob.length < headerLen + IV_LEN + TAG_LEN || !blob.subarray(0, MAGIC.length).equals(MAGIC)) throw new BackupCryptoError('Not a TIRE Desk backup envelope.');
  const header = blob.subarray(0, headerLen);
  const kid = header.subarray(MAGIC.length).toString('hex');
  const key = keys.find((k) => keyId(k) === kid);
  if (!key) throw new BackupCryptoError(`No encryption key with id ${kid} was supplied.`);
  const iv = blob.subarray(headerLen, headerLen + IV_LEN);
  const tag = blob.subarray(headerLen + IV_LEN, headerLen + IV_LEN + TAG_LEN);
  const ct = blob.subarray(headerLen + IV_LEN + TAG_LEN);
  try {
    const d = createDecipheriv('aes-256-gcm', key, iv);
    d.setAAD(header);
    d.setAuthTag(tag);
    return Buffer.concat([d.update(ct), d.final()]);
  } catch {
    throw new BackupCryptoError('Backup failed authentication (corrupt, truncated or tampered).');
  }
}

// ---- AWS Signature Version 4 -----------------------------------------------------------------------------

const sha256Hex = (d: string | Buffer): string => createHash('sha256').update(d).digest('hex');
const hmac = (k: string | Buffer, d: string): Buffer => createHmac('sha256', k).update(d).digest();
const enc = (s: string): string => encodeURIComponent(s).replace(/[!'()*]/g, (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`);

export interface SignInput {
  method: string;
  /** already-encoded path, e.g. /bucket/a/b.tdbk */
  path: string;
  query?: Record<string, string>;
  /** lower-case names; must include host and x-amz-date. Exactly these headers are signed. */
  headers: Record<string, string>;
  payloadHash: string;
  region: string;
  service: string;
  accessKeyId: string;
  secretAccessKey: string;
  amzDate: string; // YYYYMMDD'T'HHMMSS'Z'
}

export function signV4(i: SignInput): { authorization: string; signature: string; signedHeaders: string } {
  const names = Object.keys(i.headers).sort();
  const canonicalHeaders = names.map((n) => `${n}:${i.headers[n]!.trim().replace(/\s+/g, ' ')}\n`).join('');
  const signedHeaders = names.join(';');
  const canonicalQuery = Object.entries(i.query ?? {})
    .map(([k, v]) => [enc(k), enc(v)] as const)
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([k, v]) => `${k}=${v}`)
    .join('&');
  const canonicalRequest = [i.method, i.path, canonicalQuery, canonicalHeaders, signedHeaders, i.payloadHash].join('\n');
  const day = i.amzDate.slice(0, 8);
  const scope = `${day}/${i.region}/${i.service}/aws4_request`;
  const stringToSign = ['AWS4-HMAC-SHA256', i.amzDate, scope, sha256Hex(canonicalRequest)].join('\n');
  const kSigning = hmac(hmac(hmac(hmac(`AWS4${i.secretAccessKey}`, day), i.region), i.service), 'aws4_request');
  const signature = createHmac('sha256', kSigning).update(stringToSign).digest('hex');
  return { signature, signedHeaders, authorization: `AWS4-HMAC-SHA256 Credential=${i.accessKeyId}/${scope}, SignedHeaders=${signedHeaders}, Signature=${signature}` };
}

// ---- Minimal S3 client (path-style) ------------------------------------------------------------------------

export class OffsiteError extends Error {
  constructor(message: string, public status: number | null = null) {
    super(message);
    this.name = 'OffsiteError';
  }
}

const amzNow = (d: Date): string => d.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}/, '');

export class S3Client {
  constructor(private cfg: OffsiteConfig, private fetchImpl: typeof fetch = fetch, private now: () => Date = () => new Date()) {}

  private async call(method: string, key: string, opts: { query?: Record<string, string>; body?: Buffer } = {}): Promise<Response> {
    const base = new URL(this.cfg.endpoint);
    const objectPath = key ? `/${enc(this.cfg.bucket)}/${key.split('/').map(enc).join('/')}` : `/${enc(this.cfg.bucket)}`;
    const body = opts.body ?? Buffer.alloc(0);
    const payloadHash = sha256Hex(body);
    const amzDate = amzNow(this.now());
    const headers: Record<string, string> = { host: base.host, 'x-amz-content-sha256': payloadHash, 'x-amz-date': amzDate };
    const { authorization } = signV4({ method, path: objectPath, query: opts.query, headers, payloadHash, region: this.cfg.region, service: 's3', accessKeyId: this.cfg.accessKeyId, secretAccessKey: this.cfg.secretAccessKey, amzDate });
    const qs = Object.entries(opts.query ?? {})
      .map(([k, v]) => `${enc(k)}=${enc(v)}`)
      .sort()
      .join('&');
    const url = `${base.protocol}//${base.host}${objectPath}${qs ? `?${qs}` : ''}`;
    const { host: _host, ...sendHeaders } = headers;
    void _host;
    try {
      return await this.fetchImpl(url, { method, headers: { ...sendHeaders, authorization }, body: method === 'PUT' ? new Uint8Array(body) : undefined, signal: AbortSignal.timeout(60_000) });
    } catch {
      throw new OffsiteError('Backup destination unreachable.');
    }
  }

  private async fail(res: Response, what: string): Promise<never> {
    const text = await res.text().catch(() => '');
    const code = /<Code>([\w.-]{1,64})<\/Code>/.exec(text)?.[1];
    throw new OffsiteError(`${what} failed: HTTP ${res.status}${code ? ` ${code}` : ''}`, res.status);
  }

  async put(key: string, body: Buffer): Promise<void> {
    const res = await this.call('PUT', key, { body });
    if (!res.ok) await this.fail(res, 'Upload');
  }

  async get(key: string): Promise<Buffer> {
    const res = await this.call('GET', key);
    if (!res.ok) await this.fail(res, 'Download');
    return Buffer.from(await res.arrayBuffer());
  }

  async list(prefix: string): Promise<string[]> {
    const keys: string[] = [];
    let token: string | undefined;
    for (let page = 0; page < 50; page++) {
      const res = await this.call('GET', '', { query: { 'list-type': '2', prefix, ...(token ? { 'continuation-token': token } : {}) } });
      if (!res.ok) await this.fail(res, 'Listing');
      const xml = await res.text();
      for (const m of xml.matchAll(/<Key>([^<]+)<\/Key>/g)) keys.push(m[1]!.replace(/&amp;/g, '&'));
      token = /<IsTruncated>true<\/IsTruncated>/.test(xml) ? /<NextContinuationToken>([^<]+)<\/NextContinuationToken>/.exec(xml)?.[1] : undefined;
      if (!token) break;
    }
    return keys.sort();
  }
}

// ---- Backup run + status ------------------------------------------------------------------------------------

export interface OffsiteStatus {
  lastAttemptAt: string | null;
  lastOkAt: string | null;
  lastKey: string | null;
  lastBytes: number | null;
  lastSha256: string | null;
  lastCounts: Record<string, number> | null;
  lastError: string | null;
  consecutiveFailures: number;
}

const EMPTY_STATUS: OffsiteStatus = { lastAttemptAt: null, lastOkAt: null, lastKey: null, lastBytes: null, lastSha256: null, lastCounts: null, lastError: null, consecutiveFailures: 0 };
export const OFFSITE_STATUS_KEY = 'backup-offsite/status';

export async function offsiteStatus(ctx: Ctx): Promise<OffsiteStatus & { configured: boolean; destination: string | null }> {
  const s = await read<OffsiteStatus>(ctx.kv, OFFSITE_STATUS_KEY, EMPTY_STATUS);
  const o = ctx.cfg.offsite;
  return { ...EMPTY_STATUS, ...s, configured: Boolean(o), destination: o ? `${new URL(o.endpoint).host}/${o.bucket}` : null };
}

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

export interface OffsiteRunOptions {
  retryDelaysMs?: number[];
  sleepImpl?: (ms: number) => Promise<void>;
}

export async function runOffsiteBackup(ctx: Ctx, opts: OffsiteRunOptions = {}): Promise<{ skipped: true; reason: string } | { skipped: false; key: string; bytes: number; sha256: string; counts: Record<string, number> }> {
  const cfg = ctx.cfg.offsite;
  if (!cfg) return { skipped: true, reason: 'Off-site backup is not configured.' };
  const client = new S3Client(cfg, ctx.fetchImpl ?? fetch, ctx.now);
  const attemptAt = ctx.now().toISOString();
  const delays = opts.retryDelaysMs ?? [2_000, 8_000];
  const wait = opts.sleepImpl ?? sleep;

  try {
    const plain = Buffer.from(JSON.stringify(await exportAll(ctx)));
    const sha = sha256Hex(plain);
    const stamp = attemptAt.replace(/[:.]/g, '-');
    const key = `${cfg.prefix}daily/${stamp}-${sha.slice(0, 8)}.tdbk`;
    const blob = encryptBackup(plain, cfg.encryptionKey);

    let lastErr: unknown;
    for (let attempt = 0; attempt <= delays.length; attempt++) {
      try {
        await client.put(key, blob);
        // Independent read-back: what is stored must decrypt to exactly what we exported and still validate.
        const back = decryptBackup(await client.get(key), [cfg.encryptionKey]);
        if (sha256Hex(back) !== sha) throw new OffsiteError('Read-back checksum mismatch.');
        const v = validateBackup({ ...JSON.parse(back.toString('utf8')), confirm: true }, attemptAt);
        const counts = { trades: v.trades.length, holdings: v.holdings.length, watchlist: v.watchlist.length, alerts: v.alerts.length };
        await update<OffsiteStatus>(ctx.kv, OFFSITE_STATUS_KEY, EMPTY_STATUS, (cur) => ({ ...cur, lastAttemptAt: attemptAt, lastOkAt: attemptAt, lastKey: key, lastBytes: blob.length, lastSha256: sha.slice(0, 16), lastCounts: counts, lastError: null, consecutiveFailures: 0 }));
        return { skipped: false, key, bytes: blob.length, sha256: sha.slice(0, 16), counts };
      } catch (e) {
        lastErr = e;
        const permanent = e instanceof OffsiteError && e.status !== null && e.status >= 400 && e.status < 500 && e.status !== 408 && e.status !== 429;
        if (permanent || attempt === delays.length) break;
        await wait(delays[attempt]!);
      }
    }
    throw lastErr;
  } catch (e) {
    const message = e instanceof OffsiteError || e instanceof BackupCryptoError ? e.message : 'Unexpected error while creating the off-site backup.';
    await update<OffsiteStatus>(ctx.kv, OFFSITE_STATUS_KEY, EMPTY_STATUS, (cur) => ({ ...cur, lastAttemptAt: attemptAt, lastError: message, consecutiveFailures: cur.consecutiveFailures + 1 })).catch(() => undefined);
    throw new OffsiteError(message, e instanceof OffsiteError ? e.status : null);
  }
}

/** Independent check used by operators and tests: fetch the newest remote object, decrypt and validate it. Writes nothing. */
export async function verifyLatestOffsite(ctx: Ctx): Promise<{ key: string; ok: boolean; counts: Record<string, number>; problems: string[] }> {
  const cfg = ctx.cfg.offsite;
  if (!cfg) return { key: '', ok: false, counts: {}, problems: ['Off-site backup is not configured.'] };
  const client = new S3Client(cfg, ctx.fetchImpl ?? fetch, ctx.now);
  const keys = await client.list(`${cfg.prefix}daily/`);
  const key = keys.at(-1);
  if (!key) return { key: '', ok: false, counts: {}, problems: ['No off-site backups found.'] };
  try {
    const plain = decryptBackup(await client.get(key), [cfg.encryptionKey]);
    const v = validateBackup({ ...JSON.parse(plain.toString('utf8')), confirm: true }, ctx.now().toISOString());
    return { key, ok: true, counts: { trades: v.trades.length, holdings: v.holdings.length, watchlist: v.watchlist.length, alerts: v.alerts.length }, problems: [] };
  } catch (e) {
    return { key, ok: false, counts: {}, problems: [e instanceof Error ? e.message : 'invalid'] };
  }
}

/**
 * Disaster recovery: rebuild a Desk data directory from the off-site copy alone.
 * Refuses any directory that already holds files (recovery goes into a NEW, isolated
 * location, never over live data), validates the whole export before writing, and
 * lets importAll take its usual safety snapshot.
 */
export async function restoreOffsiteInto(ctx: Ctx, which: 'latest' | string, targetDir: string): Promise<{ sourceKey: string; exportedAt: string | null; counts: Record<string, number>; safetySnapshot: string }> {
  const cfg = ctx.cfg.offsite;
  if (!cfg) throw new OffsiteError('Off-site backup is not configured (BACKUP_S3_* and BACKUP_ENCRYPTION_KEY).');
  const existing = await fs.readdir(targetDir).catch((e: NodeJS.ErrnoException) => (e.code === 'ENOENT' ? [] : Promise.reject(e)));
  if (existing.length > 0) throw new OffsiteError(`Refusing to restore into ${targetDir}: it is not empty. Restore into a new, empty directory.`);
  const client = new S3Client(cfg, ctx.fetchImpl ?? fetch, ctx.now);
  const sourceKey = which === 'latest' ? (await client.list(`${cfg.prefix}daily/`)).at(-1) : which;
  if (!sourceKey) throw new OffsiteError('No off-site backups found.');
  const plain = decryptBackup(await client.get(sourceKey), [cfg.encryptionKey]);
  const raw = { ...(JSON.parse(plain.toString('utf8')) as Record<string, unknown>), confirm: true };
  validateBackup(raw, ctx.now().toISOString()); // fail before creating anything
  await fs.mkdir(targetDir, { recursive: true });
  const res = await importAll({ ...ctx, kv: new FileKV(targetDir) }, raw);
  const stamp = (raw as Record<string, unknown>).exportedAt;
  const exportedAt = typeof stamp === 'string' ? stamp : null; // lets the operator spot a rolled-back (old) object
  return { sourceKey, exportedAt, counts: { trades: res.trades, holdings: res.holdings, watchlist: res.watchlist, alerts: res.alerts }, safetySnapshot: res.backupKey };
}
