import { FileKV, MemoryKV, type KV } from './kv';

/** Independent off-volume backup destination: any S3-compatible bucket, encrypted client-side. */
export interface OffsiteConfig {
  endpoint: string;
  bucket: string;
  region: string;
  accessKeyId: string;
  secretAccessKey: string;
  /** object-name prefix, always ending in "/" */
  prefix: string;
  /** 32-byte AES-256-GCM key (BACKUP_ENCRYPTION_KEY). Never stored with the backups. */
  encryptionKey: Buffer;
}

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
  /** Legacy edge-proxy mode only: shared secret the Netlify proxy attaches. Mutually exclusive with ingress 'railway'. */
  proxySecret: string | null;
  /**
   * Who may tell us the caller's IP.
   *  - 'railway': the app is served directly by Railway (single origin, no proxy credential exists anywhere).
   *    The IP is the right-most X-Forwarded-For entry, which Railway's edge appends.
   *  - 'edge-proxy': legacy Netlify proxy that authenticates with proxySecret.
   *  - 'local': development; socket address.
   */
  ingress: 'railway' | 'edge-proxy' | 'local';
  /** Directory of the built frontend (dist) served by this server in 'railway' ingress mode; null = API only. */
  webRoot: string | null;
  /** Run the in-process scheduler (alerts, digests, backups). Exactly one owner: the Railway service. */
  schedulerEnabled: boolean;
  /** Optional TIRE research engine base URL, probed read-only on the status page. */
  engineUrl: string | null;
  /** Off-volume encrypted backups; null when not (fully) configured. */
  offsite: OffsiteConfig | null;
  /** BACKUP_* variable names that are set while the configuration is incomplete or invalid. */
  offsiteProblems: string[];
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

const OFFSITE_VARS = ['BACKUP_S3_ENDPOINT', 'BACKUP_S3_BUCKET', 'BACKUP_S3_ACCESS_KEY_ID', 'BACKUP_S3_SECRET_ACCESS_KEY', 'BACKUP_ENCRYPTION_KEY'] as const;

function loadOffsite(env: Env): { offsite: OffsiteConfig | null; problems: string[] } {
  const anySet = OFFSITE_VARS.some((k) => nz(env[k]));
  if (!anySet) return { offsite: null, problems: [] };
  const missing = OFFSITE_VARS.filter((k) => !nz(env[k]));
  const problems: string[] = missing.length ? [`Off-site backup is partially configured; missing ${missing.join(', ')}.`] : [];
  const endpoint = originOf(nz(env.BACKUP_S3_ENDPOINT));
  if (nz(env.BACKUP_S3_ENDPOINT) && (!endpoint || !endpoint.startsWith('https://'))) problems.push('BACKUP_S3_ENDPOINT must be an https URL.');
  const keyRaw = nz(env.BACKUP_ENCRYPTION_KEY);
  const key = keyRaw ? (/^[0-9a-fA-F]{64}$/.test(keyRaw) ? Buffer.from(keyRaw, 'hex') : Buffer.from(keyRaw, 'base64')) : null;
  if (keyRaw && key?.length !== 32) problems.push('BACKUP_ENCRYPTION_KEY must be 32 random bytes (64 hex characters or base64).');
  if (problems.length) return { offsite: null, problems };
  const prefix = (nz(env.BACKUP_S3_PREFIX) ?? 'tire-desk/').replace(/^\/+/, '').replace(/\/?$/, '/');
  return {
    offsite: { endpoint: endpoint!, bucket: nz(env.BACKUP_S3_BUCKET)!, region: nz(env.BACKUP_S3_REGION) ?? 'auto', accessKeyId: nz(env.BACKUP_S3_ACCESS_KEY_ID)!, secretAccessKey: nz(env.BACKUP_S3_SECRET_ACCESS_KEY)!, prefix, encryptionKey: key! },
    problems: [],
  };
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
  const off = loadOffsite(env);
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
    ingress: nz(env.DESK_INGRESS) === 'railway' ? 'railway' : nz(env.DESK_PROXY_SECRET) ? 'edge-proxy' : 'local',
    webRoot: nz(env.DESK_INGRESS) === 'railway' ? (nz(env.DESK_WEB_ROOT) ?? 'dist') : nz(env.DESK_WEB_ROOT),
    schedulerEnabled: sched ? sched === 'on' : production,
    engineUrl: originOf(nz(env.TIRE_ENGINE_URL)) ?? null,
    offsite: off.offsite,
    offsiteProblems: off.problems,
  };
}

/** Human-readable setup problems, shown on the status page; login is refused while any exist. */
export function configProblems(cfg: Config): string[] {
  const p: string[] = [];
  if (!cfg.sessionSecret || cfg.sessionSecret.length < 32) p.push('SESSION_SECRET is missing or shorter than 32 characters.');
  if (!cfg.passwordHash && !cfg.plainPassword) p.push('No password configured. Set APP_PASSWORD_HASH (run: npm run hash-password).');
  if (!cfg.passwordHash && cfg.plainPassword) p.push('APP_PASSWORD is set in plain text. Replace it with APP_PASSWORD_HASH.');
  p.push(...cfg.offsiteProblems);
  return p;
}

/** Problems that make a production start unsafe. The server refuses to start while any exist. */
export function fatalProductionProblems(cfg: Config): string[] {
  if (!cfg.production) return [];
  const p: string[] = [];
  if (cfg.offline) p.push('DESK_OFFLINE=1 is not allowed in production (it serves synthetic market data).');
  if (cfg.storage !== 'file') p.push('DESK_STORAGE must be "file" in production (memory storage is lost on restart).');
  if (!cfg.dataDir.startsWith('/')) p.push('DESK_DATA_DIR must be an absolute path on the Railway volume, e.g. /data/desk.');
  if (!cfg.publicOrigin || !cfg.publicOrigin.startsWith('https://')) p.push('DESK_PUBLIC_ORIGIN must be the https origin users open (the Railway domain, or the Netlify site in legacy proxy mode).');
  if (cfg.ingress === 'railway' && cfg.proxySecret) p.push('DESK_INGRESS=railway and DESK_PROXY_SECRET are mutually exclusive: remove DESK_PROXY_SECRET (no proxy credential is used in single-origin mode).');
  if (cfg.ingress === 'local') p.push('Set DESK_INGRESS=railway (single origin served by Railway) or DESK_PROXY_SECRET (legacy Netlify proxy). Without either, the client IP cannot be trusted.');
  if (cfg.ingress === 'edge-proxy' && (!cfg.proxySecret || cfg.proxySecret.length < 32)) p.push('DESK_PROXY_SECRET must be 32+ random characters and match the Netlify site.');
  if (cfg.ingress === 'railway' && !cfg.webRoot) p.push('DESK_WEB_ROOT is empty.');
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
