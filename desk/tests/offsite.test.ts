// Independent encrypted backups: envelope crypto, SigV4 (against AWS's published test
// vector), the upload/read-back/verify cycle against a local fake S3 server, retry and
// failure behaviour, sanitised status, and recovery into an isolated empty store.

import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { createHash } from 'node:crypto';
import { promises as fs } from 'node:fs';
import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { loadConfig, type Ctx } from '../server/config';
import { FileKV, MemoryKV } from '../server/kv';
import { importAll } from '../server/services/backup';
import { BackupCryptoError, OFFSITE_STATUS_KEY, restoreOffsiteInto, S3Client, decryptBackup, encryptBackup, offsiteStatus, parseEncryptionKey, runOffsiteBackup, signV4, verifyLatestOffsite, type OffsiteStatus } from '../server/services/offsite';
import * as U from '../server/services/userdata';

const KEY_HEX = 'a1'.repeat(32);
const OTHER_KEY_HEX = 'b2'.repeat(32);
const ACCESS = 'AKIATESTACCESS';
const SECRET = 'testsecretkey/with+chars0123456789abcdef';

describe('SigV4 signer', () => {
  it('reproduces the AWS documentation test vector', () => {
    const out = signV4({
      method: 'GET',
      path: '/',
      query: { Action: 'ListUsers', Version: '2010-05-08' },
      headers: { 'content-type': 'application/x-www-form-urlencoded; charset=utf-8', host: 'iam.amazonaws.com', 'x-amz-date': '20150830T123600Z' },
      payloadHash: 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
      region: 'us-east-1',
      service: 'iam',
      accessKeyId: 'AKIDEXAMPLE',
      secretAccessKey: 'wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY',
      amzDate: '20150830T123600Z',
    });
    expect(out.signature).toBe('5d672d79c15b13162d9279b0855cfba6789a8edb4c82c400e06b5924a6f2b5d7');
  });
});

describe('encrypted envelope', () => {
  const key = parseEncryptionKey(KEY_HEX)!;
  const plain = Buffer.from(JSON.stringify({ hello: 'world', symbol: 'RELIANCE' }));

  it('round-trips and never contains the plaintext', () => {
    const blob = encryptBackup(plain, key);
    expect(blob.includes(Buffer.from('RELIANCE'))).toBe(false);
    expect(decryptBackup(blob, [key]).equals(plain)).toBe(true);
    expect(encryptBackup(plain, key).equals(blob)).toBe(false); // fresh IV every time
  });
  it('rejects a wrong key, tampering, truncation and foreign data', () => {
    const blob = encryptBackup(plain, key);
    expect(() => decryptBackup(blob, [parseEncryptionKey(OTHER_KEY_HEX)!])).toThrow(/No encryption key/);
    for (const i of [2, 7, 12, 30, blob.length - 1]) {
      const t = Buffer.from(blob);
      t[i] = t[i]! ^ 0x01;
      expect(() => decryptBackup(t, [key]), `byte ${i}`).toThrow(BackupCryptoError);
    }
    expect(() => decryptBackup(blob.subarray(0, blob.length - 3), [key])).toThrow(BackupCryptoError);
    expect(() => decryptBackup(Buffer.from('not a backup'), [key])).toThrow(/envelope/);
  });
  it('selects the right key during rotation', () => {
    const old = parseEncryptionKey(OTHER_KEY_HEX)!;
    expect(decryptBackup(encryptBackup(plain, old), [key, old]).equals(plain)).toBe(true);
  });
  it('accepts only 32-byte keys', () => {
    expect(parseEncryptionKey(Buffer.alloc(32, 1).toString('base64'))).not.toBeNull();
    expect(parseEncryptionKey('short')).toBeNull();
    expect(parseEncryptionKey(undefined)).toBeNull();
  });
});

