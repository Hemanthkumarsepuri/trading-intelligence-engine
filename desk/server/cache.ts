// Read-through cache with single-flight and stale-on-error.
//
// Why: every browser tab polls; Yahoo/NSE rate-limit shared cloud IPs. One
// loader call per TTL serves everyone, and when an upstream fails we serve the
// last good copy explicitly marked stale instead of a blank screen or a
// silently-old number (cf. TIRE defects H-3/M-2, stale data voting silently).

import type { KV } from './kv';

interface Entry<T> {
  value: T;
  savedAt: string;
}

export interface Cached<T> {
  value: T;
  stale: boolean;
  savedAt: string;
  fromCache: boolean;
  /** present when stale: why the refresh failed */
  error?: string;
}

const inflight = new Map<string, Promise<unknown>>();
const hot = new Map<string, { exp: number; entry: Entry<unknown> }>();

export async function cached<T>(
  kv: KV,
  key: string,
  ttlSec: number,
  loader: () => Promise<T>,
  opts: { maxStaleSec?: number; now?: () => number } = {},
): Promise<Cached<T>> {
  const now = opts.now ?? Date.now;
  const maxStale = (opts.maxStaleSec ?? 6 * 3600) * 1000;
  const k = `cache/${key}`;

  const h = hot.get(k);
  if (h && h.exp > now()) {
    const e = h.entry as Entry<T>;
    return { value: e.value, stale: false, savedAt: e.savedAt, fromCache: true };
  }

  const stored = (await kv.get<Entry<T>>(k)).value;
  if (stored && now() - Date.parse(stored.savedAt) < ttlSec * 1000) {
    hot.set(k, { exp: now() + Math.min(ttlSec, 10) * 1000, entry: stored });
    return { value: stored.value, stale: false, savedAt: stored.savedAt, fromCache: true };
  }

  const existing = inflight.get(k) as Promise<Cached<T>> | undefined;
  if (existing) return existing;

  const run = (async (): Promise<Cached<T>> => {
    try {
      const value = await loader();
      const entry: Entry<T> = { value, savedAt: new Date(now()).toISOString() };
      await kv.put(k, entry, '*').catch(() => undefined); // cache write failure must not fail the request
      hot.set(k, { exp: now() + Math.min(ttlSec, 10) * 1000, entry });
      return { value, stale: false, savedAt: entry.savedAt, fromCache: false };
    } catch (e) {
      if (stored && now() - Date.parse(stored.savedAt) < maxStale) {
        return { value: stored.value, stale: true, savedAt: stored.savedAt, fromCache: true, error: e instanceof Error ? e.message : String(e) };
      }
      throw e;
    } finally {
      inflight.delete(k);
    }
  })();
  inflight.set(k, run);
  return run;
}

/** Test helper */
export function resetCacheMemory(): void {
  hot.clear();
  inflight.clear();
}
