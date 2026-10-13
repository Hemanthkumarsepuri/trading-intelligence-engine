// Storage abstraction with compare-and-set (file store on the Railway volume,
// memory store in tests). Every read-modify-write of user data goes through
// `update()`, which uses a version token to detect a concurrent writer and
// retries instead of silently losing data (the JSONL corruption/lost-write
// class of bug in the TIRE report, H-2).

import { promises as fs } from 'node:fs';
import { createHash, randomUUID } from 'node:crypto';
import path from 'node:path';

export interface Versioned<T> {
  value: T | null;
  version: string | null;
}

export type Expect = string | null | '*';

export interface KV {
  get<T>(key: string): Promise<Versioned<T>>;
  /** expect: version string = must match; null = must not exist; '*' = overwrite */
  put<T>(key: string, value: T, expect: Expect): Promise<{ ok: boolean; version: string | null }>;
  del(key: string): Promise<void>;
  list(prefix: string): Promise<string[]>;
}

export class ConflictError extends Error {
  constructor(key: string) {
    super(`Write conflict on ${key}`);
    this.name = 'ConflictError';
  }
}

/** Atomic read-modify-write with retry on version conflict. */
export async function update<T>(kv: KV, key: string, fallback: T, mutate: (current: T) => T, retries = 6): Promise<T> {
  for (let attempt = 0; attempt <= retries; attempt++) {
    const cur = await kv.get<T>(key);
    const base = cur.value ?? structuredClone(fallback);
    const next = mutate(base);
    const res = await kv.put(key, next, cur.version);
    if (res.ok) return next;
    // jittered back-off before re-reading
    await new Promise((r) => setTimeout(r, 15 + Math.random() * 40 * (attempt + 1)));
  }
  throw new ConflictError(key);
}

export async function read<T>(kv: KV, key: string, fallback: T): Promise<T> {
  const cur = await kv.get<T>(key);
  return cur.value ?? fallback;
}

// ---- In-memory (tests, offline demo) ----------------------------------------

export class MemoryKV implements KV {
  private data = new Map<string, { value: string; version: number }>();
  private counter = 0;

  async get<T>(key: string): Promise<Versioned<T>> {
    const e = this.data.get(key);
    return e ? { value: JSON.parse(e.value) as T, version: String(e.version) } : { value: null, version: null };
  }
  async put<T>(key: string, value: T, expect: Expect): Promise<{ ok: boolean; version: string | null }> {
    const e = this.data.get(key);
    if (expect !== '*') {
      if (expect === null && e) return { ok: false, version: String(e.version) };
      if (expect !== null && (!e || String(e.version) !== expect)) return { ok: false, version: e ? String(e.version) : null };
    }
    const version = ++this.counter;
    this.data.set(key, { value: JSON.stringify(value), version });
    return { ok: true, version: String(version) };
  }
  async del(key: string): Promise<void> {
    this.data.delete(key);
  }
  async list(prefix: string): Promise<string[]> {
    return [...this.data.keys()].filter((k) => k.startsWith(prefix)).sort();
  }
}

// ---- File-backed (Railway volume in production, .devdata locally) ---------------
//
// One JSON file per key under `dir`. Writes are serialised inside the process
// (compare-and-set is read-check-write), written to a temp file, fsync'd and
// atomically renamed, then the directory is fsync'd, so a crash or container
// restart leaves either the old or the new value, never a torn file.
// Railway attaches a volume to exactly one running deployment, so a single
// process is the only writer; the CAS still guards every read-modify-write.

/** Reversible, filesystem-safe key encoding ("backup/daily-x" -> "backup%2Fdaily-x"). */
export function encodeKey(key: string): string {
  return encodeURIComponent(key).replace(/\./g, '%2E').replace(/\*/g, '%2A');
}
export function decodeKey(name: string): string {
  return decodeURIComponent(name);
}

const TRANSIENT_RENAME = new Set(['EPERM', 'EACCES', 'EBUSY']);
const RENAME_ATTEMPTS = 12;