describe('off-site configuration', () => {
  const full = { BACKUP_S3_ENDPOINT: 'https://s3.example.com', BACKUP_S3_BUCKET: 'b', BACKUP_S3_ACCESS_KEY_ID: ACCESS, BACKUP_S3_SECRET_ACCESS_KEY: SECRET, BACKUP_ENCRYPTION_KEY: KEY_HEX };
  it('is off when nothing is set and on when complete', () => {
    expect(loadConfig({}).offsite).toBeNull();
    expect(loadConfig({}).offsiteProblems).toEqual([]);
    const c = loadConfig(full);
    expect(c.offsite?.prefix).toBe('tire-desk/');
    expect(c.offsite?.region).toBe('auto');
  });
  it('reports a partial or invalid configuration instead of silently disabling', () => {
    expect(loadConfig({ ...full, BACKUP_ENCRYPTION_KEY: '' }).offsiteProblems.join(' ')).toMatch(/missing BACKUP_ENCRYPTION_KEY/);
    expect(loadConfig({ ...full, BACKUP_ENCRYPTION_KEY: 'tooshort' }).offsiteProblems.join(' ')).toMatch(/32 random bytes/);
    expect(loadConfig({ ...full, BACKUP_S3_ENDPOINT: 'http://insecure.example.com' }).offsiteProblems.join(' ')).toMatch(/https/);
    expect(loadConfig({ ...full, BACKUP_ENCRYPTION_KEY: 'tooshort' }).offsite).toBeNull();
  });
});

// ---- fake S3 ---------------------------------------------------------------------------------------------------

interface FakeS3 {
  server: Server;
  endpoint: string;
  objects: Map<string, Buffer>;
  failNextPuts: number[];
  failStatus: { code: number; body: string } | null;
  corruptOnStore: boolean;
  requests: string[];
  close: () => Promise<void>;
}

async function startFakeS3(bucket: string): Promise<FakeS3> {
  const objects = new Map<string, Buffer>();
  const state = { failNextPuts: [] as number[], failStatus: null as FakeS3['failStatus'], corruptOnStore: false, requests: [] as string[] };
  const server = createServer(async (req, res) => {
    const chunks: Buffer[] = [];
    for await (const c of req) chunks.push(c as Buffer);
    const body = Buffer.concat(chunks);
    const url = new URL(req.url ?? '/', 'http://x');
    state.requests.push(`${req.method} ${url.pathname}`);
    // authentication: the request must carry a SigV4 header for the right access key and a body hash that matches
    const auth = req.headers.authorization ?? '';
    const bodyHash = createHash('sha256').update(body).digest('hex');
    if (!auth.startsWith(`AWS4-HMAC-SHA256 Credential=${ACCESS}/`) || req.headers['x-amz-content-sha256'] !== bodyHash) {
      res.writeHead(403).end('<Error><Code>SignatureDoesNotMatch</Code></Error>');
      return;
    }
    if (state.failStatus) {
      res.writeHead(state.failStatus.code).end(state.failStatus.body);
      return;
    }
    const prefix = `/${bucket}/`;
    if (req.method === 'PUT' && url.pathname.startsWith(prefix)) {
      const code = state.failNextPuts.shift();
      if (code) {
        res.writeHead(code).end('<Error><Code>SlowDown</Code></Error>');
        return;
      }
      const stored = Buffer.from(body);
      if (state.corruptOnStore) stored[stored.length - 1] = stored[stored.length - 1]! ^ 1;
      objects.set(decodeURIComponent(url.pathname.slice(prefix.length)), stored);
      res.writeHead(200).end();
    } else if (req.method === 'GET' && url.pathname.startsWith(prefix)) {
      const o = objects.get(decodeURIComponent(url.pathname.slice(prefix.length)));
      if (!o) res.writeHead(404).end('<Error><Code>NoSuchKey</Code></Error>');
      else res.writeHead(200).end(o);
    } else if (req.method === 'GET' && url.pathname === `/${bucket}` && url.searchParams.get('list-type') === '2') {
      const p = url.searchParams.get('prefix') ?? '';
      const keys = [...objects.keys()].filter((k) => k.startsWith(p)).sort();
      res.writeHead(200).end(`<ListBucketResult><IsTruncated>false</IsTruncated>${keys.map((k) => `<Contents><Key>${k}</Key></Contents>`).join('')}</ListBucketResult>`);
    } else {
      res.writeHead(400).end('<Error><Code>BadRequest</Code></Error>');
    }
  });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
  const f: FakeS3 = {
    server,
    endpoint: `http://127.0.0.1:${(server.address() as AddressInfo).port}`,
    objects,
    get failNextPuts() { return state.failNextPuts; },
    set failNextPuts(v) { state.failNextPuts = v; },
    get failStatus() { return state.failStatus; },
    set failStatus(v) { state.failStatus = v; },
    get corruptOnStore() { return state.corruptOnStore; },
    set corruptOnStore(v) { state.corruptOnStore = v; },
    requests: state.requests,
    close: () => new Promise((r) => server.close(() => r())),
  };
  return f;
}

