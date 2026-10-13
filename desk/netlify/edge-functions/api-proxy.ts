// Netlify Edge Function: same-origin proxy from https://<site>/api/* to the
// TIRE Desk API service on Railway. Transport only - no business logic.
//
// Why a proxy instead of calling Railway from the browser: the browser then
// talks to one origin, so the session cookie stays first-party
// (HttpOnly, Secure, SameSite=Strict) and no third-party-cookie or CORS
// exception is needed. Railway refuses any request that does not carry the
// shared DESK_PROXY_SECRET this function adds, so the API is not directly
// reachable from the internet (except its public /api/health).
//
// Netlify environment variables (scope: Functions), read at request time:
//   DESK_API_ORIGIN    https origin of the Railway Desk service, e.g. https://tire-desk-api.up.railway.app
//   DESK_PROXY_SECRET  same value as on the Railway service

interface EdgeContext {
  ip?: string;
}
declare const Netlify: { env: { get(name: string): string | undefined } };

const MAX_BODY = 6 * 1024 * 1024;
const UPSTREAM_TIMEOUT_MS = 30_000;
/** Request headers forwarded to Railway. Everything else (incl. any spoofed x-desk-*) is dropped. */
const FORWARD = ['accept', 'content-type', 'cookie', 'origin', 'sec-fetch-site', 'sec-fetch-mode', 'sec-fetch-dest', 'user-agent'];
/** Response headers never passed back to the browser. */
const HOP = new Set(['connection', 'keep-alive', 'transfer-encoding', 'te', 'trailer', 'upgrade', 'proxy-authenticate', 'proxy-authorization', 'content-encoding', 'content-length']);

function json(status: number, code: string, message: string): Response {
  return new Response(JSON.stringify({ ok: false, error: { code, message } }), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store', 'x-content-type-options': 'nosniff' },
  });
}

/** Validate the configured upstream: https only, never localhost/private in production. */
export function upstreamOrigin(raw: string | undefined): string | null {
  if (!raw) return null;
  let u: URL;
  try {
    u = new URL(raw.trim());
  } catch {
    return null;
  }
  if (u.protocol !== 'https:') return null;
  if (/^(localhost|127\.|0\.0\.0\.0|\[::1\]|10\.|192\.168\.|169\.254\.)/i.test(u.hostname)) return null;
  return `${u.protocol}//${u.host}`;
}

export interface ProxyEnv {
  DESK_API_ORIGIN?: string;
  DESK_PROXY_SECRET?: string;
}

export async function proxyApi(req: Request, env: ProxyEnv, ip: string | undefined, fetchImpl: typeof fetch = fetch): Promise<Response> {
  const origin = upstreamOrigin(env.DESK_API_ORIGIN);
  const secret = env.DESK_PROXY_SECRET;
  if (!origin || !secret || secret.length < 32) {
    return json(503, 'PROXY_NOT_CONFIGURED', 'The site is not connected to its API yet (DESK_API_ORIGIN / DESK_PROXY_SECRET).');
  }
  const url = new URL(req.url);
  if (!url.pathname.startsWith('/api/') || url.pathname.includes('..')) return json(404, 'NOT_FOUND', 'Not found');

  const headers = new Headers();
  for (const h of FORWARD) {
    const v = req.headers.get(h);
    if (v !== null) headers.set(h, v);
  }
  headers.set('x-desk-proxy-key', secret);
  if (ip) headers.set('x-desk-client-ip', ip);
  headers.set('x-forwarded-proto', 'https');

  let body: ArrayBuffer | undefined;
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    const declared = Number(req.headers.get('content-length') ?? '0');
    if (declared > MAX_BODY) return json(413, 'TOO_LARGE', 'Request body too large.');
    body = await req.arrayBuffer();
    if (body.byteLength > MAX_BODY) return json(413, 'TOO_LARGE', 'Request body too large.');
  }

  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), UPSTREAM_TIMEOUT_MS);
  let upstream: Response;
  try {
    upstream = await fetchImpl(`${origin}${url.pathname}${url.search}`, { method: req.method, headers, body, redirect: 'manual', signal: ctl.signal });
  } catch (e) {
    const timedOut = e instanceof Error && /abort/i.test(e.name + e.message);
    return timedOut ? json(504, 'UPSTREAM_TIMEOUT', 'The API did not respond in time.') : json(502, 'UPSTREAM_UNREACHABLE', 'The API service is unreachable (it may be restarting).');
  } finally {
    clearTimeout(timer);
  }

  const out = new Headers();
  upstream.headers.forEach((v, k) => {
    if (!HOP.has(k.toLowerCase()) && k.toLowerCase() !== 'set-cookie') out.set(k, v);
  });
  for (const c of upstream.headers.getSetCookie?.() ?? []) out.append('set-cookie', c);
  out.set('cache-control', 'no-store');
  return new Response(upstream.body, { status: upstream.status, headers: out });
}

export default async (req: Request, context: EdgeContext): Promise<Response> =>
  proxyApi(req, { DESK_API_ORIGIN: Netlify.env.get('DESK_API_ORIGIN'), DESK_PROXY_SECRET: Netlify.env.get('DESK_PROXY_SECRET') }, context.ip);

export const config = { path: '/api/*' };
