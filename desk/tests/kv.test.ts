import { afterEach, describe, expect, it, vi } from 'vitest';
import { promises as fs } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { FileKV, MemoryKV, renameReplacing, update } from '../server/kv';
import { cached, resetCacheMemory } from '../server/cache';

describe.each([
  ['memory', () => new MemoryKV()],
  ['file', async () => new FileKV(await fs.mkdtemp(path.join(os.tmpdir(), 'desk-kv-')))],
])('%s KV', (_name, make) => {
  it('compare-and-set rejects a stale writer', async () => {
    const kv = await make();
    expect((await kv.put('k', { n: 1 }, null)).ok).toBe(true);
    expect((await kv.put('k', { n: 9 }, null)).ok).toBe(false); // already exists
    const a = await kv.get<{ n: number }>('k');
    expect((await kv.put('k', { n: 2 }, a.version)).ok).toBe(true);
    expect((await kv.put('k', { n: 3 }, a.version)).ok).toBe(false); // stale version
    expect((await kv.get<{ n: number }>('k')).value).toEqual({ n: 2 });
  });
  it('concurrent updates never lose a write', async () => {
    const kv = await make();
    await Promise.all(Array.from({ length: 25 }, (_, i) => update<number[]>(kv, 'list', [], (cur) => [...cur, i])));
    const final = (await kv.get<number[]>('list')).value!;
    expect(final).toHaveLength(25);
    expect(new Set(final).size).toBe(25);
  });
  it('lists by prefix and deletes', async () => {
    const kv = await make();
    await kv.put('a/1', 1, '*'); await kv.put('a/2', 2, '*'); await kv.put('b/1', 3, '*');
    expect(await kv.list('a/')).toEqual(['a/1', 'a/2']); // original keys round-trip, including '/'
    await kv.del('a/1');
    expect((await kv.get('a/1')).value).toBeNull();
  });
});

describe('cached()', () => {
  it('serves fresh cache, refreshes after TTL, and serves stale on error', async () => {
    resetCacheMemory();
    const kv = new MemoryKV();
    let t = 1_000_000;
    let calls = 0;
    const now = () => t;
    const ok = async () => ({ n: ++calls });
    expect((await cached(kv, 'x', 60, ok, { now })).value).toEqual({ n: 1 });
    t += 30_000;
    expect((await cached(kv, 'x', 60, ok, { now })).fromCache).toBe(true);
    expect(calls).toBe(1);
    t += 60_000;
    resetCacheMemory();
    const boom = async () => { throw new Error('upstream 429'); };
    const r = await cached(kv, 'x', 60, boom, { now });
    expect(r.stale).toBe(true);
    expect(r.value).toEqual({ n: 1 });
    expect(r.error).toMatch(/429/);
  });
  it('throws when there is nothing cached and the loader fails', async () => {
    resetCacheMemory();
    await expect(cached(new MemoryKV(), 'y', 60, async () => { throw new Error('down'); })).rejects.toThrow('down');
  });
  it('coalesces concurrent loads into one upstream call', async () => {
    resetCacheMemory();
    const kv = new MemoryKV();
    let calls = 0;
    const slow = async () => { calls++; await new Promise((r) => setTimeout(r, 30)); return calls; };
    await Promise.all([cached(kv, 'z', 60, slow), cached(kv, 'z', 60, slow), cached(kv, 'z', 60, slow)]);
    expect(calls).toBe(1);
  });
});

describe('FileKV durability', () => {
  it('keeps data across a process restart (new instance, same directory)', async () => {
    const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'desk-kv-restart-'));
    const a = new FileKV(dir);
    await update<number[]>(a, 'data/trades', [], (cur) => [...cur, 1, 2, 3]);
    await a.put('backup/daily-2026-10-12', { ok: true }, '*');
    const b = new FileKV(dir); // fresh process view
    expect((await b.get<number[]>('data/trades')).value).toEqual([1, 2, 3]);
    expect(await b.list('backup/')).toEqual(['backup/daily-2026-10-12']);
    await b.del('backup/daily-2026-10-12');
    expect(await new FileKV(dir).list('backup/')).toEqual([]);
  });
  it('leaves no temp files behind and never exposes a torn write', async () => {
    const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'desk-kv-atomic-'));
    const kv = new FileKV(dir);
    await Promise.all(Array.from({ length: 20 }, (_, i) => kv.put('k', { i, pad: 'x'.repeat(5000) }, '*')));
    const files = await fs.readdir(dir);
    expect(files.filter((f) => f.endsWith('.tmp'))).toEqual([]);
    const v = (await kv.get<{ i: number; pad: string }>('k')).value!;
    expect(v.pad).toHaveLength(5000);
  });
  it('surfaces IO errors instead of pretending the key is empty', async () => {
    const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'desk-kv-err-'));
    const kv = new FileKV(dir);
    await fs.mkdir(path.join(dir, 'bad.json')); // a directory where a file is expected
    await expect(kv.get('bad')).rejects.toThrow();
  });
});

describe('renameReplacing (Windows replacement races)', () => {
  afterEach(() => vi.restoreAllMocks());
  const err = (code: string) => Object.assign(new Error(code), { code });

  it('retries a transient EPERM on win32 and then succeeds', async () => {
    const spy = vi.spyOn(fs, 'rename').mockRejectedValueOnce(err('EPERM')).mockRejectedValueOnce(err('EBUSY')).mockResolvedValue(undefined);
    await renameReplacing('a.tmp', 'a.json', 'win32');
    expect(spy).toHaveBeenCalledTimes(3);
  });
  it('gives up after a bounded number of attempts and propagates the error', async () => {
    const spy = vi.spyOn(fs, 'rename').mockRejectedValue(err('EPERM'));
    await expect(renameReplacing('a.tmp', 'a.json', 'win32')).rejects.toMatchObject({ code: 'EPERM' });
    expect(spy.mock.calls.length).toBeLessThanOrEqual(12);
  });
  it('does not retry on Linux, nor on non-transient codes', async () => {
    const spy = vi.spyOn(fs, 'rename').mockRejectedValue(err('EPERM'));
    await expect(renameReplacing('a.tmp', 'a.json', 'linux')).rejects.toMatchObject({ code: 'EPERM' });
    expect(spy).toHaveBeenCalledTimes(1);
    spy.mockReset().mockRejectedValue(err('ENOSPC'));
    await expect(renameReplacing('a.tmp', 'a.json', 'win32')).rejects.toMatchObject({ code: 'ENOSPC' });
    expect(spy).toHaveBeenCalledTimes(1);
  });
  it('cleans up the temp file and keeps the old value when the replace ultimately fails', async () => {
    const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'desk-kv-fail-'));
    const kv = new FileKV(dir);
    await kv.put('k', { v: 1 }, null);
    vi.spyOn(fs, 'rename').mockRejectedValue(err('ENOSPC'));
    await expect(kv.put('k', { v: 2 }, '*')).rejects.toThrow();
    vi.restoreAllMocks();
    expect((await fs.readdir(dir)).filter((f) => f.endsWith('.tmp'))).toEqual([]);
    expect((await kv.get<{ v: number }>('k')).value).toEqual({ v: 1 });
  });
});