// The production config requires https; tests point the same client at the local http fake.
function ctxFor(s3: FakeS3, kv = new MemoryKV(), extra: Partial<Record<string, string>> = {}): Ctx {
  const cfg = loadConfig({ DESK_OFFLINE: '1', DESK_STORAGE: 'memory', BACKUP_S3_ENDPOINT: 'https://placeholder.example.com', BACKUP_S3_BUCKET: 'vault', BACKUP_S3_ACCESS_KEY_ID: ACCESS, BACKUP_S3_SECRET_ACCESS_KEY: SECRET, BACKUP_ENCRYPTION_KEY: KEY_HEX, ...extra });
  if (cfg.offsite) cfg.offsite.endpoint = s3.endpoint;
  return { cfg, kv, now: () => new Date('2026-10-14T21:00:00Z') };
}

const noSleep = { retryDelaysMs: [0, 0], sleepImpl: async () => undefined };

async function seed(ctx: Ctx): Promise<void> {
  await U.createTrade(ctx, { symbol: 'SYNTHTEST', segment: 'EQ_DELIVERY', side: 'LONG', qty: 3, entryPrice: 100, exitPrice: null, entryAt: '2026-10-01T04:00:00.000Z', exitAt: null, stopLoss: null, target: null, setup: 'synthetic', tags: [], emotion: null, planFollowed: null, notes: 'CONFIDENTIAL-NOTE' });
  await U.createHoldings(ctx, [{ symbol: 'TCS', qty: 5, avgPrice: 3000, buyDate: '', notes: '' }]);
  await U.upsertWatch(ctx, { symbol: 'INFY', note: 'watch', support: null, resistance: null });
  await U.createAlert(ctx, { type: 'PRICE_ABOVE', symbol: 'INFY', threshold: 5000, repeat: 'once', note: '', enabled: true });
  await U.saveSettings(ctx, { capital: 777000 });
}

