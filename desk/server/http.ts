// Outbound HTTP with timeouts, bounded retries and typed failures.

export type ProviderErrorKind = 'BLOCKED' | 'RATE_LIMITED' | 'TIMEOUT' | 'HTTP' | 'MALFORMED' | 'NOT_CONFIGURED' | 'NETWORK';

export class ProviderError extends Error {
  constructor(public kind: ProviderErrorKind, message: string, public status?: number) {
    super(message);
    this.name = 'ProviderError';
  }
}

export const BROWSER_UA =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36';

export interface FetchOpts {
  headers?: Record<string, string>;
  timeoutMs?: number;
  retries?: number;
  method?: string;
  body?: string;
  /** injectable for tests */
  fetchImpl?: typeof fetch;
}

export function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

export async function fetchText(url: string, opts: FetchOpts = {}): Promise<{ text: string; status: number; headers: Headers }> {
  const f = opts.fetchImpl ?? fetch;
  const retries = opts.retries ?? 1;
  let lastErr: unknown;
  for (let attempt = 0; attempt <= retries; attempt++) {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), opts.timeoutMs ?? 8000);
    try {
      const res = await f(url, {
        method: opts.method ?? 'GET',
        body: opts.body,
        headers: { 'User-Agent': BROWSER_UA, Accept: '*/*', ...opts.headers },
        signal: ctl.signal,
        redirect: 'follow',
      });
      const text = await res.text();
      if (res.status === 429) throw new ProviderError('RATE_LIMITED', `429 from ${hostOf(url)}`, 429);
      if (res.status === 401 || res.status === 403) throw new ProviderError('BLOCKED', `${res.status} from ${hostOf(url)} (access denied, possibly a blocked datacenter IP or bad token)`, res.status);
      if (res.status >= 500) throw new ProviderError('HTTP', `${res.status} from ${hostOf(url)}`, res.status);
      if (res.status >= 400) throw new ProviderError('HTTP', `${res.status} from ${hostOf(url)}`, res.status);
      return { text, status: res.status, headers: res.headers };
    } catch (e) {
      lastErr = e;
      if (e instanceof ProviderError && (e.kind === 'BLOCKED' || (e.kind === 'HTTP' && (e.status ?? 0) < 500))) throw e;
      if (attempt < retries) {
        await sleep(250 * (attempt + 1) + Math.random() * 150);
        continue;
      }
    } finally {
      clearTimeout(timer);
    }
  }
  if (lastErr instanceof ProviderError) throw lastErr;
  const msg = lastErr instanceof Error ? lastErr.message : String(lastErr);
  if (/abort/i.test(msg)) throw new ProviderError('TIMEOUT', `Timed out calling ${hostOf(url)}`);
  throw new ProviderError('NETWORK', `Network error calling ${hostOf(url)}: ${msg}`);
}

export async function fetchJson<T = unknown>(url: string, opts: FetchOpts = {}): Promise<T> {
  const { text } = await fetchText(url, { ...opts, headers: { Accept: 'application/json', ...opts.headers } });
  try {
    return JSON.parse(text) as T;
  } catch {
    const head = text.slice(0, 60).replace(/\s+/g, ' ');
    throw new ProviderError('MALFORMED', `Non-JSON response from ${hostOf(url)}: "${head}"`);
  }
}

function hostOf(url: string): string {
  try { return new URL(url).host; } catch { return url; }
}

/** Run `fn` over items with bounded concurrency; preserves order; never rejects as a whole. */
export async function mapLimit<T, R>(items: T[], limit: number, fn: (item: T, i: number) => Promise<R>): Promise<PromiseSettledResult<R>[]> {
  const out: PromiseSettledResult<R>[] = new Array(items.length);
  let next = 0;
  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (true) {
      const i = next++;
      if (i >= items.length) return;
      try {
        out[i] = { status: 'fulfilled', value: await fn(items[i]!, i) };
      } catch (reason) {
        out[i] = { status: 'rejected', reason };
      }
    }
  });
  await Promise.all(workers);
  return out;
}