/**
 * Atomically replace `target` with `tmp`. On Windows, MoveFileEx fails with
 * EPERM/EACCES/EBUSY while another handle (a concurrent get(), antivirus, the
 * indexer) has the target open; that clears within milliseconds. Retry a bounded
 * number of times with backoff, then propagate. The target is never deleted
 * first, so a failure leaves the old value intact. POSIX rename does not hit
 * this, so Linux/Railway errors surface immediately.
 */
export async function renameReplacing(tmp: string, target: string, platform: string = process.platform): Promise<void> {
  for (let attempt = 1; ; attempt++) {
    try {
      await fs.rename(tmp, target);
      return;
    } catch (e) {
      const code = (e as NodeJS.ErrnoException).code ?? '';
      if (platform !== 'win32' || !TRANSIENT_RENAME.has(code) || attempt >= RENAME_ATTEMPTS) throw e;
      await new Promise((r) => setTimeout(r, Math.min(5 * attempt, 40) + Math.random() * 5));
    }
  }
}

export class FileKV implements KV {
  private chain: Promise<unknown> = Promise.resolve();
  constructor(private dir: string) {}

  get directory(): string {
    return this.dir;
  }
  private file(key: string): string {
    return path.join(this.dir, `${encodeKey(key)}.json`);
  }
  private serial<R>(fn: () => Promise<R>): Promise<R> {
    const run = this.chain.then(fn, fn);
    this.chain = run.catch(() => undefined);
    return run;
  }
  private hash(text: string): string {
    return createHash('sha256').update(text).digest('hex').slice(0, 24);
  }
  // Reads share the write queue: a reader holding the target open while put()
  // renames over it is what makes Windows fail the rename (EPERM).
  get<T>(key: string): Promise<Versioned<T>> {
    return this.serial(async () => {
      let text: string;
      try {
        text = await fs.readFile(this.file(key), 'utf8');
      } catch (e) {
        if ((e as NodeJS.ErrnoException).code === 'ENOENT') return { value: null, version: null };
        throw e; // permission/IO errors must surface, not masquerade as "no data"
      }
      return { value: JSON.parse(text) as T, version: this.hash(text) };
    });
  }
  put<T>(key: string, value: T, expect: Expect): Promise<{ ok: boolean; version: string | null }> {
    return this.serial(async () => {
      const target = this.file(key);
      let curText: string | null = null;
      try {
        curText = await fs.readFile(target, 'utf8');
      } catch (e) {
        if ((e as NodeJS.ErrnoException).code !== 'ENOENT') throw e;
      }
      const curVersion = curText === null ? null : this.hash(curText);
      if (expect !== '*' && expect !== curVersion) return { ok: false, version: curVersion };
      await fs.mkdir(this.dir, { recursive: true });
      const text = JSON.stringify(value);
      const tmp = `${target}.${process.pid}.${randomUUID()}.tmp`;
      try {
        const fh = await fs.open(tmp, 'w');
        try {
          await fh.writeFile(text, 'utf8');
          await fh.sync();
        } finally {
          await fh.close();
        }
        await renameReplacing(tmp, target);
      } catch (e) {
        await fs.unlink(tmp).catch(() => undefined); // never leave a stray temp; the target is untouched
        throw e;
      }
      await this.syncDir();
      return { ok: true, version: this.hash(text) };
    });
  }
  private async syncDir(): Promise<void> {
    try {
      const d = await fs.open(this.dir, 'r');
      try { await d.sync(); } finally { await d.close(); }
    } catch {
      /* directory fsync is not supported on every platform (e.g. Windows dev) */
    }
  }
  del(key: string): Promise<void> {
    return this.serial(async () => {
      try {
        await fs.unlink(this.file(key));
        await this.syncDir();
      } catch (e) {
        if ((e as NodeJS.ErrnoException).code !== 'ENOENT') throw e;
      }
    });
  }
  async list(prefix: string): Promise<string[]> {
    let names: string[];
    try {
      names = await fs.readdir(this.dir);
    } catch (e) {
      if ((e as NodeJS.ErrnoException).code === 'ENOENT') return [];
      throw e;
    }
    return names
      .filter((n) => n.endsWith('.json'))
      .map((n) => decodeKey(n.slice(0, -5)))
      .filter((k) => k.startsWith(prefix))
      .sort();
  }
}