describe('off-site backup cycle', () => {
  let s3: FakeS3;
  beforeEach(async () => {
    s3 = await startFakeS3('vault');
  });
  afterEach(async () => s3.close());

  it('is a documented no-op when unconfigured', async () => {
    const ctx = ctxFor(s3);
    ctx.cfg.offsite = null;
    expect(await runOffsiteBackup(ctx)).toEqual({ skipped: true, reason: expect.stringMatching(/not configured/) });
    expect(s3.objects.size).toBe(0);
    expect((await offsiteStatus(ctx)).configured).toBe(false);
  });

  it('uploads an encrypted, uniquely named object, verifies it by read-back and records a sanitised status', async () => {
    const ctx = ctxFor(s3);
    await seed(ctx);
    const r = await runOffsiteBackup(ctx, noSleep);
    expect(r).toMatchObject({ skipped: false, counts: { trades: 1, holdings: 1, watchlist: 8, alerts: 1 } });
    expect(s3.objects.size).toBe(1);
    const [name, blob] = [...s3.objects.entries()][0]!;
    expect(name).toMatch(/^tire-desk\/daily\/2026-10-14T21-00-00-000Z-[0-9a-f]{8}\.tdbk$/);
    for (const secret of ['SYNTHTEST', 'CONFIDENTIAL-NOTE', 'INFY']) expect(blob.includes(Buffer.from(secret))).toBe(false);
    const st = await offsiteStatus(ctx);
    expect(st).toMatchObject({ configured: true, lastError: null, consecutiveFailures: 0, lastKey: name });
    const dump = JSON.stringify(st);
    for (const leak of [SECRET, ACCESS, KEY_HEX, s3.endpoint]) expect(dump).not.toContain(leak);
    // a second run gets a distinct name
    const ctx2 = { ...ctx, now: () => new Date('2026-10-15T21:00:00Z') };
    await runOffsiteBackup(ctx2, noSleep);
    expect(s3.objects.size).toBe(2);
  });

  it('retries transient failures and then succeeds', async () => {
    const ctx = ctxFor(s3);
    s3.failNextPuts = [503, 500];
    expect((await runOffsiteBackup(ctx, noSleep)).skipped).toBe(false);
    expect(s3.requests.filter((q) => q.startsWith('PUT')).length).toBe(3);
  });

  it('does not retry permanent errors, records a sanitised failure and leaves application data untouched', async () => {
    const ctx = ctxFor(s3);
    await seed(ctx);
    const before = JSON.stringify(await U.listTrades(ctx));
    s3.failStatus = { code: 403, body: '<Error><Code>AccessDenied</Code><Message>secret detail with key</Message></Error>' };
    await expect(runOffsiteBackup(ctx, noSleep)).rejects.toThrow(/HTTP 403 AccessDenied/);
    expect(s3.requests.filter((q) => q.startsWith('PUT')).length).toBe(1);
    const st = await offsiteStatus(ctx);
    expect(st.consecutiveFailures).toBe(1);
    expect(st.lastError).toBe('Upload failed: HTTP 403 AccessDenied');
    for (const leak of [SECRET, ACCESS, KEY_HEX, 'secret detail', s3.endpoint]) expect(JSON.stringify(st)).not.toContain(leak);
    expect(JSON.stringify(await U.listTrades(ctx))).toBe(before);
    // recovery clears the failure state
    s3.failStatus = null;
    await runOffsiteBackup(ctx, noSleep);
    expect((await offsiteStatus(ctx)).consecutiveFailures).toBe(0);
  });

  it('reports an unreachable destination without leaking its address', async () => {
    const ctx = ctxFor(s3);
    await s3.close();
    await expect(runOffsiteBackup(ctx, noSleep)).rejects.toThrow('Backup destination unreachable.');
    s3 = await startFakeS3('vault');
    expect((await offsiteStatus(ctx)).lastError).toBe('Backup destination unreachable.');
  });

  it('refuses to call a backup good when the stored copy is corrupt', async () => {
    const ctx = ctxFor(s3);
    s3.corruptOnStore = true;
    await expect(runOffsiteBackup(ctx, noSleep)).rejects.toThrow(/authentication/);
    expect((await offsiteStatus(ctx)).lastOkAt).toBeNull();
  });

  it('verifies the newest stored object independently and detects later corruption', async () => {
    const ctx = ctxFor(s3);
    await seed(ctx);
    await runOffsiteBackup(ctx, noSleep);
    expect(await verifyLatestOffsite(ctx)).toMatchObject({ ok: true, counts: { trades: 1 } });
    const [name, blob] = [...s3.objects.entries()][0]!;
    const bad = Buffer.from(blob);
    bad[bad.length - 5] = bad[bad.length - 5]! ^ 0xff;
    s3.objects.set(name, bad);
    expect((await verifyLatestOffsite(ctx)).ok).toBe(false);
  });

  it('signs listing requests the destination accepts (including pagination-free listing)', async () => {
    const ctx = ctxFor(s3);
    await runOffsiteBackup(ctx, noSleep);
    const keys = await new S3Client(ctx.cfg.offsite!).list('tire-desk/daily/');
    expect(keys).toHaveLength(1);
  });

  it('a different bucket key (wrong credentials) is refused by the destination', async () => {
    const ctx = ctxFor(s3, new MemoryKV(), { BACKUP_S3_ACCESS_KEY_ID: 'AKIAWRONG' });
    await expect(runOffsiteBackup(ctx, noSleep)).rejects.toThrow(/HTTP 403 SignatureDoesNotMatch/);
  });
});

