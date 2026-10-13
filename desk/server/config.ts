import { FileKV, MemoryKV, type KV } from './kv';

export interface Config {
  /** true on Railway or with NODE_ENV=production. Turns several warnings into hard requirements. */
  production: boolean;
  sessionSecret: string | null;
  passwordHash: string | null;
  /** accepted only outside production and only if no hash is set */
  plainPassword: string | null;
  upstoxToken: string | null;
  telegram: { token: string; chatId: string } | null;
  offline: boolean;
  storage: 'file' | 'memory';
  dataDir: string;
  /** Browser-facing origin of the app (the Netlify site), e.g. https://tire-desk.netlify.app */
  publicOrigin: string | null;
  /** Exact origins allowed to make state-changing requests. Never "*". */
  allowedOrigins: string[];
  /** Shared secret the Netlify edge proxy attaches; requests without it are refused in production. */
  proxySecret: string | null;
  /** Run the in-process scheduler (alerts, digests, backups). Exactly one owner: the Railway service. */
  schedulerEnabled: boolean;
  /** Optional TIRE research engine base URL, probed read-only on the status page. */
  engineUrl: string | null;
}

type Env = Record<string, string | undefined>;

const nz = (s: string | undefined): string | null => (s && s.trim() ? s.trim() : null);

function originOf(s: string | null): string | null {
  if (!s) return null;
  try {
    const u = new URL(s);
    return `${u.protocol}//${u.host}`;
  } catch {
    return null;
  }
}

export function loadConfig(env: Env = process.env): Config {
  const token = nz(env.TELEGRAM_BOT_TOKEN);
  const chat = nz(env.TELEGRAM_CHAT_ID);
  // Railway injects RAILWAY_ENVIRONMENT_NAME into every deployment, so production
  // hardening cannot be forgotten by omitting NODE_ENV.
  const production = env.NODE_ENV === 'production' || Boolean(nz(env.RAILWAY_ENVIRONMENT_NAME));
  const publicOrigin = originOf(nz(env.DESK_PUBLIC_ORIGIN));
  const listed = (nz(env.DESK_ALLOWED_ORIGINS) ?? '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
  const allowed = [...new Set([...(publicOrigin ? [publicOrigin] : []), ...listed])];
  const sched = nz(env.DESK_SCHEDULER);
  return {
    production,
    sessionSecret: nz(env.SESSION_SECRET),
    passwordHash: nz(env.APP_PASSWORD_HASH),
    plainPassword: production ? null : nz(env.APP_PASSWORD),
    upstoxToken: nz(env.UPSTOX_ANALYTICS_TOKEN) ?? nz(env.UPSTOX_ACCESS_TOKEN),
    telegram: token && chat ? { token, chatId: chat } : null,
    offline: env.DESK_OFFLINE === '1',
    storage: env.DESK_STORAGE === 'memory' ? 'memory' : 'file',
    dataDir: nz(env.DESK_DATA_DIR) ?? '.devdata',
    publicOrigin,
    allowedOrigins: allowed,
    proxySecret: nz(env.DESK_PROXY_SECRET),
    schedulerEnabled: sched ? sched === 'on' : production,
    engineUrl: originOf(nz(env.TIRE_ENGINE_URL)) ?? null,
  };
}

/** Human-readable setup problems, shown on the status page; login is refused while any exist. */
export function configProblems(cfg: Config): string[] {
  const p: string[] = [];
  if (!cfg.sessionSecret || cfg.sessionSecret.length < 32) p.push('SESSION_SECRET is missing or shorter than 32 characters.');
  if (!cfg.passwordHash && !cfg.plainPassword) p.push('No password configured. Set APP_PASSWORD_HASH (run: npm run hash-password).');
  if (!cfg.passwordHash && cfg.plainPassword) p.push('APP_PASSWORD is set in plain text. Replace it with APP_PASSWORD_HASH.');
  return p;
}

/** Problems that make a production start unsafe. The server refuses to start while any exist. */
export function fatalProductionProblems(cfg: Config): string[] {
  if (!cfg.production) return [];
  const p: string[] = [];
  if (cfg.offline) p.push('DESK_OFFLINE=1 is not allowed in production (it serves synthetic market data).');
  if (cfg.storage !== 'file') p.push('DESK_STORAGE must be "file" in production (memory storage is lost on restart).');
  if (!cfg.dataDir.startsWith('/')) p.push('DESK_DATA_DIR must be an absolute path on the Railway volume, e.g. /data/desk.');
  if (!cfg.publicOrigin || !cfg.publicOrigin.startsWith('https://')) p.push('DESK_PUBLIC_ORIGIN must be the https origin of the Netlify site.');
  if (!cfg.proxySecret || cfg.proxySecret.length < 32) p.push('DESK_PROXY_SECRET must be set (32+ random characters) and match the Netlify site.');
  for (const o of cfg.allowedOrigins) {
    if (o === '*' || !o.startsWith('https://')) p.push(`DESK_ALLOWED_ORIGINS entry "${o}" must be an exact https origin.`);
    if (/localhost|127\.0\.0\.1|\[::1\]/.test(o)) p.push(`DESK_ALLOWED_ORIGINS entry "${o}" must not be localhost in production.`);
  }
  return p;
}

/** Directories the TIRE research engine creates under its data root (TIRE_DATA_ROOT, /data on Railway). */
export const ENGINE_DATA_MARKERS = ['persistence', 'research_dataset', 'reference', 'historical_replay'] as const;

/**
 * Storage-isolation guard. Desk data must live in its own sub-directory, and
 * that directory must not sit next to the research engine's files (which would
 * mean the Desk was deployed into the engine service or onto its volume).
 * `siblings` are the entry names of DESK_DATA_DIR's parent directory.
 */
export function storageLayoutProblems(dataDir: string, siblings: readonly string[]): string[] {
  const p: string[] = [];
  const parts = dataDir.split('/').filter(Boolean);
  if (parts.length < 2) p.push(`DESK_DATA_DIR (${dataDir}) must be a sub-directory of the volume, e.g. /data/desk, never the volume root.`);
  const clash = ENGINE_DATA_MARKERS.filter((m) => siblings.includes(m));
  if (clash.length > 0) p.push(`DESK_DATA_DIR's parent already contains TIRE engine data (${clash.join(', ')}). The Desk must run as its own Railway service with its own volume.`);
  return p;
}

export interface Ctx {
  kv: KV;
  cfg: Config;
  now: () => Date;
  fetchImpl?: typeof fetch;
}

let kvSingleton: KV | null = null;

export async function getKV(cfg: Config): Promise<KV> {
  if (kvSingleton) return kvSingleton;
  kvSingleton = cfg.storage === 'memory' ? new MemoryKV() : new FileKV(cfg.dataDir);
  return kvSingleton;
}

export function resetKVSingleton(): void {
  kvSingleton = null;
}