describe('disaster recovery into an isolated empty store', () => {
  let s3: FakeS3;
  let dir: string;
  beforeEach(async () => {
    s3 = await startFakeS3('vault');
    dir = await fs.mkdtemp(path.join(os.tmpdir(), 'desk-dr-'));
  });
  afterEach(async () => {
    await s3.close();
    await fs.rm(dir, { recursive: true, force: true });
  });

  it('rebuilds every persisted collection from the remote object alone, with a safety snapshot', async () => {
    const source = ctxFor(s3);
    await seed(source);
    await runOffsiteBackup(source, noSleep);

    // "volume lost": a brand-new store; only the bucket and the encryption key survive.
    const target: Ctx = { ...ctxFor(s3), kv: new FileKV(dir) };
    const latest = (await new S3Client(target.cfg.offsite!).list('tire-desk/daily/')).at(-1)!;
    const plain = decryptBackup(await new S3Client(target.cfg.offsite!).get(latest), [parseEncryptionKey(KEY_HEX)!]);
    const res = await importAll(target, { ...(JSON.parse(plain.toString('utf8')) as object), confirm: true });
    expect(res).toMatchObject({ trades: 1, holdings: 1, watchlist: 8, alerts: 1 });
    expect((await U.listTrades(target))[0]).toMatchObject({ symbol: 'SYNTHTEST', qty: 3, notes: 'CONFIDENTIAL-NOTE' });
    expect((await U.listHoldings(target))[0]).toMatchObject({ symbol: 'TCS', qty: 5 });
    expect((await U.listWatchlist(target)).some((w) => w.symbol === 'INFY' && w.note === 'watch')).toBe(true);
    expect((await U.listAlerts(target))[0]).toMatchObject({ type: 'PRICE_ABOVE', threshold: 5000 });
    expect((await U.getSettings(target)).capital).toBe(777000);
    expect(res.backupKey).toMatch(/^backup\/pre-import-/); // safety snapshot taken before the import
  });

  it('rejects a corrupt export and changes nothing in the target', async () => {
    const target: Ctx = { ...ctxFor(s3), kv: new FileKV(dir) };
    await seed(target);
    const before = JSON.stringify([await U.listTrades(target), await U.listHoldings(target), await U.listWatchlist(target), await U.listAlerts(target), await U.getSettings(target)]);
    const corrupt = { version: 1, app: 'tire-desk', exportedAt: 'x', trades: [{ symbol: '' }], holdings: [], watchlist: [], alerts: [], settings: {}, confirm: true };
    await expect(importAll(target, corrupt)).rejects.toThrow(/Import rejected/);
    const after = JSON.stringify([await U.listTrades(target), await U.listHoldings(target), await U.listWatchlist(target), await U.listAlerts(target), await U.getSettings(target)]);
    expect(after).toBe(before);
    expect((await target.kv.list('backup/pre-import-')).length).toBe(0);
  });

  it('restoreOffsiteInto rebuilds a new directory, refuses a non-empty one and rejects tampered objects', async () => {
    const source = ctxFor(s3);
    await seed(source);
    await runOffsiteBackup(source, noSleep);
    const fresh = path.join(dir, 'recovered');
    const out = await restoreOffsiteInto(ctxFor(s3), 'latest', fresh);
    expect(out.counts).toMatchObject({ trades: 1, holdings: 1, alerts: 1 });
    const recovered: Ctx = { ...ctxFor(s3), kv: new FileKV(fresh) };
    expect((await U.listTrades(recovered))[0]?.symbol).toBe('SYNTHTEST');
    // second restore into the now-populated directory is refused and changes nothing
    await expect(restoreOffsiteInto(ctxFor(s3), 'latest', fresh)).rejects.toThrow(/not empty/);
    expect((await U.listTrades(recovered)).length).toBe(1);
    // a tampered remote object is rejected before anything is written
    const [name, blob] = [...s3.objects.entries()][0]!;
    const bad = Buffer.from(blob);
    bad[bad.length - 2] = bad[bad.length - 2]! ^ 0x55;
    s3.objects.set(name, bad);
    const other = path.join(dir, 'tampered');
    await expect(restoreOffsiteInto(ctxFor(s3), 'latest', other)).rejects.toThrow(/authentication/);
    await expect(fs.stat(other)).rejects.toThrow();
  });

  it('keeps status state out of exports (status lives under its own key)', async () => {
    const ctx = ctxFor(s3);
    await runOffsiteBackup(ctx, noSleep);
    expect((await ctx.kv.get<OffsiteStatus>(OFFSITE_STATUS_KEY)).value?.lastOkAt).not.toBeNull();
    const plain = decryptBackup([...s3.objects.values()][0]!, [parseEncryptionKey(KEY_HEX)!]).toString('utf8');
    expect(plain).not.toContain('lastOkAt');
  });
});
